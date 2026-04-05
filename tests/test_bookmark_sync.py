import json
import tempfile
import unittest
from pathlib import Path

from bookmark_query import IndexPaths, get_index_status, list_bookmarks
from bookmark_sync import add_tags, hide_bookmarks, remove_bookmarks, restore_bookmarks, set_note, set_rating, sync_bookmarks


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

    def _read_json(self, name: str) -> dict:
        return json.loads((self.base / name).read_text(encoding="utf-8"))

    def test_sync_removes_deleted_bookmarks_everywhere(self) -> None:
        new_export = {
            **BASE_PAYLOAD,
            "bookmarks": BASE_PAYLOAD["bookmarks"][:2],
        }
        import_file = self.base / "new-bookmarks.json"
        import_file.write_text(json.dumps(new_export, ensure_ascii=False), encoding="utf-8")

        result = sync_bookmarks(input_file=import_file, paths=self.paths)

        self.assertEqual(result["bookmarks"]["removed"], 1)
        self.assertEqual(result["bidirectional"]["detected_deletes"], 0)
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
        self.assertEqual(result["bidirectional"]["detected_deletes"], 0)
        categorized = self._read_json("categorized.json")
        self.assertEqual(len(categorized["bookmarks"]), 1)

    def test_delete_from_enriched_propagates_everywhere(self) -> None:
        sync_bookmarks(reconcile_only=True, paths=self.paths)
        enriched = self._read_json("enriched.json")
        enriched["bookmarks"] = [bookmark for bookmark in enriched["bookmarks"] if bookmark["id"] != "2"]
        (self.base / "enriched.json").write_text(json.dumps(enriched, ensure_ascii=False), encoding="utf-8")

        result = sync_bookmarks(reconcile_only=True, paths=self.paths)

        self.assertEqual(result["bidirectional"]["detected_deletes"], 1)
        self.assertEqual(result["bookmarks"]["current"], 2)
        for name in ("bookmarks.json", "enriched.json", "categorized.json"):
            ids = {bookmark["id"] for bookmark in self._read_json(name)["bookmarks"]}
            self.assertEqual(ids, {"1", "3"})

    def test_remove_uses_tombstone_and_blocks_reimport(self) -> None:
        sync_bookmarks(reconcile_only=True, paths=self.paths)

        remove_result = remove_bookmarks(["2"], paths=self.paths)
        self.assertEqual(remove_result["mutation"]["removed"], ["2"])
        self.assertEqual({bookmark["id"] for bookmark in self._read_json("bookmarks.json")["bookmarks"]}, {"1", "3"})

        import_file = self.base / "reimport.json"
        import_file.write_text(json.dumps(BASE_PAYLOAD, ensure_ascii=False), encoding="utf-8")
        result = sync_bookmarks(input_file=import_file, paths=self.paths)

        self.assertEqual(result["bookmarks"]["current"], 2)
        self.assertEqual({bookmark["id"] for bookmark in self._read_json("bookmarks.json")["bookmarks"]}, {"1", "3"})

    def test_restore_rehydrates_from_archive(self) -> None:
        sync_bookmarks(reconcile_only=True, paths=self.paths)
        remove_bookmarks(["2"], paths=self.paths)

        result = restore_bookmarks(["2"], paths=self.paths)

        self.assertEqual(result["mutation"]["restored"], ["2"])
        ids = {bookmark["id"] for bookmark in self._read_json("bookmarks.json")["bookmarks"]}
        self.assertEqual(ids, {"1", "2", "3"})

    def test_restore_all_rehydrates_all_tombstones(self) -> None:
        sync_bookmarks(reconcile_only=True, paths=self.paths)
        remove_bookmarks(["1", "2"], paths=self.paths)

        result = restore_bookmarks([], paths=self.paths)

        self.assertEqual(result["mutation"]["restored"], ["1", "2"])
        ids = {bookmark["id"] for bookmark in self._read_json("bookmarks.json")["bookmarks"]}
        self.assertEqual(ids, {"1", "2", "3"})

    def test_local_metadata_persists_through_sync(self) -> None:
        sync_bookmarks(reconcile_only=True, paths=self.paths)
        set_note("1", "worth revisiting", paths=self.paths)
        add_tags("1", ["favorite", "otel"], paths=self.paths)
        set_rating("1", 5, paths=self.paths)
        hide_bookmarks(["1"], paths=self.paths)

        import_file = self.base / "same-bookmarks.json"
        import_file.write_text(json.dumps(BASE_PAYLOAD, ensure_ascii=False), encoding="utf-8")
        sync_bookmarks(input_file=import_file, paths=self.paths)

        for name in ("bookmarks.json", "enriched.json", "categorized.json"):
            bookmark = next(item for item in self._read_json(name)["bookmarks"] if item["id"] == "1")
            self.assertEqual(bookmark["local"]["note"], "worth revisiting")
            self.assertEqual(set(bookmark["local"]["tags"]), {"favorite", "otel"})
            self.assertEqual(bookmark["local"]["rating"], 5)
            self.assertTrue(bookmark["local"]["hidden"])


if __name__ == "__main__":
    unittest.main()
