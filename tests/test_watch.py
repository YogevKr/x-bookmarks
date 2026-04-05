import unittest
from unittest import mock

from bookmark_watch import watch_once


class WatchTest(unittest.TestCase):
    def test_watch_once_refreshes_stale_index(self) -> None:
        with mock.patch("bookmark_watch.get_index_status", return_value={"stale": True, "reasons": ["source_files_changed"]}):
            with mock.patch("bookmark_watch.refresh_index", return_value={"fresh": True, "doc_count": 3, "built_at": "2026-04-05T00:00:00+00:00"}):
                result = watch_once()
        self.assertEqual(result["action"], "refreshed")
        self.assertEqual(result["doc_count"], 3)

    def test_watch_once_waits_without_sources(self) -> None:
        with mock.patch("bookmark_watch.get_index_status", side_effect=FileNotFoundError):
            result = watch_once()
        self.assertEqual(result["action"], "waiting")
        self.assertEqual(result["reason"], "no_source_files")
