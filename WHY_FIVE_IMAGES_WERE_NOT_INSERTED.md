# Document Formatter — 5 downloaded / 0 inserted fix

**Build:** `2026.11.03-v22-content-safe-image-anchors`

## What was happening

Linked-photo discovery ran on the **original** DOCX. After downloading the image
bytes, the formatter removed or modified the uploaded document's original
front matter (the uploaded cover / first page and old table of contents). It
then tried to insert each image by finding its original hyperlink target in
the **remaining** Word XML. If the matching source paragraph had been removed,
all five downloads could display **NOT INSERTED** even though downloading had
succeeded. Some Word-field hyperlinks also lost a reliable URL-based match.

This was reproducible with a document whose chapter content and image links
appear before a manual page break: five images downloaded, but all five image
anchors were discarded as though they were a removable cover page.

## Changes

1. Cover / contents cleanup now recognises substantive chapter content before
   the page-break or TOC boundary and preserves it rather than discarding it.
   Genuine title-only cover pages can still be removed.
2. Every discovered image link carries the original Word paragraph text in
   addition to its URL and visible label.
3. Image placement first matches the original image URL, then safely tries an
   exact paragraph-text match if the URL relationship is missing.
4. Unnumbered links remain supported. Images are placed below their associated
   Word paragraph without changing the surrounding text and headings.
5. Single-DOCX and ZIP-batch formatting both use the improved matching.
6. The backend job metadata now identifies itself with the v22 build string.

## Deploy

- For an existing v21 installation, merge the **patch ZIP** files into the same
  locations in GitHub; redeploy the Railway backend. The frontend is unchanged
  by this patch, so a Vercel redeploy is optional (unless you also changed the
  frontend files).
- Alternatively, deploy the complete-project ZIP and keep your existing
  environment variables and private font files.
- After deployment, upload and format the **original DOCX again**. The old
  preview/results remain tied to their previous job and do not update.
- Under the job result, confirm **Backend build:**
  `2026.11.03-v22-content-safe-image-anchors`.
- Check **Linked image results**. An image should show **INSERTED** only after
  its media is embedded in the returned Word file.

## Validation and limits

- Four new end-to-end regression tests passed, including the original failure
  pattern (five links before a page break), a TOC after content, and a lost
  hyperlink relationship.
- The DOCX smoke test embedded all five sample images; the document was
  rendered and visually reviewed across six pages.
- Full backend test suite: 130 run, 1 skipped, 1 pre-existing failure in a
  separate AI image-generation polling test; no new failures.
- The actual DOCX that produced the user's screenshot was not supplied, so
  this patch addresses confirmed failure paths in the formatter, not a
  verified diagnosis of the exact document. If that DOCX still shows 5/0,
  inspect its original hyperlink structure and validation report to determine
  which anchors are missing.
