# Find & Add Images — simplified interface (26 October 2026)

## What changed

1. **Single Word input:** Upload a `.docx`, choose an image source and press **Add images to Word**. When ready, select **Download Word document**. The result is a `.docx` with photographs inserted, not a ZIP of guides and covers.
2. **Batch input:** Upload a ZIP of multiple chapter `.docx` files. The download is a ZIP containing **only the edited Word files**, with no extra guide or cover folders.
3. **Visible image-source choices:** The browser's hard-to-read native dropdown is replaced with four high-contrast, keyboard-accessible selection cards: Pexels then Freepik, Freepik only, Pexels only, Freepik AI generation.
4. **Cleaner screen:** The requested "Search photographs via ..." and "What stays unchanged?" sections have been removed, together with the long licensing paragraph in the result area. Processing notices are collapsed.
5. **Optional guides preserved:** Users can still choose the separate **Image guides & covers** workflow when actually needed; it retains its guide-and-cover ZIP output.
6. **Image insertion logic:** Preserves existing lesson text and styles, skips excluded headings and does not generate unused cover pictures or image guides during insertion.

## Deploy

- Redeploy `backend/` to Railway to activate the direct DOCX response and correct download MIME type.
- Redeploy `frontend/` to Vercel to activate the new interface.
- Keep `FREEPIK_API_KEY` and `OPENROUTER_API_KEY` as private **backend** variables; the frontend never needs either key.
- Verify Railway `/health` reports build `2026.10.26-v19-direct-word-download-visible-image-options`.
- If the old dropdown is still displayed after deployment, confirm Vercel built from the new commit and refresh the website to clear a stale page.

## Validation

- `python -m unittest discover -s tests` (from `backend/`): **112 tests, 111 passed, 1 skipped** (LibreOffice/exact Garamond unavailable).
- Frontend TypeScript/JSX source parsed without syntax diagnostics. A full Next.js build was not possible in this offline environment because npm could not reach its package registry.
- Live Freepik/OpenRouter credentials were **not** used during testing; all image API tests are mocked.
