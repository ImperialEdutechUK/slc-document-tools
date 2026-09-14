import xml.etree.ElementTree as ET

from app.formatter.engine import (
    FOOTER_CENTER_COL_TWIPS,
    FOOTER_COPYRIGHT_COL_TWIPS,
    FOOTER_PAGE_COL_TWIPS,
    make_footer_xml,
)

WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def wt(tag: str) -> str:
    return "{" + WNS + "}" + tag


def _cell_text(cell: ET.Element) -> str:
    return "".join(node.text or "" for node in cell.iter(wt("t")))


def _visible_run_sizes(cell: ET.Element) -> list[str]:
    sizes: list[str] = []
    for run in cell.iter(wt("r")):
        if not any((node.text or "") for node in run.findall(wt("t"))):
            continue
        r_pr = run.find(wt("rPr"))
        if r_pr is None:
            continue
        size = r_pr.find(wt("sz"))
        if size is not None and size.get(wt("val")):
            sizes.append(size.get(wt("val")))
    return sizes


def test_long_course_name_footer_stays_single_line_without_changing_brand_geometry():
    title = "Level 5 Extended Diploma in Education and Training Management (RQF)"
    root = ET.fromstring(make_footer_xml(title, page_on_right=False))

    table = root.find(wt("tbl"))
    assert table is not None
    row = table.find(wt("tr"))
    assert row is not None
    cells = row.findall(wt("tc"))
    assert len(cells) == 3

    # The original SLC footer is geometrically centred: equal outer zones
    # around a 50% centre zone. This must not be changed just to make long
    # text fit.
    widths = [int(cell.find(wt("tcPr")).find(wt("tcW")).get(wt("w"))) for cell in cells]
    assert widths == [
        FOOTER_PAGE_COL_TWIPS,
        FOOTER_CENTER_COL_TWIPS,
        FOOTER_COPYRIGHT_COL_TWIPS,
    ]
    assert FOOTER_PAGE_COL_TWIPS == FOOTER_COPYRIGHT_COL_TWIPS

    copyright_cell = cells[2]
    copyright_text = _cell_text(copyright_cell)
    assert copyright_text.replace("\u00a0", " ") == "© South London College Ltd"
    assert " " not in copyright_text  # internal spaces are non-breaking
    copyright_pr = copyright_cell.find(wt("tcPr"))
    assert copyright_pr is not None
    assert copyright_pr.find(wt("noWrap")) is not None
    # Keep the original visible style: 8 pt copyright, no horizontal squeeze.
    assert _visible_run_sizes(copyright_cell) == ["16"]
    assert copyright_pr.find(wt("tcFitText")) is None

    course_cell = cells[1]
    # Long footer titles drop only the qualification level token rather than
    # wrapping or changing the footer design.
    assert _cell_text(course_cell) == "Extended Diploma in Education and Training Management (RQF)"
    assert "Level 5" not in _cell_text(course_cell)
    tc_pr = course_cell.find(wt("tcPr"))
    assert tc_pr is not None
    assert tc_pr.find(wt("noWrap")) is not None
    assert tc_pr.find(wt("tcFitText")) is not None
    # This exact title still keeps the original 8 pt footer font after trim.
    assert _visible_run_sizes(course_cell) == ["16"]


def test_footer_brand_rule_and_text_sizes_match_reference_style():
    root = ET.fromstring(make_footer_xml("Quality Assurance of Assessment Processes and Practice", page_on_right=False))
    row = root.find(wt("tbl")).find(wt("tr"))
    cells = row.findall(wt("tc"))

    for cell in cells:
        tc_pr = cell.find(wt("tcPr"))
        borders = tc_pr.find(wt("tcBorders"))
        assert borders is not None
        top = borders.find(wt("top"))
        assert top is not None
        assert top.get(wt("val")) == "single"
        assert top.get(wt("sz")) == "24"  # 3 pt teal rule
        assert top.get(wt("color")) == "1A99A0"

        paragraph = cell.find(wt("p"))
        p_pr = paragraph.find(wt("pPr"))
        style = p_pr.find(wt("pStyle"))
        assert style.get(wt("val")) == "Footer"

    page_cell, course_cell, copyright_cell = cells
    page_sizes = _visible_run_sizes(page_cell)
    # Cached PAGE result is 9 pt; the visible " | Page" suffix is 8 pt.
    assert "18" in page_sizes
    assert "16" in page_sizes
    assert _visible_run_sizes(course_cell) == ["16"]
    assert _visible_run_sizes(copyright_cell) == ["16"]


def test_footer_column_order_reverses_outer_zones_on_even_pages():
    root = ET.fromstring(make_footer_xml("Short Course", page_on_right=True))
    table = root.find(wt("tbl"))
    row = table.find(wt("tr"))
    cells = row.findall(wt("tc"))

    widths = [int(cell.find(wt("tcPr")).find(wt("tcW")).get(wt("w"))) for cell in cells]
    assert widths == [
        FOOTER_COPYRIGHT_COL_TWIPS,
        FOOTER_CENTER_COL_TWIPS,
        FOOTER_PAGE_COL_TWIPS,
    ]
    assert "South London College" in _cell_text(cells[0]).replace("\u00a0", " ")
    assert "Short Course" in _cell_text(cells[1])

    # Odd/even mirroring changes only the outer content order. The centre
    # remains exactly centred because the two outer widths are identical.
    assert FOOTER_COPYRIGHT_COL_TWIPS == FOOTER_PAGE_COL_TWIPS


def test_short_course_name_keeps_level_text():
    title = "Level 3 Award in Education and Training"
    root = ET.fromstring(make_footer_xml(title, page_on_right=False))
    table = root.find(wt("tbl"))
    row = table.find(wt("tr"))
    cells = row.findall(wt("tc"))
    assert _cell_text(cells[1]) == title
    assert _visible_run_sizes(cells[1]) == ["16"]


def test_page_number_format_is_number_then_pipe_then_page():
    root = ET.fromstring(make_footer_xml("Short Course", page_on_right=False))
    table = root.find(wt("tbl"))
    row = table.find(wt("tr"))
    cells = row.findall(wt("tc"))
    page_cell = cells[0]
    assert _cell_text(page_cell) == "1 | Page"
    instr = "".join(node.text or "" for node in page_cell.iter(wt("instrText")))
    assert "PAGE" in instr.upper()
