import json
import subprocess
import unittest

from hotel_watchdog.tailscale import TailscaleShare, dns_name_from_status

STATUS = {
    "BackendState": "Running",
    "Self": {"DNSName": "watchdog.example-tailnet.ts.net."},
}


class TailscaleTests(unittest.TestCase):
    def test_dns_name(self):
        self.assertEqual(
            dns_name_from_status(STATUS), "watchdog.example-tailnet.ts.net"
        )

    def test_disconnected_status_fails(self):
        with self.assertRaises(RuntimeError):
            dns_name_from_status({"BackendState": "Stopped"})

    def test_expose_preserves_scoped_path(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            stdout = json.dumps(STATUS) if arguments[1:] == ["status", "--json"] else ""
            return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

        share = TailscaleShare(
            port=8766,
            route="/hotel-watchdog",
            executable="tailscale",
            runner=runner,
        )
        self.assertEqual(
            share.expose(),
            "https://watchdog.example-tailnet.ts.net/hotel-watchdog/",
        )
        self.assertIn(
            [
                "tailscale",
                "serve",
                "--bg",
                "--yes",
                "--set-path",
                "/hotel-watchdog",
                "http://127.0.0.1:8766",
            ],
            calls,
        )

    def test_recording_url_quotes_filename(self):
        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(
                arguments, 0, stdout=json.dumps(STATUS), stderr=""
            )

        share = TailscaleShare(port=8766, executable="tailscale", runner=runner)
        url = share.recording_url(__import__("pathlib").Path("motion clip.mp4"))
        self.assertTrue(url.endswith("/recordings/motion%20clip.mp4"))

    def test_evidence_url_quotes_filename(self):
        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(
                arguments, 0, stdout=json.dumps(STATUS), stderr=""
            )

        share = TailscaleShare(port=8766, executable="tailscale", runner=runner)
        url = share.evidence_url(__import__("pathlib").Path("person image.jpg"))
        self.assertTrue(url.endswith("/evidence/person%20image.jpg"))


if __name__ == "__main__":
    unittest.main()
