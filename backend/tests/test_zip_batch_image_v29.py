"""v29 ZIP image batch tests. No live stock-photo or paid API calls."""
from __future__ import annotations

from io import BytesIO
import zipfile
from unittest.mock import patch

from docx import Document
import pytest

from app.services.course_image_review import (
    make_image_preview, continue_image_preview, finish_image_preview,
)
from app.services.course_images import CourseImagesError
from test_free_first_course_images import FakeProvider, document


def course_zip(chapter_count: int = 10) -> tuple[bytes, dict[str, bytes]]:
    raw_docs = {}
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for number in range(1, chapter_count + 1):
            path = f"Course/Module {number:02d}/Lesson.docx"
            raw_docs[path] = document()
            archive.writestr(path, raw_docs[path])
        archive.writestr("Course/Other files/cover.png", b"cover preserved")
        archive.writestr("Course/Other files/notes.txt", b"source notes preserved")
        archive.writestr("Course/Assignment Brief.docx", document())
        archive.writestr("../unsafe.txt", b"must not be included")
    return payload.getvalue(), raw_docs


def test_zip_can_continue_searching_without_repeating_and_keep_all_original_paths():
    raw, chapters = course_zip(10)
    provider = FakeProvider()
    first = make_image_preview(raw, "Course.zip", course_name="Health and Social Care",
                               provider=provider, images_per_chapter=2)
    assert first.details["planned_image_positions"] == 20
    assert first.details["stock_lookups_attempted"] == 16
    assert first.details["remaining_positions"] == 4
    assert first.details["has_more"]
    assert len(first.details["previews"]) == 16
    assert len(first.details["chapter_progress"]) == 10
    assert all(item["found"] >= 1 for item in first.details["chapter_progress"])
    assert provider.calls == 16

    second = continue_image_preview(first.payload, provider=provider)
    assert not second.details["has_more"]
    assert second.details["remaining_positions"] == 0
    assert second.details["batch_count"] == 2
    assert second.details["stock_lookups_attempted"] == 20
    assert len(second.details["previews"]) == 20
    assert second.details["previews"][:16] == first.details["previews"]
    assert all(item["found"] == 2 for item in second.details["chapter_progress"])
    assert provider.calls == 20
    selected = [second.details["previews"][0]["id"], second.details["previews"][-1]["id"]]
    updated = finish_image_preview(second.payload, selected)
    assert provider.calls == 20, "Finalisation must never call providers"
    assert updated.details["photos_inserted"] == 2
    assert updated.details["documents_with_images"] == 2
    assert updated.details["documents_unchanged"] == 8
    with zipfile.ZipFile(BytesIO(raw)) as original_zip, zipfile.ZipFile(BytesIO(updated.payload)) as archive:
        found_names = archive.namelist()
        assert set(chapters).issubset(found_names)
        assert "Course/Other files/cover.png" in found_names
        assert archive.read("Course/Other files/cover.png") == b"cover preserved"
        assert archive.read("Course/Other files/notes.txt") == b"source notes preserved"
        assert archive.read("Course/Assignment Brief.docx") == original_zip.read("Course/Assignment Brief.docx")
        assert "../unsafe.txt" not in found_names
        assert "SLC_Image_Insertion_Report.csv" in found_names
        report = archive.read("SLC_Image_Insertion_Report.csv").decode("utf-8-sig")
        assert "Course/Module 01/Lesson.docx" in report
        approved_paths = {second.details["previews"][i]["chapter"] for i in selected}
        for path, original in chapters.items():
            if path in approved_paths:
                assert len(Document(BytesIO(archive.read(path))).inline_shapes) == 1
            else:
                assert archive.read(path) == original


def test_zip_target_images_per_chapter_limits_and_spreads_results():
    raw, chapters = course_zip(3)
    provider = FakeProvider()
    report = make_image_preview(raw, "Course.zip", provider=provider, images_per_chapter=1)
    assert len(report.details["previews"]) == len(chapters)
    assert all(item["planned"] == 1 and item["found"] == 1 for item in report.details["chapter_progress"])
    assert report.details["has_more"] is False
    with pytest.raises(CourseImagesError, match="between 1 and 5"):
        make_image_preview(raw, "Course.zip", images_per_chapter=6, provider=provider)
    with pytest.raises(CourseImagesError, match="searched"):
        continue_image_preview(report.payload, provider=provider)


def test_one_failed_insert_keeps_original_word_and_other_chapter_succeeds():
    raw, chapters = course_zip(2)
    report = make_image_preview(raw, "Course.zip", provider=FakeProvider(), images_per_chapter=1)
    from app.services.course_image_review import _insert_images
    calls = 0
    def fail_one(doc, plans, images):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Simulated Word layout problem")
        return _insert_images(doc, plans, images)
    with patch("app.services.course_image_review._insert_images", side_effect=fail_one):
        output = finish_image_preview(report.payload, [0, 1])
    assert output.details["photos_inserted"] == 1
    assert output.details["documents_failed"] == 1
    assert output.details["documents_with_images"] == 1
    assert output.details["chapter_results"][0]["status"].startswith("Failed")
    with zipfile.ZipFile(BytesIO(output.payload)) as archive:
        assert archive.read("Course/Module 01/Lesson.docx") == chapters["Course/Module 01/Lesson.docx"]
        assert len(Document(BytesIO(archive.read("Course/Module 02/Lesson.docx"))).inline_shapes) == 1


def test_invalid_zip_docx_is_reported_and_copied_unchanged():
    raw, chapters = course_zip(2)
    source = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as original, zipfile.ZipFile(source, "w") as archive:
        for name in original.namelist():
            if not name.startswith("../"):
                archive.writestr(name, original.read(name))
        archive.writestr("Course/Module Bad/Broken.docx", b"not a word file")
    preview = make_image_preview(source.getvalue(), "Course.zip", provider=FakeProvider())
    assert len(preview.details["chapter_progress"]) == 3
    assert any("cannot plan images" in w for w in preview.details["warnings"])
    result = finish_image_preview(preview.payload, [0])
    with zipfile.ZipFile(BytesIO(result.payload)) as output:
        assert output.read("Course/Module Bad/Broken.docx") == b"not a word file"


def test_zip_api_continue_and_restore_review():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from app.database import Base, get_db
    from app.main import app
    from test_free_first_course_images import Store
    from app.services.course_image_review import make_image_preview as real_make
    from app.services.course_image_review import continue_image_preview as real_continue

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    def dependency():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    raw, docs = course_zip(9)
    provider = FakeProvider()
    store = Store()
    try:
        with TestClient(app) as client, patch("app.main.storage", store), \
             patch("app.main.make_image_preview", side_effect=lambda raw, filename, **kw: real_make(raw, filename, provider=provider, **kw)), \
             patch("app.main.continue_image_preview", side_effect=lambda raw: real_continue(raw, provider=provider)):
            res = client.post("/api/v1/course-images/review", files={"document": ("Course.zip", raw, "application/zip")},
                              data={"images_per_chapter": "2", "course_name": "Care Training"})
            assert res.status_code == 200, res.text
            initial = res.json()
            assert initial["status"] == "review_ready"
            assert len(initial["details"]["previews"]) == 16
            assert initial["details"]["has_more"]
            job_id = initial["id"]
            continuation = client.post(f"/api/v1/course-images/reviews/{job_id}/continue")
            assert continuation.status_code == 200, continuation.text
            more = continuation.json()
            assert more["id"] == job_id
            assert not more["details"]["has_more"]
            assert len(more["details"]["previews"]) == 18
            assert more["details"]["batch_count"] == 2
            restored = client.get(f"/api/v1/course-images/reviews/{job_id}")
            assert restored.status_code == 200
            assert restored.json()["details"]["previews"] == more["details"]["previews"]
            assert restored.json()["download_url"] is None
            assert client.post(f"/api/v1/course-images/reviews/{job_id}/continue").status_code == 422
            confirmed = client.post(f"/api/v1/course-images/reviews/{job_id}/confirm", json={"selected_ids": [0, 17]})
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["details"]["photos_inserted"] == 2
            zip_result = client.get(confirmed.json()["download_url"])
            assert zip_result.status_code == 200, zip_result.text
            with zipfile.ZipFile(BytesIO(zip_result.content)) as archive:
                assert set(docs).issubset(set(archive.namelist()))
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
