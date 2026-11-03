# SLC Document Formatter: linked-image insertion fix

**Build:** `2026.11.03-v20-reliable-linked-image-placement`

## What was wrong

The linked-image downloader and the Word image-placement code used different
matching rules. The downloader could find an IMAGE1 / IMAGE5 label and report
**DOWNLOADED**, even when the insertion routine refused to match that paragraph.
This occurred with hyperlinks inside paragraphs, Word-inserted invisible
characters, nonstandard punctuation, or labels alongside image URLs or other
text. Download success did not mean that the formatted DOCX contained the image.

## Changes

- Normalise invisible Word characters in image labels before scanning and matching.
- Accept isolated labels such as `IMAGE5`, `IMAGE5:`, `Image 5`, and `IMAGE 5`.
- Locate linked IMAGE labels even when they share a paragraph with other content.
- As a fallback, insert a downloaded picture after the paragraph containing its
  **actual source URL or hyperlink**, without deleting that paragraph's wording.
- Preserve unrelated text and headings; isolated IMAGE placeholder paragraphs
  are replaced with pictures, as before.
- Report the actual inserted image numbers separately from downloaded images.
  The Document Formatter now labels linked results **INSERTED**, **NOT INSERTED**,
  or **DOWNLOAD FAILED**. Unplaced photos produce a warning and appear in the
  validation report.
- Apply the same URL-anchored placement to ZIP batch formatting and provide
  batch image-placement totals.
- Add regression tests that open the real output Word document and verify the
  number of embedded pictures and preserved paragraph text.

## Deploy

1. Apply the updated files in the ZIP over your existing GitHub project. Keep
   all existing `.env` values, stored data, and `backend/fonts/` assets.
2. Redeploy **Railway** for the backend changes and **Vercel** for the interface.
3. In the Document Formatter, leave **Automatically retrieve linked images** on,
   then upload the original DOCX (or ZIP) again and select **Format document**.
4. Confirm the **Inserted** metric and the **INSERTED** status next to the linked
   images. Download the formatted Word file or preview it.
5. If an image shows **NOT INSERTED**, read the **Validation report**. The image
   must have an IMAGE-number placeholder or a genuine associated image link in
   the document. Stock links still require the configured Magnific/Freepik API
   credentials on the backend.

## Targeted test command

```sh
cd backend
python -m unittest discover -s tests -p 'test_linked_image_placement_integration.py' -v
```

All image insertion and batch-formatting regression tests passed. An existing,
unrelated AI photo-generation polling test fails on both the supplied original
project and the repaired version; this fix deliberately leaves that separate
feature unchanged.

**Packaging note:** The fix package contains code only; existing Garamond font
binaries are not redistributed. Retain the original `backend/fonts/` directory
in your deployment.
