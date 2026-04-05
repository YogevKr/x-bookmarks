# Historical: X Bookmarks → Obsidian Knowledge Base

> Historical design artifact from March 29, 2026.
> Superseded by the current CLI/index workflow documented in [../cli.md](../cli.md).
> The Obsidian export described below is no longer part of the active product surface.

## Status

Current direction:
- `bookmarks.json` source of truth
- `sync` reconciles derived files and deletions
- persistent local index in `.x-bookmarks/`
- shell, MCP, and local HTTP access for agents
- no Obsidian generation step

## Goal

Turn 2,007 exported X/Twitter bookmarks into an organized, searchable Obsidian vault with extracted content, AI categorization, and rich interlinking.

## Pipeline

```
bookmarks.json (2007 tweets)
    ↓
Phase 1: Content Extraction (summarize --extract, free)
    → Pull article text, GitHub READMEs, YouTube transcripts from 474 external URLs
    → Store in enriched.json alongside original bookmark data
    ↓
Phase 2: AI Categorization (Claude Haiku, batched)
    → Input: tweet text + extracted content
    → Output per bookmark: 1-3 categories, entities, one-line summary
    → Store in categorized.json
    ↓
Phase 3: Obsidian Generation
    → 2007 individual bookmark notes with frontmatter
    → Category MOC pages
    → Top author MOC pages
    → Dashboard index
```

## Phase 1: Content Extraction

**Tool:** `summarize --extract --format md --max-extract-characters 5000`

**Scope:**
- 474 external URLs (skip x.com self-references, those are just quoted tweets)
- ~360 extractable URLs after filtering x.com links
- GitHub repos: extract README
- YouTube/video: extract transcript
- Articles/blogs: extract main content
- Skip: images, audio-only, broken links

**Output:** `enriched.json` — same as bookmarks.json but with `extractedContent` field per bookmark.

**Rate limiting:** 2-3 concurrent extractions, 1s delay between batches. Expect ~15-20 min for all URLs.

**Error handling:** Failed extractions logged but don't block pipeline. Bookmark still gets categorized from tweet text alone.

## Phase 2: AI Categorization

**Model:** Claude Haiku (claude-haiku-4-5-20251001) — fast, cheap, good enough for categorization.

**Batch size:** 10 bookmarks per request (tweet text + truncated extracted content, max 1000 chars each).

**Prompt:** Given these bookmarks, for each return:
- `categories`: 1-3 topic categories (auto-discovered, not predefined)
- `entities`: key people, tools, companies mentioned
- `summary`: one-line summary
- `language`: detected language (en/he/other)

**Output:** `categorized.json` — enriched.json + AI fields.

**Cost estimate:** ~200 API calls × ~2K tokens each = ~400K tokens. Haiku pricing: ~$0.10-0.20 total.

**Idempotency:** Track processed bookmark IDs. Re-running skips already-categorized bookmarks.

## Phase 3: Obsidian Vault Generation

**Target:** `My brain/Captures/x-bookmarks/`

### File structure

```
x-bookmarks/
  _index.md                    # Dashboard: stats, category breakdown, top authors
  bookmarks/
    <tweet-id>.md              # 2007 individual notes
  categories/
    AI & Machine Learning.md   # MOC with all bookmarks in category
    DevTools & Infrastructure.md
    ...
  authors/
    @karpathy.md               # MOC for authors with 5+ bookmarks
    @simonw.md
    ...
```

### Bookmark note template

```markdown
---
type: x-bookmark
tweet_id: "123456789"
author: "@karpathy"
author_name: "Andrej Karpathy"
date: 2025-03-15
categories:
  - AI & Machine Learning
  - Deep Learning
entities:
  - GPT-4
  - transformer
language: en
tweet_url: https://x.com/karpathy/status/123456789
source_urls:
  - https://example.com/article
has_media: true
---

# One-line AI summary

> Original tweet text here, preserved verbatim.

## Extracted Content

Summary/excerpt of the linked article or content.

## Media

![](https://pbs.twimg.com/media/xxx.jpg)

## Links

- [Article Title](https://example.com/article)

---
Related: [[AI & Machine Learning]] | [[@karpathy]]
```

### Category MOC template

```markdown
---
type: x-bookmark-category
count: 142
---

# AI & Machine Learning

142 bookmarks in this category.

## Recent
- [[bookmark-id-1]] — summary (@author, date)
- [[bookmark-id-2]] — summary (@author, date)
...

## Top Authors in Category
- @karpathy (12)
- @simonw (8)
...
```

### Author MOC template

```markdown
---
type: x-bookmark-author
handle: "@karpathy"
bookmark_count: 29
---

# @karpathy

29 bookmarked tweets.

## By Category
### AI & Machine Learning
- [[bookmark-id]] — summary (date)
...
```

## Implementation

**Language:** Python (uv)

**Single script:** `generate.py` with subcommands:
- `extract` — Phase 1: content extraction → enriched.json
- `categorize` — Phase 2: AI categorization → categorized.json
- `generate` — Phase 3: write Obsidian markdown files
- `all` — run full pipeline

**Dependencies:** `anthropic`, `subprocess` (for summarize CLI), `json`, `pathlib`

**Resume/idempotent:** Each phase reads previous output and skips already-processed items. Safe to ctrl-C and re-run.

## Config

```python
BOOKMARKS_FILE = "bookmarks.json"
OBSIDIAN_VAULT = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/My brain"
OBSIDIAN_FOLDER = "Captures/x-bookmarks"
MIN_AUTHOR_BOOKMARKS = 5  # Only create author MOCs for authors with 5+ bookmarks
MAX_EXTRACT_CHARS = 5000
CATEGORIZE_BATCH_SIZE = 10
CATEGORIZE_MODEL = "claude-haiku-4-5-20251001"
```
