import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bookmark_query import IndexPaths
from bookmark_watch import watch_once


class WatchTest(unittest.TestCase):
    def test_watch_once_refreshes_stale_index(self) -> None:
        with mock.patch("bookmark_watch.get_index_status", return_value={"stale": True, "reasons": ["source_files_changed"]}):
            with mock.patch("bookmark_watch.refresh_index", return_value={"fresh": True, "doc_count": 3, "built_at": "2026-04-05T00:00:00+00:00"}):
                result = watch_once()
        self.assertEqual(result["action"], "refreshed")
        self.assertEqual(result["doc_count"], 3)

    def test_watch_once_waits_without_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = IndexPaths(base_dir=Path(tmp))
            with mock.patch("bookmark_watch.get_index_status", side_effect=FileNotFoundError):
                result = watch_once(paths=paths)
        self.assertEqual(result["action"], "waiting")
        self.assertEqual(result["reason"], "no_source_files")

    def test_watch_once_persists_watch_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = IndexPaths(base_dir=Path(tmp))
            with mock.patch(
                "bookmark_watch.get_index_status",
                return_value={"stale": False, "fresh": True, "doc_count": 3, "built_at": "2026-04-05T00:00:00+00:00"},
            ):
                result = watch_once(paths=paths)
            self.assertEqual(result["action"], "idle")
            state = json.loads(paths.watch_state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["last_action"], "idle")
            self.assertIn("last_watch_tick", state)
