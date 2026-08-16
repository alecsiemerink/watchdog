import unittest

from PIL import Image

from hotel_watchdog.watchdog import jpeg_from_bgr, motion_fraction, sampled_green_pixels


class MotionTests(unittest.TestCase):
    def test_motion_fraction(self):
        previous = bytes([0, 0, 0, 0])
        current = bytes([0, 30, 0, 40])
        self.assertEqual(motion_fraction(previous, current, pixel_delta=22), 0.5)

    def test_empty_motion_fraction(self):
        self.assertEqual(motion_fraction(b"", b"", pixel_delta=22), 0.0)

    def test_green_channel_sampling(self):
        # Sixteen BGR pixels produce one sampled green value.
        frame = bytes([1, 2, 3] * 16)
        self.assertEqual(sampled_green_pixels(frame), bytes([2]))

    def test_jpeg_encoding(self):
        frame = bytes([0, 0, 255] * 4)
        jpeg = jpeg_from_bgr(frame, width=2, height=2)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        image = Image.open(__import__("io").BytesIO(jpeg))
        self.assertEqual(image.size, (2, 2))


if __name__ == "__main__":
    unittest.main()
