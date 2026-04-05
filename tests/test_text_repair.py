import json
import tempfile
import unittest
from pathlib import Path

from bookmark_query import IndexPaths, refresh_index, show_bookmark
from bookmark_sync import sync_bookmarks
from text_repair import repair_text, repair_value


def mojibake(text: str) -> str:
    return text.encode("utf-8").decode("latin-1")


class TextRepairTest(unittest.TestCase):
    def test_repair_text_round_trips_hebrew(self) -> None:
        original = "אגב זה סיפור יותר טוב"
        self.assertEqual(repair_text(mojibake(original)), original)

    def test_repair_value_recurses(self) -> None:
        payload = {
            "author": mojibake("התלתל"),
            "nested": [{"text": mojibake("בן אדם")}],
        }
        repaired = repair_value(payload)
        self.assertEqual(repaired["author"], "התלתל")
        self.assertEqual(repaired["nested"][0]["text"], "בן אדם")

    def test_sync_repairs_imported_payload(self) -> None:
        original = {
            "exportDate": "2026-04-05T00:00:00Z",
            "source": "bookmark",
            "bookmarks": [
                {
                    "id": "1",
                    "author": mojibake("התלתל"),
                    "handle": "@taltimes2",
                    "timestamp": "Sat Mar 28 17:36:47 +0000 2026",
                    "text": mojibake("אגב זה סיפור יותר טוב"),
                    "media": [],
                    "hashtags": [],
                    "urls": ["https://example.com/story"],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            paths = IndexPaths(base_dir=base)
            import_file = base / "import.json"
            import_file.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            sync_bookmarks(input_file=import_file, paths=paths)
            refresh_index(paths=paths)
            shown = show_bookmark("1", paths=paths)

            self.assertEqual(shown["author"], "התלתל")
            self.assertEqual(shown["text"], "אגב זה סיפור יותר טוב")


if __name__ == "__main__":
    unittest.main()
