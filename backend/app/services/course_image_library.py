"""Reusable, server-side public-stock image library backed by the existing S3/local storage.

Only cache sources whose photo licences permit reuse in course documents. This
cache has no TTL for approved photos; Pixabay API *search responses* have a
separate minimum 24-hour cache, per that provider's API terms.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .storage import StorageError, StorageNotFoundError, storage

ALLOWED_SOURCES = frozenset({"Pexels", "Pixabay", "Openverse"})


def _key(prefix: str, *values: str) -> str:
    payload = json.dumps([v.strip().casefold() for v in values], ensure_ascii=False).encode("utf-8")
    return f"course-image-library/v2/{prefix}/{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class SavedImage:
    payload: bytes
    source: str
    source_url: str
    identifier: str
    licence: str
    author: str


class ImageLibrary:
    """Metadata+JPG object cache. No incoming Word documents are cached here."""

    def __init__(self, backend: Any = None):
        self.backend = backend or storage

    def find(self, query: str, subject: str, *, portrait: bool = False) -> SavedImage | None:
        base = _key("photos", query, subject, "portrait" if portrait else "landscape")
        try:
            record = json.loads(self.backend.get_bytes(base + ".json"))
            if record.get("source") not in ALLOWED_SOURCES:
                return None
            raw = self.backend.get_bytes(base + ".jpg")
            if not (1024 < len(raw) <= 22 * 1024 * 1024 and raw.startswith(b"\xff\xd8")):
                return None
            return SavedImage(
                raw, record["source"], record.get("source_url", ""),
                record.get("identifier", ""), record.get("licence", ""), record.get("author", ""),
            )
        except (StorageNotFoundError, ValueError, KeyError, UnicodeError, TypeError):
            return None
        except StorageError:
            return None  # No cache must not prevent a normal online lookup.

    def save(self, query: str, subject: str, item: SavedImage, *, portrait: bool = False) -> bool:
        if item.source not in ALLOWED_SOURCES or not item.payload.startswith(b"\xff\xd8"):
            return False
        base = _key("photos", query, subject, "portrait" if portrait else "landscape")
        record = {
            "source": item.source, "source_url": item.source_url,
            "identifier": item.identifier, "licence": item.licence, "author": item.author,
        }
        try:
            self.backend.put_bytes(base + ".jpg", item.payload, "image/jpeg")
            self.backend.put_bytes(base + ".json", json.dumps(record).encode("utf-8"), "application/json")
            return True
        except StorageError:
            return False
