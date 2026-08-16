import json
import unittest
from unittest.mock import patch

from watchdog_app.hark import HarkClient


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"ok":true,"delivered":1}'


class HarkClientTests(unittest.TestCase):
    def test_notification_payload_includes_private_tap_url(self):
        with patch("watchdog_app.hark.urlopen", return_value=FakeResponse()) as send:
            result = HarkClient(
                "https://hark.example/hooks/secret", log=lambda _message: None
            ).send(
                "Motion detected",
                summary="Tap for snapshot",
                url="https://watchdog.tail.example/evidence/motion.jpg",
            )
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertTrue(result["ok"])
        self.assertEqual(payload["summary"], "Tap for snapshot")
        self.assertEqual(
            payload["url"],
            "https://watchdog.tail.example/evidence/motion.jpg",
        )
        self.assertNotIn("imageUrl", payload)

    def test_missing_webhook_is_a_noop(self):
        self.assertTrue(HarkClient("").send("ignored")["skipped"])


if __name__ == "__main__":
    unittest.main()
