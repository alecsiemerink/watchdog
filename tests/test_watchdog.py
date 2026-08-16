import unittest
from pathlib import Path
from unittest.mock import patch

from hotel_watchdog.config import Config
from hotel_watchdog.watchdog import Watchdog


class WatchdogCommandTests(unittest.TestCase):
    def test_recorder_delays_audio_for_video_pre_roll(self):
        with patch(
            "hotel_watchdog.watchdog.find_executable", return_value="/tmp/ffmpeg"
        ):
            watchdog = Watchdog(Config())
        command = watchdog.recorder_command(Path("/tmp/test.mp4"), 3.0)
        offset_index = command.index("-itsoffset")
        audio_input_index = command.index(":0")
        self.assertEqual(command[offset_index + 1], "3.000")
        self.assertLess(offset_index, audio_input_index)


if __name__ == "__main__":
    unittest.main()
