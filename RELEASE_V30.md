# v30: Subject-Locked Course Image Relevance (5 November 2026)

This version extends **v29 Smart ZIP Batch Image Review**. The original user-uploaded Word documents are still never rewritten: images are inserted only below approved headings. For ZIPs, the complete folder hierarchy, filenames, unselected DOCX files and other permitted assets are still retained.

## Why this update was necessary

A health or social care chapter with the heading **Team Leadership** could previously receive a business boardroom photograph. Image-provider search ranking is not evidence of subject relevance, especially when descriptions are missing. v30 rejects ambiguous photos rather than inserting unrelated stock imagery.

## Improvements

1. **Course subject lock:** infer the discipline from the specified course title, ZIP filename, chapter names and actual teaching content. A recognised course title takes precedence over management, leadership, teamwork and similar general headings. It distinguishes health and social care, clinical healthcare, early years safeguarding, education, HR, IT and other disciplines.
2. **Stock search protection:** add the missing discipline to generic image queries, even when optional OpenRouter text refinement is enabled.
3. **Strict metadata gate (default):** Pexels, Pixabay, Openverse and Freepik candidates without suitable discipline and scene evidence are rejected before being shown. This also avoids needless image downloads when tags already show a wrong field.
4. **Balanced mode:** allows less detailed caption matches, but **never** treats a generic boardroom image as an adequate healthcare photo.
5. **Optional visual verification:** after an explicit opt-in, a multimodal model checks the actual photograph. It may reject images even when the description looks relevant. Unavailable, malformed or uncertain results fail closed. Verification is not enabled automatically and may incur charges.
6. **Safer library reuse:** Strict metadata-only review does not reuse an old image-library photo without metadata verification. Strict + AI vision can verify it again.
7. **Replacements checked too:** manually requesting an alternative does not bypass the course discipline or optional visual check.
8. **Review UI:** shows subject lock, relevance mode, per-image evidence, whether verification was **metadata-only** or **visual**, and rejected-result counts. Scores are **uncalibrated estimates, not guarantees**.
9. **Approval required in the web tab:** the Find & Add Images user interface now requires human review for both DOCX and ZIP. For backward compatibility, older clients may still call `/course-images/prepare` with `mode=insert`; this path now applies Strict subject metadata filtering, but cannot provide human approval or optional visual checks. Use the review/confirm API for best results.

## Using the improved workflow

1. Deploy the new backend and frontend together.
2. Open **Find & Add Images**; upload a DOCX or course ZIP.
3. Enter the **exact course title** where possible (e.g. `Award in Managing a Care Team Skills for New Managers at QLS Level 4`).
4. Keep **Strict** selected for healthcare and social care materials.
5. Optional: enable **Verify the actual photograph using AI vision**, only if OpenRouter is configured and the user approves its cost/data transfer.
6. Search, open each chapter, inspect every photo, approve suitable images and confirm to download. When no photo passes, the heading is left unillustrated.

## Railway configuration (optional)

Strict or Balanced text-metadata filtering uses existing stock APIs; no new API key is required. Optional visual checking requires both:

```
OPENROUTER_API_KEY=your_existing_secret
OPENROUTER_VISION_MODEL=openai/gpt-4o-mini
COURSE_IMAGE_MAX_VISION_CHECKS=32
```

Select an OpenRouter model that **accepts image inputs**. Photos are sent to OpenRouter *only* after checking the AI vision box; do not add credentials to the frontend or commit them to Git. The visual model makes text+image requests, not image-generation requests. Chargeable usage may increase with large course ZIPs.

## Operational notes

- **Metadata-only is not actual image recognition.** Pexels/other image descriptions and provider tags may be wrong. Human review remains essential, particularly for medical procedures, medication and safeguarding.
- The scoring rules are qualitative heuristics, **not calibrated percentages**. Likewise, an AI vision confidence estimate is not a guarantee of correctness.
- Strict mode intentionally produces fewer images than older versions when stock captions provide little evidence. No fake filler is inserted to reach an image quota.
- An older v29 browser review cannot silently bypass the new rules; start a fresh search after updating.
- The **v29** chapter-by-chapter ZIP batching, resume controls, unchanged-file protection and report remain intact.
- The updated tests use synthetic documents and mocked stock providers. They do **not** prove accuracy on every real provider catalogue or every medical training topic.

## QA

Run backend tests from `backend`:

```
python -m pytest -q
```

Run frontend type-check with installed dependencies from `frontend`:

```
npm install
npm run lint
```

The v30 change adds focused tests for wrong-domain rejection, medication actions, Pexels metadata filtering, optical verification and unchanged ZIP behaviour.
