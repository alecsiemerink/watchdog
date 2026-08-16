from __future__ import annotations

import queue
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

Log = Callable[[str], None]


class HLSStreamer:
    """Encodes camera frames plus microphone audio into a rolling HLS stream."""

    def __init__(
        self,
        *,
        ffmpeg: str,
        directory: Path,
        width: int,
        height: int,
        fps: int,
        microphone_index: int,
        audio_enabled: bool = True,
        log: Log = print,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.directory = directory
        self.width = width
        self.height = height
        self.fps = fps
        self.microphone_index = microphone_index
        self.audio_enabled = audio_enabled
        self.log = log
        self.process: subprocess.Popen | None = None
        self._frames: queue.Queue[bytes | None] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._failed = False

    @property
    def playlist_path(self) -> Path:
        return self.directory / "live.m3u8"

    def command(self) -> list[str]:
        command = [
            self.ffmpeg,
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgr24",
            "-video_size",
            f"{self.width}x{self.height}",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
        ]
        if self.audio_enabled:
            command.extend(
                [
                    "-thread_queue_size",
                    "1024",
                    "-f",
                    "avfoundation",
                    "-i",
                    f":{self.microphone_index}",
                ]
            )
        command.extend(["-map", "0:v:0"])
        if self.audio_enabled:
            command.extend(["-map", "1:a:0"])
        command.extend(
            [
                "-c:v",
                "h264_videotoolbox",
                "-b:v",
                "3500k",
                "-maxrate",
                "4500k",
                "-bufsize",
                "7000k",
                "-g",
                str(self.fps),
                "-keyint_min",
                str(self.fps),
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if self.audio_enabled:
            command.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-af",
                    "aresample=async=1:first_pts=0",
                ]
            )
        command.extend(
            [
                "-f",
                "hls",
                "-hls_time",
                "1",
                "-hls_list_size",
                "6",
                "-hls_delete_threshold",
                "2",
                "-hls_allow_cache",
                "0",
                "-hls_flags",
                "delete_segments+omit_endlist+independent_segments+temp_file",
                "-hls_segment_filename",
                str(self.directory / "segment_%06d.ts"),
                str(self.playlist_path),
            ]
        )
        return command

    def _prepare_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        for candidate in self.directory.iterdir():
            if candidate.name in {"live.m3u8", "live.m3u8.tmp"} or (
                candidate.name.startswith("segment_")
                and candidate.suffix in {".ts", ".tmp"}
            ):
                candidate.unlink(missing_ok=True)

    def start(self) -> None:
        self._prepare_directory()
        self._failed = False
        self.process = subprocess.Popen(
            self.command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=None,
            bufsize=max(0, self.width * self.height * 3 * 2),
        )
        if self.process.stdin is None:
            raise RuntimeError("Could not open the live-stream encoder input.")
        self._thread = threading.Thread(
            target=self._run,
            name="watchdog-hls-stream",
            daemon=True,
        )
        self._thread.start()
        audio = " with microphone audio" if self.audio_enabled else ""
        self.log(f"Native HLS live stream starting{audio}.")

    def submit(self, frame: bytes) -> None:
        if self._failed or not self.process:
            return
        if self.process.poll() is not None:
            self._fail(f"encoder exited with code {self.process.returncode}")
            return
        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                pass

    def _fail(self, reason: str) -> None:
        if not self._failed:
            self._failed = True
            self.log(
                f"Native HLS live stream disabled ({reason}); MJPEG fallback remains available."
            )

    def _run(self) -> None:
        assert self.process is not None
        assert self.process.stdin is not None
        try:
            while True:
                frame = self._frames.get()
                if frame is None:
                    return
                if self.process.poll() is not None:
                    self._fail(f"encoder exited with code {self.process.returncode}")
                    return
                try:
                    self.process.stdin.write(frame)
                except (BrokenPipeError, OSError, ValueError) as error:
                    self._fail(str(error))
                    return
        finally:
            try:
                self.process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def stop(self) -> None:
        process = self.process
        if not process:
            return
        try:
            self._frames.put_nowait(None)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            self._frames.put_nowait(None)
        if self._thread:
            self._thread.join(timeout=5)
        if self._thread and self._thread.is_alive() and process.poll() is None:
            process.terminate()
            self._thread.join(timeout=3)
        if process.poll() is None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self._thread = None
        self.process = None


def hls_directory(config_directory: Path) -> Path:
    return config_directory / "live"
