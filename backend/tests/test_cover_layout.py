import unittest
import xml.etree.ElementTree as ET

from app.formatter.cover_layout import V_NS, W_NS, position_cover_textboxes, vqn, wqn


class CoverLayoutTests(unittest.TestCase):
    def test_textbox_is_positioned_within_blue_a4_panel(self):
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

    def test_non_textbox_shape_is_untouched(self):
        host = ET.Element(wqn("p"))
        shape = ET.SubElement(host, vqn("shape"))
        shape.set("style", "position:absolute")

        changed = position_cover_textboxes(host)

        self.assertEqual(changed, 0)
        self.assertEqual(shape.get("style"), "position:absolute")


if __name__ == "__main__":
    unittest.main()
