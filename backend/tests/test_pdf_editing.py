import unittest
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.formatter.pdf_editing import (
    PdfEditingError,
    get_pdf_page_count,
    remove_blank_second_page,
    remove_pdf_pages,
)


def make_pdf(page_count=4):
    writer = PdfWriter()
    for index in range(page_count):
        writer.add_blank_page(width=300 + index, height=400 + index)
    writer.add_metadata({"/Title": "Test PDF"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


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

    def test_removes_blank_second_page_only(self):
        result, removed = remove_blank_second_page(make_pdf(3))
        reader = PdfReader(BytesIO(result))
        self.assertTrue(removed)
        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(float(reader.pages[0].mediabox.width), 300)
        self.assertEqual(float(reader.pages[1].mediabox.width), 302)


if __name__ == "__main__":
    unittest.main()
