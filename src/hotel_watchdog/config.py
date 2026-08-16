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
    return config


def save_config(config: Config) -> Path:
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
