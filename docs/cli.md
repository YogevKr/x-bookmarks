# CLI

`x-bookmarks` now has a persistent local index in `.x-bookmarks/`.

Behavior:
- `bookmarks.json` is the source of truth.
- `sync` bootstraps from `bookmarks.json`, then keeps local snapshots in `.x-bookmarks/sync-state.json`.
- After bootstrap, deletes from `bookmarks.json`, `enriched.json`, or `categorized.json` are treated as local deletions and propagated everywhere.
- Local removals are tombstoned, so a later import does not silently resurrect them.
- Query commands auto-refresh a stale index unless you pass `--no-refresh`.
- The CLI reads data from the current working directory by default.
- Preferred macOS config file: `~/Library/Application Support/x-bookmarks/config.json`
- Supported config fallbacks: `~/.config/x-bookmarks/config.json`, `~/.x-bookmarks/config.json`
- Env still works and wins over config: `X_BOOKMARKS_HOME`, `X_BOOKMARKS_READ_ONLY`
- In read-only mode, write commands fail fast instead of mutating the shared workspace.

Config schema:

```json
{
  "base_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/x-bookmarks",
  "read_only": true
}
```

Config helpers:

```bash
uv run x-bookmarks version
uv run x-bookmarks config show
uv run x-bookmarks config init --writer --icloud
uv run x-bookmarks config init --reader --icloud
uv run x-bookmarks config init --base-dir ~/x-bookmarks --force
```

Notes:
- `version` shows the installed package version and executable path.
- `config init` writes the preferred config file for the current machine.
- `status` now includes binary path, install source, config path, and latest watch heartbeat.

## Sync

```bash
uv run x-bookmarks extract --force --limit 10
uv run x-bookmarks extract --bookmark-id 2037620876179537989
uv run x-bookmarks sync --input /path/to/bookmarks-export.json
cat bookmarks-export.json | uv run x-bookmarks sync --stdin
uv run x-bookmarks sync --reconcile-only
uv run x-bookmarks sync --input /path/to/bookmarks-export.json --no-extract
uv run x-bookmarks export-x --sync --no-extract
uv run x-bookmarks remove 2037620876179537989
uv run x-bookmarks restore 2037620876179537989
uv run x-bookmarks restore --all
uv run x-bookmarks note 2037620876179537989 "revisit for collector tuning ideas"
uv run x-bookmarks tag 2037620876179537989 favorite otel
uv run x-bookmarks rate 2037620876179537989 5
uv run x-bookmarks hide 2037620876179537989
uv run x-bookmarks unhide 2037620876179537989
uv run x-bookmarks metadata-export --output /tmp/x-bookmarks-metadata.json
uv run x-bookmarks metadata-import --input /tmp/x-bookmarks-metadata.json
```

Notes:
- `extract` now stores richer per-link context in `linked_pages`, not just one primary extract.
- `extract --force` upgrades existing bookmarks to the richer schema.
- `extract --bookmark-id` lets you re-extract one bookmark without touching the whole corpus.
- When full article extraction fails, `extract` now falls back to lightweight page metadata/text so more links still become searchable.
- Terminal failures are now cached in `enriched.json`, so later `extract` runs skip known junk unless you force a retry.
- The first `sync` bootstraps local state from `bookmarks.json`.
- Later `sync` runs detect file-side deletions in any local bookmark file, propagate them across all files, and refresh the persistent index.
- `sync` runs extraction by default; use `--no-extract` to skip link fetching on import.
- `sync --categorize` and `sync --regex` can continue the pipeline after import.
- `export-x` runs a standalone Chrome/CDP exporter. It needs a Chrome profile logged into X, but it does not use OpenClaw, Codex, or model credentials.
- `remove` deletes locally across all synced files and records a tombstone.
- `restore` rehydrates a locally removed bookmark from the sync archive.
- `note`, `tag`, `untag`, `rate`, `hide`, and `unhide` update local metadata on active bookmarks.
- `metadata-export` writes local notes/tags/ratings/hidden state into a portable JSON file.
- `metadata-import` restores that local metadata and can merge or replace it.
- `status` reports tombstone/archive counts from sync state.

## Index lifecycle

```bash
uv run x-bookmarks status
uv run x-bookmarks doctor
uv run x-bookmarks extract-failures
uv run x-bookmarks retry-failures --domain github.com
uv run x-bookmarks refresh
uv run x-bookmarks refresh --force
uv run x-bookmarks watch
uv run x-bookmarks watch --once --json
uv run x-bookmarks stale-check --max-age-hours 36
```

Notes:
- `watch` polls local source files and refreshes the SQLite index whenever they drift.
- `watch` does not require a background database service; it is just a long-running CLI loop.
- `watch` now persists a heartbeat in `.x-bookmarks/watch-state.json`.
- `stale-check` fails with exit code 2 when the source X export is older than the configured threshold.
- `extract-failures` lists cached extraction misses with domain, attempts, and terminal/retryable state.
- `retry-failures` reruns those stored misses, and `--terminal` includes failures previously marked terminal.
- index rebuilds now checkpoint the SQLite WAL, which is friendlier for iCloud-synced workspaces.

## macOS launch agent

```bash
uv run x-bookmarks launchd install
uv run x-bookmarks launchd install --base-dir /path/to/bookmark-workspace
uv run x-bookmarks launchd install-export --user-data-dir ~/.x-bookmarks/chrome-profile
uv run x-bookmarks launchd install-stale-check --max-age-hours 36
uv run x-bookmarks launchd status
uv run x-bookmarks launchd export-status
uv run x-bookmarks launchd stale-check-status
uv run x-bookmarks launchd uninstall
```

Notes:
- `launchd install` creates a LaunchAgent that starts `x-bookmarks watch` at login.
- `launchd install-export` creates a separate daily LaunchAgent that runs `x-bookmarks export-x --sync --no-extract`.
- `launchd install-stale-check` creates a separate LaunchAgent that runs `x-bookmarks stale-check --notify`.
- The watched workspace is pinned via `X_BOOKMARKS_HOME`, so it does not depend on the shell working directory.
- Logs go to `.x-bookmarks/watch.*.log`, `.x-bookmarks/export.*.log`, and `.x-bookmarks/stale-check.*.log` inside the watched workspace.
- Good shared setup: install `launchd` on one Mac only, point it at an iCloud-backed `base_dir`, and query that same folder from another Mac with `read_only: true` in config.

Example iCloud path:

```bash
mkdir -p "$HOME/Library/Application Support/x-bookmarks"
cat > "$HOME/Library/Application Support/x-bookmarks/config.json" <<'JSON'
{
  "base_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/x-bookmarks"
}
JSON

uv run x-bookmarks launchd install
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
- query results now surface highlighted match snippets from extracted linked-page text when available.
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
- On a read-only query machine, set `read_only: true` once in config instead of repeating `--no-refresh`.

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
- Claude mode requires the optional `anthropic` dependency, for example `uv sync --extra ai`.
- `categorize --regex` is the cheap deterministic fallback.
- Both modes write the same `categorized.json` shape consumed by search and agent-facing tools.
