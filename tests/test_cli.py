import os
import unittest
from unittest import mock

from cli import _augment_result_with_link_context, _auto_refresh, _require_writable, build_parser


class CliSurfaceTest(unittest.TestCase):
    def test_generate_command_removed(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["generate"])

    def test_sync_obsidian_flag_removed(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["sync", "--obsidian"])

    def test_sync_still_parses_core_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync", "--reconcile-only", "--json"])
        self.assertTrue(args.reconcile_only)
        self.assertTrue(args.json)
        self.assertTrue(args.extract)

    def test_sync_can_disable_default_extraction(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync", "--no-extract"])
        self.assertFalse(args.extract)

    def test_export_x_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "export-x",
            "--sync",
            "--no-extract",
            "--user-data-dir",
            "/tmp/x-profile",
            "--debug-port",
            "9333",
            "--json",
        ])
        self.assertTrue(args.sync)
        self.assertFalse(args.extract)
        self.assertEqual(str(args.user_data_dir), "/tmp/x-profile")
        self.assertEqual(args.debug_port, 9333)
        self.assertTrue(args.json)

    def test_search_explain_and_doctor_parse(self) -> None:
        parser = build_parser()
        search_args = parser.parse_args(["search", "observability", "--explain"])
        doctor_args = parser.parse_args(["doctor", "--json"])
        watch_args = parser.parse_args(["watch", "--once", "--json"])
        stale_args = parser.parse_args(["stale-check", "--max-age-hours", "36", "--notify", "--quiet", "--json"])
        version_args = parser.parse_args(["version", "--json"])
        self.assertTrue(search_args.explain)
        self.assertTrue(doctor_args.json)
        self.assertTrue(watch_args.once)
        self.assertTrue(watch_args.json)
        self.assertEqual(stale_args.max_age_hours, 36)
        self.assertTrue(stale_args.notify)
        self.assertTrue(stale_args.quiet)
        self.assertTrue(stale_args.json)
        self.assertTrue(version_args.json)

    def test_launchd_parse(self) -> None:
        parser = build_parser()
        install_args = parser.parse_args(["launchd", "install", "--interval", "10", "--base-dir", "/tmp/bookmarks", "--json"])
        export_install_args = parser.parse_args([
            "launchd",
            "install-export",
            "--interval",
            "3600",
            "--user-data-dir",
            "/tmp/x-profile",
            "--json",
        ])
        stale_install_args = parser.parse_args([
            "launchd",
            "install-stale-check",
            "--interval",
            "7200",
            "--max-age-hours",
            "40",
            "--json",
        ])
        status_args = parser.parse_args(["launchd", "status", "--json"])
        stale_status_args = parser.parse_args(["launchd", "stale-check-status", "--json"])
        uninstall_args = parser.parse_args(["launchd", "uninstall"])
        stale_uninstall_args = parser.parse_args(["launchd", "uninstall-stale-check"])
        self.assertEqual(install_args.interval, 10)
        self.assertEqual(str(install_args.base_dir), "/tmp/bookmarks")
        self.assertTrue(install_args.json)
        self.assertEqual(export_install_args.interval, 3600)
        self.assertEqual(str(export_install_args.user_data_dir), "/tmp/x-profile")
        self.assertTrue(export_install_args.json)
        self.assertEqual(stale_install_args.interval, 7200)
        self.assertEqual(stale_install_args.max_age_hours, 40)
        self.assertTrue(stale_install_args.json)
        self.assertEqual(status_args.launchd_command, "status")
        self.assertEqual(stale_status_args.launchd_command, "stale-check-status")
        self.assertEqual(uninstall_args.launchd_command, "uninstall")
        self.assertEqual(stale_uninstall_args.launchd_command, "uninstall-stale-check")

    def test_config_parse(self) -> None:
        parser = build_parser()
        show_args = parser.parse_args(["config", "show", "--json"])
        init_args = parser.parse_args(["config", "init", "--reader", "--icloud", "--force"])
        self.assertEqual(show_args.config_command, "show")
        self.assertTrue(init_args.reader)
        self.assertTrue(init_args.icloud)
        self.assertTrue(init_args.force)

    def test_failure_and_metadata_parse(self) -> None:
        parser = build_parser()
        failures_args = parser.parse_args(["extract-failures", "--domain", "github.com", "--json"])
        retry_args = parser.parse_args(["retry-failures", "--terminal", "--limit", "5"])
        export_args = parser.parse_args(["metadata-export", "--output", "/tmp/meta.json"])
        import_args = parser.parse_args(["metadata-import", "--input", "/tmp/meta.json", "--replace"])
        self.assertEqual(failures_args.domain, "github.com")
        self.assertTrue(failures_args.json)
        self.assertTrue(retry_args.terminal)
        self.assertEqual(retry_args.limit, 5)
        self.assertEqual(str(export_args.output), "/tmp/meta.json")
        self.assertTrue(import_args.replace)

    def test_local_metadata_commands_parse(self) -> None:
        parser = build_parser()
        note_args = parser.parse_args(["note", "123", "keep", "this"])
        tag_args = parser.parse_args(["tag", "123", "favorite", "otel"])
        rate_args = parser.parse_args(["rate", "123", "5"])
        hide_args = parser.parse_args(["hide", "123", "456"])
        self.assertEqual(note_args.text, ["keep", "this"])
        self.assertEqual(tag_args.tags, ["favorite", "otel"])
        self.assertEqual(rate_args.value, 5)
        self.assertEqual(hide_args.ids, ["123", "456"])

    def test_remove_and_restore_parse_ids(self) -> None:
        parser = build_parser()
        remove_args = parser.parse_args(["remove", "123", "456", "--json"])
        restore_args = parser.parse_args(["restore", "123"])
        restore_all_args = parser.parse_args(["restore", "--all"])
        self.assertEqual(remove_args.ids, ["123", "456"])
        self.assertTrue(remove_args.json)
        self.assertEqual(restore_args.ids, ["123"])
        self.assertTrue(restore_all_args.all)

    def test_show_and_list_parse_deleted(self) -> None:
        parser = build_parser()
        show_args = parser.parse_args(["show", "123", "--deleted"])
        list_args = parser.parse_args(["list", "--deleted"])
        hidden_args = parser.parse_args(["list", "--hidden"])
        self.assertTrue(show_args.deleted)
        self.assertTrue(list_args.deleted)
        self.assertTrue(hidden_args.hidden)

    def test_show_and_context_parse_fetch_link(self) -> None:
        parser = build_parser()
        show_args = parser.parse_args(["show", "123", "--fetch-link"])
        context_args = parser.parse_args(["context", "123", "--fetch-link"])
        self.assertTrue(show_args.fetch_link)
        self.assertTrue(context_args.fetch_link)

    def test_read_only_env_disables_auto_refresh(self) -> None:
        with mock.patch.dict(os.environ, {"X_BOOKMARKS_READ_ONLY": "1"}, clear=False):
            self.assertFalse(_auto_refresh(mock.Mock(no_refresh=False)))

    def test_read_only_env_blocks_write_actions(self) -> None:
        with mock.patch.dict(os.environ, {"X_BOOKMARKS_READ_ONLY": "1"}, clear=False):
            with self.assertRaises(SystemExit) as exc:
                _require_writable("sync")
        self.assertIn("sync is disabled", str(exc.exception))

    def test_extract_parses_force_and_targeting(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["extract", "--force", "--limit", "5", "--bookmark-id", "123"])
        self.assertTrue(args.force)
        self.assertEqual(args.limit, 5)
        self.assertEqual(args.bookmark_id, "123")

    def test_augment_result_fetches_link_on_demand(self) -> None:
        result = {
            "id": "1",
            "external_urls": ["https://example.com/post"],
            "linked_title": "",
            "linked_description": "",
            "linked_preview": "",
        }
        with mock.patch("cli._extract_link_context_from_urls", return_value={
            "url": "https://example.com/post",
            "title": "Example article",
            "description": "Short description",
            "content": "Longer linked content",
        }):
            enriched = _augment_result_with_link_context(result, fetch_link=True)
        self.assertEqual(enriched["linked_source"], "fetched")
        self.assertEqual(enriched["linked_title"], "Example article")
        self.assertEqual(enriched["linked_url"], "https://example.com/post")


if __name__ == "__main__":
    unittest.main()
