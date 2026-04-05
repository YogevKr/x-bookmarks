import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bookmark_paths import read_only_mode, resolve_base_dir
from bookmark_query import default_paths


class RuntimePathsTest(unittest.TestCase):
    def test_default_paths_follow_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            with mock.patch.dict(os.environ, {}, clear=False):
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


if __name__ == "__main__":
    unittest.main()
