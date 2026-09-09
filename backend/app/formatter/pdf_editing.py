"""PDF page inspection and page-removal helpers for the SLC document tool."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable

from pypdf import PdfReader, PdfWriter


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
