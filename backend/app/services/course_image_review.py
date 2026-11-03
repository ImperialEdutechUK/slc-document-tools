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
import re
import time
import zipfile

from docx import Document

from .course_images import (
    CourseImagesError, CourseImagesResult, ImagePlan, Picture, Provider,
    _insert_images, _plans, _read_chapters, _safe_name, _chapter_overview,
)
from .course_image_library import ImageLibrary, SavedImage

MAX_PREVIEW_IMAGES = max(1, min(60, int(os.getenv("COURSE_IMAGE_MAX_PREVIEW_IMAGES", "16"))))
MAX_STOCK_LOOKUPS = max(MAX_PREVIEW_IMAGES, min(200, int(os.getenv("COURSE_IMAGE_MAX_STOCK_LOOKUPS", "32"))))
MAX_SEARCH_SECONDS = max(20, min(180, int(os.getenv("COURSE_IMAGE_MAX_SEARCH_SECONDS", "75"))))
MAX_FREE_REPLACEMENTS = 8


@dataclass
class PreviewBundle:
    payload: bytes
    details: dict


def make_image_preview(
    raw: bytes, filename: str, *, course_name: str = "", image_source: str = "mixed",
    reuse_existing: bool = True, use_openrouter: bool = False, allow_freepik: bool = False,
    provider: Provider | None = None,
) -> PreviewBundle:
    if image_source not in {"auto", "mixed", "free", "pexels", "pixabay", "openverse", "freepik"}:
        raise CourseImagesError("Invalid stock-image source.")
    chapters, _ = _read_chapters(raw, filename)
    provider = provider or Provider()
    provider.stock_source = image_source
    provider.reuse_existing = reuse_existing
    # Don't permanently cache a new photo until the user approves it. An
    # unselected irrelevant suggestion must not keep reappearing from cache.
    provider.save_new_to_library = False
    provider.allow_freepik = allow_freepik
    if image_source == "freepik" and not allow_freepik:
        raise CourseImagesError("Enable Freepik stock access explicitly, or select free sources only.")
    if not provider.can_acquire(generate=False):
        raise CourseImagesError("No stock-image provider is configured. Set PEXELS_API_KEY or PIXABAY_API_KEY, or enable Openverse.")

    previews: list[dict] = []
    names: list[str] = []
    warnings: list[str] = []
    images_acquired = 0
    eligible_total = 0
    lookups_attempted = 0
    image_limit_hit = False
    lookup_limit_hit = False
    time_limit_hit = False
    chapter_plans: list[list[tuple[int, str, ImagePlan]]] = []
    chapter_overviews: list[str] = []
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        for chapter_index, chapter in enumerate(chapters):
            names.append(chapter.filename)
            package.writestr(f"documents/{chapter_index}.docx", chapter.payload)
            chapter_plans.append([])
            chapter_overviews.append("")
            try:
                doc = Document(BytesIO(chapter.payload))
                plans, eligible = _plans(doc, f"{course_name} {chapter.title}")
                chapter_overviews[chapter_index] = _chapter_overview(doc)
                eligible_total += eligible
            except Exception as exc:
                warnings.append(f"{chapter.filename}: cannot plan images ({type(exc).__name__}).")
                continue
            chapter_plans[chapter_index] = [(chapter_index, chapter.filename, plan) for plan in plans]

        # Fair distribution across chapters. Previously the first chapters
        # could consume the entire preview limit and later ZIP documents got
        # no photos at all. Keep one candidate per chapter per round.
        pending: list[tuple[int, str, ImagePlan]] = []
        for position in range(max((len(group) for group in chapter_plans), default=0)):
            for group in chapter_plans:
                if position < len(group):
                    pending.append(group[position])

        # Optional text refinement is limited to the planned stock lookups,
        # avoiding OpenRouter calls for headings we will never search.
        if use_openrouter:
            for index, chapter in enumerate(chapters):
                limited = [plan for chapter_index, _, plan in pending[:MAX_STOCK_LOOKUPS]
                           if chapter_index == index]
                if limited:
                    provider.refine_plans(course_name or chapter.title, chapter.title, limited,
                                          chapter_overview=chapter_overviews[index])

        started = time.monotonic()
        for chapter_index, chapter_filename, plan in pending:
            if len(previews) >= MAX_PREVIEW_IMAGES:
                image_limit_hit = True
                break
            if lookups_attempted >= MAX_STOCK_LOOKUPS:
                lookup_limit_hit = True
                break
            if time.monotonic() - started >= MAX_SEARCH_SECONDS:
                time_limit_hit = True
                break
            lookups_attempted += 1
            try:
                photo = provider.find(plan.search, subject=plan.heading, generate=False)
            except Exception as exc:
                warnings.append(f"{chapter_filename}: image search failed for '{plan.heading}' ({type(exc).__name__}).")
                photo = None
            if not photo:
                warnings.append(f"{chapter_filename}: no suitable image found for '{plan.heading}'.")
                continue
            idx = len(previews)
            package.writestr(f"images/{idx}.jpg", photo.payload)
            details = {
                "id": idx, "chapter_index": chapter_index, "chapter": chapter_filename,
                "heading": plan.heading, "search": plan.search,
                "intended_scene": plan.description,
                "original_search": plan.search,
                "plan": asdict(plan),
                "source": photo.source, "source_url": photo.source_url,
                "licence": photo.licence, "author": photo.author,
                "reused": photo.reused, "identifier": photo.identifier,
            }
            previews.append(details)
            images_acquired += 1
        if image_limit_hit:
            warnings.append(f"Image preview limit reached ({MAX_PREVIEW_IMAGES} per job). Remaining headings were skipped to control requests and costs.")
        if lookup_limit_hit:
            warnings.append(f"Stock-search limit reached ({MAX_STOCK_LOOKUPS} headings per job). Remaining headings were skipped to protect provider quotas.")
        if time_limit_hit:
            warnings.append(f"Search-time limit reached ({MAX_SEARCH_SECONDS} seconds per job). Remaining headings were skipped to prevent a stalled upload.")
        if images_acquired == 0:
            if not provider.pexels_key:
                warnings.append("Pexels API is not configured. Add a free PEXELS_API_KEY in Railway for realistic stock photography.")
            if not provider.pixabay_key:
                warnings.append("Pixabay API is not configured. Add PIXABAY_API_KEY for an additional free source.")
            if provider.openverse_enabled and not provider.pexels_key and not provider.pixabay_key:
                warnings.append("Only public-domain Openverse is configured; its limited photo catalogue may not cover abstract workplace topics.")
            if not provider.pexels_key and not provider.pixabay_key and not provider.openverse_enabled:
                warnings.append("No free photo API is enabled for this job. Freepik stock may have reached its paid request budget.")
        report = {
            "original_filename": filename, "course_name": course_name,
            "chapters": names, "previews": previews,
            "image_source": image_source, "reuse_existing": reuse_existing,
            "image_library_hits": provider.library_hits,
            "freepik_stock_downloads": provider.freepik_downloads,
            "freepik_search_attempts": provider.freepik_attempts,
            "freepik_enabled": allow_freepik,
            "generation_attempts": 0, "images_found": images_acquired,
            "free_replacements_used": 0,
            "eligible_headings": eligible_total,
            "planned_image_positions": len(pending),
            "stock_lookups_attempted": lookups_attempted,
            "matching_policy": "Course title + chapter context + individual heading; stock tags are suggestions, not visual verification.",
            "provider_attempts": provider.provider_attempts,
            "provider_successes": provider.provider_successes,
            "missing_images": [
                {"chapter": filename_, "heading": planned.heading, "search": planned.search,
                 "intended_scene": planned.description,
                 "pexels_url": planned.pexels_link, "stocksnap_url": planned.stocksnap_link,
                 "pixabay_url": planned.pixabay_link}
                for _, filename_, planned in pending[:lookups_attempted][:60]
                if not any(x["chapter"] == filename_ and x["heading"] == planned.heading for x in previews)
            ],
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
        {k: v for k, v in item.items() if k not in {"plan", "identifier", "original_search"}} for item in previews
    ]
    return PreviewBundle(out.getvalue(), public_report)


def replace_with_free_image(
    preview_bundle: bytes, image_id: int, search: str, *, provider: Provider | None = None,
) -> PreviewBundle:
    """Swap one reviewed suggestion using free sources only; never call Freepik.

    Original documents and all other image choices are preserved byte-for-byte.
    The same preview identifier is retained, so previously ticked selections stay
    valid. No Word document is edited until the user confirms the review.
    """
    query = " ".join(search.split())
    if not (3 <= len(query) <= 110) or not re.search(r"[a-zA-Z]{3}", query) or "http" in query.lower():
        raise CourseImagesError("Enter 3–110 characters of plain photo search words (not a link).")
    if image_id < 0:
        raise CourseImagesError("The selected image ID is invalid.")
    try:
        with zipfile.ZipFile(BytesIO(preview_bundle)) as archive:
            manifest = _load_manifest(archive)
            previews = manifest["previews"]
            if image_id >= len(previews) or previews[image_id].get("id") != image_id:
                raise CourseImagesError("The requested preview photo was not found.")
            if int(manifest.get("free_replacements_used", 0)) >= MAX_FREE_REPLACEMENTS:
                raise CourseImagesError("Free replacement limit reached. Start a new review for additional choices.")
            item = previews[image_id]
            files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CourseImagesError):
            raise
        raise CourseImagesError("The review archive is invalid. Start a new review.") from exc

    provider = provider or Provider()
    provider.stock_source = "free"
    provider.allow_freepik = False
    provider.reuse_existing = False  # An identical cache hit would return the original.
    provider.used_photos.update(
        p.get("identifier", "") for p in previews if p.get("identifier")
    )
    if not provider.can_acquire(generate=False):
        raise CourseImagesError("No free-photo provider is available. Enable Openverse or add a Pexels/Pixabay key.")
    new_image = provider.find(query, subject=query, generate=False)
    if not new_image:
        raise CourseImagesError("No different free photograph matched those words. The previous photo is still available.")

    item.update({
        "search": query, "source": new_image.source, "source_url": new_image.source_url,
        "licence": new_image.licence, "author": new_image.author,
        "reused": False, "identifier": new_image.identifier,
    })
    item["plan"]["search"] = query
    manifest["free_replacements_used"] = int(manifest.get("free_replacements_used", 0)) + 1
    manifest["warnings"] = list(dict.fromkeys(manifest.get("warnings", []) + provider.warnings))
    files[f"images/{image_id}.jpg"] = new_image.payload
    files["review.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name, raw in files.items():
            target.writestr(name, raw)
    public = {key: value for key, value in manifest.items() if key != "previews"}
    public["previews"] = [
        {key: value for key, value in value.items() if key not in {"plan", "identifier", "original_search"}}
        for value in previews
    ]
    return PreviewBundle(output.getvalue(), public)


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
            approved_for_cache: list[tuple[str, str, SavedImage]] = []
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
                                     identifier=item.get("identifier", ""),
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
                    if manifest.get("reuse_existing", True):
                        approved_for_cache.append((
                            item.get("original_search", plan.search), plan.heading,
                            SavedImage(photos.payload, photos.source, photos.source_url,
                                       photos.identifier, photos.licence, photos.author),
                        ))
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

    # Approve cache writes only after all selected pictures were confirmed in
    # the finished DOCX, never merely because search returned a candidate.
    if approved_for_cache:
        library = ImageLibrary()
        for search, heading, item in approved_for_cache:
            library.save(search, heading, item, portrait=False)

    report = {
        "mode": "insert", "image_source": manifest.get("image_source", "mixed"),
        "chapters_processed": len(enhanced), "documents_with_images": len(enhanced),
        "photos_inserted": inserted, "images_skipped_by_user": len(found) - len(selected),
        "images_previewed": len(found), "guides_created": 0, "covers_prepared": 0,
        "image_sources": sources, "image_library_hits": manifest.get("image_library_hits", 0),
        "freepik_stock_downloads": manifest.get("freepik_stock_downloads", 0),
        "freepik_search_attempts": manifest.get("freepik_search_attempts", 0),
        "freepik_enabled": manifest.get("freepik_enabled", False),
        "free_replacements_used": manifest.get("free_replacements_used", 0),
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
