"""Phase 1: Extract content from external URLs using summarize CLI."""

from datetime import UTC, datetime
from functools import lru_cache
import json
import re
import subprocess
import sys
import time
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from bookmark_paths import resolve_base_dir
from text_repair import repair_value

MAX_EXTRACT_CHARS = 5000
MAX_LINKS_PER_BOOKMARK = 3
FALLBACK_FETCH_BYTES = 512_000
FALLBACK_TIMEOUT_SECONDS = 15

SKIP_DOMAINS = {"x.com", "twitter.com", "t.co"}
SHORTLINK_DOMAINS = {"bloom.bg", "buff.ly", "ow.ly"}
TERMINAL_DOMAINS = {"api.openai.com"}
DROP_QUERY_PARAMS = {"ab_channel", "fbclid", "feature", "g_st", "mc_cid", "mc_eid", "si", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}
USER_AGENT = "Mozilla/5.0 (compatible; x-bookmarks/0.1; +https://github.com/YogevKr/x-bookmarks)"


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
    normalized = {
        "url": str(page.get("url") or fallback_url or ""),
        "title": str(page.get("title", "")),
        "description": str(page.get("description", "")),
        "content": str(page.get("content", ""))[:MAX_EXTRACT_CHARS],
        "preview": str(page.get("preview") or _preview_text(page.get("content", ""))),
        "word_count": int(page.get("word_count", 0) or 0),
        "site_name": str(page.get("site_name", "")),
    }
    resolved_url = str(page.get("resolved_url", "")).strip()
    if resolved_url:
        normalized["resolved_url"] = resolved_url
    return normalized


def _has_link_payload(page: dict | None) -> bool:
    if not isinstance(page, dict):
        return False
    return any(str(page.get(field, "")).strip() for field in ("title", "description", "content"))


def _primary_extracted(page: dict) -> dict:
    payload = {
        "url": page.get("url", ""),
        "title": page.get("title", ""),
        "description": page.get("description", ""),
        "content": page.get("content", ""),
        "preview": page.get("preview", ""),
        "word_count": page.get("word_count", 0),
        "site_name": page.get("site_name", ""),
    }
    if page.get("resolved_url"):
        payload["resolved_url"] = page.get("resolved_url", "")
    return payload


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "").lower()


def _request_headers(*, accept: str = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8") -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }


def _normalize_target_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{url.strip()}")

    scheme = parsed.scheme.lower() or "https"
    domain = parsed.netloc.lower()
    path = parsed.path or "/"
    query_pairs = list(parse_qsl(parsed.query, keep_blank_values=True))

    def keep_param(key: str) -> bool:
        if key in DROP_QUERY_PARAMS:
            return False
        if key.startswith("utm_"):
            return False
        return True

    if domain == "youtu.be":
        video_id = path.strip("/").split("/", 1)[0]
        if video_id:
            domain = "www.youtube.com"
            path = "/watch"
            query_pairs = [("v", video_id)]

    if domain in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path.startswith("/live/") or path.startswith("/shorts/"):
            video_id = path.strip("/").split("/", 1)[1] if "/" in path.strip("/") else ""
            if video_id:
                path = "/watch"
                query_pairs = [("v", video_id)]
        elif path == "/watch":
            query_pairs = [(key, value) for key, value in query_pairs if key in {"v", "list"}]

    query_pairs = [(key, value) for key, value in query_pairs if keep_param(key)]
    return urlunparse((scheme, domain, path, "", urlencode(query_pairs, doseq=True), ""))


@lru_cache(maxsize=512)
def _resolve_shortlink_target(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "").lower()
    if domain not in SHORTLINK_DOMAINS and not (domain == "blog.openai.com" and parsed.path.startswith("/p/")):
        return url

    request = Request(url, headers=_request_headers())
    try:
        with urlopen(request, timeout=FALLBACK_TIMEOUT_SECONDS) as response:
            final_url = str(response.geturl() or url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return url
    return _normalize_target_url(final_url)


def _prepare_target_url(url: str) -> tuple[str, str]:
    normalized = _normalize_target_url(url)
    resolved = _resolve_shortlink_target(normalized)
    return normalized, resolved


def _failure_record(
    url: str,
    *,
    reason: str,
    terminal: bool,
    normalized_url: str | None = None,
    resolved_url: str | None = None,
    previous: dict | None = None,
) -> dict:
    attempts = 1
    if isinstance(previous, dict):
        previous_attempts = previous.get("attempts")
        if isinstance(previous_attempts, int) and previous_attempts > 0:
            attempts = previous_attempts + 1
    payload = {
        "reason": reason,
        "terminal": terminal,
        "failed_at": _now_iso(),
        "attempts": attempts,
    }
    if normalized_url and normalized_url != url:
        payload["normalized_url"] = normalized_url
    if resolved_url and resolved_url not in {url, normalized_url or ""}:
        payload["resolved_url"] = resolved_url
    return payload


def _normalize_failure_map(value: dict | None) -> dict[str, dict]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict] = {}
    for url, metadata in value.items():
        clean_url = str(url).strip()
        if not clean_url:
            continue
        if isinstance(metadata, dict):
            normalized[clean_url] = {
                "reason": str(metadata.get("reason", "extract_failed")),
                "terminal": bool(metadata.get("terminal")),
                "failed_at": str(metadata.get("failed_at") or _now_iso()),
                "attempts": int(metadata.get("attempts", 1) or 1),
            }
            for key in ("normalized_url", "resolved_url"):
                if metadata.get(key):
                    normalized[clean_url][key] = str(metadata[key])
            continue
        normalized[clean_url] = _failure_record(clean_url, reason=str(metadata or "extract_failed"), terminal=False)
    return normalized


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


def _existing_failures(bookmark: dict) -> dict[str, dict]:
    return _normalize_failure_map(repair_value(bookmark.get("extract_failures")))


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


def _extract_youtube_metadata(url: str) -> dict | None:
    if _domain(url) not in {"youtube.com", "youtu.be"}:
        return None
    oembed_url = f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"
    request = Request(oembed_url, headers=_request_headers(accept="application/json,text/plain;q=0.9,*/*;q=0.8"))
    try:
        with urlopen(request, timeout=FALLBACK_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8", errors="ignore"))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None
    title = str(data.get("title", "")).strip()
    author = str(data.get("author_name", "")).strip()
    description = f"by {author}" if author else ""
    content = " ".join(part for part in (title, author) if part)
    page = {
        "url": url,
        "title": title,
        "description": description,
        "content": content,
        "preview": _preview_text(content or title or description),
        "word_count": len(content.split()),
        "site_name": "YouTube",
    }
    return page if _has_link_payload(page) else None


def _fallback_extract_url(url: str) -> dict | None:
    request = Request(
        url,
        headers=_request_headers(),
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
        "url": url,
        "title": parser.title,
        "description": parser.description,
        "content": parser.text,
        "preview": _preview_text(parser.text or parser.description or parser.title),
        "word_count": len(parser.text.split()),
        "site_name": parser.site_name or urlparse(final_url).netloc.replace("www.", ""),
    }
    if final_url != url:
        page["resolved_url"] = final_url
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


def _extract_url_with_status(url: str) -> tuple[dict | None, dict | None]:
    normalized_url, target_url = _prepare_target_url(url)

    if _domain(target_url) in TERMINAL_DOMAINS:
        return None, _failure_record(
            url,
            reason="unsupported_api_endpoint",
            terminal=True,
            normalized_url=normalized_url,
            resolved_url=target_url,
        )

    youtube = _extract_youtube_metadata(target_url)
    if youtube:
        if target_url != url:
            youtube["resolved_url"] = target_url
        youtube["url"] = url
        return youtube, None

    summarized: dict | None = None
    summarize_timed_out = False
    try:
        result = subprocess.run(
            [
                "summarize", "--extract", "--json", "--plain",
                "--max-extract-characters", str(MAX_EXTRACT_CHARS),
                target_url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            summarized = _normalize_summarize_payload(target_url, data := json.loads(result.stdout).get("extracted", {}))
            if summarized and target_url != url:
                summarized["resolved_url"] = target_url
                summarized["url"] = url
            if summarized and (str(summarized.get("content", "")).strip() or str(summarized.get("description", "")).strip()):
                return summarized, None
            if data.get("title") or data.get("description"):
                summarized = summarized or _normalize_summarize_payload(target_url, data)
                if summarized and target_url != url:
                    summarized["resolved_url"] = target_url
                    summarized["url"] = url
        else:
            summarized = None
    except subprocess.TimeoutExpired as error:
        print(f"  FAIL: {url[:60]} — {error}", file=sys.stderr)
        summarize_timed_out = True
    except (json.JSONDecodeError, Exception) as error:
        print(f"  FAIL: {url[:60]} — {error}", file=sys.stderr)

    fallback = _fallback_extract_url(target_url)
    if fallback and target_url != url:
        fallback["resolved_url"] = fallback.get("resolved_url") or target_url
        fallback["url"] = url
    merged = _merge_extracted(summarized, fallback)
    if merged:
        return merged, None

    if summarized:
        return summarized, None

    domain = _domain(target_url)
    reason = "extract_failed"
    terminal = True
    if summarize_timed_out:
        reason = "summarize_timeout"
        terminal = False
    elif domain in SHORTLINK_DOMAINS:
        reason = "shortlink_unresolved"
    elif target_url.endswith("/llms.txt"):
        reason = "llmstxt_unavailable"
    elif domain in {"youtube.com", "youtu.be"}:
        reason = "youtube_metadata_unavailable"
    elif domain in {"drive.google.com"}:
        reason = "drive_file_unavailable"
    return None, _failure_record(
        url,
        reason=reason,
        terminal=terminal,
        normalized_url=normalized_url,
        resolved_url=target_url,
    )


def extract_url(url: str) -> dict | None:
    """Run summarize --extract --json on a single URL. Returns extracted dict or None."""
    extracted, _failure = _extract_url_with_status(url)
    return extracted


def run_extraction(
    *,
    force: bool = False,
    limit: int | None = None,
    bookmark_id: str | None = None,
    bookmark_ids: set[str] | None = None,
    retry_urls: dict[str, set[str]] | None = None,
) -> dict:
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
            failures = _existing_failures(bm)
            if pages or failures:
                enriched_map[bm["id"]] = {}
                if pages:
                    enriched_map[bm["id"]]["linked_pages"] = pages
                    enriched_map[bm["id"]]["extracted"] = _primary_extracted(pages[0])
                if failures:
                    enriched_map[bm["id"]]["extract_failures"] = failures
        if not force and not bookmark_id:
            print(f"Resuming: {len(enriched_map)} already extracted")

    # Collect URLs to extract
    todo = []
    skipped_terminal = 0
    selected_ids = {str(value) for value in (bookmark_ids or set())}
    if bookmark_id:
        selected_ids.add(str(bookmark_id))
    retry_urls = {str(key): {str(url) for url in values} for key, values in (retry_urls or {}).items()}

    for bm in bookmarks:
        bm_id = str(bm["id"])
        if selected_ids and bm_id not in selected_ids:
            continue
        urls = [u for u in bm.get("urls", []) if should_extract(u)][:MAX_LINKS_PER_BOOKMARK]
        if retry_urls:
            targets = retry_urls.get(bm_id)
            if targets is None:
                continue
            urls = [url for url in urls if url in targets]
        if urls:
            existing = enriched_map.get(bm_id, {})
            existing_pages = existing.get("linked_pages", [])
            existing_failures = existing.get("extract_failures", {})
            covered = {page.get("url") for page in existing_pages}
            pending_urls = []
            for url in urls:
                if not force and url in covered:
                    continue
                if not force and not retry_urls and existing_failures.get(url, {}).get("terminal"):
                    skipped_terminal += 1
                    continue
                pending_urls.append(url)
            if force or pending_urls:
                todo.append((bm_id, pending_urls if pending_urls else urls))

    if limit is not None:
        todo = todo[:limit]

    print(f"Extracting content from {len(todo)} bookmarks ({sum(len(u) for _, u in todo)} URLs)...")
    if skipped_terminal and not force:
        print(f"Skipping {skipped_terminal} terminal failures cached from prior runs")

    extracted_count = 0
    extracted_pages = 0
    failed_count = 0

    for i, (bm_id, urls) in enumerate(todo):
        existing_pages = [] if force else list(enriched_map.get(bm_id, {}).get("linked_pages", []))
        failure_map = {} if force else dict(enriched_map.get(bm_id, {}).get("extract_failures", {}))
        covered = {page.get("url") for page in existing_pages}
        results = list(existing_pages)
        bookmark_had_success = False
        bookmark_had_failure = False
        allowed_retry_urls = retry_urls.get(bm_id, set()) if retry_urls else set()
        for url in urls:
            if not force and url in covered:
                continue
            if not force and failure_map.get(url, {}).get("terminal") and url not in allowed_retry_urls:
                continue
            print(f"  [{i+1}/{len(todo)}] {url[:70]}...", end=" ", flush=True)
            result, failure = _extract_url_with_status(url)
            if result:
                print(f"OK ({result['word_count']}w)")
                results.append(_normalize_page(result))
                covered.add(url)
                bookmark_had_success = True
                extracted_pages += 1
                failure_map.pop(url, None)
            else:
                print("skip")
                bookmark_had_failure = True
                failure_map[url] = failure or _failure_record(url, reason="extract_failed", terminal=False, previous=failure_map.get(url))

        results = _dedupe_pages(results)
        if results or failure_map:
            enriched_map[bm_id] = {}
            if results:
                enriched_map[bm_id]["linked_pages"] = results
                enriched_map[bm_id]["extracted"] = _primary_extracted(results[0])
            if failure_map:
                enriched_map[bm_id]["extract_failures"] = failure_map
        if bookmark_had_success:
            extracted_count += 1
        elif bookmark_had_failure and not results:
            failed_count += 1

        # Save progress every 20 bookmarks
        if (i + 1) % 20 == 0:
            _save_enriched(data, bookmarks, enriched_map, enriched_file)

        # Rate limit
        time.sleep(0.5)

    _save_enriched(data, bookmarks, enriched_map, enriched_file)
    summary = {
        "updated_bookmarks": extracted_count,
        "extracted_pages": extracted_pages,
        "failed_bookmarks": failed_count,
        "total_enriched": len(enriched_map),
        "matched_bookmarks": len(todo),
        "matched_urls": sum(len(urls) for _, urls in todo),
    }
    print(
        f"\nDone: {extracted_count} bookmarks updated, {extracted_pages} pages extracted, "
        f"{failed_count} failed, {len(enriched_map)} total enriched"
    )
    return summary


def _save_enriched(data: dict, bookmarks: list, enriched_map: dict, output_file):
    enriched_bookmarks = []
    for bm in bookmarks:
        enriched = {**bm}
        enriched.pop("extracted", None)
        enriched.pop("linked_pages", None)
        enriched.pop("extract_failures", None)
        if bm["id"] in enriched_map:
            if enriched_map[bm["id"]].get("extracted"):
                enriched["extracted"] = enriched_map[bm["id"]]["extracted"]
            if enriched_map[bm["id"]].get("linked_pages"):
                enriched["linked_pages"] = enriched_map[bm["id"]]["linked_pages"]
            if enriched_map[bm["id"]].get("extract_failures"):
                enriched["extract_failures"] = enriched_map[bm["id"]]["extract_failures"]
        enriched_bookmarks.append(enriched)

    output = {**data, "bookmarks": enriched_bookmarks}
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def list_extract_failures(
    *,
    bookmark_id: str | None = None,
    domain: str | None = None,
    terminal_only: bool = False,
    limit: int | None = None,
) -> list[dict]:
    base_dir = resolve_base_dir()
    enriched_file = base_dir / "enriched.json"
    if not enriched_file.exists():
        return []

    with enriched_file.open(encoding="utf-8") as handle:
        payload = repair_value(json.load(handle))

    rows: list[dict] = []
    for bookmark in payload.get("bookmarks", []):
        bm_id = str(bookmark.get("id", ""))
        if bookmark_id and bm_id != str(bookmark_id):
            continue
        for url, metadata in _normalize_failure_map(bookmark.get("extract_failures")).items():
            effective_url = str(metadata.get("resolved_url") or metadata.get("normalized_url") or url)
            failure_domain = _domain(effective_url or url)
            if domain and failure_domain != str(domain).casefold():
                continue
            if terminal_only and not metadata.get("terminal"):
                continue
            rows.append(
                {
                    "bookmark_id": bm_id,
                    "author": str(bookmark.get("author", "")),
                    "handle": str(bookmark.get("handle", "")),
                    "url": url,
                    "domain": failure_domain,
                    "reason": str(metadata.get("reason", "extract_failed")),
                    "terminal": bool(metadata.get("terminal")),
                    "attempts": int(metadata.get("attempts", 1) or 1),
                    "failed_at": str(metadata.get("failed_at", "")),
                    "normalized_url": str(metadata.get("normalized_url", "")),
                    "resolved_url": str(metadata.get("resolved_url", "")),
                }
            )
    rows.sort(key=lambda item: (item["failed_at"], item["bookmark_id"], item["url"]), reverse=True)
    return rows[:limit] if limit is not None else rows


def retry_extract_failures(
    *,
    bookmark_id: str | None = None,
    domain: str | None = None,
    include_terminal: bool = False,
    limit: int | None = None,
) -> dict:
    failures = list_extract_failures(bookmark_id=bookmark_id, domain=domain, terminal_only=False, limit=limit)
    selected = [item for item in failures if include_terminal or not item["terminal"]]
    if not selected:
        base_dir = resolve_base_dir()
        enriched_count = 0
        enriched_file = base_dir / "enriched.json"
        if enriched_file.exists():
            with enriched_file.open(encoding="utf-8") as handle:
                payload = repair_value(json.load(handle))
            enriched_count = sum(
                1
                for bookmark in payload.get("bookmarks", [])
                if bookmark.get("linked_pages") or bookmark.get("extracted") or bookmark.get("extract_failures")
            )
        return {
            "matched_bookmarks": 0,
            "matched_urls": 0,
            "updated_bookmarks": 0,
            "extracted_pages": 0,
            "failed_bookmarks": 0,
            "total_enriched": enriched_count,
        }

    retry_map: dict[str, set[str]] = {}
    for item in selected:
        retry_map.setdefault(item["bookmark_id"], set()).add(item["url"])

    result = run_extraction(
        force=False,
        bookmark_ids=set(retry_map),
        retry_urls=retry_map,
    )
    result["matched_bookmarks"] = len(retry_map)
    result["matched_urls"] = sum(len(urls) for urls in retry_map.values())
    return result
