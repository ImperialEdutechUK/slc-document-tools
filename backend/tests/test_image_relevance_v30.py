"""v30: subject locks, no corporate-care fallbacks and opt-in pixel checks."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import patch
import json

import httpx
from PIL import Image
from docx import Document
import pytest

from app.services.course_image_relevance import (
    RelevanceContext, find_domain, lock_query, metadata_decision,
)
from app.services.course_image_vision import OpenRouterVision
from app.services.course_image_review import make_image_preview
from app.services.course_images import Provider, CourseImagesError


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (1300, 820), (50, 130, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def care_document():
    doc = Document()
    doc.add_heading("Leading and Managing a Care Team", 1)
    doc.add_paragraph("A care manager works with support workers in a residential care setting.")
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def care_context(**kwargs):
    values = dict(domain="adult_safeguarding", course="Managing a Care Team",
                  heading="Team Communication and Leadership",
                  intended_scene="Care staff discussing a resident's needs",
                  query="care worker team meeting", mode="strict")
    values.update(kwargs)
    return RelevanceContext(**values)


def test_course_title_overrides_generic_management_and_supports_care_terminology():
    assert find_domain("Award in Managing a Care Team Skills for New Managers") == "adult_safeguarding"
    assert find_domain("Award in Care Planning and Assessment") == "adult_safeguarding"
    assert find_domain("Managing Quality and Service Delivery in Healthcare") == "healthcare"
    assert find_domain("Safe Administration of Medication") == "healthcare"
    assert find_domain("Award in Early Years Safeguarding") == "child_safeguarding"
    assert find_domain("Award in Personalisation in Care and Support Services") == "adult_safeguarding"
    assert find_domain("Unit 1 - Leadership and Management", "Leading a Care Team", "A nurse leads a care team in a residential care home") == "adult_safeguarding"
    assert lock_query("business team office meeting", "adult_safeguarding").startswith("care worker")


def test_strict_care_rejects_business_boardroom_and_accepts_care_team():
    context = care_context()
    wrong = metadata_decision("Corporate executive meeting in a boardroom", context)
    assert not wrong.eligible
    assert "Corporate" in wrong.reason
    right = metadata_decision("Care workers and nursing staff in a care home team meeting", context)
    assert right.eligible, right.reason
    assert right.score >= 80
    assert not metadata_decision("Office group meeting", context).eligible
    assert not metadata_decision("", context).eligible


def test_strict_medication_requires_medication_activity_evidence():
    context = care_context(heading="Safe Medication Administration", query="nurse checking medication label")
    assert metadata_decision("Nurse checking a patient's medication and prescription label", context).eligible
    assert not metadata_decision("Nurse looking out of a hospital window", context).eligible


def test_pexels_filters_wrong_domain_and_uses_care_photo():
    requested_photos = []
    def handle(request):
        if request.url.host == "api.pexels.com":
            return httpx.Response(200, json={"photos": [
                {"id": 11, "alt": "Corporate business team meeting in boardroom",
                 "url": "https://www.pexels.com/photo/corporate-business-11/",
                 "src": {"large2x": "https://images.pexels.com/photos/11/p.jpg"}},
                {"id": 12, "alt": "Care workers and nurses in care home team meeting",
                 "url": "https://www.pexels.com/photo/care-worker-nurse-12/",
                 "src": {"large2x": "https://images.pexels.com/photos/12/p.jpg"}},
            ]})
        if request.url.host == "images.pexels.com":
            requested_photos.append(str(request.url))
            return httpx.Response(200, content=image_bytes(), headers={"content-type": "image/jpeg"})
        raise AssertionError(request.url)
    real_client = httpx.Client
    provider = Provider(pexels_key="fake", pixabay_key="", freepik_key="", openverse_enabled=False, reuse_existing=False)
    provider.active_context = care_context()
    with patch("app.services.course_images.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
        photo = provider.find("business office team meeting", subject="Team Communication and Leadership", generate=False)
    assert photo is not None
    assert photo.identifier == "pexels:12"
    assert photo.verification_method == "metadata"
    assert photo.relevance_score >= 80
    assert len(requested_photos) == 1
    assert "/12/" in requested_photos[0]
    assert provider.relevance_rejections > 0


def test_pexels_no_evidence_is_skipped_in_strict_mode():
    requests = []
    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"photos": [
            {"id": 3, "alt": "Business managers talking in office",
             "url": "https://www.pexels.com/photo/office-managers-3/",
             "src": {"large": "https://images.pexels.com/photos/3/p.jpg"}},
        ]})
    real_client = httpx.Client
    provider = Provider(pexels_key="fake", pixabay_key="", freepik_key="", openverse_enabled=False, reuse_existing=False)
    provider.active_context = care_context()
    with patch("app.services.course_images.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
        assert provider.find("office managers meeting", subject="Team Communication and Leadership", generate=False) is None
    assert len(requests) == 1, "Do not download irrelevant stock photos"


def test_vision_checks_pixels_and_rejects_even_when_stock_metadata_looks_correct():
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "relevant": False, "confidence": 99,
            "reason": "The image shows accountants in a corporate boardroom"
        })}}]})
    real_client = httpx.Client
    with patch("app.services.course_image_vision.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
        result = OpenRouterVision("test-key", "openai/gpt-4o-mini").check(image_bytes(), care_context(verify_pixels=True))
    assert not result.eligible
    assert result.score == 99  # Confidence in the negative verdict is not approval.
    assert seen[0]["messages"][1]["content"][1]["type"] == "image_url"


def test_vision_on_without_config_fails_safe():
    provider = Provider(pexels_key="x", pixabay_key="", freepik_key="", openverse_enabled=False,
                        openrouter_key="")
    with pytest.raises(CourseImagesError, match="OPENROUTER_API_KEY"):
        make_image_preview(care_document(), "Care.docx", course_name="Managing a Care Team",
                           verify_images=True, provider=provider)


def test_strict_review_persists_subject_lock_and_skips_unmatched_photos():
    provider = Provider(pexels_key="x", pixabay_key="", freepik_key="", openverse_enabled=False,
                        reuse_existing=False)
    # Provider never finds a suitable stock photograph: the plan still exists,
    # but no incorrect filler image is added to the review.
    with patch.object(provider, "_stock", return_value=None):
        preview = make_image_preview(care_document(), "Care.docx", course_name="Care Leadership",
                                     provider=provider)
    assert preview.details["course_domain"] == "adult_safeguarding"
    assert preview.details["chapter_domains"] == ["adult_safeguarding"]
    assert preview.details["relevance_mode"] == "strict"
    assert preview.details["images_found"] == 0
    assert preview.details["missing_images"]
