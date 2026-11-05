"""Private, multi-batch review of stock photos for DOCX and course ZIPs.

Each pass has a bounded stock/search budget. Progress and downloaded candidates
live in the private review bundle so a later pass can continue without repeating
completed searches, and final confirmation never calls photo providers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO, StringIO
import csv
import json
import os
from pathlib import Path
import re
import stat
import time
import zipfile

from docx import Document

from .course_images import (
    CourseImagesError, CourseImagesResult, ImagePlan, Picture, Provider,
    MAX_ARCHIVE_BYTES, MAX_ARCHIVE_FILES, MAX_DOCUMENT_BYTES,
    _insert_images, _plans, _read_chapters, _safe_name, _chapter_overview,
)
from .course_image_library import ImageLibrary, SavedImage
from .course_image_relevance import RelevanceContext, find_domain, lock_query
from .zip_utils import normalise_zip_name, safe_zip_member

# Per-request budgets; the complete ZIP may be processed in several passes.
MAX_PREVIEW_IMAGES = max(1, min(60, int(os.getenv("COURSE_IMAGE_MAX_PREVIEW_IMAGES", "16"))))
MAX_TOTAL_IMAGES = max(MAX_PREVIEW_IMAGES, min(120, int(os.getenv("COURSE_IMAGE_MAX_TOTAL_PREVIEW_IMAGES", "80"))))
MAX_STOCK_LOOKUPS = max(MAX_PREVIEW_IMAGES, min(200, int(os.getenv("COURSE_IMAGE_MAX_STOCK_LOOKUPS", "32"))))
MAX_SEARCH_SECONDS = max(20, min(180, int(os.getenv("COURSE_IMAGE_MAX_SEARCH_SECONDS", "75"))))
MAX_FREE_REPLACEMENTS = max(8, min(50, int(os.getenv("COURSE_IMAGE_MAX_FREE_REPLACEMENTS", "24"))))


@dataclass
class PreviewBundle:
    payload: bytes
    details: dict


def _balanced_plans(plans: list[ImagePlan], target: int) -> list[ImagePlan]:
    """Sample planned positions across an entire chapter, not just its start."""
    if target <= 0 or len(plans) <= target:
        return plans
    return [plans[min(len(plans) - 1, int((i + .5) * len(plans) / target))] for i in range(target)]


def _provider_for_review(manifest: dict, provider: Provider | None) -> Provider:
    provider = provider or Provider()
    provider.stock_source = manifest["image_source"]
    provider.reuse_existing = bool(manifest.get("reuse_existing", True))
    # Only confirmed images may enter the shared image library.
    provider.save_new_to_library = False
    provider.allow_freepik = bool(manifest.get("freepik_enabled", False))
    if manifest.get("verify_images") and (not provider.openrouter_key or not provider.vision_model):
        raise CourseImagesError(
            "Visual verification requires OPENROUTER_API_KEY and OPENROUTER_VISION_MODEL on the backend. "
            "Disable visual verification to use metadata-only subject filtering."
        )
    if manifest["image_source"] == "freepik" and not provider.allow_freepik:
        raise CourseImagesError("Enable Freepik stock access explicitly, or select free sources only.")
    if not provider.can_acquire(generate=False):
        raise CourseImagesError("No stock-image provider is configured. Set PEXELS_API_KEY or PIXABAY_API_KEY, or enable Openverse.")
    provider.used_photos.update(item.get("identifier", "") for item in manifest.get("previews", []) if item.get("identifier"))
    # Freepik paid-credit limits are cumulative across continuation requests.
    provider.freepik_attempts = int(manifest.get("freepik_search_attempts", 0))
    provider.freepik_downloads = int(manifest.get("freepik_stock_downloads", 0))
    # Each continuation has its own vision budget; the manifest tracks totals.
    provider.vision_checks = 0
    return provider


def _update_progress(manifest: dict) -> None:
    pending = manifest.get("pending_plans", [])
    cursor = min(int(manifest.get("next_plan_index", 0)), len(pending))
    previews = manifest["previews"]
    chapters = manifest["chapters"]
    paths = manifest.get("chapter_paths", chapters)
    eligible = manifest.get("eligible_by_chapter", [0] * len(chapters))
    manifest["chapter_progress"] = [{
        "index": index,
        "name": name,
        "path": paths[index],
        "eligible": eligible[index],
        "planned": sum(int(p["chapter_index"] == index) for p in pending),
        "searched": sum(int(p["chapter_index"] == index) for p in pending[:cursor]),
        "found": sum(int(p["chapter_index"] == index) for p in previews),
    } for index, name in enumerate(chapters)]
    manifest["images_found"] = len(previews)
    manifest["planned_image_positions"] = len(pending)
    manifest["remaining_positions"] = max(0, len(pending) - cursor)
    manifest["max_total_images"] = MAX_TOTAL_IMAGES
    manifest["has_more"] = bool(cursor < len(pending) and len(previews) < MAX_TOTAL_IMAGES)
    manifest["max_free_replacements"] = MAX_FREE_REPLACEMENTS


def _run_search_batch(manifest: dict, files: dict[str, bytes], provider: Provider) -> None:
    pending = manifest["pending_plans"]
    cursor = min(int(manifest.get("next_plan_index", 0)), len(pending))
    initial_count = len(manifest["previews"])
    searches_this_batch = 0
    started = time.monotonic()

    # Optional text-only prompt refinement is limited to THIS search pass.
    if manifest.get("use_openrouter") and cursor < len(pending):
        stop = min(len(pending), cursor + MAX_STOCK_LOOKUPS)
        for chapter_index in range(len(manifest["chapters"])):
            chunk = [ImagePlan(**entry["plan"]) for entry in pending[cursor:stop]
                     if entry["chapter_index"] == chapter_index]
            if not chunk:
                continue
            provider.refine_plans(manifest.get("course_name") or manifest["chapters"][chapter_index],
                                  manifest["chapters"][chapter_index], chunk,
                                  chapter_overview=manifest.get("chapter_overviews", [""] * len(manifest["chapters"]))[chapter_index])
            iterator = iter(chunk)
            for entry in pending[cursor:stop]:
                if entry["chapter_index"] == chapter_index:
                    entry["plan"] = asdict(next(iterator))

    while cursor < len(pending):
        if len(manifest["previews"]) >= MAX_TOTAL_IMAGES:
            break
        if len(manifest["previews"]) - initial_count >= MAX_PREVIEW_IMAGES:
            break
        if searches_this_batch >= MAX_STOCK_LOOKUPS:
            break
        if time.monotonic() - started >= MAX_SEARCH_SECONDS:
            break
        entry = pending[cursor]
        plan = ImagePlan(**entry["plan"])
        chapter_index = entry["chapter_index"]
        chapter = entry["chapter"]
        domain = (manifest.get("chapter_domains") or [None] * len(manifest["chapters"]))[chapter_index]
        course = manifest.get("course_name") or manifest.get("original_filename") or ""
        provider.active_context = RelevanceContext(
            domain=domain, course=course, heading=plan.heading,
            intended_scene=plan.description, query=lock_query(plan.search, domain, plan.heading),
            mode=manifest.get("relevance_mode", "strict"),
            verify_pixels=bool(manifest.get("verify_images", False)),
        )
        cursor += 1  # An unsuccessful lookup is still completed; never re-charge it.
        searches_this_batch += 1
        try:
            photo = provider.find(provider.active_context.query, subject=plan.heading, generate=False)
        except Exception as exc:
            manifest["warnings"].append(f"{chapter}: image search failed for '{plan.heading}' ({type(exc).__name__}).")
            photo = None
        if photo is None:
            manifest["warnings"].append(f"{chapter}: no suitable image found for '{plan.heading}'.")
            manifest["missing_images"].append({
                "chapter_index": chapter_index, "chapter": chapter,
                "heading": plan.heading, "search": plan.search,
                "intended_scene": plan.description,
                "pexels_url": plan.pexels_link,
                "stocksnap_url": plan.stocksnap_link,
                "pixabay_url": plan.pixabay_link,
            })
            continue
        image_id = len(manifest["previews"])
        files[f"images/{image_id}.jpg"] = photo.payload
        manifest["previews"].append({
            "id": image_id, "chapter_index": chapter_index, "chapter": chapter,
            "heading": plan.heading, "search": plan.search,
            "intended_scene": plan.description, "original_search": plan.search,
            "plan": asdict(plan), "source": photo.source,
            "source_url": photo.source_url, "licence": photo.licence,
            "author": photo.author, "reused": photo.reused,
            "identifier": photo.identifier,
                "relevance_score": photo.relevance_score,
                "relevance_reason": photo.relevance_reason,
                "verification_method": photo.verification_method or "not_checked",
                "course_domain": domain,
        })

    provider.active_context = None

    manifest["next_plan_index"] = cursor
    manifest["batch_count"] = int(manifest.get("batch_count", 0)) + 1
    manifest["stock_lookups_attempted"] = int(manifest.get("stock_lookups_attempted", 0)) + searches_this_batch
    manifest["image_library_hits"] = int(manifest.get("image_library_hits", 0)) + provider.library_hits
    manifest["freepik_stock_downloads"] = provider.freepik_downloads
    manifest["freepik_search_attempts"] = provider.freepik_attempts
    manifest["relevance_rejections"] = int(manifest.get("relevance_rejections", 0)) + provider.relevance_rejections
    manifest["vision_checks"] = int(manifest.get("vision_checks", 0)) + provider.vision_checks
    for source, count in provider.provider_attempts.items():
        manifest["provider_attempts"][source] = manifest["provider_attempts"].get(source, 0) + count
    for source, count in provider.provider_successes.items():
        manifest["provider_successes"][source] = manifest["provider_successes"].get(source, 0) + count
    manifest["warnings"].extend(provider.warnings)
    if provider.relevance_rejections:
        manifest["warnings"].append(
            f"Rejected {provider.relevance_rejections} stock results in this batch because subject, "
            "scene or visual verification was insufficient. These images were not suggested."
        )
    _update_progress(manifest)
    if manifest["has_more"]:
        manifest["warnings"].append(
            f"Image preview limit reached for this search batch (up to {MAX_PREVIEW_IMAGES} images, "
            f"{MAX_STOCK_LOOKUPS} searches or {MAX_SEARCH_SECONDS} seconds). "
            "Choose 'Find next batch' to continue from the next heading; earlier searches are retained."
        )
    elif manifest["remaining_positions"] > 0:
        manifest["warnings"].append(
            f"Maximum {MAX_TOTAL_IMAGES} images reached for this review. Start a new review to change coverage."
        )
    if not manifest["previews"]:
        if not provider.pexels_key:
            manifest["warnings"].append("Pexels API is not configured. Add a free PEXELS_API_KEY in Railway for realistic stock photography.")
        if not provider.pixabay_key:
            manifest["warnings"].append("Pixabay API is not configured. Add PIXABAY_API_KEY for an additional free source.")
        if provider.openverse_enabled and not provider.pexels_key and not provider.pixabay_key:
            manifest["warnings"].append("Only public-domain Openverse is configured; its limited photo catalogue may not cover abstract workplace topics.")
    manifest["warnings"] = list(dict.fromkeys(manifest["warnings"]))


def _public_details(manifest: dict) -> dict:
    private_keys = {"pending_plans", "chapter_overviews"}
    public = {key: value for key, value in manifest.items() if key not in private_keys and key != "previews"}
    public["previews"] = [
        {key: value for key, value in item.items() if key not in {"plan", "identifier", "original_search"}}
        for item in manifest["previews"]
    ]
    return public


def _pack_files(files: dict[str, bytes], manifest: dict) -> PreviewBundle:
    files["review.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name, contents in files.items():
            package.writestr(name, contents)
    return PreviewBundle(output.getvalue(), _public_details(manifest))


def make_image_preview(
    raw: bytes, filename: str, *, course_name: str = "", image_source: str = "mixed",
    reuse_existing: bool = True, use_openrouter: bool = False, allow_freepik: bool = False,
    images_per_chapter: int = 2, provider: Provider | None = None,
    relevance_mode: str = "strict", verify_images: bool = False,
) -> PreviewBundle:
    if image_source not in {"auto", "mixed", "free", "pexels", "pixabay", "openverse", "freepik"}:
        raise CourseImagesError("Invalid stock-image source.")
    if not 1 <= images_per_chapter <= 5:
        raise CourseImagesError("Choose between 1 and 5 images per chapter.")
    if relevance_mode not in {"strict", "balanced"}:
        raise CourseImagesError("Select Strict or Balanced image relevance.")
    chapters, _ = _read_chapters(raw, filename)
    manifest = {
        "original_filename": filename, "course_name": course_name,
        "chapters": [chapter.filename for chapter in chapters],
        "chapter_paths": [chapter.relative_path or chapter.filename for chapter in chapters],
        "eligible_by_chapter": [], "chapter_overviews": [],
        "previews": [], "pending_plans": [], "next_plan_index": 0,
        "image_source": image_source, "reuse_existing": reuse_existing,
        "relevance_mode": relevance_mode, "verify_images": bool(verify_images),
        "relevance_rejections": 0, "vision_checks": 0,
        "use_openrouter": use_openrouter, "images_per_chapter": images_per_chapter,
        "image_library_hits": 0, "freepik_stock_downloads": 0,
        "freepik_search_attempts": 0, "freepik_enabled": allow_freepik,
        "generation_attempts": 0, "free_replacements_used": 0,
        "stock_lookups_attempted": 0, "batch_count": 0, "missing_images": [],
        "matching_policy": "Course title subject lock + heading-specific metadata filtering; optional pixel verification. Uncertain results are omitted, not inserted.",
        "provider_attempts": {"Pexels": 0, "Pixabay": 0, "Openverse": 0, "Freepik": 0},
        "provider_successes": {"Pexels": 0, "Pixabay": 0, "Openverse": 0, "Freepik": 0},
        "warnings": [], "providers_configured": {},
    }
    files: dict[str, bytes] = {}
    if Path(filename).suffix.lower() == ".zip":
        # Keep the original ZIP privately for exact relative paths and untouched
        # assignment briefs, covers and other course assets at confirmation.
        files["original_upload.zip"] = raw
    chapter_plans: list[list[dict]] = []
    manifest["chapter_domains"] = []
    # A specific course title overrides all generic management/leadership
    # headings, including across several different DOCX files in a ZIP.
    course_lock = find_domain(course_name.strip() or Path(filename).stem)
    manifest["course_domain"] = course_lock
    eligible_total = 0
    for index, chapter in enumerate(chapters):
        files[f"documents/{index}.docx"] = chapter.payload
        chapter_plans.append([])
        manifest["chapter_overviews"].append("")
        manifest["eligible_by_chapter"].append(0)
        try:
            doc = Document(BytesIO(chapter.payload))
            overview = _chapter_overview(doc)
            manifest["chapter_overviews"][index] = overview
            first_headings = " ".join(p.text for p in doc.paragraphs[:30] if p.text.strip())[:400]
            local_domain = course_lock or find_domain(chapter.title, first_headings, overview)
            manifest["chapter_domains"].append(local_domain)
            # Local inference is used only if no explicit whole-course subject
            # exists. It steers both the scene planner and its stock queries.
            alias = {
                "adult_safeguarding": "health and social care", "child_safeguarding": "child safeguarding",
                "healthcare": "healthcare nursing", "education": "education teaching",
                "hr_payroll": "human resources payroll", "counselling": "counselling therapy",
                "it_security": "information technology", "waste": "waste management",
                "food": "food catering", "law": "legal studies", "finance": "accounting finance",
                "safety": "workplace safety", "management": "business management",
            }
            title = f"{alias.get(local_domain, '')} {course_name or Path(filename).stem} {chapter.title}".strip()
            plans, eligible = _plans(doc, title)
            manifest["eligible_by_chapter"][index] = eligible
            eligible_total += eligible
            chosen = _balanced_plans(plans, images_per_chapter) if Path(filename).suffix.lower() == ".zip" else plans
            chapter_plans[index] = [
                {"chapter_index": index, "chapter": manifest["chapter_paths"][index], "plan": asdict(plan)}
                for plan in chosen
            ]
        except Exception as exc:
            if len(manifest["chapter_domains"]) <= index:
                manifest["chapter_domains"].append(course_lock)
            manifest["warnings"].append(f"{manifest['chapter_paths'][index]}: cannot plan images ({type(exc).__name__}); original will be preserved.")
    if not course_lock:
        recognised = [d for d in manifest["chapter_domains"] if d]
        if recognised and len(set(recognised)) == 1:
            manifest["course_domain"] = recognised[0]
    # The same round-robin is used for every follow-up search: early chapters
    # cannot consume all requests while later chapters remain unsearched.
    for position in range(max((len(group) for group in chapter_plans), default=0)):
        for group in chapter_plans:
            if position < len(group):
                manifest["pending_plans"].append(group[position])
    manifest["eligible_headings"] = eligible_total
    configured = _provider_for_review(manifest, provider)
    manifest["providers_configured"] = {
        "pexels": bool(configured.pexels_key), "pixabay": bool(configured.pixabay_key),
        "openverse": configured.openverse_enabled, "freepik": bool(configured.freepik_key),
        "visual_verification": bool(configured.openrouter_key and configured.vision_model),
    }
    _run_search_batch(manifest, files, configured)
    return _pack_files(files, manifest)


def continue_image_preview(preview_bundle: bytes, *, provider: Provider | None = None) -> PreviewBundle:
    """Perform the next bounded batch without repeating searches or losing choices."""
    try:
        with zipfile.ZipFile(BytesIO(preview_bundle)) as archive:
            manifest = _load_manifest(archive)
            if not manifest.get("pending_plans"):
                raise CourseImagesError("This review does not support continuation; start a new preview.")
            if not manifest.get("has_more"):
                raise CourseImagesError("All planned headings have been searched for this review.")
            files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/") and name != "review.json"}
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CourseImagesError):
            raise
        raise CourseImagesError("The review archive is invalid. Start a new review.") from exc
    configured = _provider_for_review(manifest, provider)
    _run_search_batch(manifest, files, configured)
    return _pack_files(files, manifest)


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
    domain = item.get("course_domain")
    old_plan = item.get("plan") or {}
    locked_query = lock_query(query, domain, item.get("heading", ""))
    provider.active_context = RelevanceContext(
        domain=domain, course=manifest.get("course_name") or manifest.get("original_filename", ""),
        heading=item.get("heading", ""), intended_scene=old_plan.get("description", ""),
        query=locked_query, mode=manifest.get("relevance_mode", "strict"),
        verify_pixels=bool(manifest.get("verify_images", False)),
    )
    if provider.active_context.verify_pixels and (not provider.openrouter_key or not provider.vision_model):
        raise CourseImagesError("Visual verification requires OPENROUTER_API_KEY and OPENROUTER_VISION_MODEL.")
    provider.used_photos.update(
        p.get("identifier", "") for p in previews if p.get("identifier")
    )
    if not provider.can_acquire(generate=False):
        raise CourseImagesError("No free-photo provider is available. Enable Openverse or add a Pexels/Pixabay key.")
    new_image = provider.find(locked_query, subject=item["heading"], generate=False)
    if not new_image:
        raise CourseImagesError("No different free photograph matched those words. The previous photo is still available.")

    item.update({
        "search": query, "source": new_image.source, "source_url": new_image.source_url,
        "licence": new_image.licence, "author": new_image.author,
        "reused": False, "identifier": new_image.identifier,
        "relevance_score": new_image.relevance_score,
        "relevance_reason": new_image.relevance_reason,
        "verification_method": new_image.verification_method or "not_checked",
    })
    item["plan"]["search"] = query
    manifest["relevance_rejections"] = int(manifest.get("relevance_rejections", 0)) + provider.relevance_rejections
    manifest["vision_checks"] = int(manifest.get("vision_checks", 0)) + provider.vision_checks
    manifest["free_replacements_used"] = int(manifest.get("free_replacements_used", 0)) + 1
    manifest["warnings"] = list(dict.fromkeys(manifest.get("warnings", []) + provider.warnings))
    files[f"images/{image_id}.jpg"] = new_image.payload
    files["review.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name, raw in files.items():
            target.writestr(name, raw)
    return PreviewBundle(output.getvalue(), _public_details(manifest))


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




def _csv_value(value: object) -> str:
    """Avoid CSV formula injection from untrusted archive paths and headings."""
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _report_csv(rows: list[dict], sources: list[dict], missing: list[dict], warnings: list[str]) -> bytes:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Document path", "Status", "Eligible headings", "Planned images",
                     "Photos found", "Approved images", "Inserted images", "Message"])
    for item in rows:
        writer.writerow([_csv_value(item.get(key, "")) for key in (
            "path", "status", "eligible", "planned", "found", "approved", "inserted", "message"
        )])
    writer.writerow([])
    writer.writerow(["Approved photographs and licence details"])
    writer.writerow(["Chapter", "Heading", "Source provider", "Licence", "Photographer", "Source URL",
                     "Relevance check", "Relevance estimate (uncalibrated)", "Relevance rationale"])
    for item in sources:
        writer.writerow([_csv_value(item.get(key, "")) for key in (
            "chapter", "heading", "provider", "licence", "author", "source_url",
            "verification_method", "relevance_score", "relevance_reason",
        )])
    writer.writerow([])
    writer.writerow(["Searched headings without a matching photo"])
    writer.writerow(["Chapter", "Heading", "Suggested search words"])
    for item in missing:
        writer.writerow([_csv_value(item.get(key, "")) for key in ("chapter", "heading", "search")])
    if warnings:
        writer.writerow([])
        writer.writerow(["Processing notices"])
        for message in warnings:
            writer.writerow([_csv_value(message)])
    return output.getvalue().encode("utf-8-sig")


def _copy_original_zip(original: bytes, enhanced: dict[str, bytes], output: zipfile.ZipFile,
                       warnings: list[str]) -> tuple[int, str]:
    """Keep safe original folder paths, untouched course assets and skipped DOCX."""
    used: set[str] = set()
    files_written = 0
    uncompressed = 0
    with zipfile.ZipFile(BytesIO(original)) as source:
        if len(source.infolist()) > MAX_ARCHIVE_FILES:
            raise CourseImagesError("The source ZIP contains too many files.")
        for info in source.infolist():
            raw_name = normalise_zip_name(info.filename)
            if info.is_dir():
                continue
            if not safe_zip_member(info.filename):
                warnings.append(f"Unsafe ZIP entry omitted: {info.filename[:120]}")
                continue
            if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                warnings.append(f"Symbolic link omitted from ZIP: {raw_name[:120]}")
                continue
            if raw_name.lower() in used:
                warnings.append(f"Duplicate ZIP path omitted: {raw_name[:120]}")
                continue
            if info.file_size > MAX_DOCUMENT_BYTES:
                warnings.append(f"Oversized ZIP entry omitted: {raw_name[:120]}")
                continue
            if uncompressed + info.file_size > MAX_ARCHIVE_BYTES:
                raise CourseImagesError("The ZIP's uncompressed content exceeds the safe limit.")
            used.add(raw_name.lower())
            uncompressed += info.file_size
            try:
                data = enhanced.get(raw_name)
                if data is None:
                    data = source.read(info)
            except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as exc:
                warnings.append(f"Could not copy ZIP entry {raw_name[:120]} ({type(exc).__name__}).")
                continue
            output.writestr(raw_name, data)
            files_written += 1
    # Ensures a report does not overwrite or duplicate an input file.
    report_base = "SLC_Image_Insertion_Report.csv"
    report_name = report_base
    count = 2
    while report_name.lower() in used:
        report_name = f"SLC_Image_Insertion_Report_{count}.csv"
        count += 1
    return files_written, report_name


def finish_image_preview(preview_bundle: bytes, selected_ids: list[int]) -> CourseImagesResult:
    """Insert only approved photos; preserve exact input ZIP paths and other files.

    A malformed chapter cannot destroy the batch: its original DOCX is copied
    unchanged and its failure is included in the batch report.
    """
    if len(selected_ids) > MAX_TOTAL_IMAGES or len(selected_ids) != len(set(selected_ids)):
        raise CourseImagesError("The selected image list is invalid.")
    try:
        with zipfile.ZipFile(BytesIO(preview_bundle)) as archive:
            manifest = _load_manifest(archive)
            found = manifest["previews"]
            if any(type(i) is not int or i < 0 or i >= len(found) for i in selected_ids):
                raise CourseImagesError("One or more selected image IDs are invalid.")
            selected = set(selected_ids)
            enhanced: dict[str, bytes] = {}
            sources: list[dict] = []
            approved_for_cache: list[tuple[str, str, SavedImage]] = []
            warnings = list(manifest.get("warnings", []))
            inserted = 0
            documents_updated = 0
            documents_failed = 0
            chapter_rows: list[dict] = []
            paths = manifest.get("chapter_paths", manifest["chapters"])
            chapter_progress = manifest.get("chapter_progress", [])
            for chapter_index, chapter_filename in enumerate(manifest["chapters"]):
                path = paths[chapter_index]
                original = archive.read(f"documents/{chapter_index}.docx")
                chosen = [item for item in found if item.get("chapter_index") == chapter_index
                          and item.get("id") in selected]
                counters = chapter_progress[chapter_index] if chapter_index < len(chapter_progress) else {}
                row = {
                    "path": path, "eligible": counters.get("eligible", 0),
                    "planned": counters.get("planned", 0),
                    "found": counters.get("found", 0),
                    "approved": len(chosen), "inserted": 0,
                    "status": "Unchanged", "message": "No photographs approved",
                }
                revised = original
                if chosen:
                    try:
                        doc = Document(BytesIO(original))
                        plans = []
                        pictures = {}
                        for item in chosen:
                            plan = ImagePlan(**item["plan"])
                            if (plan.paragraph_index < 0 or plan.paragraph_index >= len(doc.paragraphs)
                                or " ".join(doc.paragraphs[plan.paragraph_index].text.split()) != plan.heading):
                                raise CourseImagesError("A planned heading could not be verified in the original Word document.")
                            pictures[plan.paragraph_index] = Picture(
                                archive.read(f"images/{item['id']}.jpg"), item["source"],
                                item.get("source_url", ""), identifier=item.get("identifier", ""),
                                licence=item.get("licence", ""), author=item.get("author", ""),
                                reused=bool(item.get("reused")),
                            )
                            plans.append(plan)
                        prior_shapes = len(doc.inline_shapes)
                        revised = _insert_images(doc, plans, pictures)
                        if len(Document(BytesIO(revised)).inline_shapes) < prior_shapes + len(pictures):
                            raise CourseImagesError("Some images were not embedded in the saved Word document.")
                        row["status"] = "Updated"
                        row["inserted"] = len(pictures)
                        row["message"] = "Approved images embedded"
                        inserted += len(pictures)
                        documents_updated += 1
                        for item in chosen:
                            photo = pictures[item["plan"]["paragraph_index"]]
                            sources.append({
                                "chapter": path, "heading": item["heading"],
                                "provider": photo.source, "source_url": photo.source_url,
                                "licence": photo.licence, "author": photo.author,
                                "reused": photo.reused,
                                "verification_method": item.get("verification_method", "not_checked"),
                                "relevance_score": item.get("relevance_score"),
                                "relevance_reason": item.get("relevance_reason", ""),
                            })
                            if manifest.get("reuse_existing", True):
                                approved_for_cache.append((
                                    item.get("original_search", item["search"]), item["heading"],
                                    SavedImage(photo.payload, photo.source, photo.source_url,
                                               photo.identifier, photo.licence, photo.author),
                                ))
                    except Exception as exc:
                        # Prefer a completely untouched chapter to a partially
                        # modified DOCX; continue processing the rest of the ZIP.
                        revised = original
                        row["status"] = "Failed – original preserved"
                        row["message"] = f"Image insertion failed ({type(exc).__name__})"
                        warnings.append(f"{path}: {row['message']}; original chapter retained.")
                        documents_failed += 1
                enhanced[path] = revised
                chapter_rows.append(row)
            original_zip = archive.read("original_upload.zip") if "original_upload.zip" in archive.namelist() else None
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CourseImagesError):
            raise
        raise CourseImagesError("The preview could not be finalised; create a new preview.") from exc

    # Cache only verified successful insertions. In particular, rejected
    # proposals or pictures for failed chapters are never cached.
    if approved_for_cache:
        library = ImageLibrary()
        for search, heading, item in approved_for_cache:
            library.save(search, heading, item, portrait=False)

    report = {
        "mode": "insert", "image_source": manifest.get("image_source", "mixed"),
        "chapters_processed": len(enhanced), "documents_with_images": documents_updated,
        "documents_unchanged": len(enhanced) - documents_updated - documents_failed,
        "documents_failed": documents_failed,
        "photos_inserted": inserted, "images_skipped_by_user": len(found) - len(selected),
        "images_previewed": len(found), "guides_created": 0, "covers_prepared": 0,
        "chapter_results": chapter_rows,
        "image_sources": sources, "image_library_hits": manifest.get("image_library_hits", 0),
        "freepik_stock_downloads": manifest.get("freepik_stock_downloads", 0),
        "freepik_search_attempts": manifest.get("freepik_search_attempts", 0),
        "freepik_enabled": manifest.get("freepik_enabled", False),
        "free_replacements_used": manifest.get("free_replacements_used", 0),
        "generation_attempts": 0, "warnings": list(dict.fromkeys(warnings)),
        "reuse_existing": manifest.get("reuse_existing", True),
    }
    filename = manifest["original_filename"]
    if Path(filename).suffix.lower() == ".docx":
        report["output_format"] = "docx"
        return CourseImagesResult(
            _safe_name(f"{Path(filename).stem}_with_images.docx"), next(iter(enhanced.values())), report
        )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        if original_zip is None:
            for path, data in enhanced.items():
                package.writestr(path, data)
            report_name = "SLC_Image_Insertion_Report.csv"
        else:
            _, report_name = _copy_original_zip(original_zip, enhanced, package, report["warnings"])
        package.writestr(report_name, _report_csv(chapter_rows, sources, manifest.get("missing_images", []), report["warnings"]))
    report["warnings"] = list(dict.fromkeys(report["warnings"]))
    report["output_format"] = "zip"
    report["report_filename"] = report_name
    return CourseImagesResult(
        _safe_name(manifest.get("course_name") or Path(filename).stem) + "_Documents_With_Images.zip",
        output.getvalue(), report,
    )
