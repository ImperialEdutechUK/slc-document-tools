"""Cover-page layout helpers for the SLC Word formatter."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

V_NS = "urn:schemas-microsoft-com:vml"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def vqn(tag: str) -> str:
    return "{" + V_NS + "}" + tag


def wqn(tag: str) -> str:
    return "{" + W_NS + "}" + tag


# The blue panel begins at approximately 63% of an A4 portrait page. These
# dimensions keep the full text box inside that panel with safe side margins.
_COVER_TEXTBOX_STYLE = {
    "position": "absolute",
    "margin-left": "47pt",
    "margin-top": "565pt",
    "width": "501pt",
    "height": "190pt",
    "z-index": "251660288",
    "visibility": "visible",
    "mso-wrap-style": "square",
    "mso-width-percent": "0",
    "mso-height-percent": "0",
    "mso-wrap-distance-left": "9pt",
    "mso-wrap-distance-top": "0",
    "mso-wrap-distance-right": "9pt",
    "mso-wrap-distance-bottom": "0",
    "mso-position-horizontal": "absolute",
    "mso-position-horizontal-relative": "page",
    "mso-position-vertical": "absolute",
    "mso-position-vertical-relative": "page",
    "mso-width-relative": "page",
    "mso-height-relative": "page",
    "v-text-anchor": "middle",
}


def _parse_style(style: str) -> tuple[list[str], dict[str, str]]:
    order: list[str] = []
    values: dict[str, str] = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        if key not in values:
            order.append(key)
        values[key] = value.strip()
    return order, values


def position_cover_textboxes(cover_element: ET.Element) -> int:
    """Place cover text boxes safely inside the lower blue A4 panel.

    Only VML shapes containing Word text-box content are changed. Other cover
    artwork remains untouched.
    """
    changed = 0
    for shape in cover_element.iter(vqn("shape")):
        if shape.find(".//" + wqn("txbxContent")) is None:
            continue

        order, values = _parse_style(shape.get("style", ""))
        for key, value in _COVER_TEXTBOX_STYLE.items():
            if key not in values:
                order.append(key)
            values[key] = value
        shape.set("style", ";".join(f"{key}:{values[key]}" for key in order))
        changed += 1
    return changed
