# SLC Find & Add Images — free-first, review-before-insert (v24)

## Default behaviour

This change is scoped to the **Find & Add Images** tab, not to the separate Document Formatter linked-image feature.

- **Image source:** `mixed` — cached free photos, Pexels, Pixabay, Openverse CC0/PDM public-domain, then Freepik licensed stock.
- **Reuse existing image library:** enabled (cache only Pexels, Pixabay, Openverse JPEGs with source metadata; exact search-and-heading match).
- **Preview before insertion:** enabled; click to accept/deselect each candidate before creating the document.
- **Freepik approval:** not requested; stock fallback can happen when a free photograph is not found. Charged API use depends on your Freepik plan. AI-image generation is deliberately disabled (no AI image generation credits are consumed).
- **Maximum automatic Freepik stock selections:** 3 per job by default. Up to one search-result download candidate is considered per heading; some API attempts may count against your account separately.
- **Maximum preview images:** 16 per job by default. This is important for cost containment and preventing abusive bulk queries.

## Railway backend variables

Add `PEXELS_API_KEY` (Pexels API) and `PIXABAY_API_KEY` (Pixabay API). Openverse is enabled by default without a key; set `OPENVERSE_ENABLED=false` to disable. Set `FREEPIK_API_KEY` or `MAGNIFIC_API_KEY` only if you want paid Freepik stock fallback. Freepik remains optional. Set `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=0` to guarantee that mixed mode never downloads Freepik assets. For resilient image reuse and private preview bundles, set the existing S3-compatible `S3_*` bucket variables: otherwise storage uses a local directory that may disappear when Railway restarts.

No new Python runtime packages are necessary. The backend uses the project's existing `httpx`, Pillow, python-docx and object storage support.

## User interaction

1. In Find & Add Images, upload DOCX or ZIP and leave **Free first, Freepik fallback** selected.
2. Keep **Reuse images from the saved library** and **Preview images before adding** checked.
3. Click **Find images and preview**. The backend may perform Freepik stock downloads during this step, automatically, only if free sources fail.
4. Inspect thumbnails, provider, photographer, attribution/licence and document heading. Uncheck unwanted images, then click **Insert selected images and download**.
5. Download the edited Word file or ZIP. The confirm step does **not** call image APIs again. Existing text, headings, paragraph styles and Word structure remain intact; page breaks can shift because photos take space.

If **Preview images before adding** is unchecked, the old direct insertion endpoint still works (with new free-first selection). The separate guide mode remains available. The results show source provenance, library hits, Freepik stock downloads and warnings.

## APIs

- `POST /api/v1/course-images/review` multipart fields: `document`, `course_name`, `image_source=mixed`, `reuse_existing=true`, `use_openrouter=false`.
- Response is a normal job object with status `review_ready`, a `details.previews[]` array of candidate metadata and a `preview_url` per item. It intentionally does **not** expose the private ZIP of original source DOCX and previews as a direct download.
- `GET /api/v1/course-images/reviews/{job_id}/images/{image_id}` returns one preview JPEG (random job ID is required).
- `POST /api/v1/course-images/reviews/{job_id}/confirm` accepts JSON `{ "selected_ids": [0,2] }`, returns a completed job with a standard Word/ZIP download URL.
- `POST /api/v1/course-images/prepare` remains for existing clients. Fields include `image_source`, `reuse_existing`, `mode` and `use_openrouter`.

For a real deployment, normal application access protections and object-storage lifecycle/retention policies should be configured. Preview jobs contain user documents in private storage; they are *not* the persistent stock-photo library.

## Content and licensing safeguards

- Pexels API: https://www.pexels.com/api/documentation/ — free API, follow its credit/link rules.
- Pixabay API: https://pixabay.com/api/docs/ — cache search results for 24 hours; do not bulk-download, or permanently hotlink, stock photos. Attribution is displayed where possible.
- Openverse API: https://api.openverse.org/v1/ — only CC0 / Public Domain Mark metadata is considered, with image download restricted to `upload.wikimedia.org`; no arbitrary URLs from Openverse results are fetched.
- Freepik stock API: only resources identified as Freemium/free photos using its official download endpoints are considered. Account licensing/credits still apply; the source URL and supplied licence metadata are shown to reviewers. AI generation is disabled in this tab.

The app makes an automated relevance check using photo metadata and chapter keywords, but **a human must verify photographic and licensing suitability** before publication. API keys must never be placed in frontend code or Word documents.

## Deployment and verification

1. Copy updated `backend/app` modules and frontend changes, preserving your own environment variables and licensed `backend/fonts` assets.
2. Redeploy Railway backend and Vercel frontend, and confirm build `2026.11.03-v24-free-first-review` from the job details.
3. Test a simple Word document with two eligible headings: first click Find images and preview, then unselect one, confirm and download. Check that exactly one picture is in the saved DOCX and the heading texts still match.
4. Repeat the same course/title/heading with library enabled to verify image library hits. Verify S3 persistence after a restart. Check your Freepik provider dashboard for actual billed credits: the app cannot independently know your account's billing model.

The test suite uses mock API responses and offline Word documents. It does not verify third-party API credentials, user account charges, real stock-image relevance or your deployed Railway/Vercel environment.
