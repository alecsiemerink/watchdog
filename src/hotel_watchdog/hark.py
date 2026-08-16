from __future__ import annotations

import json
import threading
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

Log = Callable[[str], None]


class HarkClient:
    def __init__(self, webhook_url: str, log: Log = print) -> None:
        self.webhook_url = webhook_url.strip()
        self.log = log

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(
        self,
        body: str,
        *,
        summary: str | None = None,
        url: str | None = None,
    ) -> dict:
        if not self.enabled:
            return {"ok": False, "skipped": True}

        payload: dict[str, object] = {
            "title": "Hotel Watchdog",
            "body": body,
            "summary": summary or body,
            "project": "Hotel Watchdog",
        }
        if url:
            payload["url"] = url

        request = Request(
            self.webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                self.log(
                    f"Hark accepted alert (delivered={result.get('delivered', '?')})."
                )
                return result
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hark returned HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError(f"Could not reach Hark: {error}") from error

    def send_background(
        self,
        body: str,
        *,
        summary: str | None = None,
        url: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        def worker() -> None:
            try:
                self.send(body, summary=summary, url=url)
            except Exception as error:  # noqa: BLE001 - alerts must never stop recording.
                self.log(f"Hark alert failed: {error}")

        threading.Thread(target=worker, name="hark-alert", daemon=True).start()
