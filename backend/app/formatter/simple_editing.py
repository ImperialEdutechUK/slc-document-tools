from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import zipfile
import xml.etree.ElementTree as ET

WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


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
    return "".join(node.text or "" for node in paragraph.iter(wt("t"))).strip()


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


def get_editable_paragraphs(docx_bytes: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(BytesIO(docx_bytes), "r") as archive:
            document_xml = archive.read("word/document.xml")
            numbering_xml = archive.read("word/numbering.xml") if "word/numbering.xml" in archive.namelist() else None
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


def apply_simple_edits(docx_bytes: bytes, paragraph_indices: list[int], action: str) -> tuple[bytes, int]:
    allowed_actions = {
        "bullets",
        "numbering",
        "normal",
        "page_break_before",
        "remove_page_break_before",
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
        document_xml = source.read("word/document.xml")
        document_root = ET.fromstring(document_xml)
        body = document_root.find(wt("body"))
        if body is None:
            raise SimpleEditingError("The document body could not be found.")

        numbering_root = None
        numbering_bytes = None
        if action in {"bullets", "numbering"}:
            if "word/numbering.xml" not in source.namelist():
                raise SimpleEditingError("This document does not contain list numbering definitions.")
            numbering_bytes = source.read("word/numbering.xml")
            numbering_root = ET.fromstring(numbering_bytes)
            desired_format = "bullet" if action == "bullets" else "decimal"
            num_id = _ensure_numbering_format(numbering_root, desired_format)
        else:
            num_id = ""

        changed = 0
        paragraph_index = -1
        for child in list(body):
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
            changed += 1

        if not changed:
            raise SimpleEditingError("None of the selected paragraphs could be edited.")

        updated_document_xml = ET.tostring(
            document_root, encoding="UTF-8", xml_declaration=True, short_empty_elements=True
        )
        updated_numbering_xml = None
        if numbering_root is not None:
            updated_numbering_xml = ET.tostring(
                numbering_root, encoding="UTF-8", xml_declaration=True, short_empty_elements=True
            )

        output = BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                if info.filename == "word/document.xml":
                    payload = updated_document_xml
                elif info.filename == "word/numbering.xml" and updated_numbering_xml is not None:
                    payload = updated_numbering_xml
                else:
                    payload = source.read(info.filename)
                target.writestr(info, payload)
        return output.getvalue(), changed
    finally:
        source.close()
