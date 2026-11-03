# v24 — Free-first stock image search, image library and preview-and-confirm

**Build identifier:** `2026.11.03-v24-free-first-review`

## Requested settings delivered

- `mixed`: saved free image library > Pexels > Pixabay > Openverse (CC0/PDM) > licensed Freepik stock fallback.
- Freepik approval prompt: disabled; fallback starts automatically only after free providers cannot supply a suitable photo. Freepik stock may incur account charges.
- Reuse previously downloaded free photos: on. Stored in the backend's existing S3-compatible object storage or local storage; survives redeploy only with persistent S3.
- Pre-insertion preview: on. Review thumbnails, credit/licence information and select which photos to insert. Confirm makes no additional external image API calls.
- AI image generation: disabled for Find & Add Images, avoiding generation credits.
- Default maximum successful Freepik stock assets per job: 3. Configurable via `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS`. A failed provider API request may still be charged independently; check your Freepik plan.
- Default maximum preview images per job: 16; configurable via `COURSE_IMAGE_MAX_PREVIEW_IMAGES`.

## Install

1. Upload the full ZIP project or apply the patch with its relative paths. **Do not overwrite your own `.env`/Railway Variables or licensed font binaries**.
2. Add free-provider API keys to Railway: `PEXELS_API_KEY`, `PIXABAY_API_KEY`. `OPENVERSE_ENABLED=true` enables safe public-domain search without a key. Add `FREEPIK_API_KEY` or `MAGNIFIC_API_KEY` only if you want automatic licensed stock fallback.
3. Set persistent S3-compatible storage (`S3_*`) if you want image reuse and review state to survive Railway deployment/restart. Local `/tmp` works only on a single running instance.
4. Redeploy **Railway backend and Vercel frontend**. Verify the backend `/health` build identifier.
5. Test a DOCX and a chapter ZIP. Review selections, confirm, download the edited Word output, and inspect picture placements and text.

## Tests / limitations

- 141 offline backend unit tests pass; 1 prior LibreOffice/Garamond-dependent conversion test skipped due environment.
- Provider API calls were verified using offline mock HTTP responses. Your live API keys, credits, quotas and provider content have **not** been exercised.
- Frontend TSX parsed and transpiled with global TypeScript without syntax errors. A full Next.js build was **not** performed because npm package installation was unavailable in this runtime.
- One sample Word document containing two embedded images was rendered with LibreOffice and both pages visually inspected. Text and heading styles matched the original in tests.

Documentation: `docs/FREE_FIRST_IMAGE_WORKFLOW.md`.
