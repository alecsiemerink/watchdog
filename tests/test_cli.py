from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from watchdog_app.cli import process_is_watchdog


class ProcessIsWatchdogTests(TestCase):
    @patch("watchdog_app.cli.subprocess.run")
    @patch("watchdog_app.cli.pid_is_running", return_value=True)
    def test_accepts_console_script_process(self, _running, run):
        run.return_value = SimpleNamespace(
            stdout="/venv/bin/python /venv/bin/watchdog run\n"
        )
        self.assertTrue(process_is_watchdog(123))

    @patch("watchdog_app.cli.subprocess.run")
    @patch("watchdog_app.cli.pid_is_running", return_value=True)
    def test_accepts_module_process(self, _running, run):
        run.return_value = SimpleNamespace(
            stdout="/venv/bin/python -m watchdog_app run\n"
        )
        self.assertTrue(process_is_watchdog(123))

    @patch("watchdog_app.cli.subprocess.run")
    @patch("watchdog_app.cli.pid_is_running", return_value=True)
    def test_rejects_other_watchdog_command(self, _running, run):
        run.return_value = SimpleNamespace(stdout="/venv/bin/watchdog status\n")
        self.assertFalse(process_is_watchdog(123))

    @patch("watchdog_app.cli.subprocess.run")
    @patch("watchdog_app.cli.pid_is_running", return_value=False)
    def test_rejects_stopped_process(self, _running, run):
        self.assertFalse(process_is_watchdog(123))
        run.assert_not_called()
