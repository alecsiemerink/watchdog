# Roadmap

Hotel Watchdog keeps capture local, remote access private, and unattended behavior predictable.

## v0.1 — Initial private release

- [x] Local motion detection and H.264/AAC recording
- [x] Hark arming, motion, completion, and fatal-error alerts
- [x] Tailnet-only current snapshot, live view, and mobile MP4 playback
- [x] Secret-redacted mode-`0600` configuration
- [x] Idle-sleep prevention, device doctor, tests, CI, and open-source files

## v0.2 — Safer unattended monitoring

- [x] Local Apple Vision person detection without face recognition
- [x] Sustained camera obstruction, darkness, movement, and recovery detection
- [x] Immediate private trigger/person/tamper evidence links through Hark
- [x] Three-second, memory-bounded video pre-roll with synchronized triggered audio
- [x] Age, total-size, and free-disk retention policy with dry-run mode
- [x] Active-recording deletion protection and Hark disk warnings
- [x] macOS LaunchAgent install, status, and uninstall commands
- [x] Automatic maximum landscape resolution selection with a safe fallback
- [x] Camera/microphone permission onboarding and operational documentation
- [x] Private rich-preview research: keep evidence tailnet-only while Hark requires publicly fetched images

## Before making the repository public

- [ ] Run longer unattended soak tests on battery and mains power
- [ ] Test an additional Intel or Apple Silicon Mac/camera combination
- [ ] Verify LaunchAgent permission behavior for both `pipx` and virtualenv installs
- [ ] Perform a final secret and captured-media audit
- [ ] Decide whether v0.2 is the first public release

## Future candidates

- A signed/notarized menu-bar app with arm/disarm controls
- Native private notification media if Hark adds authenticated image fetching
- Optional encrypted off-device backups
- Additional redistribution-safe person-detection fixtures

## Deliberate non-goals

- Disabling or hiding the macOS camera indicator
- Public streaming by default
- Uploading recordings to a third-party cloud without explicit opt-in
- Face recognition, identity tracking, or biometric profiles
