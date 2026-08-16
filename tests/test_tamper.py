import unittest

from hotel_watchdog.tamper import CameraTamperDetector


class CameraTamperDetectorTests(unittest.TestCase):
    def detector(self) -> CameraTamperDetector:
        return CameraTamperDetector(
            baseline_frames=2,
            required_hits=2,
            recovery_hits=2,
            changed_fraction=0.75,
        )

    def test_sustained_darkness_and_recovery(self):
        detector = self.detector()
        baseline = bytes([60, 100, 140, 180] * 10)
        detector.update(baseline)
        detector.update(baseline)
        event, metrics = detector.update(bytes([0] * 40))
        self.assertIsNone(event)
        self.assertEqual(metrics.reason, "dark")
        event, _ = detector.update(bytes([0] * 40))
        self.assertEqual(event, "obstructed")
        self.assertTrue(detector.obstructed)
        detector.update(baseline)
        event, _ = detector.update(baseline)
        self.assertEqual(event, "recovered")

    def test_uniform_bright_cover(self):
        detector = self.detector()
        baseline = bytes([40, 90, 150, 220] * 10)
        detector.update(baseline)
        detector.update(baseline)
        detector.update(bytes([245] * 40))
        event, metrics = detector.update(bytes([245] * 40))
        self.assertEqual(metrics.reason, "covered")
        self.assertEqual(event, "obstructed")

    def test_brief_view_change_does_not_alert(self):
        detector = self.detector()
        baseline = bytes([30, 80, 130, 180] * 10)
        detector.update(baseline)
        detector.update(baseline)
        event, _ = detector.update(bytes(reversed(baseline)))
        self.assertIsNone(event)
        event, _ = detector.update(baseline)
        self.assertIsNone(event)
        self.assertFalse(detector.obstructed)

    def test_persistent_view_shift_alerts(self):
        detector = self.detector()
        baseline = bytes([20, 60, 120, 220] * 10)
        shifted = bytes(reversed(baseline))
        detector.update(baseline)
        detector.update(baseline)
        event, metrics = detector.update(shifted)
        self.assertIsNone(event)
        self.assertGreaterEqual(metrics.changed_fraction, 0.75)
        event, _ = detector.update(shifted)
        self.assertEqual(event, "obstructed")


if __name__ == "__main__":
    unittest.main()
