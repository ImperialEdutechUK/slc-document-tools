"""Verify real HTTP endpoints report a storage outage as 503, never 500/422."""
from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Job
from app.services.storage import StorageError


class StorageEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        session_factory = sessionmaker(bind=self.engine)

        def get_test_db():
            with session_factory() as session:
                yield session

        self.session_factory = session_factory
        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def pdf():
        pdf = PdfWriter()
        pdf.add_blank_page(width=300, height=500)
        pdf.add_blank_page(width=300, height=500)
        buffer = BytesIO()
        pdf.write(buffer)
        return buffer.getvalue()

    @patch("app.main.storage.put_bytes", side_effect=StorageError("Object storage rejected the request signature (SignatureDoesNotMatch)."))
    def test_remove_pages_returns_503_and_records_failed_job(self, _save):
        response = self.client.post(
            "/api/v1/pdf/remove-pages",
            files={"file": ("example.pdf", self.pdf(), "application/pdf")},
            data={"pages": "1"},
        )
        self.assertEqual(503, response.status_code)
        self.assertIn("SignatureDoesNotMatch", response.json()["detail"])
        with self.session_factory() as session:
            jobs = session.query(Job).all()
            self.assertEqual(1, len(jobs))
            self.assertEqual("failed", jobs[0].status)
            self.assertIn("SignatureDoesNotMatch", jobs[0].error_message)

    @patch("app.main.storage.put_bytes", side_effect=StorageError("Object storage credentials need updating."))
    @patch("app.main.convert_word_files", return_value=("example.pdf", b"%PDF", "application/pdf", {}))
    def test_word_to_pdf_returns_503_not_unhelpful_500(self, _convert, _save):
        response = self.client.post(
            "/api/v1/word-to-pdf",
            files={"files": ("example.docx", b"fake-docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        self.assertEqual(503, response.status_code)
        self.assertIn("credentials", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
