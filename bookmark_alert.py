"""Stale source export checks and optional macOS notifications."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from bookmark_query import IndexPaths, default_paths, get_index_status

DEFAULT_MAX_AGE_HOURS = 36.0
DEFAULT_ALERT_EVERY_HOURS = 24.0
NOTIFICATION_TITLE = "x-bookmarks export stale"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def _run_notification(message: str, *, title: str = NOTIFICATION_TITLE) -> tuple[bool, str | None]:
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode == 0:
        return True, None
    error = (result.stderr or result.stdout or "").strip()
    return False, error or f"osascript exited {result.returncode}"


def _should_notify(
    *,
    state: dict,
    reference_at: str | None,
    now: datetime,
    alert_every_seconds: int,
) -> tuple[bool, str]:
    last_alert_at = _parse_dt(state.get("last_alert_at"))
    last_reference_at = state.get("last_reference_at")
    if last_alert_at is None:
        return True, "first_alert"
    if reference_at and reference_at != last_reference_at:
        return True, "source_reference_changed"
    if int((now - last_alert_at).total_seconds()) >= alert_every_seconds:
        return True, "alert_interval_elapsed"
    return False, "recently_alerted"


def check_stale_source_export(
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    notify: bool = False,
    alert_every_hours: float = DEFAULT_ALERT_EVERY_HOURS,
    state_file: Path | None = None,
    paths: IndexPaths | None = None,
) -> dict:
    current_paths = paths or default_paths()
    status = get_index_status(paths=current_paths)
    freshness = status.get("source_freshness", {})
    bookmark_snapshot = status.get("source_state", {}).get("files", {}).get("bookmarks", {})
    max_age_seconds = int(max_age_hours * 60 * 60)
    alert_every_seconds = int(alert_every_hours * 60 * 60)
    age_seconds = freshness.get("age_seconds")
    reference_at = freshness.get("reference_at")
    reason = "fresh"

    if not bookmark_snapshot.get("exists"):
        reason = "bookmarks_file_missing"
    elif not bookmark_snapshot.get("valid_json"):
        reason = "bookmarks_file_invalid"
    elif age_seconds is None:
        reason = "source_reference_missing"
    elif age_seconds > max_age_seconds:
        reason = "source_age_exceeded"

    stale = reason != "fresh"
    message = "x-bookmarks source export is fresh"
    if stale:
        if age_seconds is None:
            message = f"x-bookmarks source export stale: {reason.replace('_', ' ')}"
        else:
            age_hours = age_seconds / 3600
            message = f"x-bookmarks source export is {age_hours:.1f}h old; threshold is {max_age_hours:g}h"

    now = _now()
    target_state_file = state_file or (current_paths.data_dir / "stale-check-state.json")
    state = _read_state(target_state_file) if notify else {}
    notified = False
    notification_error = None
    notification_reason = None

    if notify and stale:
        should_notify, notification_reason = _should_notify(
            state=state,
            reference_at=reference_at,
            now=now,
            alert_every_seconds=alert_every_seconds,
        )
        if should_notify:
            notified, notification_error = _run_notification(message)
            if notified:
                state["last_alert_at"] = now.isoformat()
    elif notify:
        notification_reason = "fresh"

    if notify:
        state["last_check_at"] = now.isoformat()
        state["last_reference_at"] = reference_at
        state["last_stale"] = stale
        if not stale:
            state["last_ok_at"] = now.isoformat()
        _write_state(target_state_file, state)

    return {
        "ok": not stale,
        "stale": stale,
        "reason": reason,
        "message": message,
        "max_age_hours": max_age_hours,
        "max_age_seconds": max_age_seconds,
        "source_age_seconds": age_seconds,
        "source_reference_at": reference_at,
        "source_exported_at": freshness.get("source_exported_at"),
        "source_modified_at": freshness.get("source_modified_at"),
        "bookmark_count": bookmark_snapshot.get("bookmark_count"),
        "bookmarks_path": bookmark_snapshot.get("path"),
        "state_file": str(target_state_file),
        "notified": notified,
        "notification_reason": notification_reason,
        "notification_error": notification_error,
    }
