import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from app.formatter.image_placement import prepare_image_catalog
from app.formatter.safe_images import open_checked_image


class SafeImageTests(unittest.TestCase):
    @staticmethod
    def sample_image():
        output = BytesIO()
        Image.new("RGB", (20, 20), "white").save(output, format="PNG")
        return output.getvalue()

    def test_small_image_can_be_opened(self):
        with open_checked_image(self.sample_image()) as img:
            self.assertEqual((20, 20), img.size)

    def test_oversized_image_is_rejected_before_loading_pixels(self):
        with patch("app.formatter.safe_images.MAX_SAFE_IMAGE_PIXELS", 100):
            with self.assertRaisesRegex(ValueError, "50 megapixels"):
                open_checked_image(self.sample_image())

    def test_catalog_reports_oversized_image_instead_of_crashing(self):
        with patch("app.formatter.safe_images.MAX_SAFE_IMAGE_PIXELS", 100):
            catalog = prepare_image_catalog([("1.png", self.sample_image())])
        self.assertEqual({}, catalog.selected)
        self.assertIn("1.png", catalog.corrupted_files)
        self.assertIn("Resize", catalog.corrupted_files["1.png"])


if __name__ == "__main__":
    unittest.main()
