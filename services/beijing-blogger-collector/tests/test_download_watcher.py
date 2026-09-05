from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mx_agent.download_watcher import DownloadWatcher
from mx_agent.importer import published_at_from_name


class _Importer:
    def __init__(self, folders: list[Path]):
        self._folders = folders

    def download_dirs(self) -> list[Path]:
        return self._folders


class _ImportingImporter(_Importer):
    def import_file_as_video(self, path: Path, **_kwargs):
        return {"video_id": 77, "created": True, "message": "已导入"}


class _Storage:
    def __init__(self, known_paths: set[str] | None = None):
        self.known_paths = known_paths or set()

    def known_asset_source_paths(self) -> set[str]:
        return self.known_paths


class DownloadWatcherTests(unittest.TestCase):
    def test_canonical_normalized_filename_time_is_supported(self) -> None:
        self.assertEqual(
            published_at_from_name("20260730_1556_7668224399283757817"),
            "2026-07-30T07:56:00+00:00",
        )

    def test_automatic_watch_excludes_general_downloads_when_model_folder_is_configured(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-") as root:
            base = Path(root)
            downloads = base / "Downloads"
            model_folder = base / "模型视频"
            downloads.mkdir()
            model_folder.mkdir()
            unrelated = downloads / "ChatGPT Image.png"
            intended = model_folder / "202607061300.mp4"
            unrelated.write_bytes(b"not model content")
            intended.write_bytes(b"model content")

            with patch.dict(
                os.environ,
                {"MX_AGENT_IMPORT_EXISTING_DIRS": str(model_folder)},
                clear=False,
            ):
                watcher = DownloadWatcher(
                    settings=None,
                    storage=None,
                    importer=_Importer([downloads, model_folder]),
                    analyzer=None,
                )
                candidates = watcher._candidate_paths()

            self.assertEqual([intended.resolve()], candidates)
            self.assertNotIn(unrelated.resolve(), candidates)

    def test_dedicated_folders_map_to_the_correct_account(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-accounts-") as root:
            base = Path(root)
            model_folder = base / "模型视频"
            world_folder = base / "模型哥视频"
            model_folder.mkdir()
            world_folder.mkdir()
            model_file = model_folder / "202607220800.mp4"
            world_file = world_folder / "202607220900.mp4"
            model_file.write_bytes(b"model")
            world_file.write_bytes(b"world")

            with patch.dict(
                os.environ,
                {"MX_AGENT_IMPORT_EXISTING_DIRS": f"{model_folder};{world_folder}"},
                clear=False,
            ):
                watcher = DownloadWatcher(
                    settings=SimpleNamespace(source_account_name="模型先生"),
                    storage=None,
                    importer=_Importer([model_folder, world_folder]),
                    analyzer=None,
                )
                self.assertEqual(watcher._account_for_path(model_file), "模型先生")
                self.assertEqual(watcher._account_for_path(world_file), "模型哥看世界")

    def test_dated_model_files_are_imported_by_published_time_not_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-timeline-") as root:
            model_folder = Path(root) / "模型视频"
            model_folder.mkdir()
            old_video = model_folder / "202401011200.mp4"
            middle_video = model_folder / "202501011200.mp4"
            new_video = model_folder / "202601011200.mp4"
            for path in (old_video, middle_video, new_video):
                path.write_bytes(path.name.encode("utf-8"))

            os.utime(old_video, (300, 300))
            os.utime(middle_video, (200, 200))
            os.utime(new_video, (100, 100))

            with patch.dict(
                os.environ,
                {"MX_AGENT_IMPORT_EXISTING_DIRS": str(model_folder)},
                clear=False,
            ):
                watcher = DownloadWatcher(
                    settings=SimpleNamespace(source_account_name="模型先生"),
                    storage=None,
                    importer=_Importer([model_folder]),
                    analyzer=None,
                )
                candidates = watcher._candidate_paths()

            self.assertEqual(
                [new_video.resolve(), middle_video.resolve(), old_video.resolve()],
                candidates,
            )

    def test_creator_library_is_watched_recursively_and_maps_creator_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-creators-") as root:
            library = Path(root) / "博主数据"
            alice_video_dir = library / "艾丽的无废话财经" / "视频"
            bk_image_dir = library / "BK(老号)" / "图片"
            alice_video_dir.mkdir(parents=True)
            bk_image_dir.mkdir(parents=True)
            alice_video = alice_video_dir / "202608191000.mp4"
            bk_image = bk_image_dir / "202608191100.jpg"
            alice_video.write_bytes(b"alice")
            bk_image.write_bytes(b"bk")

            with patch.dict(
                os.environ,
                {"MX_AGENT_IMPORT_EXISTING_DIRS": str(library)},
                clear=False,
            ):
                watcher = DownloadWatcher(
                    settings=SimpleNamespace(source_account_name="BK(老号)"),
                    storage=None,
                    importer=_Importer([library]),
                    analyzer=None,
                )
                candidates = watcher._candidate_paths()
                self.assertEqual(watcher._account_for_path(alice_video), "艾丽的无废话财经")
                self.assertEqual(watcher._account_for_path(bk_image), "BK(老号)")

            self.assertEqual({alice_video.resolve(), bk_image.resolve()}, set(candidates))

    def test_generated_ocr_images_are_never_watched_as_new_works(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-ocr-") as root:
            library = Path(root) / "博主数据"
            image_dir = library / "贵族之路" / "图片"
            image_dir.mkdir(parents=True)
            original = image_dir / "20260819_1840_7675690762692815091_01.webp"
            generated = image_dir / "20260819_1840_7675690762692815091_01_ocr.png"
            original.write_bytes(b"original")
            generated.write_bytes(b"generated")

            with patch.dict(
                os.environ,
                {"MX_AGENT_IMPORT_EXISTING_DIRS": str(library)},
                clear=False,
            ):
                watcher = DownloadWatcher(
                    settings=SimpleNamespace(source_account_name="贵族之路"),
                    storage=_Storage(),
                    importer=_Importer([library]),
                    analyzer=None,
                )
                candidates = watcher._candidate_paths()

            self.assertEqual([original.resolve()], candidates)

    def test_known_creator_asset_is_skipped_before_import_side_effects(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-known-") as root:
            path = Path(root) / "202608191840.mp4"
            path.write_bytes(b"known creator asset")
            storage = _Storage({str(path.resolve()).casefold()})
            watcher = DownloadWatcher(
                settings=SimpleNamespace(source_account_name="贵族之路"),
                storage=storage,
                importer=_Importer([Path(root)]),
                analyzer=None,
            )

            self.assertEqual("skipped", watcher._observe_path(path))
            self.assertNotIn(str(path), watcher._pending)

    def test_new_video_import_notifies_auto_transcription(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mx-watcher-callback-") as root:
            path = Path(root) / "20260821_1200_1234567890.mp4"
            path.write_bytes(b"new video")
            received: list[int] = []
            watcher = DownloadWatcher(
                settings=SimpleNamespace(
                    source_account_name="测试博主",
                    auto_analyze_new_videos=False,
                ),
                storage=_Storage(),
                importer=_ImportingImporter([Path(root)]),
                analyzer=None,
                on_video_imported=received.append,
            )
            watcher.stable_seconds = 0

            watcher._observe_path(path)
            result = watcher._observe_path(path)

            self.assertEqual(result, "imported")
            self.assertEqual(received, [77])


if __name__ == "__main__":
    unittest.main()
