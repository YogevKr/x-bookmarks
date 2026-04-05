# x-bookmarks

Shell-first X bookmarks archive for local search, extraction, sync, and agent access.

`x-bookmarks` keeps your raw export local, enriches linked pages, builds a persistent search index, and exposes the corpus through CLI, HTTP, and MCP surfaces.

## Install

Prereqs:
- Python `3.14+`
- `uv`

Install from the repo:

```bash
uv tool install --from . x-bookmarks
```

Install from GitHub:

```bash
uv tool install --from git+https://github.com/YogevKr/x-bookmarks x-bookmarks
```

Run without installing:

```bash
uv run x-bookmarks --help
```

## Quickstart

Import an export and build local state:

```bash
uv run x-bookmarks sync --input /path/to/bookmarks-export.json
```

Search:

```bash
uv run x-bookmarks search "observability"
uv run x-bookmarks search "agent engineering" --explain
```

Inspect one bookmark:

```bash
uv run x-bookmarks show 2037620876179537989
uv run x-bookmarks context 2037620876179537989
```

Agent surfaces:

```bash
uv run x-bookmarks mcp
uv run x-bookmarks serve --port 4111
```

## Data model

- `bookmarks.json`: source of truth
- `enriched.json`: linked-page extracts and extraction failures
- `categorized.json`: AI or regex categorization
- `.x-bookmarks/`: local index and sync state

All corpus data is intentionally local-only and ignored by git.

## Features

- persistent SQLite FTS index with hybrid ranking
- full-text search, filters, stats, domains, and terminal viz
- link extraction with metadata fallback and cached terminal failures
- local delete/restore, notes, tags, ratings, and hidden state
- stdio MCP server and local HTTP API

## CLI docs

See [docs/cli.md](docs/cli.md).

## Publishing notes

- Homebrew formula: not added yet
- License: MIT
