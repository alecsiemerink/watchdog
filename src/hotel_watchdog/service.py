from __future__ import annotations

import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import Config, config_dir

LAUNCH_AGENT_LABEL = "com.alecsiemerink.hotel-watchdog"
LAUNCHER_BUNDLE_ID = f"{LAUNCH_AGENT_LABEL}.launcher"
LAUNCHER_NAME = "Hotel Watchdog"

_LAUNCHER_SOURCE = r"""
#import <AVFoundation/AVFoundation.h>
#import <AppKit/AppKit.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile sig_atomic_t child_pid = -1;
static volatile sig_atomic_t terminate_requested = 0;
static const char *launcher_pid_path = NULL;

static void forward_signal(int signal_number) {
    terminate_requested = 1;
    if (child_pid > 0) {
        kill(child_pid, signal_number);
    }
}

static void remove_pid_file(void) {
    if (launcher_pid_path != NULL) {
        unlink(launcher_pid_path);
    }
}

static int request_access(AVMediaType media_type, const char *label) {
    AVAuthorizationStatus status =
        [AVCaptureDevice authorizationStatusForMediaType:media_type];
    if (status == AVAuthorizationStatusAuthorized) {
        return 1;
    }
    if (status == AVAuthorizationStatusDenied ||
        status == AVAuthorizationStatusRestricted) {
        fprintf(
            stderr,
            "Hotel Watchdog does not have %s access. Enable it in System "
            "Settings > Privacy & Security.\n",
            label
        );
        return 0;
    }

    __block BOOL granted = NO;
    __block BOOL completed = NO;
    [AVCaptureDevice requestAccessForMediaType:media_type
                            completionHandler:^(BOOL allowed) {
        granted = allowed;
        completed = YES;
    }];
    while (!completed && !terminate_requested) {
        [[NSRunLoop currentRunLoop]
            runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
    }
    if (terminate_requested) {
        return 0;
    }
    if (!granted) {
        fprintf(stderr, "Hotel Watchdog %s access was denied.\n", label);
    }
    return granted ? 1 : 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [NSApp finishLaunching];
    [NSApp activateIgnoringOtherApps:YES];
    if (argc < 4) {
        fputs("Hotel Watchdog launcher configuration is missing.\n", stderr);
        return 78;
    }

    int log_fd = open(argv[2], O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (log_fd >= 0) {
        fchmod(log_fd, 0600);
        dup2(log_fd, STDOUT_FILENO);
        dup2(log_fd, STDERR_FILENO);
        close(log_fd);
    }
    setenv(
        "PATH",
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        1
    );
    launcher_pid_path = argv[3];
    int pid_fd = open(
        launcher_pid_path,
        O_WRONLY | O_CREAT | O_TRUNC,
        0600
    );
    if (pid_fd >= 0) {
        fchmod(pid_fd, 0600);
        dprintf(pid_fd, "%d\n", getpid());
        close(pid_fd);
    }
    atexit(remove_pid_file);
    signal(SIGTERM, forward_signal);
    signal(SIGINT, forward_signal);
    if (!request_access(AVMediaTypeVideo, "Camera") ||
        !request_access(AVMediaTypeAudio, "Microphone")) {
        return 77;
    }
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];

    child_pid = fork();
    if (child_pid < 0) {
        perror("Could not fork Hotel Watchdog");
        return 71;
    }
    if (child_pid == 0) {
        execl(argv[1], argv[1], "run", (char *)NULL);
        perror("Could not start Hotel Watchdog");
        _exit(70);
    }

    int status = 0;
    while (waitpid(child_pid, &status, 0) < 0 && errno == EINTR) {}
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 1;
    }
}
"""


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_agent_log_path() -> Path:
    # launchd can reject protected user folders such as ~/Movies as direct
    # stdout/stderr destinations before the Python process even starts.
    return config_dir() / "launchagent.log"


def launcher_app_path() -> Path:
    return config_dir() / f"{LAUNCHER_NAME}.app"


def launcher_pid_path() -> Path:
    return config_dir() / "launcher.pid"


def resolve_cli_executable() -> Path:
    executable = shutil.which("hotel-watchdog")
    if executable:
        return Path(executable).resolve()
    candidate = Path(sys.argv[0]).resolve()
    if candidate.is_file():
        return candidate
    raise RuntimeError("Could not resolve the installed hotel-watchdog executable.")


def launch_agent_payload(app_path: Path, executable: Path) -> dict[str, object]:
    log_path = launch_agent_log_path()
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            "/usr/bin/open",
            "-W",
            "-n",
            str(app_path),
            "--args",
            str(executable),
            str(log_path),
            str(launcher_pid_path()),
        ],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "ThrottleInterval": 30,
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def install_launcher_app(executable: Path) -> Path:
    """Build the tiny app wrapper macOS needs for camera/mic TCC attribution."""
    app_path = launcher_app_path()
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    launcher = macos / "HotelWatchdogLauncher"
    info = {
        "CFBundleDisplayName": LAUNCHER_NAME,
        "CFBundleExecutable": launcher.name,
        "CFBundleIdentifier": LAUNCHER_BUNDLE_ID,
        "CFBundleName": LAUNCHER_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "NSCameraUsageDescription": (
            "Hotel Watchdog uses the camera to detect room activity and record evidence."
        ),
        "NSMicrophoneUsageDescription": (
            "Hotel Watchdog uses the microphone when recording a detected event."
        ),
    }
    with (contents / "Info.plist").open("wb") as destination:
        plistlib.dump(info, destination, sort_keys=False)

    clang = subprocess.run(
        ["/usr/bin/xcrun", "--find", "clang"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    sdk_path = subprocess.run(
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix="hotel-watchdog-launcher-") as directory:
        source = Path(directory) / "main.m"
        source.write_text(_LAUNCHER_SOURCE, encoding="utf-8")
        subprocess.run(
            [
                clang,
                "-isysroot",
                sdk_path,
                "-fobjc-arc",
                "-mmacosx-version-min=13.0",
                "-O2",
                "-framework",
                "Foundation",
                "-framework",
                "AVFoundation",
                "-framework",
                "AppKit",
                "-o",
                str(launcher),
                str(source),
            ],
            check=True,
        )
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", str(app_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return app_path


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _stop_launcher_app() -> None:
    path = launcher_pid_path()
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        path.unlink(missing_ok=True)
        return
    expected = str(launcher_app_path() / "Contents" / "MacOS" / "HotelWatchdogLauncher")
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode or expected not in result.stdout:
        path.unlink(missing_ok=True)
        return

    try:
        process_group = os.getpgid(pid)
        if process_group == pid:
            os.killpg(process_group, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            path.unlink(missing_ok=True)
            return
        time.sleep(0.1)
    try:
        if process_group == pid:
            os.killpg(process_group, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    path.unlink(missing_ok=True)


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
    launch_agent_log_path().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = launch_agent_path()
    subprocess.run(
        ["/bin/launchctl", "bootout", _domain(), str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _stop_launcher_app()
    app_path = install_launcher_app(executable)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as destination:
        plistlib.dump(
            launch_agent_payload(app_path, executable),
            destination,
            sort_keys=False,
        )
    temporary.chmod(0o644)
    temporary.replace(path)

    subprocess.run(
        ["/bin/launchctl", "enable", f"{_domain()}/{LAUNCH_AGENT_LABEL}"],
        check=True,
    )
    if start_now:
        subprocess.run(
            ["/bin/launchctl", "bootstrap", _domain(), str(path)], check=True
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
    _stop_launcher_app()
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
