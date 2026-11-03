import unittest
import xml.etree.ElementTree as ET

from app.formatter.engine import add_bullets_to_references, wt


def paragraph(text, style=None, numbered=False):
    p = ET.Element(wt("p"))
    p_pr = ET.SubElement(p, wt("pPr"))
    if style:
        ET.SubElement(p_pr, wt("pStyle")).set(wt("val"), style)
    if numbered:
        num_pr = ET.SubElement(p_pr, wt("numPr"))
        ET.SubElement(num_pr, wt("ilvl")).set(wt("val"), "0")
        ET.SubElement(num_pr, wt("numId")).set(wt("val"), "3")
    run = ET.SubElement(p, wt("r"))
    ET.SubElement(run, wt("t")).text = text
    return p


class ReferenceBulletTests(unittest.TestCase):
    def test_resources_for_further_reference_heading_uses_bullets(self):
        body = ET.Element(wt("body"))
        heading = paragraph("Resources for Further Reference:", "Heading1")
        first = paragraph("Department for Education item", numbered=True)
        second = paragraph("2. Another source")
        next_heading = paragraph("Next Section", "Heading1")
        after = paragraph("Should not become a bullet")
        body.extend([heading, first, second, next_heading, after])

        changed = add_bullets_to_references(body)

        self.assertEqual(changed, 2)
        for item in (first, second):
            num_id = item.find("./" + wt("pPr") + "/" + wt("numPr") + "/" + wt("numId"))
            self.assertIsNotNone(num_id)
            self.assertEqual(num_id.get(wt("val")), "12")
        text = "".join(node.text or "" for node in second.iter(wt("t")))
        self.assertEqual(text, "Another source")
        self.assertIsNone(after.find("./" + wt("pPr") + "/" + wt("numPr")))


if __name__ == "__main__":
    unittest.main()
