from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config

LAUNCH_AGENT_LABEL = "com.alecsiemerink.hotel-watchdog"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def resolve_cli_executable() -> Path:
    executable = shutil.which("hotel-watchdog")
    if executable:
        return Path(executable).resolve()
    candidate = Path(sys.argv[0]).resolve()
    if candidate.is_file():
        return candidate
    raise RuntimeError("Could not resolve the installed hotel-watchdog executable.")


def launch_agent_payload(executable: Path, config: Config) -> dict[str, object]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(executable), "run"],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "ThrottleInterval": 30,
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
        "StandardOutPath": str(config.log_path),
        "StandardErrorPath": str(config.log_path),
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install_launch_agent(
    config: Config,
    *,
    executable: Path | None = None,
    start_now: bool = True,
) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation is supported only on macOS.")
    executable = executable or resolve_cli_executable()
    if not executable.is_file():
        raise RuntimeError(f"Executable does not exist: {executable}")

    config.output_path.mkdir(parents=True, exist_ok=True)
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as destination:
        plistlib.dump(
            launch_agent_payload(executable, config), destination, sort_keys=False
        )
    temporary.chmod(0o644)
    temporary.replace(path)

    subprocess.run(
        ["/bin/launchctl", "bootout", _domain(), str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["/bin/launchctl", "enable", f"{_domain()}/{LAUNCH_AGENT_LABEL}"],
        check=True,
    )
    if start_now:
        subprocess.run(
            ["/bin/launchctl", "bootstrap", _domain(), str(path)], check=True
        )
        subprocess.run(
            [
                "/bin/launchctl",
                "kickstart",
                "-k",
                f"{_domain()}/{LAUNCH_AGENT_LABEL}",
            ],
            check=True,
        )
    return path


def uninstall_launch_agent() -> bool:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation is supported only on macOS.")
    path = launch_agent_path()
    subprocess.run(
        ["/bin/launchctl", "bootout", _domain(), str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def launch_agent_status() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", "print", f"{_domain()}/{LAUNCH_AGENT_LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
