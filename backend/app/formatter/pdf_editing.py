"""PDF page-removal helpers for the Streamlit document tool."""

from __future__ import annotations

from io import BytesIO
from typing import Iterable

from pypdf import PdfReader, PdfWriter


class PdfEditingError(ValueError):
    """Raised when a PDF cannot be safely processed."""


def get_pdf_page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in a readable, unencrypted PDF."""
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

    page_count = len(reader.pages)
    if page_count < 1:
        raise PdfEditingError("The uploaded PDF does not contain any pages.")
    return page_count


def remove_pdf_pages(pdf_bytes: bytes, pages_to_remove: Iterable[int]) -> bytes:
    """Remove one-based page numbers and return the updated PDF bytes."""
    page_count = get_pdf_page_count(pdf_bytes)

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

    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()

    for page_number, page in enumerate(reader.pages, start=1):
        if page_number not in selected:
            writer.add_page(page)

    if reader.metadata:
        metadata = {
            str(key): str(value)
            for key, value in reader.metadata.items()
            if key and value is not None
        }
        if metadata:
            writer.add_metadata(metadata)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()
