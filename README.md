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

Optional AI categorization dependency:

```bash
uv sync --extra ai
```

Run without installing:

```bash
uv run x-bookmarks --help
```

## Quickstart

Initialize config once:

```bash
uv run x-bookmarks config init --writer --icloud
uv run x-bookmarks config show
```

Import an export and build local state:

```bash
uv run x-bookmarks sync --input /path/to/bookmarks-export.json
```

Search:

```bash
uv run x-bookmarks version
uv run x-bookmarks status
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
uv run x-bookmarks watch
uv run x-bookmarks launchd install
```

Portable local metadata:

```bash
uv run x-bookmarks metadata-export --output /tmp/x-bookmarks-metadata.json
uv run x-bookmarks metadata-import --input /tmp/x-bookmarks-metadata.json
```

## Shared iCloud workspace

One good setup:
- SSH Mac: single writer; runs `sync` and `watch`
- this Mac: query-only; reads the synced workspace from iCloud

Preferred config file on macOS:
- `~/Library/Application Support/x-bookmarks/config.json`
- other supported paths: `~/.config/x-bookmarks/config.json`, `~/.x-bookmarks/config.json`, `~/.x-bookmark/config.json`

Schema:

```json
{
  "base_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/x-bookmarks",
  "read_only": true
}
```

Resolution order:
- env: `X_BOOKMARKS_HOME`, `X_BOOKMARKS_READ_ONLY`
- config file
- current working directory

Writer machine:

```bash
uv run x-bookmarks config init --writer --icloud
x-bookmarks sync --input /path/to/bookmarks-export.json
x-bookmarks launchd install
```

Query machine:

```bash
uv run x-bookmarks config init --reader --icloud
x-bookmarks search "observability"
x-bookmarks context 2037620876179537989
```

Notes:
- keep one writer only; do not run `watch` or `sync` on both machines
- read-only mode disables query auto-refresh on the second machine
- read-only mode also blocks write commands like `sync`, `refresh`, `watch`, `extract`, and local metadata edits
- the index is checkpointed after rebuilds so iCloud sync has less SQLite WAL churn
- `status` shows the active binary path, install source, config path, and latest watch heartbeat

## Data model

- `bookmarks.json`: source of truth
- `enriched.json`: linked-page extracts and extraction failures
- `categorized.json`: AI or regex categorization
- `.x-bookmarks/`: local index and sync state

All corpus data is intentionally local-only and ignored by git.

## Features

- persistent SQLite FTS index with hybrid ranking
- full-text search, filters, stats, domains, and terminal viz
- `version`, richer `status`, and guided `config init`
- link extraction with metadata fallback and cached terminal failures
- extraction failure inspection and targeted retries
- local delete/restore, notes, tags, ratings, and hidden state
- metadata export/import for local notes, tags, ratings, and hidden state
- stdio MCP server and local HTTP API
- watch mode and macOS launch-at-login support via `launchd`
- optional Claude-based categorization via the `ai` extra

## CLI docs

See [docs/cli.md](docs/cli.md).

## Publishing notes

- Homebrew tap: `yogevkr/tap`
- Formula: `yogevkr/tap/x-bookmarks`
- License: MIT
