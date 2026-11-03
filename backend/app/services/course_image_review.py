"""Two-stage image review workflow: find photos, show previews, then insert selected.

The preview archive is a private backend artifact; originals and acquired photo
bytes are never sent to the browser until explicitly downloaded as the final Word
file. Confirming a preview requires no additional provider API calls or credits.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import zipfile

from docx import Document

from .course_images import (
    CourseImagesError, CourseImagesResult, ImagePlan, Picture, Provider,
    _insert_images, _plans, _read_chapters, _safe_name,
)

MAX_PREVIEW_IMAGES = max(1, min(60, int(os.getenv("COURSE_IMAGE_MAX_PREVIEW_IMAGES", "16"))))


@dataclass
class PreviewBundle:
    payload: bytes
    details: dict


def make_image_preview(
    raw: bytes, filename: str, *, course_name: str = "", image_source: str = "mixed",
    reuse_existing: bool = True, use_openrouter: bool = False, provider: Provider | None = None,
) -> PreviewBundle:
    if image_source not in {"auto", "mixed", "free", "pexels", "pixabay", "openverse", "freepik"}:
        raise CourseImagesError("Invalid stock-image source.")
    chapters, _ = _read_chapters(raw, filename)
    provider = provider or Provider()
    provider.stock_source = image_source
    provider.reuse_existing = reuse_existing
    if not provider.can_acquire(generate=False):
        raise CourseImagesError("No stock-image provider is configured. Set PEXELS_API_KEY or PIXABAY_API_KEY, or enable Openverse.")

    previews: list[dict] = []
    names: list[str] = []
    warnings: list[str] = []
    images_acquired = 0
    eligible_total = 0
    image_limit_hit = False
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        for chapter_index, chapter in enumerate(chapters):
            names.append(chapter.filename)
            package.writestr(f"documents/{chapter_index}.docx", chapter.payload)
            try:
                doc = Document(BytesIO(chapter.payload))
                plans, eligible = _plans(doc, f"{course_name} {chapter.title}")
                eligible_total += eligible
                if use_openrouter:
                    provider.refine_plans(course_name, chapter.title, plans)
            except Exception as exc:
                warnings.append(f"{chapter.filename}: cannot plan images ({type(exc).__name__}).")
                continue
            for plan in plans:
                if len(previews) >= MAX_PREVIEW_IMAGES:
                    image_limit_hit = True
                    break
                try:
                    photo = provider.find(plan.search, subject=plan.heading, generate=False)
                except Exception as exc:
                    warnings.append(f"{chapter.filename}: image search failed for '{plan.heading}' ({type(exc).__name__}).")
                    photo = None
                if not photo:
                    warnings.append(f"{chapter.filename}: no suitable image found for '{plan.heading}'.")
                    continue
                idx = len(previews)
                package.writestr(f"images/{idx}.jpg", photo.payload)
                details = {
                    "id": idx, "chapter_index": chapter_index, "chapter": chapter.filename,
                    "heading": plan.heading, "search": plan.search,
                    "plan": asdict(plan),
                    "source": photo.source, "source_url": photo.source_url,
                    "licence": photo.licence, "author": photo.author,
                    "reused": photo.reused,
                }
                previews.append(details)
                images_acquired += 1
        if image_limit_hit:
            warnings.append(f"Image preview limit reached ({MAX_PREVIEW_IMAGES} per job). Remaining headings were skipped to control requests and costs.")
        report = {
            "original_filename": filename, "course_name": course_name,
            "chapters": names, "previews": previews,
            "image_source": image_source, "reuse_existing": reuse_existing,
            "image_library_hits": provider.library_hits,
            "freepik_stock_downloads": provider.freepik_downloads,
            "generation_attempts": 0, "images_found": images_acquired,
            "eligible_headings": eligible_total,
            "warnings": list(dict.fromkeys(warnings + provider.warnings)),
            "providers_configured": {
                "pexels": bool(provider.pexels_key), "pixabay": bool(provider.pixabay_key),
                "openverse": provider.openverse_enabled, "freepik": bool(provider.freepik_key),
            },
        }
        package.writestr("review.json", json.dumps(report, ensure_ascii=False).encode("utf-8"))
    # Do not put photo bytes or document content in the public job report.
    public_report = {k: v for k, v in report.items() if k != "previews"}
    public_report["previews"] = [
        {k: v for k, v in item.items() if k != "plan"} for item in previews
    ]
    return PreviewBundle(out.getvalue(), public_report)


def _load_manifest(package: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(package.read("review.json"))
    except (ValueError, KeyError) as exc:
        raise CourseImagesError("The image preview is invalid. Run a fresh preview.") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("previews"), list):
        raise CourseImagesError("Invalid preview manifest.")
    return manifest


def preview_jpeg(preview_bundle: bytes, image_id: int) -> bytes:
    if image_id < 0:
        raise CourseImagesError("Image number is invalid.")
    try:
        with zipfile.ZipFile(BytesIO(preview_bundle)) as archive:
            manifest = _load_manifest(archive)
            if image_id >= len(manifest["previews"]):
                raise CourseImagesError("Preview image not found.")
            raw = archive.read(f"images/{image_id}.jpg")
            if not raw.startswith(b"\xff\xd8"):
                raise CourseImagesError("The preview image is corrupt.")
            return raw
    except (zipfile.BadZipFile, KeyError) as exc:
        raise CourseImagesError("The preview archive is corrupt.") from exc


def finish_image_preview(preview_bundle: bytes, selected_ids: list[int]) -> CourseImagesResult:
    if len(selected_ids) > MAX_PREVIEW_IMAGES or len(selected_ids) != len(set(selected_ids)):
        raise CourseImagesError("The selected image list is invalid.")
    try:
        with zipfile.ZipFile(BytesIO(preview_bundle)) as archive:
            manifest = _load_manifest(archive)
            found = manifest["previews"]
            if any(type(i) is not int or i < 0 or i >= len(found) for i in selected_ids):
                raise CourseImagesError("One or more selected image IDs are invalid.")
            selected = set(selected_ids)
            enhanced: list[tuple[str, bytes]] = []
            sources: list[dict] = []
            inserted = 0
            for chapter_index, chapter_filename in enumerate(manifest["chapters"]):
                original = archive.read(f"documents/{chapter_index}.docx")
                doc = Document(BytesIO(original))
                plans = []
                pictures = {}
                for item in found:
                    if item.get("chapter_index") != chapter_index or item.get("id") not in selected:
                        continue
                    plan = ImagePlan(**item["plan"])
                    image_bytes = archive.read(f"images/{item['id']}.jpg")
                    photos = Picture(image_bytes, item["source"], item.get("source_url", ""),
                                     licence=item.get("licence", ""), author=item.get("author", ""),
                                     reused=bool(item.get("reused")))
                    if not (0 <= plan.paragraph_index < len(doc.paragraphs)) or " ".join(doc.paragraphs[plan.paragraph_index].text.split()) != plan.heading:
                        raise CourseImagesError("A heading changed since preview. Please run a fresh preview.")
                    plans.append(plan)
                    pictures[plan.paragraph_index] = photos
                    sources.append({"chapter": chapter_filename, "heading": plan.heading,
                                    "provider": photos.source, "source_url": photos.source_url,
                                    "licence": photos.licence, "author": photos.author,
                                    "reused": photos.reused})
                original_picture_count = len(doc.inline_shapes)
                revised = _insert_images(doc, plans, pictures) if pictures else original
                # Verify saved DOCX rather than only the insertion code's intention.
                result_doc = Document(BytesIO(revised))
                if len(result_doc.inline_shapes) < original_picture_count + len(pictures):
                    raise CourseImagesError("Some images were not embedded in the saved Word file.")
                inserted += len(pictures)
                enhanced.append((f"{Path(chapter_filename).stem}_with_images.docx", revised))
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CourseImagesError):
            raise
        raise CourseImagesError("The preview could not be finalised; create a new preview.") from exc

    report = {
        "mode": "insert", "image_source": manifest.get("image_source", "mixed"),
        "chapters_processed": len(enhanced), "documents_with_images": len(enhanced),
        "photos_inserted": inserted, "images_skipped_by_user": len(found) - len(selected),
        "images_previewed": len(found), "guides_created": 0, "covers_prepared": 0,
        "image_sources": sources, "image_library_hits": manifest.get("image_library_hits", 0),
        "freepik_stock_downloads": manifest.get("freepik_stock_downloads", 0),
        "generation_attempts": 0, "warnings": manifest.get("warnings", []),
        "reuse_existing": manifest.get("reuse_existing", True),
    }
    filename = manifest["original_filename"]
    if Path(filename).suffix.lower() == ".docx":
        report["output_format"] = "docx"
        return CourseImagesResult(_safe_name(enhanced[0][0]), enhanced[0][1], report)
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        used_names = set()
        for name, raw in enhanced:
            path = _safe_name(name)
            counter = 2
            while path.lower() in used_names:
                path = f"{Path(name).stem}_{counter}.docx"
                counter += 1
            used_names.add(path.lower())
            package.writestr(path, raw)
    report["output_format"] = "zip"
    return CourseImagesResult(_safe_name(manifest.get("course_name") or Path(filename).stem) + "_Documents_With_Images.zip", out.getvalue(), report)
