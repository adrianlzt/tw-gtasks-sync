"""Bidirectional synchronization logic."""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from tw_gtasks_sync.config import (
    GTASKS_ID_UDA,
    AccountConfig,
    get_mapping_path,
)
from tw_gtasks_sync.converters import are_items_identical, gtask_to_tw, tw_to_gtask
from tw_gtasks_sync.gtasks_side import GTasksItem, GTasksSide
from tw_gtasks_sync.notify import ConflictInfo, notify_conflict
from tw_gtasks_sync.tw_side import TWItem, TaskWarriorSide

if TYPE_CHECKING:
    pass


COMPARISON_KEYS = ["description", "title", "status", "due", "notes"]


@dataclass
class SyncStats:
    """Statistics from a sync operation."""

    created_tw: int = 0
    created_gtasks: int = 0
    updated_tw: int = 0
    updated_gtasks: int = 0
    deleted_tw: int = 0
    deleted_gtasks: int = 0
    conflicts: int = 0


@dataclass
class CachedItem:
    """Cached item from previous sync."""

    id: str
    modified: datetime | None
    content_hash: str = ""


@dataclass
class Mapping:
    """ID mapping between Taskwarrior and Google Tasks."""

    tw_to_gtasks: dict[str, str] = field(default_factory=dict)
    gtasks_to_tw: dict[str, str] = field(default_factory=dict)
    last_sync: datetime | None = None

    def add(self, tw_uuid: str, gtasks_id: str) -> None:
        """Add a mapping."""
        self.tw_to_gtasks[tw_uuid] = gtasks_id
        self.gtasks_to_tw[gtasks_id] = tw_uuid

    def remove_tw(self, tw_uuid: str) -> None:
        """Remove mapping by Taskwarrior UUID."""
        if tw_uuid in self.tw_to_gtasks:
            gtasks_id = self.tw_to_gtasks.pop(tw_uuid)
            self.gtasks_to_tw.pop(gtasks_id, None)

    def remove_gtasks(self, gtasks_id: str) -> None:
        """Remove mapping by Google Tasks ID."""
        if gtasks_id in self.gtasks_to_tw:
            tw_uuid = self.gtasks_to_tw.pop(gtasks_id)
            self.tw_to_gtasks.pop(tw_uuid, None)

    def get_gtasks_id(self, tw_uuid: str) -> str | None:
        """Get Google Tasks ID for a Taskwarrior UUID."""
        return self.tw_to_gtasks.get(tw_uuid)

    def get_tw_uuid(self, gtasks_id: str) -> str | None:
        """Get Taskwarrior UUID for a Google Tasks ID."""
        return self.gtasks_to_tw.get(gtasks_id)


class Synchronizer:
    """Bidirectional synchronizer between Google Tasks and Taskwarrior."""

    def __init__(
        self,
        *,
        gtasks_side: GTasksSide,
        tw_side: TaskWarriorSide,
        account: AccountConfig,
        serdes_dir: Path,
        force: bool = False,
    ) -> None:
        self._gtasks = gtasks_side
        self._tw = tw_side
        self._account = account
        self._serdes_dir = serdes_dir
        self._serdes_dir.mkdir(parents=True, exist_ok=True)
        self._force = force

        self._mapping = self._load_mapping()
        self._stats = SyncStats()

        self._tw_serdes = self._serdes_dir / "tw"
        self._gtasks_serdes = self._serdes_dir / "gtasks"
        self._tw_serdes.mkdir(exist_ok=True)
        self._gtasks_serdes.mkdir(exist_ok=True)

    def _load_mapping(self) -> Mapping:
        """Load ID mapping from file."""
        mapping_path = get_mapping_path(self._account.name)

        if not mapping_path.exists():
            return Mapping()

        with mapping_path.open("r") as f:
            data = yaml.safe_load(f) or {}

        mapping = Mapping()
        mapping.tw_to_gtasks = data.get("tw_to_gtasks", {})
        mapping.gtasks_to_tw = data.get("gtasks_to_tw", {})

        if last_sync := data.get("last_sync"):
            mapping.last_sync = datetime.fromisoformat(last_sync)

        return mapping

    def _save_mapping(self) -> None:
        """Save ID mapping to file."""
        mapping_path = get_mapping_path(self._account.name)

        data = {
            "tw_to_gtasks": self._mapping.tw_to_gtasks,
            "gtasks_to_tw": self._mapping.gtasks_to_tw,
            "last_sync": datetime.now(timezone.utc).isoformat(),
        }

        with mapping_path.open("w") as f:
            yaml.dump(data, f)

    def _cache_tw_item(self, item: TWItem) -> None:
        """Cache a Taskwarrior item."""
        path = self._tw_serdes / f"{item.uuid}.pickle"
        with path.open("wb") as f:
            pickle.dump(dict(item), f)

    def _cache_gtasks_item(self, item: GTasksItem) -> None:
        """Cache a Google Tasks item."""
        if item.id:
            path = self._gtasks_serdes / f"{item.id}.pickle"
            with path.open("wb") as f:
                pickle.dump(dict(item), f)

    def _get_cached_tw_item(self, uuid: str) -> dict | None:
        """Get cached Taskwarrior item."""
        path = self._tw_serdes / f"{uuid}.pickle"
        if path.exists():
            with path.open("rb") as f:
                return pickle.load(f)
        return None

    def _get_cached_gtasks_item(self, gtasks_id: str) -> dict | None:
        """Get cached Google Tasks item."""
        path = self._gtasks_serdes / f"{gtasks_id}.pickle"
        if path.exists():
            with path.open("rb") as f:
                return pickle.load(f)
        return None

    def _remove_cached_tw_item(self, uuid: str) -> None:
        """Remove cached Taskwarrior item."""
        path = self._tw_serdes / f"{uuid}.pickle"
        path.unlink(missing_ok=True)

    def _remove_cached_gtasks_item(self, gtasks_id: str) -> None:
        """Remove cached Google Tasks item."""
        path = self._gtasks_serdes / f"{gtasks_id}.pickle"
        path.unlink(missing_ok=True)

    def sync(self) -> SyncStats:
        """Perform bidirectional sync."""
        tw_items = {item.uuid: item for item in self._tw.get_all_items()}
        gtasks_items = {item.id: item for item in self._gtasks.get_all_items() if item.id}

        tw_ids = set(tw_items.keys())
        gtasks_ids = set(gtasks_items.keys())

        mapped_tw_ids = set(self._mapping.tw_to_gtasks.keys())
        mapped_gtasks_ids = set(self._mapping.gtasks_to_tw.keys())

        new_tw = tw_ids - mapped_tw_ids
        new_gtasks = gtasks_ids - mapped_gtasks_ids

        existing_tw = tw_ids & mapped_tw_ids
        existing_gtasks = gtasks_ids & mapped_gtasks_ids

        deleted_tw = mapped_tw_ids - tw_ids
        deleted_gtasks = mapped_gtasks_ids - gtasks_ids

        self._process_new_from_tw(new_tw, tw_items, gtasks_items)
        self._process_new_from_gtasks(new_gtasks, gtasks_items, tw_items)

        self._process_deleted_tw(deleted_tw)
        self._process_deleted_gtasks(deleted_gtasks)

        self._process_updates(existing_tw, tw_items, gtasks_items)

        self._save_mapping()

        return self._stats

    def _process_new_from_tw(
        self,
        new_tw_ids: set[str],
        tw_items: dict[str, TWItem],
        gtasks_items: dict[str, GTasksItem],
    ) -> None:
        """Process new tasks from Taskwarrior."""
        for tw_uuid in new_tw_ids:
            tw_item = tw_items[tw_uuid]

            existing_gtasks_id = tw_item.gtasks_id
            if existing_gtasks_id and existing_gtasks_id in gtasks_items:
                self._mapping.add(tw_uuid, existing_gtasks_id)
                self._cache_tw_item(tw_item)
                self._cache_gtasks_item(gtasks_items[existing_gtasks_id])
                continue

            gtask_data = tw_to_gtask(tw_item, sync_tag=self._account.tw_tag)
            created = self._gtasks.add_item(gtask_data)

            if created.id:
                self._mapping.add(tw_uuid, created.id)
                self._cache_tw_item(tw_item)
                self._cache_gtasks_item(created)

                tw_update = {GTASKS_ID_UDA: created.id}
                self._tw.update_item(tw_uuid, **tw_update)

                self._stats.created_gtasks += 1

    def _process_new_from_gtasks(
        self,
        new_gtasks_ids: set[str],
        gtasks_items: dict[str, GTasksItem],
        tw_items: dict[str, TWItem],
    ) -> None:
        """Process new tasks from Google Tasks."""
        tw_items_by_gtasks_id = {
            item.gtasks_id: item for item in tw_items.values() if item.gtasks_id
        }

        for gtasks_id in new_gtasks_ids:
            existing_tw = tw_items_by_gtasks_id.get(gtasks_id)
            if existing_tw:
                self._mapping.add(existing_tw.uuid, gtasks_id)
                self._cache_tw_item(existing_tw)
                self._cache_gtasks_item(gtasks_items[gtasks_id])
                continue

            gtasks_item = gtasks_items[gtasks_id]
            tw_data = gtask_to_tw(gtasks_item, self._account.tw_tag)
            created = self._tw.add_item(tw_data)

            self._mapping.add(created.uuid, gtasks_id)
            self._cache_tw_item(created)
            self._cache_gtasks_item(gtasks_item)

            self._stats.created_tw += 1

    def _process_deleted_tw(self, deleted_tw_ids: set[str]) -> None:
        """Process deleted Taskwarrior tasks."""
        for tw_uuid in deleted_tw_ids:
            gtasks_id = self._mapping.get_gtasks_id(tw_uuid)

            if gtasks_id:
                try:
                    self._gtasks.delete_item(gtasks_id)
                    self._stats.deleted_gtasks += 1
                except Exception:
                    pass

                self._mapping.remove_tw(tw_uuid)
                self._remove_cached_tw_item(tw_uuid)
                self._remove_cached_gtasks_item(gtasks_id)

    def _process_deleted_gtasks(self, deleted_gtasks_ids: set[str]) -> None:
        """Process deleted Google Tasks."""
        for gtasks_id in deleted_gtasks_ids:
            tw_uuid = self._mapping.get_tw_uuid(gtasks_id)

            if tw_uuid:
                try:
                    self._tw.delete_item(tw_uuid)
                    self._stats.deleted_tw += 1
                except Exception:
                    pass

                self._mapping.remove_gtasks(gtasks_id)
                self._remove_cached_tw_item(tw_uuid)
                self._remove_cached_gtasks_item(gtasks_id)

    def _process_updates(
        self,
        existing_tw_ids: set[str],
        tw_items: dict[str, TWItem],
        gtasks_items: dict[str, GTasksItem],
    ) -> None:
        """Process updates to existing items."""
        for tw_uuid in existing_tw_ids:
            gtasks_id = self._mapping.get_gtasks_id(tw_uuid)
            if not gtasks_id:
                continue

            tw_item = tw_items.get(tw_uuid)
            gtasks_item = gtasks_items.get(gtasks_id)

            if not tw_item or not gtasks_item:
                continue

            if self._force:
                gtask_data = tw_to_gtask(tw_item, sync_tag=self._account.tw_tag)
                del gtask_data["id"]
                self._gtasks.update_item(gtasks_id, **gtask_data)
                self._cache_tw_item(tw_item)
                self._cache_gtasks_item(gtasks_item)
                self._stats.updated_gtasks += 1
                continue

            cached_tw = self._get_cached_tw_item(tw_uuid)
            cached_gtasks = self._get_cached_gtasks_item(gtasks_id)

            tw_changed = self._item_changed(tw_item, cached_tw, "tw")
            gtasks_changed = self._item_changed(gtasks_item, cached_gtasks, "gtasks")

            if tw_changed and gtasks_changed:
                self._handle_conflict(tw_item, gtasks_item)
                continue

            if tw_changed:
                gtask_data = tw_to_gtask(tw_item, sync_tag=self._account.tw_tag)
                del gtask_data["id"]
                self._gtasks.update_item(gtasks_id, **gtask_data)
                self._cache_gtasks_item(gtasks_item)
                self._stats.updated_gtasks += 1

            elif gtasks_changed:
                tw_data = gtask_to_tw(gtasks_item, self._account.tw_tag)
                del tw_data[GTASKS_ID_UDA]
                self._tw.update_item(tw_uuid, **tw_data)
                self._cache_tw_item(tw_item)
                self._stats.updated_tw += 1

    def _item_changed(
        self,
        current: dict,
        cached: dict | None,
        side: str,
    ) -> bool:
        """Check if an item has changed since last sync."""
        if cached is None:
            return False

        comparison_keys = COMPARISON_KEYS.copy()
        if side == "tw":
            comparison_keys.append("annotations")

        return not are_items_identical(
            current,
            cached,
            comparison_keys,
            ignore_keys=["uuid", "id", "modified", "updated", GTASKS_ID_UDA],
        )

    def _handle_conflict(self, tw_item: TWItem, gtasks_item: GTasksItem) -> None:
        """Handle a sync conflict."""
        self._stats.conflicts += 1

        conflict = ConflictInfo(
            task_title=tw_item.description[:50],
            tw_modified=str(tw_item.modified) if tw_item.modified else None,
            gtasks_modified=str(gtasks_item.updated) if gtasks_item.updated else None,
            account_name=self._account.name,
            tw_uuid=tw_item.uuid,
            gtasks_id=gtasks_item.id,
        )

        notify_conflict(conflict)
