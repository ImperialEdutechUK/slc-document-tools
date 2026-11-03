## Latest fix — Find & Add Images v28 (matching course + headings)

**Current release: v28.** A successful Pixabay API key alone cannot ensure relevant pictures. v28 examines the course title, local paragraphs and chapter context, translates headings into realistic stock-photo scenes, matches Pixabay tags by photographic meaning instead of exact academic words, and adds a broader-query fallback. **Nothing is preselected** in the image preview: approve only suitable photos. No Freepik image credits are used unless explicitly enabled. See [RELEASE_V28.md](RELEASE_V28.md) for the fix, Railway settings, deployment and testing.

## Earlier fix — Find & Add Images v27

For contextual search, Pexels captionless results, keyless Openverse/StockSnap public-domain sources, and zero-results diagnostics, see [RELEASE_V27.md](RELEASE_V27.md). Railway `/health` build: `2026.11.03-v27-contextual-search-diagnostics`. For consistent automatic stock-photo results, configure the free `PEXELS_API_KEY`; otherwise use Openverse or manual search links. Freepik remains opt-in, with `COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=0` to prohibit paid fallback completely.

> **Earlier release v26:** See [`RELEASE_V26.md`](RELEASE_V26.md) for the repaired **Find & Add Images** tab, strict Freepik cost controls, free-image replacements and deployment instructions. The separate v25 any-name linked-image formatting fixes remain included; see [`RELEASE_V25.md`](RELEASE_V25.md).

## Automatic linked images regardless of hyperlink display text

In the **Document Formatter** tab, enable **Automatically retrieve linked images**.
The formatter tests the actual external Word hyperlink destination, not the
visible label. A real image can be linked as **Image 01**, **Click here**,
**Workplace photo**, or another name. Direct JPG/PNG/WebP responses are
verified before embedding; known photo-page links can also supply an advertised
image. Genuine `Image 01` marker lines are replaced by the photo, while links
within ordinary lesson text stay intact and receive a picture beneath them.

Normal webpages that are not image sources are kept unchanged (the UI reports
**NOT AN IMAGE**). References, Summary and Contents sections are excluded.
Authenticated, blocked, expired, internal-only and non-image URLs cannot be
turned into images merely because the display text is underlined/blue.
See `RELEASE_V25.md` and `UNNUMBERED_IMAGE_LINKS_FIX.md`.

# SLC Document Tools — Vercel + Railway migration

This repository contains the production migration of the existing Streamlit **SLC Word Formatter** into a split web architecture:

- **Frontend:** Next.js / React, designed for Vercel.
- **Backend:** FastAPI / Python, designed for Railway in a Docker container.
- **Database:** PostgreSQL on Railway for processing-job records.
- **File storage:** local storage in development; S3-compatible/Railway bucket in production.
- **DOCX → PDF:** LibreOffice runs in the Railway backend container.

## What is implemented in this first build

### Document Formatter

The original formatting engine has been extracted from Streamlit and reused by FastAPI. Existing behaviour remains intact:

- SLC cover/template application.
- Heading correction and page-start rules.
- Clean paragraph pagination: paragraphs stay together where possible and headings stay with following content.
- Reference/resource headings such as `Resources for Further Reference:` are converted to bullet lists automatically.
- Automatic TOC generation.
- Footer/page numbering.
- Existing numbered-image insertion and validation.
- Manual JPG/PNG/WebP/ZIP image uploads.
- DOCX or ZIP input from the same formatter screen.
- Word-compatible full-page cover layout: stale Letter-size drawing metadata is removed, the background is page-relative with a small bleed, and both modern/legacy textbox representations are aligned to the actual A4 section size.

### Find & Add Images — v28 context-aware, credit-safe workflow

**Defaults:** `image_source=mixed`, `reuse_existing=true`, `preview_images=true`, `allow_freepik=false`, `use_openrouter=false`. Mixed mode works with only free sources and the stored image library until you explicitly enable Freepik stock; paid **AI image generation is disabled** throughout this workflow.

1. Upload a single DOCX or a ZIP of chapter DOCX files.
2. Click **Find images and preview**. The backend checks its saved image library and then tries **Pexels → Pixabay → Openverse** (CC0/public-domain Wikimedia-hosted photos). Without the separate opt-in, **zero Freepik calls** are made. If explicitly enabled, Freepik stock is limited to **one search attempt and one successful download** by default, even when the attempt fails.
3. See image thumbnails, intended photographic scene, exact chapter/heading placement, origin, licence and photographer. Every photo starts **unchecked**: actively approve only genuinely relevant pictures, or use **Find free alternative** to retry one photograph with better search words (free providers only, up to eight successful replacements per review). The app searches chapter plans in a balanced order, so large ZIPs do not use their entire preview allowance on the first chapter.
4. Click **Insert selected images and download**. This step uses already acquired bytes, so it makes **no further stock-provider calls**. The DOCX retains all original paragraph text, styles and headings; adding pictures may change page flow. Existing illustrations and Summary/References/Contents headings are skipped.
5. A single DOCX returns a single DOCX. A ZIP returns a ZIP containing edited chapter DOCX files.

**Saved library:** free-stock JPEGs are cached in the app's existing S3-compatible bucket (`S3_*`) or local `STORAGE_DIR`, keyed to the search and heading. Repeated matching jobs can reuse them instead of making more API requests. For Railway redeploy persistence, configure an S3 bucket; `/tmp` is ephemeral. Freepik licensed stock is **not** cached by default because reuse rights vary by licence. Pixabay search responses are cached for 24 hours as required by Pixabay API terms. No original Word content is stored in the image library (originals for a preview stay in a separate private review bundle).

**Setup:** add `PEXELS_API_KEY` and `PIXABAY_API_KEY` in Railway backend Variables, optionally `FREEPIK_API_KEY` or `MAGNIFIC_API_KEY`; `OPENVERSE_ENABLED=true` requires no key. Use `COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=1`, `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=1`, `COURSE_IMAGE_MAX_PREVIEW_IMAGES=16`, `COURSE_IMAGE_MAX_STOCK_LOOKUPS=32`, and `COURSE_IMAGE_MAX_SEARCH_SECONDS=75` initially. Set `NEXT_PUBLIC_API_URL` in Vercel and add the Vercel origin to backend `ALLOWED_ORIGINS`. Keys stay server-side. Rebuild/redeploy **both Railway and Vercel** for v26.

**Other modes:** `free` (never touches Freepik), `pexels`, `pixabay`, `openverse`, and `freepik`. Optional OpenRouter text-only scene refinement is off by default. The guides-and-covers workflow still works without modifying the original documents. Some images or source sites may be unavailable and are skipped with warnings. Users should verify actual image relevance, stock rights and attribution before publishing.

For API details and provider limitations, see `docs/FREE_FIRST_IMAGE_WORKFLOW.md`.

## ZIP batch formatting

The formatter can now process a ZIP of course documents in one job:

- Safe in-memory ZIP extraction rejects absolute paths, traversal entries, Windows drive paths, and `__MACOSX` metadata.
- `Assignment Brief` and `Written Assignment Template` DOCX files are skipped from SLC formatting and copied unchanged into `skipped/`.
- Eligible DOCX files reuse the existing formatter and linked-image workflow.
- One document failure does not stop the remaining batch.
- The result ZIP contains only `formatted/` and `skipped/`. No report folder or batch report file is added.
- Batch cover chapter/unit titles are derived from each DOCX filename while Awarding Body and Course Name remain shared inputs.

New linked-image automation is included:

1. Scan the DOCX XML and hyperlink relationships.
2. Associate a URL with `Image N` when the link is:
   - on the same paragraph;
   - attached to the `Image N` text as a Word hyperlink; or
   - on the immediately following paragraph.
3. Download direct public image URLs safely.
4. Route Freepik links through a server-side API key when configured.
5. Give manual uploads priority for the same image number.
6. Add linked-image successes/failures to the validation report without stopping the rest of the document.

### Preview, simple editing and PDF export

After formatting a single DOCX, the user can stay in the formatter screen to:

- Preview the formatted document.
- Open a lightweight document editor without leaving the formatter workflow.
- Edit paragraph wording directly; pressing Enter in the text editor creates a manual next line in the DOCX.
- Select paragraphs and apply Heading 1, bullets, numbering or normal text.
- Add blank lines above/below selected paragraphs.
- Force selected paragraphs to the next page or return them to normal page flow.
- Keep selected paragraphs together on one page where possible, or allow normal page splitting.
- Edit the generated footer course text, copyright text and page label.
- Refresh/rebuild the generated Table of Contents after heading or layout edits; LibreOffice UNO recalculates the live TOC page numbers before the refreshed preview.
- Convert the current edited DOCX directly to PDF.
- Automatically remove an unwanted blank second PDF page when detected.

### Word → PDF

A separate workflow converts files without applying SLC formatting. The formatter writes `Garamond` explicitly throughout the document. PDF conversion is now **Garamond-only**: EB Garamond aliases and substitutions have been removed. Before conversion, the server verifies that fontconfig resolves the family exactly as `Garamond`; if exact Garamond is unavailable, conversion stops with a clear error rather than changing the font. Proprietary font binaries are not bundled in this repository; provide a properly licensed Garamond family through the private Railway deployment under `backend/fonts/`.


- Single DOCX → PDF.
- Multiple DOCX files → ZIP containing PDFs.
- ZIP containing DOCX files → ZIP containing PDFs.

This was tested against the supplied `Unit 11.zip`: both the Assignment Brief and Written Assignment Template converted successfully as separate PDFs.

### PDF Page Editor

The existing PDF page-removal feature is exposed through FastAPI and the new frontend.

### Replace PDF Cover

A dedicated cover-only workflow can now create a revised PDF without changing the source file:

- Replace page 1 with a PDF, JPG, PNG, or WebP cover image.
- Add exactly three centred cover text rows: Awarding Body Name, Course Name and Unit Name.
- Use one shared medium text size for all three rows. The shared size starts at 24 pt and all three reduce together only when required to fit the longest line.
- Use bundled Garamond when available, with white text over a subtle translucent panel for readability.
- Keep every page after the cover unchanged.

## Repository structure

```text
slc-document-tools/
├── frontend/                 # Next.js app for Vercel
│   └── app/
├── backend/                  # FastAPI app for Railway
│   ├── app/
│   │   ├── formatter/        # Existing formatter logic, refactored from Streamlit
│   │   └── services/
│   ├── tests/
│   ├── Dockerfile
│   └── railway.json
```

## Local backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Open `http://localhost:8080/docs` for the FastAPI API explorer.

The backend uses SQLite automatically when `DATABASE_URL` is not supplied.

## Local frontend

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Then open `http://localhost:3000`.

## Railway deployment

Create a Railway project containing:

1. A **PostgreSQL** service.
2. A **bucket/object-storage** service if persistent document output is required.
3. A backend service whose root directory is `backend/` and which builds from `backend/Dockerfile`.

The Docker image installs LibreOffice Writer, the Python UNO bridge used for exact TOC field/page-number refresh, plus the open-source font packages used for stable Office-font substitution. No proprietary Microsoft font files are bundled in the repository.

Set backend variables using `backend/.env.example` as the guide. In particular:

- `DATABASE_URL` — Railway PostgreSQL connection URL.
- `ALLOWED_ORIGINS` — the deployed Vercel frontend URL.
- `S3_*` — map the bucket's S3-compatible credentials to the variable names expected by the storage service.
- `MAGNIFIC_API_KEY` — server-side Magnific API key used for Magnific/Freepik stock resource links; never expose it in the frontend.

The backend health check is:

```text
GET /health
```

The response includes `build: 2026.11.03-v26-free-first-image-repair`. The same build value is shown beside completed frontend jobs, making it easy to confirm that Railway is serving the new deployment rather than an older cached backend.

## Vercel deployment

Create a Vercel project with `frontend/` as the project root and add:

```text
NEXT_PUBLIC_API_URL=https://YOUR-RAILWAY-BACKEND
```

After Vercel creates the final public domain, add that URL to the Railway backend's `ALLOWED_ORIGINS` value.

## Magnific / Freepik resource configuration

The linked-image service recognises both `magnific.com` and `freepik.com` resource-page URLs, extracts the numeric resource ID, and uses Magnific's stock-content API to request an actual JPG/PNG asset. This avoids treating the HTML resource page itself as an image.

For local development, copy `backend/.env.example` to `backend/.env` and add your private API key:

```text
MAGNIFIC_API_KEY=...
MAGNIFIC_RESOURCE_API_BASE=https://api.magnific.com/v1/resources
MAGNIFIC_API_HEADER=x-magnific-api-key
```

The backend automatically loads `backend/.env` locally. On Railway, set the same variables in the service Variables panel instead. Legacy `FREEPIK_API_KEY` and `FREEPIK_RESOURCE_API_*` names are still accepted so older deployments do not break.

### Optional account fallback

The official stock API remains the preferred Freepik/Magnific download path. If you also have a licensed account, the backend can optionally try an authenticated browser download only after the API path fails. Enable it with Railway service variables (or `backend/.env` locally):

```env
FREEPIK_ACCOUNT_FALLBACK=true

# Preferred multi-account setup
FREEPIK_ACCOUNT_1_EMAIL=first-account@example.com
FREEPIK_ACCOUNT_1_PASSWORD=first-account-password
FREEPIK_ACCOUNT_2_EMAIL=second-account@example.com
FREEPIK_ACCOUNT_2_PASSWORD=second-account-password

# Existing deployments can keep using these legacy single-account names.
# If ACCOUNT_1 is not configured, this legacy account is tried first.
FREEPIK_ACCOUNT_EMAIL=existing-account@example.com
FREEPIK_ACCOUNT_PASSWORD=existing-account-password
```

Do **not** put the real email/password in GitHub, `.env.example`, frontend code, or application logs. The Docker image includes Chromium and the backend uses Playwright only when this fallback is enabled. Website authentication can require verification or UI-selector updates; if Freepik/Magnific changes its login/download controls, the optional `FREEPIK_*_SELECTOR` variables in `backend/.env.example` can be overridden without changing code.

Direct public image URLs work independently of the stock API.

## API endpoints

```text
POST /api/v1/formatter/format
POST /api/v1/formatter/batch
POST /api/v1/course-images/prepare
POST /api/v1/word-to-pdf
GET  /api/v1/jobs/{job_id}/preview
GET  /api/v1/jobs/{job_id}/editable-paragraphs
POST /api/v1/jobs/{job_id}/simple-edit
POST /api/v1/jobs/{job_id}/edit-text
GET  /api/v1/jobs/{job_id}/footer-settings
POST /api/v1/jobs/{job_id}/edit-footer
POST /api/v1/jobs/{job_id}/update-toc
POST /api/v1/jobs/{job_id}/convert-to-pdf
POST /api/v1/pdf/page-count
POST /api/v1/pdf/remove-pages
POST /api/v1/pdf/replace-cover
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/download
GET  /api/v1/jobs/{job_id}/report
```

## Tests

```bash
cd backend
PYTHONPATH=. python -m unittest discover -s tests -v
```

The backend has **159 passing tests, 1 skipped environment-dependent conversion test** in the v26 offline regression suite, covering all original workflows plus paid-fallback request limits, custom headings, approved-only photo caching, fair ZIP chapter distribution, free photo replacements and review/confirmation API calls. Live provider accounts, deployed Railway/Vercel and credit billing still require an in-environment check.

## Next implementation step

The next useful production check is to run the formatter against a representative real course ZIP containing the content team's normal filenames and linked-image patterns. That will validate naming conventions and external image retrieval under Railway production credentials before release.

## Legacy Magnific / Freepik resource links

The linked-image downloader first tries the numeric resource ID contained in the document URL. If that historical ID returns 404, it searches the official Magnific stock catalogue using the page slug/title and retries the download only when a high-confidence matching resource is found. This avoids silently inserting an unrelated stock image when an old resource ID has been migrated or retired.

## Freepik/Magnific authenticated session fallback

If Railway cannot see the Magnific email/password form, use a browser session captured on a trusted local computer instead of automating the login screen.

1. From the `backend` folder, install Playwright if needed: `pip install playwright` and `playwright install chromium`.
2. Run `python scripts/capture_freepik_session.py`.
3. A normal browser opens. Sign in to Magnific/Freepik yourself and complete any verification or 2FA.
4. Return to the terminal and press Enter after you are fully signed in.
5. Copy the printed base64 value into Railway as `FREEPIK_ACCOUNT_1_STORAGE_STATE_B64`.
6. For a second account, repeat the process in a fresh browser run and save it as `FREEPIK_ACCOUNT_2_STORAGE_STATE_B64`.
7. Keep `FREEPIK_ACCOUNT_FALLBACK=true` and redeploy the backend.

Saved-session fallback is attempted before automated credential login. Session values contain authentication material and must be treated like passwords: keep them only in Railway secrets/environment variables, do not commit them to Git, and refresh them if Magnific expires or revokes the session.
