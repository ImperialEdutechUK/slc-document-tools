"""Document structure helpers for the SLC Word formatter.

The functions in this module operate directly on WordprocessingML so that the
formatter can add a predictable front-matter TOC and enforce page starts for
Heading 1-3 paragraphs without relying on Microsoft Word during generation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XMLNS = "http://www.w3.org/XML/1998/namespace"


def wt(tag: str) -> str:
    return "{" + WNS + "}" + tag


def _paragraph_style(paragraph: ET.Element) -> str:
    p_pr = paragraph.find(wt("pPr"))
    p_style = p_pr.find(wt("pStyle")) if p_pr is not None else None
    return p_style.get(wt("val"), "") if p_style is not None else ""


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(t.text or "" for t in paragraph.iter(wt("t"))).strip()


def _ensure_p_pr(paragraph: ET.Element) -> ET.Element:
    p_pr = paragraph.find(wt("pPr"))
    if p_pr is None:
        p_pr = ET.Element(wt("pPr"))
        paragraph.insert(0, p_pr)
    return p_pr


def _is_heading_style(style_id: str) -> bool:
    return style_id in {"Heading1", "Heading2", "Heading3"}


def _first_meaningful_top_level_block(body: ET.Element) -> ET.Element | None:
    """Return the first visible top-level block in the document body."""
    for child in list(body):
        if child.tag == wt("sectPr"):
            continue
        if child.tag == wt("tbl"):
            return child
        if child.tag != wt("p"):
            continue

        has_visible_content = bool(
            _paragraph_text(child)
            or child.find(".//" + wt("drawing")) is not None
            or child.find(".//" + wt("pict")) is not None
        )
        if has_visible_content:
            return child
    return None


def _insert_page_break_before(p_pr: ET.Element) -> None:
    existing = p_pr.find(wt("pageBreakBefore"))
    if existing is not None:
        p_pr.remove(existing)

    page_break_before = ET.Element(wt("pageBreakBefore"))
    page_break_before.set(wt("val"), "1")

    # Keep the element in the schema-friendly part of w:pPr: after style and
    # pagination controls, but before numbering, spacing, indentation, etc.
    allowed_before = {
        wt("pStyle"),
        wt("keepNext"),
        wt("keepLines"),
    }
    insert_at = 0
    for index, child in enumerate(list(p_pr)):
        if child.tag in allowed_before:
            insert_at = index + 1
    p_pr.insert(insert_at, page_break_before)


def force_headings_to_new_pages(
    body: ET.Element,
    *,
    skip_first_top_level_heading: bool = True,
) -> int:
    """Add ``pageBreakBefore`` to every Heading 1-3 paragraph.

    When a generated TOC is immediately followed by a manual page break, the
    first top-level heading does not need a second page-break instruction. It
    is therefore skipped only when it is also the first visible content block.

    The returned count is the number of Heading 1-3 paragraphs governed by the
    rule, including a first heading that is already separated by the TOC break.
    """
    first_block = _first_meaningful_top_level_block(body)
    first_heading_to_skip = None
    if (
        skip_first_top_level_heading
        and first_block is not None
        and first_block.tag == wt("p")
        and _is_heading_style(_paragraph_style(first_block))
    ):
        first_heading_to_skip = first_block

    parent_map = {
        child: parent
        for parent in body.iter()
        for child in list(parent)
    }

    governed = 0
    for paragraph in body.iter(wt("p")):
        if not _is_heading_style(_paragraph_style(paragraph)):
            continue

        # Text inside a drawing/text box is not part of the document's normal
        # page flow and should not be forced onto a separate page.
        ancestor = parent_map.get(paragraph)
        inside_text_box = False
        while ancestor is not None and ancestor is not body:
            if ancestor.tag == wt("txbxContent"):
                inside_text_box = True
                break
            ancestor = parent_map.get(ancestor)
        if inside_text_box:
            continue

        governed += 1
        if paragraph is first_heading_to_skip:
            # The generated page break after the TOC already starts this
            # heading on a new page.
            continue

        _insert_page_break_before(_ensure_p_pr(paragraph))

    return governed


def make_page_break_paragraph() -> ET.Element:
    paragraph = ET.Element(wt("p"))
    run = ET.SubElement(paragraph, wt("r"))
    page_break = ET.SubElement(run, wt("br"))
    page_break.set(wt("type"), "page")
    return paragraph


def make_toc_elements() -> list[ET.Element]:
    """Create an automatic Word TOC followed by a page break.

    Word updates the field when the document opens because the package's
    settings are also marked with ``updateFields`` by the formatter.
    """
    heading = ET.Element(wt("p"))
    heading_pr = ET.SubElement(heading, wt("pPr"))
    ET.SubElement(heading_pr, wt("pStyle")).set(wt("val"), "TOCHeading")
    heading_run = ET.SubElement(heading, wt("r"))
    heading_text = ET.SubElement(heading_run, wt("t"))
    heading_text.text = "Table of Contents"

    field_paragraph = ET.Element(wt("p"))

    begin_run = ET.SubElement(field_paragraph, wt("r"))
    begin = ET.SubElement(begin_run, wt("fldChar"))
    begin.set(wt("fldCharType"), "begin")
    begin.set(wt("dirty"), "true")

    instruction_run = ET.SubElement(field_paragraph, wt("r"))
    instruction = ET.SubElement(instruction_run, wt("instrText"))
    instruction.set("{" + XMLNS + "}space", "preserve")
    instruction.text = ' TOC \\o "1-3" \\h \\z \\u '

    separator_run = ET.SubElement(field_paragraph, wt("r"))
    separator = ET.SubElement(separator_run, wt("fldChar"))
    separator.set(wt("fldCharType"), "separate")

    placeholder_run = ET.SubElement(field_paragraph, wt("r"))
    placeholder_text = ET.SubElement(placeholder_run, wt("t"))
    placeholder_text.text = (
        "The table of contents will update automatically when opened in "
        "Microsoft Word."
    )

    end_run = ET.SubElement(field_paragraph, wt("r"))
    end = ET.SubElement(end_run, wt("fldChar"))
    end.set(wt("fldCharType"), "end")

    return [heading, field_paragraph, make_page_break_paragraph()]
