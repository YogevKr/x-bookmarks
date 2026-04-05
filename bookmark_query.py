"""Persistent bookmark index, hybrid retrieval, and shell formatting."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from bookmark_paths import read_only_mode, resolve_base_dir
from text_repair import repair_value

INDEX_VERSION = 4
VECTOR_DIM = 256
RRF_K = 60
SKIP_DOMAINS = {"x.com", "twitter.com", "t.co"}
TEXT_TRANSLATION = str.maketrans({"\n": " ", "\r": " ", "\t": " "})
SPARKS = "▁▂▃▄▅▆▇█"
BLOCK = "█"


@dataclass(frozen=True)
class IndexPaths:
    base_dir: Path

    @property
    def bookmarks_file(self) -> Path:
        return self.base_dir / "bookmarks.json"

    @property
    def enriched_file(self) -> Path:
        return self.base_dir / "enriched.json"

    @property
    def categorized_file(self) -> Path:
        return self.base_dir / "categorized.json"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / ".x-bookmarks"

    @property
    def index_db(self) -> Path:
        return self.data_dir / "bookmarks.db"

    @property
    def manifest_file(self) -> Path:
        return self.data_dir / "manifest.json"

    @property
    def sync_state_file(self) -> Path:
        return self.data_dir / "sync-state.json"


def default_paths() -> IndexPaths:
    return IndexPaths(base_dir=resolve_base_dir())


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


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


def _load_sync_state(paths: IndexPaths) -> dict:
    return _read_json(paths.sync_state_file, strict=False) or {}


def _sync_state_summary(paths: IndexPaths) -> dict:
    state = _load_sync_state(paths)
    tombstones = state.get("tombstones", {})
    archive = state.get("archive", {})
    return {
        "exists": paths.sync_state_file.exists(),
        "path": str(paths.sync_state_file),
        "updated_at": state.get("updated_at"),
        "tombstone_count": len(tombstones),
        "archive_count": len(archive),
        "deleted_ids": sorted(tombstones),
    }


def _doctor_check(name: str, status: str, detail: str, *, data: dict | None = None) -> dict:
    payload = {"name": name, "status": status, "detail": detail}
    if data is not None:
        payload["data"] = data
    return payload


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file_snapshot(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "path": str(path)}

    data = _read_json(path, strict=False)
    bookmark_count = len(data.get("bookmarks", [])) if data else None
    return {
        "exists": True,
        "path": str(path),
        "sha256": _sha256(path),
        "mtime_ns": path.stat().st_mtime_ns,
        "bookmark_count": bookmark_count,
        "valid_json": data is not None,
    }


def _snapshot_signature(snapshot: dict | None) -> dict:
    data = snapshot or {}
    return {
        "exists": data.get("exists"),
        "sha256": data.get("sha256"),
        "bookmark_count": data.get("bookmark_count"),
        "valid_json": data.get("valid_json"),
    }


def _source_state_signature(source_state: dict | None) -> dict:
    files = (source_state or {}).get("files", {})
    return {
        name: _snapshot_signature(files.get(name))
        for name in ("bookmarks", "enriched", "categorized")
    }


def parse_timestamp(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def parse_cli_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def bookmark_date(bookmark: dict) -> datetime | None:
    return parse_timestamp(bookmark.get("timestamp"))


def bookmark_date_string(bookmark: dict) -> str:
    dt = bookmark_date(bookmark)
    return dt.strftime("%Y-%m-%d") if dt else "?"


def normalize_handle(handle: str | None) -> str:
    return str(handle or "").strip().lstrip("@").casefold()


def iter_external_domains(bookmark: dict) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for url in bookmark.get("urls", []):
        domain = urlparse(url).netloc.casefold().removeprefix("www.")
        if not domain or domain in SKIP_DOMAINS or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


def external_urls(bookmark: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for url in bookmark.get("urls", []):
        domain = urlparse(url).netloc.casefold().removeprefix("www.")
        if not domain or domain in SKIP_DOMAINS or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def bookmark_categories(bookmark: dict) -> list[str]:
    return list(bookmark.get("ai", {}).get("categories", []))


def bookmark_entities(bookmark: dict) -> list[str]:
    return list(bookmark.get("ai", {}).get("entities", []))


def bookmark_language(bookmark: dict) -> str:
    value = str(bookmark.get("ai", {}).get("language", "")).strip()
    if value:
        return value
    text = str(bookmark.get("text", ""))
    return "he" if any("\u0590" <= char <= "\u05FF" for char in text) else "en"


def bookmark_type(bookmark: dict) -> str:
    return str(bookmark.get("ai", {}).get("type", "")).strip() or "unknown"


def bookmark_summary(bookmark: dict, limit: int = 110) -> str:
    summary = str(bookmark.get("ai", {}).get("summary", "")).strip()
    if summary:
        return _truncate(summary, limit)
    title = bookmark_link_title(bookmark)
    if title:
        return _truncate(title, limit)
    return _truncate(str(bookmark.get("text", "")).strip(), limit)


def bookmark_link_pages(bookmark: dict) -> list[dict]:
    pages = [page for page in bookmark.get("linked_pages", []) if isinstance(page, dict)]
    if pages:
        return pages
    extracted = bookmark.get("extracted")
    return [extracted] if isinstance(extracted, dict) and extracted else []


def bookmark_text_preview(bookmark: dict, limit: int = 180) -> str:
    return _truncate(str(bookmark.get("text", "")).strip(), limit)


def bookmark_link_title(bookmark: dict) -> str:
    page = next(iter(bookmark_link_pages(bookmark)), {})
    return str(page.get("title", "")).strip()


def bookmark_link_description(bookmark: dict, limit: int = 160) -> str:
    page = next(iter(bookmark_link_pages(bookmark)), {})
    return _truncate(str(page.get("description", "")).strip(), limit)


def bookmark_link_preview(bookmark: dict, limit: int = 260) -> str:
    page = next(iter(bookmark_link_pages(bookmark)), {})
    for field in ("preview", "content", "description", "title"):
        value = str(page.get(field, "")).strip()
        if value:
            return _truncate(value, limit)
    return ""


def bookmark_link_url(bookmark: dict) -> str:
    page = next(iter(bookmark_link_pages(bookmark)), {})
    return str(page.get("url", "")).strip()


def bookmark_link_site(bookmark: dict) -> str:
    page = next(iter(bookmark_link_pages(bookmark)), {})
    return str(page.get("site_name", "")).strip()


def bookmark_local(bookmark: dict) -> dict:
    local = bookmark.get("local", {})
    return local if isinstance(local, dict) else {}


def bookmark_local_note(bookmark: dict) -> str:
    return str(bookmark_local(bookmark).get("note", "")).strip()


def bookmark_local_tags(bookmark: dict) -> list[str]:
    tags = bookmark_local(bookmark).get("tags", [])
    return [str(tag).strip() for tag in tags if str(tag).strip()]


def bookmark_local_rating(bookmark: dict) -> int | None:
    rating = bookmark_local(bookmark).get("rating")
    return rating if isinstance(rating, int) else None


def bookmark_hidden(bookmark: dict) -> bool:
    return bool(bookmark_local(bookmark).get("hidden"))


def _parsed_from_bookmark(bookmark: dict, *, deleted: bool = False, deleted_info: dict | None = None) -> dict:
    dt = bookmark_date(bookmark)
    categories = bookmark_categories(bookmark)
    entities = bookmark_entities(bookmark)
    domains = iter_external_domains(bookmark)
    return {
        "id": str(bookmark["id"]),
        "bookmark": repair_value(bookmark),
        "handle": str(bookmark.get("handle", "")),
        "author": str(bookmark.get("author", "")),
        "date": dt.strftime("%Y-%m-%d") if dt else "?",
        "text": str(bookmark.get("text", "")),
        "summary": bookmark_summary(bookmark),
        "url": tweet_url(bookmark),
        "categories": categories,
        "entities": entities,
        "domains": domains,
        "language": bookmark_language(bookmark),
        "type": bookmark_type(bookmark),
        "importance": bookmark_importance(bookmark),
        "search_text": _combined_search_text(bookmark),
        "vector": _bookmark_vector(bookmark),
        "deleted": deleted,
        "deleted_info": deleted_info or {},
    }


def bookmark_importance(bookmark: dict) -> int | None:
    value = bookmark.get("ai", {}).get("importance")
    return value if isinstance(value, int) else None


def tweet_url(bookmark: dict) -> str:
    handle = str(bookmark.get("handle", "")).lstrip("@")
    return f"https://x.com/{handle}/status/{bookmark['id']}"


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(text.translate(TEXT_TRANSLATION).split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _tokenize(text: str) -> list[str]:
    return [token for token in "".join(ch.casefold() if ch.isalnum() else " " for ch in text).split() if token]


def _char_ngrams(text: str, size: int = 3) -> list[str]:
    compact = " ".join(text.casefold().translate(TEXT_TRANSLATION).split())
    if len(compact) < size:
        return [compact] if compact else []
    return [compact[idx : idx + size] for idx in range(len(compact) - size + 1)]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def load_bookmark_data(preferred_file: Path | None = None, *, paths: IndexPaths | None = None) -> tuple[Path, dict, list[dict]]:
    current_paths = paths or default_paths()
    candidates = [preferred_file] if preferred_file else [
        current_paths.categorized_file,
        current_paths.enriched_file,
        current_paths.bookmarks_file,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            data = _read_json(candidate, strict=False)
            if data is None:
                continue
            return candidate, data, data.get("bookmarks", [])
    raise FileNotFoundError("No bookmarks input file found")


def _merge_bookmark_corpus(paths: IndexPaths | None = None) -> tuple[dict, list[dict]]:
    current_paths = paths or default_paths()
    sources = {
        "bookmarks": _read_json(current_paths.bookmarks_file),
        "enriched": _read_json(current_paths.enriched_file, strict=False),
        "categorized": _read_json(current_paths.categorized_file, strict=False),
    }

    base_name = next((name for name in ("bookmarks", "enriched", "categorized") if sources[name]), None)
    if not base_name:
        raise FileNotFoundError("No bookmarks source file found")

    base_rows = {bookmark["id"]: dict(bookmark) for bookmark in sources[base_name].get("bookmarks", [])}

    enriched = sources["enriched"]
    if enriched:
        for bookmark in enriched.get("bookmarks", []):
            existing = base_rows.get(bookmark["id"])
            if not existing:
                continue
            if bookmark.get("linked_pages"):
                existing["linked_pages"] = bookmark["linked_pages"]
            if bookmark.get("extracted"):
                existing["extracted"] = bookmark["extracted"]
            if bookmark.get("local"):
                existing["local"] = bookmark["local"]

    categorized = sources["categorized"]
    if categorized:
        for bookmark in categorized.get("bookmarks", []):
            existing = base_rows.get(bookmark["id"])
            if not existing:
                continue
            if bookmark.get("linked_pages"):
                existing["linked_pages"] = bookmark["linked_pages"]
            if bookmark.get("extracted"):
                existing["extracted"] = bookmark["extracted"]
            if bookmark.get("ai"):
                existing["ai"] = bookmark["ai"]
            if bookmark.get("local"):
                existing["local"] = bookmark["local"]

    bookmarks = list(base_rows.values())
    bookmarks.sort(key=lambda bookmark: bookmark_date(bookmark) or datetime.min.replace(tzinfo=UTC), reverse=True)

    source_state = {
        "base": base_name,
        "files": {
            "bookmarks": _file_snapshot(current_paths.bookmarks_file),
            "enriched": _file_snapshot(current_paths.enriched_file),
            "categorized": _file_snapshot(current_paths.categorized_file),
        },
        "merged_count": len(bookmarks),
    }
    return source_state, bookmarks


def _ensure_data_dir(paths: IndexPaths) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)


def _connect(paths: IndexPaths, *, write: bool = False) -> sqlite3.Connection:
    if write:
        _ensure_data_dir(paths)
        conn = sqlite3.connect(paths.index_db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    else:
        if not paths.index_db.exists():
            raise FileNotFoundError(f"Index DB not found: {paths.index_db}")
        uri = f"file:{paths.index_db}?mode=ro" if read_only_mode() else str(paths.index_db)
        conn = sqlite3.connect(uri, uri=read_only_mode())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks (
            id TEXT PRIMARY KEY,
            handle TEXT,
            author TEXT,
            timestamp TEXT,
            date TEXT,
            year INTEGER,
            text TEXT,
            summary TEXT,
            language TEXT,
            type TEXT,
            importance INTEGER,
            media_count INTEGER,
            categories_json TEXT,
            categories_flat TEXT,
            entities_json TEXT,
            entities_flat TEXT,
            urls_json TEXT,
            domains_json TEXT,
            domains_flat TEXT,
            hashtags_json TEXT,
            local_json TEXT,
            local_note TEXT,
            local_tags_json TEXT,
            local_tags_flat TEXT,
            hidden INTEGER,
            rating INTEGER,
            linked_pages_json TEXT,
            extracted_title TEXT,
            extracted_description TEXT,
            extracted_content TEXT,
            search_text TEXT,
            vector BLOB
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_date ON bookmarks(date DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_handle ON bookmarks(handle)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_type ON bookmarks(type)")
    try:
        conn.execute("ALTER TABLE bookmarks ADD COLUMN media_count INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE bookmarks ADD COLUMN linked_pages_json TEXT")
    except sqlite3.OperationalError:
        pass
    for column, ddl in [
        ("local_json", "TEXT"),
        ("local_note", "TEXT"),
        ("local_tags_json", "TEXT"),
        ("local_tags_flat", "TEXT"),
        ("hidden", "INTEGER"),
        ("rating", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE bookmarks ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(id UNINDEXED, text, summary, categories, entities, extracted_title, extracted_description, extracted_content, author, handle, domains, hashtags, local_note, local_tags, tokenize='unicode61')")


def _combined_search_text(bookmark: dict) -> str:
    link_pages = bookmark_link_pages(bookmark)
    ai = bookmark.get("ai", {})
    local_tags = bookmark_local_tags(bookmark)
    extracted_text = "\n".join(
        piece
        for page in link_pages
        for piece in (
            str(page.get("title", "")),
            str(page.get("description", "")),
            str(page.get("preview", "")),
            str(page.get("content", ""))[:1600],
            str(page.get("site_name", "")),
            str(page.get("url", "")),
        )
        if piece
    )
    pieces = [
        str(bookmark.get("text", "")),
        str(ai.get("summary", "")),
        " ".join(bookmark_categories(bookmark)),
        " ".join(bookmark_entities(bookmark)),
        extracted_text[:6000],
        str(bookmark.get("author", "")),
        str(bookmark.get("handle", "")),
        " ".join(iter_external_domains(bookmark)),
        " ".join(bookmark.get("hashtags", [])),
        bookmark_local_note(bookmark),
        " ".join(local_tags),
    ]
    return "\n".join(piece for piece in pieces if piece)


def _hash_feature(feature: str) -> tuple[int, int]:
    digest = hashlib.sha1(feature.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % VECTOR_DIM
    sign = 1 if digest[4] & 1 else -1
    return idx, sign


def _vector_add(target: list[float], text: str, weight: float, *, chargrams: bool = False) -> None:
    features = _tokenize(text)
    if chargrams:
        features.extend(f"cg:{value}" for value in _char_ngrams(text))
    for feature in features:
        idx, sign = _hash_feature(feature)
        target[idx] += sign * weight


def _normalize_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def _bookmark_vector(bookmark: dict) -> list[float]:
    link_pages = bookmark_link_pages(bookmark)
    ai = bookmark.get("ai", {})
    local_note = bookmark_local_note(bookmark)
    local_tags = " ".join(bookmark_local_tags(bookmark))
    link_titles = " ".join(str(page.get("title", "")) for page in link_pages if page.get("title"))
    link_descriptions = " ".join(str(page.get("description", "")) for page in link_pages if page.get("description"))
    link_content = "\n".join(str(page.get("content", ""))[:1000] for page in link_pages if page.get("content"))
    link_sites = " ".join(str(page.get("site_name", "")) for page in link_pages if page.get("site_name"))
    vector = [0.0] * VECTOR_DIM
    parts = [
        (str(bookmark.get("text", "")), 3.0, True),
        (str(ai.get("summary", "")), 2.0, True),
        (" ".join(bookmark_categories(bookmark)), 2.0, False),
        (" ".join(bookmark_entities(bookmark)), 2.0, False),
        (link_titles, 2.0, True),
        (link_descriptions, 1.0, True),
        (link_content[:2000], 1.0, True),
        (link_sites, 0.5, False),
        (" ".join(iter_external_domains(bookmark)), 1.5, False),
        (str(bookmark.get("author", "")), 1.0, False),
        (str(bookmark.get("handle", "")), 1.0, False),
        (" ".join(bookmark.get("hashtags", [])), 1.0, False),
        (local_note, 2.0, True),
        (local_tags, 1.5, False),
    ]
    for text, weight, chargrams in parts:
        if text:
            _vector_add(vector, text, weight, chargrams=chargrams)
    return _normalize_vector(vector)


def _query_vector(query: str) -> list[float]:
    vector = [0.0] * VECTOR_DIM
    _vector_add(vector, query, 3.0, chargrams=True)
    return _normalize_vector(vector)


def _pack_vector(values: list[float]) -> bytes:
    return struct.pack(f"{VECTOR_DIM}f", *values)


def _unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{VECTOR_DIM}f", blob))


def _dot(left: list[float], right: list[float]) -> float:
    return sum(left[idx] * right[idx] for idx in range(VECTOR_DIM))


def _row_from_bookmark(bookmark: dict) -> dict:
    dt = bookmark_date(bookmark)
    link_pages = bookmark_link_pages(bookmark)
    primary_link = link_pages[0] if link_pages else bookmark.get("extracted", {})
    categories = bookmark_categories(bookmark)
    entities = bookmark_entities(bookmark)
    domains = iter_external_domains(bookmark)
    hashtags = list(bookmark.get("hashtags", []))
    local = bookmark_local(bookmark)
    local_tags = bookmark_local_tags(bookmark)
    vector = _pack_vector(_bookmark_vector(bookmark))
    aggregated_titles = "\n".join(str(page.get("title", "")) for page in link_pages if page.get("title"))
    aggregated_descriptions = "\n".join(str(page.get("description", "")) for page in link_pages if page.get("description"))
    aggregated_content = "\n\n".join(str(page.get("content", ""))[:1200] for page in link_pages if page.get("content"))[:4000]
    return {
        "id": bookmark["id"],
        "handle": str(bookmark.get("handle", "")),
        "author": str(bookmark.get("author", "")),
        "timestamp": str(bookmark.get("timestamp", "")),
        "date": dt.strftime("%Y-%m-%d") if dt else None,
        "year": int(dt.strftime("%Y")) if dt else None,
        "text": str(bookmark.get("text", "")),
        "summary": str(bookmark.get("ai", {}).get("summary", "")),
        "language": bookmark_language(bookmark),
        "type": bookmark_type(bookmark),
        "importance": bookmark_importance(bookmark),
        "media_count": len(bookmark.get("media", [])),
        "categories_json": json.dumps(categories, ensure_ascii=False),
        "categories_flat": " | ".join(category.casefold() for category in categories),
        "entities_json": json.dumps(entities, ensure_ascii=False),
        "entities_flat": " | ".join(entity.casefold() for entity in entities),
        "urls_json": json.dumps(bookmark.get("urls", []), ensure_ascii=False),
        "domains_json": json.dumps(domains, ensure_ascii=False),
        "domains_flat": " | ".join(domains),
        "hashtags_json": json.dumps(hashtags, ensure_ascii=False),
        "local_json": json.dumps(local, ensure_ascii=False) if local else None,
        "local_note": bookmark_local_note(bookmark),
        "local_tags_json": json.dumps(local_tags, ensure_ascii=False),
        "local_tags_flat": " | ".join(local_tags),
        "hidden": 1 if bookmark_hidden(bookmark) else 0,
        "rating": bookmark_local_rating(bookmark),
        "linked_pages_json": json.dumps(link_pages, ensure_ascii=False),
        "hashtags_text": " ".join(hashtags),
        "extracted_title": aggregated_titles or str(primary_link.get("title", "")),
        "extracted_description": aggregated_descriptions or str(primary_link.get("description", "")),
        "extracted_content": aggregated_content or str(primary_link.get("content", ""))[:4000],
        "search_text": _combined_search_text(bookmark),
        "vector": vector,
    }


def _save_manifest(paths: IndexPaths, payload: dict) -> None:
    _ensure_data_dir(paths)
    with paths.manifest_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _load_manifest(paths: IndexPaths) -> dict | None:
    if not paths.manifest_file.exists():
        return None
    with paths.manifest_file.open(encoding="utf-8") as handle:
        return json.load(handle)


def refresh_index(*, paths: IndexPaths | None = None, force: bool = False) -> dict:
    current_paths = paths or default_paths()
    source_state, bookmarks = _merge_bookmark_corpus(current_paths)
    conn = _connect(current_paths, write=True)
    try:
        manifest = _load_manifest(current_paths)
        rebuild_schema = force or bool(manifest and manifest.get("version") != INDEX_VERSION)
        if rebuild_schema:
            with conn:
                conn.execute("DROP TABLE IF EXISTS bookmarks_fts")
                conn.execute("DROP TABLE IF EXISTS bookmarks")
        _ensure_schema(conn)
        with conn:
            conn.execute("DELETE FROM bookmarks")
            conn.execute("DELETE FROM bookmarks_fts")

            for bookmark in bookmarks:
                row = _row_from_bookmark(bookmark)
                conn.execute(
                    """
                    INSERT INTO bookmarks (
                        id, handle, author, timestamp, date, year, text, summary, language, type, importance,
                        media_count,
                        categories_json, categories_flat, entities_json, entities_flat, urls_json, domains_json,
                        domains_flat, hashtags_json, local_json, local_note, local_tags_json, local_tags_flat, hidden, rating,
                        linked_pages_json, extracted_title, extracted_description, extracted_content,
                        search_text, vector
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["handle"],
                        row["author"],
                        row["timestamp"],
                        row["date"],
                        row["year"],
                        row["text"],
                        row["summary"],
                        row["language"],
                        row["type"],
                        row["importance"],
                        row["media_count"],
                        row["categories_json"],
                        row["categories_flat"],
                        row["entities_json"],
                        row["entities_flat"],
                        row["urls_json"],
                        row["domains_json"],
                        row["domains_flat"],
                        row["hashtags_json"],
                        row["local_json"],
                        row["local_note"],
                        row["local_tags_json"],
                        row["local_tags_flat"],
                        row["hidden"],
                        row["rating"],
                        row["linked_pages_json"],
                        row["extracted_title"],
                        row["extracted_description"],
                        row["extracted_content"],
                        row["search_text"],
                        row["vector"],
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO bookmarks_fts (
                        id, text, summary, categories, entities, extracted_title, extracted_description,
                        extracted_content, author, handle, domains, hashtags, local_note, local_tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["text"],
                        row["summary"],
                        row["categories_flat"],
                        row["entities_flat"],
                        row["extracted_title"],
                        row["extracted_description"],
                        row["extracted_content"],
                        row["author"],
                        row["handle"],
                        row["domains_flat"],
                        row["hashtags_text"],
                        row["local_note"],
                        row["local_tags_flat"],
                    ),
                )

        manifest = {
            "version": INDEX_VERSION,
            "built_at": _now_iso(),
            "db_path": str(current_paths.index_db),
            "doc_count": len(bookmarks),
            "source_state": source_state,
        }
        _save_manifest(current_paths, manifest)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {
            "fresh": True,
            "stale": False,
            "rebuilt": True,
            "reason": "force" if force else "source_changed",
            "doc_count": len(bookmarks),
            "index_db": str(current_paths.index_db),
            "manifest_path": str(current_paths.manifest_file),
            "source_state": source_state,
            "built_at": manifest["built_at"],
        }
    finally:
        conn.close()


def get_index_status(*, paths: IndexPaths | None = None) -> dict:
    current_paths = paths or default_paths()
    source_state, bookmarks = _merge_bookmark_corpus(current_paths)
    manifest = _load_manifest(current_paths)
    sync_state = _sync_state_summary(current_paths)
    db_exists = current_paths.index_db.exists()
    reasons: list[str] = []

    if not db_exists:
        reasons.append("index_db_missing")
    if not manifest:
        reasons.append("manifest_missing")
    else:
        if manifest.get("version") != INDEX_VERSION:
            reasons.append("version_mismatch")
        if manifest.get("doc_count") != len(bookmarks):
            reasons.append("doc_count_mismatch")
        if _source_state_signature(manifest.get("source_state")) != _source_state_signature(source_state):
            reasons.append("source_files_changed")
        if manifest.get("source_state", {}).get("base") != source_state.get("base"):
            reasons.append("source_base_changed")

    indexed_count = None
    if db_exists:
        conn = _connect(current_paths)
        try:
            try:
                indexed_count = int(conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0])
            except sqlite3.DatabaseError:
                reasons.append("db_unreadable")
        finally:
            conn.close()
        if indexed_count is not None and indexed_count != len(bookmarks):
            reasons.append("indexed_count_mismatch")

    stale = len(reasons) > 0
    return {
        "fresh": not stale,
        "stale": stale,
        "reasons": reasons,
        "read_only": read_only_mode(),
        "index_db": str(current_paths.index_db),
        "manifest_path": str(current_paths.manifest_file),
        "doc_count": len(bookmarks),
        "indexed_count": indexed_count,
        "source_state": source_state,
        "sync_state": sync_state,
        "built_at": manifest.get("built_at") if manifest else None,
    }


def ensure_index(*, paths: IndexPaths | None = None, auto_refresh: bool = True, force: bool = False) -> dict:
    current_paths = paths or default_paths()
    status = get_index_status(paths=current_paths)
    if (status["stale"] and auto_refresh) or force:
        return refresh_index(paths=current_paths, force=force)
    return status


def doctor_report(*, paths: IndexPaths | None = None) -> dict:
    current_paths = paths or default_paths()
    status = get_index_status(paths=current_paths)
    sync_state_raw = _read_json(current_paths.sync_state_file, strict=False)
    sync_state = sync_state_raw or {}
    source_state, merged_bookmarks = _merge_bookmark_corpus(current_paths)
    merged_ids = {bookmark["id"] for bookmark in merged_bookmarks}
    checks: list[dict] = []

    bookmarks_snapshot = status["source_state"]["files"]["bookmarks"]
    if not bookmarks_snapshot["exists"]:
        checks.append(_doctor_check("bookmarks_file", "error", "bookmarks.json missing", data=bookmarks_snapshot))
    elif not bookmarks_snapshot["valid_json"]:
        checks.append(_doctor_check("bookmarks_file", "error", "bookmarks.json is invalid JSON", data=bookmarks_snapshot))
    else:
        checks.append(_doctor_check("bookmarks_file", "ok", "bookmarks.json readable", data=bookmarks_snapshot))

    for name in ("enriched", "categorized"):
        snapshot = status["source_state"]["files"][name]
        if not snapshot["exists"]:
            checks.append(_doctor_check(f"{name}_file", "warn", f"{name}.json missing", data=snapshot))
        elif not snapshot["valid_json"]:
            checks.append(_doctor_check(f"{name}_file", "error", f"{name}.json is invalid JSON", data=snapshot))
        else:
            checks.append(_doctor_check(f"{name}_file", "ok", f"{name}.json readable", data=snapshot))

    severe_index_reasons = {"db_unreadable"}
    if status["fresh"]:
        checks.append(_doctor_check("index", "ok", "index is fresh", data={"built_at": status["built_at"]}))
    else:
        index_status = "error" if any(reason in severe_index_reasons for reason in status["reasons"]) else "warn"
        checks.append(
            _doctor_check(
                "index",
                index_status,
                f"index is stale: {', '.join(status['reasons'])}",
                data={"reasons": status["reasons"]},
            )
        )

    if current_paths.sync_state_file.exists():
        if sync_state_raw is None:
            checks.append(_doctor_check("sync_state", "error", "sync-state.json is invalid JSON"))
        else:
            checks.append(
                _doctor_check(
                    "sync_state",
                    "ok",
                    "sync-state.json readable",
                    data=_sync_state_summary(current_paths),
                )
            )
    else:
        checks.append(_doctor_check("sync_state", "warn", "sync-state.json missing; run sync to bootstrap local state"))

    tombstones = sync_state.get("tombstones", {}) if isinstance(sync_state.get("tombstones", {}), dict) else {}
    archive = sync_state.get("archive", {}) if isinstance(sync_state.get("archive", {}), dict) else {}
    tombstones_missing_archive = sorted(bookmark_id for bookmark_id in tombstones if bookmark_id not in archive)
    if tombstones_missing_archive:
        checks.append(
            _doctor_check(
                "tombstones_missing_archive",
                "error",
                f"{len(tombstones_missing_archive)} tombstones missing archive records",
                data={"ids": tombstones_missing_archive},
            )
        )
    else:
        checks.append(_doctor_check("tombstones_missing_archive", "ok", "all tombstones have archive records"))

    tombstones_in_active = sorted(bookmark_id for bookmark_id in tombstones if bookmark_id in merged_ids)
    if tombstones_in_active:
        checks.append(
            _doctor_check(
                "tombstones_in_active_corpus",
                "error",
                f"{len(tombstones_in_active)} tombstoned bookmarks still present in active corpus",
                data={"ids": tombstones_in_active},
            )
        )
    else:
        checks.append(_doctor_check("tombstones_in_active_corpus", "ok", "no tombstoned bookmarks in active corpus"))

    snapshots = sync_state.get("snapshots", {}) if isinstance(sync_state.get("snapshots", {}), dict) else {}
    drift: dict[str, int] = {}
    for name in ("bookmarks", "enriched", "categorized"):
        snapshot_ids = set(snapshots.get(name, {}).get("ids", []))
        actual_ids = {
            bookmark["id"]
            for bookmark in (_read_json(getattr(current_paths, f"{name}_file"), strict=False) or {}).get("bookmarks", [])
        }
        if snapshot_ids and snapshot_ids != actual_ids:
            drift[name] = len(snapshot_ids.symmetric_difference(actual_ids))
    if drift:
        checks.append(_doctor_check("snapshot_drift", "warn", "local files changed since last sync snapshot", data=drift))
    else:
        checks.append(_doctor_check("snapshot_drift", "ok", "snapshot ids match current files"))

    errors = [check for check in checks if check["status"] == "error"]
    warnings = [check for check in checks if check["status"] == "warn"]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "checks": checks,
        "summary": {
            "active_bookmarks": len(merged_ids),
            "index_fresh": status["fresh"],
            "tombstones": len(tombstones),
            "archive": len(archive),
            "built_at": status["built_at"],
            "sync_updated_at": sync_state.get("updated_at"),
        },
        "status": status,
        "source_state": source_state,
    }


def _parse_row(row: sqlite3.Row) -> dict:
    categories = json.loads(row["categories_json"] or "[]")
    entities = json.loads(row["entities_json"] or "[]")
    urls = json.loads(row["urls_json"] or "[]")
    domains = json.loads(row["domains_json"] or "[]")
    hashtags = json.loads(row["hashtags_json"] or "[]")
    local = json.loads(row["local_json"] or "{}")
    linked_pages = json.loads(row["linked_pages_json"] or "[]")
    primary_link = linked_pages[0] if linked_pages else {
        "title": row["extracted_title"],
        "description": row["extracted_description"],
        "content": row["extracted_content"],
    }
    bookmark = {
        "id": row["id"],
        "handle": row["handle"],
        "author": row["author"],
        "timestamp": row["timestamp"],
        "text": row["text"],
        "urls": urls,
        "hashtags": hashtags,
        "local": local,
        "linked_pages": linked_pages,
        "extracted": primary_link,
        "ai": {
            "summary": row["summary"],
            "categories": categories,
            "entities": entities,
            "language": row["language"],
            "type": row["type"],
            "importance": row["importance"],
        },
    }
    return {
        "id": row["id"],
        "bookmark": bookmark,
        "handle": row["handle"],
        "author": row["author"],
        "date": row["date"] or "?",
        "text": row["text"],
        "summary": row["summary"] or bookmark_summary(bookmark),
        "url": tweet_url(bookmark),
        "categories": categories,
        "entities": entities,
        "domains": domains,
        "language": row["language"],
        "type": row["type"],
        "importance": row["importance"],
        "search_text": row["search_text"],
        "vector": _unpack_vector(row["vector"]) if row["vector"] else [0.0] * VECTOR_DIM,
    }


def _fts_query(query: str) -> str:
    raw = query.strip()
    if any(symbol in raw for symbol in ['"', " OR ", " AND ", " NOT ", "(", ")"]):
        return raw
    terms = _tokenize(raw)
    if not terms:
        return raw
    return " OR ".join(f'"{term}"*' for term in terms)


def _filters_sql(filters: dict) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    values: list[object] = []
    if filters.get("hidden_only"):
        clauses.append("coalesce(hidden, 0) = 1")
    elif not filters.get("include_hidden"):
        clauses.append("coalesce(hidden, 0) = 0")
    if filters.get("author"):
        clauses.append("lower(handle) = ?")
        values.append(normalize_handle(filters["author"]))
    if filters.get("category"):
        clauses.append("categories_flat LIKE ?")
        values.append(f"%{str(filters['category']).casefold()}%")
    if filters.get("domain"):
        clauses.append("domains_flat LIKE ?")
        values.append(f"%{str(filters['domain']).casefold()}%")
    if filters.get("language"):
        clauses.append("lower(language) = ?")
        values.append(str(filters["language"]).casefold())
    if filters.get("bookmark_type_value"):
        clauses.append("lower(type) = ?")
        values.append(str(filters["bookmark_type_value"]).casefold())
    if filters.get("after"):
        clauses.append("date >= ?")
        values.append(str(filters["after"]))
    if filters.get("before"):
        clauses.append("date <= ?")
        values.append(str(filters["before"]))
    return clauses, values


def _bm25_candidates(conn: sqlite3.Connection, query: str, filters: dict, limit: int) -> list[tuple[str, float]]:
    clauses, values = _filters_sql(filters)
    where = " AND ".join(["bookmarks_fts MATCH ?"] + clauses)
    sql = f"""
        SELECT bookmarks.id, bm25(bookmarks_fts, 10.0, 8.0, 5.0, 5.0, 4.0, 2.0, 1.0, 1.0, 2.0, 3.0, 1.0) AS score
        FROM bookmarks_fts
        JOIN bookmarks ON bookmarks_fts.id = bookmarks.id
        WHERE {where}
        ORDER BY score
        LIMIT ?
    """
    rows = conn.execute(sql, [_fts_query(query), *values, limit]).fetchall()
    return [(row["id"], float(row["score"])) for row in rows]


def _vector_candidates(conn: sqlite3.Connection, query: str, filters: dict, limit: int, exclude_id: str | None = None) -> list[tuple[str, float]]:
    clauses, values = _filters_sql(filters)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
               categories_json, entities_json, urls_json, domains_json, hashtags_json,
               local_json, local_note, local_tags_json, local_tags_flat, hidden, rating, linked_pages_json,
               extracted_title, extracted_description, extracted_content, search_text, vector
        FROM bookmarks
        {where}
    """
    rows = conn.execute(sql, values).fetchall()
    query_vector = _query_vector(query)
    scored: list[tuple[str, float]] = []
    for row in rows:
        if exclude_id and row["id"] == exclude_id:
            continue
        vector = _unpack_vector(row["vector"]) if row["vector"] else [0.0] * VECTOR_DIM
        score = _dot(query_vector, vector)
        if score > 0:
            scored.append((row["id"], score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def _fetch_rows_by_ids(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
               categories_json, entities_json, urls_json, domains_json, hashtags_json,
               local_json, local_note, local_tags_json, local_tags_flat, hidden, rating, linked_pages_json,
               extracted_title, extracted_description, extracted_content, search_text, vector
        FROM bookmarks
        WHERE id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    return {row["id"]: _parse_row(row) for row in rows}


def _match_reasons(result: dict, query: str, bm25_rank: int | None, vector_rank: int | None) -> list[str]:
    reasons: list[str] = []
    lowered_query = query.casefold().strip()
    if bm25_rank is not None:
        reasons.append(f"bm25#{bm25_rank}")
    if vector_rank is not None:
        reasons.append(f"vector#{vector_rank}")
    if lowered_query and lowered_query in str(result.get("search_text", "")).casefold():
        reasons.append("exact_phrase")
    for category in result.get("categories", []):
        if lowered_query and lowered_query in category.casefold():
            reasons.append(f"category:{category}")
    for entity in result.get("entities", []):
        if lowered_query and lowered_query in entity.casefold():
            reasons.append(f"entity:{entity}")
    return _dedupe(reasons)


def _terms_in_text(text: str, terms: list[str]) -> list[str]:
    lowered = text.casefold()
    return [term for term in terms if term and term in lowered]


def _explain_payload(
    result: dict,
    *,
    query: str,
    combined_score: float,
    bm25_rank: int | None,
    bm25_score: float | None,
    vector_rank: int | None,
    vector_score: float | None,
) -> dict:
    bookmark = result["bookmark"]
    lowered_query = query.casefold().strip()
    terms = _dedupe(_tokenize(query.casefold()))
    exact_phrase = bool(lowered_query and lowered_query in result["search_text"].casefold())
    field_values = {
        "text": str(bookmark.get("text", "")),
        "summary": str(bookmark.get("ai", {}).get("summary", "")),
        "categories": " ".join(bookmark_categories(bookmark)),
        "entities": " ".join(bookmark_entities(bookmark)),
        "author": " ".join([str(bookmark.get("author", "")), str(bookmark.get("handle", ""))]),
        "domains": " ".join(iter_external_domains(bookmark)),
        "hashtags": " ".join(str(tag) for tag in bookmark.get("hashtags", [])),
        "linked_title": " ".join(str(page.get("title", "")) for page in bookmark_link_pages(bookmark)),
        "linked_description": " ".join(str(page.get("description", "")) for page in bookmark_link_pages(bookmark)),
        "linked_content": "\n".join(
            str(page.get("preview", "")).strip() or str(page.get("content", "")).strip()
            for page in bookmark_link_pages(bookmark)
        ),
    }
    matched_fields = {
        name: matched
        for name, value in field_values.items()
        if (matched := _terms_in_text(value, terms))
    }
    matched_terms = _dedupe(term for values in matched_fields.values() for term in values)
    matched_link_pages = []
    for page in bookmark_link_pages(bookmark):
        page_text = "\n".join(
            [
                str(page.get("title", "")),
                str(page.get("description", "")),
                str(page.get("preview", "")),
                str(page.get("content", "")),
            ]
        )
        page_terms = _terms_in_text(page_text, terms)
        if page_terms:
            matched_link_pages.append(
                {
                    "url": str(page.get("url", "")),
                    "title": str(page.get("title", "")),
                    "terms": page_terms,
                }
            )

    bm25_rrf = (1.0 / (RRF_K + bm25_rank)) if bm25_rank is not None else 0.0
    vector_rrf = (1.0 / (RRF_K + vector_rank)) if vector_rank is not None else 0.0
    exact_phrase_boost = 0.01 if exact_phrase else 0.0
    return {
        "matched_terms": matched_terms,
        "matched_fields": matched_fields,
        "matched_link_pages": matched_link_pages,
        "exact_phrase": exact_phrase,
        "rrf": {
            "combined_score": round(combined_score, 6),
            "bm25_rank": bm25_rank,
            "bm25_score": round(bm25_score, 6) if bm25_score is not None else None,
            "bm25_rrf": round(bm25_rrf, 6) if bm25_rrf else 0.0,
            "vector_rank": vector_rank,
            "vector_score": round(vector_score, 6) if vector_score is not None else None,
            "vector_rrf": round(vector_rrf, 6) if vector_rrf else 0.0,
            "exact_phrase_boost": round(exact_phrase_boost, 6),
        },
    }


def _result_payload(
    result: dict,
    *,
    score: float | None = None,
    bm25_rank: int | None = None,
    vector_rank: int | None = None,
    query: str | None = None,
    explain: dict | None = None,
) -> dict:
    bookmark = result["bookmark"]
    payload = {
        "id": result["id"],
        "author": result["author"],
        "handle": result["handle"],
        "date": result["date"],
        "summary": bookmark_summary(bookmark),
        "text": result["text"],
        "url": result["url"],
        "categories": result["categories"],
        "entities": result["entities"],
        "domains": result["domains"],
        "hashtags": list(bookmark.get("hashtags", [])),
        "note": bookmark_local_note(bookmark),
        "tags": bookmark_local_tags(bookmark),
        "rating": bookmark_local_rating(bookmark),
        "hidden": bookmark_hidden(bookmark),
        "external_urls": external_urls(bookmark),
        "link_pages": bookmark_link_pages(bookmark),
        "language": result["language"],
        "type": result["type"],
        "importance": result["importance"],
        "tweet_preview": bookmark_text_preview(bookmark),
        "linked_title": bookmark_link_title(bookmark),
        "linked_description": bookmark_link_description(bookmark),
        "linked_preview": bookmark_link_preview(bookmark),
        "linked_url": bookmark_link_url(bookmark),
        "linked_site": bookmark_link_site(bookmark),
        "score": round(score, 6) if score is not None else None,
        "why": _match_reasons(result, query or "", bm25_rank, vector_rank) if query else [],
        "deleted": bool(result.get("deleted")),
        "deleted_at": result.get("deleted_info", {}).get("deleted_at"),
        "deletion_source": result.get("deleted_info", {}).get("source"),
    }
    if explain is not None:
        payload["explain"] = explain
    return payload


def _deleted_result_payloads(paths: IndexPaths) -> list[dict]:
    state = _load_sync_state(paths)
    tombstones = state.get("tombstones", {})
    archive = state.get("archive", {})
    payloads: list[dict] = []
    for bookmark_id, deleted_info in tombstones.items():
        bookmark = archive.get(bookmark_id)
        if not isinstance(bookmark, dict) or not bookmark.get("id"):
            continue
        payloads.append(_result_payload(_parsed_from_bookmark(bookmark, deleted=True, deleted_info=deleted_info)))
    payloads.sort(
        key=lambda item: (
            str(item.get("deleted_at") or ""),
            str(item.get("date") or ""),
            str(item["id"]),
        ),
        reverse=True,
    )
    return payloads


def _matches_deleted_filters(
    item: dict,
    *,
    query: str | None = None,
    author: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    language: str | None = None,
    bookmark_type_value: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> bool:
    if author and item.get("handle") != author:
        return False
    if category and category not in item.get("categories", []):
        return False
    if domain and domain not in item.get("domains", []):
        return False
    if language and item.get("language") != language:
        return False
    if bookmark_type_value and item.get("type") != bookmark_type_value:
        return False
    item_date = parse_cli_date(item.get("date")) if item.get("date") and item.get("date") != "?" else None
    after_date = parse_cli_date(after)
    before_date = parse_cli_date(before)
    if after_date and item_date and item_date < after_date:
        return False
    if before_date and item_date and item_date > before_date:
        return False
    if query:
        haystack = "\n".join(
            [
                str(item.get("summary", "")),
                str(item.get("text", "")),
                str(item.get("linked_title", "")),
                str(item.get("linked_description", "")),
                str(item.get("linked_preview", "")),
                " ".join(item.get("categories", [])),
                " ".join(item.get("entities", [])),
            ]
        ).casefold()
        if query.casefold() not in haystack:
            return False
    return True


def _group_results(results: list[dict], group_by: str) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        keys: list[str]
        if group_by == "author":
            keys = [result["handle"] or "@unknown"]
        elif group_by == "year":
            keys = [result["date"][:4] if result["date"] and result["date"] != "?" else "unknown"]
        elif group_by == "domain":
            keys = result["domains"][:1] or ["(none)"]
        else:
            keys = result["categories"][:1] or ["Uncategorized"]
        for key in keys:
            grouped[key].append(result)
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    return {
        "group_by": group_by,
        "groups": [
            {"name": name, "count": len(items), "results": items}
            for name, items in ordered
        ],
    }


def search_bookmarks(
    query: str,
    *,
    author: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    language: str | None = None,
    bookmark_type_value: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    group_by: str | None = None,
    hidden: bool = False,
    explain: bool = False,
    auto_refresh: bool = True,
    paths: IndexPaths | None = None,
) -> list[dict] | dict:
    current_paths = paths or default_paths()
    ensure_index(paths=current_paths, auto_refresh=auto_refresh)
    conn = _connect(current_paths)
    try:
        filters = {
            "author": author,
            "category": category,
            "domain": domain,
            "language": language,
            "bookmark_type_value": bookmark_type_value,
            "after": after,
            "before": before,
            "hidden_only": hidden,
        }
        candidate_limit = max(limit * 8, 100)
        bm25 = _bm25_candidates(conn, query, filters, candidate_limit)
        vector = _vector_candidates(conn, query, filters, candidate_limit)
        bm25_ranks = {bookmark_id: rank for rank, (bookmark_id, _score) in enumerate(bm25, start=1)}
        bm25_scores = {bookmark_id: score for bookmark_id, score in bm25}
        vector_ranks = {bookmark_id: rank for rank, (bookmark_id, _score) in enumerate(vector, start=1)}
        vector_scores = {bookmark_id: score for bookmark_id, score in vector}
        candidate_ids = list(dict.fromkeys([bookmark_id for bookmark_id, _score in bm25] + [bookmark_id for bookmark_id, _score in vector]))
        rows = _fetch_rows_by_ids(conn, candidate_ids)

        scored: list[tuple[float, dict, int | None, int | None]] = []
        for bookmark_id in candidate_ids:
            result = rows.get(bookmark_id)
            if not result:
                continue
            bm25_rank = bm25_ranks.get(bookmark_id)
            vector_rank = vector_ranks.get(bookmark_id)
            combined = 0.0
            if bm25_rank is not None:
                combined += 1.0 / (RRF_K + bm25_rank)
            if vector_rank is not None:
                combined += 1.0 / (RRF_K + vector_rank)
            if query.casefold().strip() and query.casefold().strip() in result["search_text"].casefold():
                combined += 0.01
            scored.append((combined, result, bm25_rank, vector_rank))

        scored.sort(key=lambda item: (item[0], item[1]["date"]), reverse=True)
        results = [
            _result_payload(
                result,
                score=score,
                bm25_rank=bm25_rank,
                vector_rank=vector_rank,
                query=query,
                explain=(
                    _explain_payload(
                        result,
                        query=query,
                        combined_score=score,
                        bm25_rank=bm25_rank,
                        bm25_score=bm25_scores.get(result["id"]),
                        vector_rank=vector_rank,
                        vector_score=vector_scores.get(result["id"]),
                    )
                    if explain
                    else None
                ),
            )
            for score, result, bm25_rank, vector_rank in scored[:limit]
        ]
        return _group_results(results, group_by) if group_by else results
    finally:
        conn.close()


def list_bookmarks(
    *,
    query: str | None = None,
    author: str | None = None,
    category: str | None = None,
    domain: str | None = None,
    language: str | None = None,
    bookmark_type_value: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 30,
    offset: int = 0,
    sort: str = "date",
    deleted: bool = False,
    hidden: bool = False,
    auto_refresh: bool = True,
    paths: IndexPaths | None = None,
) -> list[dict]:
    current_paths = paths or default_paths()
    if deleted:
        results = [
            item
            for item in _deleted_result_payloads(current_paths)
            if _matches_deleted_filters(
                item,
                query=query,
                author=author,
                category=category,
                domain=domain,
                language=language,
                bookmark_type_value=bookmark_type_value,
                after=after,
                before=before,
            )
        ]
        if sort == "date":
            results.sort(key=lambda item: item["date"], reverse=True)
        return results[offset : offset + limit]

    ensure_index(paths=current_paths, auto_refresh=auto_refresh)
    if query:
        results = search_bookmarks(
            query,
            author=author,
            category=category,
            domain=domain,
                language=language,
                bookmark_type_value=bookmark_type_value,
                after=after,
                before=before,
                limit=limit + offset,
                hidden=hidden,
                auto_refresh=False,
                paths=current_paths,
            )
        assert isinstance(results, list)
        if sort == "date":
            results.sort(key=lambda item: item["date"], reverse=True)
        return results[offset : offset + limit]

    conn = _connect(current_paths)
    try:
        filters = {
            "author": author,
            "category": category,
            "domain": domain,
            "language": language,
            "bookmark_type_value": bookmark_type_value,
            "after": after,
            "before": before,
            "hidden_only": hidden,
        }
        clauses, values = _filters_sql(filters)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
                   categories_json, entities_json, urls_json, domains_json, hashtags_json,
                   local_json, local_note, local_tags_json, local_tags_flat, hidden, rating, linked_pages_json,
                   extracted_title, extracted_description, extracted_content, search_text, vector
            FROM bookmarks
            {where}
            ORDER BY date DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*values, limit, offset],
        ).fetchall()
        return [_result_payload(_parse_row(row)) for row in rows]
    finally:
        conn.close()


def show_bookmark(bookmark_id: str, *, auto_refresh: bool = True, paths: IndexPaths | None = None) -> dict | None:
    current_paths = paths or default_paths()
    ensure_index(paths=current_paths, auto_refresh=auto_refresh)
    conn = _connect(current_paths)
    try:
        row = conn.execute(
            """
            SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
                   categories_json, entities_json, urls_json, domains_json, hashtags_json,
                   local_json, local_note, local_tags_json, local_tags_flat, hidden, rating, linked_pages_json,
                   extracted_title, extracted_description, extracted_content, search_text, vector
            FROM bookmarks WHERE id = ?
            """,
            [bookmark_id],
        ).fetchone()
        if not row:
            return None
        parsed = _parse_row(row)
        return _result_payload(parsed)
    finally:
        conn.close()


def show_deleted_bookmark(bookmark_id: str, *, paths: IndexPaths | None = None) -> dict | None:
    current_paths = paths or default_paths()
    for item in _deleted_result_payloads(current_paths):
        if item["id"] == bookmark_id:
            return item
    return None


def bookmark_context(bookmark_id: str, *, limit: int = 5, auto_refresh: bool = True, paths: IndexPaths | None = None) -> dict | None:
    current_paths = paths or default_paths()
    ensure_index(paths=current_paths, auto_refresh=auto_refresh)
    conn = _connect(current_paths)
    try:
        row = conn.execute(
            """
            SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
                   categories_json, entities_json, urls_json, domains_json, hashtags_json,
                   local_json, local_note, local_tags_json, local_tags_flat, hidden, rating, linked_pages_json,
                   extracted_title, extracted_description, extracted_content, search_text, vector
            FROM bookmarks WHERE id = ?
            """,
            [bookmark_id],
        ).fetchone()
        if not row:
            return None
        anchor = _parse_row(row)
        anchor_out = _result_payload(anchor)

        same_author = []
        if anchor["handle"]:
            same_author = [
                item for item in list_bookmarks(author=anchor["handle"], limit=limit + 1, auto_refresh=False, paths=current_paths)
                if item["id"] != bookmark_id
            ][:limit]

        shared_categories: list[dict] = []
        if anchor["categories"]:
            for category in anchor["categories"][:2]:
                for item in list_bookmarks(category=category, limit=limit + 3, auto_refresh=False, paths=current_paths):
                    if item["id"] == bookmark_id or item in shared_categories:
                        continue
                    shared_categories.append(item)
                    if len(shared_categories) >= limit:
                        break
                if len(shared_categories) >= limit:
                    break

        shared_domains: list[dict] = []
        if anchor["domains"]:
            for domain in anchor["domains"][:2]:
                for item in list_bookmarks(domain=domain, limit=limit + 3, auto_refresh=False, paths=current_paths):
                    if item["id"] == bookmark_id or item in shared_domains:
                        continue
                    shared_domains.append(item)
                    if len(shared_domains) >= limit:
                        break
                if len(shared_domains) >= limit:
                    break

        vector = _vector_candidates(conn, anchor["summary"] or anchor["text"], {}, limit * 3, exclude_id=bookmark_id)
        similar_rows = _fetch_rows_by_ids(conn, [bookmark for bookmark, _score in vector])
        similar = [
            _result_payload(similar_rows[bookmark_id_value], score=score)
            for bookmark_id_value, score in vector
            if bookmark_id_value in similar_rows
        ][:limit]

        previous_row = conn.execute(
            "SELECT id FROM bookmarks WHERE date < ? AND coalesce(hidden, 0) = 0 ORDER BY date DESC LIMIT 1",
            [anchor["date"]],
        ).fetchone()
        next_row = conn.execute(
            "SELECT id FROM bookmarks WHERE date > ? AND coalesce(hidden, 0) = 0 ORDER BY date ASC LIMIT 1",
            [anchor["date"]],
        ).fetchone()

        return {
            "bookmark": anchor_out,
            "same_author": same_author,
            "shared_categories": shared_categories,
            "shared_domains": shared_domains,
            "similar": similar,
            "previous_id": previous_row["id"] if previous_row else None,
            "next_id": next_row["id"] if next_row else None,
        }
    finally:
        conn.close()


def collect_stats(*, auto_refresh: bool = True, paths: IndexPaths | None = None) -> dict:
    current_paths = paths or default_paths()
    ensure_index(paths=current_paths, auto_refresh=auto_refresh)
    conn = _connect(current_paths)
    try:
        rows = conn.execute(
            """
            SELECT handle, author, date, language, type, importance, categories_json, domains_json,
                   urls_json, extracted_content, text, media_count
            FROM bookmarks
            WHERE coalesce(hidden, 0) = 0
            """
        ).fetchall()
        authors = Counter()
        categories = Counter()
        domains = Counter()
        languages = Counter()
        types = Counter()
        monthly = Counter()
        dates: list[str] = []
        with_urls = 0
        with_extracted = 0
        total_text_len = 0
        for row in rows:
            if row["handle"]:
                authors[row["handle"]] += 1
            for category in json.loads(row["categories_json"] or "[]"):
                categories[category] += 1
            for domain in json.loads(row["domains_json"] or "[]"):
                domains[domain] += 1
            languages[row["language"] or "unknown"] += 1
            types[row["type"] or "unknown"] += 1
            if row["date"]:
                dates.append(row["date"])
                monthly[row["date"][:7]] += 1
            if json.loads(row["urls_json"] or "[]"):
                with_urls += 1
            if row["extracted_content"]:
                with_extracted += 1
            total_text_len += len(row["text"] or "")

        return {
            "total_bookmarks": len(rows),
            "unique_authors": len(authors),
            "date_range": {
                "earliest": min(dates) if dates else None,
                "latest": max(dates) if dates else None,
            },
            "top_authors": [{"handle": handle, "count": count} for handle, count in authors.most_common(15)],
            "categories": [{"name": name, "count": count} for name, count in categories.most_common(15)],
            "domains": [{"name": name, "count": count} for name, count in domains.most_common(15)],
            "languages": [{"language": name, "count": count} for name, count in languages.most_common(10)],
            "types": [{"type": name, "count": count} for name, count in types.most_common(10)],
            "monthly_activity": [{"month": month, "count": count} for month, count in sorted(monthly.items())],
            "with_media": sum(1 for row in rows if int(row["media_count"] or 0) > 0),
            "with_urls": with_urls,
            "with_extracted": with_extracted,
            "average_text_length": round(total_text_len / len(rows), 1) if rows else 0.0,
        }
    finally:
        conn.close()


def domain_counts(
    *,
    author: str | None = None,
    category: str | None = None,
    language: str | None = None,
    bookmark_type_value: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 25,
    auto_refresh: bool = True,
    paths: IndexPaths | None = None,
) -> list[dict]:
    current_paths = paths or default_paths()
    rows = list_bookmarks(
        author=author,
        category=category,
        language=language,
        bookmark_type_value=bookmark_type_value,
        after=after,
        before=before,
        limit=100000,
        auto_refresh=auto_refresh,
        paths=current_paths,
    )
    counts = Counter()
    for row in rows:
        for domain in row["domains"]:
            counts[domain] += 1
    return [{"domain": name, "count": count} for name, count in counts.most_common(limit)]


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""
    high = max(values)
    if high <= 0:
        return SPARKS[0] * len(values)
    return "".join(SPARKS[round(value / high * (len(SPARKS) - 1))] for value in values)


def _bar(value: int, high: int, width: int = 24) -> str:
    if high <= 0:
        return ""
    filled = max(1, round(value / high * width))
    return BLOCK * filled


def render_viz(*, auto_refresh: bool = True, paths: IndexPaths | None = None) -> str:
    stats = collect_stats(auto_refresh=auto_refresh, paths=paths)
    monthly = stats["monthly_activity"][-18:]
    lines = [
        "X Bookmarks Observatory",
        f"{stats['total_bookmarks']} bookmarks · {stats['unique_authors']} authors · "
        f"{stats['date_range']['earliest']} -> {stats['date_range']['latest']}",
        "",
    ]
    if monthly:
        lines.append("Recent activity")
        lines.append(_sparkline([item["count"] for item in monthly]))
        lines.append(" ".join(item["month"] for item in monthly[-6:]))
        lines.append("")
    for title, items, key in [
        ("Top categories", stats["categories"][:8], "name"),
        ("Top external domains", stats["domains"][:8], "name"),
        ("Top authors", stats["top_authors"][:8], "handle"),
    ]:
        if not items:
            continue
        high = items[0]["count"]
        lines.append(title)
        for item in items:
            lines.append(f"{item[key][:24].ljust(24)} {_bar(item['count'], high)} {item['count']}")
        lines.append("")
    lines.append("Languages")
    lines.append(", ".join(f"{item['language']}={item['count']}" for item in stats["languages"]))
    lines.append("")
    lines.append("Types")
    lines.append(", ".join(f"{item['type']}={item['count']}" for item in stats["types"]))
    lines.append("")
    lines.append("Coverage")
    lines.append(
        f"media={stats['with_media']} · urls={stats['with_urls']} · "
        f"extracted={stats['with_extracted']} · avg_text_len={stats['average_text_length']}"
    )
    return "\n".join(lines)


def format_search_results(results: list[dict] | dict) -> str:
    if isinstance(results, dict):
        lines = [f"Grouped by {results['group_by']}"]
        for group in results["groups"]:
            lines.append("")
            lines.append(f"{group['name']} ({group['count']})")
            for idx, result in enumerate(group["results"], start=1):
                labels = [label for label in [result["type"], *result["categories"][:2]] if label and label != "unknown"]
                meta = " · ".join([result["date"], result["handle"], *labels])
                if result.get("deleted"):
                    meta = f"[deleted] {meta}"
                elif result.get("hidden"):
                    meta = f"[hidden] {meta}"
                why = f" [{', '.join(result['why'][:3])}]" if result.get("why") else ""
                lines.append(f"{idx}. {meta}{why}")
                lines.extend(_format_result_preview_lines(result))
                lines.extend(_format_explain_lines(result))
                lines.append(f"   {result['url']}")
        return "\n".join(lines).strip()

    if not results:
        return "No results found."

    lines: list[str] = []
    for idx, result in enumerate(results, start=1):
        labels = [label for label in [result["type"], *result["categories"][:2]] if label and label != "unknown"]
        meta = " · ".join([result["date"], result["handle"], *labels])
        if result.get("deleted"):
            meta = f"[deleted] {meta}"
        elif result.get("hidden"):
            meta = f"[hidden] {meta}"
        why = f" [{', '.join(result['why'][:3])}]" if result.get("why") else ""
        lines.append(f"{idx}. {meta}{why}")
        lines.extend(_format_result_preview_lines(result))
        lines.extend(_format_explain_lines(result))
        lines.append(f"   {result['url']}")
    return "\n\n".join(lines)


def format_list_results(results: list[dict]) -> str:
    if not results:
        return "No bookmarks found."
    lines: list[str] = []
    for result in results:
        labels = [label for label in [result["id"], result["date"], result["handle"], result["type"], *result["categories"][:2]] if label and label != "unknown"]
        prefix = "[deleted] " if result.get("deleted") else "[hidden] " if result.get("hidden") else ""
        lines.append(f"{prefix}{' · '.join(labels)}")
        lines.extend("  " + line[3:] if line.startswith("   ") else line for line in _format_result_preview_lines(result))
        lines.extend("  " + line[3:] if line.startswith("   ") else line for line in _format_explain_lines(result))
        if result.get("deleted_at"):
            lines.append(f"  deleted: {result['deleted_at']} ({result.get('deletion_source') or 'unknown'})")
        if result["domains"]:
            lines.append(f"  domains: {', '.join(result['domains'][:4])}")
        if result.get("why"):
            lines.append(f"  why: {', '.join(result['why'])}")
        lines.append(f"  {result['url']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_result_preview_lines(result: dict) -> list[str]:
    lines = [f"   {result['summary']}"]
    tweet_preview = result.get("tweet_preview", "")
    if tweet_preview and tweet_preview.casefold() != str(result["summary"]).casefold():
        lines.append(f"   tweet: {tweet_preview}")
    linked_title = str(result.get("linked_title", "")).strip()
    linked_description = str(result.get("linked_description", "")).strip()
    linked_preview = str(result.get("linked_preview", "")).strip()
    if linked_title:
        linked_line = linked_title
        if linked_description and linked_description.casefold() != linked_title.casefold():
            linked_line = f"{linked_line} — {linked_description}"
        lines.append(f"   link: {linked_line}")
    elif linked_description:
        lines.append(f"   link: {linked_description}")
    elif linked_preview and linked_preview.casefold() != tweet_preview.casefold():
        lines.append(f"   link: {linked_preview}")
    return lines


def _format_explain_lines(result: dict) -> list[str]:
    explain = result.get("explain")
    if not explain:
        return []
    rrf = explain.get("rrf", {})
    header_parts = [
        f"rrf={rrf.get('combined_score')}",
        f"bm25#{rrf.get('bm25_rank')}" if rrf.get("bm25_rank") is not None else None,
        f"vector#{rrf.get('vector_rank')}" if rrf.get("vector_rank") is not None else None,
    ]
    lines = [f"   explain: {', '.join(part for part in header_parts if part)}"]
    matched_fields = explain.get("matched_fields", {})
    if matched_fields:
        field_bits = [f"{name}={','.join(terms[:3])}" for name, terms in matched_fields.items()]
        lines.append(f"   fields: {'; '.join(field_bits[:4])}")
    matched_link_pages = explain.get("matched_link_pages", [])
    if matched_link_pages:
        page = matched_link_pages[0]
        lines.append(f"   matched link: {page.get('title') or page.get('url') or '(untitled)'}")
    return lines


def format_show_result(result: dict | None) -> str:
    if not result:
        return "Bookmark not found."
    lines = [
        f"{'[deleted] ' if result.get('deleted') else ''}{result['id']} · {result['handle']} · {result['date']}",
        result["url"],
        "",
        result["summary"],
        "",
        "tweet",
        result["text"],
    ]
    if result.get("deleted_at"):
        lines.extend(["", f"deleted: {result['deleted_at']} ({result.get('deletion_source') or 'unknown'})"])
    if result.get("linked_title") or result.get("linked_description") or result.get("linked_preview"):
        heading = "linked"
        if result.get("linked_source") in {"stored", "fetched"}:
            heading = f"{heading} ({result['linked_source']})"
        lines.extend(["", heading])
        if result.get("linked_url"):
            lines.append(result["linked_url"])
        if result.get("linked_title"):
            lines.append(result["linked_title"])
        if result.get("linked_description"):
            lines.append(result["linked_description"])
        linked_preview = str(result.get("linked_preview", "")).strip()
        linked_description = str(result.get("linked_description", "")).strip()
        linked_title = str(result.get("linked_title", "")).strip()
        if linked_preview and linked_preview.casefold() not in {
            linked_title.casefold(),
            linked_description.casefold(),
        }:
            lines.extend(["", linked_preview])
    elif result.get("linked_url"):
        heading = "linked"
        if result.get("linked_source") == "available":
            heading = f"{heading} (available)"
        elif result.get("linked_source") == "fetch_failed":
            heading = f"{heading} (fetch failed)"
        lines.extend(["", heading, result["linked_url"]])
        if result.get("linked_source") == "available":
            lines.append("(use --fetch-link to extract this page now)")
    extra_pages = list(result.get("link_pages", []))[1:]
    if extra_pages:
        lines.extend(["", "other links"])
        for page in extra_pages[:3]:
            lines.extend(_format_link_page(page, bullet=True))
    if result["categories"]:
        lines.extend(["", f"categories: {', '.join(result['categories'])}"])
    if result["entities"]:
        lines.extend(["", f"entities: {', '.join(result['entities'])}"])
    if result.get("tags"):
        lines.extend(["", f"tags: {', '.join('#' + tag for tag in result['tags'])}"])
    if result.get("rating") is not None:
        lines.extend(["", f"rating: {result['rating']}/5"])
    if result.get("note"):
        lines.extend(["", "note", result["note"]])
    if result.get("hidden"):
        lines.extend(["", "hidden: true"])
    if result["domains"]:
        lines.extend(["", f"domains: {', '.join(result['domains'])}"])
    if result["hashtags"]:
        lines.extend(["", f"hashtags: {', '.join('#' + tag.lstrip('#') for tag in result['hashtags'][:8])}"])
    return "\n".join(lines)


def _format_related_item(item: dict) -> list[str]:
    lines = [f"- {item['id']} · {item['date']} · {item['handle']} · {item['summary']}"]
    tweet_preview = str(item.get("tweet_preview", "")).strip()
    if tweet_preview and tweet_preview.casefold() != str(item["summary"]).casefold():
        lines.append(f"  tweet: {tweet_preview}")
    linked_title = str(item.get("linked_title", "")).strip()
    if linked_title:
        lines.append(f"  link: {linked_title}")
    return lines


def _format_link_page(page: dict, *, bullet: bool = False) -> list[str]:
    prefix = "- " if bullet else ""
    title = str(page.get("title", "")).strip() or str(page.get("url", "")).strip() or "(untitled link)"
    url = str(page.get("url", "")).strip()
    site = str(page.get("site_name", "")).strip()
    description = _truncate(str(page.get("description", "")).strip(), 160)
    preview = _truncate(str(page.get("preview", "")).strip() or str(page.get("content", "")).strip(), 220)
    label = title if not site else f"{title} [{site}]"
    lines = [f"{prefix}{label}"]
    if url:
        lines.append(f"  {url}")
    if description and description.casefold() != title.casefold():
        lines.append(f"  {description}")
    if preview and preview.casefold() not in {title.casefold(), description.casefold()}:
        lines.append(f"  {preview}")
    return lines


def format_context_result(result: dict | None) -> str:
    if not result:
        return "Bookmark not found."
    lines = [format_show_result(result["bookmark"])]
    for section in ("same_author", "shared_categories", "shared_domains", "similar"):
        items = result.get(section, [])
        if not items:
            continue
        lines.extend(["", section.replace("_", " ").title()])
        for item in items:
            lines.extend(_format_related_item(item))
    if result.get("previous_id") or result.get("next_id"):
        lines.extend(["", f"timeline: prev={result.get('previous_id')} next={result.get('next_id')}"])
    return "\n".join(lines)
