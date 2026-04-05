"""Phase 1: Extract content from external URLs using summarize CLI."""

import json
import subprocess
import sys
import time

from bookmark_paths import resolve_base_dir

MAX_EXTRACT_CHARS = 5000

SKIP_DOMAINS = {"x.com", "twitter.com", "t.co"}


def should_extract(url: str) -> bool:
    """Skip x.com self-references and t.co shortlinks without expansion."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace("www.", "")
        return domain not in SKIP_DOMAINS
    except Exception:
        return False


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
            "title": ext.get("title", ""),
            "description": ext.get("description", ""),
            "content": content[:MAX_EXTRACT_CHARS],
            "word_count": ext.get("wordCount", 0),
            "site_name": ext.get("siteName", ""),
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"  FAIL: {url[:60]} — {e}", file=sys.stderr)
        return None


def run_extraction():
    base_dir = resolve_base_dir()
    bookmarks_file = base_dir / "bookmarks.json"
    enriched_file = base_dir / "enriched.json"

    with bookmarks_file.open(encoding="utf-8") as f:
        data = json.load(f)

    bookmarks = data["bookmarks"]

    # Load existing enriched data for resume
    enriched_map = {}
    if enriched_file.exists():
        with enriched_file.open(encoding="utf-8") as f:
            existing = json.load(f)
        for bm in existing["bookmarks"]:
            if bm.get("extracted"):
                enriched_map[bm["id"]] = bm["extracted"]
        print(f"Resuming: {len(enriched_map)} already extracted")

    # Collect URLs to extract
    todo = []
    for bm in bookmarks:
        if bm["id"] in enriched_map:
            continue
        urls = [u for u in bm.get("urls", []) if should_extract(u)]
        if urls:
            todo.append((bm["id"], urls))

    print(f"Extracting content from {len(todo)} bookmarks ({sum(len(u) for _, u in todo)} URLs)...")

    extracted_count = 0
    failed_count = 0

    for i, (bm_id, urls) in enumerate(todo):
        # Try each URL until one succeeds
        result = None
        for url in urls:
            print(f"  [{i+1}/{len(todo)}] {url[:70]}...", end=" ", flush=True)
            result = extract_url(url)
            if result:
                print(f"OK ({result['word_count']}w)")
                break
            else:
                print("skip")

        if result:
            enriched_map[bm_id] = result
            extracted_count += 1
        else:
            failed_count += 1

        # Save progress every 20 bookmarks
        if (i + 1) % 20 == 0:
            _save_enriched(data, bookmarks, enriched_map, enriched_file)

        # Rate limit
        time.sleep(0.5)

    _save_enriched(data, bookmarks, enriched_map, enriched_file)
    print(f"\nDone: {extracted_count} extracted, {failed_count} failed, {len(enriched_map)} total enriched")


def _save_enriched(data: dict, bookmarks: list, enriched_map: dict, output_file):
    enriched_bookmarks = []
    for bm in bookmarks:
        enriched = {**bm}
        if bm["id"] in enriched_map:
            enriched["extracted"] = enriched_map[bm["id"]]
        enriched_bookmarks.append(enriched)

    output = {**data, "bookmarks": enriched_bookmarks}
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
