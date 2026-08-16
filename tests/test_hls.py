import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from watchdog_app.hls import HLSStreamer


class HLSStreamerTests(unittest.TestCase):
    def test_command_has_native_video_and_microphone_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            streamer = HLSStreamer(
                ffmpeg="/tmp/ffmpeg",
                directory=Path(directory),
                width=1760,
                height=1328,
                fps=15,
                microphone_index=2,
            )
            command = streamer.command()
        self.assertIn("h264_videotoolbox", command)
        self.assertIn(":2", command)
        self.assertIn("aac", command)
        self.assertIn("3500k", command)
        self.assertIn(
            "delete_segments+omit_endlist+independent_segments+temp_file", command
        )
        self.assertIn("1760x1328", command)

    def test_video_only_command_omits_audio_input(self):
        with tempfile.TemporaryDirectory() as directory:
            streamer = HLSStreamer(
                ffmpeg="/tmp/ffmpeg",
                directory=Path(directory),
                width=640,
                height=480,
                fps=15,
                microphone_index=0,
                audio_enabled=False,
            )
            command = streamer.command()
        self.assertNotIn("avfoundation", command)
        self.assertNotIn("aac", command)
        self.assertNotIn(":0", command)

    def test_prepare_directory_only_removes_live_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            stream_dir = Path(directory)
            (stream_dir / "live.m3u8").write_text("old", encoding="utf-8")
            (stream_dir / "segment_000001.ts").write_bytes(b"old")
            keep = stream_dir / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            streamer = HLSStreamer(
                ffmpeg="/tmp/ffmpeg",
                directory=stream_dir,
                width=640,
                height=480,
                fps=15,
                microphone_index=0,
            )
            streamer._prepare_directory()
            self.assertFalse((stream_dir / "live.m3u8").exists())
            self.assertFalse((stream_dir / "segment_000001.ts").exists())
            self.assertTrue(keep.exists())

    def test_start_and_stop_finalize_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            process = MagicMock()
            process.stdin = MagicMock()
            process.poll.return_value = None
            process.wait.return_value = 0
            streamer = HLSStreamer(
                ffmpeg="/tmp/ffmpeg",
                directory=Path(directory),
                width=640,
                height=480,
                fps=15,
                microphone_index=0,
                log=lambda _message: None,
            )
            with patch("watchdog_app.hls.subprocess.Popen", return_value=process):
                streamer.start()
                streamer.stop()
            process.stdin.close.assert_called_once()
            process.wait.assert_called_once_with(timeout=8)


if __name__ == "__main__":
    unittest.main()
