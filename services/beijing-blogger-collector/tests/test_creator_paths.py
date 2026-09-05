from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mx_agent.creator_paths import (
    creator_folder_name,
    ensure_creator_directories,
    rename_creator_directory,
)


class CreatorPathTests(unittest.TestCase):
    def test_creates_one_readable_folder_tree_per_creator(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = ensure_creator_directories("艾丽的无废话财经", data_root=root)

            self.assertEqual(paths["root"], root / "艾丽的无废话财经")
            self.assertTrue(paths["videos"].is_dir())
            self.assertTrue(paths["images"].is_dir())
            self.assertTrue(paths["originals"].is_dir())

    def test_sanitizes_windows_folder_characters(self) -> None:
        self.assertEqual(creator_folder_name('财经/A:*?'), "财经_A_")

    def test_rename_keeps_existing_creator_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = ensure_creator_directories("旧名字", data_root=root)
            sample = paths["videos"] / "sample.mp4"
            sample.write_bytes(b"video")

            renamed = rename_creator_directory("旧名字", "新名字", data_root=root)

            self.assertFalse((root / "旧名字").exists())
            self.assertEqual((renamed / "视频" / "sample.mp4").read_bytes(), b"video")


if __name__ == "__main__":
    unittest.main()
