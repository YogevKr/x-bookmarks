import unittest

from cli import build_parser


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


if __name__ == "__main__":
    unittest.main()
