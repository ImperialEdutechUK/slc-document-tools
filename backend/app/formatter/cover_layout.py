"""Cover-page layout helpers for the SLC Word formatter.

The bundled cover template was authored with a Letter-sized floating picture
(612 x ~791 pt) even though the generated document is A4. Microsoft Word can
retain those historical drawing hints more aggressively than LibreOffice,
which is why the old cover could stop about 50 pt above the bottom of an A4
page even after the visible ``wp:extent`` was changed.

These helpers rewrite every size/position hint that can influence Word's
rendering, remove stale relative-size metadata, and apply a very small bleed so
rounding can never expose a white strip at a page edge.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET

from PIL import Image

V_NS = "urn:schemas-microsoft-com:vml"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WP14_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"

EMU_PER_POINT = 12_700

# A4 reference geometry in points (Word stores the section size in twips).
REFERENCE_PAGE_WIDTH_PT = 11906 / 20
REFERENCE_PAGE_HEIGHT_PT = 16838 / 20

# Keep the original visual placement of the three cover labels while scaling
# it to the real output page size.
_REFERENCE_TEXTBOX = {
    "left": 47.0,
    "top": 565.0,
    "width": 501.0,
    "height": 190.0,
}

# 1 pt on every side is enough to hide renderer rounding without visibly
# cropping the artwork. Word clips the excess at the page boundary.
BACKGROUND_BLEED_PT = 1.0


# Brand teal used for the cover's bottom text band (matches the footer rule
# color and the table-cell border color used elsewhere in the formatter).
BRAND_TEAL_RGB = (0x1A, 0x99, 0xA0)

# How far a pixel's color may drift from BRAND_TEAL_RGB (Euclidean distance
# in RGB space) and still count as "the teal band", including its
# semi-transparent gradient edge over the photo. Calibrated against real
# cover photos: solid teal fill sits around distance ~24, the gradient edge
# around ~40; ordinary photo content (skin tones, suits, walls) measures
# well above 100.
_TEAL_MATCH_TOLERANCE = 55

# Minimum number of consecutive non-teal rows (scanning upward from the
# bottom of the image) required to conclude the teal band has ended, so a
# single noisy/JPEG-artifact row can't cut the detected band short.
_BAND_BOUNDARY_CONFIRM_ROWS = 4


def detect_teal_band_top_fraction(image_bytes: bytes) -> float | None:
    """Return how far down a cover photo its solid teal band begins.

    Cover photos carry their brand-teal caption band baked directly into
    the image, and different photos position that band at different
    heights. A text box positioned from a single fixed reference coordinate
    (calibrated against one specific photo) can therefore land above the
    band — overlapping the photograph — on any other photo whose band sits
    lower.

    This scans a vertical strip near the horizontal center of the image,
    from the bottom upward, looking for where the pixel color stops being
    within BRAND_TEAL_RGB's tolerance. That boundary, expressed as a
    fraction of the image's total height (0 = top, 1 = bottom), is where
    the solid band reliably begins. Returns None if no such band can be
    confidently detected (e.g. a photo with no teal band at all), so the
    caller can fall back to a fixed default rather than mis-position text
    against a boundary that doesn't really exist.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            width, height = img.size
            if width < 2 or height < 2:
                return None

            left = int(width * 0.3)
            right = max(left + 1, int(width * 0.7))
            strip = img.crop((left, 0, right, height))
            pixels = strip.load()
            strip_width = right - left

            def row_is_teal(y: int) -> bool:
                total = 0
                r_sum = g_sum = b_sum = 0
                for x in range(strip_width):
                    r, g, b = pixels[x, y]
                    r_sum += r
                    g_sum += g
                    b_sum += b
                    total += 1
                r_avg = r_sum / total
                g_avg = g_sum / total
                b_avg = b_sum / total
                distance = (
                    (r_avg - BRAND_TEAL_RGB[0]) ** 2
                    + (g_avg - BRAND_TEAL_RGB[1]) ** 2
                    + (b_avg - BRAND_TEAL_RGB[2]) ** 2
                ) ** 0.5
                return distance <= _TEAL_MATCH_TOLERANCE

            if not row_is_teal(height - 1):
                return None

            non_teal_run = 0
            boundary_y = 0
            for y in range(height - 1, -1, -1):
                if row_is_teal(y):
                    non_teal_run = 0
                    boundary_y = y
                else:
                    non_teal_run += 1
                    if non_teal_run >= _BAND_BOUNDARY_CONFIRM_ROWS:
                        break

            return boundary_y / height
    except Exception:
        return None


def vqn(tag: str) -> str:
    return "{" + V_NS + "}" + tag


def wqn(tag: str) -> str:
    return "{" + W_NS + "}" + tag


def wpqn(tag: str) -> str:
    return "{" + WP_NS + "}" + tag


def wp14qn(tag: str) -> str:
    return "{" + WP14_NS + "}" + tag


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


_BAND_TOP_PADDING_PT = 10.0
_BAND_BOTTOM_MARGIN_PT = 18.0
_MIN_TEXTBOX_HEIGHT_PT = 90.0


def _scaled_textbox(
    page_width_pt: float,
    page_height_pt: float,
    band_top_fraction: float | None = None,
) -> dict[str, float]:
    scale_x = page_width_pt / REFERENCE_PAGE_WIDTH_PT
    scale_y = page_height_pt / REFERENCE_PAGE_HEIGHT_PT

    left = _REFERENCE_TEXTBOX["left"] * scale_x
    width = _REFERENCE_TEXTBOX["width"] * scale_x
    default_top = _REFERENCE_TEXTBOX["top"] * scale_y
    default_height = _REFERENCE_TEXTBOX["height"] * scale_y

    if band_top_fraction is None:
        return {
            "left": left,
            "top": default_top,
            "width": width,
            "height": default_height,
        }

    top = (band_top_fraction * page_height_pt) + _BAND_TOP_PADDING_PT
    available_height = page_height_pt - top - _BAND_BOTTOM_MARGIN_PT

    if available_height < _MIN_TEXTBOX_HEIGHT_PT:
        # The detected band is too close to the bottom of the page to fit a
        # readable text box below it — this reads as an unreliable
        # detection (e.g. a very thin accent stripe rather than the real
        # caption band), so fall back to the fixed reference position
        # rather than cramming text into a sliver of space.
        return {
            "left": left,
            "top": default_top,
            "width": width,
            "height": default_height,
        }

    height = min(default_height, available_height)
    return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


def _replace_position(position: ET.Element, offset_emu: str) -> None:
    for child in list(position):
        position.remove(child)
    ET.SubElement(position, wpqn("posOffset")).text = offset_emu


def _remove_stale_relative_sizing(anchor: ET.Element) -> None:
    """Remove Word 2010 relative-size hints from the historical template.

    The template stores ``wp14:sizeRelH/V`` elements even though their values
    are zero. Current Word versions may preserve/reconstruct the original
    Letter drawing geometry from this metadata when a document is opened and
    saved. The exact ``wp:extent`` values are sufficient, so the stale hints
    are removed completely.
    """

    for child in list(anchor):
        if child.tag in {wp14qn("sizeRelH"), wp14qn("sizeRelV")}:
            anchor.remove(child)


def _unlock_aspect_ratio(anchor: ET.Element) -> None:
    """Allow the cover artwork to match the page's exact A4 aspect ratio."""

    for locks in anchor.iter(aqn("graphicFrameLocks")):
        # The source template locks the old Letter-shaped frame. This setting
        # is an editing constraint, not useful for a page background, and can
        # cause Word to restore the old dimensions.
        locks.set("noChangeAspect", "0")


def _sync_internal_transforms(
    anchor: ET.Element,
    width_emu: str,
    height_emu: str,
) -> None:
    """Synchronise all DrawingML transforms with the outer anchor extent."""

    for transform in anchor.iter(aqn("xfrm")):
        inner_offset = transform.find(aqn("off"))
        if inner_offset is not None:
            inner_offset.set("x", "0")
            inner_offset.set("y", "0")

        inner_extent = transform.find(aqn("ext"))
        if inner_extent is not None:
            inner_extent.set("cx", width_emu)
            inner_extent.set("cy", height_emu)


def _set_drawing_anchor_box(
    anchor: ET.Element,
    *,
    left_pt: float,
    top_pt: float,
    width_pt: float,
    height_pt: float,
    unlock_aspect: bool = False,
) -> None:
    """Write an absolute page-relative box in every relevant DrawingML field."""

    for attribute in ("distT", "distB", "distL", "distR"):
        anchor.set(attribute, "0")

    anchor.set("simplePos", "0")
    # Page-relative objects must not be constrained to a table/text cell. This
    # also improves Word/Word Online parity for body-anchored cover artwork.
    anchor.set("layoutInCell", "0")
    anchor.set("allowOverlap", "1")

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

    _remove_stale_relative_sizing(anchor)
    if unlock_aspect:
        _unlock_aspect_ratio(anchor)
    _sync_internal_transforms(anchor, width_emu, height_emu)


def position_cover_textboxes(
    cover_element: ET.Element,
    page_width_pt: float = REFERENCE_PAGE_WIDTH_PT,
    page_height_pt: float = REFERENCE_PAGE_HEIGHT_PT,
    band_top_fraction: float | None = None,
) -> int:
    """Position modern and legacy cover text boxes against the actual page.

    When ``band_top_fraction`` is given (see
    ``detect_teal_band_top_fraction``), the box is anchored just below that
    detected boundary instead of the fixed reference position, so the text
    lands inside this specific photo's teal band rather than a position
    calibrated against a different photo.
    """

    box = _scaled_textbox(page_width_pt, page_height_pt, band_top_fraction)
    changed = 0

    # Modern DrawingML representation used by current Word versions.
    for anchor in cover_element.iter(wpqn("anchor")):
        if anchor.find(".//" + wqn("txbxContent")) is None:
            continue
        _set_drawing_anchor_box(
            anchor,
            left_pt=box["left"],
            top_pt=box["top"],
            width_pt=box["width"],
            height_pt=box["height"],
        )
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
    """Make cover artwork a true full-page background in Word and LibreOffice.

    The source cover's frame is 612 x ~791 pt (Letter-like). On an A4 page the
    vertical difference is about 50 pt, matching the white strip visible in
    Word when the historical geometry is retained. We therefore remove all
    stale relative-size/aspect metadata and write a slightly oversized A4 box
    so Word clips the artwork cleanly to the page edges.
    """

    changed = 0
    bleed = BACKGROUND_BLEED_PT

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
            left_pt=-bleed,
            top_pt=-bleed,
            width_pt=page_width_pt + (2 * bleed),
            height_pt=page_height_pt + (2 * bleed),
            unlock_aspect=True,
        )
        anchor.set("behindDoc", "1")
        changed += 1

    return changed
