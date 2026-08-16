import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hotel_watchdog.live import LiveServer, LiveState


class LiveServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary.name)
        self.state = LiveState()
        self.state.set_armed(True)
        self.state.update_frame(b"\xff\xd8fake-jpeg\xff\xd9")
        self.server = LiveServer(
            self.state, self.output_dir, port=0, log=lambda _message: None
        )
        self.port = self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stop()
        self.temporary.cleanup()

    def test_health_and_snapshot(self):
        with urlopen(self.base + "/health") as response:
            health = json.load(response)
        self.assertTrue(health["armed"])

        with urlopen(self.base + "/snapshot.jpg") as response:
            self.assertEqual(response.headers.get_content_type(), "image/jpeg")
            self.assertEqual(response.read(), b"\xff\xd8fake-jpeg\xff\xd9")

    def test_dashboard_has_live_stream(self):
        with urlopen(self.base + "/") as response:
            document = response.read().decode()
        self.assertIn("stream.mjpeg", document)
        self.assertIn("ARMED", document)

    def test_recording_range_request(self):
        recording = self.output_dir / "motion_test.mp4"
        recording.write_bytes(b"0123456789")
        request = Request(
            self.base + "/recordings/motion_test.mp4",
            headers={"Range": "bytes=2-5"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(response.read(), b"2345")

    def test_evidence_snapshot(self):
        evidence = self.output_dir / "person_2026-08-16_10-13-04.jpg"
        evidence.write_bytes(b"\xff\xd8evidence\xff\xd9")
        with urlopen(self.base + "/evidence/" + evidence.name) as response:
            self.assertEqual(response.headers.get_content_type(), "image/jpeg")
            self.assertEqual(response.read(), evidence.read_bytes())

    def test_motion_trigger_snapshot(self):
        evidence = self.output_dir / "motion_2026-08-16_10-13-04.jpg"
        evidence.write_bytes(b"\xff\xd8trigger\xff\xd9")
        with urlopen(self.base + "/evidence/" + evidence.name) as response:
            self.assertEqual(response.headers.get_content_type(), "image/jpeg")
            self.assertEqual(response.read(), evidence.read_bytes())

    def test_directory_traversal_is_rejected(self):
        with self.assertRaises(HTTPError) as context:
            urlopen(self.base + "/recordings/..%2Fsecret.mp4")
        context.exception.close()


if __name__ == "__main__":
    unittest.main()
