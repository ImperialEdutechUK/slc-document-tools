from __future__ import annotations

import copy
from dataclasses import dataclass
from io import BytesIO
import posixpath
import re
from pathlib import PurePosixPath
import zipfile
import xml.etree.ElementTree as ET

from .document_structure import collect_toc_entries_and_bookmark_headings, make_toc_elements
from .heading_styles import apply_heading_style

WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ORNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PRNS = "http://schemas.openxmlformats.org/package/2006/relationships"
XMLNS = "http://www.w3.org/XML/1998/namespace"


def wt(tag: str) -> str:
    return "{" + WNS + "}" + tag


class SimpleEditingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EditableParagraph:
    index: int
    text: str
    style: str
    list_type: str
    page_break_before: bool
    keep_together: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "style": self.style,
            "list_type": self.list_type,
            "page_break_before": self.page_break_before,
            "keep_together": self.keep_together,
        }


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == wt("t"):
            parts.append(node.text or "")
        elif node.tag == wt("tab"):
            parts.append("\t")
        elif node.tag == wt("br") and node.get(wt("type"), "") != "page":
            parts.append("\n")
    return "".join(parts).strip()


def _paragraph_style(paragraph: ET.Element) -> str:
    p_pr = paragraph.find(wt("pPr"))
    p_style = p_pr.find(wt("pStyle")) if p_pr is not None else None
    return p_style.get(wt("val"), "") if p_style is not None else ""


def _ensure_p_pr(paragraph: ET.Element) -> ET.Element:
    p_pr = paragraph.find(wt("pPr"))
    if p_pr is None:
        p_pr = ET.Element(wt("pPr"))
        paragraph.insert(0, p_pr)
    return p_pr


def _set_on_off(p_pr: ET.Element, tag: str, enabled: bool) -> None:
    element = p_pr.find(wt(tag))
    if not enabled:
        if element is not None:
            p_pr.remove(element)
        return

    if element is None:
        element = ET.Element(wt(tag))
    else:
        p_pr.remove(element)

    element.set(wt("val"), "1")

    allowed_before = {wt("pStyle")}
    if tag in {"keepLines", "pageBreakBefore", "widowControl"}:
        allowed_before.add(wt("keepNext"))
    if tag in {"pageBreakBefore", "widowControl"}:
        allowed_before.add(wt("keepLines"))
    if tag == "widowControl":
        allowed_before.update({wt("pageBreakBefore"), wt("framePr")})

    insert_at = 0
    for index, child in enumerate(list(p_pr)):
        if child.tag in allowed_before:
            insert_at = index + 1
    p_pr.insert(insert_at, element)


def _set_paragraph_style(paragraph: ET.Element, style_name: str) -> None:
    p_pr = _ensure_p_pr(paragraph)
    p_style = p_pr.find(wt("pStyle"))
    if p_style is None:
        p_style = ET.Element(wt("pStyle"))
        p_pr.insert(0, p_style)
    p_style.set(wt("val"), style_name)


def _set_list_numbering(paragraph: ET.Element, num_id: str) -> None:
    p_pr = _ensure_p_pr(paragraph)
    p_style = p_pr.find(wt("pStyle"))
    if p_style is None:
        p_style = ET.Element(wt("pStyle"))
        p_pr.insert(0, p_style)
    p_style.set(wt("val"), "ListParagraph")

    old = p_pr.find(wt("numPr"))
    if old is not None:
        p_pr.remove(old)

    num_pr = ET.Element(wt("numPr"))
    ET.SubElement(num_pr, wt("ilvl")).set(wt("val"), "0")
    ET.SubElement(num_pr, wt("numId")).set(wt("val"), str(num_id))

    insert_at = 0
    pagination_tags = {
        wt("pStyle"),
        wt("keepNext"),
        wt("keepLines"),
        wt("pageBreakBefore"),
        wt("widowControl"),
    }
    for index, child in enumerate(list(p_pr)):
        if child.tag in pagination_tags:
            insert_at = index + 1
    p_pr.insert(insert_at, num_pr)


def _clear_list_numbering(paragraph: ET.Element) -> None:
    p_pr = _ensure_p_pr(paragraph)
    old = p_pr.find(wt("numPr"))
    if old is not None:
        p_pr.remove(old)

    p_style = p_pr.find(wt("pStyle"))
    if p_style is not None and p_style.get(wt("val"), "") in {
        "ListParagraph",
        "ListBullet",
        "ListNumber",
    }:
        p_style.set(wt("val"), "Normal")


def _parse_numbering_formats(numbering_root: ET.Element) -> dict[str, str]:
    abstract_formats: dict[str, str] = {}
    for abstract in numbering_root.findall(wt("abstractNum")):
        abstract_id = abstract.get(wt("abstractNumId"), "")
        for level in abstract.findall(wt("lvl")):
            if level.get(wt("ilvl"), "0") != "0":
                continue
            num_fmt = level.find(wt("numFmt"))
            if num_fmt is not None:
                abstract_formats[abstract_id] = num_fmt.get(wt("val"), "")
            break

    result: dict[str, str] = {}
    for num in numbering_root.findall(wt("num")):
        num_id = num.get(wt("numId"), "")
        abstract_ref = num.find(wt("abstractNumId"))
        abstract_id = abstract_ref.get(wt("val"), "") if abstract_ref is not None else ""
        result[num_id] = abstract_formats.get(abstract_id, "")
    return result


def _next_numeric_id(values: list[str], minimum: int = 1) -> str:
    parsed = []
    for value in values:
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    return str(max([minimum - 1] + parsed) + 1)


def _ensure_numbering_format(numbering_root: ET.Element, desired_format: str) -> str:
    formats = _parse_numbering_formats(numbering_root)
    for num_id, fmt in formats.items():
        if fmt == desired_format:
            return num_id

    abstract_ids = [
        element.get(wt("abstractNumId"), "")
        for element in numbering_root.findall(wt("abstractNum"))
    ]
    num_ids = [
        element.get(wt("numId"), "")
        for element in numbering_root.findall(wt("num"))
    ]
    abstract_id = _next_numeric_id(abstract_ids, minimum=1)
    num_id = _next_numeric_id(num_ids, minimum=1)

    abstract = ET.SubElement(numbering_root, wt("abstractNum"))
    abstract.set(wt("abstractNumId"), abstract_id)
    ET.SubElement(abstract, wt("multiLevelType")).set(wt("val"), "singleLevel")
    level = ET.SubElement(abstract, wt("lvl"))
    level.set(wt("ilvl"), "0")
    ET.SubElement(level, wt("start")).set(wt("val"), "1")
    ET.SubElement(level, wt("numFmt")).set(wt("val"), desired_format)
    ET.SubElement(level, wt("lvlText")).set(
        wt("val"), "•" if desired_format == "bullet" else "%1."
    )
    ET.SubElement(level, wt("lvlJc")).set(wt("val"), "left")
    level_p_pr = ET.SubElement(level, wt("pPr"))
    indent = ET.SubElement(level_p_pr, wt("ind"))
    indent.set(wt("left"), "720")
    indent.set(wt("hanging"), "360")

    num = ET.SubElement(numbering_root, wt("num"))
    num.set(wt("numId"), num_id)
    ET.SubElement(num, wt("abstractNumId")).set(wt("val"), abstract_id)
    return num_id


def _is_user_editable(paragraph: ET.Element) -> bool:
    text = _paragraph_text(paragraph)
    if not text:
        return False

    style = _paragraph_style(paragraph)
    if style.startswith("TOC") or style in {"TOC-Heading", "TOCHeading"}:
        return False

    # Cover artwork and positioned text boxes should never appear in the
    # lightweight paragraph editor.
    if paragraph.find(".//" + wt("drawing")) is not None:
        return False
    if paragraph.find(".//" + wt("pict")) is not None:
        return False
    if paragraph.find(".//" + wt("txbxContent")) is not None:
        return False
    if paragraph.find(".//" + wt("instrText")) is not None:
        return False
    return True


def _write_docx_parts(source: zipfile.ZipFile, replacements: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            target.writestr(info, replacements.get(info.filename, source.read(info.filename)))
    return output.getvalue()


def get_editable_paragraphs(docx_bytes: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(BytesIO(docx_bytes), "r") as archive:
            document_xml = archive.read("word/document.xml")
            numbering_xml = (
                archive.read("word/numbering.xml")
                if "word/numbering.xml" in archive.namelist()
                else None
            )
    except (zipfile.BadZipFile, KeyError) as exc:
        raise SimpleEditingError("The formatted DOCX could not be read.") from exc

    document_root = ET.fromstring(document_xml)
    body = document_root.find(wt("body"))
    if body is None:
        raise SimpleEditingError("The document body could not be found.")

    numbering_formats: dict[str, str] = {}
    if numbering_xml:
        numbering_formats = _parse_numbering_formats(ET.fromstring(numbering_xml))

    result: list[dict] = []
    paragraph_index = -1
    for child in list(body):
        if child.tag != wt("p"):
            continue
        paragraph_index += 1
        if not _is_user_editable(child):
            continue

        p_pr = child.find(wt("pPr"))
        num_pr = p_pr.find(wt("numPr")) if p_pr is not None else None
        num_id_element = num_pr.find(wt("numId")) if num_pr is not None else None
        num_id = num_id_element.get(wt("val"), "") if num_id_element is not None else ""
        num_format = numbering_formats.get(num_id, "")
        list_type = (
            "bullet"
            if num_format == "bullet"
            else "number"
            if num_format in {"decimal", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman"}
            else "none"
        )

        result.append(
            EditableParagraph(
                index=paragraph_index,
                text=_paragraph_text(child),
                style=_paragraph_style(child) or "Normal",
                list_type=list_type,
                page_break_before=(
                    p_pr is not None and p_pr.find(wt("pageBreakBefore")) is not None
                ),
                keep_together=(
                    p_pr is not None and p_pr.find(wt("keepLines")) is not None
                ),
            ).to_dict()
        )
    return result


def apply_simple_edits(
    docx_bytes: bytes, paragraph_indices: list[int], action: str
) -> tuple[bytes, int]:
    allowed_actions = {
        "heading1",
        "bullets",
        "numbering",
        "normal",
        "page_break_before",
        "remove_page_break_before",
        "keep_together",
        "allow_split",
        "blank_line_before",
        "blank_line_after",
    }
    if action not in allowed_actions:
        raise SimpleEditingError("Unsupported formatting action.")

    requested = {int(index) for index in paragraph_indices}
    if not requested:
        raise SimpleEditingError("Select at least one paragraph to edit.")

    try:
        source = zipfile.ZipFile(BytesIO(docx_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise SimpleEditingError("The formatted DOCX could not be read.") from exc

    try:
        document_root = ET.fromstring(source.read("word/document.xml"))
        body = document_root.find(wt("body"))
        if body is None:
            raise SimpleEditingError("The document body could not be found.")

        numbering_root = None
        if action in {"bullets", "numbering"}:
            if "word/numbering.xml" not in source.namelist():
                raise SimpleEditingError(
                    "This document does not contain list numbering definitions."
                )
            numbering_root = ET.fromstring(source.read("word/numbering.xml"))
            desired_format = "bullet" if action == "bullets" else "decimal"
            num_id = _ensure_numbering_format(numbering_root, desired_format)
        else:
            num_id = ""

        changed = 0
        paragraph_index = -1
        original_children = list(body)
        for child in original_children:
            if child.tag != wt("p"):
                continue
            paragraph_index += 1
            if paragraph_index not in requested or not _is_user_editable(child):
                continue

            if action == "heading1":
                apply_heading_style(child, "Heading1")
            elif action in {"bullets", "numbering"}:
                _set_list_numbering(child, num_id)
            elif action == "normal":
                _clear_list_numbering(child)
                _set_paragraph_style(child, "Normal")
            elif action == "page_break_before":
                _set_on_off(_ensure_p_pr(child), "pageBreakBefore", True)
            elif action == "remove_page_break_before":
                _set_on_off(_ensure_p_pr(child), "pageBreakBefore", False)
            elif action == "keep_together":
                _set_on_off(_ensure_p_pr(child), "keepLines", True)
            elif action == "allow_split":
                _set_on_off(_ensure_p_pr(child), "keepLines", False)
            elif action in {"blank_line_before", "blank_line_after"}:
                blank = ET.Element(wt("p"))
                current_index = list(body).index(child)
                body.insert(
                    current_index if action == "blank_line_before" else current_index + 1,
                    blank,
                )
            changed += 1

        if not changed:
            raise SimpleEditingError("None of the selected paragraphs could be edited.")

        replacements = {
            "word/document.xml": ET.tostring(
                document_root,
                encoding="UTF-8",
                xml_declaration=True,
                short_empty_elements=True,
            )
        }
        if numbering_root is not None:
            replacements["word/numbering.xml"] = ET.tostring(
                numbering_root,
                encoding="UTF-8",
                xml_declaration=True,
                short_empty_elements=True,
            )
        return _write_docx_parts(source, replacements), changed
    finally:
        source.close()


def _first_run_properties(paragraph: ET.Element) -> ET.Element | None:
    first_run = paragraph.find(".//" + wt("r"))
    if first_run is None:
        return None
    r_pr = first_run.find(wt("rPr"))
    return copy.deepcopy(r_pr) if r_pr is not None else None


def _replace_paragraph_text(paragraph: ET.Element, text: str) -> None:
    run_properties = _first_run_properties(paragraph)

    preserve_tags = {
        wt("pPr"),
        wt("bookmarkStart"),
        wt("bookmarkEnd"),
        wt("permStart"),
        wt("permEnd"),
    }
    for child in list(paragraph):
        if child.tag not in preserve_tags:
            paragraph.remove(child)

    run = ET.Element(wt("r"))
    if run_properties is not None:
        run.append(run_properties)

    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")
    for line_index, line in enumerate(lines):
        if line_index:
            ET.SubElement(run, wt("br"))
        text_element = ET.SubElement(run, wt("t"))
        if line[:1].isspace() or line[-1:].isspace():
            text_element.set("{" + XMLNS + "}space", "preserve")
        text_element.text = line

    paragraph.append(run)


def replace_paragraph_text(
    docx_bytes: bytes, paragraph_index: int, text: str
) -> tuple[bytes, int]:
    try:
        source = zipfile.ZipFile(BytesIO(docx_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise SimpleEditingError("The formatted DOCX could not be read.") from exc

    try:
        document_root = ET.fromstring(source.read("word/document.xml"))
        body = document_root.find(wt("body"))
        if body is None:
            raise SimpleEditingError("The document body could not be found.")

        current_index = -1
        target = None
        for child in list(body):
            if child.tag != wt("p"):
                continue
            current_index += 1
            if current_index == int(paragraph_index):
                target = child
                break

        if target is None or not _is_user_editable(target):
            raise SimpleEditingError("The selected paragraph could not be edited.")

        _replace_paragraph_text(target, text)
        updated_document_xml = ET.tostring(
            document_root,
            encoding="UTF-8",
            xml_declaration=True,
            short_empty_elements=True,
        )
        return _write_docx_parts(source, {"word/document.xml": updated_document_xml}), 1
    finally:
        source.close()


def _footer_cell_text(cell: ET.Element) -> str:
    return "".join(node.text or "" for node in cell.iter(wt("t"))).strip()


def _footer_cell_has_page_field(cell: ET.Element) -> bool:
    return any(
        "PAGE" in (node.text or "").upper()
        for node in cell.iter(wt("instrText"))
    )


def _classify_footer_cell(cell: ET.Element) -> str:
    if _footer_cell_has_page_field(cell):
        return "page"
    text = _footer_cell_text(cell)
    if "©" in text or "SOUTH LONDON COLLEGE" in text.upper():
        return "copyright"
    if text:
        return "course"
    return "empty"


_FOOTER_PAGE_WIDTH_TWIPS = 11906
_FOOTER_PAGE_MARGIN_TWIPS = 1440
_FOOTER_CONTENT_WIDTH_TWIPS = _FOOTER_PAGE_WIDTH_TWIPS - (2 * _FOOTER_PAGE_MARGIN_TWIPS)
# Match the original SLC footer geometry: equal left/right zones around a
# truly centred course-title zone. Keep the original 25% / 50% / 25%
# geometry. LibreOffice can still wrap long text despite w:noWrap, so the
# footer runs also use a small horizontal text-scale safeguard. Odd/even pages
# mirror content, not widths; the visible footer positions remain unchanged.
_FOOTER_PAGE_COL_TWIPS = _FOOTER_CONTENT_WIDTH_TWIPS * 25 // 100
_FOOTER_COPYRIGHT_COL_TWIPS = _FOOTER_PAGE_COL_TWIPS
_FOOTER_CENTER_COL_TWIPS = (
    _FOOTER_CONTENT_WIDTH_TWIPS - _FOOTER_PAGE_COL_TWIPS - _FOOTER_COPYRIGHT_COL_TWIPS
)


_FOOTER_FULL_TITLE_CHAR_LIMIT = 60
_FOOTER_PAGE_LABEL = " | Page"
_FOOTER_LEVEL_RE = re.compile(r"\bLevel\s+(?:\d+(?:\.\d+)?|[IVXLC]+)\b\s*", re.IGNORECASE)
# Manual footer-position controls use small, reversible paragraph indents.
# One step is deliberately subtle so users can visually nudge a footer item
# without changing the branded table geometry or forcing text onto a new line.
_FOOTER_OFFSET_STEP_TWIPS = 25
_FOOTER_OFFSET_MIN_STEPS = -6
_FOOTER_OFFSET_MAX_STEPS = 6


def _footer_display_course_name(course_name: str) -> str:
    title = " ".join(str(course_name or "").split())
    if len(title) <= _FOOTER_FULL_TITLE_CHAR_LIMIT:
        return title
    shortened = _FOOTER_LEVEL_RE.sub("", title, count=1)
    shortened = " ".join(shortened.split())
    return shortened or title


def _footer_course_font_half_points(course_name: str) -> int:
    # Preserve the source footer's 8 pt Garamond size whenever practical.
    # Long titles are handled first by Level X trimming and small horizontal
    # scaling; point-size reduction is only a final fallback.
    length = len(" ".join(str(course_name or "").split()))
    if length <= 64:
        return 16
    if length <= 76:
        return 15
    if length <= 88:
        return 14
    if length <= 100:
        return 13
    if length <= 116:
        return 12
    if length <= 132:
        return 11
    return 10


def _footer_course_width_percent(course_name: str) -> int:
    length = len(" ".join(str(course_name or "").split()))
    if length <= 52:
        return 100
    if length <= 64:
        return 90
    if length <= 88:
        return 88
    return 85


def _footer_display_copyright(copyright_text: str) -> str:
    """Return copyright text that cannot wrap between words.

    Some LibreOffice versions can wrap text inside a Word table cell even
    when ``w:noWrap`` is present. Non-breaking spaces make the one-line rule
    deterministic without changing how the footer looks.
    """
    text = " ".join(str(copyright_text or "").replace("\u00a0", " " ).split())
    return text.replace(" ", "\u00a0")


def _ensure_xml_child(parent: ET.Element, tag: str, *, first: bool = False) -> ET.Element:
    child = parent.find(wt(tag))
    if child is None:
        child = ET.Element(wt(tag))
        if first:
            parent.insert(0, child)
        else:
            parent.append(child)
    return child


def _set_footer_cell_layout(cell: ET.Element, width: int, *, fit_text: bool = False) -> None:
    tc_pr = cell.find(wt("tcPr"))
    if tc_pr is None:
        tc_pr = ET.Element(wt("tcPr"))
        cell.insert(0, tc_pr)

    tc_w = _ensure_xml_child(tc_pr, "tcW", first=True)
    tc_w.set(wt("w"), str(width))
    tc_w.set(wt("type"), "dxa")
    _ensure_xml_child(tc_pr, "noWrap")

    fit = tc_pr.find(wt("tcFitText"))
    if fit_text:
        if fit is None:
            tc_pr.append(ET.Element(wt("tcFitText")))
    elif fit is not None:
        tc_pr.remove(fit)


def _set_footer_course_font(cell: ET.Element, course_text: str) -> None:
    size = str(_footer_course_font_half_points(course_text))
    width_percent = str(_footer_course_width_percent(course_text))
    for run in cell.iter(wt("r")):
        # Only resize/condense runs carrying visible title text.
        if not any((node.text or "") for node in run.findall(wt("t"))):
            continue
        r_pr = run.find(wt("rPr"))
        if r_pr is None:
            r_pr = ET.Element(wt("rPr"))
            run.insert(0, r_pr)
        sz = _ensure_xml_child(r_pr, "sz")
        sz.set(wt("val"), size)
        sz_cs = _ensure_xml_child(r_pr, "szCs")
        sz_cs.set(wt("val"), size)
        width = _ensure_xml_child(r_pr, "w")
        width.set(wt("val"), width_percent)




def _set_run_font_size(run: ET.Element, half_points: str) -> None:
    r_pr = run.find(wt("rPr"))
    if r_pr is None:
        r_pr = ET.Element(wt("rPr"))
        run.insert(0, r_pr)
    sz = _ensure_xml_child(r_pr, "sz")
    sz.set(wt("val"), half_points)
    sz_cs = _ensure_xml_child(r_pr, "szCs")
    sz_cs.set(wt("val"), half_points)


def _set_run_width_percent(run: ET.Element, percent: str) -> None:
    r_pr = run.find(wt("rPr"))
    if r_pr is None:
        r_pr = ET.Element(wt("rPr"))
        run.insert(0, r_pr)
    width = _ensure_xml_child(r_pr, "w")
    width.set(wt("val"), percent)


def _clamp_footer_offset_steps(value: int | float | str | None) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    return max(_FOOTER_OFFSET_MIN_STEPS, min(_FOOTER_OFFSET_MAX_STEPS, parsed))


def _footer_cell_paragraph(cell: ET.Element) -> ET.Element:
    paragraph = cell.find(wt("p"))
    if paragraph is None:
        paragraph = ET.SubElement(cell, wt("p"))
    return paragraph


def _footer_paragraph_alignment(paragraph: ET.Element) -> str:
    p_pr = paragraph.find(wt("pPr"))
    jc = p_pr.find(wt("jc")) if p_pr is not None else None
    return jc.get(wt("val"), "left") if jc is not None else "left"


def _set_footer_horizontal_offset(cell: ET.Element, steps: int) -> None:
    """Nudge one footer item left/right while preserving its cell and style.

    The user-facing value is a small number of steps. Positive values move the
    visible item to the right; negative values move it to the left. Using
    paragraph indents instead of literal spaces keeps PAGE fields intact and
    avoids reintroducing the LibreOffice wrapping problem.
    """
    steps = _clamp_footer_offset_steps(steps)
    paragraph = _footer_cell_paragraph(cell)
    p_pr = paragraph.find(wt("pPr"))
    if p_pr is None:
        p_pr = ET.Element(wt("pPr"))
        paragraph.insert(0, p_pr)

    ind = p_pr.find(wt("ind"))
    if ind is None:
        ind = ET.Element(wt("ind"))
        p_pr.append(ind)

    # Remove only the horizontal indentation attributes managed by this tool;
    # preserve any first-line/hanging values that may exist in unusual source
    # documents.
    for attr in ("left", "right", "start", "end"):
        ind.attrib.pop(wt(attr), None)

    if steps == 0:
        if not ind.attrib:
            p_pr.remove(ind)
        return

    offset = steps * _FOOTER_OFFSET_STEP_TWIPS
    alignment = _footer_paragraph_alignment(paragraph)

    if alignment == "center":
        # A centred paragraph moves by half the left/right indent difference,
        # so apply twice the requested amount on the appropriate side.
        if offset > 0:
            ind.set(wt("left"), str(offset * 2))
        else:
            ind.set(wt("right"), str((-offset) * 2))
    elif alignment == "right":
        # Right-aligned text is positioned from the right edge. A negative
        # right indent moves it outward/right; a positive value moves left.
        ind.set(wt("right"), str(-offset))
    else:
        # Left-aligned text moves directly with its left indent. Word and
        # LibreOffice both accept a small signed value here.
        ind.set(wt("left"), str(offset))


def _get_footer_horizontal_offset(cell: ET.Element) -> int:
    paragraph = cell.find(wt("p"))
    if paragraph is None:
        return 0
    p_pr = paragraph.find(wt("pPr"))
    ind = p_pr.find(wt("ind")) if p_pr is not None else None
    if ind is None:
        return 0

    def _twips(attr: str) -> int:
        try:
            return int(ind.get(wt(attr), "0") or "0")
        except ValueError:
            return 0

    left = _twips("left") or _twips("start")
    right = _twips("right") or _twips("end")
    alignment = _footer_paragraph_alignment(paragraph)
    if alignment == "center":
        twips = (left - right) / 2
    elif alignment == "right":
        twips = -right
    else:
        twips = left
    return _clamp_footer_offset_steps(round(twips / _FOOTER_OFFSET_STEP_TWIPS))


def _set_footer_course_offset(root: ET.Element, steps: int) -> None:
    """Move the centre footer zone without narrowing the course-title cell.

    Paragraph indents make LibreOffice wrap long course names. Instead, keep
    the middle cell at its original 50% width and transfer a very small amount
    of width between the two outer cells. This moves the entire centre zone
    left/right while preserving the one-line course title.
    """
    steps = _clamp_footer_offset_steps(steps)
    shift = steps * _FOOTER_OFFSET_STEP_TWIPS
    for table in root.iter(wt("tbl")):
        row = table.find(wt("tr"))
        if row is None:
            continue
        cells = row.findall(wt("tc"))
        if len(cells) != 3:
            continue
        kinds = [_classify_footer_cell(cell) for cell in cells]
        if kinds[1] != "course" or "page" not in kinds:
            continue

        widths = [
            _FOOTER_PAGE_COL_TWIPS + shift,
            _FOOTER_CENTER_COL_TWIPS,
            _FOOTER_COPYRIGHT_COL_TWIPS - shift,
        ]
        for cell, width in zip(cells, widths):
            tc_pr = cell.find(wt("tcPr"))
            if tc_pr is None:
                tc_pr = ET.Element(wt("tcPr"))
                cell.insert(0, tc_pr)
            tc_w = _ensure_xml_child(tc_pr, "tcW", first=True)
            tc_w.set(wt("w"), str(width))
            tc_w.set(wt("type"), "dxa")

        grid = table.find(wt("tblGrid"))
        if grid is None:
            grid = ET.Element(wt("tblGrid"))
            table.insert(1 if table.find(wt("tblPr")) is not None else 0, grid)
        for child in list(grid):
            grid.remove(child)
        for width in widths:
            ET.SubElement(grid, wt("gridCol")).set(wt("w"), str(width))


def _get_footer_course_offset(root: ET.Element) -> int:
    for table in root.iter(wt("tbl")):
        row = table.find(wt("tr"))
        if row is None:
            continue
        cells = row.findall(wt("tc"))
        if len(cells) != 3:
            continue
        kinds = [_classify_footer_cell(cell) for cell in cells]
        if kinds[1] != "course" or "page" not in kinds:
            continue
        tc_pr = cells[0].find(wt("tcPr"))
        tc_w = tc_pr.find(wt("tcW")) if tc_pr is not None else None
        if tc_w is None:
            return 0
        try:
            left_width = int(tc_w.get(wt("w"), str(_FOOTER_PAGE_COL_TWIPS)))
        except ValueError:
            return 0
        shift = left_width - _FOOTER_PAGE_COL_TWIPS
        return _clamp_footer_offset_steps(round(shift / _FOOTER_OFFSET_STEP_TWIPS))
    return 0


def _normalise_page_number_cell(cell: ET.Element) -> bool:
    """Force the footer page marker to render exactly as ``1 | Page``.

    The PAGE field stays dynamic, so subsequent pages render as ``2 | Page``,
    ``3 | Page`` and so on. Rebuilding the paragraph also upgrades older
    footers where the label appeared before the page number or spacing varied.
    """
    before = ET.tostring(cell, encoding="UTF-8")
    paragraph = cell.find(wt("p"))
    if paragraph is None:
        paragraph = ET.SubElement(cell, wt("p"))

    # Preserve paragraph properties such as Footer style and alignment, but
    # rebuild the visible/field runs in one deterministic order.
    p_pr = paragraph.find(wt("pPr"))
    for child in list(paragraph):
        if child is not p_pr:
            paragraph.remove(child)

    def add_run(size: str = "18") -> ET.Element:
        run = ET.SubElement(paragraph, wt("r"))
        _set_run_font_size(run, size)
        return run

    begin_run = add_run("18")
    begin = ET.SubElement(begin_run, wt("fldChar"))
    begin.set(wt("fldCharType"), "begin")

    instr_run = add_run("18")
    instr = ET.SubElement(instr_run, wt("instrText"))
    instr.set("{" + XMLNS + "}space", "preserve")
    instr.text = r" PAGE \* MERGEFORMAT "

    separate_run = add_run("18")
    separate = ET.SubElement(separate_run, wt("fldChar"))
    separate.set(wt("fldCharType"), "separate")

    result_run = add_run("18")
    result = ET.SubElement(result_run, wt("t"))
    result.text = "1"

    end_run = add_run("18")
    end = ET.SubElement(end_run, wt("fldChar"))
    end.set(wt("fldCharType"), "end")

    label_run = add_run("16")
    label = ET.SubElement(label_run, wt("t"))
    label.set("{" + XMLNS + "}space", "preserve")
    label.text = _FOOTER_PAGE_LABEL

    return before != ET.tostring(cell, encoding="UTF-8")

def _normalise_footer_table_layout(root: ET.Element) -> None:
    """Keep footer edits on one line without changing the SLC footer style.

    The source design uses equal outer zones with the qualification title
    centred on the page. We preserve that geometry, the mirrored odd/even
    placement, the teal rule, and the original font sizing. Only the course
    title may be shortened (Level X removed) or minimally reduced if needed.
    """
    for table in root.iter(wt("tbl")):
        row = table.find(wt("tr"))
        if row is None:
            continue
        cells = row.findall(wt("tc"))
        if len(cells) != 3:
            continue

        kinds = [_classify_footer_cell(cell) for cell in cells]
        if "course" not in kinds or "page" not in kinds:
            continue

        widths: list[int] = []
        for cell, kind in zip(cells, kinds):
            if kind == "page":
                width = _FOOTER_PAGE_COL_TWIPS
                _set_footer_cell_layout(cell, width)
                _normalise_page_number_cell(cell)
            elif kind == "copyright":
                width = _FOOTER_COPYRIGHT_COL_TWIPS
                _set_footer_cell_layout(cell, width)
                current_copyright = _footer_cell_text(cell)
                display_copyright = _footer_display_copyright(current_copyright)
                if display_copyright != current_copyright:
                    _replace_visible_text(cell, display_copyright)
                for run in cell.iter(wt("r")):
                    if any((node.text or "") for node in run.findall(wt("t"))):
                        _set_run_font_size(run, "16")
                        _set_run_width_percent(run, "95")
            else:
                width = _FOOTER_CENTER_COL_TWIPS
                _set_footer_cell_layout(cell, width, fit_text=True)
                current_course_text = _footer_cell_text(cell)
                display_course_text = _footer_display_course_name(current_course_text)
                if display_course_text != current_course_text:
                    _replace_visible_text(cell, display_course_text)
                _set_footer_course_font(cell, display_course_text)
            widths.append(width)

        tbl_pr = table.find(wt("tblPr"))
        if tbl_pr is None:
            tbl_pr = ET.Element(wt("tblPr"))
            table.insert(0, tbl_pr)
        tbl_w = _ensure_xml_child(tbl_pr, "tblW", first=True)
        tbl_w.set(wt("w"), str(_FOOTER_CONTENT_WIDTH_TWIPS))
        tbl_w.set(wt("type"), "dxa")
        layout = _ensure_xml_child(tbl_pr, "tblLayout")
        layout.set(wt("type"), "fixed")

        grid = table.find(wt("tblGrid"))
        if grid is None:
            grid = ET.Element(wt("tblGrid"))
            # tblGrid belongs after tblPr and before the first row.
            insert_at = 1 if table.find(wt("tblPr")) is not None else 0
            table.insert(insert_at, grid)
        for child in list(grid):
            grid.remove(child)
        for width in widths:
            col = ET.SubElement(grid, wt("gridCol"))
            col.set(wt("w"), str(width))


def _replace_visible_text(container: ET.Element, value: str) -> bool:
    text_nodes = list(container.iter(wt("t")))
    if not text_nodes:
        paragraph = container.find(".//" + wt("p"))
        if paragraph is None:
            paragraph = ET.SubElement(container, wt("p"))
        run = ET.SubElement(paragraph, wt("r"))
        text_node = ET.SubElement(run, wt("t"))
        text_node.text = value
        return True

    changed = any((node.text or "") for node in text_nodes[1:]) or (text_nodes[0].text or "") != value
    text_nodes[0].text = value
    if value[:1].isspace() or value[-1:].isspace():
        text_nodes[0].set("{" + XMLNS + "}space", "preserve")
    for node in text_nodes[1:]:
        node.text = ""
    return changed


def _replace_page_label(cell: ET.Element, value: str) -> bool:
    text_nodes = list(cell.iter(wt("t")))
    if not text_nodes:
        return False

    # The generated page cell stores the cached page number first and the
    # editable label (for example `` | Page``) last. Selecting the final text
    # node also lets a user intentionally clear the label and edit it again.
    node = text_nodes[-1]
    changed = (node.text or "") != value
    node.text = value
    if value[:1].isspace() or value[-1:].isspace():
        node.set("{" + XMLNS + "}space", "preserve")
    return changed


def _active_footer_names(source: zipfile.ZipFile) -> list[str]:
    fallback = sorted(
        (
            name
            for name in source.namelist()
            if re.fullmatch(r"word/[^/]*footer[^/]*\.xml", name, flags=re.IGNORECASE)
        ),
        key=lambda name: (0 if "slc_footer" in name.lower() else 1, name.lower()),
    )

    try:
        document_root = ET.fromstring(source.read("word/document.xml"))
        rels_root = ET.fromstring(source.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError):
        return fallback

    referenced_ids = {
        element.get("{" + ORNS + "}id", "")
        for element in document_root.iter(wt("footerReference"))
        if element.get("{" + ORNS + "}id", "")
    }
    if not referenced_ids:
        return fallback

    resolved: list[str] = []
    for rel in rels_root.findall("{" + PRNS + "}Relationship"):
        if rel.get("Id", "") not in referenced_ids:
            continue
        if not rel.get("Type", "").endswith("/footer"):
            continue
        target = rel.get("Target", "")
        if not target:
            continue
        part = posixpath.normpath(
            str(PurePosixPath("word") / PurePosixPath(target))
        )
        if part in source.namelist() and part not in resolved:
            resolved.append(part)

    if not resolved:
        return fallback
    return sorted(
        resolved,
        key=lambda name: (0 if "slc_footer" in name.lower() else 1, name.lower()),
    )


def get_footer_settings(docx_bytes: bytes) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(docx_bytes), "r") as archive:
            footer_names = _active_footer_names(archive)
            if not footer_names:
                return {
                    "course_text": "",
                    "copyright_text": "",
                    "page_label": _FOOTER_PAGE_LABEL,
                    "footer_parts": 0,
                    "page_offset": 0,
                    "course_offset": 0,
                    "copyright_offset": 0,
                }

            course_text = ""
            copyright_text = ""
            page_label = _FOOTER_PAGE_LABEL
            offsets: dict[str, int | None] = {
                "page": None,
                "course": None,
                "copyright": None,
            }
            for name in footer_names:
                root = ET.fromstring(archive.read(name))
                if offsets["course"] is None:
                    offsets["course"] = _get_footer_course_offset(root)
                for cell in root.iter(wt("tc")):
                    kind = _classify_footer_cell(cell)
                    if kind == "course" and not course_text:
                        course_text = _footer_cell_text(cell)
                    elif kind == "copyright" and not copyright_text:
                        copyright_text = _footer_cell_text(cell).replace("\u00a0", " ")
                    elif kind == "page":
                        # Page-number format is fixed to: 1 | Page, 2 | Page, ...
                        page_label = _FOOTER_PAGE_LABEL
                    if kind in {"page", "copyright"} and offsets[kind] is None:
                        offsets[kind] = _get_footer_horizontal_offset(cell)

            return {
                "course_text": course_text,
                "copyright_text": copyright_text,
                "page_label": page_label,
                "footer_parts": len(footer_names),
                "page_offset": int(offsets["page"] or 0),
                "course_offset": int(offsets["course"] or 0),
                "copyright_offset": int(offsets["copyright"] or 0),
            }
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise SimpleEditingError("The document footer could not be read.") from exc


def edit_footer(
    docx_bytes: bytes,
    *,
    course_text: str,
    copyright_text: str,
    page_label: str,
    page_offset: int = 0,
    course_offset: int = 0,
    copyright_offset: int = 0,
) -> tuple[bytes, int]:
    try:
        source = zipfile.ZipFile(BytesIO(docx_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise SimpleEditingError("The formatted DOCX could not be read.") from exc

    try:
        footer_names = _active_footer_names(source)
        if not footer_names:
            raise SimpleEditingError("No editable footer was found in this document.")

        replacements: dict[str, bytes] = {}
        changed_parts = 0
        for name in footer_names:
            root = ET.fromstring(source.read(name))
            before_xml = ET.tostring(root, encoding="UTF-8")
            changed = False
            for cell in root.iter(wt("tc")):
                kind = _classify_footer_cell(cell)
                if kind == "course":
                    display_course_text = _footer_display_course_name(course_text)
                    changed = _replace_visible_text(cell, display_course_text) or changed
                elif kind == "copyright":
                    changed = _replace_visible_text(cell, copyright_text) or changed
                elif kind == "page":
                    # The page marker is a fixed brand format: 1 | Page.
                    # _normalise_footer_table_layout rebuilds this cell below.
                    pass

            # Re-apply the branded footer geometry after an edit. This keeps
            # the original visual style while enforcing the single-line rule.
            _normalise_footer_table_layout(root)

            # Apply user-controlled horizontal spacing only after the branded
            # footer has been normalised. This preserves the 25/50/25 table,
            # teal rule, one-line safeguards, font settings and odd/even
            # mirroring while allowing small visual position corrections.
            _set_footer_course_offset(root, course_offset)
            role_offsets = {
                "page": _clamp_footer_offset_steps(page_offset),
                "copyright": _clamp_footer_offset_steps(copyright_offset),
            }
            for cell in root.iter(wt("tc")):
                kind = _classify_footer_cell(cell)
                if kind in role_offsets:
                    _set_footer_horizontal_offset(cell, role_offsets[kind])

            after_xml = ET.tostring(root, encoding="UTF-8")
            changed = changed or before_xml != after_xml

            if changed:
                changed_parts += 1
                replacements[name] = ET.tostring(
                    root,
                    encoding="UTF-8",
                    xml_declaration=True,
                    short_empty_elements=True,
                )

        if not changed_parts:
            # Saving the same values is still a valid edit request. Keep the
            # document unchanged rather than reporting a misleading failure.
            return docx_bytes, 0
        return _write_docx_parts(source, replacements), changed_parts
    except ET.ParseError as exc:
        raise SimpleEditingError("The document footer could not be edited.") from exc
    finally:
        source.close()


def _remove_existing_toc_bookmarks(body: ET.Element) -> None:
    toc_ids: set[str] = set()
    for paragraph in body.iter(wt("p")):
        for bookmark in list(paragraph.findall(wt("bookmarkStart"))):
            name = bookmark.get(wt("name"), "")
            if name.startswith("_Toc"):
                toc_ids.add(bookmark.get(wt("id"), ""))
                paragraph.remove(bookmark)
        for bookmark in list(paragraph.findall(wt("bookmarkEnd"))):
            if bookmark.get(wt("id"), "") in toc_ids:
                paragraph.remove(bookmark)


def _paragraph_has_page_break(paragraph: ET.Element) -> bool:
    return any(
        node.get(wt("type"), "") == "page"
        for node in paragraph.iter(wt("br"))
    )


def _find_toc_span(body: ET.Element) -> tuple[int, int]:
    children = list(body)
    start = -1
    for index, child in enumerate(children):
        if child.tag != wt("p"):
            continue
        style = _paragraph_style(child)
        text = _paragraph_text(child).strip().lower()
        if style in {"TOCHeading", "TOC-Heading"} or text == "table of contents":
            start = index
            break

    if start < 0:
        raise SimpleEditingError(
            "The document does not contain a generated Table of Contents."
        )

    # The generated TOC is a field block. Stop at its fldChar=end, then include
    # the immediately following page-break paragraph when present. This avoids
    # deleting real content if a user has removed the TOC page break manually.
    end = start
    field_finished = False
    for index in range(start + 1, len(children)):
        child = children[index]
        if child.tag != wt("p"):
            if field_finished:
                break
            end = index
            continue

        fld_end = any(
            node.get(wt("fldCharType"), "") == "end"
            for node in child.iter(wt("fldChar"))
        )
        if fld_end:
            end = index
            field_finished = True
            continue

        if field_finished:
            if _paragraph_has_page_break(child):
                end = index
            break

        # TOC result paragraphs use TOC styles. If real content starts before
        # a closing field marker, stop instead of treating it as TOC content.
        style = _paragraph_style(child)
        if style and not style.startswith("TOC"):
            break
        end = index

    return start, end


def _settings_with_update_fields(settings_xml: bytes) -> bytes:
    root = ET.fromstring(settings_xml)
    update_fields = root.find(wt("updateFields"))
    if update_fields is None:
        update_fields = ET.SubElement(root, wt("updateFields"))
    update_fields.set(wt("val"), "true")
    return ET.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def regenerate_toc(docx_bytes: bytes) -> tuple[bytes, int]:
    try:
        source = zipfile.ZipFile(BytesIO(docx_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise SimpleEditingError("The formatted DOCX could not be read.") from exc

    try:
        document_root = ET.fromstring(source.read("word/document.xml"))
        body = document_root.find(wt("body"))
        if body is None:
            raise SimpleEditingError("The document body could not be found.")

        start, end = _find_toc_span(body)
        _remove_existing_toc_bookmarks(body)

        # Remove the old cached TOC and its following page break, then rebuild
        # it from the document's current Heading 1-3 text and page-break rules.
        for child in list(body)[start : end + 1]:
            body.remove(child)

        entries = collect_toc_entries_and_bookmark_headings(body, start_page=2)
        toc_elements = make_toc_elements(entries)
        for offset, element in enumerate(toc_elements):
            body.insert(start + offset, element)

        replacements = {
            "word/document.xml": ET.tostring(
                document_root,
                encoding="UTF-8",
                xml_declaration=True,
                short_empty_elements=True,
            )
        }
        if "word/settings.xml" in source.namelist():
            replacements["word/settings.xml"] = _settings_with_update_fields(
                source.read("word/settings.xml")
            )

        return _write_docx_parts(source, replacements), len(entries)
    except ET.ParseError as exc:
        raise SimpleEditingError("The Table of Contents could not be updated.") from exc
    finally:
        source.close()
