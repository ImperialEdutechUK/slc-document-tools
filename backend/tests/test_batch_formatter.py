import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch

from app.services.batch_formatter import (
    extract_batch_documents,
    format_zip_batch,
    should_skip_document,
)
from app.services.linked_images import LinkedImageResult
from app.services.zip_utils import safe_zip_member


def make_zip(items):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in items:
            archive.writestr(name, payload)
    return buffer.getvalue()


class BatchFormatterTests(unittest.TestCase):
    def test_safe_zip_member_rejects_traversal_windows_paths_and_macos_metadata(self):
        self.assertTrue(safe_zip_member("folder/unit.docx"))
        self.assertFalse(safe_zip_member("../unit.docx"))
        self.assertFalse(safe_zip_member("folder/../../unit.docx"))
        self.assertFalse(safe_zip_member("..\\unit.docx"))
        self.assertFalse(safe_zip_member("/absolute/unit.docx"))
        self.assertFalse(safe_zip_member("C:/temp/unit.docx"))
        self.assertFalse(safe_zip_member("__MACOSX/._unit.docx"))

    def test_skip_rules_cover_assignment_documents(self):
        self.assertTrue(should_skip_document("Unit 01 - Assignment Brief.docx"))
        self.assertTrue(should_skip_document("Unit_01_Written_Assignment_Template.docx"))
        self.assertFalse(should_skip_document("Unit 01 - Learning Content.docx"))

    def test_extract_batch_documents_ignores_unsafe_and_non_docx_members(self):
        payload = make_zip(
            [
                ("content/Unit 01.docx", b"one"),
                ("../escape.docx", b"bad"),
                ("__MACOSX/._Unit 01.docx", b"bad"),
                ("notes.txt", b"ignore"),
            ]
        )
        documents = extract_batch_documents(payload)
        self.assertEqual(1, len(documents))
        self.assertEqual("Unit 01.docx", documents[0].filename)
        self.assertEqual(b"one", documents[0].payload)

    @patch("app.services.batch_formatter.process")
    @patch("app.services.batch_formatter.download_linked_images")
    def test_batch_builds_formatted_reports_skipped_and_summary_zip(self, linked_mock, process_mock):
        linked_mock.return_value = LinkedImageResult()
        process_mock.return_value = (b"formatted-docx", ["ok"], "VALIDATION\n", None, None)
        source = make_zip(
            [
                ("Module/Unit 01.docx", b"content-one"),
                ("Module/Unit 01 - Assignment Brief.docx", b"brief"),
                ("Module/Unit 01 - Written Assignment Template.docx", b"template"),
                ("Module/Unit 02.docx", b"content-two"),
            ]
        )

        result = format_zip_batch(
            source,
            zip_filename="Course.zip",
            awarding_body="ATHE",
            course_name="Example Course",
            auto_download_links=True,
        )

        self.assertEqual("Course_SLC_formatted.zip", result.output_filename)
        self.assertEqual(4, result.details["documents_detected"])
        self.assertEqual(2, result.details["formatted"])
        self.assertEqual(2, result.details["skipped"])
        self.assertEqual(0, result.details["failed"])

        with zipfile.ZipFile(BytesIO(result.payload)) as archive:
            names = set(archive.namelist())
            self.assertIn("formatted/Unit 01_SLC_formatted.docx", names)
            self.assertIn("formatted/Unit 02_SLC_formatted.docx", names)
            self.assertIn("reports/Unit 01_validation_report.txt", names)
            self.assertIn("reports/Unit 02_validation_report.txt", names)
            self.assertIn("skipped/Unit 01 - Assignment Brief.docx", names)
            self.assertIn("skipped/Unit 01 - Written Assignment Template.docx", names)
            self.assertIn("batch_report.txt", names)
            report = archive.read("batch_report.txt").decode("utf-8")
            self.assertIn("Documents detected: 4", report)
            self.assertIn("Formatted:          2", report)
            self.assertIn("Skipped:            2", report)

        self.assertEqual(2, process_mock.call_count)
        first_call = process_mock.call_args_list[0].args
        self.assertEqual("Unit 01", first_call[5])

    @patch("app.services.batch_formatter.process")
    @patch("app.services.batch_formatter.download_linked_images")
    def test_one_document_failure_does_not_stop_remaining_batch(self, linked_mock, process_mock):
        linked_mock.return_value = LinkedImageResult()
        process_mock.side_effect = [ValueError("broken document"), (b"ok", [], "report", None, None)]
        source = make_zip([("Bad.docx", b"bad"), ("Good.docx", b"good")])

        result = format_zip_batch(source, zip_filename="Batch.zip")

        self.assertEqual(1, result.details["formatted"])
        self.assertEqual(1, result.details["failed"])
        self.assertEqual("Bad.docx", result.details["failures"][0]["filename"])
        self.assertIn("broken document", result.details["failures"][0]["reason"])
        with zipfile.ZipFile(BytesIO(result.payload)) as archive:
            self.assertIn("formatted/Good_SLC_formatted.docx", archive.namelist())


if __name__ == "__main__":
    unittest.main()
