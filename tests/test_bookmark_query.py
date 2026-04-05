import json
import tempfile
import unittest
from pathlib import Path

from bookmark_query import (
    IndexPaths,
    bookmark_context,
    collect_stats,
    domain_counts,
    format_context_result,
    format_search_results,
    format_show_result,
    get_index_status,
    list_bookmarks,
    refresh_index,
    render_viz,
    search_bookmarks,
    show_bookmark,
    show_deleted_bookmark,
)
from bookmark_sync import remove_bookmarks, sync_bookmarks


BOOKMARKS = {
    "bookmarks": [
        {
            "id": "1",
            "handle": "@otel",
            "author": "OpenTelemetry",
            "text": "Observability with OpenTelemetry in Python",
            "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
            "urls": [
                "https://opentelemetry.io/docs/languages/python/",
                "https://example.com/otel-deep-dive",
            ],
            "hashtags": ["observability"],
            "media": [],
        },
        {
            "id": "2",
            "handle": "@anthropicai",
            "author": "Anthropic",
            "text": "Claude Code memory tool for AI agents",
            "timestamp": "Sun Feb 01 12:00:00 +0000 2026",
            "urls": ["https://github.com/anthropics/claude-memory"],
            "hashtags": ["ai"],
            "media": [],
        },
        {
            "id": "3",
            "handle": "@sec",
            "author": "Sec Team",
            "text": "מדריך לאבטחת Kubernetes בענן",
            "timestamp": "Sun Mar 01 12:00:00 +0000 2026",
            "urls": ["https://example.com/security/kubernetes"],
            "hashtags": [],
            "media": ["https://img.example.com/1.jpg"],
        },
    ]
}

ENRICHED = {
    "bookmarks": [
        {
            "id": "1",
            "handle": "@otel",
            "author": "OpenTelemetry",
            "text": "Observability with OpenTelemetry in Python",
            "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
            "urls": [
                "https://opentelemetry.io/docs/languages/python/",
                "https://example.com/otel-deep-dive",
            ],
            "hashtags": ["observability"],
            "media": [],
            "linked_pages": [
                {
                    "url": "https://opentelemetry.io/docs/languages/python/",
                    "title": "Python instrumentation",
                    "description": "Tracing and metrics with OpenTelemetry",
                    "content": "Observability pipelines rely on traces, metrics, and logs.",
                    "preview": "Observability pipelines rely on traces, metrics, and logs.",
                    "site_name": "OpenTelemetry",
                    "word_count": 12,
                },
                {
                    "url": "https://example.com/otel-deep-dive",
                    "title": "Advanced telemetry deep dive",
                    "description": "Production OpenTelemetry patterns",
                    "content": "Deep dive into production-grade telemetry pipelines and collector tuning.",
                    "preview": "Deep dive into production-grade telemetry pipelines and collector tuning.",
                    "site_name": "Example",
                    "word_count": 11,
                },
            ],
            "extracted": {
                "title": "Python instrumentation",
                "description": "Tracing and metrics with OpenTelemetry",
                "content": "Observability pipelines rely on traces, metrics, and logs.",
                "preview": "Observability pipelines rely on traces, metrics, and logs.",
                "url": "https://opentelemetry.io/docs/languages/python/",
                "site_name": "OpenTelemetry",
            },
        },
        BOOKMARKS["bookmarks"][1],
        BOOKMARKS["bookmarks"][2],
    ]
}

CATEGORIZED = {
    "bookmarks": [
        {
            **BOOKMARKS["bookmarks"][0],
            "ai": {
                "categories": ["DevTools"],
                "entities": ["OpenTelemetry", "Python"],
                "summary": "OpenTelemetry Python observability guide",
                "language": "en",
                "importance": 4,
                "type": "article",
            },
        },
        {
            **BOOKMARKS["bookmarks"][1],
            "ai": {
                "categories": ["AI & Machine Learning", "Software Engineering"],
                "entities": ["Claude", "GitHub"],
                "summary": "Agent memory tool on GitHub",
                "language": "en",
                "importance": 5,
                "type": "tool",
            },
        },
        {
            **BOOKMARKS["bookmarks"][2],
            "ai": {
                "categories": ["Security", "Hebrew Content"],
                "entities": ["Kubernetes"],
                "summary": "מדריך לאבטחת Kubernetes",
                "language": "he",
                "importance": 4,
                "type": "article",
            },
        },
    ]
}


class BookmarkIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.paths = IndexPaths(base_dir=self.base)
        (self.base / "bookmarks.json").write_text(json.dumps(BOOKMARKS, ensure_ascii=False), encoding="utf-8")
        (self.base / "enriched.json").write_text(json.dumps(ENRICHED, ensure_ascii=False), encoding="utf-8")
        (self.base / "categorized.json").write_text(json.dumps(CATEGORIZED, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_status_and_refresh(self) -> None:
        status = get_index_status(paths=self.paths)
        self.assertTrue(status["stale"])
        result = refresh_index(paths=self.paths)
        self.assertTrue(result["fresh"])
        status = get_index_status(paths=self.paths)
        self.assertTrue(status["fresh"])
        self.assertIn("sync_state", status)

    def test_search_bookmarks_hybrid(self) -> None:
        results = search_bookmarks("observability", limit=5, paths=self.paths)
        self.assertIsInstance(results, list)
        self.assertEqual(results[0]["id"], "1")
        self.assertIn("bm25#1", results[0]["why"])

    def test_search_matches_secondary_link_content(self) -> None:
        results = search_bookmarks("collector tuning", limit=5, paths=self.paths)
        self.assertEqual(results[0]["id"], "1")

    def test_grouped_search(self) -> None:
        results = search_bookmarks("ai", limit=5, group_by="category", paths=self.paths)
        self.assertEqual(results["group_by"], "category")
        self.assertGreaterEqual(results["groups"][0]["count"], 1)

    def test_list_filters(self) -> None:
        results = list_bookmarks(category="Security", language="he", limit=10, paths=self.paths)
        self.assertEqual([item["id"] for item in results], ["3"])

    def test_show_and_context(self) -> None:
        refresh_index(paths=self.paths)
        shown = show_bookmark("1", paths=self.paths)
        self.assertEqual(shown["id"], "1")
        self.assertEqual(shown["linked_title"], "Python instrumentation")
        self.assertIn("Observability pipelines rely on traces", shown["linked_preview"])
        self.assertEqual(len(shown["link_pages"]), 2)
        context = bookmark_context("1", paths=self.paths)
        self.assertEqual(context["bookmark"]["id"], "1")
        self.assertIn("similar", context)

    def test_formatters_include_context_previews(self) -> None:
        refresh_index(paths=self.paths)
        results = search_bookmarks("observability", limit=5, paths=self.paths)
        output = format_search_results(results)
        self.assertIn("tweet:", output)
        self.assertIn("link:", output)

        shown = show_bookmark("1", paths=self.paths)
        show_output = format_show_result(shown)
        self.assertIn("linked", show_output)
        self.assertIn("Python instrumentation", show_output)
        self.assertIn("other links", show_output)
        self.assertIn("Advanced telemetry deep dive", show_output)

        context = bookmark_context("1", paths=self.paths)
        context_output = format_context_result(context)
        self.assertIn("linked", context_output)
        self.assertIn("Observability with OpenTelemetry in Python", context_output)

    def test_collect_stats_and_domains(self) -> None:
        refresh_index(paths=self.paths)
        stats = collect_stats(paths=self.paths)
        self.assertEqual(stats["total_bookmarks"], 3)
        self.assertEqual(stats["with_media"], 1)
        counts = domain_counts(limit=10, paths=self.paths)
        self.assertIn("opentelemetry.io", {item["domain"] for item in counts})

    def test_render_viz_contains_sections(self) -> None:
        refresh_index(paths=self.paths)
        output = render_viz(paths=self.paths)
        self.assertIn("Top categories", output)
        self.assertIn("Coverage", output)

    def test_source_change_marks_index_stale(self) -> None:
        refresh_index(paths=self.paths)
        updated = json.loads((self.base / "bookmarks.json").read_text(encoding="utf-8"))
        updated["bookmarks"] = updated["bookmarks"][:-1]
        (self.base / "bookmarks.json").write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
        status = get_index_status(paths=self.paths)
        self.assertTrue(status["stale"])
        self.assertIn("source_files_changed", status["reasons"])

    def test_deleted_list_and_show(self) -> None:
        sync_bookmarks(reconcile_only=True, paths=self.paths)
        remove_bookmarks(["2"], paths=self.paths)

        deleted = list_bookmarks(deleted=True, paths=self.paths)
        self.assertEqual([item["id"] for item in deleted], ["2"])
        self.assertTrue(deleted[0]["deleted"])
        self.assertEqual(deleted[0]["deletion_source"], "cli:remove")

        shown = show_deleted_bookmark("2", paths=self.paths)
        self.assertIsNotNone(shown)
        assert shown is not None
        self.assertTrue(shown["deleted"])
        self.assertEqual(shown["id"], "2")


if __name__ == "__main__":
    unittest.main()
