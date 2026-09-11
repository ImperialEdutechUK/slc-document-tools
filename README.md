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

### ZIP batch formatting

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
- Select paragraphs and switch between bullets, numbering and normal text.
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

The response includes `build: 2026.09.11-v7-expanded-document-editor`. The same build value is shown beside completed frontend jobs, making it easy to confirm that Railway is serving the new deployment rather than an older cached backend.

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

Direct public image URLs work independently of the stock API.

## API endpoints

```text
POST /api/v1/formatter/format
POST /api/v1/formatter/batch
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
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/download
GET  /api/v1/jobs/{job_id}/report
```

## Tests

```bash
cd backend
PYTHONPATH=. python -m unittest discover -s tests -v
```

The backend currently has **58 passing tests, 1 skipped environment-dependent conversion test**, covering the original formatter helpers, page-aware cover positioning, safe ZIP handling, batch formatting, linked-image detection/manual overrides, PDF editing, and Word-to-PDF conversion.

## Next implementation step

The next useful production check is to run the formatter against a representative real course ZIP containing the content team's normal filenames and linked-image patterns. That will validate naming conventions and external image retrieval under Railway production credentials before release.

## Legacy Magnific / Freepik resource links

The linked-image downloader first tries the numeric resource ID contained in the document URL. If that historical ID returns 404, it searches the official Magnific stock catalogue using the page slug/title and retries the download only when a high-confidence matching resource is found. This avoids silently inserting an unrelated stock image when an old resource ID has been migrated or retired.
