from __future__ import annotations

from dataclasses import asdict, dataclass, field
from io import BytesIO
import ipaddress
import json
import os
from pathlib import Path
import re
from difflib import SequenceMatcher
import socket
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse
import xml.etree.ElementTree as ET
import zipfile

import httpx
from PIL import Image, UnidentifiedImageError

from ..formatter.image_placement import prepare_image_catalog

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


@dataclass
class LinkedImageSource:
    number: int
    url: str
    source_type: str


@dataclass
class LinkEntry:
    number: int
    url: str
    source_type: str
    status: str
    message: str = ""


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
    host = (urlparse(url).hostname or "").lower()
    for source_type, pattern in STOCK_HOST_PATTERNS.items():
        if pattern.search(host):
            return source_type
    return "direct"


def scan_linked_image_sources(docx_bytes: bytes) -> tuple[list[LinkedImageSource], list[str]]:
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
    }

    found: dict[int, LinkedImageSource] = {}
    pending_number: int | None = None

    for paragraph in root.iter(_qn(W_NS, "p")):
        text = " ".join("".join(node.text or "" for node in paragraph.iter(_qn(W_NS, "t"))).split())
        urls: list[str] = []

        for hyperlink in paragraph.iter(_qn(W_NS, "hyperlink")):
            rel_id = hyperlink.get(_qn(R_NS, "id"))
            if rel_id and relationships.get(rel_id):
                urls.append(relationships[rel_id])

        for field in paragraph.iter(_qn(W_NS, "instrText")):
            if field.text:
                urls.extend(URL_RE.findall(field.text))
        urls.extend(URL_RE.findall(text))
        urls = list(dict.fromkeys(_clean_url(url) for url in urls))

        match = IMAGE_NUMBER_RE.search(text)
        current_number = int(match.group("number")) if match else None
        number_for_urls = current_number or pending_number

        if urls and number_for_urls is not None:
            chosen = urls[0]
            if number_for_urls in found and found[number_for_urls].url != chosen:
                warnings.append(
                    f"Image {number_for_urls} has more than one linked URL; the first detected link is used."
                )
            else:
                found[number_for_urls] = LinkedImageSource(
                    number=number_for_urls,
                    url=chosen,
                    source_type=_source_type(chosen),
                )
            pending_number = None
            continue

        if current_number is not None and not urls:
            pending_number = current_number
        elif text:
            pending_number = None

    return [found[number] for number in sorted(found)], warnings


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
    api_key, base, header_name = _api_config()
    resource_id = _extract_resource_id(url)
    if resource_id is None:
        raise ValueError("Could not identify the stock resource ID from this link.")

    headers = {header_name: api_key, "Accept": "application/json"}
    timeout = httpx.Timeout(20.0, connect=10.0)
    errors: list[str] = []

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

    detail = "; ".join(errors[-10:]) if errors else "no downloadable raster URL was returned"
    raise ValueError(f"The stock API could not return a usable JPG/PNG image ({detail}).")


def _verify_image(payload: bytes) -> None:
    try:
        with Image.open(BytesIO(payload)) as image:
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
    sources, warnings = scan_linked_image_sources(docx_bytes)
    result.warnings.extend(warnings)
    manual_numbers = manual_image_numbers(manual_items or [])

    for source in sources:
        if source.number in manual_numbers:
            result.entries.append(
                LinkEntry(source.number, source.url, source.source_type, "manual_override", "Manual upload takes priority.")
            )
            continue
        try:
            if source.source_type in {"freepik", "magnific"}:
                payload = _download_stock_resource(source.url)
            else:
                payload = _download_public_url(source.url)
                _verify_image(payload)
            result.items.append((f"Image {source.number}.jpg", payload))
            result.entries.append(LinkEntry(source.number, source.url, source.source_type, "downloaded"))
        except Exception as exc:
            result.entries.append(LinkEntry(source.number, source.url, source.source_type, "failed", str(exc)))

    return result
