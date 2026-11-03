"""Regression coverage for downloaded stock photos missing from formatted DOCX.

The external stock service is intentionally not accessed: its downloaded image
bytes are emulated, while the real formatting and DOCX round trip are tested.
"""

import unittest
from io import BytesIO

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from PIL import Image

from app.formatter.engine import process
from app.formatter.image_placement import extract_placeholder_number
from app.services.linked_images import scan_linked_image_sources


def make_jpg():
    out = BytesIO()
    Image.new("RGB", (850, 510), "#487c96").save(out, format="JPEG")
    return out.getvalue()


def add_hyperlink(paragraph, text, url):
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    label = OxmlElement("w:t")
    label.text = text
    run.append(label)
    link.append(run)
    paragraph._p.append(link)


class LinkedImageInsertionEndToEndTests(unittest.TestCase):
    def test_invisible_word_characters_are_handled(self):
        self.assertEqual(5, extract_placeholder_number("IMAGE\u200b5"))
        self.assertEqual(5, extract_placeholder_number("IMAGE5:"))
        self.assertEqual(5, extract_placeholder_number("IMAGE\u00ad5"))
        self.assertIsNone(extract_placeholder_number("A normal paragraph about Image 5"))

    def test_downloaded_images_become_embedded_pictures_without_losing_body_text(self):
        doc = Document()
        doc.add_paragraph("Introduction", style="Heading 1")
        substantive_text = "The architecture is explained in the paragraph."
        doc.add_paragraph(substantive_text)

        # IMAGE1 is an underlined link sharing a paragraph with meaningful text.
        p = doc.add_paragraph("Relevant example: ")
        add_hyperlink(p, "IMAGE1", "https://www.magnific.com/free-photo/one_10001.htm")
        p.add_run(" demonstrates the design.")

        # IMAGE2 looks standalone, but Word inserted a zero-width space.
        p = doc.add_paragraph()
        add_hyperlink(p, "IMAGE\u200b2", "https://www.magnific.com/free-photo/two_10002.htm")

        # IMAGE3 uses a trailing colon (common in course content).
        p = doc.add_paragraph()
        add_hyperlink(p, "IMAGE3:", "https://www.magnific.com/free-photo/three_10003.htm")

        # IMAGE4 has a URL but the link text isn't an image placeholder.
        p = doc.add_paragraph("Source photograph: ")
        add_hyperlink(p, "Open photograph", "https://www.magnific.com/free-photo/four_10004.htm")

        # This sentence is not an image placeholder and must survive unchanged.
        untouched_sentence = "See Image 1 in the explanation; do not remove this sentence."
        doc.add_paragraph(untouched_sentence)

        before = BytesIO()
        doc.save(before)
        source_docx = before.getvalue()
        sources, warnings = scan_linked_image_sources(source_docx)
        self.assertEqual([], warnings)
        # Unnumbered stock-photo hyperlinks now also qualify for insertion.
        self.assertEqual([1, 2, 3, 4], [source.number for source in sources])

        jpg = make_jpg()
        items = [(f"Image {i}.jpg", jpg) for i in range(1, 5)]
        url_map = {
            1: "https://www.magnific.com/free-photo/one_10001.htm",
            2: "https://www.magnific.com/free-photo/two_10002.htm",
            3: "https://www.magnific.com/free-photo/three_10003.htm",
            4: "https://www.magnific.com/free-photo/four_10004.htm",
        }
        result_bytes, _log, _validation, placement, _catalog = process(
            source_docx, None, "jpg", "Awarding Body", "Course", "Unit",
            items,
            linked_image_urls=url_map,
        )
        result_doc = Document(BytesIO(result_bytes))
        paragraphs = [p.text for p in result_doc.paragraphs]
        self.assertEqual(4, placement.images_inserted)
        self.assertEqual([1, 2, 3, 4], placement.used_image_numbers)
        self.assertEqual(4, len(result_doc.inline_shapes))
        self.assertIn(substantive_text, paragraphs)
        self.assertIn(untouched_sentence, paragraphs)
        self.assertIn("Relevant example: IMAGE1 demonstrates the design.", paragraphs)
        self.assertIn("Source photograph: Open photograph", paragraphs)
        # Replaced isolated placeholders no longer appear as visible text.
        self.assertNotIn("IMAGE\u200b2", paragraphs)
        self.assertNotIn("IMAGE3:", paragraphs)

    def test_downloaded_photo_can_anchor_to_visible_url_in_paragraph(self):
        url = "https://cdn.example.org/photo-8.jpg"
        doc = Document()
        original = f"Supporting image (source: {url})"
        doc.add_paragraph(original)
        before = BytesIO()
        doc.save(before)
        output, _, _, placement, _ = process(
            before.getvalue(), None, "jpg", "Award", "Course", "Unit",
            [("Image 8.jpg", make_jpg())], linked_image_urls={8: url}
        )
        updated = Document(BytesIO(output))
        self.assertEqual(1, placement.images_inserted)
        self.assertIn(original, [p.text for p in updated.paragraphs])
        self.assertEqual(1, len(updated.inline_shapes))


if __name__ == "__main__":
    unittest.main()
