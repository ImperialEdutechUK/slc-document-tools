"""Automatic numbered image placement for WordprocessingML documents.

The module deliberately works at XML level so the formatter can replace image
placeholder paragraphs without rebuilding the document or losing existing
styles, section settings, tables, headers, or other formatting.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import zipfile
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps, UnidentifiedImageError


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}
EMU_PER_INCH = 914400

# A4 is 8.2677 x 11.6929 inches. The main formatter applies one-inch margins.
DEFAULT_WRITABLE_WIDTH_INCHES = 8.2677 - 2.0
DEFAULT_WRITABLE_HEIGHT_INCHES = 11.6929 - 2.0

# Recognises 1.jpg, 001.png, Image 1.jpg, Image_001.webp, and Image-12.jpeg.
IMAGE_FILENAME_RE = re.compile(
    r"^(?:image[\s_-]*)?0*(?P<number>\d+)$",
    re.IGNORECASE,
)

# The paragraph must be a standalone placeholder, not a sentence containing a
# reference to an image. Examples include "Image 3" and
# "Unit 12 Chapter 1 Image 3".
PLACEHOLDER_RE = re.compile(
    r"^\s*(?P<prefix>(?:(?:unit|module|lesson|chapter|section|topic|figure|placeholder)"
    r"\s*[\w.()/-]+\s*(?:[-–—:|]\s*)?)*)"
    r"image\s*(?:number|no\.?|#)?\s*[:#\-–—]?\s*0*(?P<number>\d+)\s*[.)]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PreparedImage:
    number: int
    source_name: str
    data: bytes
    extension: str
    width_px: int
    height_px: int
    dpi_x: float
    dpi_y: float


@dataclass
class ImageCatalog:
    selected: Dict[int, PreparedImage] = field(default_factory=dict)
    valid_files: List[PreparedImage] = field(default_factory=list)
    duplicate_image_numbers: Dict[int, List[str]] = field(default_factory=dict)
    unsupported_files: List[str] = field(default_factory=list)
    corrupted_files: Dict[str, str] = field(default_factory=dict)
    archive_errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class PlacementReport:
    total_placeholders: int = 0
    images_inserted: int = 0
    placeholder_counts: Dict[int, int] = field(default_factory=dict)
    missing_numbers: List[int] = field(default_factory=list)
    missing_placeholder_occurrences: int = 0
    duplicate_placeholder_numbers: Dict[int, int] = field(default_factory=dict)
    used_image_numbers: List[int] = field(default_factory=list)
    used_source_files: List[str] = field(default_factory=list)
    unused_uploaded_images: List[str] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return bool(
            self.missing_numbers
            or self.duplicate_placeholder_numbers
            or self.unused_uploaded_images
        )


def _qn(namespace: str, tag: str) -> str:
    return "{" + namespace + "}" + tag


def register_namespaces() -> None:
    ET.register_namespace("w", W_NS)
    ET.register_namespace("r", R_NS)
    ET.register_namespace("wp", WP_NS)
    ET.register_namespace("a", A_NS)
    ET.register_namespace("pic", PIC_NS)
    ET.register_namespace("", PR_NS)


def _normalise_upload_name(name: str) -> str:
    """Use a safe display name while retaining ZIP subfolder context."""
    cleaned = str(PurePosixPath(str(name).replace("\\", "/")))
    return cleaned.lstrip("/") or "unnamed-file"


def _is_ignored_archive_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not name
        or name.endswith("/")
        or "__MACOSX" in path.parts
        or path.name.startswith(".")
        or path.name.lower() in {"thumbs.db", "desktop.ini"}
    )


def expand_uploaded_files(
    file_items: Sequence[Tuple[str, bytes]],
) -> Tuple[List[Tuple[str, bytes]], List[str], Dict[str, str]]:
    """Expand uploaded ZIP folders and return image candidates.

    Returns (supported candidates, unsupported files, archive errors).
    """
    candidates: List[Tuple[str, bytes]] = []
    unsupported: List[str] = []
    archive_errors: Dict[str, str] = {}

    for original_name, payload in file_items:
        name = _normalise_upload_name(original_name)
        suffix = Path(name).suffix.lower()

        if suffix == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(payload), "r") as archive:
                    for member in archive.infolist():
                        member_name = _normalise_upload_name(member.filename)
                        if member.is_dir() or _is_ignored_archive_entry(member_name):
                            continue

                        member_suffix = Path(member_name).suffix.lower()
                        display_name = f"{name}/{member_name}"
                        if member_suffix not in SUPPORTED_IMAGE_EXTENSIONS:
                            unsupported.append(display_name)
                            continue

                        try:
                            candidates.append((display_name, archive.read(member)))
                        except Exception as exc:  # Bad individual archive member.
                            archive_errors[display_name] = str(exc)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                archive_errors[name] = str(exc)
            continue

        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            candidates.append((name, payload))
        elif not _is_ignored_archive_entry(name):
            unsupported.append(name)

    return candidates, sorted(set(unsupported)), archive_errors


def extract_image_number(filename: str) -> int | None:
    stem = Path(PurePosixPath(filename).name).stem.strip()
    match = IMAGE_FILENAME_RE.fullmatch(stem)
    if not match:
        return None
    return int(match.group("number"))


def _normalise_dpi(value: object) -> Tuple[float, float]:
    default = (96.0, 96.0)
    if not value:
        return default

    try:
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            x, y = float(value[0]), float(value[1])
        else:
            x = y = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return default

    # Ignore implausible metadata that would make an image microscopic or huge.
    if not (36.0 <= x <= 600.0):
        x = 96.0
    if not (36.0 <= y <= 600.0):
        y = 96.0
    return x, y


def _prepare_image(number: int, source_name: str, payload: bytes) -> PreparedImage:
    try:
        with Image.open(BytesIO(payload)) as check_image:
            check_image.verify()

        with Image.open(BytesIO(payload)) as image:
            detected_format = (image.format or "").upper()
            width_px, height_px = image.size
            if width_px <= 0 or height_px <= 0:
                raise ValueError("Image has invalid dimensions")

            dpi_x, dpi_y = _normalise_dpi(image.info.get("dpi"))

            # WebP is converted to PNG for broad Microsoft Word compatibility.
            if detected_format == "WEBP":
                image = ImageOps.exif_transpose(image)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                converted = BytesIO()
                image.save(converted, format="PNG", optimize=True)
                payload = converted.getvalue()
                extension = "png"
                width_px, height_px = image.size
            elif detected_format == "PNG":
                extension = "png"
            elif detected_format == "JPEG":
                extension = "jpg"
            else:
                # A file may have a supported suffix but contain another format.
                raise ValueError(f"Unsupported image data format: {detected_format or 'unknown'}")

    except UnidentifiedImageError as exc:
        raise ValueError("File is not a valid or readable image") from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise ValueError(str(exc) or "Corrupted image file") from exc

    return PreparedImage(
        number=number,
        source_name=source_name,
        data=payload,
        extension=extension,
        width_px=width_px,
        height_px=height_px,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
    )


def _candidate_priority(image: PreparedImage) -> Tuple[int, int, str]:
    stem = Path(PurePosixPath(image.source_name).name).stem.strip().lower()
    numeric_name_first = 0 if stem.lstrip("0").isdigit() else 1
    extension_priority = {"jpg": 0, "jpeg": 1, "png": 2}.get(image.extension, 9)
    return numeric_name_first, extension_priority, image.source_name.lower()


def prepare_image_catalog(file_items: Sequence[Tuple[str, bytes]]) -> ImageCatalog:
    """Create a one-time number -> image hash map for efficient placement."""
    catalog = ImageCatalog()
    candidates, unsupported, archive_errors = expand_uploaded_files(file_items)
    catalog.unsupported_files.extend(unsupported)
    catalog.archive_errors.update(archive_errors)

    raw_number_groups: MutableMapping[int, List[str]] = defaultdict(list)
    valid_number_groups: MutableMapping[int, List[PreparedImage]] = defaultdict(list)

    for source_name, payload in candidates:
        number = extract_image_number(source_name)
        if number is None:
            catalog.unsupported_files.append(source_name)
            continue

        raw_number_groups[number].append(source_name)
        try:
            prepared = _prepare_image(number, source_name, payload)
        except ValueError as exc:
            catalog.corrupted_files[source_name] = str(exc)
            continue

        catalog.valid_files.append(prepared)
        valid_number_groups[number].append(prepared)

    for number, names in raw_number_groups.items():
        if len(names) > 1:
            catalog.duplicate_image_numbers[number] = sorted(names, key=str.lower)

    for number, images in valid_number_groups.items():
        catalog.selected[number] = sorted(images, key=_candidate_priority)[0]

    catalog.valid_files.sort(key=lambda item: (item.number, item.source_name.lower()))
    catalog.unsupported_files = sorted(set(catalog.unsupported_files), key=str.lower)
    return catalog


def paragraph_text(paragraph: ET.Element) -> str:
    text = "".join(
        node.text or "" for node in paragraph.iter(_qn(W_NS, "t"))
    )
    return " ".join(text.replace("\u00a0", " ").split())


def extract_placeholder_number(text: str) -> int | None:
    if not text or len(text) > 180:
        return None
    match = PLACEHOLDER_RE.fullmatch(text)
    return int(match.group("number")) if match else None


def _ensure_child(parent: ET.Element, namespace: str, tag: str) -> ET.Element:
    qname = _qn(namespace, tag)
    child = parent.find(qname)
    if child is None:
        child = ET.SubElement(parent, qname)
    return child


def _set_paragraph_image_format(paragraph: ET.Element) -> None:
    ppr = paragraph.find(_qn(W_NS, "pPr"))
    if ppr is None:
        ppr = ET.Element(_qn(W_NS, "pPr"))
        paragraph.insert(0, ppr)

    jc = _ensure_child(ppr, W_NS, "jc")
    jc.set(_qn(W_NS, "val"), "center")

    spacing = _ensure_child(ppr, W_NS, "spacing")
    spacing.set(_qn(W_NS, "before"), "120")
    spacing.set(_qn(W_NS, "after"), "120")

    # Preserve paragraph properties and replace only the placeholder content.
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)


def _next_relationship_id(existing_ids: set[str], counter: int) -> Tuple[str, int]:
    while True:
        candidate = f"rIdAutoImage{counter}"
        counter += 1
        if candidate not in existing_ids:
            existing_ids.add(candidate)
            return candidate, counter


def _next_media_filename(media_dir: Path, number: int, extension: str) -> str:
    base = f"numbered_image_{number}"
    filename = f"{base}.{extension}"
    suffix = 2
    while (media_dir / filename).exists():
        filename = f"{base}_{suffix}.{extension}"
        suffix += 1
    return filename


def _ensure_content_type(content_types_root: ET.Element, extension: str) -> None:
    extension = extension.lower().lstrip(".")
    content_type = CONTENT_TYPES[extension]
    default_tag = _qn(CT_NS, "Default")

    for element in content_types_root.findall(default_tag):
        if (element.get("Extension") or "").lower() == extension:
            return

    ET.SubElement(
        content_types_root,
        default_tag,
        {"Extension": extension, "ContentType": content_type},
    )


def _calculate_size_emu(
    image: PreparedImage,
    max_width_inches: float,
    max_height_inches: float,
) -> Tuple[int, int]:
    intrinsic_width = image.width_px / image.dpi_x
    intrinsic_height = image.height_px / image.dpi_y

    # Never enlarge beyond the image's intrinsic size; shrink proportionally.
    scale = min(
        1.0,
        max_width_inches / intrinsic_width if intrinsic_width else 1.0,
        max_height_inches / intrinsic_height if intrinsic_height else 1.0,
    )
    width_inches = max(0.01, intrinsic_width * scale)
    height_inches = max(0.01, intrinsic_height * scale)
    return int(width_inches * EMU_PER_INCH), int(height_inches * EMU_PER_INCH)


def _make_drawing_run(
    relationship_id: str,
    image_name: str,
    width_emu: int,
    height_emu: int,
    doc_property_id: int,
) -> ET.Element:
    run = ET.Element(_qn(W_NS, "r"))
    drawing = ET.SubElement(run, _qn(W_NS, "drawing"))

    inline = ET.SubElement(
        drawing,
        _qn(WP_NS, "inline"),
        {"distT": "0", "distB": "0", "distL": "0", "distR": "0"},
    )
    ET.SubElement(
        inline,
        _qn(WP_NS, "extent"),
        {"cx": str(width_emu), "cy": str(height_emu)},
    )
    ET.SubElement(
        inline,
        _qn(WP_NS, "effectExtent"),
        {"l": "0", "t": "0", "r": "0", "b": "0"},
    )
    ET.SubElement(
        inline,
        _qn(WP_NS, "docPr"),
        {
            "id": str(doc_property_id),
            "name": f"Automatic Image {doc_property_id}",
            "descr": image_name,
        },
    )
    frame_properties = ET.SubElement(inline, _qn(WP_NS, "cNvGraphicFramePr"))
    ET.SubElement(
        frame_properties,
        _qn(A_NS, "graphicFrameLocks"),
        {"noChangeAspect": "1"},
    )

    graphic = ET.SubElement(inline, _qn(A_NS, "graphic"))
    graphic_data = ET.SubElement(
        graphic,
        _qn(A_NS, "graphicData"),
        {"uri": PIC_NS},
    )
    picture = ET.SubElement(graphic_data, _qn(PIC_NS, "pic"))

    non_visual = ET.SubElement(picture, _qn(PIC_NS, "nvPicPr"))
    ET.SubElement(
        non_visual,
        _qn(PIC_NS, "cNvPr"),
        {"id": "0", "name": image_name},
    )
    ET.SubElement(non_visual, _qn(PIC_NS, "cNvPicPr"))

    blip_fill = ET.SubElement(picture, _qn(PIC_NS, "blipFill"))
    ET.SubElement(
        blip_fill,
        _qn(A_NS, "blip"),
        {_qn(R_NS, "embed"): relationship_id},
    )
    stretch = ET.SubElement(blip_fill, _qn(A_NS, "stretch"))
    ET.SubElement(stretch, _qn(A_NS, "fillRect"))

    shape_properties = ET.SubElement(picture, _qn(PIC_NS, "spPr"))
    transform = ET.SubElement(shape_properties, _qn(A_NS, "xfrm"))
    ET.SubElement(transform, _qn(A_NS, "off"), {"x": "0", "y": "0"})
    ET.SubElement(
        transform,
        _qn(A_NS, "ext"),
        {"cx": str(width_emu), "cy": str(height_emu)},
    )
    geometry = ET.SubElement(
        shape_properties,
        _qn(A_NS, "prstGeom"),
        {"prst": "rect"},
    )
    ET.SubElement(geometry, _qn(A_NS, "avLst"))
    return run


def insert_numbered_images(
    document_tree: ET.ElementTree,
    relationships_root: ET.Element,
    content_types_root: ET.Element,
    media_directory: str | Path,
    catalog: ImageCatalog,
    *,
    max_width_inches: float = 6.0,
    writable_width_inches: float = DEFAULT_WRITABLE_WIDTH_INCHES,
    writable_height_inches: float = DEFAULT_WRITABLE_HEIGHT_INCHES,
) -> PlacementReport:
    """Replace all detected numbered placeholders in one document scan."""
    register_namespaces()
    report = PlacementReport()
    root = document_tree.getroot()
    body = root.find(_qn(W_NS, "body"))
    if body is None:
        return report

    paragraphs = list(body.iter(_qn(W_NS, "p")))
    placeholder_counts: Counter[int] = Counter()
    used_numbers: set[int] = set()
    used_source_files: set[str] = set()

    existing_relationship_ids = {
        relationship.get("Id") or "" for relationship in list(relationships_root)
    }
    relationship_counter = 1

    existing_doc_property_ids: List[int] = []
    for doc_property in root.iter(_qn(WP_NS, "docPr")):
        try:
            existing_doc_property_ids.append(int(doc_property.get("id", "0")))
        except ValueError:
            continue
    next_doc_property_id = max(existing_doc_property_ids, default=0) + 1

    media_dir = Path(media_directory)
    media_dir.mkdir(parents=True, exist_ok=True)
    image_relationships: Dict[int, str] = {}

    max_width = min(max_width_inches, writable_width_inches * 0.90)
    # Leave room for a heading/caption and normal paragraph flow. This also
    # prevents tall portrait images from consuming an entire writable page.
    max_height = writable_height_inches * 0.78

    for paragraph in paragraphs:
        number = extract_placeholder_number(paragraph_text(paragraph))
        if number is None:
            continue

        report.total_placeholders += 1
        placeholder_counts[number] += 1
        image = catalog.selected.get(number)
        if image is None:
            report.missing_placeholder_occurrences += 1
            continue

        if number not in image_relationships:
            relationship_id, relationship_counter = _next_relationship_id(
                existing_relationship_ids, relationship_counter
            )
            media_filename = _next_media_filename(media_dir, number, image.extension)
            (media_dir / media_filename).write_bytes(image.data)
            ET.SubElement(
                relationships_root,
                _qn(PR_NS, "Relationship"),
                {
                    "Id": relationship_id,
                    "Type": R_NS + "/image",
                    "Target": "media/" + media_filename,
                },
            )
            _ensure_content_type(content_types_root, image.extension)
            image_relationships[number] = relationship_id

        width_emu, height_emu = _calculate_size_emu(image, max_width, max_height)
        _set_paragraph_image_format(paragraph)
        paragraph.append(
            _make_drawing_run(
                image_relationships[number],
                Path(PurePosixPath(image.source_name).name).name,
                width_emu,
                height_emu,
                next_doc_property_id,
            )
        )
        next_doc_property_id += 1
        report.images_inserted += 1
        used_numbers.add(number)
        used_source_files.add(image.source_name)

    report.placeholder_counts = dict(sorted(placeholder_counts.items()))
    report.missing_numbers = sorted(
        number for number in placeholder_counts if number not in catalog.selected
    )
    report.duplicate_placeholder_numbers = {
        number: count
        for number, count in sorted(placeholder_counts.items())
        if count > 1
    }
    report.used_image_numbers = sorted(used_numbers)
    report.used_source_files = sorted(used_source_files, key=str.lower)

    # Every valid uploaded file that was not selected and inserted is unused.
    report.unused_uploaded_images = sorted(
        {
            image.source_name
            for image in catalog.valid_files
            if image.source_name not in used_source_files
        },
        key=str.lower,
    )
    return report


def build_validation_report(catalog: ImageCatalog, report: PlacementReport) -> str:
    lines: List[str] = [
        "Document Formatting Complete",
        "",
        f"Total placeholders found: {report.total_placeholders}",
        f"Images inserted: {report.images_inserted}",
        f"Missing images: {len(report.missing_numbers)}",
        f"Unused uploaded images: {len(report.unused_uploaded_images)}",
    ]

    if report.missing_numbers:
        lines.extend(["", "Missing Images"])
        lines.extend(f"Image {number}" for number in report.missing_numbers)
        if report.missing_placeholder_occurrences > len(report.missing_numbers):
            lines.append(
                "Missing placeholder occurrences: "
                + str(report.missing_placeholder_occurrences)
            )

    if report.unused_uploaded_images:
        lines.extend(["", "Unused Images"])
        lines.extend(report.unused_uploaded_images)

    if report.duplicate_placeholder_numbers:
        lines.extend(["", "Duplicate Placeholders"])
        lines.extend(
            f"Image {number}: {count} occurrences"
            for number, count in report.duplicate_placeholder_numbers.items()
        )

    if catalog.duplicate_image_numbers:
        lines.extend(["", "Duplicate Image Numbers"])
        for number, names in sorted(catalog.duplicate_image_numbers.items()):
            chosen = catalog.selected.get(number)
            chosen_text = chosen.source_name if chosen else "none (all invalid)"
            lines.append(f"Image {number}: selected {chosen_text}")
            for name in names:
                if name != chosen_text:
                    lines.append(f"  Ignored duplicate: {name}")

    if catalog.corrupted_files:
        lines.extend(["", "Corrupted Images"])
        lines.extend(
            f"{name}: {reason}"
            for name, reason in sorted(catalog.corrupted_files.items())
        )

    if catalog.unsupported_files:
        lines.extend(["", "Unsupported or Invalidly Named Files"])
        lines.extend(catalog.unsupported_files)

    if catalog.archive_errors:
        lines.extend(["", "Archive Errors"])
        lines.extend(
            f"{name}: {reason}"
            for name, reason in sorted(catalog.archive_errors.items())
        )

    has_warnings = bool(
        report.has_warnings
        or catalog.duplicate_image_numbers
        or catalog.corrupted_files
        or catalog.unsupported_files
        or catalog.archive_errors
    )
    lines.extend(
        [
            "",
            "Formatting completed with warnings."
            if has_warnings
            else "Formatting successful.",
        ]
    )
    return "\n".join(lines)
