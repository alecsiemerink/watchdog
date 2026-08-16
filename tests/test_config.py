import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watchdog_app.config import Config, config_path, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_legacy_config_is_migrated_without_losing_webhook(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch("watchdog_app.config.Path.home", return_value=Path(directory)),
        ):
            legacy = Path(directory) / ".config" / "hotel-watchdog" / "config.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                json.dumps(
                    {
                        "hark_webhook_url": "https://example.test/secret",
                        "output_dir": "~/Movies/HotelWatchdog",
                        "tailscale_path": "/hotel-watchdog",
                    }
                ),
                encoding="utf-8",
            )
            config = load_config()
            self.assertEqual(config.hark_webhook_url, "https://example.test/secret")
            self.assertEqual(config.output_dir, "~/Movies/HotelWatchdog")
            self.assertEqual(config.tailscale_path, "/watchdog")
            self.assertTrue(config_path().exists())
            self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)

    def test_round_trip_and_permissions(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"WATCHDOG_CONFIG_DIR": directory}, clear=False),
        ):
            expected = Config(
                hark_webhook_url="https://example.test/secret", tailscale_share=True
            )
            path = save_config(expected)
            actual = load_config()

            self.assertEqual(actual.hark_webhook_url, expected.hark_webhook_url)
            self.assertTrue(actual.tailscale_share)
            self.assertEqual(path, config_path())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_environment_webhook_overrides_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"hark_webhook_url": "https://file.test"}))
            with patch.dict(
                os.environ,
                {
                    "WATCHDOG_CONFIG_DIR": directory,
                    "WATCHDOG_HARK_URL": "https://environment.test",
                },
                clear=False,
            ):
                self.assertEqual(
                    load_config().hark_webhook_url, "https://environment.test"
                )

    def test_public_dict_redacts_webhook(self):
        public = Config(hark_webhook_url="secret").public_dict()
        self.assertEqual(public["hark_webhook_url"], "configured (redacted)")
        self.assertNotIn("secret", json.dumps(public))

    def test_invalid_pre_roll_is_rejected(self):
        with self.assertRaises(ValueError):
            Config(pre_roll_seconds=11).validate()

    def test_broad_output_directories_are_rejected(self):
        with self.assertRaises(ValueError):
            Config(output_dir=str(Path.home())).validate()
        with self.assertRaises(ValueError):
            Config(output_dir="/").validate()


if __name__ == "__main__":
    unittest.main()
