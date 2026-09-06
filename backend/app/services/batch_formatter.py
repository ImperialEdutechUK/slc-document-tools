"""ZIP batch formatting orchestration for the SLC document formatter."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import zipfile

from ..formatter.engine import process
from .linked_images import LinkedImageResult, download_linked_images
from .zip_utils import normalise_zip_name, safe_zip_member

ZIP_MIME = "application/zip"

_SKIP_PATTERNS = (
    re.compile(r"\bassignment[\s_-]*brief\b", re.IGNORECASE),
    re.compile(r"\bwritten[\s_-]*assignment[\s_-]*template\b", re.IGNORECASE),
)


class BatchFormatterError(ValueError):
    pass


@dataclass
class BatchDocument:
    archive_name: str
    filename: str
    payload: bytes


@dataclass
class BatchFailure:
    filename: str
    reason: str


@dataclass
class BatchResult:
    output_filename: str
    payload: bytes
    report_text: str
    details: dict


@dataclass
class _Aggregate:
    detected: int = 0
    formatted: int = 0
    skipped: int = 0
    failed: int = 0
    linked_downloaded: int = 0
    linked_failed: int = 0
    formatted_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    failures: list[BatchFailure] = field(default_factory=list)


def should_skip_document(filename: str) -> bool:
    """Identify documents that should bypass the SLC content formatter."""
    stem = Path(filename).stem
    candidate = re.sub(r"[_-]+", " ", stem)
    return any(pattern.search(candidate) for pattern in _SKIP_PATTERNS)


def _friendly_title(filename: str) -> str:
    stem = Path(filename).stem
    title = re.sub(r"[_-]+", " ", stem)
    return " ".join(title.split()) or "Course document"


def _unique_name(name: str, used: set[str]) -> str:
    candidate = name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used.add(candidate.lower())
    return candidate


def extract_batch_documents(zip_bytes: bytes) -> list[BatchDocument]:
    """Extract safe DOCX members from a user ZIP without writing paths to disk."""
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            documents: list[BatchDocument] = []
            for info in archive.infolist():
                if info.is_dir() or not safe_zip_member(info.filename):
                    continue
                safe_name = normalise_zip_name(info.filename)
                if Path(safe_name).suffix.lower() != ".docx":
                    continue
                filename = PurePosixPath(safe_name).name
                documents.append(
                    BatchDocument(
                        archive_name=safe_name,
                        filename=filename,
                        payload=archive.read(info),
                    )
                )
    except zipfile.BadZipFile as exc:
        raise BatchFormatterError("The uploaded file is not a readable ZIP archive.") from exc

    if not documents:
        raise BatchFormatterError("No DOCX files were found in the ZIP archive.")
    return documents


def _linked_report_text(link_result: LinkedImageResult) -> str:
    if not link_result.entries and not link_result.warnings:
        return ""
    lines = ["", "Linked image retrieval", "----------------------"]
    for entry in link_result.entries:
        label = {
            "downloaded": "OK",
            "failed": "FAILED",
            "manual_override": "MANUAL OVERRIDE",
        }.get(entry.status, entry.status.upper())
        line = f"Image {entry.number}: {label} - {entry.source_type} - {entry.url}"
        if entry.message:
            line += f" ({entry.message})"
        lines.append(line)
    for warning in link_result.warnings:
        lines.append(f"Warning: {warning}")
    return "\n".join(lines) + "\n"


def _batch_report(aggregate: _Aggregate) -> str:
    lines = [
        "SLC Batch Formatting Report",
        "===========================",
        "",
        f"Documents detected: {aggregate.detected}",
        f"Formatted:          {aggregate.formatted}",
        f"Skipped:            {aggregate.skipped}",
        f"Failed:             {aggregate.failed}",
        "",
        f"Linked images downloaded: {aggregate.linked_downloaded}",
        f"Linked image failures:    {aggregate.linked_failed}",
    ]

    if aggregate.skipped_files:
        lines.extend(["", "SKIPPED", "-------"])
        lines.extend(f"- {name}" for name in aggregate.skipped_files)

    if aggregate.failures:
        lines.extend(["", "FAILED", "------"])
        for failure in aggregate.failures:
            lines.append(f"- {failure.filename}")
            lines.append(f"  Reason: {failure.reason}")

    if aggregate.formatted_files:
        lines.extend(["", "FORMATTED", "---------"])
        lines.extend(f"- {name}" for name in aggregate.formatted_files)

    return "\n".join(lines).rstrip() + "\n"


def format_zip_batch(
    zip_bytes: bytes,
    *,
    zip_filename: str,
    awarding_body: str = "",
    course_name: str = "",
    auto_download_links: bool = True,
    cover_bytes: bytes | None = None,
    cover_ext: str = "jpg",
) -> BatchResult:
    """Format every eligible DOCX in a ZIP and return one structured ZIP."""
    documents = extract_batch_documents(zip_bytes)
    aggregate = _Aggregate(detected=len(documents))

    formatted_items: list[tuple[str, bytes]] = []
    report_items: list[tuple[str, bytes]] = []
    skipped_items: list[tuple[str, bytes]] = []
    used_formatted: set[str] = set()
    used_reports: set[str] = set()
    used_skipped: set[str] = set()

    for document in documents:
        if should_skip_document(document.filename):
            aggregate.skipped += 1
            aggregate.skipped_files.append(document.filename)
            skipped_name = _unique_name(document.filename, used_skipped)
            skipped_items.append((skipped_name, document.payload))
            continue

        try:
            linked_result = LinkedImageResult()
            if auto_download_links:
                linked_result = download_linked_images(document.payload, [])

            result_bytes, log, validation_text, placement_report, image_catalog = process(
                document.payload,
                cover_bytes,
                cover_ext,
                awarding_body,
                course_name,
                _friendly_title(document.filename),
                linked_result.items,
            )
            validation_text += _linked_report_text(linked_result)

            aggregate.linked_downloaded += linked_result.to_dict()["downloaded"]
            aggregate.linked_failed += linked_result.to_dict()["failed"]
            aggregate.formatted += 1

            formatted_name = _unique_name(
                f"{Path(document.filename).stem}_SLC_formatted.docx",
                used_formatted,
            )
            report_name = _unique_name(
                f"{Path(document.filename).stem}_validation_report.txt",
                used_reports,
            )
            aggregate.formatted_files.append(formatted_name)
            formatted_items.append((formatted_name, result_bytes))
            report_items.append((report_name, validation_text.encode("utf-8")))
        except Exception as exc:  # keep the rest of the batch moving
            aggregate.failed += 1
            aggregate.failures.append(BatchFailure(document.filename, str(exc)))

    report_text = _batch_report(aggregate)
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in formatted_items:
            archive.writestr(f"formatted/{name}", payload)
        for name, payload in report_items:
            archive.writestr(f"reports/{name}", payload)
        for name, payload in skipped_items:
            archive.writestr(f"skipped/{name}", payload)
        archive.writestr("batch_report.txt", report_text.encode("utf-8"))

    stem = Path(zip_filename or "Course").stem.strip() or "Course"
    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_", " "} else "_"
        for char in stem
    )[:160]
    output_filename = f"{safe_stem}_SLC_formatted.zip"
    details = {
        "documents_detected": aggregate.detected,
        "formatted": aggregate.formatted,
        "skipped": aggregate.skipped,
        "failed": aggregate.failed,
        "formatted_files": aggregate.formatted_files,
        "skipped_files": aggregate.skipped_files,
        "failures": [
            {"filename": failure.filename, "reason": failure.reason}
            for failure in aggregate.failures
        ],
        "linked_images": {
            "downloaded": aggregate.linked_downloaded,
            "failed": aggregate.linked_failed,
        },
    }
    return BatchResult(output_filename, output.getvalue(), report_text, details)
