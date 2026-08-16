import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hotel_watchdog.config import Config
from hotel_watchdog.watchdog import Watchdog, read_exact


class WatchdogCommandTests(unittest.TestCase):
    def test_read_exact_can_stop_while_camera_pipe_is_idle(self):
        read_fd, write_fd = os.pipe()
        stop = threading.Event()
        stream = os.fdopen(read_fd, "rb", buffering=0)
        try:
            timer = threading.Timer(0.05, stop.set)
            timer.start()
            started = time.monotonic()
            self.assertIsNone(read_exact(stream, 10, stop))
            self.assertLess(time.monotonic() - started, 1)
            timer.join()
        finally:
            stream.close()
            os.close(write_fd)

    def test_recorder_delays_audio_for_video_pre_roll(self):
        with patch(
            "hotel_watchdog.watchdog.find_executable", return_value="/tmp/ffmpeg"
        ):
            watchdog = Watchdog(Config(auto_max_resolution=False))
        command = watchdog.recorder_command(Path("/tmp/test.mp4"), 3.0)
        offset_index = command.index("-itsoffset")
        audio_input_index = command.index(":0")
        self.assertEqual(command[offset_index + 1], "3.000")
        self.assertLess(offset_index, audio_input_index)


if __name__ == "__main__":
    unittest.main()
