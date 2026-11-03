# Document Formatter: downloaded image links not inserted (v23)

Backend build: `2026.11.03-v23-stable-image-bookmarks`

## Observed symptom

Five images can be downloaded successfully while all five still show `NOT INSERTED` with the message that the source photo link was not found after formatting.

## Confirmed defects corrected

1. Image hyperlinks were discovered from the *original* Word document, but cover-page and Table of Contents cleanup could discard their source paragraphs before image insertion. This happens, for example, when five photo links share one paragraph before a manual page break, or when five links are inside a table preceding a TOC.
2. A second cleanup step removed the first top-level element if it contained a drawing. It also removed a legitimate **table of teaching images** inserted by the formatter, because it matched drawings recursively. That could discard successfully inserted images after the insertion counter was calculated.
3. The UI originally trusted the intermediate insertion counter rather than confirming that image relationships were referenced in the completed DOCX.

## Changes

- Source image links receive unique, invisible Word bookmark anchors **before** any cover/TOC cleanup.
- Front-matter deletion protects those marked paragraphs, including links inside Word tables and multiple links within a single paragraph.
- Image placement first uses these stable anchors, then falls back to the existing URL and paragraph-text methods.
- Cover-art cleanup is limited to standalone blank picture paragraphs. It no longer recursively deletes a table containing inserted teaching images.
- The final saved DOCX is audited for the exact inserted image relationships. If later formatting removes an image, it is no longer reported as `INSERTED`.
- Named hyperlinks and `IMAGE1`, `IMAGE2`, etc. placeholders continue to work, with no source text/headings rewritten as part of this fix.

## Test verification

- Confirmed that v22 produced `5 downloaded / 0 inserted` for five image links in one paragraph before a page break, and five image links in a table before the TOC.
- New end-to-end tests confirm `5 downloaded / 5 embedded` for both DOCX structures in v23.
- Checked two complete formatted Word documents through LibreOffice render to page images. All five illustrations were present in the final documents.
- The final-image audit was tested with a deliberately damaged embedding reference: it correctly changed the report to `0 inserted`.
- All targeted image-link and image-insertion tests pass. The wider test suite continues to have one pre-existing failure in the unrelated AI image generation/polling test.

## Deploy instructions

1. **Patch deployment:** unzip `slc-document-tools-image-insertion-v23-patch.zip` and copy its `backend/...` paths over the matching files in your GitHub repo (or use the complete project ZIP, preserving any repo-specific secrets/configuration).
2. Commit/push and deploy the **Railway backend**. Vercel frontend code is unchanged in v23, so a Vercel redeploy is optional.
3. In the Document Formatter tab, upload the **original DOCX again**, not the previously formatted file. Run a new job with automatic linked-image retrieval turned on.
4. On the new job result, check `Backend build: 2026.11.03-v23-stable-image-bookmarks` (displayed near the formatted document download).
5. Read the new job's validation report; it will show the number of images actually embedded in the final DOCX.

This change was verified against simulated DOCX structures reproducing the symptom. The actual problematic source DOCX shown in the screenshot was not supplied, so the result for that document is not yet independently verified.
