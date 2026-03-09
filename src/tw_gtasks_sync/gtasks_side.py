"""Google Tasks API integration."""

from __future__ import annotations

import datetime
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient import discovery
from googleapiclient.http import HttpError

from tw_gtasks_sync.config import get_credentials_path

if TYPE_CHECKING:
    from collections.abc import Sequence

SCOPES = ["https://www.googleapis.com/auth/tasks"]

GTASKS_ID_KEY = "id"
GTASKS_TITLE_KEY = "title"
GTASKS_NOTES_KEY = "notes"
GTASKS_STATUS_KEY = "status"
GTASKS_UPDATED_KEY = "updated"
GTASKS_DUE_KEY = "due"
GTASKS_COMPLETED_KEY = "completed"


class GTasksItem(dict):
    """Google Tasks item representation."""

    @property
    def id(self) -> str | None:
        return self.get(GTASKS_ID_KEY)

    @property
    def title(self) -> str:
        return self.get(GTASKS_TITLE_KEY, "")

    @property
    def notes(self) -> str | None:
        return self.get(GTASKS_NOTES_KEY)

    @property
    def status(self) -> str:
        return self.get(GTASKS_STATUS_KEY, "needsAction")

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def updated(self) -> datetime.datetime | None:
        updated_str = self.get(GTASKS_UPDATED_KEY)
        if updated_str:
            return self._parse_datetime(updated_str)
        return None

    @property
    def due(self) -> datetime.datetime | None:
        due_str = self.get(GTASKS_DUE_KEY)
        if due_str:
            return self._parse_datetime(due_str)
        return None

    @property
    def completed_date(self) -> datetime.datetime | None:
        completed_str = self.get(GTASKS_COMPLETED_KEY)
        if completed_str:
            return self._parse_datetime(completed_str)
        return None

    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime.datetime:
        """Parse RFC3339 datetime string."""
        from dateutil import parser

        dt = parser.parse(dt_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)


class GTasksSide:
    """Google Tasks API client."""

    def __init__(
        self,
        *,
        credentials_file: str,
        oauth_port: int = 8081,
        task_list_name: str,
    ) -> None:
        self._credentials_file = credentials_file
        self._oauth_port = oauth_port
        self._task_list_name = task_list_name
        self._service: discovery.Resource | None = None
        self._task_list_id: str | None = None
        self._items_cache: dict[str, GTasksItem] = {}

    def authenticate(self) -> None:
        """Authenticate with Google Tasks API using OAuth."""
        creds_path = get_credentials_path(self._credentials_file)
        creds = self._load_or_create_credentials(creds_path)
        self._service = discovery.build("tasks", "v1", credentials=creds)

    def _load_or_create_credentials(self, creds_path: Path):
        """Load existing credentials or create new ones via OAuth flow."""
        creds = None

        if creds_path.exists():
            with creds_path.open("rb") as f:
                creds = pickle.load(f)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                self._get_client_secret_path(),
                SCOPES,
            )
            creds = flow.run_local_server(port=self._oauth_port)

        with creds_path.open("wb") as f:
            pickle.dump(creds, f)

        return creds

    def _get_client_secret_path(self) -> Path:
        """Get path to OAuth client secret file.

        First checks for a custom client secret file in the config directory,
        then falls back to the bundled default.
        """
        from tw_gtasks_sync.config import get_config_dir

        custom_secret = get_config_dir() / "client_secret.json"
        if custom_secret.exists():
            return custom_secret

        return Path(__file__).parent / "res" / "client_secret.json"

    def start(self) -> None:
        """Initialize connection and get/create task list."""
        if self._service is None:
            self.authenticate()

        self._task_list_id = self._find_or_create_task_list()

    def _find_or_create_task_list(self) -> str:
        """Find task list by name or create it."""
        task_lists = self._service.tasklists().list().execute()

        for task_list in task_lists.get("items", []):
            if task_list["title"] == self._task_list_name:
                return task_list["id"]

        new_list = (
            self._service.tasklists().insert(body={"title": self._task_list_name}).execute()
        )
        return new_list["id"]

    def get_all_items(self) -> Sequence[GTasksItem]:
        """Get all tasks from the task list."""
        if self._service is None or self._task_list_id is None:
            raise RuntimeError("Service not initialized. Call start() first.")

        items: list[GTasksItem] = []
        request = self._service.tasks().list(
            tasklist=self._task_list_id,
            showCompleted=True,
            showDeleted=False,
            showHidden=True,
        )

        while request:
            response = request.execute()
            for item in response.get("items", []):
                if item.get("status") != "deleted":
                    gtask = GTasksItem(item)
                    items.append(gtask)
                    if gtask.id:
                        self._items_cache[gtask.id] = gtask
            request = self._service.tasks().list_next(request, response)

        return items

    def get_item(self, item_id: str) -> GTasksItem | None:
        """Get a single task by ID."""
        if cached := self._items_cache.get(item_id):
            return cached

        if self._service is None or self._task_list_id is None:
            return None

        try:
            item = (
                self._service.tasks()
                .get(
                    tasklist=self._task_list_id,
                    task=item_id,
                )
                .execute()
            )
            gtask = GTasksItem(item)
            self._items_cache[item_id] = gtask
            return gtask
        except HttpError:
            return None

    def add_item(self, item: dict) -> GTasksItem:
        """Add a new task."""
        if self._service is None or self._task_list_id is None:
            raise RuntimeError("Service not initialized. Call start() first.")

        created = (
            self._service.tasks()
            .insert(
                tasklist=self._task_list_id,
                body=item,
            )
            .execute()
        )

        gtask = GTasksItem(created)
        if gtask.id:
            self._items_cache[gtask.id] = gtask
        return gtask

    def update_item(self, item_id: str, **changes) -> GTasksItem:
        """Update an existing task."""
        if self._service is None or self._task_list_id is None:
            raise RuntimeError("Service not initialized. Call start() first.")

        existing = (
            self._service.tasks()
            .get(
                tasklist=self._task_list_id,
                task=item_id,
            )
            .execute()
        )

        existing.update(changes)
        updated = (
            self._service.tasks()
            .update(
                tasklist=self._task_list_id,
                task=item_id,
                body=existing,
            )
            .execute()
        )

        gtask = GTasksItem(updated)
        self._items_cache[item_id] = gtask
        return gtask

    def delete_item(self, item_id: str) -> None:
        """Delete a task."""
        if self._service is None or self._task_list_id is None:
            raise RuntimeError("Service not initialized. Call start() first.")

        self._service.tasks().delete(
            tasklist=self._task_list_id,
            task=item_id,
        ).execute()
        self._items_cache.pop(item_id, None)

    @staticmethod
    def id_key() -> str:
        return GTASKS_ID_KEY

    @staticmethod
    def title_key() -> str:
        return GTASKS_TITLE_KEY

    @staticmethod
    def last_modification_key() -> str:
        return GTASKS_UPDATED_KEY

    def finish(self) -> None:
        """Cleanup when done."""
        pass
