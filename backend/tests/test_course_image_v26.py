"""v26 regressions: no accidental credits, custom headings and free replacements."""
from __future__ import annotations

from io import BytesIO
import zipfile
from unittest import TestCase
from unittest.mock import patch

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from PIL import Image

from app.services.course_images import (
    CourseImagesError, Picture, Provider, _heading_level, _plans,
)
from app.services.course_image_library import ImageLibrary
from app.services.course_image_review import (
    finish_image_preview, make_image_preview, preview_jpeg, replace_with_free_image,
)
from test_free_first_course_images import FakeProvider, Store, document, jpeg


def replacement_photo() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1100, 750), (185, 91, 31)).save(output, "JPEG")
    return output.getvalue()


class AlternativeFreeProvider(FakeProvider):
    def find(self, query, *, portrait=False, generate=True, prompt="", subject=""):
        assert self.stock_source == "free"
        assert self.allow_freepik is False
        assert self.reuse_existing is False
        self.calls += 1
        return Picture(replacement_photo(), "Pixabay", "https://pixabay.com/photos/alternative-10/",
                       "pixabay:alt10", "Pixabay Content License", "Alternative Photographer")


class ImageFinderV26Tests(TestCase):
    def test_mixed_never_calls_freepik_without_opt_in(self):
        provider = Provider(pexels_key="", pixabay_key="", openverse_enabled=False,
                            freepik_key="secret-test-key", allow_freepik=False)
        self.assertFalse(provider.can_acquire(generate=False))
        with patch.object(provider, "_freepik") as api:
            self.assertIsNone(provider.find("medicine", subject="medication"))
            self.assertIsNone(provider.find("medicine", subject="medication"))
            api.assert_not_called()
        self.assertEqual(provider.freepik_attempts, 0)

    def test_failed_freepik_requests_consume_the_one_request_budget(self):
        provider = Provider(pexels_key="", pixabay_key="", openverse_enabled=False,
                            freepik_key="secret-test-key", allow_freepik=True,
                            freepik_attempt_limit=1, freepik_limit=3)
        with patch.object(provider, "_freepik") as api:
            api.return_value.search_photo.return_value = None
            for _ in range(12):
                self.assertIsNone(provider.find("safe medicines", subject="medication"))
            api.return_value.search_photo.assert_called_once()
        self.assertEqual(provider.freepik_attempts, 1)
        self.assertEqual(provider.freepik_downloads, 0)
        self.assertTrue(any("request budget" in warning for warning in provider.warnings))

    def test_custom_heading_style_numbered_bold_and_excluded_sections(self):
        doc = Document()
        doc.add_heading("Contents", 1)
        doc.add_heading("Medication example in contents", 2)
        doc.add_paragraph("This entry should not receive an image.")
        doc.add_heading("Medication and Dementia Care", 1)
        doc.add_paragraph("This is a real lesson on medication support in a care setting.")
        special = doc.styles.add_style("SLC Subtopic", WD_STYLE_TYPE.PARAGRAPH)
        special.base_style = doc.styles["Heading 2"]
        custom = doc.add_paragraph("Safe medication support", style=special)
        doc.add_paragraph("Care staff should follow safe medicines support procedures.")
        bold = doc.add_paragraph()
        bold.add_run("2.3 Support for medicines").bold = True
        doc.add_paragraph("Support is tailored to the person's recorded needs.")
        doc.add_heading("References", 1)
        doc.add_heading("Additional sources", 2)
        doc.add_paragraph("A reference list does not get illustrative photos.")
        self.assertEqual(_heading_level(custom), 2)
        self.assertEqual(_heading_level(bold), 2)
        plans, eligible = _plans(doc, "Medication and Dementia Care")
        self.assertEqual(eligible, 3)
        self.assertEqual(len(plans), 2)
        self.assertTrue(all("Contents" not in p.heading and "sources" not in p.heading.lower() for p in plans))

    def test_free_replacement_keeps_original_word_and_never_uses_freepik(self):
        original = document()
        preview_provider = FakeProvider()
        first = make_image_preview(original, "Unit.docx", provider=preview_provider)
        old_bytes = preview_jpeg(first.payload, 0)
        alternative = AlternativeFreeProvider()
        changed = replace_with_free_image(first.payload, 0, "medical records nurse", provider=alternative)
        self.assertEqual(alternative.calls, 1)
        self.assertEqual(alternative.freepik_attempts, 0)
        self.assertEqual(changed.details["freepik_stock_downloads"], 0)
        self.assertEqual(changed.details["free_replacements_used"], 1)
        self.assertEqual(changed.details["previews"][0]["source"], "Pixabay")
        self.assertEqual(changed.details["previews"][1]["source"], "Pexels")
        self.assertNotEqual(preview_jpeg(changed.payload, 0), old_bytes)
        self.assertNotIn("plan", changed.details["previews"][0])
        self.assertNotIn("identifier", changed.details["previews"][0])
        finished = finish_image_preview(changed.payload, [0])
        self.assertEqual(finished.details["photos_inserted"], 1)
        self.assertEqual(finished.details["image_sources"][0]["provider"], "Pixabay")
        restored = Document(BytesIO(finished.payload))
        source_doc = Document(BytesIO(original))
        self.assertEqual([(p.text, p.style.name) for p in restored.paragraphs if p.text],
                         [(p.text, p.style.name) for p in source_doc.paragraphs if p.text])
        self.assertEqual(len(restored.inline_shapes), 1)
        with self.assertRaises(CourseImagesError):
            replace_with_free_image(first.payload, 0, "https://example.com/photo", provider=alternative)

    def test_only_confirmed_free_photos_are_saved_to_reuse_library(self):
        store = Store()
        library = ImageLibrary(store)
        provider = Provider(pexels_key="test", pixabay_key="", freepik_key="",
                            openverse_enabled=False, library=library)
        counter = 0

        def stock(query, **kwargs):
            nonlocal counter
            counter += 1
            return Picture(jpeg(), "Pexels", "https://www.pexels.com/photo/example/",
                           f"pexels:{counter}", "Pexels License", "Photographer")

        with patch.object(provider, "_stock", side_effect=stock):
            preview = make_image_preview(document(), "Example.docx", provider=provider)
        self.assertEqual(store.objects, {}, "A preview should not cache rejected images")
        with patch("app.services.course_image_review.ImageLibrary", return_value=library):
            finish_image_preview(preview.payload, [0])
        self.assertEqual(len(store.objects), 2, "Only one approved photograph should be saved")
        item = preview.details["previews"][0]
        cached = library.find(item["search"], item["heading"])
        self.assertIsNotNone(cached)

    def test_zip_preview_budget_distributes_images_across_chapters(self):
        input_zip = BytesIO()
        with zipfile.ZipFile(input_zip, "w") as package:
            package.writestr("Chapter 1.docx", document())
            package.writestr("Chapter 2.docx", document())
        with patch("app.services.course_image_review.MAX_PREVIEW_IMAGES", 2):
            review = make_image_preview(input_zip.getvalue(), "Course.zip", provider=FakeProvider())
        self.assertEqual(len(review.details["previews"]), 2)
        self.assertEqual([p["chapter_index"] for p in review.details["previews"]], [0, 1])
        self.assertEqual(review.details["planned_image_positions"], 4)
        self.assertEqual(review.details["stock_lookups_attempted"], 2)
        self.assertTrue(any("preview limit reached" in w for w in review.details["warnings"]))
