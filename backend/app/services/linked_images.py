from __future__ import annotations

from dataclasses import asdict, dataclass, field
from io import BytesIO
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
from difflib import SequenceMatcher
import socket
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse
import xml.etree.ElementTree as ET
import zipfile

import httpx
from PIL import UnidentifiedImageError

from ..formatter.image_placement import extract_placeholder_number, prepare_image_catalog
from ..formatter.safe_images import open_checked_image
from .freepik_account import account_fallback_configured, download_with_accounts

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
IMAGE_NUMBER_RE = re.compile(r"\bImage\s*[-:#]?\s*(?P<number>\d+)\b", re.IGNORECASE)
STOCK_HOST_PATTERNS = {
    "freepik": re.compile(r"(^|\.)freepik\.com$", re.IGNORECASE),
    "magnific": re.compile(r"(^|\.)magnific\.com$", re.IGNORECASE),
}
RESOURCE_ID_PATTERNS = [
    re.compile(r"_(\d+)(?:\.htm|\.html)?(?:[?#].*)?$", re.IGNORECASE),
    re.compile(r"/(?:resource|photo|vector|psd)/(\d+)(?:[/?#]|$)", re.IGNORECASE),
]
MAX_IMAGE_BYTES = int(os.getenv("MAX_REMOTE_IMAGE_BYTES", str(25 * 1024 * 1024)))
MAX_LINKED_IMAGES = int(os.getenv("MAX_LINKED_IMAGES_PER_DOCUMENT", "100"))

IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_LABEL_RE = re.compile(
    r"\b(?:images?|photos?|photographs?|pictures?|illustrations?|"
    r"graphics?|visuals?|diagrams?|screenshots?|infographics?|artwork)\b",
    re.IGNORECASE,
)
IMAGE_PAGE_SEGMENTS = {
    "photo", "photos", "free-photo", "image", "images", "image-photo",
    "stock-photo", "photography", "picture", "pictures", "illustration",
    "illustrations", "vector", "free-vector", "vectors",
}
# Recognise image-specific *destinations*, not all links on these websites.
IMAGE_PAGE_HOSTS = (
    "pexels.com", "unsplash.com", "pixabay.com", "shutterstock.com",
    "istockphoto.com", "gettyimages.com", "stock.adobe.com", "flickr.com",
)
EXCLUDED_SECTION_RE = re.compile(
    r"^(?:references?|reference list|bibliography|works cited|further reading|"
    r"summary|table of contents|contents)$",
    re.IGNORECASE,
)



@dataclass
class LinkedImageSource:
    number: int
    url: str
    source_type: str
    label: str = ""
    numbered: bool = True
    anchor_text: str = ""


@dataclass
class LinkEntry:
    number: int
    url: str
    source_type: str
    status: str
    message: str = ""
    label: str = ""
    numbered: bool = True
    anchor_text: str = ""


@dataclass
class LinkedImageResult:
    items: list[tuple[str, bytes]] = field(default_factory=list)
    entries: list[LinkEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entries": [asdict(entry) for entry in self.entries],
            "warnings": self.warnings,
            "downloaded": sum(1 for entry in self.entries if entry.status == "downloaded"),
            "failed": sum(1 for entry in self.entries if entry.status == "failed"),
            "manual_override": sum(1 for entry in self.entries if entry.status == "manual_override"),
        }


def _qn(ns: str, tag: str) -> str:
    return "{" + ns + "}" + tag


def _clean_url(url: str) -> str:
    return url.rstrip(".,);]}")


def _source_type(url: str) -> str:
    parsed = urlparse(url)
    # Stock-provider image CDN URLs are already raster files; do not send them
    # to the provider's resource-ID API (which expects a /free-photo/... page).
    if Path(unquote(parsed.path).lower()).suffix in IMAGE_FILE_EXTENSIONS:
        return "direct"
    host = (parsed.hostname or "").lower()
    for source_type, pattern in STOCK_HOST_PATTERNS.items():
        if pattern.search(host):
            return source_type
    return "direct"


def _normalise_link_text(value: str) -> str:
    return " ".join(re.sub(r"[\u00ad\u200b-\u200f\u2060\ufeff]", "", value).split())


def _looks_like_image_link(url: str, label: str) -> bool:
    """Identify image links without requiring IMAGE1 / IMAGE2 display text.

    Ordinary reference/website hyperlinks must not turn into photographs simply
    because the linked webpage advertises a social-media preview image.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    path = unquote(parsed.path).lower()
    if Path(path).suffix in IMAGE_FILE_EXTENSIONS:
        return True
    query = parse_qs(parsed.query.lower())
    if any(
        Path(value).suffix in IMAGE_FILE_EXTENSIONS
        for key in ("file", "filename", "image", "src", "url")
        for value in query.get(key, [])
    ) or any(value in {"jpg", "jpeg", "png", "webp"}
             for key in ("format", "fm", "extension") for value in query.get(key, [])):
        return True
    host = parsed.hostname.lower()
    if host.startswith(("images.", "img.", "photos.")) and path not in {"", "/"}:
        return True
    if _source_type(url) in {"magnific", "freepik"}:
        return bool(path and path != "/")
    segments = {segment for segment in path.split("/") if segment}
    if any(host == domain or host.endswith("." + domain) for domain in IMAGE_PAGE_HOSTS):
        if IMAGE_PAGE_SEGMENTS & segments:
            return True
    # An image-specific URL on another site is still supported regardless of
    # the hyperlink label, e.g. 'Click here' -> /photos/workplace-safety.
    if IMAGE_PAGE_SEGMENTS & segments:
        return True
    # Explicitly labelled image links may point to image pages with opaque URLs.
    return bool(IMAGE_LABEL_RE.search(_normalise_link_text(label)))


@dataclass
class _DiscoveredLink:
    url: str
    label: str
    number: int | None = None
    anchor_text: str = ""


def scan_linked_image_sources(
    docx_bytes: bytes, *, occupied_numbers: Iterable[int] = (),
) -> tuple[list[LinkedImageSource], list[str]]:
    """Scan actual Word hyperlinks and direct URLs, preserving their document order.

    Numbered placeholders retain their original numbers. Other genuine image
    links receive non-conflicting internal numbers for the existing DOCX image
    catalog; users do not need to rename or number any hyperlink.
    """
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(docx_bytes)) as archive:
            document_xml = archive.read("word/document.xml")
            rels_xml = archive.read("word/_rels/document.xml.rels")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("The uploaded DOCX package is not readable.") from exc

    root = ET.fromstring(document_xml)
    rels_root = ET.fromstring(rels_xml)
    relationships = {
        rel.get("Id"): rel.get("Target")
        for rel in rels_root
        if rel.get("TargetMode") == "External" and rel.get("Target")
        and (rel.get("Type") or "").endswith("/hyperlink")
    }

    discovered: list[_DiscoveredLink] = []
    used_explicit: dict[int, str] = {}
    pending_number: int | None = None
    in_excluded_section = False
    occupied = set(occupied_numbers)
    max_document_number = max(occupied, default=0)

    for paragraph in root.iter(_qn(W_NS, "p")):
        text = _normalise_link_text("".join(node.text or "" for node in paragraph.iter(_qn(W_NS, "t"))))
        style = paragraph.find(f"{_qn(W_NS, 'pPr')}/{_qn(W_NS, 'pStyle')}")
        style_name = (style.get(_qn(W_NS, "val"), "") if style is not None else "").lower()
        is_heading = "heading" in style_name or style_name in {"title", "subtitle"}
        # Some Word imports omit heading styles. A standalone References,
        # Summary or Table of Contents paragraph is still a section boundary.
        if EXCLUDED_SECTION_RE.fullmatch(text):
            in_excluded_section = True
        elif is_heading and text:
            in_excluded_section = False

        matches = list(IMAGE_NUMBER_RE.finditer(text))
        for match in matches:
            max_document_number = max(max_document_number, int(match.group("number")))
        current_number = int(matches[0].group("number")) if matches else None

        if in_excluded_section:
            pending_number = None
            continue

        links: list[_DiscoveredLink] = []
        linked_urls: set[str] = set()
        for hyperlink in paragraph.iter(_qn(W_NS, "hyperlink")):
            rel_id = hyperlink.get(_qn(R_NS, "id"))
            url = relationships.get(rel_id)
            if not url:
                continue
            url = _clean_url(url)
            label = _normalise_link_text("".join(
                node.text or "" for node in hyperlink.iter(_qn(W_NS, "t"))
            ))
            links.append(_DiscoveredLink(url, label, anchor_text=text))
            linked_urls.add(url)

        # DOCX can also contain old-style HYPERLINK field instructions or plain
        # HTTP URLs (including the paragraph immediately after an Image 7 tag).
        for field in paragraph.iter(_qn(W_NS, "instrText")):
            for url in URL_RE.findall(field.text or ""):
                cleaned = _clean_url(url)
                if cleaned not in linked_urls:
                    links.append(_DiscoveredLink(cleaned, text, anchor_text=text))
                    linked_urls.add(cleaned)
        for url in URL_RE.findall(text):
            cleaned = _clean_url(url)
            if cleaned not in linked_urls:
                links.append(_DiscoveredLink(cleaned, text))
                linked_urls.add(cleaned)

        # Explicit number belongs to a specific hyperlink if its own label is
        # 'Image 3', not to an unrelated link earlier in the same paragraph.
        for link in links:
            link.number = extract_placeholder_number(link.label)
        if len(links) == 1 and links[0].number is None:
            if current_number is not None and (
                re.match(r"^\s*image\s*[-:#]?\s*\d+\b", text, re.IGNORECASE)
                or extract_placeholder_number(text) is not None
            ):
                links[0].number = current_number
            elif pending_number is not None:
                links[0].number = pending_number

        for link in links:
            if link.number is None and not _looks_like_image_link(link.url, link.label):
                continue
            if link.number is not None:
                existing = used_explicit.get(link.number)
                if existing:
                    if existing != link.url:
                        warnings.append(
                            f"Image {link.number} has more than one linked URL; the first detected link is used."
                        )
                    continue
                used_explicit[link.number] = link.url
            discovered.append(link)

        if links:
            pending_number = None
        elif current_number is not None and extract_placeholder_number(text) is not None:
            pending_number = current_number
        elif text:
            pending_number = None

    next_number = max([max_document_number, *used_explicit.keys()]) + 1
    results: list[LinkedImageSource] = []
    for link in discovered:
        if link.number is None:
            while next_number in occupied or next_number in used_explicit:
                next_number += 1
            number = next_number
            next_number += 1
            numbered = False
        else:
            number = link.number
            numbered = True
        results.append(LinkedImageSource(
            number, link.url, _source_type(link.url), link.label[:200],
            numbered, link.anchor_text[:1000],
        ))
        if len(results) >= MAX_LINKED_IMAGES:
            warnings.append(f"Only the first {MAX_LINKED_IMAGES} image links are processed per document.")
            break

    return results, warnings


def _extract_resource_id(url: str) -> int | None:
    for pattern in RESOURCE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return int(match.group(1))
    return None


def _resource_slug(url: str) -> str:
    """Return the human-readable stock slug without the legacy numeric ID."""
    name = Path(unquote(urlparse(url).path)).name
    name = re.sub(r"\.html?$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"_\d+$", "", name)
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _normalise_stock_text(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _candidate_score(item: object, target_slug: str) -> float:
    if not isinstance(item, dict):
        return 0.0

    candidates: list[str] = []
    item_url = item.get("url")
    if isinstance(item_url, str):
        candidates.append(_resource_slug(item_url))
    item_slug = item.get("slug")
    if isinstance(item_slug, str):
        candidates.append(_normalise_stock_text(item_slug))
    title = item.get("title") or item.get("name")
    if isinstance(title, str):
        candidates.append(_normalise_stock_text(title))

    score = 0.0
    target_tokens = set(target_slug.split("-"))
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == target_slug:
            return 1.0
        ratio = SequenceMatcher(None, target_slug, candidate).ratio()
        candidate_tokens = set(candidate.split("-"))
        overlap = (
            len(target_tokens & candidate_tokens) / max(len(target_tokens), 1)
            if candidate_tokens
            else 0.0
        )
        score = max(score, (ratio * 0.7) + (overlap * 0.3))
    return score


def _search_replacement_resource(client: httpx.Client, base: str, url: str, original_id: int) -> dict | None:
    """Find a migrated/current resource when a legacy page ID returns 404."""
    slug = _resource_slug(url)
    if not slug:
        return None

    # Magnific's stock catalogue accepts a `term` query. The spaced title is
    # more reliable for older Freepik-era slugs than sending punctuation.
    response = client.get(base, params={"term": slug.replace("-", " "), "limit": 20, "order": "relevance"})
    response.raise_for_status()
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None

    ranked: list[tuple[float, dict]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, int) or item_id == original_id:
            continue
        ranked.append((_candidate_score(item, slug), item))

    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = ranked[0]
    # Avoid inserting a merely similar stock image. Only use a high-confidence
    # match to the original link/title.
    return best if best_score >= 0.88 else None


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP/HTTPS image links are supported.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The image URL is not valid.")

    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local/private image URLs are blocked.")

    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError("The image host could not be resolved.") from exc

    for address in addresses:
        ip_text = address[4][0].split("%", 1)[0]
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("Local/private image URLs are blocked.")


def _download_public_url(url: str) -> bytes:
    current = url
    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": "SLC-Document-Tools/1.0"}) as client:
        for _ in range(6):
            _validate_public_http_url(current)
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Remote image redirect did not include a destination.")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_IMAGE_BYTES:
                    raise ValueError("Remote image exceeds the configured size limit.")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_IMAGE_BYTES:
                        raise ValueError("Remote image exceeds the configured size limit.")
                if not payload:
                    raise ValueError("Remote image download returned an empty file.")
                return bytes(payload)
    raise ValueError("Too many redirects while downloading the image.")


def _api_config() -> tuple[str, str, str]:
    # Magnific's stock-content API currently serves both Magnific/Freepik
    # resource links. Keep the original FREEPIK_* names as backwards-compatible
    # aliases so existing Railway environments continue to work.
    api_key = os.getenv("MAGNIFIC_API_KEY") or os.getenv("FREEPIK_API_KEY")
    if not api_key:
        raise ValueError(
            "MAGNIFIC_API_KEY is not configured on the backend "
            "(FREEPIK_API_KEY is also accepted for backwards compatibility)."
        )

    base = (
        os.getenv("MAGNIFIC_RESOURCE_API_BASE")
        or os.getenv("FREEPIK_RESOURCE_API_BASE")
        or "https://api.magnific.com/v1/resources"
    ).rstrip("/")
    header_name = (
        os.getenv("MAGNIFIC_API_HEADER")
        or os.getenv("FREEPIK_API_HEADER")
        or "x-magnific-api-key"
    )
    return api_key, base, header_name


def _response_download_urls(body: object) -> list[str]:
    urls: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            signed = value.get("signed_url")
            direct = value.get("url")
            if isinstance(signed, str) and signed.startswith(("http://", "https://")):
                urls.append(signed)
            if isinstance(direct, str) and direct.startswith(("http://", "https://")):
                urls.append(direct)
            for key, child in value.items():
                if key not in {"signed_url", "url"}:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(body)
    return list(dict.fromkeys(urls))


def _available_raster_formats(detail_body: object) -> list[str]:
    if not isinstance(detail_body, dict):
        return []
    data = detail_body.get("data")
    if not isinstance(data, dict):
        return []
    formats = data.get("available_formats") or data.get("meta", {}).get("available_formats")
    if not isinstance(formats, dict):
        return []
    return [fmt for fmt in ("jpg", "png") if fmt in formats]


def _try_stock_resource_id(
    client: httpx.Client,
    base: str,
    resource_id: int,
    *,
    known_formats: list[str] | None = None,
) -> tuple[bytes | None, list[str]]:
    errors: list[str] = []
    formats = list(known_formats or [])

    if not formats:
        try:
            detail = client.get(f"{base}/{resource_id}")
            detail.raise_for_status()
            formats = _available_raster_formats(detail.json())
        except Exception as exc:
            errors.append(f"resource details: {exc}")

    for fmt in list(dict.fromkeys(formats + ["jpg", "png"])):
        try:
            response = client.get(f"{base}/{resource_id}/download/{fmt}")
            if response.status_code >= 400:
                errors.append(f"{fmt}: HTTP {response.status_code}")
                continue
            for download_url in _response_download_urls(response.json()):
                try:
                    payload = _download_public_url(download_url)
                    _verify_image(payload)
                    return payload, errors
                except Exception as exc:
                    errors.append(f"{fmt} download: {exc}")
        except Exception as exc:
            errors.append(f"{fmt}: {exc}")

    # Photos can also be returned as a signed image URL from the generic
    # endpoint. This is deliberately last because vectors often return ZIPs.
    try:
        response = client.get(f"{base}/{resource_id}/download", params={"image_size": "large"})
        response.raise_for_status()
        for download_url in _response_download_urls(response.json()):
            try:
                payload = _download_public_url(download_url)
                _verify_image(payload)
                return payload, errors
            except Exception as exc:
                errors.append(f"default download: {exc}")
    except Exception as exc:
        errors.append(f"default: {exc}")

    return None, errors


def _download_stock_resource(url: str) -> bytes:
    errors: list[str] = []

    try:
        api_key, base, header_name = _api_config()
        resource_id = _extract_resource_id(url)
        if resource_id is None:
            raise ValueError("Could not identify the stock resource ID from this link.")

        headers = {header_name: api_key, "Accept": "application/json"}
        timeout = httpx.Timeout(20.0, connect=10.0)

        with httpx.Client(timeout=timeout, headers=headers) as client:
            payload, primary_errors = _try_stock_resource_id(client, base, resource_id)
            errors.extend(primary_errors)
            if payload is not None:
                return payload

            # Some older Freepik/Magnific page URLs still resolve on the website
            # even though their historical numeric ID no longer resolves through
            # /resources/{id}. Search the official catalogue by the page slug and
            # retry with a high-confidence migrated/current resource match.
            try:
                replacement = _search_replacement_resource(client, base, url, resource_id)
            except Exception as exc:
                replacement = None
                errors.append(f"catalogue search: {exc}")

            if replacement is not None:
                replacement_id = replacement.get("id")
                known_formats = _available_raster_formats({"data": replacement})
                if isinstance(replacement_id, int):
                    payload, replacement_errors = _try_stock_resource_id(
                        client,
                        base,
                        replacement_id,
                        known_formats=known_formats,
                    )
                    if payload is not None:
                        return payload
                    errors.append(f"replacement resource {replacement_id} failed")
                    errors.extend(replacement_errors)
            else:
                errors.append("catalogue search: no high-confidence replacement resource found")
    except Exception as exc:
        errors.append(f"stock API: {exc}")

    if account_fallback_configured():
        try:
            payload = download_with_accounts(url)
            _verify_image(payload)
            return payload
        except Exception as exc:
            errors.append(f"account fallback: {exc}")

    detail = "; ".join(errors[-10:]) if errors else "no downloadable raster URL was returned"
    raise ValueError(f"The stock image could not be downloaded ({detail}).")


class _PageImageParser(HTMLParser):
    """Extract a webpage's explicitly advertised photo, not arbitrary <img> tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "meta":
            name = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
            if name in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "image"}:
                if values.get("content"):
                    self.urls.append(values["content"])
        elif tag == "link" and "image_src" in values.get("rel", "").lower():
            if values.get("href"):
                self.urls.append(values["href"])


def _download_image_link(url: str) -> bytes:
    """Download a public raster link, or its advertised image on a photo page."""
    payload = _download_public_url(url)
    try:
        _verify_image(payload)
        return payload
    except ValueError:
        pass

    # A stock-photo landing page frequently returns HTML rather than an image.
    # Only image links identified by our scanner arrive here; arbitrary website
    # hyperlinks are never scraped for attractive but irrelevant social previews.
    sample = payload[:1024].lstrip().lower()
    if b"<html" not in sample and b"<!doctype html" not in sample:
        raise ValueError("The link did not return a supported raster image.")
    parser = _PageImageParser()
    parser.feed(payload[:2_000_000].decode("utf-8", errors="replace"))
    errors: list[str] = []
    for candidate in dict.fromkeys(parser.urls[:8]):
        resolved = urljoin(url, candidate)
        try:
            image_bytes = _download_public_url(resolved)
            _verify_image(image_bytes)
            return image_bytes
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError(
        "The photo page does not expose a downloadable JPG, PNG or WebP image."
        + (f" First error: {errors[0]}" if errors else "")
    )


def _verify_image(payload: bytes) -> None:
    try:
        with open_checked_image(payload) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("The downloaded file is not a valid image.") from exc


def manual_image_numbers(file_items: Iterable[tuple[str, bytes]]) -> set[int]:
    items = list(file_items)
    if not items:
        return set()
    catalog = prepare_image_catalog(items)
    return set(catalog.selected)


def download_linked_images(
    docx_bytes: bytes,
    manual_items: list[tuple[str, bytes]] | None = None,
) -> LinkedImageResult:
    result = LinkedImageResult()
    manual_numbers = manual_image_numbers(manual_items or [])
    sources, warnings = scan_linked_image_sources(docx_bytes, occupied_numbers=manual_numbers)
    result.warnings.extend(warnings)
    cache: dict[str, bytes] = {}

    for source in sources:
        if source.number in manual_numbers:
            result.entries.append(LinkEntry(
                source.number, source.url, source.source_type, "manual_override",
                "Manual upload takes priority.", source.label, source.numbered,
                source.anchor_text,
            ))
            continue
        try:
            if source.url in cache:
                payload = cache[source.url]
            elif source.source_type in {"freepik", "magnific"}:
                payload = _download_stock_resource(source.url)
                cache[source.url] = payload
            else:
                payload = _download_image_link(source.url)
                cache[source.url] = payload
            result.items.append((f"Image {source.number}.jpg", payload))
            result.entries.append(LinkEntry(
                source.number, source.url, source.source_type, "downloaded",
                label=source.label, numbered=source.numbered,
                anchor_text=source.anchor_text,
            ))
        except Exception as exc:
            result.entries.append(LinkEntry(
                source.number, source.url, source.source_type, "failed", str(exc),
                source.label, source.numbered, source.anchor_text,
            ))

    return result
