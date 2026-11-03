# Find & Add Images — new document workflow

The existing Document Formatter, Word → PDF, PDF Editor and Replace PDF Cover workflows are unchanged. Use the new **Find & Add Images** tab for chapters that lack photographs. This option implements the rules from `COURSE_IMAGE_MASTER_PROMPT.txt`.

## Input

- Upload one `.docx` or a `.zip` containing chapter `.docx` files.
- Supply the course name for more relevant photographic subject matching (optional, inferred from filename if absent).
- You may optionally upload an existing cover photo. A single `cover` image found inside the ZIP is also recognised.
- Assignment briefs and written assignment templates are skipped in course image preparation.

## Mode 1: Automatically add images to Word (default in the website)

- Select **Add images to my Word document** in the Find & Add Images tab (selected by default).
- All image sources are permanently visible as four high-contrast cards: **Pexels, then Freepik**, **Freepik only**, **Pexels only**, or **Freepik AI generation**.
- Uploading a **single `.docx`** returns that document with images inserted as a directly downloadable **`.docx`**, named `<original name>_with_images.docx`.
- Uploading a **`.zip` of chapters** returns a ZIP containing **only the enhanced DOCX files**, at ZIP root. There are no extra guide folders, cover pictures or other assets to sift through.
- An insertion job does **not** create image guides or generate covers, avoiding unused image-provider calls.
- The result screen offers one clear download button; processing notices are collapsed by default.

## Mode 2: Image search guides & covers (optional)

- Creates **one Word image-search guide per eligible chapter**.
- Selects approximately 50% of eligible Heading 1–6 paragraphs that do not already have an image below them.
- Excludes Summary, Conclusion, References, Bibliography, Table of Contents and Resources for Further Reference.
- Each guide includes exact heading text, a suggested photographic scene, placement instructions and clickable **Pexels** and **StockSnap** search links. If Freepik is configured and included in the image-source choice, a **Freepik** search link is also provided with a licence reminder.
- Optionally enable **OpenRouter to improve image search ideas**. OpenRouter analyses the title, exact heading and short text excerpt to refine the scene description and stock search terms. The local rule-based description is retained if OpenRouter is unavailable or returns a mismatched suggestion.
- Creates dark charcoal-panel cover PNGs (portrait, 73% photograph, 27% blank title space, gold divider) if a suitable real photograph or generated one is available. Existing covers with a solid coloured lower panel are corrected without redrawing their upper photograph.
- Does **not** change or include the original Word chapter files.
- The result ZIP has only `Matching Image Search Guides/` and `Cover Images/`.

### How automatic image insertion works

- Uses the same heading analysis as the guide mode but does not generate guides.
- Choose **Find stock: Pexels, then Freepik** (automatic), **Freepik only**, **Pexels only** or **Generate new images** in the UI.
- Pexels search uses its official API. Freepik search uses the official Freepik/Magnific stock-resource API and only considers photo resources with free/freemium metadata and no premium product flag. Only official licenced download URLs are used, never preview thumbnails. Freepik *freemium* does not mean unrestricted use: confirm licence and attribution requirements.
- Search results must match specific meaningful heading terms; no unrelated filler photos are inserted. StockSnap remains search-link-only.
- Prevents repeat use of the same Pexels or Freepik photograph across chapters in one job.
- **Generate missing** (off by default) uses Freepik's Flux 2 Pro image model with optional direct OpenAI image fallback. **Generate new images** skips stock search and generates all requested placements. All generation is capped at `COURSE_IMAGE_MAX_GENERATIONS` attempts per job (default 8), to help control costs.
- Inserts selected landscape photographs directly **below the intended heading**, without changing existing text, styles or tables. Page flow can change because pictures occupy space.
- The output contains only the finished Word document(s); single DOCX uploads are never wrapped in a ZIP.
- Missing images are explicitly reported; unrelated images are not substituted.

## Deploying / using image providers

Set keys as **backend** environment variables in Railway (or `backend/.env` locally). These values are not exposed to the Next.js browser and must not be committed to Git:

```dotenv
# You already have these keys; add their actual values privately in Railway.
OPENROUTER_API_KEY=<your OpenRouter key>
OPENROUTER_MODEL=openai/gpt-4o-mini
FREEPIK_API_KEY=<your existing Freepik API key>
FREEPIK_IMAGE_MODEL=flux-2-pro
COURSE_IMAGE_MAX_GENERATIONS=8

# Optional stock-photo fallback, if you have a Pexels key:
PEXELS_API_KEY=

# Optional legacy generation fallback requiring its own separate API key:
OPENAI_API_KEY=
```

- If your key was issued under the **Magnific** API, use `MAGNIFIC_API_KEY=` instead of `FREEPIK_API_KEY=`. The code selects the matching official API domain/header. If your account requires the other endpoint, set `FREEPIK_API_BASE=https://api.magnific.com/v1` and `FREEPIK_API_HEADER=x-magnific-api-key`, or the corresponding `api.freepik.com` / `x-freepik-api-key` values.
- **OpenRouter is used for text-only scene understanding and better image search descriptions**, not direct image generation. Select its optional checkbox in the interface. This sends chapter headings and brief context excerpts to the selected model provider through OpenRouter. The original uploaded Word file is never sent in full.
- **Freepik stock search** gets a photo only through the stock catalogue and official licence-backed download endpoint, never by hotlinking a preview. Depending on your account and asset licence, downloads may incur fees or require attribution. Review every image and licence before distribution.
- **Freepik AI generation** uses the asynchronous Flux 2 Pro API, polling its task status until completed. The generated photo is used only when the user explicitly selects generation or enables *Generate missing*. It may consume credits. The frontend initially leaves *Generate missing* unchecked.
- Without any API keys, image search **guides** still work. Automatic Word photo insertion requires one stock provider or a configured generation provider. If the key is invalid or permission is lacking, the report includes a warning; it never invents a photo.
- With large chapter batches, generation can take time and is capped per job. Manage credit spending in the Freepik/Magnific and OpenRouter dashboards.
- Do not enable external image processing on confidential documents without appropriate approval. No API keys are accepted in document uploads or frontend form fields.

### Request fields

`POST /api/v1/course-images/prepare` as `multipart/form-data`:

| Field | Type | Required |
|---|---|---|
| `document` | `.docx` or `.zip` | Yes |
| `course_name` | text | No |
| `mode` | `guides` / `insert` | No; default `guides` |
| `image_source` | `auto`, `pexels`, `freepik`, `generate` | No; default `auto` |
| `use_openrouter` | boolean | No; default `false` |
| `generate_missing` | boolean | No; default `false`; forced on when source is `generate` |
| `cover_image` | PNG / JPEG / WebP | No |

Returns a persisted job download URL with the correct `.docx` or `.zip` output type, image counts, image sources, optional OpenRouter refinements, generation attempts and warnings. These results are not a publishing/licensing clearance report.

## URL and usage safety

Pexels photographs are requested from its official API and downloads are restricted to `https://images.pexels.com`. Freepik/Magnific photograph searches and licences are requested from the official resource API; signed image downloads are restricted to approved Freepik/Magnific CDN domains and are size-limited. StockSnap is not automatically downloaded. Stock photos and generated images should be checked for fitness, AI accuracy and licensing before educational publication. Search links do not guarantee availability or permissions. An OpenRouter key cannot replace an OpenAI image generation key.

## Compatibility note

The API retains its previous default `mode=guides` for clients that omit the field; the website explicitly sends `mode=insert` by default. Existing clients requesting guides still get the guide-and-cover ZIP. Only insertion output packaging has changed.
