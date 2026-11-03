# SLC Document Tools v25 — insert linked pictures by URL, not by label

Build: `2026.11.03-v25-name-independent-image-links`

This release includes the complete v24 Find & Add Images free-first workflow and the earlier v23 content-preserving formatting fixes. No v24 settings have been removed.

## Root cause and changes

The Document Formatter previously filtered some external Word hyperlinks based on the visible text or whether the URL looked like an image. A hyperlink named `Click to view` targeting an extensionless image endpoint would never even be downloaded, and reused `Image 01` labels could conflict.

- Inspect genuine external Word hyperlinks independent of their display text, as well as standard raster URLs and stock-photo URLs.
- Also discover Word `w:fldSimple` and complex-field links.
- Download and verify real JPG/PNG/WebP image bytes returned by opaque paths, signed links and extensionless endpoints.
- For image-labelled hyperlinks or recognizable photo landing pages, resolve an advertised image where possible. Do not take a generic webpage's social-preview image when no evidence of an image link exists.
- Anchor each successful download to its exact source paragraph **before** changing the cover, contents page and formatting. Insert linked images before handling unlinked/manual `IMAGE n` placeholders.
- Repeated labels like `Image 01` get unique internal IDs and do not swap photos.
- Replace a standalone `Image 01` marker with the picture. Preserve other hyperlink names/lesson paragraphs and insert pictures immediately after them.
- Keep ordinary website references unchanged and report those URLs as `NOT AN IMAGE` rather than `DOWNLOAD FAILED`. Summary/References/TOC are excluded.
- Check the final DOCX image references so 'inserted' means present in the saved Word file.

## Deployment

1. Use the **full v25 ZIP**, or extract the **v25 patch ZIP** over the existing GitHub project while preserving folder structure.
2. Commit and push. Redeploy **Railway** (Word link scanner/insertion backend) and **Vercel** (updated `NOT AN IMAGE` statuses).
3. Confirm `/health` returns `2026.11.03-v25-name-independent-image-links`.
4. In **Document Formatter**, enable **Automatically retrieve linked images** and upload the **original source DOCX**, not a previously formatted output.
5. Download the new DOCX and validation report. Check `INSERTED`, `DOWNLOAD FAILED`, and `NOT AN IMAGE` rows as appropriate.

## Verification and limitations

- Offline regression suite: **152 passed, 1 skipped**. Tests cover `Image 01`, arbitrary labels, extensionless links, Word field hyperlinks, duplicate image names, benign non-image web links, References exclusion, batch formatting, and final-DOCX picture references.
- A source Word document reproducing `Image 01` beneath a numbered heading was formatted and rendered. The `Image 01` hyperlink was replaced by an actual embedded picture, while all lesson text stayed intact.
- No live Freepik/stock API credentials or the user's exact failing DOCX were available for this verification. Links that are inaccessible, behind sign-in, expired, or not an image/known photo page cannot be inserted automatically. An underlined label alone, without a hyperlink target stored in Word, is not a downloadable link.
- This patch does not use additional AI image generation or change Freepik pricing.
