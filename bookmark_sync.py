"""Bookmark source-of-truth import and derived-file reconciliation."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bookmark_query import IndexPaths, default_paths, parse_timestamp, refresh_index

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _read_json(path: Path, *, strict: bool = True) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        if strict:
            raise
        return None


def _write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _normalize_bookmark(bookmark: dict) -> dict:
    handle = str(bookmark.get("handle", "")).strip()
    if handle and not handle.startswith("@"):
        handle = f"@{handle}"
    return {
        "id": str(bookmark["id"]),
        "author": str(bookmark.get("author", "Unknown")),
        "handle": handle or "@unknown",
        "timestamp": str(bookmark.get("timestamp", "")),
        "text": str(bookmark.get("text", "")),
        "media": list(bookmark.get("media", [])),
        "hashtags": list(bookmark.get("hashtags", [])),
        "urls": list(bookmark.get("urls", [])),
    }


def _sort_bookmarks(bookmarks: list[dict]) -> list[dict]:
    return sorted(
        bookmarks,
        key=lambda bookmark: parse_timestamp(bookmark.get("timestamp")) or EPOCH,
        reverse=True,
    )


def _canonical_payload(payload: dict) -> dict:
    bookmarks = [_normalize_bookmark(bookmark) for bookmark in payload.get("bookmarks", [])]
    return {
        "exportDate": payload.get("exportDate"),
        "totalBookmarks": len(bookmarks),
        "source": payload.get("source", "bookmark"),
        "bookmarks": _sort_bookmarks(bookmarks),
    }


def _load_import_payload(*, input_file: Path | None = None, stdin_text: str | None = None) -> dict:
    if stdin_text is not None:
        return _canonical_payload(json.loads(stdin_text))
    if input_file is None:
        raise ValueError("sync requires --input, --stdin, or --reconcile-only")
    with input_file.open(encoding="utf-8") as handle:
        return _canonical_payload(json.load(handle))


def _reconcile_payload(base_payload: dict, existing_payload: dict | None) -> tuple[dict, dict]:
    existing_map = {bookmark["id"]: bookmark for bookmark in (existing_payload or {}).get("bookmarks", [])}
    base_ids = {bookmark["id"] for bookmark in base_payload["bookmarks"]}
    retained_extracted = 0
    retained_ai = 0
    reconciled_bookmarks: list[dict] = []

    for bookmark in base_payload["bookmarks"]:
        merged = dict(bookmark)
        previous = existing_map.get(bookmark["id"])
        if previous and previous.get("extracted"):
            merged["extracted"] = previous["extracted"]
            retained_extracted += 1
        if previous and previous.get("ai"):
            merged["ai"] = previous["ai"]
            retained_ai += 1
        reconciled_bookmarks.append(merged)

    removed = len(set(existing_map) - base_ids)
    return (
        {
            **{key: value for key, value in base_payload.items() if key != "bookmarks"},
            "totalBookmarks": len(reconciled_bookmarks),
            "bookmarks": reconciled_bookmarks,
        },
        {
            "removed": removed,
            "retained_extracted": retained_extracted,
            "retained_ai": retained_ai,
        },
    )


def sync_bookmarks(
    *,
    input_file: Path | None = None,
    stdin_text: str | None = None,
    reconcile_only: bool = False,
    run_extract: bool = False,
    run_categorize: bool = False,
    use_regex: bool = False,
    paths: IndexPaths | None = None,
) -> dict:
    current_paths = paths or default_paths()
    base_before = _read_json(current_paths.bookmarks_file)
    previous_count = len((base_before or {}).get("bookmarks", []))

    if reconcile_only:
        if base_before is None:
            raise FileNotFoundError(f"Missing source-of-truth file: {current_paths.bookmarks_file}")
        base_payload = _canonical_payload(base_before)
    else:
        base_payload = _load_import_payload(input_file=input_file, stdin_text=stdin_text)
        _write_json(current_paths.bookmarks_file, base_payload)

    source_ids = {bookmark["id"] for bookmark in base_payload["bookmarks"]}
    previous_ids = {bookmark["id"] for bookmark in (base_before or {}).get("bookmarks", [])}
    added = len(source_ids - previous_ids)
    removed = len(previous_ids - source_ids)

    enriched_payload, enriched_stats = _reconcile_payload(base_payload, _read_json(current_paths.enriched_file, strict=False))
    categorized_payload, categorized_stats = _reconcile_payload(base_payload, _read_json(current_paths.categorized_file, strict=False))

    if current_paths.enriched_file.exists():
        _write_json(current_paths.enriched_file, enriched_payload)
    if current_paths.categorized_file.exists():
        _write_json(current_paths.categorized_file, categorized_payload)

    if run_extract:
        from extract import run_extraction

        run_extraction()
    if run_categorize:
        from categorize import run_categorization

        run_categorization(force=False, use_regex=use_regex)

    index_state = refresh_index(paths=current_paths)
    return {
        "source_of_truth": str(current_paths.bookmarks_file),
        "input_file": str(input_file) if input_file else None,
        "bookmarks": {
            "previous": previous_count,
            "current": len(base_payload["bookmarks"]),
            "added_estimate": added,
            "removed": removed,
        },
        "derived": {
            "enriched": enriched_stats,
            "categorized": categorized_stats,
        },
        "index": index_state,
    }


def read_stdin_json() -> str:
    payload = sys.stdin.read()
    if not payload.strip():
        raise ValueError("stdin was empty")
    return payload
