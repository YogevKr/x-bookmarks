"""Continuous on-demand index refresh loop."""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass

from bookmark_query import IndexPaths, get_index_status, refresh_index


@dataclass
class _StopFlag:
    triggered: bool = False


def watch_once(*, paths: IndexPaths | None = None, force: bool = False) -> dict:
    try:
        status = get_index_status(paths=paths)
    except FileNotFoundError:
        return {
            "action": "waiting",
            "reason": "no_source_files",
            "fresh": False,
            "rebuilt": False,
        }

    if status["stale"] or force:
        refreshed = refresh_index(paths=paths, force=force)
        return {
            "action": "refreshed",
            **refreshed,
        }

    return {
        "action": "idle",
        **status,
        "rebuilt": False,
    }


def _format_watch_result(result: dict) -> str:
    action = result.get("action", "unknown")
    if action == "waiting":
        return "waiting: no source bookmark files found"
    if action == "refreshed":
        reason = result.get("reason", "source_changed")
        return f"refreshed: {result.get('doc_count')} docs ({reason}) @ {result.get('built_at')}"
    return f"idle: fresh index ({result.get('doc_count')} docs) @ {result.get('built_at')}"


def run_watch(
    *,
    interval: float = 5.0,
    once: bool = False,
    quiet: bool = False,
    force: bool = False,
    paths: IndexPaths | None = None,
) -> dict | None:
    stop = _StopFlag()

    def _handle_signal(_signum: int, _frame) -> None:
        stop.triggered = True

    previous_sigint = signal.signal(signal.SIGINT, _handle_signal)
    previous_sigterm = signal.signal(signal.SIGTERM, _handle_signal)
    try:
        while not stop.triggered:
            result = watch_once(paths=paths, force=force)
            if not quiet:
                print(_format_watch_result(result), flush=True)
            if once:
                return result

            deadline = time.monotonic() + max(interval, 0.25)
            while time.monotonic() < deadline and not stop.triggered:
                time.sleep(0.25)
        return None
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
