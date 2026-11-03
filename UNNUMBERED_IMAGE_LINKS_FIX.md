# Document Formatter: auto-insert unnumbered image links

**Build:** `2026.11.03-v21-unnumbered-image-links`

## What changed

The Document Formatter no longer requires Word image hyperlinks to be named
`IMAGE1`, `IMAGE2`, etc. When **Automatically retrieve linked images** is enabled,
the backend examines the destination of each hyperlink and its display text,
then assigns a unique *internal* image number for use by the existing placement
engine. No renaming or numbering is needed in the source Word document.

It now recognises:

- Direct links to raster image files: `.jpg`, `.jpeg`, `.png`, `.webp` (including
  query strings), even if the clickable text is `Click here` or another name.
- Freepik and Magnific stock-resource links, independent of their display text.
- Recognisable photo-page paths on stock-image websites and other photo sites;
  photo pages must actually advertise a publicly downloadable raster image.
- Links explicitly labelled with image/photo/picture/illustration/diagram/etc.
  words even when their URLs do not have an image-file extension.
- Multiple distinct image links in a paragraph and repeated URLs in separate
  paragraphs; the downloaded bytes are reused and each matching link receives
  a placed image.
- Existing numbered `IMAGE1`, `IMAGE2` Word placeholders and manual image files
  remain supported. Unnumbered links are assigned IDs that do not collide with
  manual uploads or existing numbered placeholders.

Image links in **Summary**, **Table of Contents/Contents**, and **References**
sections are skipped. Ordinary website and citation hyperlinks are not
retrieved as pictures. The original hyperlink text, other paragraphs, and
headings remain untouched for unnumbered links; the image is embedded in a
separate centred paragraph at its linked location.

## Downloading limitations

- A website link with no image indicators in **either** the URL or hyperlink
  text cannot reliably be distinguished from an ordinary website link; such
  links are intentionally skipped to protect references and source material.
- Stock-photo providers can restrict downloads or require API access. Keep the
  existing `MAGNIFIC_API_KEY` / `FREEPIK_API_KEY` Railway variables configured.
- For an image-specific web page, the formatter tries the page's `og:image`,
  `twitter:image`, or `image_src` metadata. If there is no downloadable public
  JPG, PNG, or WebP, the result is labelled **DOWNLOAD FAILED**, not success.
- Network requests are limited to public HTTP(S) destinations with redirect,
  private-address, image validity, file-size, and per-document count safeguards.

## Deployment

1. Copy the changed files from the **patch ZIP** into the same paths in the
   existing GitHub project; or use the complete code ZIP. Preserve your
   original `.env` values, Railway credentials, and local `backend/fonts/`
   files (font binaries are not included in the ZIP).
2. Redeploy the backend on **Railway** and the frontend on **Vercel**.
3. In **Document Formatter > Images**, enable **Automatically retrieve linked
   images** and upload a DOCX containing photo hyperlinks with any names.
4. Run formatting and verify **Inserted** equals the number of available
   photo links. The **Linked image results** section shows each source label
   and the actual result. Review the validation report for downloads that
   could not be embedded or photo pages that did not return a usable picture.
5. ZIP batch formatting automatically uses the same discovery logic for each
   DOCX it processes.

## Verification

- The previous numbered-link regression tests pass.
- New tests verify unnumbered named links, stock-photo links, direct photo
  downloads, linked HTML photo pages, multiple links in a paragraph, duplicate
  URLs, manual-number collisions, references and summary exclusions, and the
  resulting embedded DOCX picture count with original body text intact.
- A generated test document was rendered to page images and visually checked;
  two unnumbered links placed two inline photographs below their paragraphs.
- The overall backend test suite currently has one **pre-existing** failure
  in the separate AI photo-generation polling integration. It fails in the
  previous version too and is unrelated to linked-image placement.

## Run tests

```sh
cd backend
python -m unittest discover -s tests -p 'test_unnumbered_image_links.py' -v
python -m unittest discover -s tests -p 'test_linked_image_placement_integration.py' -v
```
