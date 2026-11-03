"""v27 regression coverage for legitimate API results and abstract subjects."""
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

import httpx
from docx import Document
from PIL import Image

from app.services.course_images import ImagePlan, Provider, _scene_context, _plans
from app.services.course_image_review import make_image_preview
from app.services.free_stock_sources import find_openverse
from app.services.course_image_library import ImageLibrary
from test_free_first_course_images import Store, jpeg


class ImageSearchV27Tests(TestCase):
    def test_conflict_heading_produces_photograph_friendly_queries(self):
        course = "Choosing the Right Conflict Management Approach"
        expectations = {
            "Avoidance: when not acting immediately can be appropriate": "thoughtful employee office conversation",
            "Competing or directing: using authority when decisive action is required": "manager leading office meeting",
            "Collaborating: working together for a solution that addresses underlying needs": "colleagues solving problem meeting",
            "Negotiation: reaching agreement through structured discussion": "business negotiation discussion",
            "Selecting the most proportionate approach": "business team decision meeting",
            "Common mistakes when choosing a conflict-management method": "colleagues workplace disagreement",
        }
        for heading, expected in expectations.items():
            with self.subTest(heading=heading):
                search, description = _scene_context(course, heading, "Workplace disagreement in a professional setting.")
                self.assertEqual(search, expected)
                self.assertIn("photograph", description)
                self.assertNotIn("watermark,", description)

    def test_learning_outcomes_do_not_consume_photo_budget(self):
        doc = Document()
        doc.add_heading("Learning outcomes", 1)
        doc.add_paragraph("Learners should be able to describe conflict strategies.")
        doc.add_heading("Collaborating in a professional team", 2)
        doc.add_paragraph("Working together to resolve conflict in a workplace.")
        plans, eligible = _plans(doc, "Conflict Management")
        self.assertEqual(eligible, 1)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].search, "colleagues solving problem meeting")

    def test_pexels_empty_alt_is_valid_for_preview(self):
        def handle(req):
            if req.url.host == "api.pexels.com":
                return httpx.Response(200, json={"photos": [
                    {"id": 123, "alt": "", "url": "https://www.pexels.com/photo/team-123/",
                     "src": {"large": "https://images.pexels.com/photos/123/pexels-photo-123.jpeg"},
                     "photographer": "Stock photographer"},
                ]})
            if req.url.host == "images.pexels.com":
                return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
            raise AssertionError(f"Unexpected host: {req.url.host}")
        real_client = httpx.Client
        provider = Provider(pexels_key="test-free-key", pixabay_key="", freepik_key="",
                            openverse_enabled=False, library=ImageLibrary(Store()))
        with patch("app.services.course_images.httpx.Client",
                   side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
            selected = provider.find("business negotiation discussion", subject="Negotiation")
        self.assertIsNotNone(selected)
        self.assertEqual(selected.source, "Pexels")
        self.assertEqual(provider.provider_attempts["Pexels"], 1)
        self.assertEqual(provider.provider_successes["Pexels"], 1)
        self.assertEqual(provider.freepik_attempts, 0)

    def test_openverse_can_use_stocksnap_cc0_cdn_without_other_hosts(self):
        def handle(req):
            if req.url.host == "api.openverse.org":
                return httpx.Response(200, json={"results": [{
                    "id": "ss123", "source": "stocksnap", "license": "cc0",
                    "title": "Office team meeting", "width": 1200, "height": 900,
                    "url": "https://cdn.stocksnap.io/img-thumbs/960w/SS123.jpg",
                    "foreign_landing_url": "https://stocksnap.io/photo/SS123",
                    "creator": "Example", "tags": [],
                }]})
            if req.url.host == "cdn.stocksnap.io":
                return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
            raise AssertionError(f"Unexpected host: {req.url.host}")
        real_client = httpx.Client
        with patch("app.services.free_stock_sources.httpx.Client",
                   side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
            selected = find_openverse("office team", "team", set(), portrait=False, normalise=lambda raw: raw)
        self.assertEqual(selected[2], "openverse:ss123")
        self.assertEqual(selected[3], "CC0")

    def test_opaque_zero_result_is_replaced_with_actionable_provider_diagnostics(self):
        doc = Document()
        doc.add_heading("Collaborating in business", 1)
        doc.add_paragraph("This describes a conflict management meeting.")
        blob = BytesIO(); doc.save(blob)
        provider = Provider(pexels_key="", pixabay_key="", freepik_key="",
                            openverse_enabled=True, library=ImageLibrary(Store()))
        with patch("app.services.course_images.find_openverse", return_value=None):
            review = make_image_preview(blob.getvalue(), "Conflict.docx", provider=provider)
        self.assertEqual(review.details["images_found"], 0)
        self.assertEqual(review.details["provider_attempts"]["Openverse"], 1)
        self.assertEqual(len(review.details["missing_images"]), 1)
        missing = review.details["missing_images"][0]
        self.assertIn("pexels.com/search/", missing["pexels_url"])
        self.assertIn("stocksnap.io/search/", missing["stocksnap_url"])
        self.assertTrue(any("Pexels API is not configured" in msg for msg in review.details["warnings"]))


def test_refinement_accepts_photographic_synonyms_without_exact_heading_terms():
    provider = Provider(pexels_key="x", freepik_key="", openrouter_key="text-api-key")
    plan = ImagePlan(0, "Avoidance: when not acting immediately", "avoidance meeting",
                     "Initial workplace conflict photo description", "Under heading", "Employee chooses not to act immediately.")
    answer = [{"id": 0, "search": "thoughtful employee office conversation",
               "photo": "An employee calmly listening at a workplace meeting before responding to a colleague."}]
    with patch("app.services.course_images.OpenRouterScenes") as planner:
        planner.return_value.improve.return_value = answer
        provider.refine_plans("Conflict Management", "Chapter 1", [plan])
    assert provider.refined_sections == 1
    assert plan.search == "thoughtful employee office conversation"
