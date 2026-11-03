"""Regression tests for all downloaded images being reported as NOT INSERTED.

No live image provider is called; tests use real DOCX packages and simulated
image downloads so they verify the location and embedding steps end to end.
"""
import unittest
from io import BytesIO
from unittest.mock import patch

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.enum.text import WD_BREAK
from PIL import Image

from app.formatter.engine import process
from app.formatter.image_placement import (
    insert_numbered_images, verify_final_image_placements,
)
from app.services.linked_images import download_linked_images


def jpg():
    out = BytesIO()
    Image.new('RGB', (960, 600), '#669899').save(out, format='JPEG')
    return out.getvalue()


def add_link(paragraph, label, url):
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement('w:hyperlink')
    link.set(qn('r:id'), rel_id)
    run = OxmlElement('w:r')
    label_el = OxmlElement('w:t')
    label_el.text = label
    run.append(label_el)
    link.append(run)
    paragraph._p.append(link)


def as_bytes(doc):
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def format_with_downloads(source):
    with patch('app.services.linked_images._download_image_link', return_value=jpg()):
        downloads = download_linked_images(source)
    linked = {entry.number: entry.url for entry in downloads.entries if entry.status == 'downloaded'}
    anchors = {entry.number: entry.anchor_text for entry in downloads.entries if entry.status == 'downloaded'}
    formatted, log, validation, placement, _ = process(
        source, None, 'jpg', 'South London College', 'Working with Software', 'Unit 1',
        downloads.items, linked_image_urls=linked,
        linked_image_anchor_texts=anchors,
    )
    return downloads, Document(BytesIO(formatted)), placement


class FiveDownloadedNotInsertedTests(unittest.TestCase):
    def test_five_links_in_one_paragraph_before_page_break_all_insert(self):
        """One image-link paragraph used to be mistaken for a disposable cover."""
        doc = Document()
        paragraph = doc.add_paragraph('Illustration sources: ')
        for number in range(1, 6):
            add_link(paragraph, f'View photo {number}', f'https://images.example.org/photo-{number}.jpg')
            paragraph.add_run('  ')
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_paragraph('Introduction', style='Heading 1')
        downloads, formatted, placement = format_with_downloads(as_bytes(doc))
        self.assertEqual(5, downloads.to_dict()['downloaded'])
        self.assertEqual(5, placement.images_inserted)
        self.assertEqual(5, len(formatted.inline_shapes))
        self.assertTrue(any('Illustration sources:' in p.text for p in formatted.paragraphs))

    def test_five_image_links_inside_table_before_toc_all_insert(self):
        """TOC cleanup must not remove the whole table of image-source links."""
        doc = Document()
        doc.add_paragraph('Course title')
        table = doc.add_table(rows=5, cols=1)
        for number in range(1, 6):
            add_link(table.rows[number-1].cells[0].paragraphs[0],
                     f'View photo {number}', f'https://images.example.org/photo-{number}.jpg')
        doc.add_paragraph('Table of Contents', style='Heading 1')
        doc.add_paragraph('Introduction', style='Heading 1')
        downloads, formatted, placement = format_with_downloads(as_bytes(doc))
        self.assertEqual(5, downloads.to_dict()['downloaded'])
        self.assertEqual(5, placement.images_inserted)
        # Count pictures *inside the table*, excluding generated cover art.
        # A misleading placement report used to claim 5/5 even when the
        # formatter silently removed this entire table after insertion.
        self.assertEqual(5, len(formatted.tables[0].rows))
        table_pictures = sum(
            len(paragraph._p.xpath('.//w:drawing'))
            for row in formatted.tables[0].rows for cell in row.cells
            for paragraph in cell.paragraphs
        )
        self.assertEqual(5, table_pictures)
        self.assertIn('View photo 5', formatted.tables[0].rows[4].cells[0].text)

    def test_unit_images_on_first_page_before_manual_page_break_are_preserved(self):
        """Old cover-removal logic discarded all five linked image anchors."""
        doc = Document()
        doc.add_paragraph('1.1 Applying a safer process', style='Heading 1')
        for i in range(1, 6):
            p = doc.add_paragraph(f'Illustration for step {i}: ')
            add_link(p, f'View illustration {i}', f'https://images.example.org/step{i}.jpg')
        doc.add_paragraph('The five visual examples are part of this lesson.')
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_paragraph('1.2 Next step', style='Heading 1')
        doc.add_paragraph('The next part of the lesson. ' * 8)
        downloads, formatted, placement = format_with_downloads(as_bytes(doc))
        self.assertEqual(5, downloads.to_dict()['downloaded'])
        self.assertEqual(5, placement.images_inserted)
        self.assertEqual(5, len(formatted.inline_shapes))
        texts = [p.text for p in formatted.paragraphs]
        for i in range(1, 6):
            self.assertIn(f'Illustration for step {i}: View illustration {i}', texts)
        self.assertIn('The five visual examples are part of this lesson.', texts)

    def test_toc_after_images_does_not_delete_early_chapter_content(self):
        doc = Document()
        doc.add_paragraph('Chapter 1 Working Safely', style='Heading 1')
        for i in range(1, 6):
            add_link(doc.add_paragraph(f'Source {i}: '), 'See photograph', f'https://images.example.org/toc-sample{i}.jpg')
        doc.add_paragraph('Table of Contents', style='Heading 1')
        doc.add_paragraph('Chapter 2 More Content', style='Heading 1')
        downloads, formatted, placement = format_with_downloads(as_bytes(doc))
        self.assertEqual(5, downloads.to_dict()['downloaded'])
        self.assertEqual(5, placement.images_inserted)
        self.assertEqual(5, len(formatted.inline_shapes))
        self.assertTrue(any('Chapter 1 Working Safely' in p.text for p in formatted.paragraphs))

    def test_original_text_fallback_when_hyperlink_relationship_cannot_match(self):
        doc = Document()
        doc.add_paragraph('Introduction', style='Heading 1')
        add_link(doc.add_paragraph('Relevant example: '), 'See photograph', 'https://images.example.org/one.jpg')
        source = as_bytes(doc)
        with patch('app.formatter.image_placement._linked_anchor_paragraph', return_value=None):
            downloads, formatted, placement = format_with_downloads(source)
        self.assertEqual(1, downloads.to_dict()['downloaded'])
        self.assertEqual(1, placement.images_inserted)
        self.assertEqual(1, len(formatted.inline_shapes))
        self.assertTrue(any('Relevant example: See photograph' in p.text for p in formatted.paragraphs))

    def test_report_verifies_media_is_still_referenced_in_saved_docx(self):
        """Do not claim INSERTED if a later step removes the inserted drawing."""
        doc = Document()
        add_link(doc.add_paragraph('Training image: '), 'View photo',
                 'https://images.example.org/verified.jpg')
        with patch('app.services.linked_images._download_image_link', return_value=jpg()):
            downloads = download_linked_images(as_bytes(doc))
        output, _log, _validation, placement, catalog = process(
            as_bytes(doc), None, 'jpg', 'South London College', 'Course', 'Unit',
            downloads.items,
            linked_image_urls={e.number:e.url for e in downloads.entries},
            linked_image_anchor_texts={e.number:e.anchor_text for e in downloads.entries},
        )
        self.assertEqual(1, placement.images_inserted)
        # Remove the embedding reference in an otherwise valid ZIP. The media
        # file remains in /word/media, but is no longer drawn in the document.
        import xml.etree.ElementTree as ET
        import zipfile
        with zipfile.ZipFile(BytesIO(output)) as original:
            rewritten = BytesIO()
            with zipfile.ZipFile(rewritten, 'w') as modified:
                for item in original.infolist():
                    data = original.read(item.filename)
                    if item.filename == 'word/document.xml':
                        root = ET.fromstring(data)
                        for blip in root.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}blip'):
                            if blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed') in placement.image_relationships.values():
                                blip.set('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed', 'missing-rel')
                        data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                    modified.writestr(item, data)
        lost = verify_final_image_placements(rewritten.getvalue(), catalog, placement)
        self.assertEqual([1], lost)
        self.assertEqual(0, placement.images_inserted)
        self.assertEqual([], placement.used_image_numbers)

    def test_genuine_title_only_cover_is_still_removed(self):
        doc = Document()
        doc.add_paragraph('Working with Software')
        doc.add_paragraph('Award in Computing Level 4')
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_paragraph('Introduction', style='Heading 1')
        add_link(doc.add_paragraph(), 'View training photo', 'https://images.example.org/training.jpg')
        downloads, formatted, placement = format_with_downloads(as_bytes(doc))
        self.assertEqual(1, placement.images_inserted)
        all_text = '\n'.join(p.text for p in formatted.paragraphs)
        self.assertNotIn('Award in Computing Level 4', all_text)
        self.assertIn('View training photo', all_text)


if __name__ == '__main__':
    unittest.main()
