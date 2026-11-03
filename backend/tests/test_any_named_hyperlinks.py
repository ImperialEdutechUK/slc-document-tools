"""Image placement follows actual external links, never hyperlink display names."""
from io import BytesIO
from unittest.mock import patch
import unittest

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from PIL import Image

from app.formatter.engine import process
from app.services.linked_images import scan_linked_image_sources, download_linked_images


def image_data():
    output = BytesIO()
    Image.new('RGB', (720, 440), '#458A93').save(output, format='PNG')
    return output.getvalue()


def with_link(paragraph, label, url):
    rid = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement('w:hyperlink')
    link.set(qn('r:id'), rid)
    run = OxmlElement('w:r')
    text = OxmlElement('w:t')
    text.text = label
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


def from_doc(doc):
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def run_flow(source):
    # Mock the HTTP payload, but not link discovery or raster verification.
    with patch('app.services.linked_images._download_public_url', return_value=image_data()):
        downloaded = download_linked_images(source)
    docx, _log, _validation, report, _catalog = process(
        source, None, 'jpg', 'SLC', 'Health Course', 'Chapter 1',
        downloaded.items,
        linked_image_urls={e.number:e.url for e in downloaded.entries if e.status=='downloaded'},
        linked_image_anchor_texts={e.number:e.anchor_text for e in downloaded.entries if e.status=='downloaded'},
    )
    return downloaded, Document(BytesIO(docx)), report


class ArbitrarilyNamedLinkTests(unittest.TestCase):
    def test_image_01_hyperlink_replaced_by_photo_at_exact_heading(self):
        doc = Document()
        doc.add_heading('1.1 Why effective communication is important', level=2)
        with_link(doc.add_paragraph(), 'Image 01', 'https://example.test/api/media/opaque-abc')
        doc.add_paragraph('Effective communication supports person-centred care.')
        source = from_doc(doc)
        sources,_ = scan_linked_image_sources(source)
        self.assertEqual(1, sources[0].number)
        retrieved, formatted, report = run_flow(source)
        self.assertEqual(1, retrieved.to_dict()['downloaded'])
        self.assertEqual(1, report.images_inserted)
        self.assertEqual(1, len(formatted.inline_shapes))
        self.assertNotIn('Image 01', '\n'.join(p.text for p in formatted.paragraphs))
        self.assertIn('Effective communication supports person-centred care.', '\n'.join(p.text for p in formatted.paragraphs))

    def test_arbitrary_label_to_extensionless_image_data_embeds(self):
        doc = Document()
        doc.add_heading('1.2 Teamwork', level=2)
        with_link(doc.add_paragraph(), 'Click to view', 'https://example.test/get?item=123')
        doc.add_paragraph('Keep all original lesson paragraphs.')
        source = from_doc(doc)
        sources,_ = scan_linked_image_sources(source)
        self.assertEqual(1,len(sources))
        self.assertFalse(sources[0].allow_page_preview)
        retrieved, formatted, report = run_flow(source)
        self.assertEqual(1, retrieved.to_dict()['downloaded'])
        self.assertEqual(1, report.images_inserted)
        self.assertEqual(1, len(formatted.inline_shapes))
        self.assertIn('Click to view', '\n'.join(p.text for p in formatted.paragraphs))
        self.assertIn('Keep all original lesson paragraphs.', '\n'.join(p.text for p in formatted.paragraphs))

    def test_photo_page_with_any_label_is_accepted_when_url_is_photo_specific(self):
        doc = Document()
        with_link(doc.add_paragraph(), 'More details', 'https://example.test/photos/staff-working')
        source = from_doc(doc)
        sources,_ = scan_linked_image_sources(source)
        self.assertTrue(sources[0].allow_page_preview)
        html = b'<!doctype html><html><head><meta property="og:image" content="https://img.example.test/photo.png"></head></html>'
        def fake_download(url):
            return image_data() if url == 'https://img.example.test/photo.png' else html
        with patch('app.services.linked_images._download_public_url', side_effect=fake_download):
            result=download_linked_images(source)
        self.assertEqual(1, result.to_dict()['downloaded'])

    def test_generic_website_link_does_not_take_its_social_preview_image(self):
        doc = Document()
        with_link(doc.add_paragraph(), 'Read the guidance', 'https://example.test/guidance')
        source = from_doc(doc)
        html = b'<!doctype html><meta property="og:image" content="https://example.test/logo.png">'
        with patch('app.services.linked_images._download_public_url', return_value=html):
            result=download_linked_images(source)
        self.assertEqual(0, result.to_dict()['downloaded'])
        self.assertEqual(1, result.to_dict()['skipped'])

    def test_word_fldsimple_hyperlink_is_scanned_and_embedded(self):
        doc = Document()
        doc.add_heading('1.3 Working practices', level=2)
        p = doc.add_paragraph()
        field = OxmlElement('w:fldSimple')
        field.set(qn('w:instr'), ' HYPERLINK "https://media.example.test/render?token=26" ')
        run=OxmlElement('w:r')
        text=OxmlElement('w:t')
        text.text='Image 02'
        run.append(text)
        field.append(run)
        p._p.append(field)
        source = from_doc(doc)
        sources,_=scan_linked_image_sources(source)
        self.assertEqual([(2,'Image 02')],[(x.number,x.label) for x in sources])
        retrieved, formatted, report=run_flow(source)
        self.assertEqual(1, retrieved.to_dict()['downloaded'])
        self.assertEqual(1, report.images_inserted)
        self.assertEqual(1, len(formatted.inline_shapes))

    def test_repeated_image_01_labels_each_use_their_own_destination(self):
        doc = Document()
        doc.add_heading('1.1 First topic', level=2)
        with_link(doc.add_paragraph(), 'Image 01', 'https://example.test/img/first.jpg')
        doc.add_paragraph('Different section text remains.')
        doc.add_heading('1.2 Second topic', level=2)
        with_link(doc.add_paragraph(), 'Image 01', 'https://example.test/img/second.jpg')
        source=from_doc(doc)
        sources,_=scan_linked_image_sources(source)
        self.assertEqual(2,len(sources))
        self.assertEqual(2,len(set(x.number for x in sources)))
        retrieved, formatted, report=run_flow(source)
        self.assertEqual(2, retrieved.to_dict()['downloaded'])
        self.assertEqual(2, report.images_inserted)
        self.assertEqual(2,len(formatted.inline_shapes))
        self.assertIn('Different section text remains.', '\n'.join(p.text for p in formatted.paragraphs))

    def test_reference_links_stay_as_references(self):
        doc=Document()
        doc.add_heading('2.1 Safety', level=2)
        with_link(doc.add_paragraph(), 'See safety example', 'https://example.test/image/example.png')
        doc.add_heading('References', level=1)
        with_link(doc.add_paragraph(), 'Online article', 'https://example.test/research/image.jpg')
        sources,_=scan_linked_image_sources(from_doc(doc))
        self.assertEqual(1,len(sources))
        self.assertIn('safety',sources[0].label.lower())


if __name__ == '__main__':
    unittest.main()
