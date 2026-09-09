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


def collapse_excess_blank_paragraphs(
    body: ET.Element,
    *,
    max_consecutive: int = 1,
) -> int:
    """Collapse long runs of empty paragraphs down to at most
    ``max_consecutive`` in a row.

    Uploaded source documents sometimes carry a long run of blank lines
    (leftover spacer paragraphs from manual formatting) that has no visible
    effect other than pushing a full extra blank page into the output. This
    trims those runs while leaving a single blank paragraph in place so any
    intentional spacing is preserved, and never touches a paragraph that
    carries pagination-relevant properties (a manual page break, a section
    break, list numbering, or an explicit page-break-before flag) since
    those are never purely decorative.

    ``bookmarkStart``/``bookmarkEnd`` markers are treated as transparent:
    they neither count towards nor interrupt a run of blank paragraphs,
    since Word commonly wraps headings in bookmarks and that should not
    prevent the blank paragraphs around them from being collapsed.
    """

    def is_collapsible_blank(paragraph: ET.Element) -> bool:
        if paragraph.tag != wt("p"):
            return False
        if _paragraph_text(paragraph):
            return False
        if paragraph.find(".//" + wt("drawing")) is not None:
            return False
        if paragraph.find(".//" + wt("pict")) is not None:
            return False

        p_pr = paragraph.find(wt("pPr"))
        if p_pr is not None:
            if p_pr.find(wt("sectPr")) is not None:
                return False
            if p_pr.find(wt("pageBreakBefore")) is not None:
                return False
            if p_pr.find(wt("numPr")) is not None:
                return False
        if paragraph.find(".//" + wt("br")) is not None:
            return False

        return True

    removed = 0
    run: list[ET.Element] = []

    def flush() -> None:
        nonlocal removed
        for extra in run[max_consecutive:]:
            body.remove(extra)
            removed += 1
        run.clear()

    for child in list(body):
        if child.tag in (wt("bookmarkStart"), wt("bookmarkEnd")):
            continue
        if is_collapsible_blank(child):
            run.append(child)
        else:
            flush()
    flush()

    return removed


def _headings_immediately_following_another_heading(
    body: ET.Element,
) -> set[ET.Element]:
    """Identify heading paragraphs that come immediately after another
    heading paragraph in the top-level document flow, with absolutely
    nothing — not even a blank paragraph — between them.

    These don't need a forced page break of their own: two heading titles
    stacked directly on top of each other with nothing to read in between
    just produces an orphan page holding a single line of text. A blank
    paragraph in between is deliberately NOT treated as bridgeable here:
    it usually signals the author's own intentional spacing/separation
    between two unrelated sections (e.g. a title page followed by a
    section heading), and collapsing across it would wrongly merge pages
    that were meant to stay apart.
    """
    result: set[ET.Element] = set()
    prev_heading: ET.Element | None = None

    for child in list(body):
        if child.tag in (wt("bookmarkStart"), wt("bookmarkEnd")):
            # Transparent markers: Word commonly wraps headings in
            # bookmarks, so these should not break true adjacency.
            continue

        if child.tag != wt("p"):
            prev_heading = None
            continue

        is_heading = _is_heading_style(_paragraph_style(child))
        if is_heading:
            if prev_heading is not None:
                result.add(child)
            prev_heading = child
        else:
            prev_heading = None

    return result


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

    headings_after_headings = _headings_immediately_following_another_heading(body)

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

        if paragraph in headings_after_headings:
            # This heading immediately follows another heading with no body
            # content in between (e.g. a section title immediately followed
            # by its first sub-heading). Forcing a new page here would strand
            # the previous heading alone on its own near-empty page, so let
            # this one flow directly after it instead.
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
