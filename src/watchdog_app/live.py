from __future__ import annotations

import html
import json
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

Log = Callable[[str], None]
RECORDING_NAME = re.compile(
    r"motion_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.(?:mp4|mov)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LiveStatus:
    armed: bool
    recording: bool
    person_present: bool
    camera_warning: bool
    started_at: str
    latest_recording: str | None
    last_event: str


class LiveState:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._jpeg: bytes | None = None
        self._frame_version = 0
        self._armed = False
        self._recording = False
        self._person_present = False
        self._camera_warning = False
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._latest_recording: str | None = None
        self._last_event = "Starting"

    def update_frame(self, jpeg: bytes) -> None:
        with self._condition:
            self._jpeg = jpeg
            self._frame_version += 1
            self._condition.notify_all()

    def wait_for_frame(
        self, previous_version: int, timeout: float = 10
    ) -> tuple[bytes | None, int]:
        with self._condition:
            if self._frame_version == previous_version:
                self._condition.wait(timeout=timeout)
            return self._jpeg, self._frame_version

    def snapshot(self) -> bytes | None:
        with self._condition:
            return self._jpeg

    def set_armed(self, armed: bool) -> None:
        with self._condition:
            self._armed = armed
            self._last_event = "Armed" if armed else "Stopped"

    def set_recording(self, recording: bool, filename: str | None = None) -> None:
        with self._condition:
            self._recording = recording
            if recording:
                self._last_event = "Motion detected — recording"
            else:
                self._last_event = "Monitoring"
                if filename:
                    self._latest_recording = filename

    def set_person_present(self, present: bool) -> None:
        with self._condition:
            self._person_present = present
            if present:
                self._last_event = "Person detected"
            elif not self._recording and not self._camera_warning:
                self._last_event = "Monitoring"

    def set_camera_warning(self, warning: bool) -> None:
        with self._condition:
            self._camera_warning = warning
            if warning:
                self._last_event = "Camera may be obstructed or moved"
            elif not self._recording and not self._person_present:
                self._last_event = "Monitoring"

    def status(self) -> LiveStatus:
        with self._condition:
            return LiveStatus(
                armed=self._armed,
                recording=self._recording,
                person_present=self._person_present,
                camera_warning=self._camera_warning,
                started_at=self._started_at,
                latest_recording=self._latest_recording,
                last_event=self._last_event,
            )


class WatchdogHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def recording_library(output_dir: Path, limit: int = 100) -> list[dict[str, str]]:
    recordings: list[tuple[float, Path]] = []
    for candidate in output_dir.iterdir():
        if not candidate.is_file() or not RECORDING_NAME.fullmatch(candidate.name):
            continue
        try:
            recordings.append((candidate.stat().st_mtime, candidate))
        except OSError:
            continue
    recordings.sort(key=lambda item: item[0], reverse=True)

    result: list[dict[str, str]] = []
    for modified, candidate in recordings[:limit]:
        match = RECORDING_NAME.fullmatch(candidate.name)
        assert match is not None
        try:
            timestamp = datetime.strptime(
                match.group(1), "%Y-%m-%d_%H-%M-%S"
            ).astimezone()
        except ValueError:
            timestamp = datetime.fromtimestamp(modified).astimezone()
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        result.append(
            {
                "name": candidate.name,
                "url": "recordings/" + quote(candidate.name),
                "iso": timestamp.isoformat(timespec="seconds"),
                "timestamp": timestamp.strftime("%b %d, %Y · %H:%M:%S %Z"),
                "size": f"{size / 1_000_000:.1f} MB",
            }
        )
    return result


def recording_library_html(output_dir: Path) -> str:
    recordings = recording_library(output_dir)
    if not recordings:
        return '<p class="empty">No completed recordings yet.</p>'
    return "".join(
        '<a class="recording" href="{url}">'
        '<span><strong>Motion recording</strong><time datetime="{iso}">{timestamp}</time></span>'
        '<span class="recording-meta">{size}<b aria-hidden="true">›</b></span>'
        "</a>".format(
            url=html.escape(item["url"], quote=True),
            iso=html.escape(item["iso"], quote=True),
            timestamp=html.escape(item["timestamp"]),
            size=html.escape(item["size"]),
        )
        for item in recordings
    )


def make_handler(
    state: LiveState,
    output_dir: Path,
    hls_dir: Path,
    live_audio: bool,
    log: Log,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Watchdog/0.1"

        def log_message(self, format_string: str, *args) -> None:
            log("Live view: " + (format_string % args))

        def do_HEAD(self) -> None:
            try:
                self._route(head_only=True)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True

        def do_GET(self) -> None:
            try:
                self._route(head_only=False)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True

        def _route(self, *, head_only: bool) -> None:
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._dashboard(head_only=head_only)
            elif path == "/health":
                self._health(head_only=head_only)
            elif path == "/snapshot.jpg":
                self._snapshot(head_only=head_only)
            elif path == "/stream.mjpeg" and not head_only:
                self._stream()
            elif path.startswith("/live/"):
                self._hls(path.removeprefix("/live/"), head_only=head_only)
            elif path.startswith("/recordings/"):
                self._recording(path.removeprefix("/recordings/"), head_only=head_only)
            elif path.startswith("/evidence/"):
                self._evidence(path.removeprefix("/evidence/"), head_only=head_only)
            else:
                self.send_error(404)

        def _common_headers(self) -> None:
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")

        def _dashboard(self, *, head_only: bool) -> None:
            current = state.status()
            recordings = recording_library_html(output_dir)
            audio_note = (
                "Live microphone audio is available — tap the speaker to unmute."
                if live_audio
                else "The native live stream is video-only by configuration."
            )
            if current.camera_warning:
                status_class, status_text = "warning", "CAMERA CHECK"
            elif current.recording:
                status_class, status_text = "recording", "RECORDING"
            elif current.person_present:
                status_class, status_text = "person", "PERSON"
            else:
                status_class = "armed"
                status_text = "ARMED" if current.armed else "OFFLINE"
            document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="color-scheme" content="dark">
  <title>Watchdog</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-rounded, -apple-system, BlinkMacSystemFont, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: #080b0f; color: #f4f7f5; }}
    main {{ width: min(920px, 100%); margin: auto; padding: 18px; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }}
    h1 {{ font-size: clamp(1.35rem, 5vw, 2rem); margin: 0; letter-spacing: -.03em; }}
    .status {{ border: 1px solid #284331; color: #75ee9c; background: #102018; border-radius: 999px; padding: 7px 11px; font: 700 .72rem ui-monospace, monospace; letter-spacing: .08em; }}
    .status.recording {{ border-color: #613636; color: #ff8a8a; background: #2b1212; }}
    .status.person {{ border-color: #31577a; color: #83c7ff; background: #102238; }}
    .status.warning {{ border-color: #735f26; color: #ffd86a; background: #2d260f; }}
    .viewer {{ overflow: hidden; border-radius: 18px; border: 1px solid #242a31; background: #11161c; box-shadow: 0 24px 70px #0008; }}
    .viewer video, .viewer img {{ width: 100%; height: auto; display: block; aspect-ratio: 4/3; object-fit: cover; background: #050607; }}
    .meta {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; padding: 12px 14px; color: #aeb8b0; font-size: .88rem; }}
    .audio-note {{ padding: 0 14px 13px; color: #91a299; font-size: .8rem; }}
    .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }}
    .button {{ display: block; text-align: center; color: #07110b; background: #71e998; text-decoration: none; padding: 13px; border-radius: 12px; font-weight: 750; }}
    .button.secondary {{ color: #e9efea; background: #1a2128; border: 1px solid #303840; }}
    .library {{ margin-top: 26px; }}
    .library h2 {{ margin: 0 0 10px; font-size: 1.1rem; }}
    .recording-list {{ overflow: hidden; border: 1px solid #242a31; border-radius: 14px; background: #11161c; }}
    .recording {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 13px 14px; color: #eef3ef; text-decoration: none; border-bottom: 1px solid #242a31; }}
    .recording:last-child {{ border-bottom: 0; }}
    .recording:hover {{ background: #171d24; }}
    .recording strong, .recording time {{ display: block; }}
    .recording time {{ color: #94a099; font-size: .82rem; margin-top: 3px; }}
    .recording-meta {{ display: flex; align-items: center; gap: 10px; color: #94a099; font-size: .82rem; white-space: nowrap; }}
    .recording-meta b {{ color: #71e998; font-size: 1.5rem; line-height: 1; }}
    .empty {{ margin: 0; padding: 16px; color: #94a099; }}
    footer {{ color: #69736c; text-align: center; font-size: .78rem; margin-top: 18px; }}
    @media (max-width: 560px) {{ .actions {{ grid-template-columns: 1fr; }} main {{ padding: 12px; }} }}
  </style>
</head>
<body>
  <main>
    <header><h1>Watchdog</h1><span class="status {status_class}">{status_text}</span></header>
    <section class="viewer">
      <video id="live-player" controls autoplay muted playsinline poster="snapshot.jpg" aria-label="Live room camera with audio"></video>
      <img id="mjpeg-fallback" src="stream.mjpeg" alt="Live room camera video fallback" hidden>
      <div class="meta"><span>{html.escape(current.last_event)}</span><span>Started {html.escape(current.started_at)}</span></div>
      <div class="audio-note">{html.escape(audio_note)}</div>
    </section>
    <nav class="actions">
      <a class="button" href="snapshot.jpg">Open current snapshot</a>
      <a class="button secondary" href="stream.mjpeg">Low-bandwidth video fallback</a>
    </nav>
    <section class="library">
      <h2>Recordings</h2>
      <div class="recording-list">{recordings}</div>
    </section>
    <footer>Tailnet-only live view · no cloud video storage</footer>
  </main>
  <script>
    (() => {{
      const player = document.getElementById("live-player");
      const fallback = document.getElementById("mjpeg-fallback");
      const playlist = "live/live.m3u8";
      const showFallback = () => {{ player.hidden = true; fallback.hidden = false; }};
      if (!player.canPlayType("application/vnd.apple.mpegurl")) {{
        showFallback();
        return;
      }}
      player.addEventListener("error", showFallback, {{ once: true }});
      const connect = async () => {{
        for (let attempt = 0; attempt < 30; attempt += 1) {{
          try {{
            const response = await fetch(playlist, {{ cache: "no-store" }});
            if (response.ok) {{
              player.src = playlist;
              player.play().catch(() => {{}});
              return;
            }}
          }} catch (_) {{}}
          await new Promise(resolve => setTimeout(resolve, 500));
        }}
        showFallback();
      }};
      connect();
    }})();
  </script>
</body>
</html>
""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(document)))
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(document)

        def _health(self, *, head_only: bool) -> None:
            payload = json.dumps(asdict(state.status())).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def _snapshot(self, *, head_only: bool) -> None:
            jpeg = state.snapshot()
            if not jpeg:
                self.send_error(503, "Camera is warming up")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(jpeg)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self._common_headers()
            self.end_headers()
            version = -1
            try:
                while True:
                    jpeg, next_version = state.wait_for_frame(version)
                    if not jpeg or next_version == version:
                        continue
                    version = next_version
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return

        def _hls(self, raw_name: str, *, head_only: bool) -> None:
            name = unquote(raw_name)
            if not re.fullmatch(r"(?:live\.m3u8|segment_\d{6,12}\.ts)", name):
                self.send_error(404)
                return
            path = hls_dir / name
            if not path.is_file():
                if name == "live.m3u8":
                    self.send_error(503, "Native live stream is warming up")
                else:
                    self.send_error(404)
                return
            try:
                payload = path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            content_type = (
                "application/vnd.apple.mpegurl" if name == "live.m3u8" else "video/mp2t"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)

        def _recording(self, raw_name: str, *, head_only: bool) -> None:
            name = unquote(raw_name)
            if not re.fullmatch(
                r"[A-Za-z0-9_.-]+\.(?:mp4|mov)", name, flags=re.IGNORECASE
            ):
                self.send_error(404)
                return
            path = output_dir / name
            if not path.is_file():
                self.send_error(404)
                return
            self._send_file(path, head_only=head_only)

        def _evidence(self, raw_name: str, *, head_only: bool) -> None:
            name = unquote(raw_name)
            if not re.fullmatch(
                r"(?:motion|person|tamper)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.jpg",
                name,
            ):
                self.send_error(404)
                return
            path = output_dir / name
            if not path.is_file():
                self.send_error(404)
                return
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(size))
            self._common_headers()
            self.end_headers()
            if not head_only:
                self.wfile.write(path.read_bytes())

        def _send_file(self, path: Path, *, head_only: bool) -> None:
            size = path.stat().st_size
            start = 0
            end = size - 1
            response_code = 200
            range_header = self.headers.get("Range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match:
                    self.send_error(416)
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = int(last) if last else end
                elif last:
                    length = int(last)
                    start = max(0, size - length)
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                response_code = 206

            length = end - start + 1
            self.send_response(response_code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            if response_code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self._common_headers()
            self.end_headers()
            if head_only:
                return

            remaining = length
            with path.open("rb") as source:
                source.seek(start)
                while remaining:
                    chunk = source.read(min(128 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return Handler


class LiveServer:
    def __init__(
        self,
        state: LiveState,
        output_dir: Path,
        port: int,
        log: Log = print,
        *,
        hls_dir: Path | None = None,
        live_audio: bool = True,
    ) -> None:
        self.state = state
        self.output_dir = output_dir
        self.port = port
        self.log = log
        self.hls_dir = hls_dir or output_dir / ".watchdog-live"
        self.live_audio = live_audio
        self._server: WatchdogHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        handler = make_handler(
            self.state,
            self.output_dir,
            self.hls_dir,
            self.live_audio,
            self.log,
        )
        self._server = WatchdogHTTPServer(("127.0.0.1", self.port), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="watchdog-live-server",
            daemon=True,
        )
        self._thread.start()
        self.log(f"Live server listening on http://127.0.0.1:{self.port}.")
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
