from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace

from .config import Config

Runner = Callable[..., subprocess.CompletedProcess[str]]
Log = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CameraMode:
    width: int
    height: int
    minimum_fps: float
    maximum_fps: float

    @property
    def pixels(self) -> int:
        return self.width * self.height


MODE_PATTERN = re.compile(
    r"(?P<width>\d+)x(?P<height>\d+)@\[\s*"
    r"(?P<minimum>[\d.]+)\s+(?P<maximum>[\d.]+)\]fps"
)


def parse_camera_modes(stderr: str) -> list[CameraMode]:
    modes = {
        CameraMode(
            width=int(match.group("width")),
            height=int(match.group("height")),
            minimum_fps=float(match.group("minimum")),
            maximum_fps=float(match.group("maximum")),
        )
        for match in MODE_PATTERN.finditer(stderr)
    }
    return sorted(modes, key=lambda mode: (mode.pixels, mode.width, mode.height))


def select_max_landscape_mode(
    modes: list[CameraMode], requested_fps: float
) -> CameraMode | None:
    compatible = [
        mode for mode in modes if mode.minimum_fps <= requested_fps <= mode.maximum_fps
    ]
    if not compatible:
        return None
    landscape = [mode for mode in compatible if mode.width > mode.height]
    candidates = landscape or compatible
    return max(candidates, key=lambda mode: (mode.pixels, mode.width, mode.height))


def probe_camera_modes(
    ffmpeg: str,
    *,
    camera_index: int,
    requested_fps: float,
    runner: Runner = subprocess.run,
) -> list[CameraMode]:
    # AVFoundation prints every supported mode when an impossible size is asked
    # for. No frames are recorded or written by this probe.
    result = runner(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "verbose",
            "-f",
            "avfoundation",
            "-framerate",
            str(requested_fps),
            "-video_size",
            "2x2",
            "-i",
            str(camera_index),
            "-t",
            "0.01",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return parse_camera_modes(result.stderr)


def resolve_max_resolution(
    config: Config,
    ffmpeg: str,
    *,
    runner: Runner = subprocess.run,
    log: Log = print,
) -> Config:
    if not config.auto_max_resolution:
        return config
    try:
        modes = probe_camera_modes(
            ffmpeg,
            camera_index=config.camera_index,
            requested_fps=config.camera_input_fps,
            runner=runner,
        )
        selected = select_max_landscape_mode(modes, config.camera_input_fps)
    except OSError as error:
        log(
            f"Could not probe maximum camera resolution; using "
            f"{config.width}x{config.height}: {error}"
        )
        return config
    if not selected:
        log(
            f"Camera mode probe returned no compatible landscape mode; using "
            f"{config.width}x{config.height}."
        )
        return config
    resolved = replace(config, width=selected.width, height=selected.height)
    log(
        f"Auto-selected maximum landscape camera resolution "
        f"{resolved.width}x{resolved.height}."
    )
    return resolved
