import unittest
from pathlib import Path

from hotel_watchdog.config import Config
from hotel_watchdog.service import LAUNCH_AGENT_LABEL, launch_agent_payload


class LaunchAgentTests(unittest.TestCase):
    def test_payload_runs_cli_in_aqua_session(self):
        config = Config(output_dir="/tmp/hotel-watchdog-tests")
        payload = launch_agent_payload(Path("/usr/local/bin/hotel-watchdog"), config)
        self.assertEqual(payload["Label"], LAUNCH_AGENT_LABEL)
        self.assertEqual(
            payload["ProgramArguments"],
            ["/usr/local/bin/hotel-watchdog", "run"],
        )
        self.assertEqual(payload["LimitLoadToSessionType"], "Aqua")
        self.assertTrue(payload["RunAtLoad"])
        self.assertFalse(payload["KeepAlive"])
        self.assertIn("/opt/homebrew/bin", payload["EnvironmentVariables"]["PATH"])


if __name__ == "__main__":
    unittest.main()
