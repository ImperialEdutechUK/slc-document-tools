import unittest
import xml.etree.ElementTree as ET

from app.formatter.cover_layout import (
    A_NS,
    EMU_PER_POINT,
    REFERENCE_PAGE_HEIGHT_PT,
    REFERENCE_PAGE_WIDTH_PT,
    V_NS,
    W_NS,
    WP_NS,
    WPS_NS,
    WP14_NS,
    BACKGROUND_BLEED_PT,
    position_cover_background,
    position_cover_textboxes,
    vqn,
    wqn,
)


def qn(ns, tag):
    return "{" + ns + "}" + tag


class CoverLayoutTests(unittest.TestCase):
    def test_vml_textbox_is_positioned_within_blue_a4_panel(self):
        host = ET.Element(wqn("p"))
        shape = ET.SubElement(host, vqn("shape"))
        shape.set(
            "style",
            "position:absolute;margin-left:-34.2pt;margin-top:515.7pt;"
            "width:521.4pt;height:148.2pt;"
            "mso-position-horizontal-relative:margin;"
            "mso-position-vertical-relative:text;v-text-anchor:top",
        )
        textbox = ET.SubElement(shape, wqn("txbxContent"))
        ET.SubElement(textbox, wqn("p"))

        changed = position_cover_textboxes(host)

        self.assertEqual(changed, 1)
        style = shape.get("style", "")
        self.assertIn("margin-left:47pt", style)
        self.assertIn("margin-top:565pt", style)
        self.assertIn("width:501pt", style)
        self.assertIn("height:190pt", style)
        self.assertIn("mso-position-horizontal-relative:page", style)
        self.assertIn("mso-position-vertical-relative:page", style)
        self.assertIn("v-text-anchor:middle", style)

    def test_modern_drawingml_textbox_is_scaled_and_page_relative(self):
        host = ET.Element(wqn("p"))
        anchor = ET.SubElement(host, qn(WP_NS, "anchor"))
        ET.SubElement(anchor, qn(WP_NS, "simplePos"), {"x": "5", "y": "6"})
        pos_h = ET.SubElement(anchor, qn(WP_NS, "positionH"), {"relativeFrom": "margin"})
        ET.SubElement(pos_h, qn(WP_NS, "align")).text = "left"
        pos_v = ET.SubElement(anchor, qn(WP_NS, "positionV"), {"relativeFrom": "paragraph"})
        ET.SubElement(pos_v, qn(WP_NS, "posOffset")).text = "123"
        extent = ET.SubElement(anchor, qn(WP_NS, "extent"), {"cx": "1", "cy": "2"})
        transform = ET.SubElement(anchor, qn(A_NS, "xfrm"))
        inner_extent = ET.SubElement(transform, qn(A_NS, "ext"), {"cx": "3", "cy": "4"})
        body_pr = ET.SubElement(anchor, qn(WPS_NS, "bodyPr"), {"anchor": "t"})
        textbox = ET.SubElement(anchor, wqn("txbxContent"))
        ET.SubElement(textbox, wqn("p"))

        changed = position_cover_textboxes(
            host,
            REFERENCE_PAGE_WIDTH_PT * 2,
            REFERENCE_PAGE_HEIGHT_PT * 2,
        )

        self.assertEqual(changed, 1)
        self.assertEqual("page", pos_h.get("relativeFrom"))
        self.assertEqual("page", pos_v.get("relativeFrom"))
        self.assertEqual(
            str(round(94 * EMU_PER_POINT)),
            pos_h.find(qn(WP_NS, "posOffset")).text,
        )
        self.assertEqual(
            str(round(1130 * EMU_PER_POINT)),
            pos_v.find(qn(WP_NS, "posOffset")).text,
        )
        self.assertEqual(str(round(1002 * EMU_PER_POINT)), extent.get("cx"))
        self.assertEqual(str(round(380 * EMU_PER_POINT)), extent.get("cy"))
        self.assertEqual(extent.get("cx"), inner_extent.get("cx"))
        self.assertEqual(extent.get("cy"), inner_extent.get("cy"))
        self.assertEqual("ctr", body_pr.get("anchor"))

    def test_cover_background_fills_actual_page(self):
        host = ET.Element(wqn("p"))
        anchor = ET.SubElement(host, qn(WP_NS, "anchor"), {"behindDoc": "0"})
        ET.SubElement(anchor, qn(WP_NS, "simplePos"), {"x": "0", "y": "0"})
        pos_h = ET.SubElement(anchor, qn(WP_NS, "positionH"), {"relativeFrom": "page"})
        ET.SubElement(pos_h, qn(WP_NS, "align")).text = "right"
        pos_v = ET.SubElement(anchor, qn(WP_NS, "positionV"), {"relativeFrom": "paragraph"})
        ET.SubElement(pos_v, qn(WP_NS, "posOffset")).text = "-914400"
        extent = ET.SubElement(anchor, qn(WP_NS, "extent"), {"cx": "7772400", "cy": "10050780"})
        ET.SubElement(anchor, qn(A_NS, "blip"))
        transform = ET.SubElement(anchor, qn(A_NS, "xfrm"))
        inner_extent = ET.SubElement(transform, qn(A_NS, "ext"), {"cx": "1", "cy": "1"})

        changed = position_cover_background(host, 600.0, 800.0)

        self.assertEqual(changed, 1)
        self.assertEqual("page", pos_h.get("relativeFrom"))
        self.assertEqual(str(round(-BACKGROUND_BLEED_PT * EMU_PER_POINT)), pos_h.find(qn(WP_NS, "posOffset")).text)
        self.assertEqual("page", pos_v.get("relativeFrom"))
        self.assertEqual(str(round(-BACKGROUND_BLEED_PT * EMU_PER_POINT)), pos_v.find(qn(WP_NS, "posOffset")).text)
        self.assertEqual(str(round((600 + 2 * BACKGROUND_BLEED_PT) * EMU_PER_POINT)), extent.get("cx"))
        self.assertEqual(str(round((800 + 2 * BACKGROUND_BLEED_PT) * EMU_PER_POINT)), extent.get("cy"))
        self.assertEqual(extent.get("cx"), inner_extent.get("cx"))
        self.assertEqual(extent.get("cy"), inner_extent.get("cy"))
        self.assertEqual("1", anchor.get("behindDoc"))

    def test_background_removes_word_relative_size_hints_and_aspect_lock(self):
        host = ET.Element(wqn("p"))
        anchor = ET.SubElement(host, qn(WP_NS, "anchor"), {"layoutInCell": "1"})
        ET.SubElement(anchor, qn(WP_NS, "simplePos"), {"x": "0", "y": "0"})
        ET.SubElement(anchor, qn(WP_NS, "positionH"), {"relativeFrom": "page"})
        ET.SubElement(anchor, qn(WP_NS, "positionV"), {"relativeFrom": "paragraph"})
        ET.SubElement(anchor, qn(WP_NS, "extent"), {"cx": "7772400", "cy": "10050780"})
        locks = ET.SubElement(anchor, qn(A_NS, "graphicFrameLocks"), {"noChangeAspect": "1"})
        ET.SubElement(anchor, qn(A_NS, "blip"))
        size_h = ET.SubElement(anchor, qn(WP14_NS, "sizeRelH"), {"relativeFrom": "margin"})
        ET.SubElement(size_h, qn(WP14_NS, "pctWidth")).text = "0"
        size_v = ET.SubElement(anchor, qn(WP14_NS, "sizeRelV"), {"relativeFrom": "margin"})
        ET.SubElement(size_v, qn(WP14_NS, "pctHeight")).text = "0"

        changed = position_cover_background(host)

        self.assertEqual(changed, 1)
        self.assertIsNone(anchor.find(qn(WP14_NS, "sizeRelH")))
        self.assertIsNone(anchor.find(qn(WP14_NS, "sizeRelV")))
        self.assertEqual("0", locks.get("noChangeAspect"))
        self.assertEqual("0", anchor.get("layoutInCell"))

    def test_non_textbox_shape_is_untouched(self):
        host = ET.Element(wqn("p"))
        shape = ET.SubElement(host, vqn("shape"))
        shape.set("style", "position:absolute")

        changed = position_cover_textboxes(host)

        self.assertEqual(changed, 0)
        self.assertEqual(shape.get("style"), "position:absolute")


if __name__ == "__main__":
    unittest.main()
