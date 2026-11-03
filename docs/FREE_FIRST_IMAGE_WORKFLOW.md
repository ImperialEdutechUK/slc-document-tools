# Find & Add Images — v26 cost-safe, review-before-insert workflow

This workflow is separate from Document Formatter's URL/hyperlink image insertion. It leaves existing Word text, headings, paragraph styles and tables in place, inserting only the selected photographs below eligible headings.

## Safe defaults

- **Image source:** Mixed (saved photos → Pexels → Pixabay → Openverse). Pexels and Pixabay need their own free API keys; Openverse supports anonymous search and can be enabled without a key.
- **Paid Freepik access:** OFF by default, even in Mixed. Freepik is consulted only when the user explicitly checks **Allow paid Freepik stock fallback** (or chooses Freepik stock only, which explicitly opts in).
- **Server-side paid caps:** `COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=1` (failed attempts count), `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=1` (successful stock selections). Set both to `0` for an additional server-side guarantee that Find & Add Images never requests Freepik stock.
- **AI image generation:** unavailable in Find & Add Images. No Freepik Flux or OpenAI image-generation endpoint is called by this tab.
- **Reuse saved image library:** ON. Free photographs are cached by heading/search **after they are selected and inserted**, not merely previewed, in S3-compatible storage or temporarily in `STORAGE_DIR`.
- **Big ZIP fairness and timeouts:** Preview positions are spread across chapters before the maximum is applied. By default at most 16 photos, 32 stock-search attempts and approximately 75 seconds of stock lookups are allocated per review. Increase `COURSE_IMAGE_MAX_PREVIEW_IMAGES`, `COURSE_IMAGE_MAX_STOCK_LOOKUPS` or `COURSE_IMAGE_MAX_SEARCH_SECONDS` in Railway for bigger batches if acceptable; reports name any skipped headings.
- **Preview images first:** ON. Untick unsuitable images or change search words with **Find free alternative** (never uses Freepik; at most 8 successful swaps per review).
- **OpenRouter text improvement:** OFF by default, because it is optional and may have a separate text API cost.

## User workflow

1. Upload one DOCX or a ZIP of Word chapters, optionally specifying the course/module name.
2. Leave Mixed and **Allow paid Freepik stock fallback** unchecked for the lowest-risk option. Keep reuse and preview turned on.
3. Click **Find images and preview**. Inspect every heading, picture, source, photographer and licence. Search words are intentionally short and describe a plausible photograph rather than repeating the full course title.
4. If the photo is unrelated, type a better visual search phrase and click **Find free alternative**. The same review and selected state are preserved. If still unsuitable, untick it.
5. Click **Insert selected images and download**. Confirmation uses stored image bytes; it never calls image providers again. One DOCX uploads to one DOCX; ZIP to ZIP.
6. Verify the resulting Word layout and the publication rights for every retained photograph. Images affect pagination even though the underlying text and styles are not rewritten.

## Configuring Railway/Vercel

```dotenv
# Railway backend variables (do not place keys in the frontend)
PEXELS_API_KEY=
PIXABAY_API_KEY=
OPENVERSE_ENABLED=true
FREEPIK_API_KEY=  # Optional paid fallback; may be omitted
COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=1
COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=1
COURSE_IMAGE_MAX_PREVIEW_IMAGES=16
COURSE_IMAGE_MAX_STOCK_LOOKUPS=32
COURSE_IMAGE_MAX_SEARCH_SECONDS=75
# Configure S3_BUCKET, S3_ENDPOINT, S3_ACCESS_KEY_ID,
# S3_SECRET_ACCESS_KEY and S3_REGION for persistent reuse.
```

For Vercel, set `NEXT_PUBLIC_API_URL` to the public Railway backend URL (without a trailing slash). In Railway, add the Vercel origin to `ALLOWED_ORIGINS`. Redeploy both services. `GET /health` should show `2026.11.03-v26-free-first-image-repair`.

The Pexels API is free subject to its published rate limits and attribution/link guidance. Pixabay search responses are cached for 24 hours. Openverse matches are restricted to CC0/PDM metadata and actual photos hosted on approved Wikimedia CDN hosts. Check the source licence and context before publishing. Freepik catalogue/download calls may still count against the account; the app cannot measure actual charged credits. A Freepik "freemium" label is not a guarantee of unrestricted rights.

## API summary

- `POST /api/v1/course-images/review` — multipart: `document`, `course_name`, `image_source=mixed`, `reuse_existing=true`, `allow_freepik=false`, `use_openrouter=false`; returns a private `review_ready` job with image thumbnail URLs.
- `GET /api/v1/course-images/reviews/{job_id}/images/{image_id}` — thumbnail JPEG, includes browser-cache revision after replacement.
- `POST /api/v1/course-images/reviews/{job_id}/replace` — JSON `{ "image_id": 0, "search": "nurse medication care" }`; finds one free alternative, does not modify the original DOCX, reuses the review ID.
- `POST /api/v1/course-images/reviews/{job_id}/confirm` — JSON `{ "selected_ids": [0, 2] }`; inserts existing selected images and returns the final DOCX/ZIP download job.
- `POST /api/v1/course-images/prepare` — prior direct insertion/guide endpoint remains supported, with new optional `allow_freepik=false` safety field.

If no matching image is found, the app leaves that heading unchanged and reports the skipped search. It does **not** manufacture filler artwork or silently insert an unrelated image. Live provider credentials, the user's problematic input DOCX and deployment billing were not available in the offline test environment.
