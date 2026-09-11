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

            if action in {"bullets", "numbering"}:
                _set_list_numbering(child, num_id)
            elif action == "normal":
                _clear_list_numbering(child)
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
                    "page_label": " | Page",
                    "footer_parts": 0,
                }

            course_text = ""
            copyright_text = ""
            page_label = " | Page"
            for name in footer_names:
                root = ET.fromstring(archive.read(name))
                for cell in root.iter(wt("tc")):
                    kind = _classify_footer_cell(cell)
                    if kind == "course" and not course_text:
                        course_text = _footer_cell_text(cell)
                    elif kind == "copyright" and not copyright_text:
                        copyright_text = _footer_cell_text(cell)
                    elif kind == "page":
                        text_nodes = list(cell.iter(wt("t")))
                        if text_nodes:
                            page_label = text_nodes[-1].text or ""

            return {
                "course_text": course_text,
                "copyright_text": copyright_text,
                "page_label": page_label,
                "footer_parts": len(footer_names),
            }
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise SimpleEditingError("The document footer could not be read.") from exc


def edit_footer(
    docx_bytes: bytes,
    *,
    course_text: str,
    copyright_text: str,
    page_label: str,
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
            changed = False
            for cell in root.iter(wt("tc")):
                kind = _classify_footer_cell(cell)
                if kind == "course":
                    changed = _replace_visible_text(cell, course_text) or changed
                elif kind == "copyright":
                    changed = _replace_visible_text(cell, copyright_text) or changed
                elif kind == "page":
                    changed = _replace_page_label(cell, page_label) or changed

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
