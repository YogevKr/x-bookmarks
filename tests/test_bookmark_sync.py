import json
import tempfile
import unittest
from pathlib import Path

from bookmark_query import IndexPaths, get_index_status, list_bookmarks
from bookmark_sync import sync_bookmarks


BASE_PAYLOAD = {
    "exportDate": "2026-04-05T00:00:00Z",
    "source": "bookmark",
    "bookmarks": [
        {
            "id": "1",
            "author": "Alice",
            "handle": "@alice",
            "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
            "text": "OpenTelemetry observability guide",
            "media": [],
            "hashtags": ["observability"],
            "urls": ["https://opentelemetry.io/docs"],
        },
        {
            "id": "2",
            "author": "Bob",
            "handle": "@bob",
            "timestamp": "Fri Jan 02 12:00:00 +0000 2026",
            "text": "Claude Code memory system",
            "media": [],
            "hashtags": ["ai"],
            "urls": ["https://github.com/acme/memory"],
        },
        {
            "id": "3",
            "author": "Carol",
            "handle": "@carol",
            "timestamp": "Sat Jan 03 12:00:00 +0000 2026",
            "text": "Security bulletin",
            "media": [],
            "hashtags": [],
            "urls": ["https://example.com/security"],
        },
    ],
}


class BookmarkSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.paths = IndexPaths(base_dir=self.base)
        (self.base / "bookmarks.json").write_text(json.dumps(BASE_PAYLOAD, ensure_ascii=False), encoding="utf-8")
        (self.base / "enriched.json").write_text(
            json.dumps(
                {
                    **BASE_PAYLOAD,
                    "bookmarks": [
                        {**bookmark, "extracted": {"title": f"title-{bookmark['id']}", "description": "", "content": ""}}
                        for bookmark in BASE_PAYLOAD["bookmarks"]
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.base / "categorized.json").write_text(
            json.dumps(
                {
                    **BASE_PAYLOAD,
                    "bookmarks": [
                        {
                            **bookmark,
                            "ai": {
                                "categories": ["DevTools"],
                                "entities": [],
                                "summary": bookmark["text"],
                                "language": "en",
                                "importance": 3,
                                "type": "article",
                            },
                        }
                        for bookmark in BASE_PAYLOAD["bookmarks"]
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_sync_removes_deleted_bookmarks_everywhere(self) -> None:
        new_export = {
            **BASE_PAYLOAD,
            "bookmarks": BASE_PAYLOAD["bookmarks"][:2],
        }
        import_file = self.base / "new-bookmarks.json"
        import_file.write_text(json.dumps(new_export, ensure_ascii=False), encoding="utf-8")

        result = sync_bookmarks(input_file=import_file, paths=self.paths)

        self.assertEqual(result["bookmarks"]["removed"], 1)
        self.assertEqual(result["index"]["doc_count"], 2)

        enriched = json.loads((self.base / "enriched.json").read_text(encoding="utf-8"))
        categorized = json.loads((self.base / "categorized.json").read_text(encoding="utf-8"))
        self.assertEqual({bookmark["id"] for bookmark in enriched["bookmarks"]}, {"1", "2"})
        self.assertEqual({bookmark["id"] for bookmark in categorized["bookmarks"]}, {"1", "2"})

        status = get_index_status(paths=self.paths)
        self.assertTrue(status["fresh"])
        listed = list_bookmarks(limit=10, paths=self.paths)
        self.assertEqual({item["id"] for item in listed}, {"1", "2"})

    def test_reconcile_only_prunes_derived_files(self) -> None:
        bookmarks = json.loads((self.base / "bookmarks.json").read_text(encoding="utf-8"))
        bookmarks["bookmarks"] = bookmarks["bookmarks"][:1]
        (self.base / "bookmarks.json").write_text(json.dumps(bookmarks, ensure_ascii=False), encoding="utf-8")

        result = sync_bookmarks(reconcile_only=True, paths=self.paths)

        self.assertEqual(result["bookmarks"]["removed"], 0)
        self.assertEqual(result["derived"]["categorized"]["removed"], 2)
        categorized = json.loads((self.base / "categorized.json").read_text(encoding="utf-8"))
        self.assertEqual(len(categorized["bookmarks"]), 1)


if __name__ == "__main__":
    unittest.main()
