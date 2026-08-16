# Changelog

All notable changes are documented here. The project follows semantic versioning while the command-line and configuration formats stabilize.

## 0.2.2 — 2026-08-16

- Write LaunchAgent stdout/stderr to the app config directory instead of `~/Movies`, preventing launchd `EX_CONFIG` failures on protected media folders.
- Launch the watcher through a small ad-hoc-signed app wrapper so macOS can grant the login service a stable Camera and Microphone permission identity.
- Make idle camera reads interruptible so stop/uninstall can cleanly terminate while a permission prompt or camera is stalled.
- Prevent a second direct `run` process from replacing the active watcher's PID file.
- Print the service-specific log path after LaunchAgent installation.

## 0.2.1 — 2026-08-16

- Auto-probe AVFoundation camera modes and select the compatible landscape resolution with the most pixels by default.
- Keep configured width and height as safe fallbacks and add `--no-max-resolution` for explicit resolution control.
- Verify this Mac's FaceTime HD Camera at 1760×1328 with a 1760×1328 live snapshot.

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
