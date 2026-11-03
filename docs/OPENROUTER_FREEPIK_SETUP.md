# Connect your OpenRouter and Freepik API keys

The **Find & Add Images** option uses Freepik for stock-photo search and/or AI image generation. OpenRouter is a separate, optional **text-based scene planner** that improves search phrases and photographic prompts. It does not itself create images in this integration.

## 1. Add the keys to the backend (Railway)

In Railway, open the SLC Document Tools **backend** service, go to **Variables**, and add:

```dotenv
FREEPIK_API_KEY=<your Freepik API key>
OPENROUTER_API_KEY=<your OpenRouter API key>
OPENROUTER_MODEL=openai/gpt-4o-mini
FREEPIK_IMAGE_MODEL=flux-2-pro
COURSE_IMAGE_MAX_GENERATIONS=8
```

For a new **Magnific** account, use `MAGNIFIC_API_KEY` instead of `FREEPIK_API_KEY`. If your key uses a specific legacy/new API endpoint, use the matching `FREEPIK_API_BASE` and `FREEPIK_API_HEADER` variables as documented in `COURSE_IMAGES_WORKFLOW.md`.

**Never paste keys into the browser, upload them in ZIPs, add them to `NEXT_PUBLIC_*`, commit `.env`, or send them in messages.** After setting Railway variables, redeploy the backend. The frontend needs no secret keys.

## 2. How to use it

Open **Find & Add Images** and upload one chapter `.docx` or a ZIP of chapters. **Add images to my Word document** is selected by default; no separate guide or cover is generated during insertion.

- Choose **Add images to my Word document** to insert relevant photographs below about half the eligible headings while preserving content and styles.
- Choose **Image guides & covers (optional)** to receive one clickable photo-search guide per chapter instead of changing Word content. With Freepik enabled, guides include Freepik links in addition to Pexels and StockSnap.
- The **Image source** selection has four always-visible cards: **Pexels, then Freepik**, **Freepik only**, **Pexels only** and **Freepik AI generation**. Click the card you want; there is no hard-to-read dropdown.
- Enable **Use OpenRouter to improve image search ideas** only if AI text refinement is desired; it may incur model charges and sends headings plus short text excerpts to the external API.
- Enable **Generate only when no matching stock photo is found** if new images are allowed as a fallback. This defaults **off**. Generation is limited to 8 attempts per job by default, even if more headings qualify.

## 3. What the result contains

**Single DOCX input, insertion mode:** one enhanced Word document (`..._with_images.docx`) ready to download directly. No ZIP is created.

**ZIP input, insertion mode:** a ZIP containing only enhanced Word documents at its root (no guide or cover folders).

**Guide-only mode:** a ZIP with `Matching Image Search Guides/` and `Cover Images/`.

The results screen shows the download button, an inserted-image count and collapsible processing notices. Image-source and provider metadata remain available in the backend job response. Guide mode includes searchable Freepik source links where configured.

## 4. Important limits

- The actual API connection has not been tested against **your** credentials until you configure the service and run a real request; the automated tests use mocked provider responses.
- Using a Freepik API key does not make all images copyright-free. The tool filters out obvious premium-only stock results and records available free/freemium licence metadata, but someone must verify attribution, publication rights and subject relevance before publishing.
- Generation and OpenRouter calls can consume credits. Freepik images are created by the asynchronous **Flux 2 Pro** API and may take longer than stock search. A model may reject a request or return no image; the tool reports missing images rather than inserting unrelated ones.
- No original Word text or pre-existing photos are removed. Images can affect document pagination.
- For confidential training material, obtain permission before sending chapter excerpts or visual prompts to external providers.
