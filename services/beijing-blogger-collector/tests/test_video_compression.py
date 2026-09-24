from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mx_agent.downloader_engine.douyin_core import (
    BLOGGER_VIDEO_SCALE_FILTER,
    compact_video_command,
)


class VideoCompressionTests(unittest.TestCase):
    def test_phone_video_halves_dimensions_and_keeps_a_readable_floor(self) -> None:
        self.assertEqual(
            BLOGGER_VIDEO_SCALE_FILTER,
            "scale=trunc(max(iw/2\\,240)/2)*2:-2:flags=lanczos",
        )

    def test_command_keeps_iphone_compatible_audio_video_and_fast_start(self) -> None:
        command = compact_video_command(
            "/usr/bin/ffmpeg",
            Path("source.mp4"),
            Path("compact.mp4"),
        )
        self.assertEqual(command.count("-i"), 1)
        self.assertIn("0:a:0", command)
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("aac", command)
        self.assertIn("64k", command)
        self.assertIn("+faststart", command)

    def test_real_ffmpeg_generates_half_size_phone_video(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("ffmpeg/ffprobe is not installed")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp4"
            compact = root / "compact.mp4"
            subprocess.run(
                [
                    ffmpeg, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=10:d=0.3",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=0.3",
                    "-shortest", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                compact_video_command(ffmpeg, source, compact),
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            probe = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-show_entries", "stream=codec_type,codec_name,width,height,pix_fmt",
                    "-of", "json", str(compact),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        streams = json.loads(probe.stdout)["streams"]
        video = next(item for item in streams if item["codec_type"] == "video")
        audio = next(item for item in streams if item["codec_type"] == "audio")
        self.assertEqual((video["width"], video["height"]), (360, 640))
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(audio["codec_name"], "aac")


if __name__ == "__main__":
    unittest.main()
