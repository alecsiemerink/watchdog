import io
import unittest

from PIL import Image

from watchdog_app.person import PersonObservation, PersonPresence, annotate_people


def person(confidence: float) -> PersonObservation:
    return PersonObservation(confidence, (0.2, 0.2, 0.4, 0.6))


class PersonPresenceTests(unittest.TestCase):
    def test_detected_and_cleared_are_debounced(self):
        presence = PersonPresence(
            confidence_threshold=0.5, required_hits=2, clear_hits=3
        )
        self.assertIsNone(presence.update([person(0.8)]))
        self.assertEqual(presence.update([person(0.7)]), "detected")
        self.assertTrue(presence.present)
        self.assertIsNone(presence.update([]))
        self.assertIsNone(presence.update([]))
        self.assertEqual(presence.update([]), "cleared")
        self.assertFalse(presence.present)

    def test_low_confidence_is_not_presence(self):
        presence = PersonPresence(
            confidence_threshold=0.7, required_hits=1, clear_hits=1
        )
        self.assertIsNone(presence.update([person(0.69)]))
        self.assertFalse(presence.present)

    def test_annotation_draws_valid_jpeg(self):
        source = io.BytesIO()
        Image.new("RGB", (100, 100), "black").save(source, "JPEG")
        annotated = annotate_people(source.getvalue(), [person(0.9)])
        self.assertTrue(annotated.startswith(b"\xff\xd8"))
        self.assertEqual(Image.open(io.BytesIO(annotated)).size, (100, 100))


if __name__ == "__main__":
    unittest.main()
