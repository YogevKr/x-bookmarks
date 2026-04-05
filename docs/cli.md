# CLI

`x-bookmarks` now has a persistent local index in `.x-bookmarks/`.

Behavior:
- `bookmarks.json` is the source of truth.
- `sync` bootstraps from `bookmarks.json`, then keeps local snapshots in `.x-bookmarks/sync-state.json`.
- After bootstrap, deletes from `bookmarks.json`, `enriched.json`, or `categorized.json` are treated as local deletions and propagated everywhere.
- Local removals are tombstoned, so a later import does not silently resurrect them.
- Query commands auto-refresh a stale index unless you pass `--no-refresh`.
- The CLI reads data from the current working directory by default.
- Set `X_BOOKMARKS_HOME=/path/to/data-dir` to target another bookmark workspace.

## Sync

```bash
uv run x-bookmarks extract --force --limit 10
uv run x-bookmarks extract --bookmark-id 2037620876179537989
uv run x-bookmarks sync --input /path/to/bookmarks-export.json
cat bookmarks-export.json | uv run x-bookmarks sync --stdin
uv run x-bookmarks sync --reconcile-only
uv run x-bookmarks remove 2037620876179537989
uv run x-bookmarks restore 2037620876179537989
uv run x-bookmarks restore --all
uv run x-bookmarks note 2037620876179537989 "revisit for collector tuning ideas"
uv run x-bookmarks tag 2037620876179537989 favorite otel
uv run x-bookmarks rate 2037620876179537989 5
uv run x-bookmarks hide 2037620876179537989
uv run x-bookmarks unhide 2037620876179537989
```

Notes:
- `extract` now stores richer per-link context in `linked_pages`, not just one primary extract.
- `extract --force` upgrades existing bookmarks to the richer schema.
- `extract --bookmark-id` lets you re-extract one bookmark without touching the whole corpus.
- The first `sync` bootstraps local state from `bookmarks.json`.
- Later `sync` runs detect file-side deletions in any local bookmark file, propagate them across all files, and refresh the persistent index.
- `sync --extract`, `sync --categorize`, and `sync --regex` can continue the pipeline after import.
- `remove` deletes locally across all synced files and records a tombstone.
- `restore` rehydrates a locally removed bookmark from the sync archive.
- `note`, `tag`, `untag`, `rate`, `hide`, and `unhide` update local metadata on active bookmarks.
- `status` reports tombstone/archive counts from sync state.

## Index lifecycle

```bash
uv run x-bookmarks status
uv run x-bookmarks doctor
uv run x-bookmarks refresh
uv run x-bookmarks refresh --force
```

## Query commands

```bash
uv run x-bookmarks search "observability"
uv run x-bookmarks search "observability" --explain
uv run x-bookmarks search "favorite" --hidden
uv run x-bookmarks search "ai" --group-by category
uv run x-bookmarks list --author @simonw --category "AI & Machine Learning"
uv run x-bookmarks list --deleted
uv run x-bookmarks list --hidden
uv run x-bookmarks show 2037620876179537989
uv run x-bookmarks show 2037620876179537989 --deleted
uv run x-bookmarks show 2037620876179537989 --fetch-link
uv run x-bookmarks context 2037620876179537989
uv run x-bookmarks context 2037620876179537989 --fetch-link
uv run x-bookmarks stats
uv run x-bookmarks domains
uv run x-bookmarks viz
```

Notes:
- `search` uses hybrid retrieval: SQLite FTS5 BM25 + local vector similarity fused with RRF.
- `search --explain` shows matched fields, matched linked pages, and the BM25/vector RRF components for each result.
- Local notes and tags are indexed and searchable.
- `search`, `list`, `show`, `context`, `stats`, `domains`, and `status` support `--json`.
- `search` supports `--group-by category|author|domain|year`.
- `list` supports `--author`, `--category`, `--domain`, `--language`, `--type`, `--after`, and `--before`.
- `list --deleted` shows locally removed bookmarks from the sync archive.
- `list --hidden` shows bookmarks hidden from normal search/list/stats.
- `show --deleted` shows one locally removed bookmark, including when and why it was deleted.
- Hidden bookmarks are excluded from normal `search`, `list`, `stats`, `domains`, and `viz` output by default.
- Terminal results now include tweet previews and linked-page context when extracted content exists, so you often do not need to open the URL.
- `show --fetch-link` and `context --fetch-link` will extract the first external link on demand when no stored extract exists.
- The persistent index now searches across all stored extracted link pages for a bookmark, not only the first link.

## Agent access

```bash
uv run x-bookmarks mcp
uv run x-bookmarks serve --port 4111
```

Notes:
- `mcp` exposes bookmark search/context/status tools over stdio.
- `serve` exposes local JSON endpoints such as `/status`, `/search`, `/show`, and `/context`.

## Diagnostics

`doctor` checks local file health, index freshness, sync-state integrity, and tombstone/archive consistency.

## Categorization

```bash
uv run x-bookmarks categorize
uv run x-bookmarks categorize --regex
```

Notes:
- `categorize` uses Claude Haiku.
- `categorize --regex` is the cheap deterministic fallback.
- Both modes write the same `categorized.json` shape consumed by search and agent-facing tools.
