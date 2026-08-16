from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

Runner = Callable[..., subprocess.CompletedProcess[str]]


def dns_name_from_status(status: dict[str, Any]) -> str:
    if status.get("BackendState") != "Running":
        raise RuntimeError("Tailscale is not connected.")
    name = status.get("Self", {}).get("DNSName", "").rstrip(".")
    if not name:
        raise RuntimeError("Tailscale did not report a MagicDNS name.")
    return name


class TailscaleShare:
    def __init__(
        self,
        *,
        port: int,
        route: str = "/hotel-watchdog",
        executable: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.port = port
        self.route = "/" + route.strip("/")
        self.executable = executable or shutil.which("tailscale") or "tailscale"
        self.runner = runner
        self._dns_name: str | None = None

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            [self.executable, *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def status(self) -> dict[str, Any]:
        result = self._run(["status", "--json"])
        return json.loads(result.stdout)

    @property
    def dns_name(self) -> str:
        if not self._dns_name:
            self._dns_name = dns_name_from_status(self.status())
        return self._dns_name

    @property
    def base_url(self) -> str:
        return f"https://{self.dns_name}{self.route}/"

    @property
    def snapshot_url(self) -> str:
        return self.base_url + "snapshot.jpg"

    @property
    def live_url(self) -> str:
        return self.base_url

    def recording_url(self, path: Path) -> str:
        return self.base_url + "recordings/" + quote(path.name)

    def evidence_url(self, path: Path) -> str:
        return self.base_url + "evidence/" + quote(path.name)

    def expose(self) -> str:
        # Proxying localhost also works with the sandboxed macOS Tailscale app,
        # which cannot serve files from protected user directories directly.
        target = f"http://127.0.0.1:{self.port}"
        self._run(["serve", "--bg", "--yes", "--set-path", self.route, target])
        return self.base_url
