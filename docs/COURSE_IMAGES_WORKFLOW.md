# Find & Add Images — new document workflow

The existing Document Formatter, Word → PDF, PDF Editor and Replace PDF Cover workflows are unchanged. Use the new **Find & Add Images** tab for chapters that lack photographs. This option implements the rules from `COURSE_IMAGE_MASTER_PROMPT.txt`.

## Input

- Upload one `.docx` or a `.zip` containing chapter `.docx` files.
- Supply the course name for more relevant photographic subject matching (optional, inferred from filename if absent).
- You may optionally upload an existing cover photo. A single `cover` image found inside the ZIP is also recognised.
- Assignment briefs and written assignment templates are skipped in course image preparation.

## Mode 1: Image search guides & covers (default)

- Creates **one Word image-search guide per eligible chapter**.
- Selects approximately 50% of eligible Heading 1–6 paragraphs that do not already have an image below them.
- Excludes Summary, Conclusion, References, Bibliography, Table of Contents and Resources for Further Reference.
- Each guide includes exact heading text, a suggested photographic scene, placement instructions and clickable **Pexels** and **StockSnap** search links.
- Creates dark charcoal-panel cover PNGs (portrait, 73% photograph, 27% blank title space, gold divider) if a suitable real photograph or generated one is available. Existing covers with a solid coloured lower panel are corrected without redrawing their upper photograph.
- Does **not** change or include the original Word chapter files.
- The result ZIP has only `Matching Image Search Guides/` and `Cover Images/`.

## Mode 2: Automatically add images to Word

- Uses the same heading analysis and guide creation.
- Searches **Pexels** first for relevant free stock photographs; a picture's metadata must match at least one search term. It does not scrape StockSnap, which is offered through search links.
- Prevents repeat use of the same Pexels photograph within one job.
- If *Generate missing* is enabled and an OpenAI image API key is configured, generates realistic landscape images when suitable stock photos cannot be found.
- Inserts selected landscape photographs directly **below the intended heading**, without changing existing text, styles or tables. Page flow can change because pictures occupy space.
- The ZIP additionally includes `Word Documents With Images/`.
- Missing images are explicitly reported; unrelated images are not substituted.

## Deploying / using image providers

Set these as **backend** environment variables in Railway (or backend/.env locally):

```
PEXELS_API_KEY=<your Pexels API key>
OPENAI_API_KEY=<optional OpenAI image-generation API key>
OPENAI_IMAGE_MODEL=gpt-image-2.5-flare
```

- Pexels API signup: https://www.pexels.com/api/
- The OpenAI image API has usage fees and may need organisational access; verify permissions and budget before enabling it.
- For guides, no API key is needed. If neither provider is configured, the service does not pretend it has generated a cover or inserted photographs.
- For automatic insertion, at least one provider must be configured.
- When an external provider is used, course titles and heading search terms are sent to Pexels; with AI generation, the heading description and a short contextual passage are also sent to OpenAI. Do not enable external image processing on confidential documents without appropriate approval.

## URL and usage safety

Pexels photographs are requested only from the official API, and image downloads are restricted to `https://images.pexels.com`. StockSnap is not automatically downloaded. Stock photos should still be manually checked for fitness and licensing before final educational publication. Free stock-photo search links do not guarantee that every result is relevant or available.

## API

`POST /api/v1/course-images/prepare` as `multipart/form-data`:

| Field | Type | Required |
|---|---|---|
| `document` | `.docx` or `.zip` | Yes |
| `course_name` | text | No |
| `mode` | `guides` / `insert` | No; default `guides` |
| `generate_missing` | boolean | No; default `true` |
| `cover_image` | PNG / JPEG / WebP | No |

Returns the standard persisted job response with the ZIP download URL and counts of guides, image placements, inserted photos, prepared covers and warnings.
