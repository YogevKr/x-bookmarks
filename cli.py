#!/usr/bin/env python3
"""X Bookmarks CLI, pipeline, and agent-facing services."""

import argparse
import json
from pathlib import Path

from bookmark_paths import read_only_mode
from bookmark_query import (
    collect_stats,
    doctor_report,
    domain_counts,
    ensure_index,
    format_context_result,
    format_list_results,
    format_search_results,
    format_show_result,
    get_index_status,
    list_bookmarks,
    refresh_index,
    render_viz,
    search_bookmarks,
    show_bookmark,
    show_deleted_bookmark,
    bookmark_context,
)


def _require_writable(action: str) -> None:
    if read_only_mode():
        raise SystemExit(
            f"{action} is disabled when X_BOOKMARKS_READ_ONLY=1. "
            "Unset it on the writer machine."
        )


def cmd_watch(args: argparse.Namespace) -> None:
    _require_writable("watch")
    from bookmark_watch import run_watch

    result = run_watch(
        interval=args.interval,
        once=args.once,
        quiet=args.quiet or args.json,
        force=args.force,
    )
    if args.json and result is not None:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_launchd(args: argparse.Namespace) -> None:
    from bookmark_launchd import (
        install_launch_agent,
        launch_agent_status,
        uninstall_launch_agent,
    )

    if args.launchd_command == "install":
        _require_writable("launchd install")
        result = install_launch_agent(
            interval=args.interval,
            base_dir=args.base_dir,
            quiet=not args.verbose,
        )
    elif args.launchd_command == "uninstall":
        result = uninstall_launch_agent()
    else:
        result = launch_agent_status()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(f"Label: {result['label']}")
    print(f"Plist: {result['plist_path']}")
    print(f"Installed: {result['installed']}")
    print(f"Loaded: {result['loaded']}")
    if result.get("pid") is not None:
        print(f"PID: {result['pid']}")
    if result.get("state") is not None:
        print(f"State: {result['state']}")
    if result.get("last_exit_code") is not None:
        print(f"Last exit: {result['last_exit_code']}")


def cmd_extract(args: argparse.Namespace) -> None:
    _require_writable("extract")
    print("Phase 1: Content extraction")
    from extract import run_extraction

    run_extraction(force=args.force, limit=args.limit, bookmark_id=args.bookmark_id)
    refresh_index()


def cmd_categorize(args: argparse.Namespace) -> None:
    _require_writable("categorize")
    mode = "Regex categorization" if args.regex else "AI categorization"
    print(f"Phase 2: {mode}")
    from categorize import run_categorization

    run_categorization(force=args.force, use_regex=args.regex)
    refresh_index()


def cmd_normalize(_args: argparse.Namespace) -> None:
    _require_writable("normalize")
    print("Phase 2.5: Category normalization")
    from normalize import run_normalization

    run_normalization()
    refresh_index()


def cmd_all(args: argparse.Namespace) -> None:
    cmd_extract(args)
    cmd_categorize(argparse.Namespace(force=args.force, regex=args.regex))


def cmd_sync(args: argparse.Namespace) -> None:
    _require_writable("sync")
    from bookmark_sync import read_stdin_json, sync_bookmarks

    stdin_text = read_stdin_json() if args.stdin else None
    result = sync_bookmarks(
        input_file=args.input,
        stdin_text=stdin_text,
        reconcile_only=args.reconcile_only,
        run_extract=args.extract,
        run_categorize=args.categorize or args.regex,
        use_regex=args.regex,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Source of truth: {result['source_of_truth']}")
    print(
        f"Bookmarks: {result['bookmarks']['previous']} -> {result['bookmarks']['current']} "
        f"(removed {result['bookmarks']['removed']}, tombstoned {result['bookmarks']['tombstoned']})"
    )
    print(
        "Bidirectional: "
        f"detected_deletes={result['bidirectional']['detected_deletes']} · "
        f"restored={result['bidirectional']['restored']}"
    )
    print(f"Index: {result['index']['doc_count']} docs @ {result['index']['index_db']}")


def cmd_remove(args: argparse.Namespace) -> None:
    _require_writable("remove")
    from bookmark_sync import remove_bookmarks

    result = remove_bookmarks(args.ids)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(
        f"Removed: {', '.join(result['mutation']['removed']) or 'none'} "
        f"(missing: {', '.join(result['mutation']['missing']) or 'none'})"
    )
    print(f"Bookmarks: {result['bookmarks']['current']} active")


def cmd_restore(args: argparse.Namespace) -> None:
    _require_writable("restore")
    from bookmark_sync import restore_bookmarks

    ids = [] if args.all else args.ids
    result = restore_bookmarks(ids)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(
        f"Restored: {', '.join(result['mutation']['restored']) or 'none'} "
        f"(missing archive: {', '.join(result['mutation']['missing']) or 'none'})"
    )
    print(f"Bookmarks: {result['bookmarks']['current']} active")


def cmd_note(args: argparse.Namespace) -> None:
    _require_writable("note")
    from bookmark_sync import set_note

    text = " ".join(args.text).strip()
    if not args.clear and not text:
        raise SystemExit("note requires text or --clear")
    result = set_note(args.id, text, clear=args.clear)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Note updated: {', '.join(result['mutation']['updated']) or 'none'}")


def cmd_tag(args: argparse.Namespace) -> None:
    _require_writable("tag")
    from bookmark_sync import add_tags

    result = add_tags(args.id, args.tags)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Tagged: {', '.join(result['mutation']['updated']) or 'none'}")


def cmd_untag(args: argparse.Namespace) -> None:
    _require_writable("untag")
    from bookmark_sync import remove_tags

    result = remove_tags(args.id, args.tags)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Tags removed: {', '.join(result['mutation']['updated']) or 'none'}")


def cmd_rate(args: argparse.Namespace) -> None:
    _require_writable("rate")
    from bookmark_sync import set_rating

    if not args.clear and args.value is None:
        raise SystemExit("rate requires a value or --clear")
    result = set_rating(args.id, args.value, clear=args.clear)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Rating updated: {', '.join(result['mutation']['updated']) or 'none'}")


def cmd_hide(args: argparse.Namespace) -> None:
    _require_writable("hide")
    from bookmark_sync import hide_bookmarks

    result = hide_bookmarks(args.ids)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Hidden: {', '.join(result['mutation']['updated']) or 'none'}")


def cmd_unhide(args: argparse.Namespace) -> None:
    _require_writable("unhide")
    from bookmark_sync import unhide_bookmarks

    result = unhide_bookmarks(args.ids)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Unhidden: {', '.join(result['mutation']['updated']) or 'none'}")


def _auto_refresh(args: argparse.Namespace) -> bool:
    if read_only_mode():
        return False
    return not getattr(args, "no_refresh", False)


def _extract_link_context_from_urls(urls: list[str]) -> dict | None:
    from extract import extract_url

    for url in urls:
        extracted = extract_url(url)
        if extracted:
            return {"url": url, **extracted}
    return None


def _augment_result_with_link_context(result: dict | None, *, fetch_link: bool = False) -> dict | None:
    if not result:
        return None

    output = dict(result)
    linked_url = (output.get("external_urls") or [None])[0]
    output["linked_url"] = linked_url

    has_stored_link = any(output.get(field) for field in ("linked_title", "linked_description", "linked_preview"))
    if has_stored_link:
        output["linked_source"] = "stored"
        return output

    if not linked_url:
        output["linked_source"] = "none"
        return output

    if not fetch_link:
        output["linked_source"] = "available"
        return output

    extracted = _extract_link_context_from_urls(output.get("external_urls") or [])
    if not extracted:
        output["linked_source"] = "fetch_failed"
        return output

    output["linked_title"] = extracted.get("title", "")
    output["linked_description"] = extracted.get("description", "")
    output["linked_preview"] = extracted.get("content", "")
    output["linked_source"] = "fetched"
    output["linked_url"] = extracted.get("url", linked_url)
    return output


def _augment_context_with_link_context(result: dict | None, *, fetch_link: bool = False) -> dict | None:
    if not result:
        return None
    output = dict(result)
    output["bookmark"] = _augment_result_with_link_context(output["bookmark"], fetch_link=fetch_link)
    return output


def cmd_status(args: argparse.Namespace) -> None:
    status = get_index_status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return
    print(f"Index DB: {status['index_db']}")
    print(f"Manifest: {status['manifest_path']}")
    print("Source of truth: bookmarks.json")
    print(f"Fresh: {status['fresh']}")
    print(f"Doc count: source={status['doc_count']} indexed={status['indexed_count']}")
    print(f"Built at: {status['built_at']}")
    print(f"Base source: {status['source_state']['base']}")
    print(f"Read-only mode: {read_only_mode()}")
    print(
        "Sync state: "
        f"tombstones={status['sync_state']['tombstone_count']} · "
        f"archive={status['sync_state']['archive_count']} · "
        f"updated_at={status['sync_state']['updated_at']}"
    )
    if status["reasons"]:
        print("Reasons:")
        for reason in status["reasons"]:
            print(f"  - {reason}")


def cmd_refresh(args: argparse.Namespace) -> None:
    _require_writable("refresh")
    result = refresh_index(force=args.force)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"Rebuilt: {result['rebuilt']}")
    print(f"Doc count: {result['doc_count']}")
    print(f"Index DB: {result['index_db']}")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Built at: {result['built_at']}")


def cmd_doctor(args: argparse.Namespace) -> None:
    report = doctor_report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"OK: {report['ok']}")
    print(
        f"Summary: active={report['summary']['active_bookmarks']} · "
        f"tombstones={report['summary']['tombstones']} · "
        f"archive={report['summary']['archive']} · "
        f"index_fresh={report['summary']['index_fresh']}"
    )
    for check in report["checks"]:
        print(f"{check['status'].upper():<5} {check['name']}: {check['detail']}")


def cmd_search(args: argparse.Namespace) -> None:
    results = search_bookmarks(
        args.query,
        author=args.author,
        category=args.category,
        domain=args.domain,
        language=args.language,
        bookmark_type_value=args.bookmark_type,
        after=args.after,
        before=args.before,
        limit=args.limit,
        group_by=args.group_by,
        hidden=args.hidden,
        explain=args.explain,
        auto_refresh=_auto_refresh(args),
    )
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return
    print(format_search_results(results))


def cmd_list(args: argparse.Namespace) -> None:
    results = list_bookmarks(
        query=args.query,
        author=args.author,
        category=args.category,
        domain=args.domain,
        language=args.language,
        bookmark_type_value=args.bookmark_type,
        after=args.after,
        before=args.before,
        limit=args.limit,
        offset=args.offset,
        sort=args.sort,
        deleted=args.deleted,
        hidden=args.hidden,
        auto_refresh=_auto_refresh(args),
    )
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return
    print(format_list_results(results))


def cmd_show(args: argparse.Namespace) -> None:
    result = show_deleted_bookmark(args.id) if args.deleted else show_bookmark(args.id, auto_refresh=_auto_refresh(args))
    result = _augment_result_with_link_context(result, fetch_link=args.fetch_link)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(format_show_result(result))


def cmd_context(args: argparse.Namespace) -> None:
    result = bookmark_context(args.id, limit=args.limit, auto_refresh=_auto_refresh(args))
    result = _augment_context_with_link_context(result, fetch_link=args.fetch_link)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(format_context_result(result))


def cmd_stats(args: argparse.Namespace) -> None:
    status = ensure_index(auto_refresh=_auto_refresh(args))
    stats = collect_stats(auto_refresh=False)
    stats["index"] = {"built_at": status.get("built_at"), "fresh": status.get("fresh")}
    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return
    print(f"Bookmarks: {stats['total_bookmarks']}")
    print(f"Unique authors: {stats['unique_authors']}")
    print(f"Date range: {stats['date_range']['earliest']} -> {stats['date_range']['latest']}")
    print(
        "Coverage: "
        f"media={stats['with_media']} · urls={stats['with_urls']} · "
        f"extracted={stats['with_extracted']} · avg_text_len={stats['average_text_length']}"
    )
    print("\nTop authors:")
    for item in stats["top_authors"][:10]:
        print(f"  {item['handle']}: {item['count']}")
    print("\nLanguages:")
    for item in stats["languages"]:
        print(f"  {item['language']}: {item['count']}")
    print("\nTypes:")
    for item in stats["types"]:
        print(f"  {item['type']}: {item['count']}")
    print("\nTop categories:")
    for item in stats["categories"][:10]:
        print(f"  {item['name']}: {item['count']}")


def cmd_domains(args: argparse.Namespace) -> None:
    counts = domain_counts(
        author=args.author,
        category=args.category,
        language=args.language,
        bookmark_type_value=args.bookmark_type,
        after=args.after,
        before=args.before,
        limit=args.limit,
        auto_refresh=_auto_refresh(args),
    )
    if args.json:
        print(json.dumps(counts, indent=2, ensure_ascii=False))
        return
    if not counts:
        print("No domains found.")
        return
    for item in counts:
        print(f"{item['domain']:<28} {item['count']}")


def cmd_viz(args: argparse.Namespace) -> None:
    print(render_viz(auto_refresh=_auto_refresh(args)))


def cmd_mcp(_args: argparse.Namespace) -> None:
    from bookmark_mcp import run_mcp_server

    run_mcp_server()


def cmd_serve(args: argparse.Namespace) -> None:
    from bookmark_serve import run_http_server

    run_http_server(host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="X Bookmarks pipeline, persistent index, and agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    extract_parser = sub.add_parser("extract", help="Phase 1: Extract content from URLs")
    extract_parser.add_argument("--force", action="store_true", help="Re-extract bookmarks even if link content already exists")
    extract_parser.add_argument("--limit", type=int, help="Only process the first N matching bookmarks")
    extract_parser.add_argument("--bookmark-id", help="Only extract links for a specific bookmark id")

    cat_parser = sub.add_parser("categorize", help="Phase 2: categorize bookmarks")
    cat_parser.add_argument("--force", action="store_true", help="Re-categorize all bookmarks")
    cat_parser.add_argument("--regex", action="store_true", help="Use keyword/regex categorization instead of Claude")

    all_parser = sub.add_parser("all", help="Run full pipeline")
    all_parser.add_argument("--force", action="store_true", help="Re-categorize all bookmarks")
    all_parser.add_argument("--regex", action="store_true", help="Use keyword/regex categorization instead of Claude")

    sub.add_parser("normalize", help="Phase 2.5: Normalize/merge categories")
    sync_parser = sub.add_parser("sync", help="Import/reconcile source-of-truth bookmarks and propagate deletions")
    sync_parser.add_argument("--input", type=Path, help="Exported bookmarks JSON to import")
    sync_parser.add_argument("--stdin", action="store_true", help="Read exported bookmarks JSON from stdin")
    sync_parser.add_argument("--reconcile-only", action="store_true", help="Use existing bookmarks.json and only prune/rebuild derived files")
    sync_parser.set_defaults(extract=True)
    sync_parser.add_argument("--extract", dest="extract", action="store_true", help="Run extraction after sync (default)")
    sync_parser.add_argument("--no-extract", dest="extract", action="store_false", help="Skip extraction after sync")
    sync_parser.add_argument("--categorize", action="store_true", help="Run Claude categorization after sync")
    sync_parser.add_argument("--regex", action="store_true", help="Run regex categorization after sync")
    sync_parser.add_argument("--json", action="store_true", help="Emit JSON")

    remove_parser = sub.add_parser("remove", help="Remove bookmarks locally and propagate to all synced files")
    remove_parser.add_argument("ids", nargs="+", help="Bookmark ids")
    remove_parser.add_argument("--json", action="store_true", help="Emit JSON")

    restore_parser = sub.add_parser("restore", help="Restore locally removed bookmarks from sync archive")
    restore_parser.add_argument("ids", nargs="*", help="Bookmark ids")
    restore_parser.add_argument("--all", action="store_true", help="Restore all tombstoned bookmarks")
    restore_parser.add_argument("--json", action="store_true", help="Emit JSON")

    note_parser = sub.add_parser("note", help="Set or clear a local note on a bookmark")
    note_parser.add_argument("id", help="Bookmark id")
    note_parser.add_argument("text", nargs="*", help="Note text")
    note_parser.add_argument("--clear", action="store_true", help="Remove the local note")
    note_parser.add_argument("--json", action="store_true", help="Emit JSON")

    tag_parser = sub.add_parser("tag", help="Add local tags to a bookmark")
    tag_parser.add_argument("id", help="Bookmark id")
    tag_parser.add_argument("tags", nargs="+", help="Tags to add")
    tag_parser.add_argument("--json", action="store_true", help="Emit JSON")

    untag_parser = sub.add_parser("untag", help="Remove local tags from a bookmark")
    untag_parser.add_argument("id", help="Bookmark id")
    untag_parser.add_argument("tags", nargs="+", help="Tags to remove")
    untag_parser.add_argument("--json", action="store_true", help="Emit JSON")

    rate_parser = sub.add_parser("rate", help="Set or clear a local 1-5 rating on a bookmark")
    rate_parser.add_argument("id", help="Bookmark id")
    rate_parser.add_argument("value", nargs="?", type=int, help="Rating value 1-5")
    rate_parser.add_argument("--clear", action="store_true", help="Remove the local rating")
    rate_parser.add_argument("--json", action="store_true", help="Emit JSON")

    hide_parser = sub.add_parser("hide", help="Hide bookmarks from normal search/list/stats")
    hide_parser.add_argument("ids", nargs="+", help="Bookmark ids")
    hide_parser.add_argument("--json", action="store_true", help="Emit JSON")

    unhide_parser = sub.add_parser("unhide", help="Unhide bookmarks")
    unhide_parser.add_argument("ids", nargs="+", help="Bookmark ids")
    unhide_parser.add_argument("--json", action="store_true", help="Emit JSON")

    status_parser = sub.add_parser("status", help="Show index freshness and source drift")
    status_parser.add_argument("--json", action="store_true", help="Emit JSON")

    refresh_parser = sub.add_parser("refresh", help="Rebuild the persistent search index")
    refresh_parser.add_argument("--force", action="store_true", help="Force a full rebuild")
    refresh_parser.add_argument("--json", action="store_true", help="Emit JSON")

    watch_parser = sub.add_parser("watch", help="Continuously keep the local index fresh")
    watch_parser.add_argument("--interval", type=float, default=5.0, help="Poll interval in seconds")
    watch_parser.add_argument("--once", action="store_true", help="Run one watch iteration and exit")
    watch_parser.add_argument("--force", action="store_true", help="Force a rebuild on every iteration")
    watch_parser.add_argument("--quiet", action="store_true", help="Suppress periodic status lines")
    watch_parser.add_argument("--json", action="store_true", help="Emit JSON for --once")

    launchd_parser = sub.add_parser("launchd", help="Manage a macOS launch agent for watch mode")
    launchd_sub = launchd_parser.add_subparsers(dest="launchd_command", required=True)
    launchd_install = launchd_sub.add_parser("install", help="Install and start the watch LaunchAgent")
    launchd_install.add_argument("--interval", type=float, default=5.0, help="Watch poll interval in seconds")
    launchd_install.add_argument("--base-dir", type=Path, help="Bookmark workspace to watch (defaults to current runtime base)")
    launchd_install.add_argument("--verbose", action="store_true", help="Write watch logs to stdout/stderr instead of quiet mode")
    launchd_install.add_argument("--json", action="store_true", help="Emit JSON")
    launchd_status = launchd_sub.add_parser("status", help="Show LaunchAgent status")
    launchd_status.add_argument("--json", action="store_true", help="Emit JSON")
    launchd_uninstall = launchd_sub.add_parser("uninstall", help="Unload and remove the LaunchAgent")
    launchd_uninstall.add_argument("--json", action="store_true", help="Emit JSON")

    doctor_parser = sub.add_parser("doctor", help="Check source files, index state, and sync-state health")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON")

    search_parser = sub.add_parser("search", help="Hybrid search: BM25 + vector similarity + RRF")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--author", help="Filter by author handle")
    search_parser.add_argument("--category", help="Filter by category")
    search_parser.add_argument("--domain", help="Filter by external domain")
    search_parser.add_argument("--language", help="Filter by language")
    search_parser.add_argument("--type", dest="bookmark_type", help="Filter by bookmark type")
    search_parser.add_argument("--after", help="Only include bookmarks on/after YYYY-MM-DD")
    search_parser.add_argument("--before", help="Only include bookmarks on/before YYYY-MM-DD")
    search_parser.add_argument("--limit", type=int, default=20, help="Max results")
    search_parser.add_argument("--group-by", choices=["category", "author", "domain", "year"], help="Group results")
    search_parser.add_argument("--hidden", action="store_true", help="Search only hidden bookmarks")
    search_parser.add_argument("--explain", action="store_true", help="Show why each result matched and ranked")
    search_parser.add_argument("--json", action="store_true", help="Emit JSON")
    search_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    list_parser = sub.add_parser("list", help="Filter bookmarks from the index")
    list_parser.add_argument("--query", help="Optional text query")
    list_parser.add_argument("--author", help="Filter by author handle")
    list_parser.add_argument("--category", help="Filter by category")
    list_parser.add_argument("--domain", help="Filter by external domain")
    list_parser.add_argument("--language", help="Filter by language")
    list_parser.add_argument("--type", dest="bookmark_type", help="Filter by bookmark type")
    list_parser.add_argument("--after", help="Only include bookmarks on/after YYYY-MM-DD")
    list_parser.add_argument("--before", help="Only include bookmarks on/before YYYY-MM-DD")
    list_parser.add_argument("--sort", choices=["date", "relevance"], default="date", help="Sort order")
    list_parser.add_argument("--limit", type=int, default=30, help="Max results")
    list_parser.add_argument("--offset", type=int, default=0, help="Offset into the result set")
    list_parser.add_argument("--deleted", action="store_true", help="List locally deleted bookmarks from sync archive")
    list_parser.add_argument("--hidden", action="store_true", help="List hidden bookmarks instead of active visible ones")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON")
    list_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    show_parser = sub.add_parser("show", help="Show a bookmark in detail")
    show_parser.add_argument("id", help="Bookmark id")
    show_parser.add_argument("--deleted", action="store_true", help="Show a locally deleted bookmark from the sync archive")
    show_parser.add_argument("--fetch-link", action="store_true", help="Extract linked page content on demand if it is not already stored")
    show_parser.add_argument("--json", action="store_true", help="Emit JSON")
    show_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    context_parser = sub.add_parser("context", help="360-degree bookmark context")
    context_parser.add_argument("id", help="Bookmark id")
    context_parser.add_argument("--limit", type=int, default=5, help="Related results per section")
    context_parser.add_argument("--fetch-link", action="store_true", help="Extract linked page content for the anchor bookmark if it is not already stored")
    context_parser.add_argument("--json", action="store_true", help="Emit JSON")
    context_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    stats_parser = sub.add_parser("stats", help="Aggregate stats from the persistent index")
    stats_parser.add_argument("--json", action="store_true", help="Emit JSON")
    stats_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    domains_parser = sub.add_parser("domains", help="External domain distribution")
    domains_parser.add_argument("--author", help="Filter by author handle")
    domains_parser.add_argument("--category", help="Filter by category")
    domains_parser.add_argument("--language", help="Filter by language")
    domains_parser.add_argument("--type", dest="bookmark_type", help="Filter by bookmark type")
    domains_parser.add_argument("--after", help="Only include bookmarks on/after YYYY-MM-DD")
    domains_parser.add_argument("--before", help="Only include bookmarks on/before YYYY-MM-DD")
    domains_parser.add_argument("--limit", type=int, default=25, help="Max domains")
    domains_parser.add_argument("--json", action="store_true", help="Emit JSON")
    domains_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    viz_parser = sub.add_parser("viz", help="Terminal dashboard")
    viz_parser.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh a stale index")

    sub.add_parser("mcp", help="Run a local MCP stdio server")

    serve_parser = sub.add_parser("serve", help="Run a local HTTP server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_parser.add_argument("--port", type=int, default=4111, help="Bind port")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    command_map = {
        "extract": cmd_extract,
        "categorize": cmd_categorize,
        "normalize": cmd_normalize,
        "all": cmd_all,
        "sync": cmd_sync,
        "remove": cmd_remove,
        "restore": cmd_restore,
        "note": cmd_note,
        "tag": cmd_tag,
        "untag": cmd_untag,
        "rate": cmd_rate,
        "hide": cmd_hide,
        "unhide": cmd_unhide,
        "status": cmd_status,
        "refresh": cmd_refresh,
        "watch": cmd_watch,
        "launchd": cmd_launchd,
        "doctor": cmd_doctor,
        "search": cmd_search,
        "list": cmd_list,
        "show": cmd_show,
        "context": cmd_context,
        "stats": cmd_stats,
        "domains": cmd_domains,
        "viz": cmd_viz,
        "mcp": cmd_mcp,
        "serve": cmd_serve,
    }
    command_map[args.command](args)


if __name__ == "__main__":
    main()
