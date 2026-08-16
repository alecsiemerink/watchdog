import subprocess
import unittest

from watchdog_app.config import Config
from watchdog_app.resolution import (
    CameraMode,
    parse_camera_modes,
    resolve_max_resolution,
    select_max_landscape_mode,
)

CAMERA_OUTPUT = """
[in#0] Supported modes:
[in#0]   1920x1080@[15.000000 30.000000]fps
[in#0]   1280x720@[15.000000 30.000000]fps
[in#0]   1080x1920@[15.000000 30.000000]fps
[in#0]   1760x1328@[15.000000 30.000000]fps
[in#0]   640x480@[15.000000 30.000000]fps
[in#0]   1328x1760@[15.000000 30.000000]fps
[in#0]   1552x1552@[15.000000 30.000000]fps
"""


class ResolutionTests(unittest.TestCase):
    def test_parses_and_selects_largest_landscape_mode(self):
        modes = parse_camera_modes(CAMERA_OUTPUT)
        selected = select_max_landscape_mode(modes, 30)
        self.assertEqual(selected, CameraMode(1760, 1328, 15, 30))

    def test_selection_respects_requested_frame_rate(self):
        modes = [
            CameraMode(3840, 2160, 15, 24),
            CameraMode(1920, 1080, 15, 60),
        ]
        self.assertEqual(
            select_max_landscape_mode(modes, 30),
            CameraMode(1920, 1080, 15, 60),
        )

    def test_runtime_config_uses_probed_mode(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(
                arguments, 1, stdout="", stderr=CAMERA_OUTPUT
            )

        config = resolve_max_resolution(
            Config(), "/tmp/ffmpeg", runner=runner, log=lambda _message: None
        )
        self.assertEqual((config.width, config.height), (1760, 1328))
        self.assertIn("2x2", calls[0])

    def test_auto_selection_can_be_disabled(self):
        config = Config(width=800, height=600, auto_max_resolution=False)
        self.assertIs(resolve_max_resolution(config, "/tmp/missing"), config)


if __name__ == "__main__":
    unittest.main()
