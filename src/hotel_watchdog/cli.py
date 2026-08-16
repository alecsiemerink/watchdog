from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from . import __version__
from .config import Config, config_dir, load_config, pid_path, save_config
from .hark import HarkClient
from .person import VisionPersonDetector
from .retention import apply_retention
from .service import (
    install_launch_agent,
    launch_agent_status,
    uninstall_launch_agent,
)
from .tailscale import TailscaleShare
from .watchdog import Watchdog, find_executable, now_text


def read_pid() -> int | None:
    try:
        return int(pid_path().read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def process_is_watchdog(pid: int) -> bool:
    if not pid_is_running(pid):
        return False
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    command = result.stdout.lower().replace("-", "_")
    return "hotel_watchdog" in command and "run" in command


def ensure_runtime_dir() -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass


def start_background(config: Config) -> int:
    existing = read_pid()
    if existing and process_is_watchdog(existing):
        print(f"Hotel Watchdog is already running (PID {existing}).")
        return 0
    pid_path().unlink(missing_ok=True)
    config.output_path.mkdir(parents=True, exist_ok=True)
    ensure_runtime_dir()

    with config.log_path.open("a", encoding="utf-8") as logfile:
        process = subprocess.Popen(
            [sys.executable, "-m", "hotel_watchdog", "run"],
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path().write_text(f"{process.pid}\n", encoding="utf-8")
    pid_path().chmod(0o600)
    time.sleep(2)
    if process.poll() is not None:
        print(
            f"Hotel Watchdog failed to start. Check {config.log_path}.", file=sys.stderr
        )
        return 1
    print(f"Hotel Watchdog started (PID {process.pid}).")
    print(f"Recordings: {config.output_path}")
    print(f"Log: {config.log_path}")
    if config.tailscale_share:
        try:
            print(
                "Live view: "
                + TailscaleShare(
                    port=config.share_port, route=config.tailscale_path
                ).base_url
            )
        except (
            RuntimeError,
            subprocess.CalledProcessError,
            OSError,
            json.JSONDecodeError,
        ):
            print("Live view: waiting for Tailscale setup; check the log.")
    return 0


def stop_background() -> int:
    pid = read_pid()
    if not pid or not process_is_watchdog(pid):
        pid_path().unlink(missing_ok=True)
        print("Hotel Watchdog is not running.")
        return 0
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while process_is_watchdog(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    if process_is_watchdog(pid):
        print(f"Watchdog PID {pid} is still finalizing its current recording.")
    else:
        print("Hotel Watchdog stopped.")
    return 0


def local_health(config: Config) -> dict | None:
    try:
        with urlopen(
            f"http://127.0.0.1:{config.share_port}/health", timeout=2
        ) as response:
            return json.load(response)
    except (URLError, TimeoutError, json.JSONDecodeError):
        return None


def show_status(config: Config) -> int:
    pid = read_pid()
    if not pid or not process_is_watchdog(pid):
        print("Hotel Watchdog is not running.")
        return 1
    print(f"Hotel Watchdog is running (PID {pid}).")
    health = local_health(config)
    if health:
        print(f"State: {health.get('last_event', 'Monitoring')}")
    print(f"Recordings: {config.output_path}")
    print(f"Log: {config.log_path}")
    if config.tailscale_share:
        try:
            share = TailscaleShare(port=config.share_port, route=config.tailscale_path)
            print(f"Live view: {share.base_url}")
        except (
            RuntimeError,
            subprocess.CalledProcessError,
            OSError,
            json.JSONDecodeError,
        ) as error:
            print(f"Live view unavailable: {error}")
    return 0


def configure(args: argparse.Namespace, config: Config) -> int:
    interactive = not any(
        (
            args.hark_webhook,
            args.clear_hark,
            args.tailscale is not None,
            args.output_dir,
            args.camera_index is not None,
            args.microphone_index is not None,
            args.share_port is not None,
            args.person_detection is not None,
            args.tamper_detection is not None,
            args.pre_roll_seconds is not None,
            args.retention_days is not None,
            args.retention_max_total_gb is not None,
            args.minimum_free_disk_gb is not None,
        )
    )
    if interactive:
        current = "configured" if config.hark_webhook_url else "not configured"
        webhook = getpass.getpass(
            f"Hark webhook URL ({current}; leave blank to keep current): "
        ).strip()
        answer = (
            input(
                f"Enable private Tailscale live view? [{'Y/n' if config.tailscale_share else 'y/N'}]: "
            )
            .strip()
            .lower()
        )
        tailscale = config.tailscale_share if not answer else answer in ("y", "yes")
        if webhook:
            config = replace(config, hark_webhook_url=webhook)
        config = replace(config, tailscale_share=tailscale)
    else:
        updates: dict[str, object] = {}
        if args.hark_webhook:
            updates["hark_webhook_url"] = args.hark_webhook
        if args.clear_hark:
            updates["hark_webhook_url"] = ""
        if args.tailscale is not None:
            updates["tailscale_share"] = args.tailscale
        if args.output_dir:
            updates["output_dir"] = args.output_dir
        if args.camera_index is not None:
            updates["camera_index"] = args.camera_index
        if args.microphone_index is not None:
            updates["microphone_index"] = args.microphone_index
        if args.share_port is not None:
            updates["share_port"] = args.share_port
        if args.person_detection is not None:
            updates["person_detection"] = args.person_detection
        if args.tamper_detection is not None:
            updates["tamper_detection"] = args.tamper_detection
        if args.pre_roll_seconds is not None:
            updates["pre_roll_seconds"] = args.pre_roll_seconds
        if args.retention_days is not None:
            updates["retention_days"] = args.retention_days
        if args.retention_max_total_gb is not None:
            updates["retention_max_total_gb"] = args.retention_max_total_gb
        if args.minimum_free_disk_gb is not None:
            updates["minimum_free_disk_gb"] = args.minimum_free_disk_gb
        config = replace(config, **updates)

    path = save_config(config)
    print(f"Configuration saved to {path} (mode 0600).")
    print(json.dumps(config.public_dict(), indent=2))
    return 0


def take_snapshot(config: Config, destination: Path | None = None) -> Path:
    config.output_path.mkdir(parents=True, exist_ok=True)
    if destination is None:
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        destination = config.output_path / f"snapshot_{stamp}.jpg"
    destination = destination.expanduser().resolve()

    health = local_health(config)
    if health:
        with urlopen(
            f"http://127.0.0.1:{config.share_port}/snapshot.jpg", timeout=5
        ) as response:
            destination.write_bytes(response.read())
        return destination

    ffmpeg = find_executable("ffmpeg", "/opt/homebrew/bin/ffmpeg")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-framerate",
            str(config.camera_input_fps),
            "-video_size",
            f"{config.width}x{config.height}",
            "-i",
            str(config.camera_index),
            "-frames:v",
            "1",
            str(destination),
        ],
        check=True,
    )
    return destination


def share_for(config: Config) -> TailscaleShare:
    if not process_is_watchdog(read_pid() or -1):
        raise RuntimeError(
            "Start Hotel Watchdog before sharing its live view or recordings."
        )
    share = TailscaleShare(port=config.share_port, route=config.tailscale_path)
    share.expose()
    return share


def snapshot_command(args: argparse.Namespace, config: Config) -> int:
    path = take_snapshot(config, Path(args.output) if args.output else None)
    print(path)
    if args.notify:
        share = share_for(config)
        HarkClient(config.hark_webhook_url).send(
            f"Current Hotel Watchdog snapshot from {now_text()}. Tap to view it privately over Tailscale.",
            summary="Current room snapshot",
            url=share.snapshot_url,
        )
        print("Snapshot link sent through Hark.")
    return 0


def latest_recording(config: Config) -> Path:
    recordings = sorted(
        config.output_path.glob("motion_*.mp4"), key=lambda path: path.stat().st_mtime
    )
    if not recordings:
        raise RuntimeError("No recordings found.")
    return recordings[-1]


def share_command(args: argparse.Namespace, config: Config) -> int:
    share = share_for(config)
    if args.file:
        recording = Path(args.file).expanduser().resolve()
    else:
        recording = latest_recording(config)
    if (
        recording.parent != config.output_path.expanduser().resolve()
        or not recording.is_file()
    ):
        raise RuntimeError(f"Recording must be a file inside {config.output_path}.")
    url = share.recording_url(recording)
    print(url)
    if args.notify:
        HarkClient(config.hark_webhook_url).send(
            f"Tap to watch {recording.name} privately over Tailscale.",
            summary="Recording ready over Tailscale",
            url=url,
        )
        print("Recording link sent through Hark.")
    return 0


def recordings_command(config: Config) -> int:
    recordings = sorted(
        config.output_path.glob("motion_*.mp4"), key=lambda path: path.stat().st_mtime
    )
    if not recordings:
        print("No recordings found.")
        return 0
    for path in recordings:
        size_mb = path.stat().st_size / 1_000_000
        stamp = (
            datetime.fromtimestamp(path.stat().st_mtime)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        print(f"{stamp}  {size_mb:7.1f} MB  {path}")
    return 0


def doctor_command(config: Config) -> int:
    print(f"macOS: {platform.mac_ver()[0] or 'not detected'}")
    print(f"Python: {platform.python_version()}")
    ffmpeg = find_executable("ffmpeg", "/opt/homebrew/bin/ffmpeg")
    print(f"ffmpeg: {ffmpeg}")
    print("Checking camera and microphone for one second…")
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-framerate",
            str(config.camera_input_fps),
            "-video_size",
            f"{config.width}x{config.height}",
            "-i",
            f"{config.camera_index}:{config.microphone_index}",
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Camera/microphone check failed. Review macOS Privacy & Security permissions and device indexes."
        )
    print("Camera and microphone: OK")
    if config.person_detection:
        capture = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "avfoundation",
                "-framerate",
                str(config.camera_input_fps),
                "-video_size",
                f"{config.width}x{config.height}",
                "-i",
                str(config.camera_index),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ],
            check=True,
            capture_output=True,
        )
        detections = VisionPersonDetector().detect(capture.stdout)
        print(
            "Apple Vision person detection: OK "
            f"({len(detections)} person observation(s) in the test frame)"
        )
    print(
        f"Hark: {'configured' if config.hark_webhook_url else 'not configured (optional)'}"
    )
    if config.tailscale_share:
        share = TailscaleShare(port=config.share_port, route=config.tailscale_path)
        print(f"Tailscale: connected as {share.dns_name}")
    else:
        print("Tailscale sharing: disabled (optional)")
    print("Doctor check passed.")
    return 0


def retention_command(args: argparse.Namespace, config: Config) -> int:
    config.output_path.mkdir(parents=True, exist_ok=True)
    result = apply_retention(
        config.output_path,
        max_age_days=config.retention_days,
        max_total_bytes=round(config.retention_max_total_gb * 1_000_000_000),
        minimum_free_bytes=round(config.minimum_free_disk_gb * 1_000_000_000),
        dry_run=args.dry_run,
    )
    action = "Would remove" if args.dry_run else "Removed"
    print(
        f"{action} {len(result.removed)} media file(s), "
        f"reclaiming {result.reclaimed_bytes / 1_000_000:.1f} MB."
    )
    for path in result.removed:
        print(path)
    print(
        f"Retained media: {result.total_bytes / 1_000_000:.1f} MB; "
        f"free disk: {result.free_bytes / 1_000_000_000:.1f} GB."
    )
    return 1 if result.critically_low else 0


def service_command(args: argparse.Namespace, config: Config) -> int:
    if args.service_command == "install":
        path = install_launch_agent(config, start_now=not args.no_start)
        print(f"Installed LaunchAgent: {path}")
        if args.no_start:
            print("It will arm at the next login. Run hotel-watchdog start to arm now.")
        else:
            print("LaunchAgent loaded; check hotel-watchdog status and the log.")
        return 0
    if args.service_command == "uninstall":
        removed = uninstall_launch_agent()
        print("LaunchAgent removed." if removed else "LaunchAgent was not installed.")
        return 0
    if args.service_command == "status":
        result = launch_agent_status()
        if result.returncode:
            print("LaunchAgent is not loaded.")
            return 1
        print(result.stdout.rstrip())
        return 0
    raise RuntimeError("Unknown service command.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hotel-watchdog",
        description="Motion-triggered macOS room monitoring with Hark and Tailscale.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser(
        "configure", help="Save local configuration."
    )
    configure_parser.add_argument(
        "--hark-webhook", help="Hark webhook URL (interactive input is safer)."
    )
    configure_parser.add_argument("--clear-hark", action="store_true")
    configure_parser.add_argument(
        "--tailscale",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable the private Tailscale live view.",
    )
    configure_parser.add_argument("--output-dir")
    configure_parser.add_argument("--camera-index", type=int)
    configure_parser.add_argument("--microphone-index", type=int)
    configure_parser.add_argument("--share-port", type=int)
    configure_parser.add_argument(
        "--person-detection",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    configure_parser.add_argument(
        "--tamper-detection",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    configure_parser.add_argument("--pre-roll-seconds", type=int)
    configure_parser.add_argument("--retention-days", type=int)
    configure_parser.add_argument("--retention-max-total-gb", type=float)
    configure_parser.add_argument("--minimum-free-disk-gb", type=float)

    subparsers.add_parser(
        "show-config", help="Print configuration with secrets redacted."
    )
    subparsers.add_parser("start", help="Arm in the background.")
    subparsers.add_parser("run", help="Run in the foreground (normally used by start).")
    subparsers.add_parser("stop", help="Disarm and finish the current clip.")
    subparsers.add_parser("status", help="Show watcher state and URLs.")
    subparsers.add_parser("recordings", help="List recorded clips.")
    retention_parser = subparsers.add_parser(
        "retention", help="Apply or preview recording retention."
    )
    retention_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser(
        "doctor", help="Check dependencies and camera/microphone access."
    )
    subparsers.add_parser("test-alert", help="Send a Hark test notification.")

    snapshot_parser = subparsers.add_parser(
        "snapshot", help="Capture the current frame."
    )
    snapshot_parser.add_argument("--output")
    snapshot_parser.add_argument(
        "--notify", action="store_true", help="Send its Tailscale link via Hark."
    )

    share_parser = subparsers.add_parser(
        "share", help="Create a tailnet-only recording URL."
    )
    share_parser.add_argument("file", nargs="?")
    share_parser.add_argument(
        "--notify", action="store_true", help="Send the URL via Hark."
    )

    service_parser = subparsers.add_parser(
        "service", help="Manage automatic arming with a macOS LaunchAgent."
    )
    service_subparsers = service_parser.add_subparsers(
        dest="service_command", required=True
    )
    install_parser = service_subparsers.add_parser(
        "install", help="Install and load the LaunchAgent."
    )
    install_parser.add_argument(
        "--no-start", action="store_true", help="Install without arming immediately."
    )
    service_subparsers.add_parser(
        "uninstall", help="Unload and remove the LaunchAgent."
    )
    service_subparsers.add_parser("status", help="Show launchd service state.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config()
        if args.command == "configure":
            return configure(args, config)
        if args.command == "show-config":
            print(json.dumps(config.public_dict(), indent=2))
            return 0
        if args.command == "start":
            return start_background(config)
        if args.command == "run":
            ensure_runtime_dir()
            pid_path().write_text(f"{os.getpid()}\n", encoding="utf-8")
            pid_path().chmod(0o600)
            return Watchdog(config).run()
        if args.command == "stop":
            return stop_background()
        if args.command == "status":
            return show_status(config)
        if args.command == "recordings":
            return recordings_command(config)
        if args.command == "retention":
            return retention_command(args, config)
        if args.command == "doctor":
            return doctor_command(config)
        if args.command == "test-alert":
            result = HarkClient(config.hark_webhook_url).send(
                f"Hotel Watchdog test alert from this Mac at {now_text()}.",
                summary="Hotel Watchdog test successful",
            )
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "snapshot":
            return snapshot_command(args, config)
        if args.command == "share":
            return share_command(args, config)
        if args.command == "service":
            return service_command(args, config)
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.error("Unknown command")
    return 2
