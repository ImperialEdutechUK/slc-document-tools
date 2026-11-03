"""Regression tests for independently named hyperlinks to image sources."""
from io import BytesIO
import unittest
from unittest.mock import patch

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from PIL import Image

from app.formatter.engine import process
from app.services.linked_images import (
    _download_image_link, download_linked_images, scan_linked_image_sources,
)


def add_link(paragraph, label, url):
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = label
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def save(document):
    out = BytesIO()
    document.save(out)
    return out.getvalue()


def png():
    out = BytesIO()
    Image.new("RGB", (1100, 600), (80, 150, 160)).save(out, format="PNG")
    return out.getvalue()


class AnyNameImageLinkTests(unittest.TestCase):
    def test_any_link_name_to_stock_page_and_image_file(self):
        doc = Document()
        add_link(doc.add_paragraph(), "Workplace team", "https://www.magnific.com/free-photo/team_1234567.htm")
        add_link(doc.add_paragraph(), "Click here", "https://cdn.example.org/assets/workplace.jpg?size=800")
        add_link(doc.add_paragraph(), "Health and safety photograph", "https://example.com/asset/unknown-id")
        add_link(doc.add_paragraph(), "Additional image", "https://www.pexels.com/photo/worker-1234/")
        sources, warnings = scan_linked_image_sources(save(doc))
        self.assertEqual([], warnings)
        self.assertEqual(4, len(sources))
        self.assertEqual([1, 2, 3, 4], [src.number for src in sources])
        self.assertTrue(all(not src.numbered for src in sources))
        self.assertEqual(["Workplace team", "Click here", "Health and safety photograph", "Additional image"], [src.label for src in sources])

    def test_unrelated_links_and_references_are_skipped(self):
        doc = Document()
        add_link(doc.add_paragraph("Useful learning material: "), "Official guidance", "https://www.gov.uk/guidance/example")
        add_link(doc.add_paragraph(), "Blog post", "https://example.com/stock-photo-library-explained")
        doc.add_paragraph("References", style="Heading 1")
        add_link(doc.add_paragraph("Example source: "), "Image 42", "https://site.example/photo.jpg")
        add_link(doc.add_paragraph(), "Photo of a team", "https://site.example/photo.jpg")
        sources, _ = scan_linked_image_sources(save(doc))
        self.assertEqual([], sources)

    def test_freepik_cdn_image_is_downloaded_as_direct_photo(self):
        doc = Document()
        add_link(doc.add_paragraph(), "Open the photo", "https://img.freepik.com/example-picture.webp")
        sources, _ = scan_linked_image_sources(save(doc))
        self.assertEqual(1, len(sources))
        self.assertEqual("direct", sources[0].source_type)

    def test_summary_and_unstyled_reference_section_are_excluded(self):
        doc = Document()
        add_link(doc.add_paragraph(), "Training image", "https://images.example.org/pre.jpg")
        doc.add_paragraph("Summary")
        add_link(doc.add_paragraph(), "Summary image", "https://images.example.org/skip.jpg")
        doc.add_paragraph("Next unit", style="Heading 1")
        add_link(doc.add_paragraph(), "Unit image", "https://images.example.org/keep.jpg")
        doc.add_paragraph("References")  # Word import without heading style
        add_link(doc.add_paragraph(), "Referenced image", "https://images.example.org/ignore.jpg")
        sources, _ = scan_linked_image_sources(save(doc))
        self.assertEqual(
            ["https://images.example.org/pre.jpg", "https://images.example.org/keep.jpg"],
            [source.url for source in sources],
        )

    def test_numbered_placeholder_and_unnumbered_link_get_distinct_ids(self):
        doc = Document()
        add_link(doc.add_paragraph(), "Workplace team", "https://images.example.com/team.jpg")
        add_link(doc.add_paragraph(), "IMAGE5", "https://images.example.com/number-five.jpg")
        sources, _ = scan_linked_image_sources(save(doc))
        self.assertEqual([(6, False), (5, True)], [(src.number, src.numbered) for src in sources])

    @patch("app.services.linked_images._download_image_link")
    def test_manual_image_does_not_override_an_unnumbered_link(self, mock_get):
        mock_get.return_value = png()
        doc = Document()
        add_link(doc.add_paragraph(), "Open picture", "https://cdn.example.org/photo.jpg")
        result = download_linked_images(save(doc), [("1.jpg", png())])
        self.assertEqual("Image 2.jpg", result.items[0][0])
        self.assertEqual(2, result.entries[0].number)
        self.assertFalse(result.entries[0].numbered)

    @patch("app.services.linked_images._download_image_link")
    @patch("app.services.linked_images._download_stock_resource")
    def test_download_and_embed_images_from_named_links(self, mock_stock, mock_direct):
        mock_stock.return_value = png()
        mock_direct.return_value = png()
        doc = Document()
        doc.add_paragraph("Overview", style="Heading 1")
        original_text = "Examples of safe practice should be discussed in training."
        doc.add_paragraph(original_text)
        add_link(doc.add_paragraph("Team photo: "), "View the professionals", "https://www.magnific.com/free-photo/team_1234567.htm")
        add_link(doc.add_paragraph("Training photograph: "), "Example", "https://cdn.example.org/training.webp")
        doc.add_paragraph("The training photograph is relevant to the topic.")
        source_bytes = save(doc)
        downloads = download_linked_images(source_bytes)
        self.assertEqual(2, downloads.to_dict()["downloaded"])
        self.assertEqual([1, 2], [item.number for item in downloads.entries])
        self.assertTrue(all(not item.numbered for item in downloads.entries))

        output, _log, _validation, report, _catalog = process(
            source_bytes, None, "jpg", "SLC", "Training", "Unit 1",
            downloads.items, linked_image_urls={e.number: e.url for e in downloads.entries},
        )
        formatted = Document(BytesIO(output))
        texts = [para.text for para in formatted.paragraphs]
        self.assertEqual(2, report.images_inserted)
        self.assertEqual(2, len(formatted.inline_shapes))
        self.assertIn(original_text, texts)
        self.assertIn("Team photo: View the professionals", texts)
        self.assertIn("Training photograph: Example", texts)
        self.assertIn("The training photograph is relevant to the topic.", texts)
        self.assertEqual([1, 2], report.used_image_numbers)

    @patch("app.services.linked_images._download_image_link")
    def test_duplicate_url_in_two_paragraphs_inserts_twice(self, mock_get):
        mock_get.return_value = png()
        url = "https://example.com/workplace.jpg"
        doc = Document()
        doc.add_paragraph("Introduction", style="Heading 1")
        add_link(doc.add_paragraph("First image link: "), "Click", url)
        add_link(doc.add_paragraph("Second image link: "), "Click", url)
        source_bytes = save(doc)
        downloads = download_linked_images(source_bytes)
        # Same image is downloaded once but independently inserted for each URL.
        mock_get.assert_called_once_with(url)
        self.assertEqual(2, len(downloads.items))
        output, _, _, report, _ = process(
            source_bytes, None, "jpg", "SLC", "Training", "Unit 1",
            downloads.items, linked_image_urls={e.number: e.url for e in downloads.entries},
        )
        formatted = Document(BytesIO(output))
        self.assertEqual(2, len(formatted.inline_shapes))
        self.assertEqual(2, report.images_inserted)
        self.assertTrue(any(p.text == "First image link: Click" for p in formatted.paragraphs))
        self.assertTrue(any(p.text == "Second image link: Click" for p in formatted.paragraphs))

    @patch("app.services.linked_images._download_public_url")
    def test_photo_page_extracts_advertised_image(self, mock_download):
        pic = png()
        html = b'<html><head><meta property="og:image" content="/uploads/cover.jpg"></head><body></body></html>'
        mock_download.side_effect = [html, pic]
        result = _download_image_link("https://www.pexels.com/photo/workplace-123/")
        self.assertEqual(pic, result)
        self.assertEqual("https://www.pexels.com/uploads/cover.jpg", mock_download.call_args_list[1].args[0])

    @patch("app.services.linked_images._download_public_url")
    def test_unavailable_image_page_does_not_insert_unrelated_asset(self, mock_download):
        mock_download.return_value = b"<html><head><title>Stock photo</title></head></html>"
        with self.assertRaisesRegex(ValueError, "does not expose a downloadable"):
            _download_image_link("https://www.pexels.com/photo/workplace-123/")

    def test_two_different_links_on_same_paragraph_insert_two_pictures(self):
        doc = Document()
        p = doc.add_paragraph("Photo references: ")
        add_link(p, "First", "https://example.com/first.jpg")
        p.add_run(" and ")
        add_link(p, "Second", "https://example.com/second.jpg")
        original = save(doc)
        sources, _ = scan_linked_image_sources(original)
        self.assertEqual(2, len(sources))
        output, _, _, report, _ = process(
            original, None, "jpg", "SLC", "Training", "Unit 1",
            [(f"Image {s.number}.jpg", png()) for s in sources],
            linked_image_urls={s.number: s.url for s in sources},
        )
        formatted = Document(BytesIO(output))
        self.assertEqual(2, report.images_inserted)
        self.assertEqual(2, len(formatted.inline_shapes))
        self.assertTrue(any(p.text == "Photo references: First and Second" for p in formatted.paragraphs))


if __name__ == "__main__":
    unittest.main()
