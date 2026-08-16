# Changelog

All notable changes are documented here. The project follows semantic versioning while the command-line and configuration formats stabilize.

## 0.2.0 — 2026-08-16

### Added

- On-device Apple Vision person detection with debounced confidence thresholds and annotated evidence images.
- Sustained camera darkness, cover, major-view-change, and recovery detection.
- Exact private trigger/person/tamper snapshot links in Hark notifications.
- Configurable video pre-roll with triggered-audio timestamp synchronization.
- Media age, total-size, and minimum-free-disk retention policies plus dry-run mode.
- macOS LaunchAgent install, status, and uninstall commands.
- Person and camera-warning states on the private live dashboard.

### Security and privacy

- Frames used for person detection remain on-device; no face recognition is performed.
- Evidence stays tailnet-only instead of using Hark's publicly fetched `imageUrl` field.
- Retention never deletes an active recording.

## 0.1.0 — 2026-08-16

- Initial private release with local motion detection, H.264/AAC clips, Hark alerts, Tailscale live viewing, mobile playback, secure configuration, CLI controls, doctor checks, tests, and CI.
