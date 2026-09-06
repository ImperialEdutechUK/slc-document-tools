"""Cover-page layout helpers for the SLC Word formatter.

The bundled cover template contains two compatibility representations of its
text box (modern DrawingML and a legacy VML fallback).  It also contains a
floating background image.  Word prefers the DrawingML representation, so all
three pieces must be positioned against the *actual output page geometry*.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

V_NS = "urn:schemas-microsoft-com:vml"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"

EMU_PER_POINT = 12_700

# The intended SLC A4 cover layout.  These are reference coordinates only;
# they are scaled to whatever page dimensions the output document actually
# uses before being written back to WordprocessingML.
REFERENCE_PAGE_WIDTH_PT = 11906 / 20
REFERENCE_PAGE_HEIGHT_PT = 16838 / 20
_REFERENCE_TEXTBOX = {
    "left": 47.0,
    "top": 565.0,
    "width": 501.0,
    "height": 190.0,
}


def vqn(tag: str) -> str:
    return "{" + V_NS + "}" + tag


def wqn(tag: str) -> str:
    return "{" + W_NS + "}" + tag


def wpqn(tag: str) -> str:
    return "{" + WP_NS + "}" + tag


def aqn(tag: str) -> str:
    return "{" + A_NS + "}" + tag


def wpsqn(tag: str) -> str:
    return "{" + WPS_NS + "}" + tag


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


def _format_pt(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return f"{int(rounded)}pt"
    return f"{rounded:.2f}".rstrip("0").rstrip(".") + "pt"


def _to_emu(points: float) -> str:
    return str(int(round(points * EMU_PER_POINT)))


def _scaled_textbox(page_width_pt: float, page_height_pt: float) -> dict[str, float]:
    scale_x = page_width_pt / REFERENCE_PAGE_WIDTH_PT
    scale_y = page_height_pt / REFERENCE_PAGE_HEIGHT_PT
    return {
        "left": _REFERENCE_TEXTBOX["left"] * scale_x,
        "top": _REFERENCE_TEXTBOX["top"] * scale_y,
        "width": _REFERENCE_TEXTBOX["width"] * scale_x,
        "height": _REFERENCE_TEXTBOX["height"] * scale_y,
    }


def _replace_position(position: ET.Element, offset_emu: str) -> None:
    for child in list(position):
        position.remove(child)
    ET.SubElement(position, wpqn("posOffset")).text = offset_emu


def _set_drawing_anchor_box(
    anchor: ET.Element,
    *,
    left_pt: float,
    top_pt: float,
    width_pt: float,
    height_pt: float,
) -> None:
    for attribute in ("distT", "distB", "distL", "distR"):
        anchor.set(attribute, "0")
    anchor.set("simplePos", "0")

    simple_pos = anchor.find(wpqn("simplePos"))
    if simple_pos is not None:
        simple_pos.set("x", "0")
        simple_pos.set("y", "0")

    position_h = anchor.find(wpqn("positionH"))
    if position_h is None:
        position_h = ET.Element(wpqn("positionH"), {"relativeFrom": "page"})
        anchor.insert(1, position_h)
    position_h.set("relativeFrom", "page")
    _replace_position(position_h, _to_emu(left_pt))

    position_v = anchor.find(wpqn("positionV"))
    if position_v is None:
        position_v = ET.Element(wpqn("positionV"), {"relativeFrom": "page"})
        insert_at = list(anchor).index(position_h) + 1
        anchor.insert(insert_at, position_v)
    position_v.set("relativeFrom", "page")
    _replace_position(position_v, _to_emu(top_pt))

    width_emu = _to_emu(width_pt)
    height_emu = _to_emu(height_pt)

    extent = anchor.find(wpqn("extent"))
    if extent is None:
        extent = ET.SubElement(anchor, wpqn("extent"))
    extent.set("cx", width_emu)
    extent.set("cy", height_emu)

    effect_extent = anchor.find(wpqn("effectExtent"))
    if effect_extent is not None:
        for attribute in ("l", "t", "r", "b"):
            effect_extent.set(attribute, "0")

    # Keep the shape's internal transform in sync with the anchor extent.
    for transform in anchor.iter(aqn("xfrm")):
        inner_extent = transform.find(aqn("ext"))
        if inner_extent is not None:
            inner_extent.set("cx", width_emu)
            inner_extent.set("cy", height_emu)


def position_cover_textboxes(
    cover_element: ET.Element,
    page_width_pt: float = REFERENCE_PAGE_WIDTH_PT,
    page_height_pt: float = REFERENCE_PAGE_HEIGHT_PT,
) -> int:
    """Position all cover text-box representations against the page.

    Word documents created by recent Microsoft Word versions normally contain
    a DrawingML text box inside ``mc:Choice`` and a VML fallback for older
    clients.  Updating only VML leaves the visible Word text box unchanged, so
    both representations are deliberately updated here.
    """
    box = _scaled_textbox(page_width_pt, page_height_pt)
    changed = 0

    # Modern DrawingML representation used by current Word versions.
    for anchor in cover_element.iter(wpqn("anchor")):
        if anchor.find(".//" + wqn("txbxContent")) is None:
            continue
        _set_drawing_anchor_box(anchor, **{
            "left_pt": box["left"],
            "top_pt": box["top"],
            "width_pt": box["width"],
            "height_pt": box["height"],
        })
        for body_pr in anchor.iter(wpsqn("bodyPr")):
            body_pr.set("anchor", "ctr")
        changed += 1

    # Legacy VML fallback used by compatibility readers.
    vml_style = {
        "position": "absolute",
        "margin-left": _format_pt(box["left"]),
        "margin-top": _format_pt(box["top"]),
        "width": _format_pt(box["width"]),
        "height": _format_pt(box["height"]),
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

    for shape in cover_element.iter(vqn("shape")):
        if shape.find(".//" + wqn("txbxContent")) is None:
            continue

        order, values = _parse_style(shape.get("style", ""))
        for key, value in vml_style.items():
            if key not in values:
                order.append(key)
            values[key] = value
        shape.set("style", ";".join(f"{key}:{values[key]}" for key in order))
        changed += 1

    return changed


def position_cover_background(
    cover_element: ET.Element,
    page_width_pt: float = REFERENCE_PAGE_WIDTH_PT,
    page_height_pt: float = REFERENCE_PAGE_HEIGHT_PT,
) -> int:
    """Resize and anchor floating cover artwork to the full output page.

    The historical template image was stored at Letter-like dimensions and was
    vertically positioned relative to its paragraph.  That caused visible
    offsets once the output section was normalised to A4.  Background artwork
    is now full-page and relative to the page origin instead.
    """
    changed = 0
    for anchor in cover_element.iter(wpqn("anchor")):
        if anchor.find(".//" + wqn("txbxContent")) is not None:
            continue
        has_picture = (
            anchor.find(".//" + aqn("blip")) is not None
            or anchor.find(".//{" + PIC_NS + "}pic") is not None
        )
        if not has_picture:
            continue

        _set_drawing_anchor_box(
            anchor,
            left_pt=0.0,
            top_pt=0.0,
            width_pt=page_width_pt,
            height_pt=page_height_pt,
        )
        anchor.set("behindDoc", "1")
        changed += 1

    return changed
