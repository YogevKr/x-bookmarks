"""Continuous on-demand index refresh loop."""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from bookmark_query import INDEX_VERSION, IndexPaths, default_paths, get_index_status, refresh_index


@dataclass
class _StopFlag:
    triggered: bool = False


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _load_watch_state(paths: IndexPaths) -> dict:
    if not paths.watch_state_file.exists():
        return {}
    try:
        with paths.watch_state_file.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_watch_state(paths: IndexPaths, payload: dict) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    previous = _load_watch_state(paths)
    merged = {**previous, **payload}
    temporary_path = paths.watch_state_file.with_suffix(".json.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary_path.replace(paths.watch_state_file)
    except OSError:
        return


def _load_manifest(paths: IndexPaths) -> dict | None:
    if not paths.manifest_file.exists():
        return None
    try:
        with paths.manifest_file.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


def _source_mtime_signature(paths: IndexPaths) -> dict | None:
    signature = {}
    for name, path in (
        ("bookmarks", paths.bookmarks_file),
        ("enriched", paths.enriched_file),
        ("categorized", paths.categorized_file),
    ):
        try:
            if path.exists():
                signature[name] = {"exists": True, "mtime_ns": path.stat().st_mtime_ns}
            else:
                signature[name] = {"exists": False, "mtime_ns": None}
        except OSError:
            return None
    return signature


def _manifest_mtime_signature(manifest: dict) -> dict:
    files = (manifest.get("source_state") or {}).get("files") or {}
    return {
        name: {
            "exists": bool((files.get(name) or {}).get("exists")),
            "mtime_ns": (files.get(name) or {}).get("mtime_ns"),
        }
        for name in ("bookmarks", "enriched", "categorized")
    }


def _quick_idle_status(paths: IndexPaths) -> dict | None:
    if not paths.index_db.exists():
        return None

    manifest = _load_manifest(paths)
    if not manifest or manifest.get("version") != INDEX_VERSION:
        return None

    current_signature = _source_mtime_signature(paths)
    if current_signature is None or current_signature != _manifest_mtime_signature(manifest):
        return None

    return {
        "action": "idle",
        "fresh": True,
        "stale": False,
        "reasons": [],
        "index_db": str(paths.index_db),
        "manifest_path": str(paths.manifest_file),
        "doc_count": manifest.get("doc_count"),
        "indexed_count": manifest.get("doc_count"),
        "source_state": manifest.get("source_state"),
        "watch_state": _load_watch_state(paths),
        "built_at": manifest.get("built_at"),
        "rebuilt": False,
    }


def watch_once(*, paths: IndexPaths | None = None, force: bool = False) -> dict:
    current_paths = paths or default_paths()
    if not force:
        quick_status = _quick_idle_status(current_paths)
        if quick_status is not None:
            _save_watch_state(
                current_paths,
                {
                    "pid": os.getpid(),
                    "last_watch_tick": _now_iso(),
                    "last_action": quick_status["action"],
                    "last_error": None,
                },
            )
            return quick_status

    try:
        status = get_index_status(paths=current_paths)
    except FileNotFoundError:
        result = {
            "action": "waiting",
            "reason": "no_source_files",
            "fresh": False,
            "rebuilt": False,
        }
        _save_watch_state(
            current_paths,
            {
                "pid": os.getpid(),
                "last_watch_tick": _now_iso(),
                "last_action": result["action"],
            },
        )
        return result

    if status["stale"] or force:
        refreshed = refresh_index(paths=current_paths, force=force)
        result = {
            "action": "refreshed",
            **refreshed,
        }
        _save_watch_state(
            current_paths,
            {
                "pid": os.getpid(),
                "last_watch_tick": _now_iso(),
                "last_action": result["action"],
                "last_refresh_at": result.get("built_at"),
                "last_refresh_reason": result.get("reason"),
                "last_error": None,
            },
        )
        return result

    result = {
        "action": "idle",
        **status,
        "rebuilt": False,
    }
    _save_watch_state(
        current_paths,
        {
            "pid": os.getpid(),
            "last_watch_tick": _now_iso(),
            "last_action": result["action"],
            "last_error": None,
        },
    )
    return result


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
            current_paths = paths or default_paths()
            try:
                result = watch_once(paths=current_paths, force=force)
            except Exception as error:
                _save_watch_state(
                    current_paths,
                    {
                        "pid": os.getpid(),
                        "last_watch_tick": _now_iso(),
                        "last_action": "error",
                        "last_error": f"{type(error).__name__}: {error}",
                    },
                )
                if not quiet:
                    print(f"error: {error}", flush=True)
                if once:
                    raise
                result = None
            if not quiet:
                if result is not None:
                    print(_format_watch_result(result), flush=True)
            if once and result is not None:
                return result

            deadline = time.monotonic() + max(interval, 0.25)
            while time.monotonic() < deadline and not stop.triggered:
                time.sleep(0.25)
        return None
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
