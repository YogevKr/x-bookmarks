# Historical: X Bookmarks → Obsidian Implementation Plan

> Historical planning artifact from March 29, 2026.
> Superseded by the current CLI/index workflow documented in [../cli.md](../cli.md).
> The Obsidian export described below was removed from the active codebase.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn 2,007 exported X bookmarks into an organized Obsidian vault with extracted content, AI categorization, and rich interlinking.

**Architecture:** Single Python script (`generate.py`) with three subcommands: `extract` (fetch URL content via `summarize` CLI), `categorize` (batch Claude Haiku calls), `generate` (write Obsidian markdown). Each phase outputs a JSON file that feeds the next. Idempotent — safe to re-run.

**Tech Stack:** Python 3 (uv), `anthropic` SDK, `summarize` CLI (already installed), `asyncio` for concurrent extraction.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `generate.py`

**Step 1: Initialize uv project**

```bash
cd /path/to/x-bookmarks
uv init --no-readme --python 3.14
```

**Step 2: Add dependencies**

```bash
uv add anthropic
```

**Step 3: Create generate.py with CLI skeleton**

```python
#!/usr/bin/env python3
"""X Bookmarks → Obsidian pipeline."""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
BOOKMARKS_FILE = BASE_DIR / "bookmarks.json"
ENRICHED_FILE = BASE_DIR / "enriched.json"
CATEGORIZED_FILE = BASE_DIR / "categorized.json"

OBSIDIAN_VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/My brain"
OBSIDIAN_FOLDER = "Captures/x-bookmarks"

MIN_AUTHOR_BOOKMARKS = 5
MAX_EXTRACT_CHARS = 5000
CATEGORIZE_BATCH_SIZE = 10
CATEGORIZE_MODEL = "claude-haiku-4-5-20251001"


def load_bookmarks() -> dict:
    with open(BOOKMARKS_FILE) as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {path} ({path.stat().st_size // 1024}KB)")


def cmd_extract(args):
    print("Phase 1: Content extraction")
    from extract import run_extraction
    run_extraction()


def cmd_categorize(args):
    print("Phase 2: AI categorization")
    from categorize import run_categorization
    run_categorization()


def cmd_generate(args):
    print("Phase 3: Obsidian generation")
    from obsidian import run_generation
    run_generation()


def cmd_all(args):
    cmd_extract(args)
    cmd_categorize(args)
    cmd_generate(args)


def main():
    parser = argparse.ArgumentParser(description="X Bookmarks → Obsidian pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("extract", help="Phase 1: Extract content from URLs")
    sub.add_parser("categorize", help="Phase 2: AI categorization")
    sub.add_parser("generate", help="Phase 3: Generate Obsidian vault")
    sub.add_parser("all", help="Run full pipeline")

    args = parser.parse_args()
    {"extract": cmd_extract, "categorize": cmd_categorize, "generate": cmd_generate, "all": cmd_all}[args.command](args)


if __name__ == "__main__":
    main()
```

**Step 4: Verify skeleton runs**

```bash
uv run python generate.py --help
uv run python generate.py extract  # should print "Phase 1" then fail on missing import
```

**Step 5: Commit**

```bash
git init
git add pyproject.toml uv.lock generate.py bookmarks.json docs/
git commit -m "feat: project scaffolding with CLI skeleton"
```

---

### Task 2: Content extraction (extract.py)

**Files:**
- Create: `extract.py`

**Step 1: Create extract.py**

```python
"""Phase 1: Extract content from external URLs using summarize CLI."""

import json
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
BOOKMARKS_FILE = BASE_DIR / "bookmarks.json"
ENRICHED_FILE = BASE_DIR / "enriched.json"
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
    with open(BOOKMARKS_FILE) as f:
        data = json.load(f)

    bookmarks = data["bookmarks"]

    # Load existing enriched data for resume
    enriched_map = {}
    if ENRICHED_FILE.exists():
        with open(ENRICHED_FILE) as f:
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
            _save_enriched(data, bookmarks, enriched_map)

        # Rate limit
        time.sleep(0.5)

    _save_enriched(data, bookmarks, enriched_map)
    print(f"\nDone: {extracted_count} extracted, {failed_count} failed, {len(enriched_map)} total enriched")


def _save_enriched(data: dict, bookmarks: list, enriched_map: dict):
    enriched_bookmarks = []
    for bm in bookmarks:
        enriched = {**bm}
        if bm["id"] in enriched_map:
            enriched["extracted"] = enriched_map[bm["id"]]
        enriched_bookmarks.append(enriched)

    output = {**data, "bookmarks": enriched_bookmarks}
    with open(ENRICHED_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
```

**Step 2: Test extraction on a few URLs**

```bash
uv run python -c "
from extract import extract_url
r = extract_url('https://github.com/anthropics/claude-code')
print(r['title'] if r else 'FAIL')
print(len(r['content']) if r else 0, 'chars')
"
```

Expected: title printed, content > 0 chars.

**Step 3: Run full extraction**

```bash
uv run python generate.py extract
```

Expected: processes ~360 URLs, saves enriched.json. Takes ~15-20 min. Safe to ctrl-C and resume.

**Step 4: Verify enriched.json**

```bash
uv run python -c "
import json
with open('enriched.json') as f:
    d = json.load(f)
enriched = sum(1 for b in d['bookmarks'] if b.get('extracted'))
print(f'{enriched}/{len(d[\"bookmarks\"])} enriched')
"
```

**Step 5: Commit**

```bash
git add extract.py
git commit -m "feat: phase 1 content extraction via summarize CLI"
```

---

### Task 3: AI categorization (categorize.py)

**Files:**
- Create: `categorize.py`

**Step 1: Create categorize.py**

```python
"""Phase 2: AI categorization via Claude Haiku."""

import json
import sys
import time
from pathlib import Path

import anthropic

BASE_DIR = Path(__file__).parent
ENRICHED_FILE = BASE_DIR / "enriched.json"
BOOKMARKS_FILE = BASE_DIR / "bookmarks.json"
CATEGORIZED_FILE = BASE_DIR / "categorized.json"

BATCH_SIZE = 10
MODEL = "claude-haiku-4-5-20251001"
MAX_CONTENT_PER_BOOKMARK = 800

SYSTEM_PROMPT = """You categorize Twitter/X bookmarks. For each bookmark, return:
- categories: 1-3 topic categories (use consistent, broad names like "AI & Machine Learning", "Software Engineering", "Startups & Business", "DevTools", "Personal Finance", "Hebrew Content", "Humor & Memes", etc.)
- entities: key people, tools, companies, or concepts mentioned (max 5)
- summary: one-line summary (max 100 chars)
- language: "en", "he", or ISO code

Return valid JSON array matching the input order. No markdown fences."""


def build_bookmark_text(bm: dict) -> str:
    """Build input text for a single bookmark."""
    parts = [f"@{bm['handle']}: {bm['text'][:500]}"]
    ext = bm.get("extracted", {})
    if ext:
        content = ext.get("content", "")
        title = ext.get("title", "")
        if title:
            parts.append(f"Linked: {title}")
        if content:
            parts.append(content[:MAX_CONTENT_PER_BOOKMARK])
    if bm.get("urls"):
        parts.append("URLs: " + ", ".join(bm["urls"][:3]))
    return "\n".join(parts)


def categorize_batch(client: anthropic.Anthropic, batch: list[dict]) -> list[dict]:
    """Send a batch of bookmarks to Claude for categorization."""
    items = []
    for i, bm in enumerate(batch):
        items.append(f"[{i}] {build_bookmark_text(bm)}")

    user_msg = f"Categorize these {len(batch)} bookmarks:\n\n" + "\n\n---\n\n".join(items)

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    results = json.loads(text)
    if len(results) != len(batch):
        print(f"  WARNING: expected {len(batch)} results, got {len(results)}", file=sys.stderr)
    return results


def run_categorization():
    # Load input
    input_file = ENRICHED_FILE if ENRICHED_FILE.exists() else BOOKMARKS_FILE
    print(f"Reading from {input_file.name}")
    with open(input_file) as f:
        data = json.load(f)

    bookmarks = data["bookmarks"]

    # Load existing categorized data for resume
    cat_map = {}
    if CATEGORIZED_FILE.exists():
        with open(CATEGORIZED_FILE) as f:
            existing = json.load(f)
        for bm in existing["bookmarks"]:
            if bm.get("ai"):
                cat_map[bm["id"]] = bm["ai"]
        print(f"Resuming: {len(cat_map)} already categorized")

    todo = [bm for bm in bookmarks if bm["id"] not in cat_map]
    print(f"Categorizing {len(todo)} bookmarks in batches of {BATCH_SIZE}...")

    client = anthropic.Anthropic()

    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches}...", end=" ", flush=True)

        try:
            results = categorize_batch(client, batch)
            for bm, result in zip(batch, results):
                cat_map[bm["id"]] = {
                    "categories": result.get("categories", []),
                    "entities": result.get("entities", []),
                    "summary": result.get("summary", ""),
                    "language": result.get("language", "en"),
                }
            print(f"OK ({len(results)} categorized)")
        except Exception as e:
            print(f"FAIL: {e}", file=sys.stderr)
            # Save progress and continue
            time.sleep(2)

        # Save progress every 5 batches
        if batch_num % 5 == 0:
            _save_categorized(data, bookmarks, cat_map)

        time.sleep(0.3)

    _save_categorized(data, bookmarks, cat_map)
    print(f"\nDone: {len(cat_map)}/{len(bookmarks)} categorized")

    # Print category stats
    from collections import Counter
    cats = Counter()
    for ai in cat_map.values():
        for c in ai.get("categories", []):
            cats[c] += 1
    print("\nTop categories:")
    for cat, count in cats.most_common(15):
        print(f"  {cat}: {count}")


def _save_categorized(data: dict, bookmarks: list, cat_map: dict):
    cat_bookmarks = []
    for bm in bookmarks:
        out = {**bm}
        if bm["id"] in cat_map:
            out["ai"] = cat_map[bm["id"]]
        cat_bookmarks.append(out)

    output = {**data, "bookmarks": cat_bookmarks}
    with open(CATEGORIZED_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
```

**Step 2: Test on a small batch**

```bash
uv run python -c "
from categorize import categorize_batch, build_bookmark_text
import anthropic, json
client = anthropic.Anthropic()
with open('bookmarks.json') as f:
    bms = json.load(f)['bookmarks'][:3]
results = categorize_batch(client, bms)
print(json.dumps(results, indent=2))
"
```

Expected: 3 categorization results with categories, entities, summary, language.

**Step 3: Run full categorization**

```bash
uv run python generate.py categorize
```

Expected: ~200 batches, prints progress and category stats. Takes ~5-10 min. Safe to ctrl-C and resume.

**Step 4: Commit**

```bash
git add categorize.py
git commit -m "feat: phase 2 AI categorization via Claude Haiku"
```

---

### Task 4: Obsidian vault generation (obsidian.py)

**Files:**
- Create: `obsidian.py`

**Step 1: Create obsidian.py**

```python
"""Phase 3: Generate Obsidian markdown vault from categorized bookmarks."""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
CATEGORIZED_FILE = BASE_DIR / "categorized.json"
ENRICHED_FILE = BASE_DIR / "enriched.json"
BOOKMARKS_FILE = BASE_DIR / "bookmarks.json"

OBSIDIAN_VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/My brain"
OBSIDIAN_FOLDER = "Captures/x-bookmarks"

MIN_AUTHOR_BOOKMARKS = 5


def safe_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    # Remove/replace characters that are problematic in filenames and Obsidian
    cleaned = re.sub(r'[<>:"/\\|?*#^\[\]]', '', name)
    cleaned = cleaned.strip(". ")
    return cleaned[:100] if cleaned else "unnamed"


def parse_timestamp(ts: str) -> str | None:
    """Parse X timestamp to YYYY-MM-DD."""
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def tweet_url(handle: str, tweet_id: str) -> str:
    h = handle.lstrip("@")
    return f"https://x.com/{h}/status/{tweet_id}"


def generate_bookmark_note(bm: dict) -> str:
    """Generate markdown for a single bookmark note."""
    ai = bm.get("ai", {})
    date = parse_timestamp(bm.get("timestamp", ""))
    categories = ai.get("categories", [])
    entities = ai.get("entities", [])
    summary = ai.get("summary", "")
    language = ai.get("language", "en")
    url = tweet_url(bm["handle"], bm["id"])
    source_urls = bm.get("urls", [])

    # Frontmatter
    lines = ["---"]
    lines.append("type: x-bookmark")
    lines.append(f'tweet_id: "{bm["id"]}"')
    lines.append(f'author: "{bm["handle"]}"')
    lines.append(f'author_name: "{bm["author"]}"')
    if date:
        lines.append(f"date: {date}")
    if categories:
        lines.append("categories:")
        for c in categories:
            lines.append(f'  - "{c}"')
    if entities:
        lines.append("entities:")
        for e in entities:
            lines.append(f'  - "{e}"')
    lines.append(f"language: {language}")
    lines.append(f"tweet_url: {url}")
    if source_urls:
        lines.append("source_urls:")
        for u in source_urls:
            lines.append(f"  - {u}")
    lines.append(f"has_media: {bool(bm.get('media'))}")
    lines.append("---")
    lines.append("")

    # Summary
    if summary:
        lines.append(f"# {summary}")
    else:
        lines.append(f"# Bookmark from {bm['handle']}")
    lines.append("")

    # Tweet text
    lines.append(f"> {bm['text']}")
    lines.append("")

    # Extracted content
    ext = bm.get("extracted", {})
    if ext and ext.get("content"):
        title = ext.get("title", "Linked Content")
        lines.append(f"## {title}")
        lines.append("")
        # Truncate for Obsidian readability
        content = ext["content"][:2000]
        lines.append(content)
        lines.append("")

    # Media
    media = bm.get("media", [])
    if media:
        lines.append("## Media")
        lines.append("")
        for m in media:
            if m["type"] == "photo":
                lines.append(f"![{m['type']}]({m['url']})")
            else:
                lines.append(f"[{m['type']}]({m['url']})")
        lines.append("")

    # Links
    if source_urls:
        lines.append("## Links")
        lines.append("")
        for u in source_urls:
            lines.append(f"- [{u[:60]}]({u})")
        lines.append("")

    # Related
    related = []
    for c in categories:
        related.append(f"[[{safe_filename(c)}]]")
    related.append(f"[[{safe_filename(bm['handle'])}]]")
    lines.append("---")
    lines.append("Related: " + " | ".join(related))

    return "\n".join(lines)


def generate_category_moc(cat_name: str, bookmarks: list[dict]) -> str:
    """Generate a category MOC page."""
    lines = ["---"]
    lines.append("type: x-bookmark-category")
    lines.append(f"count: {len(bookmarks)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {cat_name}")
    lines.append("")
    lines.append(f"{len(bookmarks)} bookmarks in this category.")
    lines.append("")

    # Sort by date descending
    sorted_bms = sorted(bookmarks, key=lambda b: b.get("timestamp", ""), reverse=True)

    lines.append("## Bookmarks")
    lines.append("")
    for bm in sorted_bms:
        summary = bm.get("ai", {}).get("summary", bm["text"][:60])
        date = parse_timestamp(bm.get("timestamp", "")) or ""
        lines.append(f"- [[{bm['id']}]] — {summary} ({bm['handle']}, {date})")
    lines.append("")

    # Top authors in category
    authors = Counter(bm["handle"] for bm in bookmarks)
    if len(authors) > 1:
        lines.append("## Top Authors")
        lines.append("")
        for handle, count in authors.most_common(10):
            lines.append(f"- [[{safe_filename(handle)}]] ({count})")

    return "\n".join(lines)


def generate_author_moc(handle: str, bookmarks: list[dict]) -> str:
    """Generate an author MOC page."""
    display_name = bookmarks[0]["author"] if bookmarks else handle

    lines = ["---"]
    lines.append("type: x-bookmark-author")
    lines.append(f'handle: "{handle}"')
    lines.append(f"bookmark_count: {len(bookmarks)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {handle}")
    lines.append("")
    if display_name != handle:
        lines.append(f"**{display_name}**")
        lines.append("")
    lines.append(f"{len(bookmarks)} bookmarked tweets.")
    lines.append("")

    # Group by category
    by_cat = defaultdict(list)
    for bm in bookmarks:
        cats = bm.get("ai", {}).get("categories", ["Uncategorized"])
        for c in cats:
            by_cat[c].append(bm)

    for cat, cat_bms in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        lines.append(f"### {cat}")
        lines.append("")
        for bm in sorted(cat_bms, key=lambda b: b.get("timestamp", ""), reverse=True):
            summary = bm.get("ai", {}).get("summary", bm["text"][:60])
            date = parse_timestamp(bm.get("timestamp", "")) or ""
            lines.append(f"- [[{bm['id']}]] — {summary} ({date})")
        lines.append("")

    return "\n".join(lines)


def generate_index(bookmarks: list[dict], categories: dict, authors: dict) -> str:
    """Generate the dashboard index page."""
    lines = ["---"]
    lines.append("type: x-bookmark-index")
    lines.append("---")
    lines.append("")
    lines.append("# X Bookmarks")
    lines.append("")
    lines.append(f"**{len(bookmarks)}** bookmarks from **{len(authors)}** unique authors across **{len(categories)}** categories.")
    lines.append("")

    # Date range
    dates = [parse_timestamp(b.get("timestamp", "")) for b in bookmarks]
    dates = [d for d in dates if d]
    if dates:
        lines.append(f"Date range: {min(dates)} → {max(dates)}")
        lines.append("")

    # Categories
    lines.append("## Categories")
    lines.append("")
    for cat, bms in sorted(categories.items(), key=lambda x: -len(x[1])):
        lines.append(f"- [[{safe_filename(cat)}]] ({len(bms)})")
    lines.append("")

    # Top authors
    lines.append("## Top Authors")
    lines.append("")
    top_authors = sorted(authors.items(), key=lambda x: -len(x[1]))[:20]
    for handle, bms in top_authors:
        lines.append(f"- [[{safe_filename(handle)}]] ({len(bms)})")
    lines.append("")

    # Stats
    with_media = sum(1 for b in bookmarks if b.get("media"))
    with_urls = sum(1 for b in bookmarks if b.get("urls"))
    with_extracted = sum(1 for b in bookmarks if b.get("extracted"))
    lines.append("## Stats")
    lines.append("")
    lines.append(f"- With media: {with_media}")
    lines.append(f"- With external URLs: {with_urls}")
    lines.append(f"- With extracted content: {with_extracted}")

    return "\n".join(lines)


def run_generation():
    # Load best available data
    for candidate in [CATEGORIZED_FILE, ENRICHED_FILE, BOOKMARKS_FILE]:
        if candidate.exists():
            print(f"Reading from {candidate.name}")
            with open(candidate) as f:
                data = json.load(f)
            break
    else:
        print("ERROR: No input file found", file=sys.stderr)
        return

    bookmarks = data["bookmarks"]

    # Output directory
    out_dir = OBSIDIAN_VAULT / OBSIDIAN_FOLDER
    bm_dir = out_dir / "bookmarks"
    cat_dir = out_dir / "categories"
    author_dir = out_dir / "authors"

    for d in [bm_dir, cat_dir, author_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Build indexes
    by_category = defaultdict(list)
    by_author = defaultdict(list)

    for bm in bookmarks:
        by_author[bm["handle"]].append(bm)
        for cat in bm.get("ai", {}).get("categories", []):
            by_category[cat].append(bm)

    # Generate bookmark notes
    print(f"Writing {len(bookmarks)} bookmark notes...")
    for bm in bookmarks:
        note = generate_bookmark_note(bm)
        path = bm_dir / f"{bm['id']}.md"
        path.write_text(note, encoding="utf-8")

    # Generate category MOCs
    print(f"Writing {len(by_category)} category MOCs...")
    for cat, bms in by_category.items():
        note = generate_category_moc(cat, bms)
        path = cat_dir / f"{safe_filename(cat)}.md"
        path.write_text(note, encoding="utf-8")

    # Generate author MOCs (only for authors with enough bookmarks)
    qualifying_authors = {h: bms for h, bms in by_author.items() if len(bms) >= MIN_AUTHOR_BOOKMARKS}
    print(f"Writing {len(qualifying_authors)} author MOCs (min {MIN_AUTHOR_BOOKMARKS} bookmarks)...")
    for handle, bms in qualifying_authors.items():
        note = generate_author_moc(handle, bms)
        path = author_dir / f"{safe_filename(handle)}.md"
        path.write_text(note, encoding="utf-8")

    # Generate index
    index = generate_index(bookmarks, by_category, by_author)
    (out_dir / "_index.md").write_text(index, encoding="utf-8")

    print(f"\nDone! Vault written to {out_dir}")
    print(f"  {len(bookmarks)} bookmark notes")
    print(f"  {len(by_category)} category pages")
    print(f"  {len(qualifying_authors)} author pages")
    print(f"  1 index page")
```

**Step 2: Test generation with current data (no AI fields yet)**

```bash
uv run python generate.py generate
```

Expected: writes markdown files to `My brain/Captures/x-bookmarks/`. Open Obsidian and verify files appear.

**Step 3: Commit**

```bash
git add obsidian.py
git commit -m "feat: phase 3 Obsidian vault generation"
```

---

### Task 5: Run full pipeline

**Step 1: Run extraction**

```bash
uv run python generate.py extract
```

Wait for completion (~15-20 min). Monitor progress. Safe to ctrl-C and resume.

**Step 2: Verify enriched.json**

```bash
uv run python -c "
import json
with open('enriched.json') as f:
    d = json.load(f)
enriched = sum(1 for b in d['bookmarks'] if b.get('extracted'))
print(f'{enriched}/{len(d[\"bookmarks\"])} bookmarks have extracted content')
"
```

**Step 3: Run categorization**

```bash
uv run python generate.py categorize
```

Wait for completion (~5-10 min). Monitor progress and category stats.

**Step 4: Run vault generation**

```bash
uv run python generate.py generate
```

**Step 5: Open Obsidian and verify**

- Open "My brain" vault
- Navigate to Captures/x-bookmarks
- Check _index.md for dashboard
- Check graph view for connections
- Spot-check a few bookmark notes

**Step 6: Commit enriched data (optional)**

```bash
git add enriched.json categorized.json
git commit -m "feat: enriched and categorized bookmark data"
```

---

### Task 6: Polish and edge cases

**Step 1: Handle Unicode in filenames**

Some authors have emoji in names. Verify `safe_filename` handles them correctly. If not, add emoji stripping.

**Step 2: Handle missing AI fields gracefully**

Bookmarks where categorization failed should still generate valid Obsidian notes with "Uncategorized" category.

**Step 3: Verify Obsidian graph view**

Open graph view. Check that:
- Category nodes are hubs connecting related bookmarks
- Author nodes connect to their bookmarks
- No orphan nodes (every bookmark links to at least one category or author)

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete x-bookmarks to obsidian pipeline"
```
