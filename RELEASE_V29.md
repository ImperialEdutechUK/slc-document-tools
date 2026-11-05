# SLC Document Tools v29 — Smart ZIP Batch Image Review

Backend `/health` build: `2026.11.05-v29-zip-batch-image-review`

## New Find & Add Images ZIP workflow

1. Upload a DOCX or ZIP. For a ZIP, **Preview images before adding** is always enabled and the default target is **two planned images per chapter** (adjustable between one and five). Only headings that the existing teaching-context planner regards as suitable are proposed. A chapter with fewer suitable headings gets fewer suggestions.
2. ZIP review now displays **one expandable panel for each original Word path**, including nested course/unit subfolders, planned/search progress, found images and explicit approvals. **Approve chapter photos** and **Clear chapter approval** work independently; there is also a global Select all/Deselect all.
3. Search requests are bounded as before: **16 successful previews / 32 attempted heading searches / 75 seconds per batch**, with at most **80 images for a complete review** by default. When unsearched positions remain, click **Find next batch**. The backend continues from the saved position without repeating earlier searches or discarding downloaded photos, and existing selections remain in the UI.
4. The browser tab remembers the private review-job ID and selected photo IDs using session storage. Refreshing the page restores the review with `GET /api/v1/course-images/reviews/{job_id}`. A review must still exist in backend storage; frontend storage does not contain source Word files or photographs.
5. After approval, **Insert selected images and download** makes **zero additional stock-provider requests**. It adds photos only to approved positions. A chapter-level insertion error leaves that Word file unchanged while other chapters continue.
6. The returned ZIP preserves the input's **original nested folder layout, original Word filenames, and safe untouched assets**, including documents excluded from image planning (such as assignment briefs). It also includes `SLC_Image_Insertion_Report.csv`, listing per-document outcomes, image source/licence information, unmatched headings and processing notices.
7. ZIP traversal entries, symbolic links and duplicate paths are excluded. Existing single-DOCX output remains `<name>_with_images.docx`, and the separate **Search guides and covers** and **Document Formatter** workflows remain independent.

## API additions

- `POST /api/v1/course-images/review` — optional `images_per_chapter=1..5` multipart field for ZIP uploads (default 2).
- `GET /api/v1/course-images/reviews/{job_id}` — recover an existing review's safe public metadata and thumbnail URLs.
- `POST /api/v1/course-images/reviews/{job_id}/continue` — run one bounded continuation pass on an existing private review. No upload needed.
- `POST /api/v1/course-images/reviews/{job_id}/confirm` — still takes `{ "selected_ids": [0, 2] }`, now up to 120 IDs to accommodate larger reviews.

### Optional Railway variables

```dotenv
COURSE_IMAGE_MAX_PREVIEW_IMAGES=16
COURSE_IMAGE_MAX_TOTAL_PREVIEW_IMAGES=80
COURSE_IMAGE_MAX_STOCK_LOOKUPS=32
COURSE_IMAGE_MAX_SEARCH_SECONDS=75
COURSE_IMAGE_MAX_FREE_REPLACEMENTS=24
```

The paid Freepik stock caps remain **cumulative across all batches in one review**. Freepik is still **off by default**, AI image generation is still disabled, and users must judge individual image relevance and publishing rights.

## Testing and deployment

Backend tests use mocked image sources. Run `cd backend && PYTHONPATH=. python -m pytest -q` before deploying. TypeScript TSX syntax was parsed with no syntax errors; a full `npm install`/Next.js production build needs network access and should also be run in your deployment/CI environment.

Deploy **both** the updated `backend` to Railway and `frontend` to Vercel. Check `/health` for the build string above. No database migration is needed; review state is stored in the existing private bundle. Configure durable S3-compatible storage for review restoration after Railway instance restarts. Do not commit API keys, `.env` secrets or proprietary font files.
