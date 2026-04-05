"""Bookmark source-of-truth import and bidirectional local reconciliation."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from bookmark_query import IndexPaths, default_paths, parse_timestamp, refresh_index
from text_repair import repair_value

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
STATE_VERSION = 1
FILE_KEYS = ("bookmarks", "enriched", "categorized")


def _read_json(path: Path, *, strict: bool = True) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return repair_value(json.load(handle))
    except json.JSONDecodeError:
        if strict:
            raise
        return None


def _write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _normalize_local_metadata(local: dict | None) -> dict:
    if not isinstance(local, dict):
        return {}

    note = str(local.get("note", "")).strip()
    raw_tags = local.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags if isinstance(raw_tags, list) else []:
        value = str(tag).strip().lstrip("#").casefold()
        if not value or value in seen:
            continue
        seen.add(value)
        tags.append(value)

    rating = local.get("rating")
    normalized: dict = {}
    if note:
        normalized["note"] = note
    if tags:
        normalized["tags"] = tags
    if isinstance(rating, int) and 1 <= rating <= 5:
        normalized["rating"] = rating
    if bool(local.get("hidden")):
        normalized["hidden"] = True
    if local.get("hidden_at") and normalized.get("hidden"):
        normalized["hidden_at"] = str(local["hidden_at"])
    if normalized:
        normalized["updated_at"] = str(local.get("updated_at") or _now_iso())
    return normalized


def _normalize_bookmark(bookmark: dict) -> dict:
    handle = str(bookmark.get("handle", "")).strip()
    if handle and not handle.startswith("@"):
        handle = f"@{handle}"
    normalized = {
        "id": str(bookmark["id"]),
        "author": str(bookmark.get("author", "Unknown")),
        "handle": handle or "@unknown",
        "timestamp": str(bookmark.get("timestamp", "")),
        "text": str(bookmark.get("text", "")),
        "media": list(bookmark.get("media", [])),
        "hashtags": list(bookmark.get("hashtags", [])),
        "urls": list(bookmark.get("urls", [])),
    }
    if bookmark.get("linked_pages"):
        normalized["linked_pages"] = repair_value(bookmark["linked_pages"])
    if bookmark.get("extracted"):
        normalized["extracted"] = repair_value(bookmark["extracted"])
    if bookmark.get("extract_failures"):
        normalized["extract_failures"] = repair_value(bookmark["extract_failures"])
    if bookmark.get("ai"):
        normalized["ai"] = repair_value(bookmark["ai"])
    local = _normalize_local_metadata(repair_value(bookmark.get("local")))
    if local:
        normalized["local"] = local
    return normalized


def _sort_bookmarks(bookmarks: list[dict]) -> list[dict]:
    return sorted(
        bookmarks,
        key=lambda bookmark: parse_timestamp(bookmark.get("timestamp")) or EPOCH,
        reverse=True,
    )


def _canonical_payload(payload: dict) -> dict:
    payload = repair_value(payload)
    bookmarks = [_normalize_bookmark(bookmark) for bookmark in payload.get("bookmarks", [])]
    return {
        "exportDate": payload.get("exportDate"),
        "totalBookmarks": len(bookmarks),
        "source": payload.get("source", "bookmark"),
        "bookmarks": _sort_bookmarks(bookmarks),
    }


def _empty_payload(reference: dict | None = None) -> dict:
    reference = reference or {}
    return {
        "exportDate": reference.get("exportDate"),
        "totalBookmarks": 0,
        "source": reference.get("source", "bookmark"),
        "bookmarks": [],
    }


def _load_import_payload(*, input_file: Path | None = None, stdin_text: str | None = None) -> dict:
    if stdin_text is not None:
        return _canonical_payload(repair_value(json.loads(stdin_text)))
    if input_file is None:
        raise ValueError("sync requires --input, --stdin, or --reconcile-only")
    with input_file.open(encoding="utf-8") as handle:
        return _canonical_payload(repair_value(json.load(handle)))


def _bookmark_ids(payload: dict | None) -> set[str]:
    return {bookmark["id"] for bookmark in (payload or {}).get("bookmarks", [])}


def _payload_map(payload: dict | None) -> dict[str, dict]:
    return {bookmark["id"]: bookmark for bookmark in (payload or {}).get("bookmarks", [])}


def _state_path_snapshot(path: Path, payload: dict | None) -> dict:
    return {
        "exists": path.exists(),
        "ids": sorted(_bookmark_ids(payload)),
    }


def _empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "updated_at": None,
        "tombstones": {},
        "archive": {},
        "snapshots": {key: {"exists": False, "ids": []} for key in FILE_KEYS},
    }


def _load_sync_state(paths: IndexPaths) -> dict:
    state = _read_json(paths.sync_state_file, strict=False)
    if not state:
        return _empty_state()

    normalized = _empty_state()
    normalized["version"] = state.get("version", STATE_VERSION)
    normalized["updated_at"] = state.get("updated_at")
    normalized["tombstones"] = {
        str(bookmark_id): repair_value(value)
        for bookmark_id, value in state.get("tombstones", {}).items()
    }
    normalized["archive"] = {
        str(bookmark_id): _normalize_bookmark(value)
        for bookmark_id, value in state.get("archive", {}).items()
        if isinstance(value, dict) and value.get("id")
    }
    snapshots = state.get("snapshots", {})
    for key in FILE_KEYS:
        snapshot = snapshots.get(key, {})
        normalized["snapshots"][key] = {
            "exists": bool(snapshot.get("exists")),
            "ids": sorted({str(bookmark_id) for bookmark_id in snapshot.get("ids", [])}),
        }
    return normalized


def _save_sync_state(paths: IndexPaths, state: dict) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "updated_at": _now_iso(),
        "tombstones": state.get("tombstones", {}),
        "archive": state.get("archive", {}),
        "snapshots": state.get("snapshots", {}),
    }
    _write_json(paths.sync_state_file, payload)


def _record_tombstone(state: dict, bookmark_id: str, *, source: str, bookmark: dict | None = None) -> None:
    state.setdefault("tombstones", {})[bookmark_id] = {
        "deleted_at": _now_iso(),
        "source": source,
    }
    if bookmark:
        state.setdefault("archive", {})[bookmark_id] = _normalize_bookmark(bookmark)


def _base_record(bookmark: dict) -> dict:
    record = {
        key: repair_value(bookmark.get(key))
        for key in ("id", "author", "handle", "timestamp", "text", "media", "hashtags", "urls")
    }
    local = _normalize_local_metadata(repair_value(bookmark.get("local")))
    if local:
        record["local"] = local
    return record


def _enriched_record(bookmark: dict) -> dict:
    record = _base_record(bookmark)
    if bookmark.get("linked_pages"):
        record["linked_pages"] = repair_value(bookmark["linked_pages"])
    if bookmark.get("extracted"):
        record["extracted"] = repair_value(bookmark["extracted"])
    if bookmark.get("extract_failures"):
        record["extract_failures"] = repair_value(bookmark["extract_failures"])
    return record


def _categorized_record(bookmark: dict) -> dict:
    record = _enriched_record(bookmark)
    if bookmark.get("ai"):
        record["ai"] = repair_value(bookmark["ai"])
    return record


def _merge_record(
    bookmark_id: str,
    *,
    base_map: dict[str, dict],
    enriched_map: dict[str, dict],
    categorized_map: dict[str, dict],
    archive_map: dict[str, dict],
) -> dict | None:
    base = base_map.get(bookmark_id)
    enriched = enriched_map.get(bookmark_id)
    categorized = categorized_map.get(bookmark_id)
    archived = archive_map.get(bookmark_id)
    seed = base or enriched or categorized or archived
    if seed is None:
        return None

    merged = _normalize_bookmark(seed)
    for candidate in (archived, enriched, categorized):
        if not candidate:
            continue
        if candidate.get("linked_pages"):
            merged["linked_pages"] = repair_value(candidate["linked_pages"])
        if candidate.get("extracted"):
            merged["extracted"] = repair_value(candidate["extracted"])
        if candidate.get("extract_failures"):
            merged["extract_failures"] = repair_value(candidate["extract_failures"])
        if candidate.get("ai"):
            merged["ai"] = repair_value(candidate["ai"])
        if candidate.get("local") is not None:
            local = _normalize_local_metadata(repair_value(candidate.get("local")))
            if local:
                merged["local"] = local
            else:
                merged.pop("local", None)
    return merged


def _render_payload(reference: dict, bookmarks: list[dict], *, kind: str) -> dict:
    render = {
        "bookmarks": _base_record,
        "enriched": _enriched_record,
        "categorized": _categorized_record,
    }[kind]
    output = {
        **{key: value for key, value in reference.items() if key != "bookmarks"},
        "totalBookmarks": len(bookmarks),
        "bookmarks": [render(bookmark) for bookmark in bookmarks],
    }
    return output


def _load_active_bookmarks(paths: IndexPaths, state: dict) -> tuple[dict, list[dict]]:
    base_payload = _read_json(paths.bookmarks_file, strict=False)
    enriched_payload = _read_json(paths.enriched_file, strict=False)
    categorized_payload = _read_json(paths.categorized_file, strict=False)
    base_map = _payload_map(base_payload)
    enriched_map = _payload_map(enriched_payload)
    categorized_map = _payload_map(categorized_payload)
    archive_map = {
        str(bookmark_id): _normalize_bookmark(bookmark)
        for bookmark_id, bookmark in state.get("archive", {}).items()
    }
    active_ids = (set(base_map) | set(enriched_map) | set(categorized_map)) - set(state.get("tombstones", {}))
    merged_bookmarks = [
        merged
        for bookmark_id in active_ids
        if (
            merged := _merge_record(
                bookmark_id,
                base_map=base_map,
                enriched_map=enriched_map,
                categorized_map=categorized_map,
                archive_map=archive_map,
            )
        )
    ]
    return (base_payload or enriched_payload or categorized_payload or _empty_payload(), _sort_bookmarks(merged_bookmarks))


def _persist_bookmarks(
    *,
    reference_payload: dict,
    bookmarks: list[dict],
    state: dict,
    paths: IndexPaths,
    run_extract: bool = False,
    run_categorize: bool = False,
    use_regex: bool = False,
) -> dict:
    output_base = _render_payload(reference_payload, bookmarks, kind="bookmarks")
    output_enriched = _render_payload(reference_payload, bookmarks, kind="enriched")
    output_categorized = _render_payload(reference_payload, bookmarks, kind="categorized")

    _write_json(paths.bookmarks_file, output_base)
    should_write_enriched = paths.enriched_file.exists() or any(
        bookmark.get("linked_pages") or bookmark.get("extracted") or bookmark.get("local")
        for bookmark in bookmarks
    )
    should_write_categorized = paths.categorized_file.exists() or any(
        bookmark.get("ai") or bookmark.get("local")
        for bookmark in bookmarks
    )
    if should_write_enriched:
        _write_json(paths.enriched_file, output_enriched)
    if should_write_categorized:
        _write_json(paths.categorized_file, output_categorized)

    if run_extract:
        from extract import run_extraction

        run_extraction()
    if run_categorize:
        from categorize import run_categorization

        run_categorization(force=False, use_regex=use_regex)

    index_state = refresh_index(paths=paths)
    state["snapshots"] = {
        "bookmarks": _state_path_snapshot(paths.bookmarks_file, output_base),
        "enriched": _state_path_snapshot(paths.enriched_file, output_enriched if should_write_enriched else None),
        "categorized": _state_path_snapshot(paths.categorized_file, output_categorized if should_write_categorized else None),
    }
    _save_sync_state(paths, state)
    return {
        "base": output_base,
        "enriched": output_enriched if should_write_enriched else None,
        "categorized": output_categorized if should_write_categorized else None,
        "has_enriched": should_write_enriched,
        "has_categorized": should_write_categorized,
        "index": index_state,
    }


def sync_bookmarks(
    *,
    input_file: Path | None = None,
    stdin_text: str | None = None,
    reconcile_only: bool = False,
    run_extract: bool = False,
    run_categorize: bool = False,
    use_regex: bool = False,
    restore_ids: set[str] | None = None,
    state_override: dict | None = None,
    paths: IndexPaths | None = None,
) -> dict:
    current_paths = paths or default_paths()
    restore_ids = {str(bookmark_id) for bookmark_id in (restore_ids or set())}
    state = repair_value(state_override) if state_override is not None else _load_sync_state(current_paths)
    previous_snapshots = repair_value(state.get("snapshots", {}))
    base_before = _read_json(current_paths.bookmarks_file)
    previous_ids = set(previous_snapshots.get("bookmarks", {}).get("ids", [])) or _bookmark_ids(base_before)
    previous_count = len(previous_ids)

    if reconcile_only:
        if base_before is None:
            raise FileNotFoundError(f"Missing source-of-truth file: {current_paths.bookmarks_file}")
        base_payload = _canonical_payload(base_before)
    else:
        base_payload = _load_import_payload(input_file=input_file, stdin_text=stdin_text)
        _write_json(current_paths.bookmarks_file, base_payload)

    enriched_input = _read_json(current_paths.enriched_file, strict=False)
    categorized_input = _read_json(current_paths.categorized_file, strict=False)

    base_map = _payload_map(base_payload)
    enriched_map = _payload_map(enriched_input)
    categorized_map = _payload_map(categorized_input)
    archive_map = {
        str(bookmark_id): _normalize_bookmark(bookmark)
        for bookmark_id, bookmark in state.get("archive", {}).items()
    }

    deletion_sources: dict[str, set[str]] = {}
    for key, payload in (
        ("bookmarks", base_payload),
        ("enriched", enriched_input),
        ("categorized", categorized_input),
    ):
        snapshot_ids = set(previous_snapshots.get(key, {}).get("ids", []))
        if not snapshot_ids or payload is None:
            continue
        deleted_ids = snapshot_ids - _bookmark_ids(payload)
        if deleted_ids:
            deletion_sources[key] = deleted_ids

    detected_deleted_ids = set().union(*deletion_sources.values()) if deletion_sources else set()
    for bookmark_id in detected_deleted_ids:
        merged = _merge_record(
            bookmark_id,
            base_map=base_map,
            enriched_map=enriched_map,
            categorized_map=categorized_map,
            archive_map=archive_map,
        )
        source_names = ",".join(sorted(name for name, ids in deletion_sources.items() if bookmark_id in ids))
        _record_tombstone(state, bookmark_id, source=f"file:{source_names}", bookmark=merged)

    source_ids = set(base_map)
    if previous_snapshots.get("bookmarks", {}).get("ids"):
        active_ids = set(base_map) | set(enriched_map) | set(categorized_map)
    else:
        active_ids = set(base_map) or (set(enriched_map) | set(categorized_map))
    active_ids |= restore_ids
    active_ids -= set(state.get("tombstones", {}))

    merged_bookmarks = [
        merged
        for bookmark_id in active_ids
        if (
            merged := _merge_record(
                bookmark_id,
                base_map=base_map,
                enriched_map=enriched_map,
                categorized_map=categorized_map,
                archive_map=archive_map,
            )
        )
    ]
    merged_bookmarks = _sort_bookmarks(merged_bookmarks)

    reference_payload = base_payload if base_payload["bookmarks"] else base_before or _empty_payload()
    persisted = _persist_bookmarks(
        reference_payload=reference_payload,
        bookmarks=merged_bookmarks,
        state=state,
        paths=current_paths,
        run_extract=run_extract,
        run_categorize=run_categorize,
        use_regex=use_regex,
    )

    tombstones = state.get("tombstones", {})
    current_ids = {bookmark["id"] for bookmark in persisted["base"]["bookmarks"]}
    added = len(current_ids - previous_ids)
    removed = len(previous_ids - current_ids)
    return {
        "source_of_truth": str(current_paths.bookmarks_file),
        "input_file": str(input_file) if input_file else None,
        "bookmarks": {
            "previous": previous_count,
            "source_current": len(source_ids),
            "current": len(current_ids),
            "added_estimate": added,
            "removed": removed,
            "tombstoned": len(tombstones),
        },
        "derived": {
            "enriched": {
                "current": len((persisted["enriched"] or {}).get("bookmarks", [])),
                "has_file": persisted["has_enriched"],
            },
            "categorized": {
                "current": len((persisted["categorized"] or {}).get("bookmarks", [])),
                "has_file": persisted["has_categorized"],
            },
        },
        "bidirectional": {
            "detected_deletes": len(detected_deleted_ids),
            "delete_sources": {key: len(value) for key, value in deletion_sources.items()},
            "restored": len(restore_ids),
        },
        "index": persisted["index"],
    }


def remove_bookmarks(
    bookmark_ids: list[str],
    *,
    paths: IndexPaths | None = None,
    source: str = "cli:remove",
) -> dict:
    current_paths = paths or default_paths()
    state = _load_sync_state(current_paths)
    payloads = {
        "bookmarks": _read_json(current_paths.bookmarks_file, strict=False),
        "enriched": _read_json(current_paths.enriched_file, strict=False),
        "categorized": _read_json(current_paths.categorized_file, strict=False),
    }
    base_map = _payload_map(payloads["bookmarks"])
    enriched_map = _payload_map(payloads["enriched"])
    categorized_map = _payload_map(payloads["categorized"])
    archive_map = {
        str(bookmark_id): _normalize_bookmark(bookmark)
        for bookmark_id, bookmark in state.get("archive", {}).items()
    }

    removed: list[str] = []
    missing: list[str] = []
    for bookmark_id in bookmark_ids:
        merged = _merge_record(
            str(bookmark_id),
            base_map=base_map,
            enriched_map=enriched_map,
            categorized_map=categorized_map,
            archive_map=archive_map,
        )
        if merged is None:
            missing.append(str(bookmark_id))
            continue
        _record_tombstone(state, str(bookmark_id), source=source, bookmark=merged)
        removed.append(str(bookmark_id))

    result = sync_bookmarks(
        reconcile_only=True,
        paths=current_paths,
        state_override=state,
    )
    result["mutation"] = {"removed": removed, "missing": missing}
    return result


def _metadata_result(*, paths: IndexPaths, state: dict, persisted: dict, mutation: dict) -> dict:
    return {
        "source_of_truth": str(paths.bookmarks_file),
        "bookmarks": {
            "current": len(persisted["base"]["bookmarks"]),
            "tombstoned": len(state.get("tombstones", {})),
        },
        "derived": {
            "enriched": {
                "current": len((persisted["enriched"] or {}).get("bookmarks", [])),
                "has_file": persisted["has_enriched"],
            },
            "categorized": {
                "current": len((persisted["categorized"] or {}).get("bookmarks", [])),
                "has_file": persisted["has_categorized"],
            },
        },
        "index": persisted["index"],
        "mutation": mutation,
    }


def export_local_metadata(output_file: Path, *, paths: IndexPaths | None = None) -> dict:
    current_paths = paths or default_paths()
    state = _load_sync_state(current_paths)
    _reference_payload, bookmarks = _load_active_bookmarks(current_paths, state)
    records = []
    for bookmark in bookmarks:
        local = _normalize_local_metadata(repair_value(bookmark.get("local")))
        if not local:
            continue
        records.append(
            {
                "id": bookmark["id"],
                "handle": bookmark.get("handle", ""),
                "local": local,
            }
        )
    records.sort(key=lambda item: item["id"])
    payload = {
        "version": 1,
        "exported_at": _now_iso(),
        "count": len(records),
        "bookmarks": records,
    }
    _write_json(output_file, payload)
    return {
        "output": str(output_file),
        "count": len(records),
    }


def import_local_metadata(input_file: Path, *, replace: bool = False, paths: IndexPaths | None = None) -> dict:
    current_paths = paths or default_paths()
    state = _load_sync_state(current_paths)
    reference_payload, bookmarks = _load_active_bookmarks(current_paths, state)
    with input_file.open(encoding="utf-8") as handle:
        payload = repair_value(json.load(handle))

    bookmark_map = {bookmark["id"]: bookmark for bookmark in bookmarks}
    archive_map = state.setdefault("archive", {})
    updated: list[str] = []
    missing: list[str] = []

    for record in payload.get("bookmarks", []):
        bookmark_id = str(record.get("id", "")).strip()
        if not bookmark_id:
            continue
        imported_local = _normalize_local_metadata(repair_value(record.get("local")))
        target = bookmark_map.get(bookmark_id)
        if target is None and bookmark_id in archive_map:
            target = archive_map[bookmark_id]
        if target is None:
            missing.append(bookmark_id)
            continue
        current_local = _normalize_local_metadata(repair_value(target.get("local")))
        merged_local = imported_local if replace else _normalize_local_metadata({**current_local, **imported_local})
        if merged_local:
            target["local"] = merged_local
        else:
            target.pop("local", None)
        updated.append(bookmark_id)

    bookmarks = _sort_bookmarks(list(bookmark_map.values()))
    persisted = _persist_bookmarks(reference_payload=reference_payload, bookmarks=bookmarks, state=state, paths=current_paths)
    return _metadata_result(
        paths=current_paths,
        state=state,
        persisted=persisted,
        mutation={
            "type": "metadata-import",
            "updated": updated,
            "updated_count": len(updated),
            "missing": missing,
            "replace": replace,
        },
    )


def _mutate_local_bookmarks(
    bookmark_ids: list[str],
    *,
    paths: IndexPaths | None = None,
    mutation_name: str,
    mutate: Callable[[dict, dict], bool],
) -> dict:
    current_paths = paths or default_paths()
    state = _load_sync_state(current_paths)
    reference_payload, bookmarks = _load_active_bookmarks(current_paths, state)
    bookmark_map = {bookmark["id"]: bookmark for bookmark in bookmarks}
    updated: list[str] = []
    missing: list[str] = []

    for bookmark_id in [str(value) for value in bookmark_ids]:
        bookmark = bookmark_map.get(bookmark_id)
        if not bookmark:
            missing.append(bookmark_id)
            continue
        local = _normalize_local_metadata(repair_value(bookmark.get("local")))
        changed = mutate(local, bookmark)
        normalized_local = _normalize_local_metadata(local)
        if normalized_local:
            bookmark["local"] = normalized_local
        else:
            bookmark.pop("local", None)
        if changed:
            updated.append(bookmark_id)

    bookmarks = _sort_bookmarks(list(bookmark_map.values()))
    persisted = _persist_bookmarks(reference_payload=reference_payload, bookmarks=bookmarks, state=state, paths=current_paths)
    return _metadata_result(
        paths=current_paths,
        state=state,
        persisted=persisted,
        mutation={"type": mutation_name, "updated": updated, "missing": missing},
    )


def set_note(bookmark_id: str, note: str, *, clear: bool = False, paths: IndexPaths | None = None) -> dict:
    clean_note = str(note).strip()

    def mutate(local: dict, _bookmark: dict) -> bool:
        before = local.get("note")
        if clear or not clean_note:
            local.pop("note", None)
        else:
            local["note"] = clean_note
        local["updated_at"] = _now_iso()
        return before != local.get("note")

    return _mutate_local_bookmarks([bookmark_id], paths=paths, mutation_name="note", mutate=mutate)


def add_tags(bookmark_id: str, tags: list[str], *, paths: IndexPaths | None = None) -> dict:
    normalized_tags = _normalize_local_metadata({"tags": tags}).get("tags", [])

    def mutate(local: dict, _bookmark: dict) -> bool:
        before = list(local.get("tags", []))
        merged = before + [tag for tag in normalized_tags if tag not in before]
        if merged:
            local["tags"] = merged
        else:
            local.pop("tags", None)
        local["updated_at"] = _now_iso()
        return before != local.get("tags", [])

    return _mutate_local_bookmarks([bookmark_id], paths=paths, mutation_name="tag", mutate=mutate)


def remove_tags(bookmark_id: str, tags: list[str], *, paths: IndexPaths | None = None) -> dict:
    normalized_tags = set(_normalize_local_metadata({"tags": tags}).get("tags", []))

    def mutate(local: dict, _bookmark: dict) -> bool:
        before = list(local.get("tags", []))
        remaining = [tag for tag in before if tag not in normalized_tags]
        if remaining:
            local["tags"] = remaining
        else:
            local.pop("tags", None)
        local["updated_at"] = _now_iso()
        return before != remaining

    return _mutate_local_bookmarks([bookmark_id], paths=paths, mutation_name="untag", mutate=mutate)


def set_rating(bookmark_id: str, rating: int | None, *, clear: bool = False, paths: IndexPaths | None = None) -> dict:
    if rating is not None and not 1 <= rating <= 5:
        raise ValueError("rating must be between 1 and 5")

    def mutate(local: dict, _bookmark: dict) -> bool:
        before = local.get("rating")
        if clear or rating is None:
            local.pop("rating", None)
        else:
            local["rating"] = rating
        local["updated_at"] = _now_iso()
        return before != local.get("rating")

    return _mutate_local_bookmarks([bookmark_id], paths=paths, mutation_name="rate", mutate=mutate)


def hide_bookmarks(bookmark_ids: list[str], *, paths: IndexPaths | None = None) -> dict:
    def mutate(local: dict, _bookmark: dict) -> bool:
        before = bool(local.get("hidden"))
        local["hidden"] = True
        local["hidden_at"] = _now_iso()
        local["updated_at"] = _now_iso()
        return not before

    return _mutate_local_bookmarks(bookmark_ids, paths=paths, mutation_name="hide", mutate=mutate)


def unhide_bookmarks(bookmark_ids: list[str], *, paths: IndexPaths | None = None) -> dict:
    def mutate(local: dict, _bookmark: dict) -> bool:
        before = bool(local.get("hidden"))
        local.pop("hidden", None)
        local.pop("hidden_at", None)
        local["updated_at"] = _now_iso()
        return before

    return _mutate_local_bookmarks(bookmark_ids, paths=paths, mutation_name="unhide", mutate=mutate)


def restore_bookmarks(
    bookmark_ids: list[str],
    *,
    paths: IndexPaths | None = None,
) -> dict:
    current_paths = paths or default_paths()
    state = _load_sync_state(current_paths)
    archive = state.get("archive", {})
    target_ids = list(bookmark_ids)
    if not target_ids:
        target_ids = sorted(state.get("tombstones", {}))
    restored = [str(bookmark_id) for bookmark_id in target_ids if str(bookmark_id) in archive]
    missing = [str(bookmark_id) for bookmark_id in target_ids if str(bookmark_id) not in archive]

    for bookmark_id in restored:
        state.get("tombstones", {}).pop(bookmark_id, None)

    result = sync_bookmarks(
        reconcile_only=True,
        restore_ids=set(restored),
        paths=current_paths,
        state_override=state,
    )
    result["mutation"] = {"restored": restored, "missing": missing}
    return result


def read_stdin_json() -> str:
    payload = sys.stdin.read()
    if not payload.strip():
        raise ValueError("stdin was empty")
    return payload
