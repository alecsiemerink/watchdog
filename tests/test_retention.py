import os
import tempfile
import time
import unittest
from pathlib import Path

from hotel_watchdog.retention import apply_retention


class RetentionTests(unittest.TestCase):
    def make_recording(
        self, directory: Path, name: str, size: int, age_days: int = 0
    ) -> Path:
        path = directory / name
        path.write_bytes(b"x" * size)
        timestamp = time.time() - age_days * 86_400
        os.utime(path, (timestamp, timestamp))
        return path

    def test_removes_old_recordings_but_not_active_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = self.make_recording(directory, "motion_old.mp4", 10, age_days=31)
            active = self.make_recording(
                directory, "motion_active.mp4", 10, age_days=31
            )
            result = apply_retention(
                directory,
                max_age_days=30,
                max_total_bytes=0,
                minimum_free_bytes=0,
                active_path=active,
            )
            self.assertEqual(result.removed, (old,))
            self.assertFalse(old.exists())
            self.assertTrue(active.exists())

    def test_active_file_counts_toward_total_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            oldest = self.make_recording(directory, "motion_old.mp4", 10, age_days=1)
            active = self.make_recording(directory, "motion_active.mp4", 10)
            result = apply_retention(
                directory,
                max_age_days=0,
                max_total_bytes=10,
                minimum_free_bytes=0,
                active_path=active,
            )
            self.assertEqual(result.removed, (oldest,))
            self.assertEqual(result.total_bytes, 10)
            self.assertTrue(active.exists())

    def test_size_limit_removes_oldest_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            oldest = self.make_recording(directory, "motion_1.mp4", 10, age_days=2)
            newest = self.make_recording(directory, "motion_2.mp4", 10, age_days=1)
            result = apply_retention(
                directory,
                max_age_days=0,
                max_total_bytes=10,
                minimum_free_bytes=0,
            )
            self.assertEqual(result.removed, (oldest,))
            self.assertTrue(newest.exists())

    def test_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = self.make_recording(directory, "motion_old.mp4", 10, age_days=31)
            result = apply_retention(
                directory,
                max_age_days=30,
                max_total_bytes=0,
                minimum_free_bytes=0,
                dry_run=True,
            )
            self.assertEqual(result.removed, (old,))
            self.assertTrue(old.exists())

    def test_evidence_snapshots_follow_retention(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = self.make_recording(
                directory, "person_2026-08-16_10-13-04.jpg", 10, age_days=31
            )
            result = apply_retention(
                directory,
                max_age_days=30,
                max_total_bytes=0,
                minimum_free_bytes=0,
            )
            self.assertEqual(result.removed, (old,))
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
