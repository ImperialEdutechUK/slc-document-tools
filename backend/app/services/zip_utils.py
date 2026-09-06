"""Shared ZIP safety/name helpers used by document workflows."""

from __future__ import annotations

from pathlib import PurePosixPath


def normalise_zip_name(name: str) -> str:
    """Normalise ZIP member separators without trusting the member path."""
    return (name or "").replace("\\", "/")


def safe_zip_member(name: str) -> bool:
    """Return True only for relative, traversal-free, non-metadata members."""
    normalised = normalise_zip_name(name)
    if not normalised or normalised.startswith("/"):
        return False

    path = PurePosixPath(normalised)
    if path.is_absolute() or ".." in path.parts:
        return False
    if path.parts and path.parts[0].upper() == "__MACOSX":
        return False
    # Reject Windows drive-like prefixes (for example C:/temp/file.docx).
    if path.parts and ":" in path.parts[0]:
        return False
    return True
