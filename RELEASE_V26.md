# SLC Document Tools v26 — Find & Add Images repairs and cost limits

Build: `2026.11.03-v26-free-first-image-repair`

**Scope:** This update repairs the Find & Add Images workflow. The v25 Document Formatter's arbitrary-name Word hyperlink handling is retained without changing its behaviour.

## What changed

1. **Credit-safe defaults.** Mixed mode is still the initial setting, but it calls only saved/free providers unless **Allow paid Freepik stock fallback** is explicitly enabled. Freepik AI image generation is disabled throughout this tab. Freepik-only selection is an explicit opt-in.
2. **Real request cap.** Two independent backend limits now apply across all headings in one job: `COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=1`, including unsuccessful lookups, and `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=1`, counting successful licensed stock selections. A Freepik stock lookup tries only one licensed download endpoint, without a second chargeable fallback. For an unambiguous zero paid-stock-API policy, set both caps to `0`.
3. **Better heading detection.** In addition to Word Heading 1–6, custom styles based on those headings and explicitly numbered bold section titles are recognised. Contents, Summary, References, Bibliography, cover and front-matter headings are excluded; their subsections are not inserted into either.
4. **More usable stock queries.** Searches prefer short, observable photo subjects (e.g., a medication-care activity) instead of a long list of abstract unit/course terms.
5. **Re-search individual pictures.** The review screen now has **Find free alternative**, accepting editable keywords. It searches Pexels/Pixabay/Openverse only, preserves the other pictures and the original document, then updates the thumbnail without making another paid Freepik request. Up to eight successful replacements per review.
6. **More transparent reporting.** Jobs show eligible headings, planned positions, stock lookups, library hits, Freepik stock download counts and **Freepik lookup attempts**. Confirmation continues to use the private review bundle without re-querying image providers.
7. **Large ZIP fairness and bounded searches.** The preview budget is distributed round-robin across chapters instead of being consumed entirely by the first document. A separate 32-lookup / 75-second default search budget prevents indefinitely stalled jobs and reports when headings were skipped. Newly found free photos are saved to the reusable library **only after user confirmation**, avoiding recurrent rejected suggestions.

## Installation

1. Back up the deployed source and keep all Railway/Vercel environment variables and any separately licensed `backend/fonts/` files. Font binaries are **not included in this distribution**; do not overwrite or delete privately licensed fonts when deploying the source ZIP.
2. Merge the v26 source into GitHub (or apply the provided v26 patch ZIP over existing v25 source). Preserve local `.env`/Railway secrets and persistent S3-compatible storage.
3. On Railway, add `COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=1` and `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=1`. Set both to `0` if Freepik stock should never be accessed from Find & Add Images. Add `PEXELS_API_KEY` and `PIXABAY_API_KEY` where available; keep `OPENVERSE_ENABLED=true` if desired. Never put provider keys in Next.js `NEXT_PUBLIC_*` settings.
4. Deploy Railway **and** Vercel; check `GET /health` returns build `2026.11.03-v26-free-first-image-repair`. Check `NEXT_PUBLIC_API_URL`, `ALLOWED_ORIGINS` and configured storage if the UI cannot reach the new endpoints.
5. Test a real course DOCX using Mixed, the paid toggle off, saved-library and preview on. Verify Freepik lookup attempts = **0**. Change one preview using **Find free alternative**; confirm and download. Verify original text/headings remain unchanged and selected photos are embedded beneath the correct headings.
6. If enabling Freepik stock for a specific job, check your Freepik dashboard before and after: the app counts attempts/downloads, **not actual account charges**.

## Verification

- Backend regression suite: **159 passed, 1 skipped**. Includes v25 linked-image tests and new cost caps, custom-heading handling, approved-only caching, ZIP preview fairness, free-only replacement and full review → replace → confirm API tests.
- TypeScript/TSX syntax parsing passed with globally available TypeScript. A complete Next.js build could not be run because runtime dependencies were not available in the testing environment.
- No live external image provider keys or failing sample DOCX was supplied in this ZIP. Provider API reliability, stock relevance, licensing and billed-credit amounts must still be checked on deployment.

For current user steps and API arguments, see `docs/FREE_FIRST_IMAGE_WORKFLOW.md`.
