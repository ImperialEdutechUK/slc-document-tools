import tempfile
import unittest
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image

from app.formatter.image_placement import (
    CT_NS,
    PR_NS,
    W_NS,
    build_validation_report,
    extract_image_number,
    extract_placeholder_number,
    insert_numbered_images,
    prepare_image_catalog,
)


def qn(namespace, tag):
    return "{" + namespace + "}" + tag


def make_image(fmt="PNG", size=(800, 500)):
    image = Image.new("RGB", size, "white")
    output = BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def make_document(placeholders):
    root = ET.Element(qn(W_NS, "document"))
    body = ET.SubElement(root, qn(W_NS, "body"))
    for placeholder in placeholders:
        paragraph = ET.SubElement(body, qn(W_NS, "p"))
        run = ET.SubElement(paragraph, qn(W_NS, "r"))
        text = ET.SubElement(run, qn(W_NS, "t"))
        text.text = placeholder
    return ET.ElementTree(root)


class ImagePlacementTests(unittest.TestCase):
    def test_supported_filename_patterns(self):
        self.assertEqual(extract_image_number("1.jpg"), 1)
        self.assertEqual(extract_image_number("Image 12.png"), 12)
        self.assertEqual(extract_image_number("Image_003.webp"), 3)
        self.assertIsNone(extract_image_number("course-image.jpg"))

    def test_placeholder_detection_avoids_normal_sentences(self):
        self.assertEqual(extract_placeholder_number("Image 2"), 2)
        self.assertEqual(
            extract_placeholder_number("Unit 12 Chapter 1 Image 25"), 25
        )
        self.assertIsNone(
            extract_placeholder_number("This paragraph refers to Image 2.")
        )

    def test_missing_and_unused_images_are_reported(self):
        catalog = prepare_image_catalog(
            [("1.jpg", make_image("JPEG")), ("3.png", make_image("PNG"))]
        )
        document = make_document(["Image 1", "Image 2"])
        relationships = ET.Element(qn(PR_NS, "Relationships"))
        content_types = ET.Element(qn(CT_NS, "Types"))

        with tempfile.TemporaryDirectory() as media_dir:
            report = insert_numbered_images(
                document,
                relationships,
                content_types,
                media_dir,
                catalog,
            )

        self.assertEqual(report.total_placeholders, 2)
        self.assertEqual(report.images_inserted, 1)
        self.assertEqual(report.missing_numbers, [2])
        self.assertEqual(report.unused_uploaded_images, ["3.png"])
        validation = build_validation_report(catalog, report)
        self.assertIn("Image 2", validation)
        self.assertIn("3.png", validation)

    def test_duplicate_placeholders_reuse_one_uploaded_image(self):
        catalog = prepare_image_catalog([("Image 7.png", make_image("PNG"))])
        document = make_document(["Image 7", "Unit 1 Chapter 1 Image 7"])
        relationships = ET.Element(qn(PR_NS, "Relationships"))
        content_types = ET.Element(qn(CT_NS, "Types"))

        with tempfile.TemporaryDirectory() as media_dir:
            report = insert_numbered_images(
                document,
                relationships,
                content_types,
                media_dir,
                catalog,
            )
            media_files = list(Path(media_dir).iterdir())

        self.assertEqual(report.images_inserted, 2)
        self.assertEqual(report.duplicate_placeholder_numbers, {7: 2})
        self.assertEqual(len(media_files), 1)

    def test_webp_is_converted_for_word_compatibility(self):
        catalog = prepare_image_catalog([("1.webp", make_image("WEBP"))])
        self.assertEqual(catalog.selected[1].extension, "png")


if __name__ == "__main__":
    unittest.main()
