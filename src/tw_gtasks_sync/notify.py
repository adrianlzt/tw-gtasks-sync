"""Desktop notifications for conflicts."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import ndiff


@dataclass
class FieldDiff:
    """Difference for a single field."""

    field: str
    tw_current: str | None
    tw_cached: str | None
    gtasks_current: str | None
    gtasks_cached: str | None


@dataclass
class ConflictInfo:
    """Information about a sync conflict."""

    task_title: str
    tw_modified: str | None
    gtasks_modified: str | None
    account_name: str
    tw_id: int | None = None
    tw_uuid: str | None = None
    gtasks_id: str | None = None
    field_diffs: list[FieldDiff] | None = None


def notify_conflict(conflict: ConflictInfo) -> None:
    """Send a desktop notification about a sync conflict.

    Args:
        conflict: Information about the conflict
    """
    print(
        f"\n⚠️  CONFLICT: '{conflict.task_title}'\n"
        f"   Account: {conflict.account_name}\n"
        f"   TW modified: {conflict.tw_modified or 'unknown'}\n"
        f"   GTasks modified: {conflict.gtasks_modified or 'unknown'}\n"
    )

    if conflict.field_diffs:
        print("   Changed fields:")
        for diff in conflict.field_diffs:
            print(f"     {diff.field}:")
            print_field_change("TW", diff.tw_cached, diff.tw_current)
            print_field_change("GTasks", diff.gtasks_cached, diff.gtasks_current)

    print(
        "   Skipping sync for this task.\n"
        "   To resolve:\n"
        f"     1. Edit task in Taskwarrior: task {conflict.tw_id or conflict.tw_uuid or '<uuid>'} edit\n"
        "     2. Or edit in Google Tasks, then run sync again\n"
        "     3. Or force TW -> GTasks: tw-gtasks-sync sync --force\n"
    )

    try:
        from notifypy import Notify

        notification = Notify()
        notification.application_name = "tw-gtasks-sync"
        notification.title = f"Sync Conflict - {conflict.account_name}"
        notification.message = f"Task '{conflict.task_title[:50]}' modified on both sides."
        notification.send()
    except Exception:
        pass


def print_field_change(label: str, before: str | None, after: str | None) -> None:
    if _should_show_text_diff(before, after):
        print(f"       {label} diff:")
        for line in _render_text_diff(before, after):
            print(f"         {line}")
        return

    print(f"       {label} old: {before!r}")
    print(f"       {label} new: {after!r}")


def _should_show_text_diff(before: str | None, after: str | None) -> bool:
    values = [value for value in (before, after) if value]
    if not values:
        return False
    return any("\n" in value for value in values) or any(len(value) > 80 for value in values)


def _render_text_diff(before: str | None, after: str | None) -> list[str]:
    before_lines = [] if before is None else before.splitlines()
    after_lines = [] if after is None else after.splitlines()
    lines = []

    for line in ndiff(before_lines, after_lines):
        if line.startswith("? "):
            continue
        lines.append(line)

    if not lines:
        return ["(no visible text changes)"]

    return lines


def notify_sync_complete(
    account_name: str,
    created_tw: int,
    created_gtasks: int,
    updated_tw: int,
    updated_gtasks: int,
    deleted_tw: int,
    deleted_gtasks: int,
    conflicts: int,
) -> None:
    """Send a notification about sync completion.

    Args:
        account_name: Name of the synced account
        created_tw: Number of tasks created in Taskwarrior
        created_gtasks: Number of tasks created in Google Tasks
        updated_tw: Number of tasks updated in Taskwarrior
        updated_gtasks: Number of tasks updated in Google Tasks
        deleted_tw: Number of tasks deleted in Taskwarrior
        deleted_gtasks: Number of tasks deleted in Google Tasks
        conflicts: Number of conflicts detected
    """
    parts = []
    if created_tw:
        parts.append(f"TW: +{created_tw} created")
    if created_gtasks:
        parts.append(f"GTasks: +{created_gtasks} created")
    if updated_tw:
        parts.append(f"TW: ~{updated_tw} updated")
    if updated_gtasks:
        parts.append(f"GTasks: ~{updated_gtasks} updated")
    if deleted_tw:
        parts.append(f"TW: -{deleted_tw} deleted")
    if deleted_gtasks:
        parts.append(f"GTasks: -{deleted_gtasks} deleted")
    if conflicts:
        parts.append(f"⚠️ {conflicts} conflicts")

    if not parts:
        message = "Everything already in sync!"
    else:
        message = " | ".join(parts)

    try:
        from notifypy import Notify

        notification = Notify()
        notification.application_name = "tw-gtasks-sync"
        notification.title = f"Sync Complete - {account_name}"
        notification.message = message
        notification.send()
    except Exception:
        print(f"✓ Sync complete [{account_name}]: {message}")


def notify_error(message: str, account_name: str | None = None) -> None:
    """Send an error notification.

    Args:
        message: Error message
        account_name: Optional account name
    """
    try:
        from notifypy import Notify

        notification = Notify()
        notification.application_name = "tw-gtasks-sync"
        notification.title = f"Error{f' - {account_name}' if account_name else ''}"
        notification.message = message
        notification.send()
    except Exception:
        print(f"✗ Error: {message}")
