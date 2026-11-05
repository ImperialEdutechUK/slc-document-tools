"""Conservative, free stock-image discovery for course chapters.

Image bytes are fetched only from known publisher CDNs. Openverse is restricted
by default to CC0/Public Domain images hosted by Wikimedia Commons: arbitrary
URLs from public search indexes are never requested by the backend.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urlparse

import httpx

from .storage import StorageError, StorageNotFoundError, storage

MAX_DOWNLOAD_BYTES = 22 * 1024 * 1024
ALLOWED_PIXABAY_HOSTS = {"cdn.pixabay.com", "pixabay.com"}
# StockSnap images are also surfaced by Openverse as CC0; the image bytes
# must be hosted on the exact approved publisher CDN, not arbitrary URLs.
ALLOWED_OPENVERSE_HOSTS = {"upload.wikimedia.org", "cdn.stocksnap.io"}


def _allowed_https(url: str, hosts: set[str]) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username and not parsed.password and parsed.port in {None, 443}
    except (ValueError, AttributeError):
        return False


def _download(client: httpx.Client, url: str, hosts: set[str]) -> bytes:
    if not _allowed_https(url, hosts):
        raise ValueError("Photo URL is outside the approved stock-image CDN")
    chunks = []
    total = 0
    with client.stream("GET", url, follow_redirects=False) as result:
        result.raise_for_status()
        if result.status_code in {301, 302, 303, 307, 308}:
            raise ValueError("Remote photo redirects are not allowed")
        if result.headers.get("content-length") and int(result.headers["content-length"]) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Image exceeds the size limit")
        content_type = result.headers.get("content-type", "").lower()
        if content_type and not content_type.startswith("image/") and not content_type.startswith("application/octet-stream"):
            raise ValueError("Remote resource is not an image")
        for chunk in result.iter_bytes():
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError("Image exceeds the size limit")
            chunks.append(chunk)
    return b"".join(chunks)


# Translate synonyms into *observable* photo concepts. An academic heading
# such as "Collaborating" does not have to appear verbatim in Pixabay tags for
# a tagged "team meeting" photograph to be a valid candidate.
_CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^(?:collab|team|colleag|cowork|group|cooperat)", "team"),
    (r"^(?:manag|leader|lead|supervis|direct|boss)", "manager"),
    (r"^(?:meet|discuss|negotiat|convers|communicat|dialog|consult|talk|mediate)", "meeting"),
    (r"^(?:office|workplace|workstation)", "office"),
    (r"^(?:business|corporat|company|enterprise)", "business"),
    (r"^(?:photograph|photo|picture|image|stock)", "_stop"),
    (r"^(?:person|people|human|adult|woman|women|man|men)", "_stop"),
    (r"^(?:employ|staff|worker|professional)", "staff"),
    (r"^(?:secure|confiden|privacy|private|protect|security)", "security"),
    (r"^(?:document|record|paperwork|file|filing|folder)", "records"),
    (r"^(?:child|kid|pupil|youngster)", "child"),
    (r"^(?:caregiv|carer|nurs|healthcar)", "healthcare"),
    (r"^(?:stud|learn|educat|teach|classroom|school)", "education"),
    (r"^(?:hospital|clinic|medical)", "clinical"),
    (r"^(?:advic|support|assist|help)", "support"),
    (r"^(?:safeguard|welfare|protect)", "safeguarding"),
    (r"^(?:recruit|interview|hir)", "recruitment"),
    (r"^(?:calculat|account|bookkeep|payroll|wage|salary)", "payroll"),
    (r"^(?:inspect|audit|review|check|monitor)", "review"),
    (r"^(?:resolve|resolut|solv)", "solve"),
    (r"^(?:planning|plan|schedul|strateg)", "planning"),
    (r"^(?:rubbish|recycl|dispos|waste)", "waste"),
    (r"^(?:computer|laptop|desktop|technology|digital)", "computer"),
)
_GENERIC_CONCEPTS = {"business", "office", "team", "meeting", "staff", "computer"}
_IGNORE_WORDS = {"the", "and", "a", "an", "with", "for", "from", "into", "this", "that",
                 "there", "where", "why", "how", "when", "without", "use", "using",
                 "professional", "appropriate", "relevant", "natural", "realistic", "view",
                 "background", "beautiful", "training", "course", "work", "stock"}


def _words(value: str) -> set[str]:
    concepts: set[str] = set()
    for word in re.findall(r"[a-z]{3,}", value.lower()):
        if word in _IGNORE_WORDS:
            continue
        concept = word.rstrip("s") if len(word) > 5 and not word.endswith("ss") else word
        for pattern, group in _CONCEPT_PATTERNS:
            if re.match(pattern, word):
                concept = group
                break
        if concept != "_stop":
            concepts.add(concept)
    return concepts


def _rank(query: str, subject: str, description: str) -> int:
    """Score stock tags using photographic concepts, not literal academic terms.

    A provider photo is never *verified* by text tags; the user must still view
    it. Reject weak/generic one-token overlaps, but let specific terms such as
    'records' qualify and avoid the v27 requirement to repeat heading words.
    """
    words = _words(description)
    query_words = _words(query)
    if not words or not query_words:
        return 0
    shared = words & query_words
    if not shared:
        return 0
    # A solitary generic label ("office") doesn't establish course relevance.
    if len(shared) == 1 and len(query_words) >= 2 and not (shared - _GENERIC_CONCEPTS):
        return 0
    heading_bonus = len(words & _words(subject))
    return 4 * len(shared) + min(2, heading_bonus)


def _query_fallback(query: str) -> str:
    """One wider tag-friendly query if the precise search has no suitable hit."""
    words = query.lower().split()
    translations = {
        "thoughtful": "", "leading": "", "solving": "", "colleagues": "team",
        "collaborating": "team", "collaboration": "team", "negotiation": "meeting",
        "negotiating": "meeting", "conversation": "meeting", "discussion": "meeting",
        "professional": "", "appropriate": "", "relevant": "",
        "safeguarding": "care", "practitioner": "worker",
        "evaluating": "reviewing", "reviewing": "review",
    }
    expanded = [translations.get(word, word) for word in words]
    selected = list(dict.fromkeys(w for w in expanded if w))
    return " ".join(selected[:4])


def _pixabay_search(client: httpx.Client, api_key: str, query: str, portrait: bool) -> list[dict]:
    """Cache API *search results* for at least 24 hours, as Pixabay requires."""
    params = {
        "key": api_key, "q": query[:100], "image_type": "photo",
        "orientation": "vertical" if portrait else "horizontal", "safesearch": "true",
        "per_page": 20, "min_width": 650, "min_height": 350,
    }
    cache_key = "course-image-library/v2/pixabay-queries/" + hashlib.sha256(
        json.dumps(params, sort_keys=True).encode("utf-8")
    ).hexdigest() + ".json"
    try:
        cached = json.loads(storage.get_bytes(cache_key))
        if cached.get("saved_at", 0) + 24 * 3600 >= time.time():
            return cached.get("hits", [])
    except (StorageError, ValueError, TypeError):
        pass
    response = client.get("https://pixabay.com/api/", params=params, follow_redirects=False)
    response.raise_for_status()
    hits = response.json().get("hits", [])
    if not isinstance(hits, list):
        return []
    try:
        storage.put_bytes(cache_key, json.dumps({"saved_at": time.time(), "hits": hits}).encode("utf-8"), "application/json")
    except StorageError:
        pass
    return hits


def find_pixabay(key: str, query: str, subject: str, used: set[str], *, portrait: bool, normalise) -> tuple[bytes, str, str, str, str] | None:
    if not key:
        return None
    # One primary query + at most one broader query. This avoids repeatedly
    # downloading unrelated results or exhausting a free account's rate limit.
    wider = _query_fallback(query)
    queries = [query] + ([wider] if wider and wider.casefold() != query.casefold() else [])
    with httpx.Client(timeout=22, follow_redirects=False) as client:
        for search in queries[:2]:
            hits = _pixabay_search(client, key, search, portrait)
            ranked = sorted(
                ((score, order, hit) for order, hit in enumerate(hits) if isinstance(hit, dict)
                 and (score := _rank(query, subject, str(hit.get("tags", "")))) > 0),
                key=lambda it: (-it[0], it[1]),
            )
            for _, _, hit in ranked[:5]:
                identifier = f"pixabay:{hit.get('id', '')}"
                if not hit.get("id") or identifier in used:
                    continue
                url = str(hit.get("largeImageURL") or hit.get("webformatURL") or "")
                if not _allowed_https(url, ALLOWED_PIXABAY_HOSTS):
                    continue
                try:
                    payload = normalise(_download(client, url, ALLOWED_PIXABAY_HOSTS))
                    return payload, str(hit.get("pageURL", "")), identifier, "Pixabay Content License", str(hit.get("user", ""))[:100]
                except (ValueError, httpx.HTTPError, OSError):
                    continue
    return None


def find_openverse(query: str, subject: str, used: set[str], *, portrait: bool, normalise) -> tuple[bytes, str, str, str, str] | None:
    with httpx.Client(timeout=22, follow_redirects=False) as client:
        response = client.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query[:110], "license": "cc0,pdm", "page_size": 20, "mature": "false", "extension": "jpg,jpeg,png"},
            headers={"User-Agent": "SLC-Document-Tools/1.0 (course-image-reference)"},
        )
        response.raise_for_status()
        hits = response.json().get("results", [])
        if not isinstance(hits, list):
            return None
        ranked = []
        for hit in hits:
            if not isinstance(hit, dict) or str(hit.get("license", "")).lower() not in {"cc0", "pdm"}:
                continue
            identifier = "openverse:" + str(hit.get("id", ""))
            if not hit.get("id") or identifier in used:
                continue
            width = int(hit.get("width") or 0)
            height = int(hit.get("height") or 0)
            if width < 650 or height < 350 or (portrait and width >= height) or (not portrait and width <= height):
                continue
            url = str(hit.get("url", ""))
            if not _allowed_https(url, ALLOWED_OPENVERSE_HOSTS):
                continue
            label = str(hit.get("title") or "") + " " + " ".join(str(x.get("name") or "") for x in hit.get("tags", []) if isinstance(x, dict))
            score = _rank(query, subject, label)
            if score:
                ranked.append((score, hit))
        ranked.sort(key=lambda it: it[0], reverse=True)
        for _, hit in ranked[:5]:
            try:
                payload = normalise(_download(client, str(hit["url"]), ALLOWED_OPENVERSE_HOSTS))
                identifier = "openverse:" + str(hit["id"])
                licence = str(hit.get("license", "")).upper()
                return payload, str(hit.get("foreign_landing_url") or ""), identifier, licence, str(hit.get("creator") or "")[:100]
            except (ValueError, httpx.HTTPError, OSError):
                continue
    return None
