"""Phase 1: Extract content from external URLs using summarize CLI."""

import json
import re
import subprocess
import sys
import time
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bookmark_paths import resolve_base_dir
from text_repair import repair_value

MAX_EXTRACT_CHARS = 5000
MAX_LINKS_PER_BOOKMARK = 3
FALLBACK_FETCH_BYTES = 512_000
FALLBACK_TIMEOUT_SECONDS = 15

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


def _has_link_payload(page: dict | None) -> bool:
    if not isinstance(page, dict):
        return False
    return any(str(page.get(field, "")).strip() for field in ("title", "description", "content"))


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


class _HTMLMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self.description = ""
        self.site_name = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): (value or "") for key, value in attrs}
        lower_tag = tag.lower()
        if lower_tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if lower_tag == "title":
            self._in_title = True
            return
        if lower_tag != "meta":
            return
        key = (attrs_map.get("name") or attrs_map.get("property") or "").lower()
        content = attrs_map.get("content", "").strip()
        if not content:
            return
        if key in {"description", "og:description", "twitter:description"} and not self.description:
            self.description = content
        if key in {"og:site_name", "application-name"} and not self.site_name:
            self.site_name = content
        if key in {"og:title", "twitter:title"} and not self._title_parts:
            self._title_parts.append(content)

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.lower()
        if lower_tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if lower_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = " ".join(data.split()).strip()
        if not clean:
            return
        if self._in_title:
            self._title_parts.append(clean)
            return
        self._text_parts.append(clean)

    @property
    def title(self) -> str:
        seen: set[str] = set()
        ordered: list[str] = []
        for part in self._title_parts:
            clean = part.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
        return " - ".join(ordered[:2])

    @property
    def text(self) -> str:
        clean = " ".join(self._text_parts)
        clean = re.sub(r"\s+", " ", unescape(clean)).strip()
        return clean[:MAX_EXTRACT_CHARS]


def _normalize_summarize_payload(url: str, ext: dict) -> dict | None:
    content = str(ext.get("content", "") or "")[:MAX_EXTRACT_CHARS]
    page = {
        "url": url,
        "title": str(ext.get("title", "") or ""),
        "description": str(ext.get("description", "") or ""),
        "content": content,
        "preview": _preview_text(content or ext.get("description", "") or ext.get("title", "")),
        "word_count": int(ext.get("wordCount", 0) or 0),
        "site_name": str(ext.get("siteName", "") or ""),
    }
    return page if _has_link_payload(page) else None


def _fallback_extract_url(url: str) -> dict | None:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; x-bookmarks/0.1; +https://github.com/YogevKr/x-bookmarks)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=FALLBACK_TIMEOUT_SECONDS) as response:
            final_url = str(response.geturl() or url)
            content_type = str(response.headers.get("Content-Type", "")).lower()
            raw = response.read(FALLBACK_FETCH_BYTES)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None

    charset = "utf-8"
    match = re.search(r"charset=([a-zA-Z0-9._-]+)", content_type)
    if match:
        charset = match.group(1)
    text = raw.decode(charset, errors="ignore")

    if "text/plain" in content_type or final_url.endswith((".txt", ".md")):
        content = text[:MAX_EXTRACT_CHARS].strip()
        page = {
            "url": final_url,
            "title": final_url.rsplit("/", 1)[-1] or final_url,
            "description": "",
            "content": content,
            "preview": _preview_text(content or final_url),
            "word_count": len(content.split()),
            "site_name": urlparse(final_url).netloc.replace("www.", ""),
        }
        return page if _has_link_payload(page) else None

    parser = _HTMLMetadataParser()
    parser.feed(text)
    parser.close()
    page = {
        "url": final_url,
        "title": parser.title,
        "description": parser.description,
        "content": parser.text,
        "preview": _preview_text(parser.text or parser.description or parser.title),
        "word_count": len(parser.text.split()),
        "site_name": parser.site_name or urlparse(final_url).netloc.replace("www.", ""),
    }
    return page if _has_link_payload(page) else None


def _merge_extracted(primary: dict | None, fallback: dict | None) -> dict | None:
    if primary and fallback:
        content = primary.get("content") or fallback.get("content") or ""
        page = {
            "url": primary.get("url") or fallback.get("url") or "",
            "title": primary.get("title") or fallback.get("title") or "",
            "description": primary.get("description") or fallback.get("description") or "",
            "content": content[:MAX_EXTRACT_CHARS],
            "preview": _preview_text(content or primary.get("description") or fallback.get("description") or primary.get("title") or fallback.get("title") or ""),
            "word_count": primary.get("word_count") or fallback.get("word_count") or len(content.split()),
            "site_name": primary.get("site_name") or fallback.get("site_name") or "",
        }
        return page if _has_link_payload(page) else None
    if primary:
        return primary if _has_link_payload(primary) else None
    if fallback:
        return fallback if _has_link_payload(fallback) else None
    return None


def extract_url(url: str) -> dict | None:
    """Run summarize --extract --json on a single URL. Returns extracted dict or None."""
    summarized: dict | None = None
    try:
        result = subprocess.run(
            [
                "summarize", "--extract", "--json", "--plain",
                "--max-extract-characters", str(MAX_EXTRACT_CHARS),
                url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            summarized = _normalize_summarize_payload(url, data.get("extracted", {}))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"  FAIL: {url[:60]} — {e}", file=sys.stderr)
    if summarized and (str(summarized.get("content", "")).strip() or str(summarized.get("description", "")).strip()):
        return summarized
    fallback = _fallback_extract_url(url)
    return _merge_extracted(summarized, fallback)


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
