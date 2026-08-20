"""Reliable heading detection and style normalisation for WordprocessingML.

The formatter uses visible outline numbering as the source of truth.  It can
therefore correct a paragraph that already has the wrong Heading style, while
leaving unnumbered body text and ordinary bold paragraphs unchanged.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def qn(tag: str) -> str:
    return "{" + W_NS + "}" + tag


# Characters commonly copied from web pages/PDFs that are invisible but can
# prevent a regular expression from matching at the start of a paragraph.
_INVISIBLE_RE = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"
    "\u200b\u200c\u200d\u2060\ufeff]"
)
_SPACE_RE = re.compile(r"[\s\u00a0\u202f]+")

# Outline numbering must contain at least two components here (for example,
# 12.1 or 12.1.3).  Spaces around dots are accepted because Word documents
# assembled from copied content sometimes contain them.
_OUTLINE_RE = re.compile(
    r"^(?P<number>\d{1,3}(?:\s*\.\s*\d{1,3})+)"
    r"(?:\s*[.)])?"
    r"(?=\s|[:\-\u2013\u2014]|$)"
)

# A single manual number is allowed only up to three digits.  This prevents a
# year-like title such as "2026 Report" from being promoted accidentally.
_SINGLE_LEVEL_RE = re.compile(
    r"^(?P<number>\d{1,3})(?:\s*[.)])?"
    r"(?=\s|[:\-\u2013\u2014]|$)"
)

_UNIT_RE = re.compile(
    r"^(?:unit|chapter|module|section)\s+\d{1,3}\b",
    re.IGNORECASE,
)

_TOC_STYLES = {"TOC1", "TOC2", "TOC3", "TOCHeading", "TOC-Heading"}

# Direct paragraph properties that otherwise override the template's heading
# spacing, alignment and outline settings.
_PARAGRAPH_OVERRIDES = {
    "spacing",
    "jc",
    "ind",
    "pBdr",
    "shd",
    "tabs",
    "contextualSpacing",
    "keepNext",
    "keepLines",
    "pageBreakBefore",
    "widowControl",
    "outlineLvl",
}

# Direct character formatting and linked Heading character styles can make a
# corrected Heading 2 paragraph still look partly like Heading 1.  Remove only
# visual properties; language, fields, proofing and other semantic properties
# remain untouched.
_RUN_OVERRIDES = {
    "rFonts",
    "b",
    "bCs",
    "i",
    "iCs",
    "caps",
    "smallCaps",
    "strike",
    "dstrike",
    "color",
    "sz",
    "szCs",
    "u",
    "vertAlign",
    "position",
    "spacing",
    "w",
    "kern",
    "highlight",
    "shd",
    "effect",
    "outline",
    "shadow",
    "emboss",
    "imprint",
}


def normalise_heading_text(text: str) -> str:
    """Return a matching-friendly version without altering document content."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _INVISIBLE_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def expected_heading_style(text: str) -> str | None:
    """Infer Heading1-3 from a visible manual heading number.

    More deeply nested headings are deliberately capped at Heading 3 because
    the formatter's document template uses three principal numbered levels.
    """
    clean = normalise_heading_text(text)
    if not clean:
        return None

    lowered = clean.casefold()

    # This recurring end-of-document section must always use the main
    # Heading 1 style, even though it is unnumbered. Accept an optional final
    # colon so minor authoring differences do not affect the result.
    if lowered.rstrip(":").strip() == "resources for further reference":
        return "Heading1"

    if lowered in {"references", "table of contents"}:
        return None

    # Missing numbered-image placeholders must remain ordinary body text so
    # they are visible to the validation workflow rather than styled as Unit
    # headings.
    if re.search(r"\bimage\s+\d+\s*$", clean, re.IGNORECASE):
        return None

    if _UNIT_RE.match(clean):
        return "Heading1"

    match = _OUTLINE_RE.match(clean)
    if match:
        number = re.sub(r"\s+", "", match.group("number"))
        depth = number.count(".") + 1
        # Two-component headings such as 4.1 are main headings in the SLC
        # source documents and must therefore use Heading 1. Each additional
        # component moves down one level, capped at Heading 3.
        mapped_level = max(1, depth - 1)
        return f"Heading{min(mapped_level, 3)}"

    match = _SINGLE_LEVEL_RE.match(clean)
    if match:
        # Require actual title text after the number/separator.  A paragraph
        # containing only a number is more likely to be a list item or data.
        remainder = clean[match.end():].lstrip(" :-\u2013\u2014")
        if remainder:
            return "Heading1"

    return None


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(
        text_node.text or "" for text_node in paragraph.iter(qn("t"))
    ).strip()


def _ensure_ppr(paragraph: ET.Element) -> ET.Element:
    ppr = paragraph.find(qn("pPr"))
    if ppr is None:
        ppr = ET.Element(qn("pPr"))
        paragraph.insert(0, ppr)
    return ppr


def _style_value(ppr: ET.Element) -> str:
    style = ppr.find(qn("pStyle"))
    return style.get(qn("val"), "") if style is not None else ""


def _set_style(ppr: ET.Element, style_name: str) -> None:
    style = ppr.find(qn("pStyle"))
    if style is None:
        style = ET.Element(qn("pStyle"))
        ppr.insert(0, style)
    style.set(qn("val"), style_name)


def _remove_children_by_local_name(
    parent: ET.Element | None,
    names: set[str],
) -> None:
    if parent is None:
        return
    for child in list(parent):
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name in names:
            parent.remove(child)


def _clear_conflicting_formatting(paragraph: ET.Element, ppr: ET.Element) -> None:
    _remove_children_by_local_name(ppr, _PARAGRAPH_OVERRIDES)

    # Manual visible numbering and automatic list numbering together would
    # display duplicate numbers.  Remove numPr only for paragraphs that have
    # already been confidently identified from visible numbering.
    num_pr = ppr.find(qn("numPr"))
    if num_pr is not None:
        ppr.remove(num_pr)

    paragraph_run_properties = ppr.find(qn("rPr"))
    _remove_children_by_local_name(paragraph_run_properties, _RUN_OVERRIDES)

    for run_properties in paragraph.iter(qn("rPr")):
        character_style = run_properties.find(qn("rStyle"))
        if character_style is not None:
            value = character_style.get(qn("val"), "")
            if value.startswith("Heading"):
                run_properties.remove(character_style)
        _remove_children_by_local_name(run_properties, _RUN_OVERRIDES)


def promote_manual_numbered_headings(out_body: ET.Element) -> int:
    """Apply or correct heading styles throughout the document body.

    Visible numbering is used to infer levels for ordinary numbered text.  A
    content paragraph that the author explicitly marked as Heading 2 is treated
    as the main topic and promoted to Heading 1, including a numbered topic.
    Paragraphs nested inside tables are included, while TOC content and cover
    text boxes remain untouched.
    """
    changed = 0

    # Cover-page text is stored inside Word text boxes.  It can contain values
    # such as "Unit 12 Chapter 1", but its template formatting must not be
    # replaced with document-body heading styles.
    text_box_paragraphs = {
        paragraph
        for text_box in out_body.iter(qn("txbxContent"))
        for paragraph in text_box.iter(qn("p"))
    }

    for paragraph in list(out_body.iter(qn("p"))):
        if paragraph in text_box_paragraphs:
            continue
        text = paragraph_text(paragraph)
        ppr = _ensure_ppr(paragraph)
        current_style = _style_value(ppr)
        if current_style in _TOC_STYLES or current_style.startswith("TOC"):
            continue

        expected_style = expected_heading_style(text)

        # In source documents, authors deliberately use Heading 2 to identify
        # the main topic.  That explicit authoring signal takes precedence over
        # inferred numbering and is mapped to the template's Heading 1 style.
        if current_style == "Heading2":
            clean = normalise_heading_text(text)
            if (
                clean
                and clean.casefold() not in {"references", "table of contents"}
                and not re.search(r"\bimage\s+\d+\s*$", clean, re.IGNORECASE)
            ):
                expected_style = "Heading1"

        if expected_style is None:
            continue

        _set_style(ppr, expected_style)
        _clear_conflicting_formatting(paragraph, ppr)

        # Count paragraphs processed even when their paragraph style was
        # already correct, because conflicting direct formatting may still
        # have been normalised.
        changed += 1

    return changed
