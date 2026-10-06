"""PDF page inspection and page-removal helpers for the SLC document tool."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable

import pymupdf as fitz
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .cover_layout import detect_teal_band_top_fraction


class PdfEditingError(ValueError):
    """Raised when a PDF cannot be safely processed."""


def _read_pdf(pdf_bytes: bytes) -> PdfReader:
    """Open a readable, unencrypted PDF and return its reader."""
    if not pdf_bytes:
        raise PdfEditingError("The uploaded PDF is empty.")

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception as exc:  # pypdf exposes several parser-specific exceptions
        raise PdfEditingError("The uploaded file is not a readable PDF.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise PdfEditingError(
                "Password-protected PDFs are not supported. Remove the password and upload the file again."
            ) from exc
        if not unlocked:
            raise PdfEditingError(
                "Password-protected PDFs are not supported. Remove the password and upload the file again."
            )

    if len(reader.pages) < 1:
        raise PdfEditingError("The uploaded PDF does not contain any pages.")

    return reader


def get_pdf_page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in a readable, unencrypted PDF."""
    return len(_read_pdf(pdf_bytes).pages)


def _copy_metadata(reader: PdfReader, writer: PdfWriter) -> None:
    """Copy safe metadata fields from one PDF into another."""
    if not reader.metadata:
        return

    metadata = {
        str(key): str(value)
        for key, value in reader.metadata.items()
        if key and value is not None
    }
    if metadata:
        writer.add_metadata(metadata)


def remove_pdf_pages(pdf_bytes: bytes, pages_to_remove: Iterable[int]) -> bytes:
    """Remove one-based page numbers and return the updated PDF bytes."""
    reader = _read_pdf(pdf_bytes)
    page_count = len(reader.pages)

    try:
        selected = {int(page) for page in pages_to_remove}
    except (TypeError, ValueError) as exc:
        raise PdfEditingError("Page selections must be whole page numbers.") from exc

    if not selected:
        raise PdfEditingError("Select at least one page to remove.")

    invalid = sorted(page for page in selected if page < 1 or page > page_count)
    if invalid:
        invalid_text = ", ".join(str(page) for page in invalid)
        raise PdfEditingError(f"These page numbers are outside the PDF: {invalid_text}.")

    if len(selected) == page_count:
        raise PdfEditingError("At least one page must remain in the updated PDF.")

    writer = PdfWriter()

    for page_number, page in enumerate(reader.pages, start=1):
        if page_number not in selected:
            writer.add_page(page)

    _copy_metadata(reader, writer)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _cover_page_from_upload(cover_bytes: bytes, filename: str | None):
    """Return the first page of an uploaded PDF/image as a pypdf PageObject."""
    if not cover_bytes:
        raise PdfEditingError("The replacement cover file is empty.")

    suffix = Path(filename or "").suffix.lower()

    if suffix == ".pdf":
        cover_reader = _read_pdf(cover_bytes)
        return cover_reader.pages[0]

    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise PdfEditingError("The replacement cover must be a PDF, JPG, PNG, or WebP file.")

    try:
        with Image.open(BytesIO(cover_bytes)) as image:
            converted = image.convert("RGB")
            image_pdf = BytesIO()
            converted.save(image_pdf, format="PDF", resolution=72.0)
    except Exception as exc:
        raise PdfEditingError("The replacement cover image could not be read.") from exc

    return _read_pdf(image_pdf.getvalue()).pages[0]


def replace_pdf_cover(
    pdf_bytes: bytes,
    cover_bytes: bytes,
    cover_filename: str | None,
) -> bytes:
    """Replace page 1 with the supplied PDF/image cover and keep all other pages."""
    reader = _read_pdf(pdf_bytes)
    replacement = _cover_page_from_upload(cover_bytes, cover_filename)

    target_width = float(reader.pages[0].mediabox.width)
    target_height = float(reader.pages[0].mediabox.height)

    try:
        replacement.scale_to(target_width, target_height)
    except Exception as exc:
        raise PdfEditingError("The replacement cover could not be resized to the original page size.") from exc

    writer = PdfWriter()
    writer.add_page(replacement)
    for page in reader.pages[1:]:
        writer.add_page(page)

    _copy_metadata(reader, writer)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _span_style_for_rect(page: fitz.Page, rect: fitz.Rect) -> dict:
    """Pick the text style of the span that overlaps a searched text rectangle."""
    best: dict | None = None
    best_area = 0.0

    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        blocks = []

    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                try:
                    span_rect = fitz.Rect(span.get("bbox"))
                    overlap = span_rect & rect
                    area = max(0.0, overlap.width) * max(0.0, overlap.height)
                except Exception:
                    continue
                if area > best_area:
                    best = span
                    best_area = area

    if not best:
        return {"size": 11.0, "color": (0.0, 0.0, 0.0), "font": ""}

    raw_color = int(best.get("color", 0) or 0)
    rgb = (
        ((raw_color >> 16) & 255) / 255.0,
        ((raw_color >> 8) & 255) / 255.0,
        (raw_color & 255) / 255.0,
    )
    return {
        "size": max(5.0, float(best.get("size", 11.0) or 11.0)),
        "color": rgb,
        "font": str(best.get("font", "") or ""),
    }


def _garamond_fontfile(existing_font: str) -> str | None:
    """Use the bundled Garamond family when available, matching basic style."""
    fonts_dir = Path(__file__).resolve().parents[2] / "fonts"
    existing_lower = existing_font.lower()

    if "garamond" not in existing_lower:
        return None

    candidates: list[str]
    if "bold" in existing_lower:
        candidates = ["GARABD.TTF", "GARA.TTF"]
    elif "italic" in existing_lower or "oblique" in existing_lower:
        candidates = ["GARAIT.TTF", "GARA.TTF"]
    else:
        candidates = ["GARA.TTF"]

    for filename in candidates:
        path = fonts_dir / filename
        if path.exists():
            return str(path)
    return None


def _builtin_pdf_font(existing_font: str) -> str:
    """Map common embedded PDF font names to PyMuPDF's Base-14 aliases."""
    name = existing_font.lower()
    bold = "bold" in name
    italic = "italic" in name or "oblique" in name

    if "times" in name:
        if bold and italic:
            return "tibi"
        if bold:
            return "tibo"
        if italic:
            return "tiit"
        return "tiro"

    if "courier" in name:
        if bold and italic:
            return "cobi"
        if bold:
            return "cobo"
        if italic:
            return "coit"
        return "cour"

    if bold and italic:
        return "hebi"
    if bold:
        return "hebo"
    if italic:
        return "heit"
    return "helv"


def replace_pdf_text(
    pdf_bytes: bytes,
    replacements: Iterable[tuple[str, str]],
) -> tuple[bytes, list[dict]]:
    """
    Replace exact visible text matches while preserving the page artwork.

    Text is removed with text-only redaction so images and vector backgrounds are
    retained. Replacement text is drawn into the same bounding box using the
    detected size/colour and the bundled Garamond family when available.
    """
    pairs: list[tuple[str, str]] = []
    for find_text, replace_text in replacements:
        find_text = str(find_text or "")
        replace_text = str(replace_text or "")
        if not find_text.strip():
            continue
        pairs.append((find_text, replace_text))

    if not pairs:
        raise PdfEditingError("Add at least one non-empty text value to find.")
    if len(pairs) > 50:
        raise PdfEditingError("A maximum of 50 text replacements can be applied at once.")

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfEditingError("The uploaded file is not a readable PDF.") from exc

    if document.is_encrypted:
        document.close()
        raise PdfEditingError(
            "Password-protected PDFs are not supported. Remove the password and upload the file again."
        )

    report: list[dict] = []

    try:
        for find_text, replace_text in pairs:
            count = 0

            for page in document:
                matches = page.search_for(find_text)
                if not matches:
                    continue

                pending: list[tuple[fitz.Rect, dict]] = []
                for match in matches:
                    rect = fitz.Rect(match)
                    pending.append((rect, _span_style_for_rect(page, rect)))
                    page.add_redact_annot(rect, fill=None, cross_out=False)

                # Remove text only. Preserve images and vector graphics beneath it.
                page.apply_redactions(images=0, graphics=0, text=0)

                for rect, style in pending:
                    if replace_text:
                        fontfile = _garamond_fontfile(style["font"])
                        fontname = (
                            "slc_garamond"
                            if fontfile
                            else _builtin_pdf_font(style["font"])
                        )
                        fontsize = float(style["size"])

                        # Give the replacement a little vertical breathing room but
                        # keep it within the original text area to avoid collisions.
                        text_rect = fitz.Rect(
                            rect.x0,
                            max(page.rect.y0, rect.y0 - 1.5),
                            rect.x1,
                            min(page.rect.y1, rect.y1 + 2.5),
                        )

                        # If the new text is wider, shrink it gradually so the page
                        # layout is not pushed into neighbouring content.
                        test_font = None
                        try:
                            if fontfile:
                                test_font = fitz.Font(fontfile=fontfile)
                            else:
                                test_font = fitz.Font(fontname)
                            width = test_font.text_length(replace_text, fontsize=fontsize)
                            if width > text_rect.width and width > 0:
                                fontsize = max(5.0, fontsize * (text_rect.width / width) * 0.96)
                        except Exception:
                            pass

                        remaining = page.insert_textbox(
                            text_rect,
                            replace_text,
                            fontname=fontname,
                            fontfile=fontfile,
                            fontsize=fontsize,
                            color=style["color"],
                            align=0,
                            overlay=True,
                        )

                        # A negative return means the text did not fit. Retry once
                        # with the minimum practical size before leaving it blank.
                        if remaining < 0 and fontsize > 5.0:
                            page.insert_textbox(
                                text_rect,
                                replace_text,
                                fontname=fontname,
                                fontfile=fontfile,
                                fontsize=5.0,
                                color=style["color"],
                                align=0,
                                overlay=True,
                            )

                    count += 1

            report.append({"find": find_text, "replace": replace_text, "matches": count})

        output = document.tobytes(garbage=4, deflate=True)
    except Exception as exc:
        raise PdfEditingError("The PDF text could not be replaced safely.") from exc
    finally:
        document.close()

    return output, report



def replace_pdf_cover_with_three_lines(
    pdf_bytes: bytes,
    cover_bytes: bytes,
    cover_filename: str | None,
    awarding_body: str,
    course_name: str,
    unit_name: str,
) -> tuple[bytes, dict]:
    """Replace page 1 and place three equal-size text lines in the teal band.

    The three values always use the same font size. The shared size starts at
    24 pt and, when needed, is reduced for all three lines together so the
    longest line fits safely within the page. The text is left-aligned inside
    the lower SLC teal/green cover band, matching the reference cover style.
    Pages 2 onward are copied unchanged from the source PDF.
    """
    _read_pdf(pdf_bytes)

    values = [
        (awarding_body or "").strip(),
        (course_name or "").strip(),
        (unit_name or "").strip(),
    ]
    if not all(values):
        raise PdfEditingError(
            "Awarding body name, course name and unit name are all required."
        )
    if not cover_bytes:
        raise PdfEditingError("Upload a replacement cover image.")

    suffix = Path(cover_filename or "").suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}:
        raise PdfEditingError(
            "The replacement cover must be a PDF, JPG, PNG, or WebP file."
        )

    source = None
    cover_doc = None
    output = None
    try:
        source = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_rect = source[0].rect

        output = fitz.open()
        cover_page = output.new_page(width=page_rect.width, height=page_rect.height)
        full_rect = fitz.Rect(0, 0, page_rect.width, page_rect.height)

        # Keep a raster copy of the replacement artwork so we can detect the
        # SLC teal/green caption band and place the three text lines inside it.
        cover_image_for_band_detection = None

        if suffix == ".pdf":
            cover_doc = fitz.open(stream=cover_bytes, filetype="pdf")
            if cover_doc.page_count < 1:
                raise PdfEditingError("The replacement cover PDF does not contain a page.")
            cover_page.show_pdf_page(full_rect, cover_doc, 0, keep_proportion=False)

            # Render the first cover page only for band detection. The actual
            # PDF artwork is still inserted vector-for-vector above.
            detect_page = cover_doc[0]
            detect_pixmap = detect_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            cover_image_for_band_detection = detect_pixmap.tobytes("png")
        else:
            # Validate through Pillow first so unsupported/corrupt image files
            # produce a clear error instead of a low-level PDF exception.
            try:
                with Image.open(BytesIO(cover_bytes)) as image:
                    converted = image.convert("RGB")
                    image_buffer = BytesIO()
                    converted.save(image_buffer, format="PNG")
                    cover_image = image_buffer.getvalue()
                    cover_image_for_band_detection = cover_image
            except Exception as exc:
                raise PdfEditingError("The replacement cover image could not be read.") from exc
            cover_page.insert_image(full_rect, stream=cover_image, keep_proportion=False)

        font_path = Path(__file__).resolve().parents[2] / "fonts" / "GARA.TTF"
        font_name = "garamond_cover"
        font = None
        if font_path.exists():
            cover_page.insert_font(fontname=font_name, fontfile=str(font_path))
            font = fitz.Font(fontfile=str(font_path))
        else:
            font_name = "helv"
            font = fitz.Font(fontname="helv")

        # Put the text inside the lower SLC teal/green caption band, matching
        # the supplied reference PDF. If the band cannot be detected reliably,
        # use the lower 25% of the page as the safe fallback caption area.
        detected_band_top = (
            detect_teal_band_top_fraction(cover_image_for_band_detection)
            if cover_image_for_band_detection
            else None
        )
        band_top_fraction = detected_band_top if detected_band_top is not None else 0.75
        band_top_fraction = min(max(band_top_fraction, 0.58), 0.84)

        band_top = page_rect.height * band_top_fraction
        band_bottom = page_rect.height * 0.985
        band_height = max(1.0, band_bottom - band_top)

        # Match the reference cover: a left-aligned text block with a generous
        # left margin, white Garamond text, and no dark panel over the photo.
        left_margin = page_rect.width * 0.12
        right_margin = page_rect.width * 0.08
        safe_width = page_rect.width - left_margin - right_margin

        # Use one shared medium text size. Work out the exact largest size
        # that allows the widest of the three values to stay on ONE line.
        maximum_fontsize = 24.0
        minimum_fontsize = 8.0

        widest_at_one_point = max(
            font.text_length(value, fontsize=1.0) for value in values
        )
        if widest_at_one_point <= 0:
            raise PdfEditingError("The cover text could not be measured safely.")

        fontsize = min(maximum_fontsize, safe_width / widest_at_one_point)
        fontsize = max(minimum_fontsize, fontsize)

        # Keep the complete three-line block comfortably inside the coloured
        # band as well as within the horizontal safe area.
        line_height_factor = 1.25
        max_vertical_fontsize = band_height / (3.0 * line_height_factor)
        fontsize = min(fontsize, max_vertical_fontsize)

        # If a line is exceptionally long, keep reducing slightly below the
        # normal minimum rather than failing with a misleading textbox error.
        # A hard floor prevents unreadable output for pathological input.
        hard_floor = 5.0
        while (
            max(font.text_length(value, fontsize=fontsize) for value in values)
            > safe_width
            and fontsize > hard_floor
        ):
            fontsize -= 0.25

        widest = max(font.text_length(value, fontsize=fontsize) for value in values)
        if widest > safe_width + 0.1:
            raise PdfEditingError(
                "One of the cover text values is too long to fit safely on the cover. "
                "Please shorten that value."
            )

        line_height = fontsize * line_height_factor
        block_height = line_height * 3
        top = band_top + max(0.0, (band_height - block_height) / 2.0)

        # Font metrics are expressed relative to the font size. Using them to
        # calculate the baseline centres every line vertically within its row.
        ascender = float(getattr(font, "ascender", 0.9))
        descender = float(getattr(font, "descender", -0.2))

        for index, value in enumerate(values):
            row_center_y = top + (index + 0.5) * line_height
            baseline_y = row_center_y + ((ascender + descender) * fontsize / 2.0)

            cover_page.insert_text(
                fitz.Point(left_margin, baseline_y),
                value,
                fontname=font_name,
                fontsize=fontsize,
                color=(1, 1, 1),
                overlay=True,
            )

        if source.page_count > 1:
            output.insert_pdf(source, from_page=1, to_page=source.page_count - 1)

        result = output.tobytes(garbage=4, deflate=True)
    except PdfEditingError:
        raise
    except Exception as exc:
        raise PdfEditingError("The replacement cover could not be created safely.") from exc
    finally:
        if cover_doc is not None:
            cover_doc.close()
        if source is not None:
            source.close()
        if output is not None:
            output.close()

    return result, {
        "cover_replaced": True,
        "awarding_body": values[0],
        "course_name": values[1],
        "unit_name": values[2],
        "font_size": round(fontsize, 1),
        "text_band_top_fraction": round(band_top_fraction, 4),
        "text_alignment": "left",
        "pages": get_pdf_page_count(result),
    }

def replace_pdf_cover_and_text(
    pdf_bytes: bytes,
    *,
    cover_bytes: bytes | None = None,
    cover_filename: str | None = None,
    replacements: Iterable[tuple[str, str]] | None = None,
) -> tuple[bytes, dict]:
    """Apply an optional cover replacement and optional text replacements."""
    _read_pdf(pdf_bytes)

    updated = pdf_bytes
    cover_replaced = False
    replacement_report: list[dict] = []

    if cover_bytes is not None:
        updated = replace_pdf_cover(updated, cover_bytes, cover_filename)
        cover_replaced = True

    pairs = list(replacements or [])
    if pairs:
        updated, replacement_report = replace_pdf_text(updated, pairs)

    if not cover_replaced and not pairs:
        raise PdfEditingError("Upload a replacement cover or add at least one text replacement.")

    return updated, {
        "cover_replaced": cover_replaced,
        "text_replacements": replacement_report,
        "text_matches": sum(item["matches"] for item in replacement_report),
        "pages": get_pdf_page_count(updated),
    }


def _page_has_meaningful_text(page) -> bool:
    """
    Return True when the page contains text in the main body area.

    Text that exists only very near the top or bottom edge is treated as a
    header/footer. This lets us remove the unwanted blank second page that Word
    can create while avoiding a false non-blank result from a page number or
    running header/footer.
    """
    height = float(page.mediabox.height or 0)
    if height <= 0:
        return bool((page.extract_text() or "").strip())

    body_fragments: list[str] = []
    lower_limit = height * 0.08
    upper_limit = height * 0.92

    def visitor_text(text, _cm, tm, _font_dict, _font_size):
        if not text or not text.strip():
            return
        try:
            y = float(tm[5])
        except (TypeError, ValueError, IndexError):
            body_fragments.append(text)
            return
        if lower_limit <= y <= upper_limit:
            body_fragments.append(text)

    try:
        page.extract_text(visitor_text=visitor_text)
    except Exception:
        return bool((page.extract_text() or "").strip())

    return bool("".join(body_fragments).strip())


def _page_has_xobjects_or_annotations(page) -> bool:
    """Return True when a page contains images/forms or visible annotations."""
    try:
        if page.get("/Annots"):
            return True

        resources = page.get("/Resources")
        if resources is None:
            return False
        resources = resources.get_object() if hasattr(resources, "get_object") else resources

        xobjects = resources.get("/XObject") if resources else None
        if not xobjects:
            return False
        xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        return bool(xobjects)
    except Exception:
        # If PDF resources cannot be inspected safely, keep the page.
        return True


def _page_has_substantial_drawing_content(page) -> bool:
    """
    Conservatively keep pages that contain a substantial PDF drawing stream.

    A blank Word-generated page normally has no body text/images and a very
    small content stream. A page made from vector artwork can have no extractable
    text, so a larger stream is treated as meaningful content.
    """
    try:
        contents = page.get_contents()
        if contents is None:
            return False
        data = contents.get_data()
    except Exception:
        return True

    # 2 KiB is intentionally conservative: normal blank/header-only pages are
    # much smaller, while vector-heavy pages are retained.
    return len(data.strip()) > 2048


def is_page_visually_blank(page) -> bool:
    """Return True when a PDF page appears to contain no meaningful body content."""
    if _page_has_meaningful_text(page):
        return False
    if _page_has_xobjects_or_annotations(page):
        return False
    if _page_has_substantial_drawing_content(page):
        return False
    return True


def remove_blank_second_page(pdf_bytes: bytes) -> tuple[bytes, bool]:
    """
    Remove page 2 only when it is visually blank.

    The SLC Word-to-PDF workflow has an intermittent LibreOffice export issue
    where an unwanted blank second page is introduced. This targeted cleanup
    does not remove any other pages and never removes page 2 when meaningful
    content is detected.
    """
    reader = _read_pdf(pdf_bytes)

    if len(reader.pages) < 2 or not is_page_visually_blank(reader.pages[1]):
        return pdf_bytes, False

    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index != 1:
            writer.add_page(page)

    _copy_metadata(reader, writer)

    output = BytesIO()
    writer.write(output)
    return output.getvalue(), True
