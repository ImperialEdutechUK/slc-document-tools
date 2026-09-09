import unittest
from io import BytesIO

from docx import Document

from app.formatter.simple_editing import apply_simple_edits, get_editable_paragraphs


def make_docx():
    doc = Document()
    doc.add_heading("Resources for Further Reference:", level=1)
    doc.add_paragraph("Department for Education reference one")
    doc.add_paragraph("Department for Education reference two")
    doc.add_paragraph("Body paragraph")
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


class SimpleEditingTests(unittest.TestCase):
    def test_lists_editable_paragraphs(self):
        paragraphs = get_editable_paragraphs(make_docx())
        texts = [item["text"] for item in paragraphs]
        self.assertIn("Resources for Further Reference:", texts)
        self.assertIn("Department for Education reference one", texts)

    def test_converts_selected_paragraphs_to_bullets(self):
        payload = make_docx()
        paragraphs = get_editable_paragraphs(payload)
        selected = [
            item["index"]
            for item in paragraphs
            if item["text"].startswith("Department for Education")
        ]

        edited, changed = apply_simple_edits(payload, selected, "bullets")
        self.assertEqual(2, changed)

        updated = get_editable_paragraphs(edited)
        updated_refs = [
            item for item in updated if item["text"].startswith("Department for Education")
        ]
        self.assertEqual(["bullet", "bullet"], [item["list_type"] for item in updated_refs])

    def test_can_force_and_remove_page_break_before(self):
        payload = make_docx()
        paragraph = next(
            item for item in get_editable_paragraphs(payload) if item["text"] == "Body paragraph"
        )
        edited, _ = apply_simple_edits(payload, [paragraph["index"]], "page_break_before")
        updated = next(
            item for item in get_editable_paragraphs(edited) if item["text"] == "Body paragraph"
        )
        self.assertTrue(updated["page_break_before"])

        normal_flow, _ = apply_simple_edits(
            edited, [updated["index"]], "remove_page_break_before"
        )
        final = next(
            item for item in get_editable_paragraphs(normal_flow) if item["text"] == "Body paragraph"
        )
        self.assertFalse(final["page_break_before"])


if __name__ == "__main__":
    unittest.main()
