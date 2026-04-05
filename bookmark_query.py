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

BASE_DIR = Path(__file__).parent
INDEX_VERSION = 2
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


def default_paths() -> IndexPaths:
    return IndexPaths(base_dir=BASE_DIR)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


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
    title = str(bookmark.get("extracted", {}).get("title", "")).strip()
    if title:
        return _truncate(title, limit)
    return _truncate(str(bookmark.get("text", "")).strip(), limit)


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
            if bookmark.get("extracted"):
                existing["extracted"] = bookmark["extracted"]

    categorized = sources["categorized"]
    if categorized:
        for bookmark in categorized.get("bookmarks", []):
            existing = base_rows.get(bookmark["id"])
            if not existing:
                continue
            if bookmark.get("ai"):
                existing["ai"] = bookmark["ai"]

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


def _connect(paths: IndexPaths) -> sqlite3.Connection:
    _ensure_data_dir(paths)
    conn = sqlite3.connect(paths.index_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(id UNINDEXED, text, summary, categories, entities, extracted_title, extracted_description, extracted_content, author, handle, domains, hashtags, tokenize='unicode61')")


def _combined_search_text(bookmark: dict) -> str:
    extracted = bookmark.get("extracted", {})
    ai = bookmark.get("ai", {})
    pieces = [
        str(bookmark.get("text", "")),
        str(ai.get("summary", "")),
        " ".join(bookmark_categories(bookmark)),
        " ".join(bookmark_entities(bookmark)),
        str(extracted.get("title", "")),
        str(extracted.get("description", "")),
        str(extracted.get("content", ""))[:4000],
        str(bookmark.get("author", "")),
        str(bookmark.get("handle", "")),
        " ".join(iter_external_domains(bookmark)),
        " ".join(bookmark.get("hashtags", [])),
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
    extracted = bookmark.get("extracted", {})
    ai = bookmark.get("ai", {})
    vector = [0.0] * VECTOR_DIM
    parts = [
        (str(bookmark.get("text", "")), 3.0, True),
        (str(ai.get("summary", "")), 2.0, True),
        (" ".join(bookmark_categories(bookmark)), 2.0, False),
        (" ".join(bookmark_entities(bookmark)), 2.0, False),
        (str(extracted.get("title", "")), 2.0, True),
        (str(extracted.get("description", "")), 1.0, True),
        (str(extracted.get("content", ""))[:2000], 1.0, True),
        (" ".join(iter_external_domains(bookmark)), 1.5, False),
        (str(bookmark.get("author", "")), 1.0, False),
        (str(bookmark.get("handle", "")), 1.0, False),
        (" ".join(bookmark.get("hashtags", [])), 1.0, False),
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
    extracted = bookmark.get("extracted", {})
    categories = bookmark_categories(bookmark)
    entities = bookmark_entities(bookmark)
    domains = iter_external_domains(bookmark)
    hashtags = list(bookmark.get("hashtags", []))
    vector = _pack_vector(_bookmark_vector(bookmark))
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
        "hashtags_text": " ".join(hashtags),
        "extracted_title": str(extracted.get("title", "")),
        "extracted_description": str(extracted.get("description", "")),
        "extracted_content": str(extracted.get("content", ""))[:4000],
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
    conn = _connect(current_paths)
    try:
        _ensure_schema(conn)
        with conn:
            if force:
                conn.execute("DELETE FROM bookmarks")
                conn.execute("DELETE FROM bookmarks_fts")
            else:
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
                        domains_flat, hashtags_json, extracted_title, extracted_description, extracted_content,
                        search_text, vector
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        extracted_content, author, handle, domains, hashtags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        if manifest.get("source_state", {}).get("files") != source_state.get("files"):
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
        "index_db": str(current_paths.index_db),
        "manifest_path": str(current_paths.manifest_file),
        "doc_count": len(bookmarks),
        "indexed_count": indexed_count,
        "source_state": source_state,
        "built_at": manifest.get("built_at") if manifest else None,
    }


def ensure_index(*, paths: IndexPaths | None = None, auto_refresh: bool = True, force: bool = False) -> dict:
    current_paths = paths or default_paths()
    status = get_index_status(paths=current_paths)
    if (status["stale"] and auto_refresh) or force:
        return refresh_index(paths=current_paths, force=force)
    return status


def _parse_row(row: sqlite3.Row) -> dict:
    categories = json.loads(row["categories_json"] or "[]")
    entities = json.loads(row["entities_json"] or "[]")
    urls = json.loads(row["urls_json"] or "[]")
    domains = json.loads(row["domains_json"] or "[]")
    hashtags = json.loads(row["hashtags_json"] or "[]")
    bookmark = {
        "id": row["id"],
        "handle": row["handle"],
        "author": row["author"],
        "timestamp": row["timestamp"],
        "text": row["text"],
        "urls": urls,
        "hashtags": hashtags,
        "extracted": {
            "title": row["extracted_title"],
            "description": row["extracted_description"],
            "content": row["extracted_content"],
        },
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


def _result_payload(result: dict, *, score: float | None = None, bm25_rank: int | None = None, vector_rank: int | None = None, query: str | None = None) -> dict:
    bookmark = result["bookmark"]
    return {
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
        "language": result["language"],
        "type": result["type"],
        "importance": result["importance"],
        "score": round(score, 6) if score is not None else None,
        "why": _match_reasons(result, query or "", bm25_rank, vector_rank) if query else [],
    }


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
        }
        candidate_limit = max(limit * 8, 100)
        bm25 = _bm25_candidates(conn, query, filters, candidate_limit)
        vector = _vector_candidates(conn, query, filters, candidate_limit)
        bm25_ranks = {bookmark_id: rank for rank, (bookmark_id, _score) in enumerate(bm25, start=1)}
        vector_ranks = {bookmark_id: rank for rank, (bookmark_id, _score) in enumerate(vector, start=1)}
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
            _result_payload(result, score=score, bm25_rank=bm25_rank, vector_rank=vector_rank, query=query)
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
    auto_refresh: bool = True,
    paths: IndexPaths | None = None,
) -> list[dict]:
    current_paths = paths or default_paths()
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
        }
        clauses, values = _filters_sql(filters)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
                   categories_json, entities_json, urls_json, domains_json, hashtags_json,
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


def bookmark_context(bookmark_id: str, *, limit: int = 5, auto_refresh: bool = True, paths: IndexPaths | None = None) -> dict | None:
    current_paths = paths or default_paths()
    ensure_index(paths=current_paths, auto_refresh=auto_refresh)
    conn = _connect(current_paths)
    try:
        row = conn.execute(
            """
            SELECT id, handle, author, timestamp, date, text, summary, language, type, importance,
                   categories_json, entities_json, urls_json, domains_json, hashtags_json,
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
            "SELECT id FROM bookmarks WHERE date < ? ORDER BY date DESC LIMIT 1",
            [anchor["date"]],
        ).fetchone()
        next_row = conn.execute(
            "SELECT id FROM bookmarks WHERE date > ? ORDER BY date ASC LIMIT 1",
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
                why = f" [{', '.join(result['why'][:3])}]" if result.get("why") else ""
                lines.append(f"{idx}. {meta}{why}")
                lines.append(f"   {result['summary']}")
                lines.append(f"   {result['url']}")
        return "\n".join(lines).strip()

    if not results:
        return "No results found."

    lines: list[str] = []
    for idx, result in enumerate(results, start=1):
        labels = [label for label in [result["type"], *result["categories"][:2]] if label and label != "unknown"]
        meta = " · ".join([result["date"], result["handle"], *labels])
        why = f" [{', '.join(result['why'][:3])}]" if result.get("why") else ""
        lines.append(f"{idx}. {meta}{why}")
        lines.append(f"   {result['summary']}")
        lines.append(f"   {result['url']}")
    return "\n\n".join(lines)


def format_list_results(results: list[dict]) -> str:
    if not results:
        return "No bookmarks found."
    lines: list[str] = []
    for result in results:
        labels = [label for label in [result["id"], result["date"], result["handle"], result["type"], *result["categories"][:2]] if label and label != "unknown"]
        lines.append(" · ".join(labels))
        lines.append(f"  {result['summary']}")
        if result["domains"]:
            lines.append(f"  domains: {', '.join(result['domains'][:4])}")
        if result.get("why"):
            lines.append(f"  why: {', '.join(result['why'])}")
        lines.append(f"  {result['url']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_show_result(result: dict | None) -> str:
    if not result:
        return "Bookmark not found."
    lines = [
        f"{result['id']} · {result['handle']} · {result['date']}",
        result["url"],
        "",
        result["summary"],
        "",
        result["text"],
    ]
    if result["categories"]:
        lines.extend(["", f"categories: {', '.join(result['categories'])}"])
    if result["entities"]:
        lines.extend(["", f"entities: {', '.join(result['entities'])}"])
    if result["domains"]:
        lines.extend(["", f"domains: {', '.join(result['domains'])}"])
    return "\n".join(lines)


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
            lines.append(f"- {item['id']} · {item['date']} · {item['handle']} · {item['summary']}")
    if result.get("previous_id") or result.get("next_id"):
        lines.extend(["", f"timeline: prev={result.get('previous_id')} next={result.get('next_id')}"])
    return "\n".join(lines)
