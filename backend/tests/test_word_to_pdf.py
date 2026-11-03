import shutil
import unittest
import zipfile
from io import BytesIO

from docx import Document

from app.services.word_to_pdf import (
    _font_match,
    _is_exact_font_match,
    convert_word_files,
    expand_word_inputs,
)


def make_docx(text="Hello"):
    doc = Document()
    doc.add_paragraph(text)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


class WordToPdfTests(unittest.TestCase):
    def test_exact_garamond_rejects_substitute_family(self):
        self.assertTrue(_is_exact_font_match("Garamond", "Garamond"))
        self.assertFalse(_is_exact_font_match("Garamond", "EB Garamond,EB Garamond 08"))
        self.assertFalse(_is_exact_font_match("Garamond", "Noto Serif"))

    def test_expands_docx_from_zip_and_ignores_other_files(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("folder/a.docx", make_docx("A"))
            archive.writestr("notes.txt", b"ignore")
        documents = expand_word_inputs([("unit.zip", buffer.getvalue())])
        self.assertEqual(["a.docx"], [name for name, _ in documents])

    @unittest.skipUnless(
        (shutil.which("libreoffice") or shutil.which("soffice"))
        and _is_exact_font_match("Garamond", _font_match("Garamond")),
        "LibreOffice or exact Garamond not installed",
    )
    def test_converts_single_docx_to_pdf(self):
        name, payload, mime, details = convert_word_files([("sample.docx", make_docx())])
        self.assertEqual("sample.pdf", name)
        self.assertEqual("application/pdf", mime)
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertEqual(1, details["converted_documents"])


if __name__ == "__main__":
    unittest.main()
