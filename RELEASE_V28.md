# SLC Document Tools v28 — course-and-heading-aware image matching

Backend `/health` build: `2026.11.03-v28-course-heading-context-relevance`

## Problem addressed

With a functioning `PIXABAY_API_KEY`, course photos were sometimes irrelevant or missing. The v27 Pixabay selector required tags to share literal words with **both** its search phrase and the long academic heading. For example, an appropriate photo tagged `business, office, meeting` could be rejected for a section headed **Negotiation: reaching agreement through structured discussion**. When all photos were rejected, the user received zero previews; on other searches, very broad office photos could be suggested without enough contextual explanation.

## What changed

1. **Course context first.** The image planner inspects chapter headings and teaching text from the full document, includes local paragraphs under each eligible heading, and uses the exact Course / Module Title provided in the form to identify the discipline. New professional setting rules include safeguarding children and adults, education/early years, healthcare, HR/payroll, counselling, computing/IT, waste management, food, law, finance, safety and workplace management. A generic filename can still be supplemented by the chapter content. These are constrained text heuristics, not a complete substitute for human educational judgement.
2. **Heading-specific visual scenes.** Academic labels are interpreted as photographic activities, equipment and environments. The screenshot example of conflict-management approaches retains separate scenes for avoidance, directing, collaboration, negotiation and decision-making. A passing reference to disagreement in a different course no longer forces corporate conflict photos.
3. **Pixabay relevance fixed.** Its tag ranking now maps common photographic synonyms (e.g. `negotiation` / `meeting`, `collaboration` / `team`) and no longer requires academic heading words verbatim. Irrelevant generic one-word matches are rejected. If nothing is suitable, at most one broader stock-tag query is tried. **Actual images remain recommendations, not proof of relevance.** Existing Pixabay search-response caching, trusted image CDNs, orientation checks, limits and licence details remain in place.
4. **Review instead of automatic approval.** Every new preview starts with **zero images selected**. Each candidate displays its original Word heading, intended educational scene, stock query and source. Check only images that actually match the lesson; use **Find free alternative** for the rest. Confirmation does not repeat provider searches and all original Word text, headings and styles are preserved.
5. **Manual links when stock fails.** The no-results section now includes a Pixabay search link beside existing Pexels and StockSnap links for each missing image placement.
6. **Optional text-only understanding.** If you deliberately enable **Improve search queries with OpenRouter** and configure `OPENROUTER_API_KEY`, the text model receives the course title, chapter title, a bounded cross-section of the chapter and supporting paragraphs for the targeted headings. It designs more specific real-photo search words. This API may have a small usage charge; it does not generate any images. OFF by default.
7. **Cost and security unchanged.** `image_source=free` and `allow_freepik=false` make zero Freepik calls. The v26/v27 limits still apply. Pixabay API key is needed only in **Railway backend Variables**, not Vercel. Preview images are only cached for reuse when explicitly selected and successfully embedded.

## Railway configuration

Use the existing Pixabay API key; **no additional API key is required for the core fix**.

```dotenv
PIXABAY_API_KEY=<your existing secret Pixabay key>
OPENVERSE_ENABLED=true
COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=0
COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=0
```

Optional for low-cost text-only semantic search:

```dotenv
OPENROUTER_API_KEY=<optional secret>
OPENROUTER_MODEL=openai/gpt-4o-mini
```

For large batches, you may increase the existing `COURSE_IMAGE_MAX_PREVIEW_IMAGES`, `COURSE_IMAGE_MAX_STOCK_LOOKUPS` and `COURSE_IMAGE_MAX_SEARCH_SECONDS` within the backend safety bounds; increasing them can increase free API requests and processing time. The default **16 previews / 32 heading lookups / 75 seconds** may mean that fewer than half the headings in a very large batch receive images.

## Recommended workflow

1. Upload your DOCX or ZIP in **Find & Add Images**.
2. Enter the **exact Course / Module Title**. It is strongly recommended: if the title is omitted, the program must infer the subject from chapter filenames and text.
3. Choose **Add images to my Word document**, **Pixabay only** (or **Free sources only**), **Reuse images from saved library**, and **Preview images before adding**.
4. Keep **Allow paid Freepik stock fallback OFF**.
5. Click **Find images and preview**, compare each photograph with the **Intended photograph** description and exact heading, select the correct ones, and replace unsuitable candidates using fresh free searches.
6. Click **Insert N selected images and download**. The downloaded Word file retains its original instructional wording and headings. Original page breaks may shift to accommodate the photos.

Images are targeted to approximately half of eligible chapter headings. Learning outcomes, references, contents, summary, conclusion and existing illustrated headings are excluded. Image retrieval may return fewer recommendations due to free provider coverage or preview budgets. No arbitrary or misleading filler image is inserted to meet a numeric quota.

**Important difference from the attached master prompt:** that prompt also asks for separate chapter search-guide DOCX files and specifically designed portrait covers. **Find & Add Images** is the automatic photograph insertion workflow; use its separate **Search guides and covers** option for a guide/cover output. AI image generation remains disabled here for credit control.

## Verification

- **174 backend tests passed, 1 skipped** (includes 9 v28 tests and all v27/v26 regressions); tests run with mocked HTTP photo endpoints, not your live Pixabay account.
- TypeScript/TSX syntax parsed with **0 errors** using TypeScript's transpile parser. Full Next.js build and live Railway/Vercel deployment were not performed in this environment.
- A mock end-to-end review confirms a Pixabay stock photo with tags `business, office, meeting` is now proposed for a conflict-negotiation heading, whereas the old keyword gate would have rejected it.
- A mock Word round trip confirms original nonempty paragraph text and styles are retained. Provider licences and individual image relevance still require review prior to course publication.

## Deploying

Back up your deployed repo and settings. Upload the **complete v28 ZIP** into your project, or apply the **v28 patch ZIP** to an existing **v27** checkout. Commit and redeploy **Railway backend and Vercel frontend**. Keep private `.env` secrets and licensed `backend/fonts/` binaries from your own deployment (none are included in the distribution).

Verify Railway `/health` reports `2026.11.03-v28-course-heading-context-relevance`. You do **not** need a new Pixabay API key. Your actual website and API are not changed until you deploy the update.
