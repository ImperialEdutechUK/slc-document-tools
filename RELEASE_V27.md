# SLC Document Tools v27 — contextual photo search and zero-results diagnostics

Build: `2026.11.03-v27-contextual-search-diagnostics`

## What was wrong with v26

A course headed "Choosing the Right Conflict Management Approach" could produce long or abstract search keywords (`avoidance ... business office meeting`). Pexels search results were also rejected unless their `alt` captions repeated query words, even though the Pexels API does not provide robust detailed title/tag metadata. Openverse accepted only public-domain Wikimedia-hosted files; public-domain StockSnap photographs returned through Openverse were rejected based on CDN host. Finally, when no image was found the results screen didn't show which free APIs had actually been configured.

## Fixes

1. **Search visual scenes, not academic headings.** Conflict-management headings are converted into distinct observable workplace subjects. Generic searches prefer concrete setting/action words. Original headings and teaching text are untouched. Generic "Learning outcomes", "Learning objectives", and "Assessment criteria" receive no decorative photos but do **not** exclude following subsections.
2. **Accept captionless Pexels results into preview.** Rank available captions when present, then preserve Pexels' own search relevance ordering. `alt` is not a mandatory filter. Users still review every photograph before insertion, and images are not cached until approved. No guarantee that a search-engine result is relevant.
3. **Expand keyless public-domain sources.** Openverse still accepts only CC0/PDM metadata, but now allows both `upload.wikimedia.org` and `cdn.stocksnap.io` photo URLs. Downloads remain HTTPS-only, from strict allowlisted hosts, with size/content-type checks.
4. **Explain zero results.** The review screen displays whether Pexels/Pixabay/Openverse are configured, how often each provider was tried, and heading-specific Pexels/StockSnap search links for the positions that were attempted but did not obtain a photograph. A missing key is no longer indistinguishable from an unsuccessful search.
5. **Improve optional low-cost text refinement.** OpenRouter suggestions can use visual synonyms when they overlap with the heading, section context or local search concept; unrelated results are still rejected.
6. **Preserve cost protection.** Freepik paid stock fallback remains OFF by default, capped by `COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS` and `COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS` when explicitly enabled. No AI image generation occurs in this tab.

## Configuration and deployment

1. Back up the installed source and Railway/Vercel settings. Preserve your private `.env` values and licensed `backend/fonts/` files. The complete distribution does not include font binaries.
2. Merge the full v27 ZIP into GitHub **or** overlay the small v27 patch ZIP on an existing **v26** checkout, then commit/push.
3. In **Railway backend Variables**:

```dotenv
PEXELS_API_KEY=your_free_key              # recommended for professional stock photos
PIXABAY_API_KEY=                        # optional second free source
OPENVERSE_ENABLED=true                  # keyless but limited catalogue
COURSE_IMAGE_MAX_FREEPIK_ATTEMPTS=0     # 0 guarantees no paid Freepik lookup
COURSE_IMAGE_MAX_FREEPIK_DOWNLOADS=0    # 0 guarantees no paid Freepik stock selection
```

Do not place provider API keys into Vercel `NEXT_PUBLIC_*` variables. Pexels requires a free API account/key. Openverse works without a key, but the app cannot guarantee that public-domain results exist for every abstract professional concept. If you do not have a Pexels account, you can use Openverse/StockSnap via Openverse, or follow the manual Pexels/StockSnap search links shown when automation finds none.

4. Redeploy the Railway backend and Vercel frontend. Confirm `GET /health` returns `2026.11.03-v27-contextual-search-diagnostics`. In Vercel retain `NEXT_PUBLIC_API_URL` and in Railway retain `ALLOWED_ORIGINS` correctly.
5. In **Find & Add Images**, use **Add images to my Word document**, **Free sources only** or **Mixed** with **Allow paid Freepik stock fallback unchecked**, **Preview images before adding checked**, and a descriptive course title. Check provider diagnostics; select only photos that match the teaching text. Original words/headings are not rewritten.
6. If the review reports `Pexels not configured`, no amount of retrying in Pexels-only mode will work until a valid key is installed. Check the Railway deployment logs for provider HTTP failures. The interface reports API exception types but deliberately does not expose secrets or remote response bodies.

## Verification

- Python backend suite: **165 passed, 1 skipped**. New tests cover conflict-management searches, skipping non-photo headings, accepting blank Pexels `alt` fields, CC0 StockSnap images via Openverse, optional text-refinement grounding, and understandable zero-result diagnostics.
- TypeScript/TSX file parsed with **0 syntax diagnostics** using the installed TypeScript compiler. A full Next.js runtime build was not run; production environment dependencies were not installed in this test container.
- No live Pexels, Pixabay, Openverse or Freepik API tokens/queries were used during tests, and the exact failing DOCX was not supplied. Verify returned photos and publication licences before relying on a deployed change.

## Why this is not identical to ChatGPT/Claude image discovery

The user's master prompt is a research-and-visual-guidance brief. ChatGPT and Claude may use contextual reasoning or broader browsing, whereas this tab uses a small set of permitted provider APIs and strict download rules. v27 translates some abstract section headings into photographic subjects but cannot guarantee image relevance or replicate image-generation capabilities. Use the separate **Search guides and covers** mode to create the guide/cover workflow in the master prompt.
