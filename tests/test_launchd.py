import plistlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from bookmark_launchd import build_launch_agent_plist, launch_agent_path


class LaunchdTest(unittest.TestCase):
    def test_build_launch_agent_plist_sets_base_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            with mock.patch("bookmark_launchd.sys.platform", "darwin"):
                with mock.patch("bookmark_launchd._entrypoint_args", return_value=["/usr/local/bin/x-bookmarks"]):
                    payload = build_launch_agent_plist(base_dir=base_dir, interval=9.0, quiet=True)
        self.assertEqual(payload["Label"], "com.yogevkr.x-bookmarks.watch")
        self.assertEqual(payload["EnvironmentVariables"]["X_BOOKMARKS_HOME"], str(base_dir.resolve()))
        self.assertEqual(payload["ProgramArguments"], ["/usr/local/bin/x-bookmarks", "watch", "--interval", "9", "--quiet"])
        self.assertEqual(payload["WorkingDirectory"], str(base_dir.resolve()))

    def test_launch_agent_path_uses_launchagents_dir(self) -> None:
        path = launch_agent_path()
        self.assertTrue(str(path).endswith("/Library/LaunchAgents/com.yogevkr.x-bookmarks.watch.plist"))

    def test_plist_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            with mock.patch("bookmark_launchd.sys.platform", "darwin"):
                with mock.patch("bookmark_launchd._entrypoint_args", return_value=["/usr/local/bin/x-bookmarks"]):
                    payload = build_launch_agent_plist(base_dir=base_dir, interval=5.0, quiet=False)
            encoded = plistlib.dumps(payload)
            decoded = plistlib.loads(encoded)
        self.assertEqual(decoded["ProgramArguments"], ["/usr/local/bin/x-bookmarks", "watch", "--interval", "5"])
