"""HTTP-level review/confirm flow; no external image providers or credentials."""
from __future__ import annotations

from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from docx import Document

from app.database import Base, get_db
from app.main import app
from app.services.course_image_review import make_image_preview
from test_free_first_course_images import FakeProvider, document, Store


class PreviewApiTests(TestCase):
    def setUp(self):
        self.db_engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.db_engine)
        session_factory = sessionmaker(bind=self.db_engine)
        def get_test_db():
            with session_factory() as db:
                yield db
        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db_engine.dispose()
        app.dependency_overrides.clear()

    def test_image_review_confirm_and_download(self):
        provider = FakeProvider()
        storage = Store()
        original = document()
        def previewer(raw, filename, *, course_name, image_source, reuse_existing, use_openrouter):
            self.assertEqual(image_source, "mixed")
            self.assertTrue(reuse_existing)
            return make_image_preview(raw, filename, course_name=course_name, image_source=image_source, reuse_existing=reuse_existing, provider=provider)
        with patch("app.main.make_image_preview", side_effect=previewer), patch("app.main.storage", storage):
            res = self.client.post(
                "/api/v1/course-images/review", files={"document": ("Unit 1.docx", original,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                data={"image_source": "mixed", "reuse_existing": "true"},
            )
            self.assertEqual(res.status_code, 200, res.text)
            manifest = res.json()
            self.assertEqual(manifest["status"], "review_ready")
            self.assertIsNone(manifest["download_url"])
            self.assertEqual(len(manifest["details"]["previews"]), 2)
            job_id = manifest["id"]
            thumb = self.client.get(manifest["details"]["previews"][0]["preview_url"])
            self.assertEqual(thumb.status_code, 200, thumb.text)
            self.assertEqual(thumb.headers["content-type"], "image/jpeg")
            self.assertTrue(thumb.content.startswith(b"\xff\xd8"))
            self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/download").status_code, 404)
            assert provider.calls == 2
            confirmed = self.client.post(f"/api/v1/course-images/reviews/{job_id}/confirm", json={"selected_ids": [1]})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            outcome = confirmed.json()
            self.assertEqual(outcome["details"]["photos_inserted"], 1)
            self.assertEqual(outcome["details"]["freepik_stock_downloads"], 0)
            self.assertEqual(provider.calls, 2)
            final = self.client.get(outcome["download_url"])
            self.assertEqual(final.status_code, 200, final.text)
            doc = Document(BytesIO(final.content))
            self.assertEqual(len(doc.inline_shapes), 1)
            self.assertEqual([p.text for p in doc.paragraphs if p.text],
                             [p.text for p in Document(BytesIO(original)).paragraphs if p.text])
            bad = self.client.post(f"/api/v1/course-images/reviews/{job_id}/confirm", json={"selected_ids": [999]})
            self.assertEqual(bad.status_code, 422)
