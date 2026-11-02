"""Keep the legacy SLC template's cover artwork aligned with its page.

This module is required by both the existing document formatter and the PDF
cover editor. It was absent from the uploaded repository; the functionality
below restores the page-relative positioning those callers expect.
"""
from __future__ import annotations

from io import BytesIO
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageStat, ImageOps

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
V_NS = "urn:schemas-microsoft-com:vml"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WP14_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
EMU_PER_POINT = 12700
REFERENCE_PAGE_WIDTH_PT = 612.0
REFERENCE_PAGE_HEIGHT_PT = 792.0
BACKGROUND_BLEED_PT = 1.5


def wqn(tag: str) -> str:
    return "{" + W_NS + "}" + tag


def vqn(tag: str) -> str:
    return "{" + V_NS + "}" + tag


def _q(ns: str, tag: str) -> str:
    return "{" + ns + "}" + tag


def _reset_position(anchor: ET.Element, tag: str, value: float) -> None:
    position = anchor.find(_q(WP_NS, tag))
    if position is None:
        position = ET.SubElement(anchor, _q(WP_NS, tag))
    position.set("relativeFrom", "page")
    for child in list(position):
        position.remove(child)
    ET.SubElement(position, _q(WP_NS, "posOffset")).text = str(round(value * EMU_PER_POINT))


def _set_extent(parent: ET.Element, width_pt: float, height_pt: float, namespace: str, child_name: str, x: str, y: str) -> None:
    extent = parent.find(_q(namespace, child_name))
    if extent is None:
        extent = ET.SubElement(parent, _q(namespace, child_name))
    extent.set(x, str(round(width_pt * EMU_PER_POINT)))
    extent.set(y, str(round(height_pt * EMU_PER_POINT)))


def position_cover_background(
    root: ET.Element, page_width_pt: float = REFERENCE_PAGE_WIDTH_PT,
    page_height_pt: float = REFERENCE_PAGE_HEIGHT_PT,
) -> int:
    """Anchor cover images to the actual full-page bounds, with a tiny bleed."""
    count = 0
    for anchor in root.iter(_q(WP_NS, "anchor")):
        if anchor.find(".//" + _q(A_NS, "blip")) is None:
            continue
        # The background is the photo-only anchor; a textbox may also have an
        # unrelated a:blip, so do not treat an anchor containing text as photo.
        if anchor.find(".//" + wqn("txbxContent")) is not None:
            continue
        anchor.set("behindDoc", "1")
        anchor.set("layoutInCell", "0")
        _reset_position(anchor, "positionH", -BACKGROUND_BLEED_PT)
        _reset_position(anchor, "positionV", -BACKGROUND_BLEED_PT)
        width = page_width_pt + 2 * BACKGROUND_BLEED_PT
        height = page_height_pt + 2 * BACKGROUND_BLEED_PT
        _set_extent(anchor, width, height, WP_NS, "extent", "cx", "cy")
        for inner in anchor.iter(_q(A_NS, "xfrm")):
            _set_extent(inner, width, height, A_NS, "ext", "cx", "cy")
        for locks in anchor.iter(_q(A_NS, "graphicFrameLocks")):
            locks.set("noChangeAspect", "0")
        for child in list(anchor):
            if child.tag in {_q(WP14_NS, "sizeRelH"), _q(WP14_NS, "sizeRelV")}:
                anchor.remove(child)
        count += 1
    return count


def position_cover_textboxes(
    root: ET.Element, page_width_pt: float = REFERENCE_PAGE_WIDTH_PT,
    page_height_pt: float = REFERENCE_PAGE_HEIGHT_PT,
    band_top_fraction: float | None = None,
) -> int:
    """Move both Word's modern and legacy caption boxes inside cover panel."""
    sx = page_width_pt / REFERENCE_PAGE_WIDTH_PT
    sy = page_height_pt / REFERENCE_PAGE_HEIGHT_PT
    band_shift = 0.0 if band_top_fraction is None else (max(0.55, min(0.83, band_top_fraction)) - 0.72) * REFERENCE_PAGE_HEIGHT_PT
    x, y, width, height = 47.0 * sx, (565.0 + band_shift) * sy, 501.0 * sx, 190.0 * sy
    changed = 0
    for shape in root.iter(vqn("shape")):
        if shape.find(".//" + wqn("txbxContent")) is None:
            continue
        style = shape.get("style", "")
        attributes = {}
        for item in style.split(";"):
            if ":" in item:
                key, value = item.split(":", 1)
                attributes[key.strip()] = value.strip()
        attributes.update({
            "position": "absolute",
            "margin-left": f"{round(x, 2):g}pt",
            "margin-top": f"{round(y, 2):g}pt",
            "width": f"{round(width, 2):g}pt",
            "height": f"{round(height, 2):g}pt",
            "mso-position-horizontal-relative": "page",
            "mso-position-vertical-relative": "page",
            "v-text-anchor": "middle",
        })
        shape.set("style", ";".join(f"{k}:{v}" for k, v in attributes.items()))
        changed += 1
    for anchor in root.iter(_q(WP_NS, "anchor")):
        if anchor.find(".//" + wqn("txbxContent")) is None:
            continue
        _reset_position(anchor, "positionH", x)
        _reset_position(anchor, "positionV", y)
        _set_extent(anchor, width, height, WP_NS, "extent", "cx", "cy")
        for transform in anchor.iter(_q(A_NS, "xfrm")):
            _set_extent(transform, width, height, A_NS, "ext", "cx", "cy")
        for body_pr in anchor.iter(_q(WPS_NS, "bodyPr")):
            body_pr.set("anchor", "ctr")
        changed += 1
    return changed


def detect_teal_band_top_fraction(image_bytes: bytes) -> float | None:
    """Detect a large, flat teal/green lower title band; otherwise return None."""
    try:
        with Image.open(BytesIO(image_bytes)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((320, 440))
            w, h = image.size
            if h < 60:
                return None
            # Require the bottom panel to be predominately teal/green and
            # low-variance (rather than green foliage in a scene).
            panel = image.crop((int(w * .13), int(h * .86), int(w * .87), int(h * .94)))
            stat = ImageStat.Stat(panel)
            red, green, blue = stat.mean
            if not (green > 45 and green > red * 1.11 and blue >= red * .67 and max(stat.stddev) < 33):
                return None
            for fraction in [i / 100 for i in range(57, 86)]:
                y = int(h * fraction)
                band = image.crop((int(w * .13), y, int(w * .87), min(h, y + max(3, h // 50))))
                current = ImageStat.Stat(band)
                if max(current.stddev) < 36 and sum(abs(a - b) for a, b in zip(current.mean, stat.mean)) < 65:
                    return fraction
    except Exception:
        return None
    return None
