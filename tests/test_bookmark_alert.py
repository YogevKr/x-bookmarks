import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from bookmark_alert import check_stale_source_export
from bookmark_query import IndexPaths


def _write_bookmarks(path: Path, *, export_date: str) -> None:
    payload = {
        "exportDate": export_date,
        "bookmarks": [
            {
                "id": "1",
                "handle": "@otel",
                "author": "OpenTelemetry",
                "text": "Observability with OpenTelemetry",
                "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
                "urls": [],
                "hashtags": [],
                "media": [],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class BookmarkAlertTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.paths = IndexPaths(base_dir=self.base)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_stale_check_ok_when_source_export_is_recent(self) -> None:
        _write_bookmarks(self.base / "bookmarks.json", export_date="2026-04-25T17:00:00Z")
        with mock.patch("bookmark_alert._now", return_value=datetime(2026, 4, 25, 18, 0, tzinfo=UTC)):
            result = check_stale_source_export(max_age_hours=36, paths=self.paths)
        self.assertTrue(result["ok"])
        self.assertFalse(result["stale"])
        self.assertEqual(result["reason"], "fresh")

    def test_stale_check_alerts_and_suppresses_repeated_notifications(self) -> None:
        _write_bookmarks(self.base / "bookmarks.json", export_date="2026-04-20T17:00:00Z")
        state_file = self.base / ".x-bookmarks" / "stale-check-state.json"

        with mock.patch("bookmark_alert._now", return_value=datetime(2026, 4, 25, 18, 0, tzinfo=UTC)):
            with mock.patch("bookmark_alert._run_notification", return_value=(True, None)) as notify:
                first = check_stale_source_export(
                    max_age_hours=36,
                    notify=True,
                    alert_every_hours=24,
                    state_file=state_file,
                    paths=self.paths,
                )
                second = check_stale_source_export(
                    max_age_hours=36,
                    notify=True,
                    alert_every_hours=24,
                    state_file=state_file,
                    paths=self.paths,
                )

        self.assertFalse(first["ok"])
        self.assertEqual(first["reason"], "source_age_exceeded")
        self.assertTrue(first["notified"])
        self.assertFalse(second["ok"])
        self.assertFalse(second["notified"])
        self.assertEqual(second["notification_reason"], "recently_alerted")
        self.assertEqual(notify.call_count, 1)
