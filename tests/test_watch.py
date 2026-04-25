import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bookmark_query import INDEX_VERSION, IndexPaths
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

    def test_watch_once_ignores_watch_state_read_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = IndexPaths(base_dir=Path(tmp))
            paths.data_dir.mkdir(parents=True, exist_ok=True)
            paths.watch_state_file.write_text("{}", encoding="utf-8")
            with mock.patch("pathlib.Path.open", side_effect=OSError(11, "Resource deadlock avoided")):
                with mock.patch(
                    "bookmark_watch.get_index_status",
                    return_value={"stale": False, "fresh": True, "doc_count": 3, "built_at": "2026-04-05T00:00:00+00:00"},
                ):
                    result = watch_once(paths=paths)
            self.assertEqual(result["action"], "idle")

    def test_watch_once_uses_manifest_mtimes_for_idle_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = IndexPaths(base_dir=Path(tmp))
            paths.data_dir.mkdir(parents=True, exist_ok=True)
            paths.bookmarks_file.write_text('{"bookmarks": []}', encoding="utf-8")
            paths.index_db.write_bytes(b"")
            source_state = {
                "files": {
                    "bookmarks": {"exists": True, "mtime_ns": paths.bookmarks_file.stat().st_mtime_ns},
                    "enriched": {"exists": False, "mtime_ns": None},
                    "categorized": {"exists": False, "mtime_ns": None},
                }
            }
            paths.manifest_file.write_text(
                json.dumps({
                    "version": INDEX_VERSION,
                    "built_at": "2026-04-05T00:00:00+00:00",
                    "doc_count": 0,
                    "source_state": source_state,
                }),
                encoding="utf-8",
            )

            with mock.patch("bookmark_watch.get_index_status", side_effect=AssertionError("slow path should not run")):
                result = watch_once(paths=paths)

            self.assertEqual(result["action"], "idle")
            self.assertEqual(result["doc_count"], 0)
