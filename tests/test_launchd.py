import plistlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from bookmark_launchd import (
    _entrypoint_args,
    build_export_launch_agent_plist,
    build_launch_agent_plist,
    build_stale_check_launch_agent_plist,
    install_stale_check_launch_agent,
    launch_agent_path,
)


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
        self.assertEqual(payload["ThrottleInterval"], 30)

    def test_launch_agent_path_uses_launchagents_dir(self) -> None:
        path = launch_agent_path()
        self.assertTrue(str(path).endswith("/Library/LaunchAgents/com.yogevkr.x-bookmarks.watch.plist"))

    def test_entrypoint_prefers_absolute_current_x_bookmarks(self) -> None:
        with TemporaryDirectory() as tmp:
            executable = Path(tmp) / "x-bookmarks"
            executable.touch()
            with mock.patch.object(sys, "argv", [str(executable)]):
                self.assertEqual(_entrypoint_args(), [str(executable)])

    def test_plist_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            with mock.patch("bookmark_launchd.sys.platform", "darwin"):
                with mock.patch("bookmark_launchd._entrypoint_args", return_value=["/usr/local/bin/x-bookmarks"]):
                    payload = build_launch_agent_plist(base_dir=base_dir, interval=5.0, quiet=False)
            encoded = plistlib.dumps(payload)
            decoded = plistlib.loads(encoded)
        self.assertEqual(decoded["ProgramArguments"], ["/usr/local/bin/x-bookmarks", "watch", "--interval", "5"])

    def test_build_export_launch_agent_plist(self) -> None:
        with TemporaryDirectory() as tmp:
            base_dir = Path(tmp) / "base"
            profile_dir = Path(tmp) / "profile"
            with mock.patch("bookmark_launchd.sys.platform", "darwin"):
                with mock.patch("bookmark_launchd._entrypoint_args", return_value=["/usr/local/bin/x-bookmarks"]):
                    payload = build_export_launch_agent_plist(
                        base_dir=base_dir,
                        user_data_dir=profile_dir,
                        interval=3600,
                        debug_port=9333,
                        timeout=120,
                        quiet=True,
                    )
        self.assertEqual(payload["Label"], "com.yogevkr.x-bookmarks.export")
        self.assertEqual(payload["StartInterval"], 3600)
        self.assertEqual(payload["ThrottleInterval"], 300)
        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/usr/local/bin/x-bookmarks",
                "export-x",
                "--sync",
                "--no-extract",
                "--debug-port",
                "9333",
                "--timeout",
                "120",
                "--user-data-dir",
                str(profile_dir),
                "--quiet",
            ],
        )

    def test_build_stale_check_launch_agent_plist(self) -> None:
        with TemporaryDirectory() as tmp:
            base_dir = Path(tmp) / "base"
            with mock.patch("bookmark_launchd.sys.platform", "darwin"):
                with mock.patch("bookmark_launchd._entrypoint_args", return_value=["/usr/local/bin/x-bookmarks"]):
                    payload = build_stale_check_launch_agent_plist(
                        base_dir=base_dir,
                        interval=7200,
                        max_age_hours=40,
                        alert_every_hours=12,
                        quiet=True,
                    )
        self.assertEqual(payload["Label"], "com.yogevkr.x-bookmarks.stale-check")
        self.assertEqual(payload["StartInterval"], 7200)
        self.assertEqual(payload["ThrottleInterval"], 300)
        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/usr/local/bin/x-bookmarks",
                "stale-check",
                "--max-age-hours",
                "40",
                "--alert-every-hours",
                "12",
                "--notify",
                "--quiet",
            ],
        )

    def test_install_stale_check_does_not_kickstart(self) -> None:
        with mock.patch("bookmark_launchd.sys.platform", "darwin"):
            with mock.patch("bookmark_launchd.write_stale_check_launch_agent", return_value=Path("/tmp/stale.plist")):
                with mock.patch("bookmark_launchd._launchctl_domain", return_value="gui/501"):
                    with mock.patch("bookmark_launchd.launch_agent_status", return_value={"loaded": True}):
                        with mock.patch("bookmark_launchd.subprocess.run") as run:
                            install_stale_check_launch_agent()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:2], ["launchctl", "bootout"])
        self.assertEqual(commands[1][:2], ["launchctl", "bootstrap"])
        self.assertFalse(any("kickstart" in command for command in commands))
