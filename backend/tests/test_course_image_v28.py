"""v28: course/heading context, Pixabay tag synonyms, safe human approval."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
from docx import Document

from app.services.course_image_review import finish_image_preview, make_image_preview
from app.services.course_images import _plans
from app.services.course_visual_context import contextual_scene
from app.services.free_stock_sources import _rank, find_pixabay
from test_free_first_course_images import FakeProvider, Store, jpeg


def test_safeguarding_images_follow_the_course_and_individual_heading():
    course = "Level 5 Award in Leading the Safeguarding of Children and Young People in Care Settings"
    report = contextual_scene(course, "Reporting, referral and escalation", "Raise the concern immediately")
    participation = contextual_scene(course, "Voice, rights and participation", "Respect the child's views")
    assert report is not None and participation is not None
    assert "report" in report.search
    assert "listening" in participation.description
    assert report.search != participation.search
    assert "children" in report.description.lower()


def test_course_context_changes_meaning_of_generic_heading():
    care = contextual_scene("Safeguarding in Adult Care", "Training and supervision", "Team learning")
    hr = contextual_scene("HR and Payroll Management for the UK", "Training and supervision", "Workforce training")
    assert care and hr
    assert care.search != hr.search
    assert "care" in care.search
    assert "human resources" in hr.description.lower() or "hr" in hr.description.lower()


def test_pixabay_rank_uses_scene_meaning_not_exact_academic_heading_words():
    heading = "Collaborating: working together for a solution addressing underlying needs"
    assert _rank("colleagues solving problem meeting", heading, "team, meeting, teamwork, office") > 0
    assert _rank("business negotiation discussion", "Negotiation: reaching agreement", "business, office, meeting") > 0
    assert _rank("payroll accountant office computer", "Checking wage deductions", "salary, payroll, calculator, office") > 0
    assert _rank("manager leading office meeting", "Competing or directing", "sunset, mountains, lake, office") == 0
    assert _rank("nurse medication elderly care", "Safe medicine support", "business, corporate, tourism, beach") == 0


def test_pixabay_downloads_relevant_photo_not_unrelated_one():
    requests = []

    def handler(req):
        requests.append(str(req.url))
        if req.url.host == "pixabay.com":
            return httpx.Response(200, json={"hits": [
                {"id": 1, "tags": "mountains, beach, sunset", "largeImageURL": "https://cdn.pixabay.com/photo/wrong.jpg"},
                {"id": 2, "tags": "business, meeting, office", "largeImageURL": "https://cdn.pixabay.com/photo/appropriate.jpg",
                 "pageURL": "https://pixabay.com/photos/example-2/", "user": "Free Creator"},
            ]})
        if req.url.host == "cdn.pixabay.com":
            assert req.url.path == "/photo/appropriate.jpg"
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        raise AssertionError("Unexpected host")

    real_client = httpx.Client
    with patch("app.services.free_stock_sources.storage", Store()), patch(
        "app.services.free_stock_sources.httpx.Client",
        side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    ):
        photo = find_pixabay("test-key", "business negotiation discussion", "Negotiation: reaching agreement", set(),
                             portrait=False, normalise=lambda b: b)
    assert photo and photo[2] == "pixabay:2"
    assert len([x for x in requests if "cdn.pixabay.com" in x]) == 1


def test_pixabay_tries_one_broader_query_when_no_first_match():
    photo_lookups = []

    def handler(req):
        if req.url.host == "pixabay.com":
            q = req.url.params.get("q")
            photo_lookups.append(q)
            if q == "business meeting":
                return httpx.Response(200, json={"hits": [{
                    "id": 8, "tags": "business office meeting", "largeImageURL": "https://cdn.pixabay.com/photo/8.jpg",
                }]})
            return httpx.Response(200, json={"hits": []})
        if req.url.host == "cdn.pixabay.com":
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        raise AssertionError(req.url)

    real_client = httpx.Client
    with patch("app.services.free_stock_sources.storage", Store()), patch(
        "app.services.free_stock_sources.httpx.Client",
        side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    ):
        result = find_pixabay("key", "business negotiation", "Structured negotiation", set(), portrait=False,
                              normalise=lambda b: b)
    assert result and result[2] == "pixabay:8"
    assert photo_lookups == ["business negotiation", "business meeting"]


def test_full_chapter_content_can_identify_discipline_from_generic_filename():
    doc = Document()
    doc.add_heading("Salary deductions", 1)
    doc.add_paragraph("Payroll administrators calculate tax deductions and pensions for the UK workforce.")
    doc.add_heading("Employee records", 2)
    doc.add_paragraph("Confidential payroll records must be processed accurately.")
    doc.add_heading("References", 1)
    doc.add_paragraph("This section must not contain inserted pictures.")
    planned, eligible = _plans(doc, "Unit 1")
    assert eligible == 2
    assert planned
    assert any("payroll" in x.search or "human resources" in x.description.lower() for x in planned)


def test_previews_explain_photographic_scene_and_preserve_original_word_content():
    doc = Document()
    doc.add_heading("Leading the Safeguarding of Children", 1)
    doc.add_paragraph("Care staff protect children and young people with safeguarding procedures.")
    doc.add_heading("Reporting and referral", 2)
    doc.add_paragraph("Staff record a safeguarding concern and follow reporting procedures.")
    doc.add_heading("Learning outcomes", 2)
    doc.add_paragraph("This isn't a photographic section.")
    doc.add_heading("References", 1)
    doc.add_paragraph("Reference content remains untouched.")
    out = BytesIO(); doc.save(out)
    original = out.getvalue()
    review = make_image_preview(original, "Children.docx", course_name="Safeguarding Children in Care",
                                provider=FakeProvider())
    assert review.details["previews"]
    assert all("intended_scene" in p and "photo" in p["intended_scene"].lower()
               for p in review.details["previews"])
    assert all("Learning outcomes" not in p["heading"] for p in review.details["previews"])
    assert review.details["matching_policy"].startswith("Course title")
    final = finish_image_preview(review.payload, [p["id"] for p in review.details["previews"]])
    initial = Document(BytesIO(original))
    revised = Document(BytesIO(final.payload))
    assert [(p.text, p.style.name) for p in initial.paragraphs if p.text] == [
        (p.text, p.style.name) for p in revised.paragraphs if p.text
    ]


def test_frontend_does_not_auto_approve_found_photos():
    source = Path(__file__).resolve().parents[2] / "frontend" / "app" / "page.tsx"
    text = source.read_text()
    assert "setSelectedImages([]);" in text
    assert "Intended photograph:" in text


def test_end_to_end_pixabay_preview_for_abstract_conflict_heading():
    """A previously rejected tag set is now a proposed photo for human review."""
    from app.services.course_images import Provider
    from app.services.course_image_library import ImageLibrary

    doc = Document()
    doc.add_heading("Negotiation: reaching agreement through structured discussion", 1)
    doc.add_paragraph("Colleagues try to reach a suitable agreement during a workplace disagreement.")
    blob = BytesIO(); doc.save(blob)
    calls = []

    def handle(req):
        calls.append(req.url.host)
        if req.url.host == "pixabay.com":
            assert req.url.params["image_type"] == "photo"
            assert req.url.params["orientation"] == "horizontal"
            return httpx.Response(200, json={"hits": [{
                "id": 321, "tags": "business, meeting, office", "largeImageURL": "https://cdn.pixabay.com/photo/321.jpg",
                "pageURL": "https://pixabay.com/photos/team-321/", "user": "Artist",
            }]})
        if req.url.host == "cdn.pixabay.com":
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        raise AssertionError(req.url)

    provider = Provider(pexels_key="", pixabay_key="valid-test-key", openverse_enabled=False,
                        freepik_key="", library=ImageLibrary(Store()))
    real_client = httpx.Client
    with patch("app.services.free_stock_sources.storage", Store()), patch(
        "app.services.free_stock_sources.httpx.Client",
        side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    ):
        review = make_image_preview(blob.getvalue(), "Conflict.docx", course_name="Conflict Management",
                                    image_source="pixabay", provider=provider)
    assert review.details["images_found"] == 1
    assert review.details["previews"][0]["source"] == "Pixabay"
    assert "negotiation" in review.details["previews"][0]["heading"].lower()
    assert review.details["previews"][0]["search"] == "business negotiation discussion"
    assert review.details["freepik_search_attempts"] == 0
    assert "cdn.pixabay.com" in calls
