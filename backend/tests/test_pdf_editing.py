import unittest
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app.formatter.pdf_editing import (
    PdfEditingError,
    get_pdf_page_count,
    remove_pdf_pages,
    strip_leading_blank_pages,
)


def make_pdf(page_count=4):
    writer = PdfWriter()
    for index in range(page_count):
        writer.add_blank_page(width=300 + index, height=400 + index)
    writer.add_metadata({"/Title": "Test PDF"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_text_page_pdf(text="Real content"):
    """Build a single-page PDF with actual extractable text, so tests can
    exercise the blank/non-blank detection against genuine page content
    rather than only pypdf's own blank pages.
    """
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(300, 400))
    pdf.drawString(50, 350, text)
    pdf.save()
    return buffer.getvalue()


class PdfEditingTests(unittest.TestCase):
    def test_page_count(self):
        self.assertEqual(get_pdf_page_count(make_pdf(5)), 5)

    def test_removes_selected_one_based_pages(self):
        result = remove_pdf_pages(make_pdf(4), [2, 4])
        reader = PdfReader(BytesIO(result))
        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(float(reader.pages[0].mediabox.width), 300)
        self.assertEqual(float(reader.pages[1].mediabox.width), 302)

    def test_duplicate_selections_are_safe(self):
        result = remove_pdf_pages(make_pdf(3), [2, 2])
        self.assertEqual(len(PdfReader(BytesIO(result)).pages), 2)

    def test_requires_selection(self):
        with self.assertRaises(PdfEditingError):
            remove_pdf_pages(make_pdf(2), [])

    def test_rejects_out_of_range_page(self):
        with self.assertRaises(PdfEditingError):
            remove_pdf_pages(make_pdf(2), [3])

    def test_rejects_deleting_every_page(self):
        with self.assertRaises(PdfEditingError):
            remove_pdf_pages(make_pdf(2), [1, 2])

    def test_rejects_empty_file(self):
        with self.assertRaises(PdfEditingError):
            get_pdf_page_count(b"")

    def test_strips_leading_blank_page_before_real_content(self):
        blank_writer = PdfWriter()
        blank_writer.add_blank_page(width=300, height=400)
        blank_buffer = BytesIO()
        blank_writer.write(blank_buffer)

        content_reader = PdfReader(BytesIO(make_text_page_pdf("Cover page")))

        combined = PdfWriter()
        for page in PdfReader(BytesIO(blank_buffer.getvalue())).pages:
            combined.add_page(page)
        for page in content_reader.pages:
            combined.add_page(page)
        combined_buffer = BytesIO()
        combined.write(combined_buffer)

        result_bytes, removed = strip_leading_blank_pages(combined_buffer.getvalue())
        self.assertEqual(removed, 1)
        result_reader = PdfReader(BytesIO(result_bytes))
        self.assertEqual(len(result_reader.pages), 1)
        self.assertIn("Cover page", result_reader.pages[0].extract_text())

    def test_no_false_positive_on_real_first_page(self):
        pdf_bytes = make_text_page_pdf("Already the real cover")
        result_bytes, removed = strip_leading_blank_pages(pdf_bytes)
        self.assertEqual(removed, 0)
        self.assertEqual(result_bytes, pdf_bytes)

    def test_never_strips_every_page(self):
        # All pages blank: must always leave at least one page behind,
        # even though every page technically matches the blank check.
        result_bytes, removed = strip_leading_blank_pages(make_pdf(3))
        self.assertGreaterEqual(get_pdf_page_count(result_bytes), 1)
        self.assertLess(removed, 3)

    def test_only_strips_leading_pages_not_later_ones(self):
        content_reader = PdfReader(BytesIO(make_text_page_pdf("Page one")))
        blank_writer = PdfWriter()
        blank_writer.add_blank_page(width=300, height=400)
        blank_buffer = BytesIO()
        blank_writer.write(blank_buffer)

        combined = PdfWriter()
        for page in content_reader.pages:
            combined.add_page(page)
        for page in PdfReader(BytesIO(blank_buffer.getvalue())).pages:
            combined.add_page(page)
        combined_buffer = BytesIO()
        combined.write(combined_buffer)

        result_bytes, removed = strip_leading_blank_pages(combined_buffer.getvalue())
        self.assertEqual(removed, 0)
        self.assertEqual(get_pdf_page_count(result_bytes), 2)


if __name__ == "__main__":
    unittest.main()
