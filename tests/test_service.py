import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hotel_watchdog.service import (
    LAUNCH_AGENT_LABEL,
    _stop_launcher_app,
    launch_agent_log_path,
    launch_agent_payload,
    launcher_app_path,
    launcher_pid_path,
)


class LaunchAgentTests(unittest.TestCase):
    def test_payload_runs_cli_in_aqua_session(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ, {"HOTEL_WATCHDOG_CONFIG_DIR": directory}, clear=False
            ),
        ):
            executable = Path("/usr/local/bin/hotel-watchdog")
            payload = launch_agent_payload(launcher_app_path(), executable)
            self.assertEqual(payload["Label"], LAUNCH_AGENT_LABEL)
            self.assertEqual(
                payload["ProgramArguments"],
                [
                    "/usr/bin/open",
                    "-W",
                    "-n",
                    str(launcher_app_path()),
                    "--args",
                    str(executable),
                    str(launch_agent_log_path()),
                    str(launcher_pid_path()),
                ],
            )
            self.assertEqual(payload["LimitLoadToSessionType"], "Aqua")
            self.assertTrue(payload["RunAtLoad"])
            self.assertFalse(payload["KeepAlive"])
            self.assertIn("/opt/homebrew/bin", payload["EnvironmentVariables"]["PATH"])
            self.assertEqual(payload["StandardOutPath"], str(launch_agent_log_path()))
            self.assertNotIn("/Movies/", payload["StandardOutPath"])

    def test_stop_launcher_ignores_unrelated_stale_pid(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ, {"HOTEL_WATCHDOG_CONFIG_DIR": directory}, clear=False
            ),
            patch(
                "hotel_watchdog.service.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 0, "/usr/bin/unrelated", ""
                ),
            ),
            patch("hotel_watchdog.service.os.kill") as kill,
        ):
            launcher_pid_path().write_text("123\n", encoding="utf-8")
            _stop_launcher_app()
            kill.assert_not_called()
            self.assertFalse(launcher_pid_path().exists())

    def test_stop_launcher_signals_its_verified_process_group(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ, {"HOTEL_WATCHDOG_CONFIG_DIR": directory}, clear=False
            ),
            patch(
                "hotel_watchdog.service.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    str(
                        launcher_app_path()
                        / "Contents"
                        / "MacOS"
                        / "HotelWatchdogLauncher"
                    ),
                    "",
                ),
            ),
            patch("hotel_watchdog.service.os.getpgid", return_value=456),
            patch("hotel_watchdog.service.os.killpg") as kill_group,
            patch("hotel_watchdog.service.os.kill", side_effect=ProcessLookupError),
        ):
            launcher_pid_path().write_text("456\n", encoding="utf-8")
            _stop_launcher_app()
            kill_group.assert_called_once_with(456, 15)
            self.assertFalse(launcher_pid_path().exists())


if __name__ == "__main__":
    unittest.main()
