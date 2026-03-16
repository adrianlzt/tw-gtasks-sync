"""Taskwarrior integration."""

from __future__ import annotations

import datetime
import subprocess
import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from dateutil import parser as dateutil_parser

from tw_gtasks_sync.config import (
    GTASKS_ID_UDA,
    GTASKS_LIST_UDA,
    get_taskrc_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TW_UUID_KEY = "uuid"
TW_DESCRIPTION_KEY = "description"
TW_STATUS_KEY = "status"
TW_DUE_KEY = "due"
TW_SCHEDULED_KEY = "scheduled"
TW_END_KEY = "end"
TW_MODIFIED_KEY = "modified"
TW_ENTRY_KEY = "entry"
TW_ANNOTATIONS_KEY = "annotations"


class TWItem(dict):
    """Taskwarrior item representation."""

    @property
    def uuid(self) -> str:
        return str(self.get(TW_UUID_KEY, ""))

    @property
    def id(self) -> int | None:
        value = self.get("id")
        if value is None:
            return None
        task_id = int(value)
        if task_id <= 0:
            return None
        return task_id

    @property
    def description(self) -> str:
        return self.get(TW_DESCRIPTION_KEY, "")

    @property
    def status(self) -> str:
        return self.get(TW_STATUS_KEY, "pending")

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def due(self) -> datetime.datetime | None:
        due_str = self.get(TW_DUE_KEY)
        if due_str:
            return self._parse_datetime(due_str)
        return None

    @property
    def scheduled(self) -> datetime.datetime | None:
        scheduled_str = self.get(TW_SCHEDULED_KEY)
        if scheduled_str:
            return self._parse_datetime(scheduled_str)
        return None

    @property
    def end(self) -> datetime.datetime | None:
        end_str = self.get(TW_END_KEY)
        if end_str:
            return self._parse_datetime(end_str)
        return None

    @property
    def modified(self) -> datetime.datetime | None:
        modified_str = self.get(TW_MODIFIED_KEY)
        if modified_str:
            return self._parse_datetime(modified_str)
        return None

    @property
    def gtasks_id(self) -> str | None:
        return self.get(GTASKS_ID_UDA)

    @property
    def gtasks_list(self) -> str | None:
        return self.get(GTASKS_LIST_UDA)

    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime.datetime:
        """Parse Taskwarrior datetime string."""
        dt = dateutil_parser.parse(dt_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)


class TaskWarriorSide:
    """Taskwarrior client using `task` command."""

    def __init__(
        self,
        *,
        tag: str,
        config_path: Path | None = None,
        exclude_uda: str | None = None,
    ) -> None:
        self._tag = tag
        self._config_path = config_path or get_taskrc_path()
        self._exclude_uda = exclude_uda
        self._items_cache: dict[str, TWItem] = {}

    def _run_task(self, *args: str, input_data: str | None = None) -> str:
        """Run a taskwarrior command and return output."""
        cmd = ["task", f"rc:{self._config_path}"]
        cmd.extend(args)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=input_data,
        )

        if result.returncode != 0 and "error" in result.stderr.lower():
            raise RuntimeError(f"Taskwarrior error: {result.stderr}")

        return result.stdout

    def _run_task_json(self, *args: str) -> list[dict]:
        """Run taskwarrior command and parse JSON output."""
        cmd_args = list(args) + ["export"]
        output = self._run_task(*cmd_args)

        if not output.strip():
            return []

        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return []

    def start(self) -> None:
        """Initialize the Taskwarrior side."""
        pass

    def get_all_items(self) -> Sequence[TWItem]:
        """Get all tasks with the specified tag."""
        filter_args = [f"+{self._tag}"]

        items_data = self._run_task_json(*filter_args)

        items: list[TWItem] = []
        for item_dict in items_data:
            if self._exclude_uda and item_dict.get(self._exclude_uda):
                continue
            if item_dict.get("status") == "deleted":
                continue
            item = TWItem(item_dict)
            items.append(item)
            self._items_cache[item.uuid] = item

        return items

    def get_item(self, item_id: str) -> TWItem | None:
        """Get a single task by UUID."""
        if cached := self._items_cache.get(item_id):
            return cached

        try:
            items_data = self._run_task_json(str(item_id))
            if items_data:
                item = TWItem(items_data[0])
                self._items_cache[item_id] = item
                return item
        except (RuntimeError, json.JSONDecodeError):
            pass

        return None

    def add_item(self, item: dict) -> TWItem:
        """Add a new task."""
        description = item.pop("description", "Untitled")

        args = ["add", description]

        if tags := item.pop("tags", None):
            if isinstance(tags, list):
                for tag in tags:
                    args.append(f"+{tag}")
            else:
                args.append(f"+{tags}")

        if self._tag:
            args.append(f"+{self._tag}")

        if due := item.pop("due", None):
            args.append(f"due:{due}")

        if scheduled := item.pop("scheduled", None):
            args.append(f"scheduled:{scheduled}")

        if gtasks_id := item.pop(GTASKS_ID_UDA, None):
            args.append(f"{GTASKS_ID_UDA}:{gtasks_id}")

        if gtasks_list := item.pop(GTASKS_LIST_UDA, None):
            args.append(f"{GTASKS_LIST_UDA}:{gtasks_list}")

        if status := item.pop("status", None):
            if status == "completed":
                args.append("status:completed")

        for key, value in item.items():
            args.append(f"{key}:{value}")

        output = self._run_task(*args)

        new_uuid = self._extract_uuid_from_output(output)
        if new_uuid:
            new_item = self.get_item(new_uuid)
            if new_item:
                return new_item
            raise RuntimeError("Failed to get newly created task by UUID")

        new_id = self._extract_task_id_from_output(output)
        if new_id:
            items_data = self._run_task_json(str(new_id))
            if items_data:
                new_item = TWItem(items_data[0])
                new_uuid = new_item.uuid
                self._items_cache[new_uuid] = new_item
                return new_item

        raise RuntimeError("Failed to get UUID of newly created task")

    def _extract_task_id_from_output(self, output: str) -> int | None:
        """Extract task ID from taskwarrior output like 'Created task 23.'"""
        import re

        match = re.search(r"Created task (\d+)", output)
        if match:
            return int(match.group(1))
        return None

    def _extract_uuid_from_output(self, output: str) -> str | None:
        """Extract UUID from taskwarrior output."""
        import re

        match = re.search(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            output,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).lower()
        return None

    def update_item(self, item_id: str, **changes) -> TWItem:
        """Update an existing task."""
        for key, value in changes.items():
            if key == TW_UUID_KEY:
                continue
            if key == "tags":
                continue
            if key == TW_ANNOTATIONS_KEY:
                continue

            if key == GTASKS_ID_UDA:
                self._run_task(str(item_id), "modify", f"{GTASKS_ID_UDA}:{value}")
            elif key == TW_STATUS_KEY:
                if value == "completed":
                    self._run_task(str(item_id), "done")
                elif value == "pending":
                    self._run_task(str(item_id), "modify", "status:pending")
            else:
                self._run_task(str(item_id), "modify", f"{key}:{value}")

        item = self.get_item(item_id)
        if item:
            self._items_cache[item_id] = item
        return item

    def delete_item(self, item_id: str) -> None:
        """Delete a task."""
        self._run_task(str(item_id), "delete", "rc.confirmation=off")
        self._items_cache.pop(item_id, None)

    def mark_completed(self, item_id: str) -> None:
        """Mark a task as completed."""
        self._run_task(str(item_id), "done")

    @staticmethod
    def id_key() -> str:
        return TW_UUID_KEY

    @staticmethod
    def summary_key() -> str:
        return TW_DESCRIPTION_KEY

    @staticmethod
    def last_modification_key() -> str:
        return TW_MODIFIED_KEY

    def finish(self) -> None:
        """Cleanup when done."""
        pass
