"""Fully offline tests of Freepik stock/generation and OpenRouter planning."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.services.course_image_integrations import (
    FreepikImages, OpenRouterScenes, _media_urls, _trusted_media_url,
)
from app.services.course_images import ImagePlan, Provider
from test_course_images import jpg


def client_with_transport(handler):
    real = httpx.Client
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: real(transport=transport, **kwargs)


class ImageIntegrationTests(unittest.TestCase):
    def test_freepik_stock_requires_free_photo_and_official_licensed_download(self):
        sent = []
        def handler(request):
            sent.append(str(request.url))
            if request.url.path == "/v1/resources":
                self.assertEqual(request.headers.get("x-freepik-api-key"), "test-freepik-key")
                self.assertEqual(request.url.params.get("term"), "secure records")
                return httpx.Response(200, json={"data": [
                    {"id": 21, "title": "Secure records", "url": "https://www.freepik.com/premium-photo/21.htm",
                     "image": {"type": "photo", "orientation": "horizontal"}, "licenses": [{"type": "premium"}], "products": [{"type": "premium"}]},
                    {"id": 22, "title": "Secure records cabinet", "url": "https://www.freepik.com/free-photo/secure-records-cabinet_22.htm",
                     "image": {"type": "photo", "orientation": "horizontal", "source": {"url": "https://img.freepik.com/preview.jpg"}},
                     "licenses": [{"type": "freemium"}], "products": [], "author": {"name": "Photographer"}},
                    {"id": 23, "title": "Secure records vector", "image": {"type": "vector", "orientation": "horizontal"},
                     "licenses": [{"type": "freemium"}]},
                ]})
            if request.url.path == "/v1/resources/22/download/jpg":
                return httpx.Response(200, json={"data": {"signed_url": "https://downloadscdn5.freepik.com/original.jpg"}})
            if str(request.url) == "https://downloadscdn5.freepik.com/original.jpg":
                return httpx.Response(200, content=jpg(), headers={"content-type": "image/jpeg"})
            raise AssertionError("Unwanted network request: " + str(request.url))

        with patch.dict(os.environ, {"FREEPIK_API_KEY": "test-freepik-key", "MAGNIFIC_API_KEY": ""}):
            with patch("app.services.course_image_integrations.httpx.Client", side_effect=client_with_transport(handler)):
                api = FreepikImages("test-freepik-key", base="https://api.freepik.com/v1", header="x-freepik-api-key")
                result = api.search_photo(
                    "secure records", subject="secure records", portrait=False,
                    used=set(), normalise=lambda raw: raw,
                )
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "https://www.freepik.com/free-photo/secure-records-cabinet_22.htm")
        self.assertEqual(result[2], "freepik:22")
        self.assertEqual(result[3], "freemium")
        self.assertEqual(result[4], "Photographer")
        self.assertTrue(any("download/jpg" in request for request in sent))
        self.assertFalse(any("preview.jpg" in request or "/21/" in request for request in sent))

    def test_paid_ai_generation_is_disabled_for_course_image_workflow(self):
        # The original generated-image test predates the no-AI-generation rule.
        # A production request must never bill the Freepik AI endpoint.
        with patch("app.services.course_image_integrations.httpx.Client") as client:
            provider = Provider(
                pexels_key="", pixabay_key="", freepik_key="magnific-test",
                openverse_enabled=False, openai_key="", stock_source="generate",
                max_generations=1,
            )
            self.assertIsNone(provider.find("records security", generate=True))
            self.assertEqual(provider.generations_used, 0)
            client.assert_not_called()

    def test_openrouter_refines_search_without_changing_headings(self):
        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            self.assertEqual(request.headers.get("authorization"), "Bearer openrouter-test")
            posted = json.loads(request.content)
            self.assertIn("Secure storage and access", posted["messages"][1]["content"])
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "items": [{"id": 0, "search": "secure storage locked cabinet",
                           "photo": "Secure records stored in a locked filing cabinet by a professional in an office."},
                          {"id": 1, "search": "jazz musicians",
                           "photo": "Jazz musicians performing in an unrelated concert."}]
            })}}]})
        plans = [
            ImagePlan(1, "Secure storage and access", "old", "old description", "below", "Records are stored securely."),
            ImagePlan(3, "Requesting consent", "old", "old description", "below", "Staff should request consent."),
        ]
        with patch("app.services.course_image_integrations.httpx.Client", side_effect=client_with_transport(handler)):
            provider = Provider(pexels_key="", freepik_key="", openai_key="", openrouter_key="openrouter-test")
            provider.refine_plans("Care information", "Unit 1", plans)
        self.assertEqual(provider.refined_sections, 1)
        self.assertEqual(plans[0].heading, "Secure storage and access")
        self.assertEqual(plans[0].search, "secure storage locked cabinet")
        self.assertEqual(plans[1].search, "old")

    def test_untrusted_download_hosts_are_rejected(self):
        for url in (
            "http://downloadscdn5.freepik.com/photo.jpg",
            "https://freepik.com.evil.test/photo.jpg",
            "https://127.0.0.1/photo.jpg",
            "https://freepik.com:444/photo.jpg",
            "https://freepik.com@evil.com/photo.jpg",
        ):
            self.assertFalse(_trusted_media_url(url), url)
        self.assertEqual(_media_urls({"data": {"url": "https://127.0.0.1/photo.jpg"}}), [])
        self.assertTrue(_trusted_media_url("https://ai-statics.freepik.com/pic.jpg"))

    def test_freepik_requires_real_free_photo_metadata(self):
        def handler(request):
            return httpx.Response(200, json={"data": [
                {"id": 55, "title": "Office workers", "image": {"type": "photo", "orientation": "horizontal"},
                 "licenses": [{"type": "premium"}]},
                {"id": 66, "title": "Classroom", "image": {"type": "vector", "orientation": "horizontal"},
                 "licenses": [{"type": "freemium"}]},
            ]})
        with patch("app.services.course_image_integrations.httpx.Client", side_effect=client_with_transport(handler)):
            found = FreepikImages("test", base="https://api.magnific.com/v1", header="x-magnific-api-key").search_photo(
                "office workers", subject="workers", portrait=False, used=set(), normalise=lambda raw: raw,
            )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
