"""Offline regression coverage: free first, library reuse, review + final DOCX."""
from __future__ import annotations

from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch
import zipfile

from docx import Document
import httpx
from PIL import Image

from app.services.course_images import Picture, Provider, CourseImagesError
from app.services.course_image_review import make_image_preview, finish_image_preview, preview_jpeg
from app.services.course_image_library import ImageLibrary
from app.services.free_stock_sources import find_pixabay, find_openverse
from app.services.storage import StorageNotFoundError


def jpeg() -> bytes:
    out = BytesIO()
    Image.new("RGB", (1100, 750), (43, 123, 176)).save(out, format="JPEG")
    return out.getvalue()


def document():
    doc = Document()
    doc.add_heading("Care records", 1)
    doc.add_paragraph("Original formatted paragraph with **text** unchanged.")
    doc.add_heading("Storing confidential records", 2)
    doc.add_paragraph("Keep confidential records in a secure location.")
    doc.add_heading("Responding to requests", 2)
    doc.add_paragraph("Respond carefully to enquiries from colleagues.")
    doc.add_heading("References", 1)
    doc.add_paragraph("Some citations that must be preserved.")
    out = BytesIO(); doc.save(out)
    return out.getvalue()


class Store:
    def __init__(self): self.objects = {}
    def get_bytes(self, key):
        if key not in self.objects: raise StorageNotFoundError("Missing")
        return self.objects[key]
    def put_bytes(self, key, data, content_type):
        self.objects[key] = data


class FakeProvider(Provider):
    def __init__(self):
        super().__init__(pexels_key="test", pixabay_key="", freepik_key="", openverse_enabled=False)
        self.calls = 0
    def find(self, query, *, portrait=False, generate=True, prompt="", subject=""):
        self.calls += 1
        return Picture(jpeg(), "Pexels", "https://www.pexels.com/photo/test/", f"pexels:test-{self.calls}", "Pexels License", "Example Photographer")


class FreeFirstCourseImagesTests(TestCase):
    def test_no_freepik_when_pexels_is_available(self):
        provider = Provider(pexels_key="pkey", pixabay_key="pikey", freepik_key="fkey", openverse_enabled=True, library=ImageLibrary(Store()))
        with patch.object(provider, "_stock", return_value=Picture(jpeg(), "Pexels", "https://www.pexels.com/photo/1", "pexels:1")) as pexels, \
             patch("app.services.course_images.find_pixabay") as pixabay, \
             patch("app.services.course_images.find_openverse") as openverse, \
             patch.object(provider, "_freepik") as freepik:
            selected = provider.find("care records", subject="records")
        self.assertEqual(selected.source, "Pexels")
        pexels.assert_called_once()
        pixabay.assert_not_called()
        openverse.assert_not_called()
        freepik.assert_not_called()
        self.assertEqual(provider.freepik_downloads, 0)

    def test_library_reuse_persists_across_providers(self):
        cache = ImageLibrary(Store())
        first = Provider(pexels_key="pkey", pixabay_key="", freepik_key="", openverse_enabled=False, library=cache)
        with patch.object(first, "_stock", return_value=Picture(jpeg(), "Pexels", "https://www.pexels.com/photo/1", "pexels:1")):
            result = first.find("confidential records", subject="records")
        self.assertFalse(result.reused)
        second = Provider(pexels_key="pkey", pixabay_key="", freepik_key="", openverse_enabled=False, library=cache)
        with patch.object(second, "_stock") as stock:
            reused = second.find("confidential records", subject="records")
        stock.assert_not_called()
        self.assertTrue(reused.reused)
        self.assertEqual(reused.payload, jpeg())
        self.assertEqual(second.library_hits, 1)

    def test_freepik_is_only_final_fallback_and_capped(self):
        provider = Provider(pexels_key="pkey", pixabay_key="pikey", freepik_key="fkey", openverse_enabled=True,
                            allow_freepik=True, freepik_limit=1, library=ImageLibrary(Store()))
        sequence = []
        class Freepik:
            def search_photo(self, *args, **kwargs):
                sequence.append("freepik")
                return jpeg(), "https://www.freepik.com/free-photo/1", "freepik:1", "freemium", "A"
        with patch.object(provider, "_stock", side_effect=lambda *args, **kwargs: sequence.append("pexels")) as pexels, \
             patch("app.services.course_images.find_pixabay", side_effect=lambda *args, **kwargs: sequence.append("pixabay")), \
             patch("app.services.course_images.find_openverse", side_effect=lambda *args, **kwargs: sequence.append("openverse")), \
             patch.object(provider, "_freepik", return_value=Freepik()):
            picture = provider.find("records", subject="records")
            self.assertEqual(picture.source, "Freepik")
            self.assertIsNone(provider.find("another", subject="subject"))
        self.assertEqual(sequence, ["pexels", "pixabay", "openverse", "freepik", "pexels", "pixabay", "openverse"])
        self.assertEqual(provider.freepik_downloads, 1)
        self.assertEqual(provider.generations_used, 0)

    def test_pixabay_search_downloads_only_approved_cdn_and_caches_search_response(self):
        calls = []
        def handler(req):
            calls.append(req.url.host)
            if req.url.host == "pixabay.com":
                return httpx.Response(200, json={"hits": [
                    {"id": 10, "tags": "records confidential office", "largeImageURL": "https://cdn.pixabay.com/photo/example.jpg", "pageURL": "https://pixabay.com/photos/example-10/", "user": "photographer"},
                    {"id": 11, "tags": "records", "largeImageURL": "http://127.0.0.1/admin"},
                ]})
            if req.url.host == "cdn.pixabay.com":
                return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
            raise AssertionError("Unexpected URL")
        store = Store()
        real_client = httpx.Client
        with patch("app.services.free_stock_sources.storage", store), \
             patch("app.services.free_stock_sources.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            for _ in range(2):
                result = find_pixabay("key", "confidential records", "records", set(), portrait=False, normalise=lambda raw: raw)
                self.assertEqual(result[2], "pixabay:10")
        self.assertEqual(calls.count("pixabay.com"), 1)
        self.assertEqual(calls.count("cdn.pixabay.com"), 2)

    def test_openverse_accepts_only_public_domain_and_safe_cdn(self):
        image = {"id": "abc", "license": "pdm", "title": "Secure records workplace", "width": 1200, "height": 800,
                 "url": "https://upload.wikimedia.org/photo/abc.jpg", "foreign_landing_url": "https://commons.wikimedia.org/wiki/File:abc", "creator": "Smith", "tags": []}
        def handler(req):
            if req.url.host == "api.openverse.org":
                return httpx.Response(200, json={"results": [
                    {**image, "id": "bad1", "license": "by-nc"},
                    {**image, "id": "bad2", "url": "http://localhost/private"},
                    image,
                ]})
            if req.url.host == "upload.wikimedia.org":
                return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
            raise AssertionError("Other host should never be fetched")
        real_client = httpx.Client
        with patch("app.services.free_stock_sources.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
            selected = find_openverse("records", "records", set(), portrait=False, normalise=lambda raw: raw)
        self.assertEqual(selected[2], "openverse:abc")
        self.assertEqual(selected[3], "PDM")

    def test_review_then_confirm_has_no_second_provider_calls_and_preserves_word(self):
        source = document()
        provider = FakeProvider()
        preview = make_image_preview(source, "Training.docx", image_source="mixed", provider=provider)
        self.assertEqual(len(preview.details["previews"]), 2)
        self.assertTrue(preview_jpeg(preview.payload, 0).startswith(b"\xff\xd8"))
        with zipfile.ZipFile(BytesIO(preview.payload)) as archive:
            names = archive.namelist()
            self.assertIn("documents/0.docx", names)
            self.assertIn("images/1.jpg", names)
        calls_before = provider.calls
        final = finish_image_preview(preview.payload, [0, 1])
        self.assertEqual(provider.calls, calls_before)
        self.assertEqual(final.details["photos_inserted"], 2)
        finished = Document(BytesIO(final.payload))
        original = Document(BytesIO(source))
        self.assertEqual([(p.text, p.style.name) for p in finished.paragraphs if p.text],
                         [(p.text, p.style.name) for p in original.paragraphs if p.text])
        self.assertEqual(len(finished.inline_shapes), 2)
        self.assertEqual(len(original.inline_shapes), 0)
        self.assertEqual([s["provider"] for s in final.details["image_sources"]], ["Pexels", "Pexels"])
        skipped = finish_image_preview(preview.payload, [1])
        self.assertEqual(skipped.details["photos_inserted"], 1)
        self.assertEqual(len(Document(BytesIO(skipped.payload)).inline_shapes), 1)
        self.assertEqual(finish_image_preview(preview.payload, []).details["photos_inserted"], 0)
        with self.assertRaises(CourseImagesError):
            finish_image_preview(preview.payload, [100])

    def test_preview_multiple_chapters_and_unchanged_skipped_documents(self):
        out = BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("Unit 1.docx", document())
            archive.writestr("Unit 2.docx", document())
        provider = FakeProvider()
        preview = make_image_preview(out.getvalue(), "Chapters.zip", provider=provider)
        final = finish_image_preview(preview.payload, [0])
        with zipfile.ZipFile(BytesIO(final.payload)) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            self.assertEqual(len(Document(BytesIO(archive.read("Unit 1_with_images.docx"))).inline_shapes), 1)
            self.assertEqual(len(Document(BytesIO(archive.read("Unit 2_with_images.docx"))).inline_shapes), 0)
