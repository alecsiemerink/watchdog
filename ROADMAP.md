# Roadmap

Hotel Watchdog follows a small-core approach: reliable local capture first, private remote access second, convenience features after that.

## v0.1 — Initial private release

- [x] Motion detection using the Mac camera and local FFmpeg
- [x] H.264 video and AAC audio recordings
- [x] Hark notifications for arming, motion, completed clips, and fatal errors
- [x] Current snapshot and low-frame-rate live view
- [x] Tailnet-only HTTPS access through Tailscale Serve
- [x] Mobile playback with HTTP byte-range support
- [x] Secure local webhook configuration with secret redaction
- [x] Background operation with idle-sleep prevention
- [x] Doctor command for real camera and microphone verification
- [x] Unit tests on Python 3.10, 3.12, and 3.14
- [x] Open-source-ready README, MIT license, security policy, CI, and dependency updates

## Before making the repository public

- [ ] Run longer unattended soak tests on battery and mains power
- [ ] Test at least one additional Intel or Apple Silicon Mac/camera combination
- [ ] Review macOS permission behavior when installed with `pipx`
- [ ] Perform a final secret and captured-media audit
- [ ] Add release notes and tag the first public version

## Backlog

- [Camera obstruction or movement detection](https://github.com/alecsiemerink/hotel-watchdog/issues/1)
- [Pre-trigger recording buffer](https://github.com/alecsiemerink/hotel-watchdog/issues/2)
- [Recording retention and disk-space policy](https://github.com/alecsiemerink/hotel-watchdog/issues/3)
- [Native macOS background and menu-bar experience](https://github.com/alecsiemerink/hotel-watchdog/issues/4)
- [Private rich snapshot previews in Hark](https://github.com/alecsiemerink/hotel-watchdog/issues/5)

## Deliberate non-goals

- Disabling or hiding the macOS camera indicator
- Public streaming by default
- Uploading recordings to a third-party cloud without explicit opt-in
- Face recognition or identity tracking
