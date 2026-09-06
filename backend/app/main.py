from __future__ import annotations

from datetime import datetime, timezone
import json
import mimetypes
import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from dotenv import load_dotenv

# Load backend/.env during local development. Railway environment variables
# still take precedence because python-dotenv does not override them by default.
load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .formatter.engine import process
from .formatter.pdf_editing import PdfEditingError, get_pdf_page_count, remove_pdf_pages
from .models import Job
from .schemas import JobResponse
from .services.linked_images import LinkedImageResult, download_linked_images
from .services.batch_formatter import BatchFormatterError, format_zip_batch
from .services.storage import StorageError, storage
from .services.word_to_pdf import WordToPdfError, convert_word_files

APP_NAME = "SLC Document Tools API"
API_PREFIX = "/api/v1"
BUILD_VERSION = "2026.09.06-v3-cover-font"

app = FastAPI(title=APP_NAME, version="0.3.0")

origins = [
    item.strip()
    for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def _safe_stem(filename: str | None, fallback: str = "document") -> str:
    stem = Path(filename or fallback).stem.strip() or fallback
    return "".join(char if char.isalnum() or char in {"-", "_", " "} else "_" for char in stem)[:160]


def _job_response(job: Job, include_report: bool = True) -> JobResponse:
    details = {}
    if job.meta_json:
        try:
            details = json.loads(job.meta_json)
        except json.JSONDecodeError:
            details = {}

    report = None
    if include_report and job.report_key:
        try:
            report = storage.get_bytes(job.report_key).decode("utf-8", errors="replace")
        except StorageError:
            report = None

    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        original_filename=job.original_filename,
        output_filename=job.output_filename,
        download_url=f"{API_PREFIX}/jobs/{job.id}/download" if job.output_key else None,
        report_url=f"{API_PREFIX}/jobs/{job.id}/report" if job.report_key else None,
        report=report,
        details=details,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def _new_job(db: Session, job_type: str, original_filename: str | None) -> Job:
    job = Job(
        id=str(uuid4()),
        job_type=job_type,
        status="processing",
        original_filename=original_filename,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _complete_job(
    db: Session,
    job: Job,
    *,
    output_filename: str,
    output_key: str,
    report_key: str | None = None,
    details: dict | None = None,
) -> Job:
    job.status = "completed"
    job.output_filename = output_filename
    job.output_key = output_key
    job.report_key = report_key
    job.meta_json = json.dumps(details or {}, ensure_ascii=False)
    job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _fail_job(db: Session, job: Job, exc: Exception) -> None:
    job.status = "failed"
    job.error_message = str(exc)
    job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": APP_NAME, "build": BUILD_VERSION}


@app.post(f"{API_PREFIX}/formatter/format", response_model=JobResponse)
async def format_document(
    document: Annotated[UploadFile, File(...)],
    awarding_body: Annotated[str, Form()] = "",
    course_name: Annotated[str, Form()] = "",
    chapter: Annotated[str, Form()] = "",
    auto_download_links: Annotated[bool, Form()] = True,
    cover_image: Annotated[UploadFile | None, File()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
    db: Session = Depends(get_db),
) -> JobResponse:
    if Path(document.filename or "").suffix.lower() != ".docx":
        raise HTTPException(status_code=400, detail="Upload a DOCX document to format.")

    job = _new_job(db, "format", document.filename)
    try:
        document_bytes = await document.read()
        manual_items: list[tuple[str, bytes]] = []
        for image in images or []:
            manual_items.append((image.filename or "image", await image.read()))

        linked_result = LinkedImageResult()
        if auto_download_links:
            linked_result = download_linked_images(document_bytes, manual_items)

        cover_bytes = await cover_image.read() if cover_image else None
        cover_ext = Path(cover_image.filename or "cover.jpg").suffix.lstrip(".") if cover_image else "jpg"
        all_image_items = linked_result.items + manual_items

        result_bytes, log, validation_text, placement_report, image_catalog = process(
            document_bytes,
            cover_bytes,
            cover_ext,
            awarding_body,
            course_name,
            chapter,
            all_image_items,
        )
        validation_text += _linked_report_text(linked_result)

        stem = _safe_stem(document.filename)
        output_filename = f"{stem}_SLC_formatted.docx"
        output_key = f"jobs/{job.id}/{output_filename}"
        report_key = f"jobs/{job.id}/{stem}_validation_report.txt"
        storage.put_bytes(
            output_key,
            result_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        storage.put_bytes(report_key, validation_text.encode("utf-8"), "text/plain; charset=utf-8")

        details = {
            "build": BUILD_VERSION,
            "log": log,
            "images": {
                "placeholders": placement_report.total_placeholders,
                "inserted": placement_report.images_inserted,
                "missing": list(placement_report.missing_numbers),
                "unused": list(placement_report.unused_uploaded_images),
                "duplicates": image_catalog.duplicate_image_numbers,
            },
            "linked_images": linked_result.to_dict(),
        }
        job = _complete_job(
            db,
            job,
            output_filename=output_filename,
            output_key=output_key,
            report_key=report_key,
            details=details,
        )
        return _job_response(job)
    except Exception as exc:
        _fail_job(db, job, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(f"{API_PREFIX}/formatter/batch", response_model=JobResponse)
async def format_document_batch(
    document: Annotated[UploadFile, File(...)],
    awarding_body: Annotated[str, Form()] = "",
    course_name: Annotated[str, Form()] = "",
    auto_download_links: Annotated[bool, Form()] = True,
    cover_image: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
) -> JobResponse:
    if Path(document.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Upload a ZIP containing DOCX documents.")

    job = _new_job(db, "format_batch", document.filename)
    try:
        zip_bytes = await document.read()
        cover_bytes = await cover_image.read() if cover_image else None
        cover_ext = (
            Path(cover_image.filename or "cover.jpg").suffix.lstrip(".")
            if cover_image
            else "jpg"
        )

        result = format_zip_batch(
            zip_bytes,
            zip_filename=document.filename or "Course.zip",
            awarding_body=awarding_body,
            course_name=course_name,
            auto_download_links=auto_download_links,
            cover_bytes=cover_bytes,
            cover_ext=cover_ext,
        )

        result.details["build"] = BUILD_VERSION
        output_key = f"jobs/{job.id}/{result.output_filename}"
        report_key = f"jobs/{job.id}/batch_report.txt"
        storage.put_bytes(output_key, result.payload, "application/zip")
        storage.put_bytes(
            report_key,
            result.report_text.encode("utf-8"),
            "text/plain; charset=utf-8",
        )

        job = _complete_job(
            db,
            job,
            output_filename=result.output_filename,
            output_key=output_key,
            report_key=report_key,
            details=result.details,
        )
        return _job_response(job)
    except BatchFormatterError as exc:
        _fail_job(db, job, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _fail_job(db, job, exc)
        raise HTTPException(status_code=500, detail="Batch formatting failed.") from exc


@app.post(f"{API_PREFIX}/word-to-pdf", response_model=JobResponse)
async def word_to_pdf(
    files: Annotated[list[UploadFile], File(...)],
    db: Session = Depends(get_db),
) -> JobResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one DOCX file or ZIP.")
    original_name = files[0].filename if len(files) == 1 else f"{len(files)} uploaded files"
    job = _new_job(db, "word_to_pdf", original_name)
    try:
        items = [(item.filename or "document.docx", await item.read()) for item in files]
        output_filename, payload, content_type, details = convert_word_files(items)
        details["build"] = BUILD_VERSION
        output_key = f"jobs/{job.id}/{output_filename}"
        storage.put_bytes(output_key, payload, content_type)
        job = _complete_job(
            db,
            job,
            output_filename=output_filename,
            output_key=output_key,
            details=details,
        )
        return _job_response(job)
    except WordToPdfError as exc:
        _fail_job(db, job, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _fail_job(db, job, exc)
        raise HTTPException(status_code=500, detail="Word to PDF conversion failed.") from exc


@app.post(f"{API_PREFIX}/pdf/page-count")
async def pdf_page_count(file: Annotated[UploadFile, File(...)]) -> dict:
    try:
        payload = await file.read()
        return {"pages": get_pdf_page_count(payload)}
    except PdfEditingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(f"{API_PREFIX}/pdf/remove-pages", response_model=JobResponse)
async def pdf_remove_pages(
    file: Annotated[UploadFile, File(...)],
    pages: Annotated[str, Form(...)],
    db: Session = Depends(get_db),
) -> JobResponse:
    if Path(file.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Upload a PDF file.")
    job = _new_job(db, "pdf_remove_pages", file.filename)
    try:
        selected = [int(part.strip()) for part in pages.split(",") if part.strip()]
        payload = await file.read()
        updated = remove_pdf_pages(payload, selected)
        stem = _safe_stem(file.filename, "pdf")
        output_filename = f"{stem}_pages_removed.pdf"
        output_key = f"jobs/{job.id}/{output_filename}"
        storage.put_bytes(output_key, updated, "application/pdf")
        details = {
            "removed_pages": sorted(set(selected)),
            "original_pages": get_pdf_page_count(payload),
            "remaining_pages": get_pdf_page_count(updated),
        }
        job = _complete_job(
            db,
            job,
            output_filename=output_filename,
            output_key=output_key,
            details=details,
        )
        return _job_response(job)
    except (ValueError, PdfEditingError) as exc:
        _fail_job(db, job, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(f"{API_PREFIX}/jobs/{{job_id}}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobResponse:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_response(job)


@app.get(f"{API_PREFIX}/jobs/{{job_id}}/download")
def download_job(job_id: str, db: Session = Depends(get_db)) -> Response:
    job = db.get(Job, job_id)
    if not job or not job.output_key or not job.output_filename:
        raise HTTPException(status_code=404, detail="Output file not found.")
    try:
        payload = storage.get_bytes(job.output_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    content_type = mimetypes.guess_type(job.output_filename)[0] or "application/octet-stream"
    return Response(
        content=payload,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{job.output_filename}"'},
    )


@app.get(f"{API_PREFIX}/jobs/{{job_id}}/report")
def download_report(job_id: str, db: Session = Depends(get_db)) -> Response:
    job = db.get(Job, job_id)
    if not job or not job.report_key:
        raise HTTPException(status_code=404, detail="Report not found.")
    try:
        payload = storage.get_bytes(job.report_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="validation_report.txt"'},
    )
