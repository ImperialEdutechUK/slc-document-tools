import unittest
import xml.etree.ElementTree as ET

from app.formatter.document_structure import (
    WNS,
    force_headings_to_new_pages,
    make_toc_elements,
    wt,
)


def make_paragraph(text, style=None):
    p = ET.Element(wt("p"))
    if style:
        p_pr = ET.SubElement(p, wt("pPr"))
        ET.SubElement(p_pr, wt("pStyle")).set(wt("val"), style)
    run = ET.SubElement(p, wt("r"))
    ET.SubElement(run, wt("t")).text = text
    return p


class DocumentStructureTests(unittest.TestCase):
    def test_toc_elements_include_heading_field_and_page_break(self):
        elements = make_toc_elements()
        self.assertEqual(len(elements), 3)

        heading_style = elements[0].find(
            f"./{{{WNS}}}pPr/{{{WNS}}}pStyle"
        )
        self.assertIsNotNone(heading_style)
        self.assertEqual(heading_style.get(wt("val")), "TOCHeading")

        instruction = elements[1].find(".//" + wt("instrText"))
        self.assertIsNotNone(instruction)
        self.assertIn('TOC \\o "1-3"', instruction.text)

        page_break = elements[2].find(".//" + wt("br"))
        self.assertIsNotNone(page_break)
        self.assertEqual(page_break.get(wt("type")), "page")

    def test_first_heading_uses_toc_break_and_later_headings_get_page_break(self):
        body = ET.Element(wt("body"))
        first = make_paragraph("1 First", "Heading1")
        second = make_paragraph("1.1 Second", "Heading2")
        body.extend([first, make_paragraph("Body text"), second])

        governed = force_headings_to_new_pages(body)

        self.assertEqual(governed, 2)
        self.assertIsNone(first.find("./" + wt("pPr") + "/" + wt("pageBreakBefore")))
        page_break = second.find(
            "./" + wt("pPr") + "/" + wt("pageBreakBefore")
        )
        self.assertIsNotNone(page_break)
        self.assertEqual(page_break.get(wt("val")), "1")

    def test_heading_after_introductory_text_gets_page_break(self):
        body = ET.Element(wt("body"))
        heading = make_paragraph("1 First", "Heading1")
        body.extend([make_paragraph("Introductory text"), heading])

        governed = force_headings_to_new_pages(body)

        self.assertEqual(governed, 1)
        self.assertIsNotNone(
            heading.find("./" + wt("pPr") + "/" + wt("pageBreakBefore"))
        )

    def test_text_box_heading_is_ignored(self):
        body = ET.Element(wt("body"))
        host = ET.SubElement(body, wt("p"))
        text_box = ET.SubElement(host, wt("txbxContent"))
        heading = make_paragraph("Text box heading", "Heading1")
        text_box.append(heading)

        governed = force_headings_to_new_pages(body)

        self.assertEqual(governed, 0)
        self.assertIsNone(
            heading.find("./" + wt("pPr") + "/" + wt("pageBreakBefore"))
        )


if __name__ == "__main__":
    unittest.main()
