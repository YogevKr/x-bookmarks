# CLI

`x-bookmarks` now has a persistent local index in `.x-bookmarks/`.

Behavior:
- `bookmarks.json` is the source of truth.
- `enriched.json` and `categorized.json` are derived files and are pruned against `bookmarks.json`.
- New or removed bookmarks are reconciled on `sync`.
- Query commands auto-refresh a stale index unless you pass `--no-refresh`.

## Sync

```bash
uv run x-bookmarks sync --input /path/to/bookmarks-export.json
cat bookmarks-export.json | uv run x-bookmarks sync --stdin
uv run x-bookmarks sync --reconcile-only
```

Notes:
- `sync` updates `bookmarks.json`, prunes removed bookmarks from derived files, and refreshes the persistent index.
- `sync --extract`, `sync --categorize`, and `sync --regex` can continue the pipeline after import.

## Index lifecycle

```bash
uv run x-bookmarks status
uv run x-bookmarks refresh
uv run x-bookmarks refresh --force
```

## Query commands

```bash
uv run x-bookmarks search "observability"
uv run x-bookmarks search "ai" --group-by category
uv run x-bookmarks list --author @simonw --category "AI & Machine Learning"
uv run x-bookmarks show 2037620876179537989
uv run x-bookmarks context 2037620876179537989
uv run x-bookmarks stats
uv run x-bookmarks domains
uv run x-bookmarks viz
```

Notes:
- `search` uses hybrid retrieval: SQLite FTS5 BM25 + local vector similarity fused with RRF.
- `search`, `list`, `show`, `context`, `stats`, `domains`, and `status` support `--json`.
- `search` supports `--group-by category|author|domain|year`.
- `list` supports `--author`, `--category`, `--domain`, `--language`, `--type`, `--after`, and `--before`.

## Agent access

```bash
uv run x-bookmarks mcp
uv run x-bookmarks serve --port 4111
```

Notes:
- `mcp` exposes bookmark search/context/status tools over stdio.
- `serve` exposes local JSON endpoints such as `/status`, `/search`, `/show`, and `/context`.

## Categorization

```bash
uv run x-bookmarks categorize
uv run x-bookmarks categorize --regex
```

Notes:
- `categorize` uses Claude Haiku.
- `categorize --regex` is the cheap deterministic fallback.
- Both modes write the same `categorized.json` shape consumed by search and agent-facing tools.
