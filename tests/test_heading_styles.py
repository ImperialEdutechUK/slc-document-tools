import unittest
import xml.etree.ElementTree as ET

from app.formatter.heading_styles import (
    W_NS,
    expected_heading_style,
    promote_manual_numbered_headings,
    qn,
)


def make_paragraph(text, style=None, in_table=False, direct_formatting=False):
    p = ET.Element(qn("p"))
    ppr = ET.SubElement(p, qn("pPr"))
    if style:
        ET.SubElement(ppr, qn("pStyle")).set(qn("val"), style)
    if direct_formatting:
        ET.SubElement(ppr, qn("spacing")).set(qn("line"), "360")
        ET.SubElement(ppr, qn("jc")).set(qn("val"), "both")
    run = ET.SubElement(p, qn("r"))
    if direct_formatting:
        rpr = ET.SubElement(run, qn("rPr"))
        ET.SubElement(rpr, qn("rStyle")).set(qn("val"), "Heading1Char")
        ET.SubElement(rpr, qn("sz")).set(qn("val"), "40")
        ET.SubElement(rpr, qn("color")).set(qn("val"), "FF0000")
    ET.SubElement(run, qn("t")).text = text
    return p


def style_of(paragraph):
    ppr = paragraph.find(qn("pPr"))
    style = ppr.find(qn("pStyle")) if ppr is not None else None
    return style.get(qn("val")) if style is not None else None


class HeadingStyleTests(unittest.TestCase):
    def test_expected_levels(self):
        self.assertEqual(expected_heading_style("Unit 12 – Title"), "Heading1")
        self.assertEqual(expected_heading_style("12.1 Title"), "Heading1")
        self.assertEqual(expected_heading_style("4.1 Main heading"), "Heading1")
        self.assertEqual(expected_heading_style("12.1.3 Title"), "Heading2")
        self.assertEqual(expected_heading_style("12.1.3.2 Title"), "Heading3")
        self.assertEqual(expected_heading_style("2 Introduction"), "Heading1")


    def test_resources_for_further_reference_is_heading1(self):
        self.assertEqual(
            expected_heading_style("Resources for Further Reference:"),
            "Heading1",
        )
        self.assertEqual(
            expected_heading_style("Resources for Further Reference"),
            "Heading1",
        )

    def test_hidden_characters_and_spaces_are_tolerated(self):
        self.assertEqual(
            expected_heading_style("\u200b12 . 1\u00a0Title"),
            "Heading1",
        )

    def test_year_and_unnumbered_bold_like_text_are_not_promoted(self):
        self.assertIsNone(expected_heading_style("2026 Report"))
        self.assertIsNone(expected_heading_style("National Curriculum Models"))
        self.assertIsNone(expected_heading_style("References"))
        self.assertIsNone(
            expected_heading_style("Unit 12 Chapter 1 Image 3")
        )

    def test_wrong_existing_heading_is_corrected(self):
        body = ET.Element(qn("body"))
        paragraph = make_paragraph(
            "12.1 Understand curricula and attainment standards",
            style="Heading1",
            direct_formatting=True,
        )
        body.append(paragraph)

        changed = promote_manual_numbered_headings(body)

        self.assertEqual(changed, 1)
        self.assertEqual(style_of(paragraph), "Heading1")
        ppr = paragraph.find(qn("pPr"))
        self.assertIsNone(ppr.find(qn("spacing")))
        self.assertIsNone(ppr.find(qn("jc")))
        rpr = paragraph.find(".//" + qn("rPr"))
        self.assertIsNone(rpr.find(qn("rStyle")))
        self.assertIsNone(rpr.find(qn("sz")))
        self.assertIsNone(rpr.find(qn("color")))

    def test_heading2_topic_is_promoted_to_heading1(self):
        body = ET.Element(qn("body"))
        paragraph = make_paragraph(
            "The Importance of Effective Teaching and Learning",
            style="Heading2",
            direct_formatting=True,
        )
        body.append(paragraph)

        changed = promote_manual_numbered_headings(body)

        self.assertEqual(changed, 1)
        self.assertEqual(style_of(paragraph), "Heading1")
        ppr = paragraph.find(qn("pPr"))
        self.assertIsNone(ppr.find(qn("spacing")))
        self.assertIsNone(ppr.find(qn("jc")))
        rpr = paragraph.find(".//" + qn("rPr"))
        self.assertIsNone(rpr.find(qn("rStyle")))
        self.assertIsNone(rpr.find(qn("sz")))
        self.assertIsNone(rpr.find(qn("color")))

    def test_numbered_heading2_topic_is_promoted_to_heading1(self):
        body = ET.Element(qn("body"))
        paragraph = make_paragraph(
            "12.1 Understand curricula and attainment standards",
            style="Heading2",
        )
        body.append(paragraph)

        promote_manual_numbered_headings(body)

        self.assertEqual(style_of(paragraph), "Heading1")

    def test_special_heading2_paragraphs_are_not_promoted(self):
        for text in ("References", "Table of Contents", "Image 14"):
            with self.subTest(text=text):
                body = ET.Element(qn("body"))
                paragraph = make_paragraph(text, style="Heading2")
                body.append(paragraph)

                changed = promote_manual_numbered_headings(body)

                self.assertEqual(changed, 0)
                self.assertEqual(style_of(paragraph), "Heading2")

    def test_paragraphs_inside_tables_are_processed(self):
        body = ET.Element(qn("body"))
        table = ET.SubElement(body, qn("tbl"))
        row = ET.SubElement(table, qn("tr"))
        cell = ET.SubElement(row, qn("tc"))
        paragraph = make_paragraph("12.1.1 Table heading", style="Normal")
        cell.append(paragraph)

        promote_manual_numbered_headings(body)

        self.assertEqual(style_of(paragraph), "Heading2")

    def test_textbox_cover_paragraphs_are_untouched(self):
        body = ET.Element(qn("body"))
        textbox = ET.SubElement(body, qn("txbxContent"))
        paragraph = make_paragraph("Unit 12 Chapter 1", style="Normal")
        textbox.append(paragraph)

        changed = promote_manual_numbered_headings(body)

        self.assertEqual(changed, 0)
        self.assertEqual(style_of(paragraph), "Normal")

    def test_toc_paragraphs_are_untouched(self):
        body = ET.Element(qn("body"))
        paragraph = make_paragraph("12.1 TOC entry", style="TOC2")
        body.append(paragraph)

        changed = promote_manual_numbered_headings(body)

        self.assertEqual(changed, 0)
        self.assertEqual(style_of(paragraph), "TOC2")


if __name__ == "__main__":
    unittest.main()
