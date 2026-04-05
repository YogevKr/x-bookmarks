import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bookmark_paths import config_path, read_only_mode, resolve_base_dir
from bookmark_query import default_paths


class RuntimePathsTest(unittest.TestCase):
    def test_default_paths_follow_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(temp_dir),
                    "XDG_CONFIG_HOME": str(temp_dir / ".config-root"),
                    "X_BOOKMARKS_HOME": "",
                    "X_BOOKMARKS_READ_ONLY": "",
                    "X_BOOKMARKS_CONFIG": "",
                },
                clear=False,
            ):
                with mock.patch("pathlib.Path.cwd", return_value=temp_dir):
                    paths = default_paths()
            self.assertEqual(paths.base_dir, temp_dir.resolve())

    def test_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            with mock.patch.dict(os.environ, {"X_BOOKMARKS_HOME": str(temp_dir)}, clear=False):
                resolved = resolve_base_dir()
                paths = default_paths()
            self.assertEqual(resolved, temp_dir.resolve())
            self.assertEqual(paths.base_dir, temp_dir.resolve())

    def test_read_only_env_flag(self) -> None:
        with mock.patch.dict(os.environ, {"X_BOOKMARKS_READ_ONLY": "1"}, clear=False):
            self.assertTrue(read_only_mode())

    def test_config_file_sets_base_dir_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            base_dir = home / "shared-bookmarks"
            xdg = home / ".config-root"
            config_dir = xdg / "x-bookmarks"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps({"base_dir": str(base_dir), "read_only": True}),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": str(xdg),
                    "X_BOOKMARKS_HOME": "",
                    "X_BOOKMARKS_READ_ONLY": "",
                },
                clear=False,
            ):
                self.assertEqual(resolve_base_dir(), base_dir.resolve())
                self.assertTrue(read_only_mode())
                self.assertEqual(config_path(), (config_dir / "config.json").resolve())

    def test_env_still_overrides_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config_dir = home / ".config-root" / "x-bookmarks"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps({"base_dir": str(home / "from-config"), "read_only": True}),
                encoding="utf-8",
            )
            override = home / "from-env"
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": str(home / ".config-root"),
                    "X_BOOKMARKS_HOME": str(override),
                    "X_BOOKMARKS_READ_ONLY": "0",
                },
                clear=False,
            ):
                self.assertEqual(resolve_base_dir(), override.resolve())
                self.assertFalse(read_only_mode())


if __name__ == "__main__":
    unittest.main()
