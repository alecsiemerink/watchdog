# Changelog

All notable changes are documented here. The project follows semantic versioning while the command-line and configuration formats stabilize.

## 0.1.0 — 2026-08-16

First public release.

### Monitoring and evidence

- Local motion detection with automatic maximum-landscape-resolution selection.
- H.264/AAC recordings with a configurable, memory-bounded video pre-roll and synchronized triggered audio.
- On-device Apple Vision person detection with debounced confidence thresholds and annotated evidence images.
- Sustained camera darkness, cover, major-view-change, and recovery detection.
- Exact trigger, person, tamper, and completed-recording links in Hark notifications.
- Tailnet-only snapshots, MJPEG live view, and byte-range MP4 playback through Tailscale Serve.

### Operations and privacy

- Age, total-size, and minimum-free-disk retention policies that never remove an active recording.
- Secret-redacted mode-`0600` configuration and no cloud vision processing or face recognition.
- macOS LaunchAgent installation through a small ad-hoc-signed permission launcher.
- Interruptible camera reads, duplicate-process protection, device diagnostics, and automatic login startup.
- Unit-test coverage and CI on Python 3.10, 3.12, and 3.14.
