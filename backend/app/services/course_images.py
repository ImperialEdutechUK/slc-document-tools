"""Find chapter photographs, insert them into Word, or prepare optional guides.

The original course DOCX is never reformatted: insert mode only appends standalone
image paragraphs after selected headings. A single input DOCX returns a DOCX,
while multiple input chapters return a ZIP containing just the enhanced Word files.
Guide mode never returns modified DOCX.
External image providers are opt-in via server-side environment variables.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import quote, quote_plus, urlparse
import zipfile

import httpx
from PIL import Image, ImageDraw, ImageStat, ImageOps, UnidentifiedImageError
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph

from .zip_utils import normalise_zip_name, safe_zip_member
from .course_image_integrations import FreepikImages, OpenRouterScenes, freepik_connection
from .course_image_library import ImageLibrary, SavedImage
from .free_stock_sources import find_pixabay, find_openverse
from .course_visual_context import contextual_scene
from .course_image_relevance import RelevanceContext, lock_query, metadata_decision, find_domain
from .course_image_vision import OpenRouterVision

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_IMAGE_BYTES = 22 * 1024 * 1024
MAX_ARCHIVE_FILES = 160
SKIP_HEADING = re.compile(
    r"\b(summary|summaries|conclusions?|references?|bibliograph(?:y|ies)|"
    r"table of contents|contents|resources for further reference|"
    r"cover page|title page|acknowledgements?)\b", re.I
)
SKIP_ONLY_HEADING = re.compile(r"\b(learning outcomes?|learning objectives?|assessment criteria)\b", re.I)
SKIP_DOC = re.compile(r"\b(assignment[\s_-]*brief|written[\s_-]*assignment[\s_-]*template)\b", re.I)
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]+")
STOPWORDS = frozenset(
    "the a an and or for of to in on at with from by about into it their your how what "
    "course chapter unit module lesson level award diploma certificate qls rqf ncfe cache "
    "using use understanding introduction principles practice practices importance overview "
    "key types role roles study studying skills basic activity activities different and".split()
)


class CourseImagesError(ValueError):
    """User-visible bad input or missing image-provider configuration."""


@dataclass
class Chapter:
    filename: str
    payload: bytes
    number: str
    title: str
    relative_path: str = ""


@dataclass
class ImagePlan:
    paragraph_index: int
    heading: str
    search: str
    description: str
    location: str
    context: str = ""
    source: str = ""
    source_url: str = ""
    source_licence: str = ""
    source_author: str = ""

    @property
    def freepik_link(self) -> str:
        return "https://www.freepik.com/search?query=" + quote_plus(self.search.strip())

    @property
    def pexels_link(self) -> str:
        return "https://www.pexels.com/search/" + quote(self.search.strip(), safe="") + "/"

    @property
    def stocksnap_link(self) -> str:
        return "https://stocksnap.io/search/" + quote_plus(self.search.strip())

    @property
    def pixabay_link(self) -> str:
        return "https://pixabay.com/images/search/" + quote(self.search.strip(), safe="") + "/"


@dataclass
class Picture:
    payload: bytes
    source: str
    source_url: str = ""
    identifier: str = ""
    licence: str = ""
    author: str = ""
    reused: bool = False
    caption: str = ""
    relevance_score: int | None = None
    relevance_reason: str = ""
    verification_method: str = ""


@dataclass
class CourseImagesResult:
    filename: str
    payload: bytes
    details: dict


@dataclass
class Provider:
    """Try a relevant free stock photograph, then optionally generated imagery."""

    pexels_key: str = field(default_factory=lambda: os.getenv("PEXELS_API_KEY", "").strip())
    pixabay_key: str = field(default_factory=lambda: os.getenv("PIXABAY_API_KEY", "").strip())
    openverse_enabled: bool = field(default_factory=lambda: os.getenv("OPENVERSE_ENABLED", "true").lower() in {"1", "true", "yes"})
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    image_model: str = field(default_factory=lambda: os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare"))
    freepik_key: str = field(default_factory=lambda: freepik_connection()[0])
    freepik_model: str = field(default_factory=lambda: os.getenv("FREEPIK_IMAGE_MODEL", "flux-2-pro").strip())
    openrouter_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "").strip())
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip())
    stock_source: str = "mixed"
    reuse_existing: bool = True
    save_new_to_library: bool = True
    # Mixed mode must never spend Freepik credits without an explicit choice.
    allow_freepik: bool = False
    library: ImageLibrary = field(default_factory=ImageLibrary)
    library_hits: int = 0
    freepik_downloads: int = 0
    freepik_attempts: int = 0
    freepik_limit: int = field(default_factory=lambda: max(0, min(10, int(os.getenv("COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS", "1")))))
    freepik_attempt_limit: int = field(default_factory=lambda: max(0, min(10, int(os.getenv("COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS", "1")))))
    max_generations: int = field(default_factory=lambda: max(0, min(30, int(os.getenv("COURSE_IMAGE_MAX_GENERATIONS", "8")))))
    generations_used: int = 0
    refined_sections: int = 0
    used_photos: set[str] = field(default_factory=set)
    # Provider counts are actual source calls, not the number of headings checked.
    provider_attempts: dict[str, int] = field(default_factory=lambda: {
        "Pexels": 0, "Pixabay": 0, "Openverse": 0, "Freepik": 0,
    })
    provider_successes: dict[str, int] = field(default_factory=lambda: {
        "Pexels": 0, "Pixabay": 0, "Openverse": 0, "Freepik": 0,
    })
    warnings: list[str] = field(default_factory=list)
    # Set per-heading by the image-review workflow; never affects normal
    # formatter, cover or search-guide operations.
    active_context: RelevanceContext | None = None
    vision_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_VISION_MODEL", "openai/gpt-4o-mini").strip())
    relevance_rejections: int = 0
    vision_checks: int = 0
    vision_limit: int = field(default_factory=lambda: max(1, min(60, int(os.getenv("COURSE_IMAGE_MAX_VISION_CHECKS", "32")))))

    @property
    def available(self) -> bool:
        return bool(self.pexels_key or self.pixabay_key or self.openverse_enabled or self.freepik_key)

    def can_acquire(self, *, generate: bool) -> bool:
        source = "mixed" if self.stock_source == "auto" else self.stock_source
        if source == "generate":
            return False  # Paid AI generation deliberately disabled for this workflow.
        return bool(
            (source in {"mixed", "free", "pexels"} and self.pexels_key)
            or (source in {"mixed", "free", "pixabay"} and self.pixabay_key)
            or (source in {"mixed", "free", "openverse"} and self.openverse_enabled)
            or (source in {"mixed", "freepik"} and self.allow_freepik and self.freepik_key
                and self.freepik_limit > 0 and self.freepik_attempt_limit > 0)
        )

    def _freepik(self) -> FreepikImages:
        _, base, header = freepik_connection()
        return FreepikImages(self.freepik_key, base=base, header=header)

    def _remember(self, query: str, subject: str, picture: Picture, *, portrait: bool) -> None:
        if self.reuse_existing and self.save_new_to_library:
            self.library.save(
                query, subject,
                SavedImage(picture.payload, picture.source, picture.source_url, picture.identifier, picture.licence, picture.author),
                portrait=portrait,
            )

    def _metadata_gate(self, caption: str) -> bool:
        context = self.active_context
        if context is None:
            return True
        decision = metadata_decision(caption, context)
        # With vision enabled, empty or vague provider captions may be
        # evaluated using pixels. Explicit cross-discipline matches are never
        # rescued by a high generic search rank.
        if context.verify_pixels and decision.reason != "Corporate photograph does not show the required care setting":
            return True
        if not decision.eligible:
            self.relevance_rejections += 1
        return decision.eligible

    def _accept_picture(self, picture: Picture) -> bool:
        context = self.active_context
        if context is None:
            return True
        metadata = metadata_decision(picture.caption, context)
        if context.verify_pixels:
            if self.vision_checks >= self.vision_limit:
                self.relevance_rejections += 1
                return False
            self.vision_checks += 1
            result = OpenRouterVision(self.openrouter_key, self.vision_model).check(picture.payload, context)
            picture.verification_method = "visual"
            picture.relevance_score = result.score
            picture.relevance_reason = result.reason
            if not result.eligible:
                self.relevance_rejections += 1
                return False
            return True
        picture.verification_method = "metadata"
        picture.relevance_score = metadata.score
        picture.relevance_reason = metadata.reason
        if not metadata.eligible:
            self.relevance_rejections += 1
            return False
        return True

    def find(
        self, query: str, *, portrait: bool = False, generate: bool = True,
        prompt: str = "", subject: str = "",
    ) -> Picture | None:
        if self.active_context is not None:
            # Course discipline remains locked even when text-only query
            # refinement suggests a broad corporate/office search.
            query = lock_query(query, self.active_context.domain, self.active_context.heading)
        source = "mixed" if self.stock_source == "auto" else self.stock_source
        allow_free = source in {"mixed", "free", "pexels", "pixabay", "openverse"}
        if allow_free and self.reuse_existing:
            # Older library photos were not visually verified. In strict mode,
            # only re-use them when the user explicitly enables pixel checks.
            safe_reuse = self.active_context is None or self.active_context.verify_pixels
            saved = self.library.find(query, subject, portrait=portrait) if safe_reuse else None
            if saved and saved.identifier not in self.used_photos:
                candidate = Picture(saved.payload, saved.source, saved.source_url, saved.identifier,
                                    saved.licence, saved.author, reused=True)
                if self._accept_picture(candidate):
                    self.used_photos.add(saved.identifier)
                    self.library_hits += 1
                    return candidate
        if source in {"mixed", "free", "pexels"} and self.pexels_key:
            self.provider_attempts["Pexels"] += 1
            try:
                found = self._stock(query, portrait=portrait, subject=subject)
                if found:
                    self.provider_successes["Pexels"] += 1
                    self._remember(query, subject, found, portrait=portrait)
                    return found
            except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
                self.warnings.append(f"Pexels lookup was unsuccessful: {type(exc).__name__}.")
        if source in {"mixed", "free", "pixabay"} and self.pixabay_key:
            self.provider_attempts["Pixabay"] += 1
            try:
                result = find_pixabay(
                    self.pixabay_key, query, subject, self.used_photos,
                    portrait=portrait, normalise=lambda raw: _normalised_image(raw, portrait=portrait),
                    **({"metadata_gate": self._metadata_gate, "include_caption": True} if self.active_context else {}),
                )
                if result:
                    raw, page, identifier, licence, author = result[:5]
                    # Metadata filtering happens before download in the stock
                    # search, but vision can still reject the actual pixels.
                    found = Picture(raw, "Pixabay", page, identifier, licence, author,
                                    caption=result[5] if len(result) > 5 else "")
                    if self._accept_picture(found):
                        self.used_photos.add(identifier)
                        self.provider_successes["Pixabay"] += 1
                        self._remember(query, subject, found, portrait=portrait)
                        return found
                    self.used_photos.add(identifier)
            except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
                self.warnings.append(f"Pixabay lookup was unsuccessful: {type(exc).__name__}.")
        if source in {"mixed", "free", "openverse"} and self.openverse_enabled:
            self.provider_attempts["Openverse"] += 1
            try:
                result = find_openverse(
                    query, subject, self.used_photos,
                    portrait=portrait, normalise=lambda raw: _normalised_image(raw, portrait=portrait),
                    **({"metadata_gate": self._metadata_gate, "include_caption": True} if self.active_context else {}),
                )
                if result:
                    raw, page, identifier, licence, author = result[:5]
                    found = Picture(raw, "Openverse", page, identifier, licence, author,
                                    caption=result[5] if len(result) > 5 else "")
                    if self._accept_picture(found):
                        self.used_photos.add(identifier)
                        self.provider_successes["Openverse"] += 1
                        self._remember(query, subject, found, portrait=portrait)
                        return found
                    self.used_photos.add(identifier)
            except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
                self.warnings.append(f"Openverse lookup was unsuccessful: {type(exc).__name__}.")
        # Licensed Freepik stock is the final fallback. No AI generation call is made.
        if source in {"mixed", "freepik"} and self.allow_freepik and self.freepik_key:
            # Count attempts, not only successes; failed headings can cost too.
            if (self.freepik_downloads >= self.freepik_limit or
                    self.freepik_attempts >= self.freepik_attempt_limit):
                self.warnings.append("Freepik request budget reached; no further paid fallback attempted.")
                return None
            self.freepik_attempts += 1
            try:
                result = self._freepik().search_photo(
                    query, subject=subject, portrait=portrait, used=self.used_photos,
                    normalise=lambda raw: _normalised_image(raw, portrait=portrait),
                    max_candidates=1,  # Avoid several licensed downloads while searching one heading.
                    **({"metadata_gate": self._metadata_gate, "include_caption": True} if self.active_context else {}),
                )
                if result:
                    raw, page, identifier, licence, author = result[:5]
                    self.freepik_downloads += 1
                    # Freepik metadata is checked in its search API separately;
                    # pixels are checked here if the user opted in. Never
                    # count a rejected image as a successful recommendation.
                    candidate = Picture(raw, "Freepik", page, identifier, licence, author,
                                        caption=result[5] if len(result) > 5 else "")
                    if self._accept_picture(candidate):
                        self.used_photos.add(identifier)
                        self.provider_successes["Freepik"] += 1
                        return candidate
                    self.used_photos.add(identifier)
            except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
                self.warnings.append(f"Freepik stock lookup was unsuccessful: {type(exc).__name__}.")
        return None

    def refine_plans(self, course: str, chapter: str, plans: list[ImagePlan], chapter_overview: str = "") -> None:
        """Use OpenRouter only when explicitly requested; preserve original headings."""
        if not self.openrouter_key:
            self.warnings.append("OpenRouter refinement requested but OPENROUTER_API_KEY is not configured; used local scene suggestions.")
            return
        try:
            planner = OpenRouterScenes(self.openrouter_key, self.openrouter_model)
            for start in range(0, len(plans), 10):
                chunk = plans[start:start + 10]
                items = [
                    {"id": i, "heading": plan.heading[:160], "excerpt": plan.context[:750]}
                    for i, plan in enumerate(chunk)
                ]
                proposed = planner.improve(course, chapter, items, chapter_overview=chapter_overview[:1600])
                for suggestion in proposed:
                    if not isinstance(suggestion, dict):
                        continue
                    index = suggestion.get("id")
                    if type(index) is not int or not (0 <= index < len(chunk)):
                        continue
                    search = suggestion.get("search")
                    photo = suggestion.get("photo")
                    if not isinstance(search, str) or not isinstance(photo, str):
                        continue
                    search = " ".join(search.split())[:110]
                    photo = " ".join(photo.split())[:450]
                    # A photo can represent the heading without repeating its
                    # academic words. Require overlap with the heading OR
                    # supporting paragraph OR initial contextual scene, while
                    # rejecting unrelated subjects (e.g. jazz for consent).
                    grounding = set(_tokens(
                        chunk[index].heading + " " + chunk[index].context + " " + chunk[index].search
                    )) - {"professional", "workplace", "people", "person", "realistic", "natural", "photo", "scene"}
                    proposal = set(_tokens(search + " " + photo))
                    if not (3 <= len(search) <= 110 and 25 <= len(photo) <= 450):
                        continue
                    if grounding and not (grounding & proposal):
                        continue
                    if any(mark in (search + " " + photo).lower() for mark in ("http:", "https:", "<script")):
                        continue
                    chunk[index].search = search
                    chunk[index].description = photo
                    self.refined_sections += 1
        except (httpx.HTTPError, ValueError, OSError, TypeError, KeyError) as exc:
            self.warnings.append(f"OpenRouter prompt refinement was unsuccessful ({type(exc).__name__}); used local suggestions.")

    def _stock(self, query: str, *, portrait: bool, subject: str = "") -> Picture | None:
        headers = {"Authorization": self.pexels_key}
        with httpx.Client(timeout=22, follow_redirects=False) as client:
            response = client.get(
                "https://api.pexels.com/v1/search",
                params={"query": query[:110], "orientation": "portrait" if portrait else "landscape", "per_page": 20},
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
            photos = result.get("photos") or []
            # Pexels alt text can be absent; in strict mode such pictures are
            # skipped unless optional pixel verification is enabled. Provider
            # relevance ranking alone is not evidence of a care setting.
            tokens = set(_tokens(query))
            ranked: list[tuple[int, dict]] = []
            for order, photo in enumerate(photos):
                identifier = str(photo.get("id", ""))
                if not identifier or f"pexels:{identifier}" in self.used_photos:
                    continue
                caption = " ".join((str(photo.get("alt") or ""),
                                    str(photo.get("url") or "").replace("-", " ")))
                if not self._metadata_gate(caption):
                    continue
                overlap = len(tokens & set(_tokens(caption)))
                # Prefer matches, then preserve Pexels' own relevance order.
                ranked.append((overlap * 100 - order, photo))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            for _, photo in ranked[:5]:
                src = photo.get("src") or {}
                url = src.get("large2x") or src.get("large") or src.get("original")
                if not url or urlparse(url).scheme != "https" or urlparse(url).hostname != "images.pexels.com":
                    continue
                try:
                    with client.stream("GET", url) as stream:
                        stream.raise_for_status()
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in stream.iter_bytes():
                            total += len(chunk)
                            if total > MAX_IMAGE_BYTES:
                                raise ValueError("Image exceeds the upload limit")
                            chunks.append(chunk)
                    payload = _normalised_image(b"".join(chunks), portrait=portrait)
                except (httpx.HTTPError, ValueError, OSError, UnidentifiedImageError):
                    continue
                identifier = str(photo["id"])
                candidate = Picture(payload, "Pexels", str(photo.get("url", "")),
                                    f"pexels:{identifier}", "Pexels License",
                                    str(photo.get("photographer") or "")[:100], caption=" ".join((
                                        str(photo.get("alt") or ""),
                                        str(photo.get("url") or "").replace("-", " "))))
                if not self._accept_picture(candidate):
                    self.used_photos.add(candidate.identifier)
                    continue
                self.used_photos.add(candidate.identifier)
                return candidate
        return None

    def _generate(self, prompt: str, *, portrait: bool) -> Picture:
        request = {
            "model": self.image_model,
            "prompt": (
                "Professional, candid, ultra-photorealistic educational stock photograph. "
                "Authentic setting, realistic people and hands, natural lighting, credible "
                "equipment and details, visually distinct photographic composition. "
                "No text, writing, logos, watermarks, diagrams, synthetic-looking faces or stylisation. "
                + prompt[:2200]
            ),
            "size": "1024x1536" if portrait else "1536x1024",
            "quality": "medium",
            "output_format": "png",
            "n": 1,
        }
        with httpx.Client(timeout=180) as client:
            response = client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {self.openai_key}"},
                json=request,
            )
            response.raise_for_status()
            items = response.json().get("data") or []
            if not items or not items[0].get("b64_json"):
                raise ValueError("Image API returned no image")
            encoded = items[0]["b64_json"]
            if len(encoded) > 4 * MAX_IMAGE_BYTES // 3 + 100:
                raise ValueError("Image API payload too large")
            payload = base64.b64decode(encoded, validate=True)
            return Picture(_normalised_image(payload, portrait=portrait), "AI-generated")


def _normalised_image(raw: bytes, *, portrait: bool) -> bytes:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Invalid or oversized image")
    with Image.open(BytesIO(raw)) as source:
        source.load()
        if source.width < 450 or source.height < 300:
            raise ValueError("Image resolution too small")
        if source.width * source.height > 65_000_000:
            raise ValueError("Image resolution too large")
        image = ImageOps.exif_transpose(source).convert("RGB")
        if portrait:
            image.thumbnail((1450, 2100), Image.Resampling.LANCZOS)
        else:
            # Visual assets below headings should always be landscape.
            image = ImageOps.fit(image, (1300, 812), method=Image.Resampling.LANCZOS)
        target = BytesIO()
        image.save(target, format="JPEG", quality=89, optimize=True)
        return target.getvalue()


def _tokens(value: str) -> list[str]:
    return [t.lower().strip("-'") for t in TOKEN_RE.findall(value) if len(t) > 2 and t.lower() not in STOPWORDS]


def _safe_name(filename: str) -> str:
    return re.sub(r"[^a-zA-Z0-9 _.-]", "_", filename).strip(" .")[:120] or "Course"


def _number(filename: str, index: int) -> str:
    stem = Path(filename).stem
    match = re.search(r"(?:chapter|unit|module)\s*(\d+[a-z]?)\b", stem, re.I) or re.match(r"^(\d+[a-z]?)\b", stem, re.I)
    return match.group(1) if match else f"{index:02d}"


def _chapter_title(filename: str) -> str:
    return " ".join(re.sub(r"[_-]+", " ", Path(filename).stem).split())


def _read_chapters(raw: bytes, filename: str) -> tuple[list[Chapter], list[tuple[str, bytes]]]:
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise CourseImagesError("DOCX is too large (25 MB maximum).")
        return [Chapter(filename, raw, _number(filename, 1), _chapter_title(filename))], []
    if ext != ".zip":
        raise CourseImagesError("Upload a DOCX document or a ZIP containing Word documents.")
    chapters: list[Chapter] = []
    covers: list[tuple[str, bytes]] = []
    total_size = 0
    seen_document_paths: set[str] = set()
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            if len(archive.infolist()) > MAX_ARCHIVE_FILES:
                raise CourseImagesError("Archive contains too many entries.")
            for info in archive.infolist():
                if info.is_dir() or not safe_zip_member(info.filename):
                    continue
                if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    continue
                relative_path = normalise_zip_name(info.filename)
                name = PurePosixPath(relative_path).name
                extension = Path(name).suffix.lower()
                if extension not in {".docx", ".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                if info.file_size > MAX_DOCUMENT_BYTES:
                    raise CourseImagesError(f"Archive entry is too large: {name}")
                total_size += info.file_size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise CourseImagesError("Archive contains too much uncompressed data.")
                if extension == ".docx" and not SKIP_DOC.search(_chapter_title(name)):
                    if relative_path.lower() in seen_document_paths:
                        continue
                    seen_document_paths.add(relative_path.lower())
                    chapters.append(Chapter(name, archive.read(info), _number(name, len(chapters) + 1), _chapter_title(name), relative_path))
                elif extension != ".docx" and re.search(r"\bcover\b", re.sub(r"[_-]", " ", Path(name).stem), re.I):
                    covers.append((name, archive.read(info)))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise CourseImagesError("The uploaded ZIP is not readable.") from exc
    if not chapters:
        raise CourseImagesError("No chapter DOCX files were found in the uploaded archive.")
    return chapters, covers


def _picture_after_heading(paragraphs, index: int) -> bool:
    # Check the paragraph directly after the heading and a following spacer,
    # but never a later section or unrelated paragraph.
    for p in paragraphs[index + 1:index + 3]:
        if _heading_level(p) is not None:
            break
        if p._p.xpath(".//w:drawing") or p._p.xpath(".//w:pict"):
            return True
        if p.text.strip():
            break
    return False


def _heading_level(paragraph: Paragraph) -> int | None:
    """Recognise Word headings, custom headings and carefully numbered bold titles.

    Some supplied course files use a custom style based on Heading 2 or
    direct-format e.g. '2.3 Safe handling' in Normal. The earlier exact-style
    check silently reported zero eligible headings for these documents.
    """
    style = paragraph.style
    visited: set[str] = set()
    for _ in range(8):
        if style is None or style.style_id in visited:
            break
        visited.add(style.style_id)
        name = (style.name or "").strip()
        if re.match(r"^toc\b", name, re.I):
            return None
        match = re.search(r"\bheading\s*([1-6])\b", name, re.I)
        if match:
            return int(match.group(1))
        for outline in style.element.xpath("./w:pPr/w:outlineLvl"):
            level = outline.get(qn("w:val"))
            if level is not None and level.isdigit() and int(level) < 6:
                return int(level) + 1
        style = style.base_style

    for outline in paragraph._p.xpath("./w:pPr/w:outlineLvl"):
        level = outline.get(qn("w:val"))
        if level is not None and level.isdigit() and int(level) < 6:
            return int(level) + 1

    text = " ".join(paragraph.text.split())
    numbered = re.match(
        r"^(?:(\d{1,2})\.\d{1,2}(?:\.\d{1,2})?\s+|"
        r"(?:module|unit|chapter)\s*0?\d{1,3}\s*[:.\-]\s+)", text, re.I,
    )
    if not numbered or len(text) > 130:
        return None
    real_runs = [run for run in paragraph.runs if run.text.strip()]
    if not real_runs:
        return None
    bold_length = sum(len(run.text) for run in real_runs if run.bold or (run.style and run.style.font.bold))
    text_length = sum(len(run.text) for run in real_runs)
    if bold_length < 0.6 * max(1, text_length):
        return None
    if numbered.group(1):
        return min(6, 1 + text.count(".", 0, numbered.end()))
    return 1


def _conflict_visual(heading: str, context: str) -> tuple[str, str] | None:
    """Translate abstract professional conflict concepts into visible actions.

    Source headings remain unchanged. Query terms describe a *photograph*, not
    a paraphrase of learning outcomes or the full academic heading.
    """
    if not re.search(r"conflict|disagreement|dispute|mediat|negotiat|resolution", context, re.I):
        return None
    options = (
        (r"avoid|withdraw|delaying|pause|not acting", "thoughtful employee office conversation",
         "a professional listening carefully before responding to a workplace disagreement"),
        (r"compet|direct|authority|assertiv|decisive", "manager leading office meeting",
         "a manager giving clear instructions to colleagues during a workplace discussion"),
        (r"collaborat|together|joint|underlying needs", "colleagues solving problem meeting",
         "a small team working together around a table to resolve an issue"),
        (r"negotiat|compromis|agreement|mediat", "business negotiation discussion",
         "two professionals discussing a possible workplace agreement"),
        (r"mistake|error|misunderstand|pitfall", "colleagues workplace disagreement",
         "two colleagues discussing a workplace disagreement respectfully"),
        (r"select|choos|proportionat|appropriate approach|decision", "business team decision meeting",
         "a team leader and colleagues deciding how to handle a workplace situation"),
        (r"conflict|disagreement|resolution|management", "office mediation discussion",
         "colleagues engaging in a calm, professional workplace conversation"),
    )
    for expression, search, action in options:
        if re.search(expression, heading, re.I):
            return search, action
    return "office team discussion", "work colleagues discussing how to handle a disagreement"


def _scene_context(course: str, heading: str, excerpt: str) -> tuple[str, str]:
    combined = (course + " " + heading).lower()
    if any(w in combined for w in ("confidential", "information", "data protection", "records")):
        setting = "care or office information-handling workplace" if "care" in combined else "professional office"
        action = "a professional working carefully with secure records or workplace information"
        stock_tags = "secure records workplace"
    elif any(w in combined for w in ("food", "hygiene", "catering", "kitchen")):
        setting = "professional kitchen or food-preparation area"
        action = "a catering or care professional demonstrating a relevant food-safety activity"
        stock_tags = "professional food safety kitchen"
    elif any(w in combined for w in ("care", "dementia", "health", "patient", "nurs")):
        setting = "authentic health or social care setting"
        action = "a professional supporting a person or demonstrating a relevant care activity"
        stock_tags = "healthcare staff care"
    elif any(w in combined for w in ("child", "teaching", "education", "school", "learning")):
        setting = "authentic classroom or early-years learning setting"
        action = "educators or learners participating in a relevant activity"
        stock_tags = "education classroom teacher"
    elif any(w in combined for w in ("management", "leadership", "business", "finance", "customer")):
        setting = "credible UK business or workplace environment"
        action = "professionals undertaking a relevant everyday business activity"
        stock_tags = "business office meeting"
    else:
        setting = "realistic workplace appropriate to the course topic"
        action = "people, activities or equipment demonstrating the specific heading"
        stock_tags = "professional work"
    # Conflict handling is a *course/topic* decision. A passing mention of a
    # disagreement in the body of a safeguarding or HR section must not force
    # an unrelated generic office-mediation image.
    conflict = _conflict_visual(heading, combined)
    if conflict and not re.search(r"safeguard|care settings|social care|child|healthcare|nursery|school|eyfs", course, re.I):
        search, action = conflict
        description = (
            f"An ultra-realistic, natural landscape photograph for '{heading}', "
            f"showing {action}. Credible workplace and believable human interaction. "
            "No promotional text, watermarks, or legible private data."
        )
        return search, description
    # Use the course's professional discipline to ground each image, instead
    # of searching academic headings as if they were stock-photo keywords.
    specific = contextual_scene(course, heading, excerpt)
    if specific is not None:
        return specific.search, specific.description
    # Tie each shot to the heading's meaning, not just the course. The
    # surrounding chapter text is used as context, never altered or invented.
    heading_lower = heading.lower()
    scenes = (
        (r"storage|storing|filing|access|records?", "a staff member placing a confidential folder in a locked cabinet beside a secure workstation"),
        (r"requests?|responding|enquiries|questions?", "two staff discussing an information request discreetly at a workplace reception desk"),
        (r"consent|permission|choices?", "a care professional explaining paperwork to an adult in a private consultation room"),
        (r"confidential|privacy|data protect|security|password", "a worker protecting private records at a secure computer station with no readable personal information"),
        (r"handwashing|washing|hygiene|infection", "a professional using a handwashing sink and appropriate hygiene supplies"),
        (r"cook|temperature|heating|chill|storage of food", "a trained kitchen worker checking food with an appropriate digital probe thermometer"),
        (r"risk|hazard|safety check|assess", "a workplace supervisor checking relevant equipment against a paper safety checklist"),
        (r"equality|diversity|inclusion", "colleagues of different backgrounds working collaboratively in a respectful professional setting"),
        (r"leadership|teamwork|team management|delegat", "a manager coordinating colleagues during a realistic planning discussion"),
        (r"review|audit|monitor|evaluat", "a supervisor reviewing an activity checklist alongside a colleague"),
        (r"training|teach|learn|demonstrat", "a professional demonstrating a relevant practical skill to a colleague"),
    )
    for pattern, specific_action in scenes:
        if re.search(pattern, heading_lower):
            action = specific_action
            break
    # Search for something a camera could actually photograph, rather than
    # concatenating 8+ abstract course/assessment terms into one API query.
    # All matches still require human review in the default preview workflow.
    photo_subjects = (
        (r"medicat|medicin|prescri|pharmac|\bdos(e|age)\b|\bprn\b", "nurse medication elderly care"),
        (r"dementia|alzheimer|cognit", "caregiver elderly person care home"),
        (r"\bpain\b|symptom|physical health", "nurse patient consultation"),
        (r"confidential|privacy|records|document|filing", "office secure files documents"),
        (r"food|cater|kitchen", "food preparation professional kitchen"),
        (r"hygiene|infection|handwash", "healthcare worker washing hands"),
        (r"risk|hazard|safety", "workplace safety inspection"),
        (r"teach|student|pupil|child|learning", "teacher students classroom"),
        (r"account|finance|budget|cost", "accountant calculator office"),
        (r"leadership|teamwork|management|delegat", "colleagues office team meeting"),
        (r"project|planning|schedule", "project planning team office"),
        (r"customer|service|reception", "customer service reception desk"),
    )
    query = next((photo_query for pattern, photo_query in photo_subjects
                  if re.search(pattern, heading.lower(), re.I)), "")
    if not query:
        # Prefer a broad observable photograph when an abstract heading has
        # no subject-specific scene. Academic wording is not a stock-photo tag.
        heading_tokens = [token for token in _tokens(heading)
                          if token not in {"when", "required", "would", "could", "selected", "right", "approach", "outcomes", "method"}]
        topic_tokens = heading_tokens[:1] if heading_tokens else []
        query = " ".join(dict.fromkeys(_tokens(stock_tags)[:3] + topic_tokens))[:80]
    if not query:
        query = stock_tags
    description = (
        f"An ultra-realistic landscape photograph for the section '{heading}' "
        f"in a {setting}, showing {action}. Use believable people, activities and equipment "
        "in a natural environment, with no promotional text, watermarks or legible private data."
    )
    return query, description


def _chapter_overview(document: Document) -> str:
    """Scan the *whole* chapter, retaining a bounded cross-section of its topic.

    Reading body text matters: a heading alone may be abstract. The sampled
    passages are used for subject classification, never sent to a stock-photo
    provider as a literal search phrase.
    """
    body: list[str] = []
    excluded_level: int | None = None
    for p in document.paragraphs:
        phrase = " ".join(p.text.split())
        if not phrase:
            continue
        level = _heading_level(p)
        if level is not None:
            if SKIP_HEADING.search(phrase):
                excluded_level = level
            elif excluded_level is not None and level <= excluded_level:
                excluded_level = None
            continue
        if excluded_level is None and len(phrase) > 30:
            body.append(phrase[:350])
    if not body:
        return ""
    # Include intro, intermediate concepts and final teaching content, rather
    # than relying only on the first paragraph of the first section.
    picks = list(dict.fromkeys([0, 1, len(body)//3, len(body)//2, 2*len(body)//3, len(body)-1]))
    return " ".join(body[i] for i in picks if 0 <= i < len(body))[:1600]


def _plans(document: Document, course_title: str) -> tuple[list[ImagePlan], int]:
    paragraphs = document.paragraphs
    chapter_overview = _chapter_overview(document)
    eligible: list[ImagePlan] = []
    excluded_level: int | None = None
    for i, p in enumerate(paragraphs):
        text = " ".join(p.text.split())
        if not text:
            continue
        level = _heading_level(p)
        if level is None:
            continue
        if SKIP_ONLY_HEADING.search(text):
            continue
        if SKIP_HEADING.search(text):
            excluded_level = level
            continue
        if excluded_level is not None:
            if level > excluded_level:
                continue
            excluded_level = None
        if _picture_after_heading(paragraphs, i):
            continue
        context_items = []
        for following in paragraphs[i + 1:i + 13]:
            if _heading_level(following) is not None:
                break
            if following.text.strip():
                context_items.append(" ".join(following.text.split()))
            if sum(len(x) for x in context_items) >= 1000:
                break
        excerpt = " ".join(context_items)[:1100]
        # Heading-only chapter dividers have no instructional content under
        # them and should not receive filler or decorative photography.
        if not excerpt:
            continue
        query, description = _scene_context(course_title, text, excerpt)
        # If the supplied filename has no recognisable discipline, the chapter
        # overview can still identify it without changing the original title.
        if not contextual_scene(course_title, text, excerpt) and chapter_overview:
            subject = contextual_scene("Unit", text, excerpt, chapter_overview)
            if subject:
                query, description = subject.search, subject.description
        eligible.append(ImagePlan(i, text, query, description, f"Immediately below the heading '{text}' in the original Word document.", excerpt))
    count = math.ceil(len(eligible) * 0.5)
    if not count:
        return [], len(eligible)
    # Approximately half the relevant headings, spread through the chapter.
    # Prefer distinctive scenes over several near-identical stock searches.
    chosen: set[int] = set()
    used_queries: set[str] = set()
    for index in range(count):
        middle = min(len(eligible) - 1, int((index + 0.5) * len(eligible) / count))
        options = sorted((j for j in range(len(eligible)) if j not in chosen),
                         key=lambda j: (eligible[j].search in used_queries,
                                        abs(j - middle), j))
        if options:
            selected_index = options[0]
            chosen.add(selected_index)
            used_queries.add(eligible[selected_index].search)
    return [eligible[j] for j in sorted(chosen)], len(eligible)


def _hyperlink(paragraph: Paragraph, label: str, url: str) -> None:
    part = paragraph.part
    rid = part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "146C89")
    properties.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(underline)
    run.append(properties)
    text = OxmlElement("w:t")
    text.text = label
    run.append(text)
    link.append(run)
    paragraph._p.append(link)


def _write_guide(course_title: str, chapter: Chapter, plans: list[ImagePlan], *, freepik_links: bool = False) -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.86)
    section.right_margin = Inches(0.86)
    normal = doc.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10)
    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(18)
    heading_run = title.add_run("Matching Image Search Guide")
    heading_run.bold = True
    heading_run.font.size = Pt(22)
    heading_run.font.color.rgb = RGBColor(18, 88, 108)
    for label, value in (
        ("Course / Module Title", course_title),
        ("Chapter", f"{chapter.number} – {chapter.title}"),
        ("Recommended Image Placements", str(len(plans))),
    ):
        p = doc.add_paragraph()
        p.add_run(label + ": ").bold = True
        p.add_run(value)
    if not plans:
        doc.add_paragraph("No eligible headings without images were identified in this chapter.")
    for index, plan in enumerate(plans, start=1):
        h = doc.add_heading(f"{index:02d} – {plan.heading}", level=2)
        h.paragraph_format.keep_with_next = True
        for label, value in (("Photo", plan.description), ("Place", plan.location)):
            p = doc.add_paragraph()
            p.add_run(label + ": ").bold = True
            p.add_run(value)
        p = doc.add_paragraph()
        p.add_run("Links: ").bold = True
        _hyperlink(p, "Pexels – search photos", plan.pexels_link)
        p = doc.add_paragraph()
        _hyperlink(p, "StockSnap – search photos", plan.stocksnap_link)
        if freepik_links:
            p = doc.add_paragraph()
            _hyperlink(p, "Freepik – check licence before use", plan.freepik_link)
        if plan.source_licence:
            p = doc.add_paragraph()
            p.add_run("Stock licence: ").bold = True
            p.add_run(plan.source_licence + "; review publication and attribution terms.")
            if plan.source_author:
                p.add_run(" Credit: " + plan.source_author + ".")
        if plan.source_url:
            p = doc.add_paragraph()
            p.add_run("Selected photograph: ").bold = True
            _hyperlink(p, plan.source, plan.source_url)
        elif plan.source:
            p = doc.add_paragraph()
            p.add_run("Image used: ").bold = True
            p.add_run(plan.source)
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def _insert_images(document: Document, plans: list[ImagePlan], images: dict[int, Picture]) -> bytes:
    # Snapshot the original paragraphs first. document.paragraphs is dynamic;
    # inserted picture paragraphs would otherwise shift later heading indices.
    original_paragraphs = list(document.paragraphs)
    for plan in plans:
        photo = images.get(plan.paragraph_index)
        if not photo:
            continue
        source = original_paragraphs[plan.paragraph_index]
        new_xml = OxmlElement("w:p")
        source._p.addnext(new_xml)
        p = Paragraph(new_xml, source._parent)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(10)
        p.paragraph_format.keep_together = True
        # Use the actual page's text width. Avoid changing margins or styles.
        section = document.sections[0]
        max_width = section.page_width - section.left_margin - section.right_margin
        width_inches = min(5.8, max_width / Inches(1))
        p.add_run().add_picture(BytesIO(photo.payload), width=Inches(width_inches))
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _compose_cover(picture: bytes) -> bytes:
    with Image.open(BytesIO(picture)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        canvas = Image.new("RGB", (1240, 1754), (18, 22, 27))
        top = 1280  # 73% photographic area, 27% charcoal title panel.
        visual = ImageOps.fit(image, (1240, top + 2), method=Image.Resampling.LANCZOS)
        canvas.paste(visual, (0, 0))
        gradient = Image.new("RGBA", (1240, 90), (0, 0, 0, 0))
        mask = Image.new("L", (1, 90))
        for y in range(90):
            mask.putpixel((0, y), int((y / 89) ** 1.6 * 95))
        overlay = Image.new("RGBA", (1240, 90), (0, 0, 0, 220))
        canvas.paste(overlay, (0, top - 90), mask.resize((1240, 90)))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, top - 2, 1240, top + 3), fill=(196, 162, 77))
        output = BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _correct_existing_cover(picture: bytes) -> tuple[bytes | None, str]:
    """Correct a solid lower coloured title panel without redrawing the photo."""
    try:
        with Image.open(BytesIO(picture)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            width, height = image.size
            if width < 400 or height < 550 or height <= width:
                return None, "Cover was not portrait; existing photo was not altered."
            bottom = image.crop((int(width * .13), int(height * .86), int(width * .87), int(height * .95)))
            stat = ImageStat.Stat(bottom)
            if max(stat.mean) < 86 and max(stat.stddev) < 35:
                return picture, "Existing charcoal cover retained unchanged."
            # Only edit covers where there is clearly a reasonably uniform
            # lower colour panel. Otherwise subjects/photo might be obscured.
            if max(stat.stddev) > 27:
                return None, "Existing cover has no reliably detected solid lower panel; left unchanged."
            panel_start = int(height * .74)
            # Find a level above the sampled panel that remains similarly flat
            # in colour. This keeps every pixel of the original photo intact.
            bottom_colour = stat.mean
            for y in range(int(height * .61), int(height * .83), max(1, height // 100)):
                sample = ImageStat.Stat(image.crop((int(width * .15), y, int(width * .85), min(height, y + max(4, height // 80)))))
                if max(sample.stddev) < 28 and sum(abs(a - b) for a, b in zip(sample.mean, bottom_colour)) < 55:
                    panel_start = y
                    break
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, panel_start, width, height), fill=(18, 22, 27))
            draw.rectangle((0, panel_start - 2, width, panel_start + 2), fill=(196, 162, 77))
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue(), "Existing coloured panel changed to charcoal; upper photograph preserved."
    except (OSError, UnidentifiedImageError):
        return None, "Existing cover image could not be read."


def _cover_for_course(
    course_title: str, supplied: tuple[str, bytes] | None, provider: Provider, *, generate: bool
) -> tuple[bytes | None, str]:
    if supplied:
        corrected, description = _correct_existing_cover(supplied[1])
        if corrected:
            return corrected, description
        # Don't crop or redraw a supplied cover whose lower panel cannot
        # safely be identified: that could erase a person or existing artwork.
        return None, description
    search = " ".join(_tokens(course_title)[:6]) + " workplace professional"
    photo = provider.find(
        search.strip(), portrait=True, generate=generate, subject=course_title,
        prompt=f"Portrait cover photograph for UK training course titled '{course_title}'. "
        "Show a directly relevant professional scene. Main subjects in upper three quarters "
        "with a natural photographic composition, no titles or graphics.",
    )
    if not photo:
        return None, "No relevant cover photograph was available; cover not fabricated."
    note = f"Cover photograph: {photo.source}" + (f" ({photo.source_url})" if photo.source_url else "")
    if photo.licence:
        note += f"; licence metadata: {photo.licence} (verify rights and attribution before publication)"
    if photo.author:
        note += f"; author: {photo.author}"
    return _compose_cover(photo.payload), note


def build_course_image_package(
    raw: bytes, filename: str, *, course_name: str = "", mode: str = "guides",
    generate_missing: bool = True, cover_upload: bytes | None = None,
    image_source: str = "mixed", use_openrouter: bool = False, reuse_existing: bool = True,
    allow_freepik: bool = False,
    provider: Provider | None = None,
) -> CourseImagesResult:
    """Return edited Word documents directly, or separate guide+cover assets."""
    if mode not in {"guides", "insert"}:
        raise CourseImagesError("Choose search guides or automatic image insertion.")
    if image_source not in {"mixed", "auto", "free", "pexels", "pixabay", "openverse", "freepik", "generate"}:
        raise CourseImagesError("Choose a valid stock image or generation source.")
    if not raw:
        raise CourseImagesError("The uploaded document is empty.")
    chapters, provided_covers = _read_chapters(raw, filename)
    if cover_upload:
        if len(cover_upload) > MAX_IMAGE_BYTES:
            raise CourseImagesError("Cover upload is too large.")
        provided_covers = [("uploaded_cover.png", cover_upload)]
    provider = provider if provider is not None else Provider()
    provider.stock_source = image_source
    provider.reuse_existing = reuse_existing
    provider.allow_freepik = allow_freepik
    if image_source == "generate":
        raise CourseImagesError("AI image generation is currently disabled. Select a stock photo source.")
    if image_source == "freepik" and not allow_freepik and mode == "insert":
        raise CourseImagesError("Enable Freepik stock access explicitly, or choose free sources only.")
    generate_missing = False
    if mode == "insert" and not provider.can_acquire(generate=generate_missing):
        raise CourseImagesError(
            "No compatible image source is configured. Provide PEXELS_API_KEY or PIXABAY_API_KEY "
            "(Openverse can also be enabled), or configure FREEPIK_API_KEY for licensed stock fallback."
        )

    enhanced: list[tuple[str, bytes]] = []
    guides: list[tuple[str, bytes]] = []
    cover_assets: list[tuple[str, bytes]] = []
    cover_sources: list[dict] = []
    warnings: list[str] = []
    photos_inserted = 0
    total_recommended = 0
    sources: list[dict] = []
    used_guide_names: set[str] = set()
    successful_chapters = 0
    course_title = course_name.strip() or (
        _chapter_title(filename) if Path(filename).suffix.lower() == ".zip" else chapters[0].title
    )
    for chapter in chapters:
        try:
            document = Document(BytesIO(chapter.payload))
            overview = _chapter_overview(document)
            chapter_domain = find_domain(course_title, chapter.title, overview)
            # Keep the older direct-insert API compatible, but apply the same
            # strict provider relevance gate used by the review workflow.
            scene_course = {
                "adult_safeguarding": "social care", "child_safeguarding": "child safeguarding",
                "healthcare": "healthcare", "education": "education", "management": "business management",
            }.get(chapter_domain, "")
            plans, eligible_count = _plans(document, f"{scene_course} {course_title} {chapter.title}")
            if use_openrouter and plans:
                provider.refine_plans(course_title, chapter.title, plans, chapter_overview=overview)
            total_recommended += len(plans)
            acquired: dict[int, Picture] = {}
            if mode == "insert":
                for plan in plans:
                    provider.active_context = RelevanceContext(
                        domain=chapter_domain, course=course_title, heading=plan.heading,
                        intended_scene=plan.description,
                        query=lock_query(plan.search, chapter_domain, plan.heading),
                        mode="strict", verify_pixels=False,
                    )
                    photo = provider.find(
                        provider.active_context.query, generate=generate_missing, subject=plan.heading,
                        prompt=f"UK educational chapter '{chapter.title}', course '{course_title}', "
                        f"heading '{plan.heading}'. {plan.description}. "
                        f"Supporting passage: {plan.context[:340]}",
                    )
                    if photo:
                        acquired[plan.paragraph_index] = photo
                        plan.source = photo.source
                        plan.source_url = photo.source_url
                        plan.source_licence = photo.licence
                        plan.source_author = photo.author
                        sources.append({
                            "chapter": chapter.filename, "heading": plan.heading,
                            "provider": photo.source, "source_url": photo.source_url,
                            "licence": photo.licence, "author": photo.author, "reused": photo.reused,
                        })
                    else:
                        warnings.append(f"{chapter.filename}: no suitable image found for '{plan.heading}'.")
                photos_inserted += len(acquired)
                enhanced.append((f"{Path(chapter.filename).stem}_with_images.docx", _insert_images(document, plans, acquired)))
                provider.active_context = None
            else:
                name = f"{chapter.number}_Matching_Image_Search_Guide.docx"
                if name.lower() in used_guide_names:
                    name = f"{chapter.number}_{len(guides)+1}_Matching_Image_Search_Guide.docx"
                used_guide_names.add(name.lower())
                guides.append((name, _write_guide(
                    course_title, chapter, plans,
                    freepik_links=bool(provider.freepik_key and image_source in {"auto", "freepik"}),
                )))
            successful_chapters += 1
            if not plans:
                warnings.append(f"{chapter.filename}: no eligible headings without an existing image were found.")
        except Exception as exc:
            provider.active_context = None
            # Keep all other chapters progressing without claiming this one
            # succeeded. Includes malformed DOCX payloads.
            warnings.append(f"{chapter.filename}: could not be processed ({type(exc).__name__}).")
            if mode == "insert":
                enhanced.append((f"{Path(chapter.filename).stem}_unchanged.docx", chapter.payload))

    # Covers and guides are deliberately not generated during insertion: they
    # are not part of the requested Word download and can incur API charges.
    if mode == "guides":
        # One shared course cover for one course, rather than duplicates.
        cover_targets = (
            [course_title]
            if course_name.strip() or len(chapters) == 1 or len(provided_covers) <= 1
            else [chapter.title for chapter in chapters]
        )
        for title in cover_targets:
            supplied = None
            if provided_covers:
                if len(provided_covers) == 1:
                    supplied = provided_covers[0]
                else:
                    supplied = next((item for item in provided_covers if _tokens(title)[-1:] and _tokens(title)[-1] in item[0].lower()), None)
            cover, note = _cover_for_course(title, supplied, provider, generate=generate_missing)
            if cover:
                cover_assets.append((f"{_safe_name(title)}_cover.png", cover))
                cover_sources.append({"cover": title, "source_note": note})
            else:
                warnings.append(f"{title}: {note}")

    if not successful_chapters:
        raise CourseImagesError("None of the chapter documents could be read as Word files.")
    warnings.extend(provider.warnings)
    report = {
        "chapters_processed": successful_chapters,
        "guides_created": len(guides),
        "documents_with_images": len(enhanced),
        "image_recommendations": total_recommended,
        "photos_inserted": photos_inserted,
        "covers_prepared": len(cover_assets),
        "mode": mode,
        "image_source": image_source,
        "openrouter_refined_sections": provider.refined_sections,
        "generation_attempts": provider.generations_used,
        "freepik_stock_downloads": provider.freepik_downloads,
        "freepik_search_attempts": provider.freepik_attempts,
        "freepik_enabled": provider.allow_freepik,
        "image_library_hits": provider.library_hits,
        "reuse_existing": reuse_existing,
        "image_sources": sources,
        "cover_sources": cover_sources,
        "warnings": list(dict.fromkeys(warnings)),
        "providers_configured": {
            "pexels": bool(provider.pexels_key), "pixabay": bool(provider.pixabay_key),
            "openverse": provider.openverse_enabled, "freepik": bool(provider.freepik_key),
            "openrouter": bool(provider.openrouter_key), "openai": bool(provider.openai_key),
        },
    }

    # The ordinary path: one Word upload -> one Word download. Avoid forcing
    # users to open a ZIP and hunt through extra guide/cover folders.
    if mode == "insert" and Path(filename).suffix.lower() == ".docx":
        report["output_format"] = "docx"
        return CourseImagesResult(
            filename=_safe_name(enhanced[0][0]),
            payload=enhanced[0][1],
            details=report,
        )

    out = BytesIO()
    used_paths: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        if mode == "insert":
            # A batch ZIP contains only the edited DOCX files, at its root.
            assets = [("", enhanced)]
        else:
            archive.writestr("Matching Image Search Guides/", b"")
            archive.writestr("Cover Images/", b"")
            assets = [
                ("Matching Image Search Guides/", guides),
                ("Cover Images/", cover_assets),
            ]
        for folder, items in assets:
            for original_name, data in items:
                name = _safe_name(original_name)
                path = folder + name
                number = 2
                while path.lower() in used_paths:
                    path = folder + f"{Path(name).stem}_{number}{Path(name).suffix}"
                    number += 1
                used_paths.add(path.lower())
                archive.writestr(path, data)
    report["output_format"] = "zip"
    return CourseImagesResult(
        filename=f"{_safe_name(course_title)}_Matching_Image_Guides_and_Covers.zip" if mode == "guides"
        else f"{_safe_name(course_title)}_Documents_With_Images.zip",
        payload=out.getvalue(),
        details=report,
    )
