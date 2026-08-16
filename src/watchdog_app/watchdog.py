from __future__ import annotations

import io
import os
import select
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from .config import Config, pid_path
from .hark import HarkClient
from .live import LiveServer, LiveState
from .person import (
    PersonDetectionWorker,
    PersonObservation,
    PersonPresence,
    VisionPersonDetector,
    annotate_people,
)
from .resolution import resolve_max_resolution
from .retention import RetentionResult, apply_retention
from .tailscale import TailscaleShare
from .tamper import CameraTamperDetector, ViewMetrics


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
        ready, _, _ = select.select([stream], [], [], 0.5)
        if not ready:
            continue
        chunk = os.read(stream.fileno(), remaining)
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
        self.ffmpeg = find_executable("ffmpeg", "/opt/homebrew/bin/ffmpeg")
        self.config = resolve_max_resolution(config, self.ffmpeg, log=log)
        self.stop_event = threading.Event()
        self.live_state = LiveState()
        self.live_server = LiveServer(
            self.live_state,
            self.config.output_path,
            self.config.share_port,
            log,
        )
        self.hark = HarkClient(self.config.hark_webhook_url, log)
        self.tailscale: TailscaleShare | None = None
        self.caffeinate: subprocess.Popen | None = None
        self.camera: subprocess.Popen | None = None
        self.person_worker: PersonDetectionWorker | None = None

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

    def recorder_command(
        self, path: Path, audio_offset_seconds: float = 0
    ) -> list[str]:
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
            f"{self.config.width}x{self.config.height}",
            "-framerate",
            str(self.config.recording_fps),
            "-i",
            "pipe:0",
            "-thread_queue_size",
            "1024",
        ]
        if audio_offset_seconds > 0:
            command.extend(["-itsoffset", f"{audio_offset_seconds:.3f}"])
        command.extend(
            [
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
        )
        return command

    def _signal(self, signum, _frame) -> None:
        log(f"Received signal {signum}; stopping.")
        self.stop_event.set()

    def _setup(self) -> str | None:
        output = self.config.output_path
        output.mkdir(parents=True, exist_ok=True)
        retention = self._apply_retention()
        if retention.critically_low:
            raise RuntimeError(
                f"Less than {self.config.minimum_free_disk_gb:g} GB of free disk space remains "
                "after applying retention."
            )

        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self.live_server.start()

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

    def _apply_retention(self, active_path: Path | None = None) -> RetentionResult:
        result = apply_retention(
            self.config.output_path,
            max_age_days=self.config.retention_days,
            max_total_bytes=round(self.config.retention_max_total_gb * 1_000_000_000),
            minimum_free_bytes=round(self.config.minimum_free_disk_gb * 1_000_000_000),
            active_path=active_path,
        )
        if result.removed:
            names = ", ".join(path.name for path in result.removed)
            log(
                f"Retention removed {len(result.removed)} media file(s), "
                f"reclaiming {result.reclaimed_bytes / 1_000_000:.1f} MB: {names}"
            )
        if result.critically_low:
            self.hark.send_background(
                f"Watchdog disk space is critically low: "
                f"{result.free_bytes / 1_000_000_000:.1f} GB free after retention.",
                summary="Watchdog disk space critically low",
            )
        return result

    def _armed_notification(self, live_url: str | None) -> None:
        if live_url and self.tailscale:
            body = (
                f"Watchdog armed at {now_text()}. Tap for the private live view. "
                f"Current snapshot: {self.tailscale.snapshot_url}"
            )
        else:
            body = f"Watchdog armed at {now_text()}. Monitoring the room for motion."
        summary = "Watchdog armed — tap for live view" if live_url else "Watchdog armed"
        self.hark.send_background(body, summary=summary, url=live_url)

    def _start_recording(
        self,
        live_url: str | None,
        pre_roll: list[bytes],
        trigger_jpeg: bytes | None,
    ) -> tuple[subprocess.Popen, Path, float]:
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        path = self.config.output_path / f"motion_{timestamp}.mp4"
        evidence_url = live_url
        if trigger_jpeg:
            evidence = self.config.output_path / f"motion_{timestamp}.jpg"
            try:
                evidence.write_bytes(trigger_jpeg)
                if self.tailscale:
                    evidence_url = self.tailscale.evidence_url(evidence)
            except OSError as error:
                log(f"Could not save trigger snapshot: {error}")
        pre_roll_duration = len(pre_roll) / self.config.recording_fps
        process = subprocess.Popen(
            self.recorder_command(path, audio_offset_seconds=pre_roll_duration),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
        if process.stdin:
            try:
                for buffered_frame in pre_roll:
                    process.stdin.write(buffered_frame)
            except BrokenPipeError as error:
                raise RuntimeError(
                    "Recorder failed while writing the pre-roll buffer."
                ) from error
        started = time.monotonic() - pre_roll_duration
        self.live_state.set_recording(True)
        log(
            f"Motion detected; recording {path.name} with "
            f"{pre_roll_duration:.1f}s of video pre-roll."
        )
        body = f"Motion detected at {now_text()}. Video and audio recording started."
        if evidence_url:
            body += " Tap for the private trigger snapshot."
        else:
            body += " The trigger snapshot was saved locally."
        self.hark.send_background(
            body,
            summary=(
                "Motion detected — tap for snapshot"
                if evidence_url
                else "Motion detected — recording started"
            ),
            url=evidence_url,
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
            body = (
                f"Recording saved: {path.name} "
                f"({duration:.0f} seconds, {size_mb:.1f} MB)."
            )
            if recording_url:
                body += " Tap to play it privately."
            self.hark.send_background(
                body,
                summary=f"Recording saved — {duration:.0f} seconds",
                url=recording_url,
            )
            self._apply_retention(active_path=path)
        else:
            self.live_state.set_recording(False)
            log(f"Recorder failed with exit code {return_code}.")
            self.hark.send_background(
                f"Motion was detected, but recording failed with exit code {return_code}.",
                summary="Watchdog recording failed",
            )

    def _start_person_worker(self, live_url: str | None) -> None:
        if not self.config.person_detection:
            return
        detector = VisionPersonDetector(upper_body_only=True)
        presence = PersonPresence(
            confidence_threshold=self.config.person_confidence,
            required_hits=self.config.person_required_hits,
            clear_hits=self.config.person_clear_hits,
        )

        def callback(
            event: str,
            observations: list[PersonObservation],
            jpeg: bytes,
        ) -> None:
            if event == "cleared":
                self.live_state.set_person_present(False)
                log("Person no longer detected.")
                return

            matching = [
                observation
                for observation in observations
                if observation.confidence >= self.config.person_confidence
            ]
            if not matching:
                return
            self.live_state.set_person_present(True)
            confidence = max(observation.confidence for observation in matching)
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
            evidence = self.config.output_path / f"person_{stamp}.jpg"
            evidence.write_bytes(annotate_people(jpeg, matching))
            evidence_url = (
                self.tailscale.evidence_url(evidence) if self.tailscale else live_url
            )
            body = (
                f"Person detected locally at {now_text()} ({confidence:.0%} confidence). "
                "No face recognition was used."
            )
            if live_url:
                body += f" Live view: {live_url}"
            log(f"Person detected ({confidence:.0%}); saved {evidence.name}.")
            self.hark.send_background(
                body,
                summary=f"Person detected — {confidence:.0%} confidence",
                url=evidence_url,
            )

        self.person_worker = PersonDetectionWorker(
            detector,
            presence,
            callback,
            log,
        )
        self.person_worker.start()
        log("Local Apple Vision person detection enabled.")

    def _handle_tamper(
        self,
        event: str,
        metrics: ViewMetrics,
        jpeg: bytes | None,
        live_url: str | None,
    ) -> None:
        if event == "recovered":
            self.live_state.set_camera_warning(False)
            log("Camera view recovered.")
            self.hark.send_background(
                f"Camera view recovered at {now_text()}.",
                summary="Camera view recovered",
                url=live_url,
            )
            return

        self.live_state.set_camera_warning(True)
        evidence_url = live_url
        if jpeg:
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
            evidence = self.config.output_path / f"tamper_{stamp}.jpg"
            try:
                evidence.write_bytes(jpeg)
                if self.tailscale:
                    evidence_url = self.tailscale.evidence_url(evidence)
            except OSError as error:
                log(f"Could not save camera-warning snapshot: {error}")
        reason = metrics.reason or "changed"
        log(
            f"Camera warning: {reason}; brightness={metrics.brightness:.1f}, "
            f"contrast={metrics.contrast:.1f}, changed={metrics.changed_fraction:.0%}."
        )
        body = f"Camera may be {reason} at {now_text()}."
        if evidence_url:
            body += " Tap to inspect the private snapshot/live view."
        else:
            body += " The warning was logged locally."
        self.hark.send_background(
            body,
            summary="Camera may be obstructed or moved",
            url=evidence_url,
        )

    def run(self) -> int:
        live_url: str | None = None
        recorder: subprocess.Popen | None = None
        recording_path: Path | None = None
        recording_started = 0.0
        try:
            live_url = self._setup()
            try:
                self._start_person_worker(live_url)
            except (RuntimeError, OSError) as error:
                log(
                    f"Person detection unavailable; generic motion remains active: {error}"
                )
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
            person_every = max(
                1,
                round(self.config.recording_fps * self.config.person_interval_seconds),
            )
            checks_per_second = self.config.recording_fps / check_every
            tamper_detector = (
                CameraTamperDetector(
                    baseline_frames=self.config.tamper_baseline_frames,
                    pixel_delta=self.config.tamper_pixel_delta,
                    changed_fraction=self.config.tamper_changed_fraction,
                    dark_brightness=self.config.tamper_dark_brightness,
                    flat_contrast=self.config.tamper_flat_contrast,
                    required_hits=round(
                        self.config.tamper_persistence_seconds * checks_per_second
                    ),
                    recovery_hits=round(
                        self.config.tamper_recovery_seconds * checks_per_second
                    ),
                )
                if self.config.tamper_detection
                else None
            )
            pre_roll: deque[bytes] = deque(
                maxlen=max(
                    0,
                    round(self.config.pre_roll_seconds * self.config.recording_fps),
                )
            )
            frame_number = 0
            previous_sample: bytes | None = None
            latest_jpeg: bytes | None = None
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

                needs_person_frame = bool(
                    self.person_worker and frame_number % person_every == 0
                )
                if (
                    frame_number == 1
                    or frame_number % jpeg_every == 0
                    or needs_person_frame
                ):
                    latest_jpeg = jpeg_from_bgr(
                        frame, self.config.width, self.config.height
                    )
                    self.live_state.update_frame(latest_jpeg)
                    if not armed_notification_sent:
                        self.live_state.set_armed(True)
                        self._armed_notification(live_url)
                        armed_notification_sent = True
                if (
                    self.person_worker
                    and latest_jpeg
                    and (frame_number == 1 or needs_person_frame)
                ):
                    self.person_worker.submit(latest_jpeg)

                if frame_number % check_every == 0:
                    sample = sampled_green_pixels(frame)
                    if tamper_detector:
                        tamper_event, tamper_metrics = tamper_detector.update(sample)
                        if tamper_event:
                            self._handle_tamper(
                                tamper_event,
                                tamper_metrics,
                                latest_jpeg,
                                live_url,
                            )
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
                        live_url,
                        list(pre_roll),
                        latest_jpeg,
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
                        pre_roll.clear()
                        warmup_until = time.monotonic() + 2

                pre_roll.append(frame)

            return 0
        except Exception as error:  # noqa: BLE001 - top-level daemon safety boundary.
            log(f"Fatal error: {error}")
            self.hark.send_background(
                f"Watchdog stopped because of an error: {error}",
                summary="Watchdog stopped",
                url=live_url,
            )
            return 1
        finally:
            if self.person_worker:
                self.person_worker.stop()
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
            log("Watchdog stopped.")
