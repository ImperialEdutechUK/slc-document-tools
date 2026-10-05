import unittest
from io import BytesIO

import pymupdf as fitz
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.formatter.pdf_editing import (
    PdfEditingError,
    get_pdf_page_count,
    remove_blank_second_page,
    remove_pdf_pages,
    replace_pdf_cover,
    replace_pdf_cover_and_text,
    replace_pdf_text,
)


def make_pdf(page_count=4):
    writer = PdfWriter()
    for index in range(page_count):
        writer.add_blank_page(width=300 + index, height=400 + index)
    writer.add_metadata({"/Title": "Test PDF"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_text_pdf(text="Old Course Name"):
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), text, fontsize=18)
    payload = document.tobytes()
    document.close()
    return payload


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

    def test_replaces_first_page_cover_and_keeps_remaining_pages(self):
        source = make_pdf(3)
        cover = make_pdf(1)
        result = replace_pdf_cover(source, cover, "cover.pdf")
        reader = PdfReader(BytesIO(result))

        self.assertEqual(len(reader.pages), 3)
        self.assertEqual(float(reader.pages[0].mediabox.width), 300)
        self.assertEqual(float(reader.pages[0].mediabox.height), 400)
        self.assertEqual(float(reader.pages[1].mediabox.width), 301)
        self.assertEqual(float(reader.pages[2].mediabox.width), 302)

    def test_replaces_cover_from_png(self):
        image_buffer = BytesIO()
        Image.new("RGB", (200, 300), (20, 120, 130)).save(image_buffer, format="PNG")

        result = replace_pdf_cover(make_pdf(2), image_buffer.getvalue(), "cover.png")
        reader = PdfReader(BytesIO(result))

        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(float(reader.pages[0].mediabox.width), 300)
        self.assertEqual(float(reader.pages[0].mediabox.height), 400)

    def test_replaces_exact_visible_text(self):
        result, report = replace_pdf_text(
            make_text_pdf(),
            [("Old Course Name", "New Course Name")],
        )
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(result)).pages)

        self.assertNotIn("Old Course Name", text)
        self.assertIn("New Course Name", text)
        self.assertEqual(report[0]["matches"], 1)

    def test_combined_cover_and_text_requires_an_action(self):
        with self.assertRaises(PdfEditingError):
            replace_pdf_cover_and_text(make_text_pdf())


if __name__ == "__main__":
    unittest.main()
