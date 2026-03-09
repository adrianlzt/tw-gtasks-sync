"""Task format conversion between Google Tasks and Taskwarrior."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from tw_gtasks_sync.config import GTASKS_ID_UDA, GTASKS_LIST_UDA
from tw_gtasks_sync.gtasks_side import (
    GTasksItem,
    GTASKS_COMPLETED_KEY,
    GTASKS_DUE_KEY,
    GTASKS_NOTES_KEY,
    GTASKS_STATUS_KEY,
    GTASKS_TITLE_KEY,
    GTASKS_UPDATED_KEY,
)
from tw_gtasks_sync.tw_side import (
    TWItem,
    TW_DESCRIPTION_KEY,
    TW_DUE_KEY,
    TW_END_KEY,
    TW_STATUS_KEY,
    TW_ANNOTATIONS_KEY,
)

if TYPE_CHECKING:
    pass


NOTES_SEPARATOR = "---"
PROJECT_PREFIX = "Project: "
TAGS_PREFIX = "Tags: "
ANNOTATION_PREFIX = "• "


def _parse_notes_to_tw_data(notes: str) -> dict:
    """Parse Google Task notes to extract project, tags, and annotations.

    Args:
        notes: The notes string from Google Tasks

    Returns:
        Dictionary with 'project', 'tags', and 'annotations' keys
    """
    result: dict = {"project": None, "tags": [], "annotations": []}

    if not notes:
        return result

    lines = notes.split("\n")
    annotation_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith(PROJECT_PREFIX):
            result["project"] = line[len(PROJECT_PREFIX) :].strip()
        elif line.startswith(TAGS_PREFIX):
            tags_str = line[len(TAGS_PREFIX) :].strip()
            result["tags"] = [t.strip().lstrip("+") for t in tags_str.split(",") if t.strip()]
        elif line == NOTES_SEPARATOR:
            continue
        elif line.startswith(ANNOTATION_PREFIX):
            annotation_lines.append(line[len(ANNOTATION_PREFIX) :].strip())
        else:
            annotation_lines.append(line)

    result["annotations"] = annotation_lines
    return result


def gtask_to_tw(
    gtask: GTasksItem,
    tw_tag: str,
    tw_list_name: str | None = None,
) -> dict:
    """Convert a Google Task to Taskwarrior format.

    Args:
        gtask: Google Task item
        tw_tag: Tag to apply to the task
        tw_list_name: Optional Google Tasks list name to store as UDA

    Returns:
        Dictionary with Taskwarrior task fields
    """
    tw_item: dict = {}

    tw_item[TW_DESCRIPTION_KEY] = gtask.title

    status_map = {
        "needsAction": "pending",
        "completed": "completed",
    }
    tw_item[TW_STATUS_KEY] = status_map.get(gtask.status, "pending")

    if gtask.due:
        tw_item[TW_DUE_KEY] = gtask.due.isoformat()

    if gtask.completed_date:
        tw_item[TW_END_KEY] = gtask.completed_date.isoformat()

    if gtask.id:
        tw_item[GTASKS_ID_UDA] = gtask.id

    if tw_list_name:
        tw_item[GTASKS_LIST_UDA] = tw_list_name

    if gtask.notes:
        parsed = _parse_notes_to_tw_data(gtask.notes)

        if parsed["project"]:
            tw_item["project"] = parsed["project"]

        all_tags = set(parsed["tags"])
        if tw_tag:
            all_tags.add(tw_tag)
        if all_tags:
            tw_item["tags"] = list(all_tags)

        if parsed["annotations"]:
            tw_item[TW_ANNOTATIONS_KEY] = [
                {"description": ann} for ann in parsed["annotations"]
            ]
    elif tw_tag:
        tw_item["tags"] = [tw_tag]

    return tw_item


def tw_to_gtask(
    tw_item: TWItem,
    sync_tag: str | None = None,
) -> dict:
    """Convert a Taskwarrior task to Google Tasks format.

    Args:
        tw_item: Taskwarrior task item
        sync_tag: The tag used for syncing (will be excluded from notes)

    Returns:
        Dictionary with Google Tasks API fields
    """
    gtask: dict = {}

    gtask[GTASKS_TITLE_KEY] = tw_item.description

    status_map = {
        "pending": "needsAction",
        "completed": "completed",
        "deleted": "needsAction",
        "waiting": "needsAction",
    }
    gtask[GTASKS_STATUS_KEY] = status_map.get(tw_item.status, "needsAction")

    if tw_item.due:
        gtask[GTASKS_DUE_KEY] = tw_item.due.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if tw_item.gtasks_id:
        gtask["id"] = tw_item.gtasks_id

    notes_parts = []

    project = tw_item.get("project")
    if project:
        notes_parts.append(f"{PROJECT_PREFIX}{project}")

    tags = tw_item.get("tags", [])
    if tags:
        filtered_tags = [t for t in tags if t and t != sync_tag]
        if filtered_tags:
            tags_str = ", ".join(f"+{t}" for t in filtered_tags)
            notes_parts.append(f"{TAGS_PREFIX}{tags_str}")

    annotations = tw_item.get(TW_ANNOTATIONS_KEY, [])
    if annotations:
        for ann in annotations:
            if isinstance(ann, dict):
                ann_desc = ann.get("description", "")
                if ann_desc:
                    notes_parts.append(f"{ANNOTATION_PREFIX}{ann_desc}")
            else:
                notes_parts.append(f"{ANNOTATION_PREFIX}{ann}")

    if notes_parts:
        gtask[GTASKS_NOTES_KEY] = "\n".join(notes_parts)

    if tw_item.modified:
        gtask[GTASKS_UPDATED_KEY] = tw_item.modified.isoformat()

    return gtask


def are_items_identical(
    item1: dict,
    item2: dict,
    keys: list[str],
    ignore_keys: list[str] | None = None,
) -> bool:
    """Check if two items are identical based on specified keys.

    Args:
        item1: First item dictionary
        item2: Second item dictionary
        keys: Keys to compare
        ignore_keys: Keys to ignore in comparison

    Returns:
        True if items are identical for the specified keys
    """
    ignore_keys = ignore_keys or []
    compare_keys = [k for k in keys if k not in ignore_keys]

    for key in compare_keys:
        val1 = item1.get(key)
        val2 = item2.get(key)

        if val1 is None and val2 is None:
            continue

        if val1 is None or val2 is None:
            return False

        if isinstance(val1, datetime.datetime) and isinstance(val2, datetime.datetime):
            if abs((val1 - val2).total_seconds()) > 60:
                return False
        elif val1 != val2:
            return False

    return True
