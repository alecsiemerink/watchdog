import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from watchdog_app.live import LiveServer, LiveState


class LiveServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary.name)
        self.hls_dir = self.output_dir / "live"
        self.hls_dir.mkdir()
        self.state = LiveState()
        self.state.set_armed(True)
        self.state.update_frame(b"\xff\xd8fake-jpeg\xff\xd9")
        self.server = LiveServer(
            self.state,
            self.output_dir,
            port=0,
            log=lambda _message: None,
            hls_dir=self.hls_dir,
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
        older = self.output_dir / "motion_2026-08-15_09-08-07.mp4"
        newer = self.output_dir / "motion_2026-08-16_14-13-12.mp4"
        older.write_bytes(b"older")
        newer.write_bytes(b"newer")
        with urlopen(self.base + "/") as response:
            document = response.read().decode()
        self.assertIn('<video id="live-player" controls', document)
        self.assertIn('const playlist = "live/live.m3u8"', document)
        self.assertIn("Live microphone audio is available", document)
        self.assertIn("stream.mjpeg", document)
        self.assertIn("ARMED", document)
        self.assertIn("Recordings", document)
        self.assertIn("Aug 16, 2026 · 14:13:12", document)
        self.assertLess(document.index(newer.name), document.index(older.name))

    def test_hls_playlist_and_segment(self):
        playlist = self.hls_dir / "live.m3u8"
        segment = self.hls_dir / "segment_000001.ts"
        playlist.write_text("#EXTM3U\nsegment_000001.ts\n", encoding="utf-8")
        segment.write_bytes(b"transport-stream")

        with urlopen(self.base + "/live/live.m3u8") as response:
            self.assertEqual(
                response.headers.get_content_type(), "application/vnd.apple.mpegurl"
            )
            self.assertEqual(response.read(), playlist.read_bytes())
        with urlopen(self.base + "/live/segment_000001.ts") as response:
            self.assertEqual(response.headers.get_content_type(), "video/mp2t")
            self.assertEqual(response.read(), segment.read_bytes())

    def test_hls_directory_traversal_is_rejected(self):
        with self.assertRaises(HTTPError) as context:
            urlopen(self.base + "/live/..%2Fconfig.json")
        context.exception.close()

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
