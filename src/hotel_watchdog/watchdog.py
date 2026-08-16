from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from .config import Config, pid_path
from .hark import HarkClient
from .live import LiveServer, LiveState
from .tailscale import TailscaleShare


def now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def log(message: str) -> None:
    print(f"[{now_text()}] {message}", flush=True)


def find_executable(name: str, preferred: str | None = None) -> str:
    if preferred and Path(preferred).is_file():
        return preferred
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"Required executable is missing: {name}")
    return executable


def read_exact(
    stream: BinaryIO, byte_count: int, stop_event: threading.Event
) -> bytes | None:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining and not stop_event.is_set():
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining:
        return None
    return b"".join(chunks)


def sampled_green_pixels(frame: bytes) -> bytes:
    # BGR has three bytes per pixel. Sample one green value per 16 pixels.
    return frame[1 :: 3 * 16]


def motion_fraction(previous: bytes, current: bytes, pixel_delta: int) -> float:
    if not current:
        return 0.0
    changed = sum(
        1
        for before, after in zip(previous, current)
        if abs(before - after) >= pixel_delta
    )
    return changed / len(current)


def jpeg_from_bgr(frame: bytes, width: int, height: int, quality: int = 76) -> bytes:
    image = Image.frombytes("RGB", (width, height), frame, "raw", "BGR")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=False)
    return output.getvalue()


class Watchdog:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.ffmpeg = find_executable("ffmpeg", "/opt/homebrew/bin/ffmpeg")
        self.stop_event = threading.Event()
        self.live_state = LiveState()
        self.live_server = LiveServer(
            self.live_state,
            config.output_path,
            config.share_port,
            log,
        )
        self.hark = HarkClient(config.hark_webhook_url, log)
        self.tailscale: TailscaleShare | None = None
        self.caffeinate: subprocess.Popen | None = None
        self.camera: subprocess.Popen | None = None

    def camera_command(self) -> list[str]:
        return [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-framerate",
            str(self.config.camera_input_fps),
            "-video_size",
            f"{self.config.width}x{self.config.height}",
            "-i",
            str(self.config.camera_index),
            "-an",
            "-vf",
            f"fps={self.config.recording_fps},format=bgr24",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

    def recorder_command(self, path: Path) -> list[str]:
        return [
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
            f"{self.config.width}x{self.config.height}",
            "-framerate",
            str(self.config.recording_fps),
            "-i",
            "pipe:0",
            "-thread_queue_size",
            "1024",
            "-f",
            "avfoundation",
            "-i",
            f":{self.config.microphone_index}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-af",
            "aresample=async=1:first_pts=0",
            "-shortest",
            "-movflags",
            "+faststart",
            str(path),
        ]

    def _signal(self, signum, _frame) -> None:
        log(f"Received signal {signum}; stopping.")
        self.stop_event.set()

    def _setup(self) -> str | None:
        output = self.config.output_path
        output.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(output).free < 1_000_000_000:
            raise RuntimeError("Less than 1 GB of free disk space remains.")

        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self.live_server.start()
        self.live_state.set_armed(True)

        live_url: str | None = None
        if self.config.tailscale_share:
            try:
                self.tailscale = TailscaleShare(
                    port=self.live_server.port,
                    route=self.config.tailscale_path,
                )
                live_url = self.tailscale.expose()
                log(f"Tailscale live view: {live_url}")
            except (
                RuntimeError,
                subprocess.CalledProcessError,
                OSError,
                ValueError,
            ) as error:
                self.tailscale = None
                log(f"Tailscale sharing unavailable: {error}")

        self.caffeinate = subprocess.Popen(
            ["/usr/bin/caffeinate", "-ims", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return live_url

    def _armed_notification(self, live_url: str | None) -> None:
        if live_url and self.tailscale:
            body = (
                f"Watchdog armed at {now_text()}. Tap for the private live view. "
                f"Current snapshot: {self.tailscale.snapshot_url}"
            )
        else:
            body = f"Watchdog armed at {now_text()}. Monitoring the room for motion."
        self.hark.send_background(
            body, summary="Watchdog armed — tap for live view", url=live_url
        )

    def _start_recording(
        self, live_url: str | None
    ) -> tuple[subprocess.Popen, Path, float]:
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        path = self.config.output_path / f"motion_{timestamp}.mp4"
        process = subprocess.Popen(
            self.recorder_command(path),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
        started = time.monotonic()
        self.live_state.set_recording(True)
        log(f"Motion detected; recording {path.name}.")
        self.hark.send_background(
            f"Motion detected at {now_text()}. Video and audio recording started. Tap for the live view.",
            summary="Motion detected — recording started",
            url=live_url,
        )
        return process, path, started

    def _finish_recording(
        self, process: subprocess.Popen, path: Path, started: float
    ) -> None:
        duration = time.monotonic() - started
        if process.stdin:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
        try:
            return_code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait()

        if return_code == 0 and path.exists() and path.stat().st_size:
            size_mb = path.stat().st_size / 1_000_000
            self.live_state.set_recording(False, path.name)
            recording_url = (
                self.tailscale.recording_url(path) if self.tailscale else None
            )
            log(f"Saved {path} ({duration:.0f}s, {size_mb:.1f} MB).")
            self.hark.send_background(
                f"Recording saved: {path.name} ({duration:.0f} seconds, {size_mb:.1f} MB). Tap to play it.",
                summary=f"Recording saved — {duration:.0f} seconds",
                url=recording_url,
            )
        else:
            self.live_state.set_recording(False)
            log(f"Recorder failed with exit code {return_code}.")
            self.hark.send_background(
                f"Motion was detected, but recording failed with exit code {return_code}.",
                summary="Watchdog recording failed",
            )

    def run(self) -> int:
        live_url: str | None = None
        recorder: subprocess.Popen | None = None
        recording_path: Path | None = None
        recording_started = 0.0
        try:
            live_url = self._setup()
            log("Starting camera and microphone watchdog.")
            self.camera = subprocess.Popen(
                self.camera_command(),
                stdout=subprocess.PIPE,
                stderr=None,
                bufsize=self.config.width * self.config.height * 3 * 2,
            )
            if self.camera.stdout is None:
                raise RuntimeError("Could not read camera output.")

            frame_size = self.config.width * self.config.height * 3
            check_every = max(1, round(self.config.recording_fps / 2))
            jpeg_every = max(1, round(self.config.recording_fps / 2))
            frame_number = 0
            previous_sample: bytes | None = None
            consecutive_hits = 0
            warmup_until = time.monotonic() + self.config.warmup_seconds
            last_motion = 0.0
            armed_notification_sent = False

            while not self.stop_event.is_set():
                frame = read_exact(self.camera.stdout, frame_size, self.stop_event)
                if frame is None:
                    if self.camera.poll() is not None:
                        raise RuntimeError(
                            f"Camera process exited with code {self.camera.returncode}. "
                            "Grant Camera access to your terminal/ffmpeg in System Settings > Privacy & Security."
                        )
                    continue

                frame_number += 1
                now = time.monotonic()

                if frame_number == 1 or frame_number % jpeg_every == 0:
                    self.live_state.update_frame(
                        jpeg_from_bgr(frame, self.config.width, self.config.height)
                    )
                    if not armed_notification_sent:
                        self._armed_notification(live_url)
                        armed_notification_sent = True

                if frame_number % check_every == 0:
                    sample = sampled_green_pixels(frame)
                    if previous_sample is not None and now >= warmup_until:
                        fraction = motion_fraction(
                            previous_sample,
                            sample,
                            self.config.motion_pixel_delta,
                        )
                        motion_seen = fraction >= self.config.motion_changed_fraction
                        if motion_seen:
                            consecutive_hits += 1
                            if recorder is not None:
                                last_motion = now
                        else:
                            consecutive_hits = 0
                    previous_sample = sample

                if (
                    recorder is None
                    and consecutive_hits >= self.config.motion_hits_required
                ):
                    recorder, recording_path, recording_started = self._start_recording(
                        live_url
                    )
                    last_motion = now
                    consecutive_hits = 0

                if recorder is not None:
                    if recorder.poll() is not None:
                        raise RuntimeError(
                            f"Recorder exited unexpectedly with code {recorder.returncode}."
                        )
                    try:
                        assert recorder.stdin is not None
                        recorder.stdin.write(frame)
                    except BrokenPipeError as error:
                        raise RuntimeError(
                            "Recorder stopped accepting video frames."
                        ) from error

                    elapsed = now - recording_started
                    quiet_for = now - last_motion
                    quiet_stop = (
                        elapsed >= self.config.minimum_recording_seconds
                        and quiet_for >= self.config.quiet_seconds_to_stop
                    )
                    if quiet_stop or elapsed >= self.config.maximum_recording_seconds:
                        assert recording_path is not None
                        self._finish_recording(
                            recorder, recording_path, recording_started
                        )
                        recorder = None
                        recording_path = None
                        previous_sample = None
                        warmup_until = time.monotonic() + 2

            return 0
        except Exception as error:  # noqa: BLE001 - top-level daemon safety boundary.
            log(f"Fatal error: {error}")
            self.hark.send_background(
                f"Hotel Watchdog stopped because of an error: {error}",
                summary="Hotel Watchdog stopped",
                url=live_url,
            )
            return 1
        finally:
            if recorder is not None and recording_path is not None:
                self._finish_recording(recorder, recording_path, recording_started)
            if self.camera and self.camera.poll() is None:
                self.camera.terminate()
                try:
                    self.camera.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.camera.kill()
                    self.camera.wait()
            if self.caffeinate and self.caffeinate.poll() is None:
                self.caffeinate.terminate()
            self.live_state.set_armed(False)
            self.live_server.stop()
            pid_path().unlink(missing_ok=True)
            log("Hotel Watchdog stopped.")
