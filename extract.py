"""Phase 1: Extract content from external URLs using summarize CLI."""

import json
import subprocess
import sys
import time
from urllib.parse import urlparse

from bookmark_paths import resolve_base_dir
from text_repair import repair_value

MAX_EXTRACT_CHARS = 5000
MAX_LINKS_PER_BOOKMARK = 3

SKIP_DOMAINS = {"x.com", "twitter.com", "t.co"}


def should_extract(url: str) -> bool:
    """Skip x.com self-references and t.co shortlinks without expansion."""
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        return domain not in SKIP_DOMAINS
    except Exception:
        return False


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def _preview_text(text: str, limit: int = 320) -> str:
    return _truncate(text, limit)


def _normalize_page(page: dict, *, fallback_url: str | None = None) -> dict:
    return {
        "url": str(page.get("url") or fallback_url or ""),
        "title": str(page.get("title", "")),
        "description": str(page.get("description", "")),
        "content": str(page.get("content", ""))[:MAX_EXTRACT_CHARS],
        "preview": str(page.get("preview") or _preview_text(page.get("content", ""))),
        "word_count": int(page.get("word_count", 0) or 0),
        "site_name": str(page.get("site_name", "")),
    }


def _primary_extracted(page: dict) -> dict:
    return {
        "url": page.get("url", ""),
        "title": page.get("title", ""),
        "description": page.get("description", ""),
        "content": page.get("content", ""),
        "preview": page.get("preview", ""),
        "word_count": page.get("word_count", 0),
        "site_name": page.get("site_name", ""),
    }


def _existing_link_pages(bookmark: dict) -> list[dict]:
    pages = [_normalize_page(page) for page in bookmark.get("linked_pages", []) if isinstance(page, dict)]
    if pages:
        return pages
    extracted = bookmark.get("extracted")
    if not isinstance(extracted, dict):
        return []
    urls = [url for url in bookmark.get("urls", []) if should_extract(url)]
    fallback_url = extracted.get("url") or (urls[0] if urls else None)
    return [_normalize_page(extracted, fallback_url=fallback_url)]


def _dedupe_pages(pages: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for page in pages:
        key = (str(page.get("url", "")).strip(), str(page.get("title", "")).strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(page)
    return deduped


def extract_url(url: str) -> dict | None:
    """Run summarize --extract --json on a single URL. Returns extracted dict or None."""
    try:
        result = subprocess.run(
            [
                "summarize", "--extract", "--json", "--plain",
                "--max-extract-characters", str(MAX_EXTRACT_CHARS),
                url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        ext = data.get("extracted", {})
        content = ext.get("content", "")
        if not content or len(content.strip()) < 50:
            return None
        return {
            "url": url,
            "title": ext.get("title", ""),
            "description": ext.get("description", ""),
            "content": content[:MAX_EXTRACT_CHARS],
            "preview": _preview_text(content),
            "word_count": ext.get("wordCount", 0),
            "site_name": ext.get("siteName", ""),
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"  FAIL: {url[:60]} — {e}", file=sys.stderr)
        return None


def run_extraction(
    *,
    force: bool = False,
    limit: int | None = None,
    bookmark_id: str | None = None,
) -> None:
    base_dir = resolve_base_dir()
    bookmarks_file = base_dir / "bookmarks.json"
    enriched_file = base_dir / "enriched.json"

    with bookmarks_file.open(encoding="utf-8") as f:
        data = repair_value(json.load(f))

    bookmarks = data["bookmarks"]

    # Load existing enriched data for resume
    enriched_map: dict[str, dict] = {}
    if enriched_file.exists():
        with enriched_file.open(encoding="utf-8") as f:
            existing = repair_value(json.load(f))
        for bm in existing["bookmarks"]:
            pages = _existing_link_pages(bm)
            if pages:
                enriched_map[bm["id"]] = {
                    "linked_pages": pages,
                    "extracted": _primary_extracted(pages[0]),
                }
        if not force and not bookmark_id:
            print(f"Resuming: {len(enriched_map)} already extracted")

    # Collect URLs to extract
    todo = []
    for bm in bookmarks:
        if bookmark_id and bm["id"] != bookmark_id:
            continue
        urls = [u for u in bm.get("urls", []) if should_extract(u)][:MAX_LINKS_PER_BOOKMARK]
        if urls:
            existing = enriched_map.get(bm["id"], {})
            existing_pages = existing.get("linked_pages", [])
            covered = {page.get("url") for page in existing_pages}
            needs_upgrade = force or any(url not in covered for url in urls)
            if needs_upgrade:
                todo.append((bm["id"], urls))

    if limit is not None:
        todo = todo[:limit]

    print(f"Extracting content from {len(todo)} bookmarks ({sum(len(u) for _, u in todo)} URLs)...")

    extracted_count = 0
    extracted_pages = 0
    failed_count = 0

    for i, (bm_id, urls) in enumerate(todo):
        existing_pages = [] if force else list(enriched_map.get(bm_id, {}).get("linked_pages", []))
        covered = {page.get("url") for page in existing_pages}
        results = list(existing_pages)
        bookmark_had_success = False
        for url in urls:
            if not force and url in covered:
                continue
            print(f"  [{i+1}/{len(todo)}] {url[:70]}...", end=" ", flush=True)
            result = extract_url(url)
            if result:
                print(f"OK ({result['word_count']}w)")
                results.append(_normalize_page(result))
                covered.add(url)
                bookmark_had_success = True
                extracted_pages += 1
            else:
                print("skip")

        results = _dedupe_pages(results)
        if results:
            enriched_map[bm_id] = {
                "linked_pages": results,
                "extracted": _primary_extracted(results[0]),
            }
        if bookmark_had_success:
            extracted_count += 1
        elif not results:
            failed_count += 1

        # Save progress every 20 bookmarks
        if (i + 1) % 20 == 0:
            _save_enriched(data, bookmarks, enriched_map, enriched_file)

        # Rate limit
        time.sleep(0.5)

    _save_enriched(data, bookmarks, enriched_map, enriched_file)
    print(
        f"\nDone: {extracted_count} bookmarks updated, {extracted_pages} pages extracted, "
        f"{failed_count} failed, {len(enriched_map)} total enriched"
    )


def _save_enriched(data: dict, bookmarks: list, enriched_map: dict, output_file):
    enriched_bookmarks = []
    for bm in bookmarks:
        enriched = {**bm}
        if bm["id"] in enriched_map:
            enriched["extracted"] = enriched_map[bm["id"]]["extracted"]
            enriched["linked_pages"] = enriched_map[bm["id"]]["linked_pages"]
        enriched_bookmarks.append(enriched)

    output = {**data, "bookmarks": enriched_bookmarks}
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
