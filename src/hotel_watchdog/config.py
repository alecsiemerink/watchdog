from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_NAME = "hotel-watchdog"


def config_dir() -> Path:
    override = os.environ.get("HOTEL_WATCHDOG_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def pid_path() -> Path:
    return config_dir() / "watchdog.pid"


@dataclass(slots=True)
class Config:
    hark_webhook_url: str = ""
    output_dir: str = "~/Movies/HotelWatchdog"
    camera_index: int = 0
    microphone_index: int = 0
    width: int = 640
    height: int = 480
    auto_max_resolution: bool = True
    camera_input_fps: int = 30
    recording_fps: int = 15
    share_port: int = 8766
    tailscale_share: bool = False
    tailscale_path: str = "/hotel-watchdog"
    motion_pixel_delta: int = 22
    motion_changed_fraction: float = 0.025
    motion_hits_required: int = 2
    quiet_seconds_to_stop: int = 30
    minimum_recording_seconds: int = 15
    maximum_recording_seconds: int = 600
    warmup_seconds: int = 5
    pre_roll_seconds: int = 3
    person_detection: bool = True
    person_confidence: float = 0.5
    person_required_hits: int = 2
    person_clear_hits: int = 5
    person_interval_seconds: float = 1.0
    tamper_detection: bool = True
    tamper_baseline_frames: int = 10
    tamper_pixel_delta: int = 35
    tamper_changed_fraction: float = 0.75
    tamper_dark_brightness: float = 18
    tamper_flat_contrast: float = 7
    tamper_persistence_seconds: int = 8
    tamper_recovery_seconds: int = 4
    retention_days: int = 30
    retention_max_total_gb: float = 10
    minimum_free_disk_gb: float = 2

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser()

    @property
    def log_path(self) -> Path:
        return self.output_path / "watchdog.log"

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["hark_webhook_url"]:
            data["hark_webhook_url"] = "configured (redacted)"
        return data

    def validate(self) -> None:
        output = self.output_path.resolve()
        if output == Path(output.anchor) or output == Path.home().resolve():
            raise ValueError(
                "output_dir cannot be the filesystem root or home directory."
            )
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Camera dimensions must be positive.")
        if self.camera_index < 0 or self.microphone_index < 0:
            raise ValueError("Camera and microphone indexes cannot be negative.")
        if self.camera_input_fps <= 0 or self.recording_fps <= 0:
            raise ValueError("Frame rates must be positive.")
        if self.pre_roll_seconds < 0 or self.pre_roll_seconds > 10:
            raise ValueError("pre_roll_seconds must be between 0 and 10.")
        if not 1 <= self.share_port <= 65_535:
            raise ValueError("share_port must be between 1 and 65535.")
        if not 0 <= self.person_confidence <= 1:
            raise ValueError("person_confidence must be between 0 and 1.")
        if self.person_required_hits <= 0 or self.person_clear_hits <= 0:
            raise ValueError("Person detection hit counts must be positive.")
        if self.person_interval_seconds <= 0:
            raise ValueError("person_interval_seconds must be positive.")
        if not 0 <= self.motion_changed_fraction <= 1:
            raise ValueError("motion_changed_fraction must be between 0 and 1.")
        if not 0 <= self.motion_pixel_delta <= 255:
            raise ValueError("motion_pixel_delta must be between 0 and 255.")
        if self.motion_hits_required <= 0:
            raise ValueError("motion_hits_required must be positive.")
        if (
            min(
                self.quiet_seconds_to_stop,
                self.minimum_recording_seconds,
                self.maximum_recording_seconds,
                self.warmup_seconds,
            )
            < 0
        ):
            raise ValueError("Recording timing values cannot be negative.")
        if self.maximum_recording_seconds < self.minimum_recording_seconds:
            raise ValueError(
                "maximum_recording_seconds cannot be less than the minimum."
            )
        if not 0 <= self.tamper_changed_fraction <= 1:
            raise ValueError("tamper_changed_fraction must be between 0 and 1.")
        if not 0 <= self.tamper_pixel_delta <= 255:
            raise ValueError("tamper_pixel_delta must be between 0 and 255.")
        if self.tamper_baseline_frames <= 0:
            raise ValueError("tamper_baseline_frames must be positive.")
        if self.tamper_persistence_seconds <= 0 or self.tamper_recovery_seconds <= 0:
            raise ValueError("Tamper timing values must be positive.")
        if self.retention_days < 0 or self.retention_max_total_gb < 0:
            raise ValueError("Retention values cannot be negative.")
        if self.minimum_free_disk_gb < 0:
            raise ValueError("minimum_free_disk_gb cannot be negative.")


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        config = Config()
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {field.name for field in fields(Config)}
        config = Config(**{key: value for key, value in raw.items() if key in allowed})

    env_webhook = os.environ.get("HOTEL_WATCHDOG_HARK_URL")
    if env_webhook:
        config.hark_webhook_url = env_webhook
    config.validate()
    return config


def save_config(config: Config) -> Path:
    config.validate()
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass

    path = config_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path
