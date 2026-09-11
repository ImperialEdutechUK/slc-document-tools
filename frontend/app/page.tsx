"use client";

import {
  DragEvent,
  FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type Tab = "formatter" | "word-pdf" | "pdf-editor";

type JobResponse = {
  id: string;
  job_type: string;
  status: string;
  original_filename?: string | null;
  output_filename?: string | null;
  download_url?: string | null;
  report_url?: string | null;
  report?: string | null;
  details?: Record<string, any>;
  error_message?: string | null;
};

type EditableParagraph = {
  index: number;
  text: string;
  style: string;
  list_type: "bullet" | "number" | "none";
  page_break_before: boolean;
  keep_together: boolean;
};

type FooterSettings = {
  course_text: string;
  copyright_text: string;
  page_label: string;
  footer_parts: number;
};

const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080"
).replace(/\/$/, "");

function apiUrl(path?: string | null) {
  if (!path) return "#";
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

async function apiRequest(path: string, options: RequestInit) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      payload.detail ||
        payload.error ||
        "The request could not be completed."
    );
  }

  return payload;
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  return payload.detail || payload.error || fallback;
}

/* -------------------------------------------------------
   FILE / DRAG AND DROP HELPERS
------------------------------------------------------- */

function fileMatchesAccept(file: File, accept: string) {
  if (!accept.trim()) return true;

  const rules = accept
    .split(",")
    .map((rule) => rule.trim().toLowerCase())
    .filter(Boolean);

  const filename = file.name.toLowerCase();
  const mime = file.type.toLowerCase();

  return rules.some((rule) => {
    if (rule.startsWith(".")) {
      return filename.endsWith(rule);
    }

    if (rule.endsWith("/*")) {
      const prefix = rule.slice(0, -1);
      return mime.startsWith(prefix);
    }

    return mime === rule;
  });
}

function FileField({
  label,
  accept,
  multiple = false,
  onChange,
  help,
}: {
  label: string;
  accept: string;
  multiple?: boolean;
  onChange: (files: File[]) => void;
  help?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const [dropError, setDropError] = useState("");
  const dragDepth = useRef(0);

  function processFiles(files: File[]) {
    setDropError("");

    const accepted = files.filter((file) =>
      fileMatchesAccept(file, accept)
    );

    const rejected = files.filter(
      (file) => !fileMatchesAccept(file, accept)
    );

    if (accepted.length === 0) {
      setDropError("Unsupported file type.");
      return;
    }

    if (rejected.length > 0) {
      setDropError(
        `${rejected.length} unsupported file${
          rejected.length === 1 ? "" : "s"
        } ignored.`
      );
    }

    if (multiple) {
      onChange(accepted);
    } else {
      onChange([accepted[0]]);
    }
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.stopPropagation();

    dragDepth.current = 0;
    setDragging(false);

    const files = Array.from(event.dataTransfer.files || []);
    if (!files.length) return;

    processFiles(files);
  }

  function handleDragEnter(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.stopPropagation();

    dragDepth.current += 1;
    setDragging(true);
  }

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.stopPropagation();

    event.dataTransfer.dropEffect = "copy";
    setDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.stopPropagation();

    dragDepth.current -= 1;

    if (dragDepth.current <= 0) {
      dragDepth.current = 0;
      setDragging(false);
    }
  }

  return (
    <div className="file-field">
      <span className="field-label">{label}</span>

      <label
        className={`drop-zone ${dragging ? "dragging" : ""}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <span className="upload-icon">{dragging ? "↓" : "⇧"}</span>

        <span className="drop-zone-title">
          {dragging
            ? multiple
              ? "Drop files here"
              : "Drop file here"
            : multiple
            ? "Drag & drop files here or click to browse"
            : "Drag & drop a file here or click to browse"}
        </span>

        <small>{help}</small>

        <input
          type="file"
          accept={accept}
          multiple={multiple}
          onChange={(event) => {
            const selected = Array.from(event.target.files || []);

            if (selected.length) {
              processFiles(selected);
            }

            event.target.value = "";
          }}
        />
      </label>

      {dropError && <small className="file-error">{dropError}</small>}
    </div>
  );
}

/* -------------------------------------------------------
   RESULT CARD
------------------------------------------------------- */

function JobResult({
  job,
  title = "Output ready",
  downloadLabel = "Download output",
}: {
  job: JobResponse;
  title?: string;
  downloadLabel?: string;
}) {
  return (
    <section className="result-card">
      <div>
        <span className="success-dot" />
        <strong>{title}</strong>
        <p>{job.output_filename}</p>
        {job.details?.build && (
          <small>Backend build: {job.details.build}</small>
        )}
      </div>

      <div className="result-actions">
        {job.download_url && (
          <a
            className="button primary"
            href={apiUrl(job.download_url)}
          >
            {downloadLabel}
          </a>
        )}

        {job.report_url && (
          <a
            className="button secondary"
            href={apiUrl(job.report_url)}
          >
            Validation report
          </a>
        )}
      </div>
    </section>
  );
}

/* -------------------------------------------------------
   DOCUMENT FORMATTER + PREVIEW + PDF EXPORT
------------------------------------------------------- */

function FormatterPanel() {
  const [document, setDocument] = useState<File | null>(null);
  const [cover, setCover] = useState<File | null>(null);
  const [images, setImages] = useState<File[]>([]);

  const [awardingBody, setAwardingBody] = useState("");
  const [courseName, setCourseName] = useState("");
  const [chapter, setChapter] = useState("");
  const [autoLinks, setAutoLinks] = useState(true);

  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [editorBusy, setEditorBusy] = useState(false);
  const [editApplyBusy, setEditApplyBusy] = useState(false);

  const [error, setError] = useState("");
  const [job, setJob] = useState<JobResponse | null>(null);
  const [pdfJob, setPdfJob] = useState<JobResponse | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editableParagraphs, setEditableParagraphs] = useState<EditableParagraph[]>([]);
  const [selectedParagraphs, setSelectedParagraphs] = useState<number[]>([]);
  const [editorSearch, setEditorSearch] = useState("");
  const [editMessage, setEditMessage] = useState("");
  const [textEditorOpen, setTextEditorOpen] = useState(false);
  const [textDraft, setTextDraft] = useState("");
  const [footerEditorOpen, setFooterEditorOpen] = useState(false);
  const [footerBusy, setFooterBusy] = useState(false);
  const [footerSettings, setFooterSettings] = useState<FooterSettings>({
    course_text: "",
    copyright_text: "© South London College Ltd",
    page_label: " | Page",
    footer_parts: 0,
  });

  const batchMode = document?.name.toLowerCase().endsWith(".zip") ?? false;

  useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  function resetPostFormatWorkflow() {
    setPdfJob(null);
    setError("");
    setEditorOpen(false);
    setEditableParagraphs([]);
    setSelectedParagraphs([]);
    setEditorSearch("");
    setEditMessage("");
    setTextEditorOpen(false);
    setTextDraft("");
    setFooterEditorOpen(false);
    setFooterSettings({
      course_text: "",
      copyright_text: "© South London College Ltd",
      page_label: " | Page",
      footer_parts: 0,
    });
    setPreviewUrl((current) => {
      if (current) {
        URL.revokeObjectURL(current);
      }
      return "";
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!document) return;

    setBusy(true);
    setError("");
    setJob(null);
    resetPostFormatWorkflow();

    try {
      const form = new FormData();

      form.append("document", document);
      form.append("awarding_body", awardingBody);
      form.append("course_name", courseName);

      if (!batchMode) {
        form.append("chapter", chapter);
      }

      form.append("auto_download_links", String(autoLinks));

      if (cover) {
        form.append("cover_image", cover);
      }

      if (!batchMode) {
        images.forEach((image) => {
          form.append("images", image);
        });
      }

      const result = await apiRequest(
        batchMode
          ? "/api/v1/formatter/batch"
          : "/api/v1/formatter/format",
        {
          method: "POST",
          body: form,
        }
      );

      setJob(result);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Formatting failed."
      );
    } finally {
      setBusy(false);
    }
  }

  async function generatePreview(jobId: string) {
    setPreviewBusy(true);
    setError("");

    try {
      const response = await fetch(
        `${API_BASE}/api/v1/jobs/${jobId}/preview`,
        { method: "GET" }
      );

      if (!response.ok) {
        throw new Error(
          await responseError(response, "Preview could not be generated.")
        );
      }

      const blob = await response.blob();
      const nextPreviewUrl = URL.createObjectURL(blob);

      setPreviewUrl((current) => {
        if (current) {
          URL.revokeObjectURL(current);
        }
        return nextPreviewUrl;
      });
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Preview could not be generated."
      );
    } finally {
      setPreviewBusy(false);
    }
  }

  async function previewFormattedDocument() {
    if (!job || batchMode) return;
    await generatePreview(job.id);
  }

  async function loadEditableParagraphs(jobId: string) {
    setEditorBusy(true);
    setError("");

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${jobId}/editable-paragraphs`,
        { method: "GET" }
      );
      setEditableParagraphs(result.paragraphs || []);
      setSelectedParagraphs([]);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The simple formatting editor could not be loaded."
      );
    } finally {
      setEditorBusy(false);
    }
  }

  async function toggleSimpleEditor() {
    if (!job || batchMode) return;

    if (editorOpen) {
      setEditorOpen(false);
      return;
    }

    setEditorOpen(true);
    setEditMessage("");
    await loadEditableParagraphs(job.id);
  }

  function toggleParagraphSelection(index: number) {
    setSelectedParagraphs((current) =>
      current.includes(index)
        ? current.filter((item) => item !== index)
        : [...current, index]
    );
  }

  async function applySimpleEdit(
    action:
      | "bullets"
      | "numbering"
      | "normal"
      | "page_break_before"
      | "remove_page_break_before"
      | "keep_together"
      | "allow_split"
      | "blank_line_before"
      | "blank_line_after"
  ) {
    if (!job || selectedParagraphs.length === 0) {
      setError("Select at least one paragraph to edit.");
      return;
    }

    setEditApplyBusy(true);
    setError("");
    setEditMessage("");
    setPdfJob(null);

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${job.id}/simple-edit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            paragraph_indices: selectedParagraphs,
            action,
          }),
        }
      );

      setJob(result);
      setEditMessage(
        `${result.details?.simple_edit?.paragraphs_changed ?? selectedParagraphs.length} paragraph(s) updated.`
      );
      await loadEditableParagraphs(result.id);
      await generatePreview(result.id);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "The formatting edit failed."
      );
    } finally {
      setEditApplyBusy(false);
    }
  }

  function openTextEditor() {
    if (selectedParagraphs.length !== 1) {
      setError("Select exactly one paragraph to edit its text.");
      return;
    }

    const paragraph = editableParagraphs.find(
      (item) => item.index === selectedParagraphs[0]
    );
    if (!paragraph) {
      setError("The selected paragraph could not be found.");
      return;
    }

    setError("");
    setFooterEditorOpen(false);
    setTextDraft(paragraph.text);
    setTextEditorOpen(true);
  }

  async function saveTextEdit() {
    if (!job || selectedParagraphs.length !== 1) {
      setError("Select exactly one paragraph to edit its text.");
      return;
    }

    setEditApplyBusy(true);
    setError("");
    setEditMessage("");
    setPdfJob(null);

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${job.id}/edit-text`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            paragraph_index: selectedParagraphs[0],
            text: textDraft,
          }),
        }
      );

      setJob(result);
      setTextEditorOpen(false);
      setEditMessage(
        "Text updated. Pressing Enter in the text box creates a new line in the document."
      );
      await loadEditableParagraphs(result.id);
      await generatePreview(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The text edit failed.");
    } finally {
      setEditApplyBusy(false);
    }
  }

  async function toggleFooterEditor() {
    if (!job) return;

    if (footerEditorOpen) {
      setFooterEditorOpen(false);
      return;
    }

    setFooterBusy(true);
    setError("");
    setTextEditorOpen(false);

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${job.id}/footer-settings`,
        { method: "GET" }
      );
      setFooterSettings({
        course_text: result.course_text ?? "",
        copyright_text:
          result.copyright_text ?? "© South London College Ltd",
        page_label: result.page_label ?? " | Page",
        footer_parts: result.footer_parts ?? 0,
      });
      setFooterEditorOpen(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "The footer could not be loaded."
      );
    } finally {
      setFooterBusy(false);
    }
  }

  async function saveFooterEdit() {
    if (!job) return;

    setEditApplyBusy(true);
    setError("");
    setEditMessage("");
    setPdfJob(null);

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${job.id}/edit-footer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            course_text: footerSettings.course_text,
            copyright_text: footerSettings.copyright_text,
            page_label: footerSettings.page_label,
          }),
        }
      );

      setJob(result);
      setFooterEditorOpen(false);
      setEditMessage("Footer updated on the document pages.");
      await loadEditableParagraphs(result.id);
      await generatePreview(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The footer edit failed.");
    } finally {
      setEditApplyBusy(false);
    }
  }

  async function updateTableOfContents() {
    if (!job) return;

    setEditApplyBusy(true);
    setError("");
    setEditMessage("");
    setPdfJob(null);

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${job.id}/update-toc`,
        { method: "POST" }
      );
      setJob(result);
      setTextEditorOpen(false);
      setFooterEditorOpen(false);
      const tocDetails = result.details?.toc_update;
      setEditMessage(
        tocDetails?.exact_layout_refresh
          ? `Table of Contents updated with ${tocDetails?.entries ?? 0} heading(s), including refreshed page numbers.`
          : `Table of Contents rebuilt with ${tocDetails?.entries ?? 0} heading(s). Exact page numbers will refresh when opened in Word.`
      );
      await loadEditableParagraphs(result.id);
      await generatePreview(result.id);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The Table of Contents could not be updated."
      );
    } finally {
      setEditApplyBusy(false);
    }
  }

  async function convertFormattedDocumentToPdf() {
    if (!job || batchMode) return;

    setPdfBusy(true);
    setError("");
    setPdfJob(null);

    try {
      const result = await apiRequest(
        `/api/v1/jobs/${job.id}/convert-to-pdf`,
        { method: "POST" }
      );
      setPdfJob(result);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "PDF conversion failed."
      );
    } finally {
      setPdfBusy(false);
    }
  }

  const imageDetails = job?.details?.images;
  const linked = job?.details?.linked_images;
  const batchDetails = batchMode ? job?.details : null;

  const filteredEditableParagraphs = useMemo(() => {
    const query = editorSearch.trim().toLowerCase();
    if (!query) return editableParagraphs;
    return editableParagraphs.filter((paragraph) =>
      `${paragraph.text} ${paragraph.style} ${paragraph.list_type}`
        .toLowerCase()
        .includes(query)
    );
  }, [editableParagraphs, editorSearch]);

  const allVisibleParagraphsSelected =
    filteredEditableParagraphs.length > 0 &&
    filteredEditableParagraphs.every((paragraph) =>
      selectedParagraphs.includes(paragraph.index)
    );

  function toggleAllVisibleParagraphs() {
    const visible = filteredEditableParagraphs.map((paragraph) => paragraph.index);
    if (allVisibleParagraphsSelected) {
      setSelectedParagraphs((current) =>
        current.filter((index) => !visible.includes(index))
      );
      return;
    }

    setSelectedParagraphs((current) =>
      Array.from(new Set([...current, ...visible]))
    );
  }

  return (
    <form onSubmit={submit} className="panel-stack">
      <section className="hero compact">
        <div>
          <span className="eyebrow">SLC Document Workflow</span>
          <h2>Document Formatter</h2>
          <p>
            Format your document, preview and make simple formatting edits,
            then convert it to PDF without moving between separate tabs.
          </p>
        </div>

        <div className="hero-badge">DOCX / PDF</div>
      </section>

      <div className="two-column">
        <section className="card">
          <div className="section-heading">
            <span>01</span>

            <div>
              <h3>Document details</h3>
              <p>Used on the formatted cover page.</p>
            </div>
          </div>

          <label className="input-label">
            Awarding Body
            <input
              value={awardingBody}
              onChange={(event) => setAwardingBody(event.target.value)}
            />
          </label>

          <label className="input-label">
            Course Name / Centre Text
            <input
              value={courseName}
              onChange={(event) => setCourseName(event.target.value)}
            />
          </label>

          <label className="input-label">
            Chapter / Unit Title
            <input
              value={chapter}
              disabled={batchMode}
              placeholder={
                batchMode
                  ? "Taken from each DOCX filename in ZIP mode"
                  : undefined
              }
              onChange={(event) => setChapter(event.target.value)}
            />

            {batchMode && (
              <small>
                Each formatted document uses its own filename as the cover
                chapter / unit title.
              </small>
            )}
          </label>
        </section>

        <section className="card">
          <div className="section-heading">
            <span>02</span>

            <div>
              <h3>Source document</h3>
              <p>
                Drag and drop one DOCX or a ZIP containing multiple DOCX files,
                plus an optional cover image.
              </p>
            </div>
          </div>

          <FileField
            label="Source DOCX / ZIP"
            accept=".docx,.zip"
            onChange={(files) => {
              const next = files[0] || null;
              setDocument(next);
              setJob(null);
              resetPostFormatWorkflow();

              if (next?.name.toLowerCase().endsWith(".zip")) {
                setImages([]);
              }
            }}
            help={document?.name || "DOCX or ZIP containing DOCX files"}
          />

          <FileField
            label="Cover image (optional)"
            accept=".jpg,.jpeg,.png"
            onChange={(files) => setCover(files[0] || null)}
            help={cover?.name || "JPG or PNG"}
          />
        </section>
      </div>

      <section className="card">
        <div className="section-heading">
          <span>03</span>

          <div>
            <h3>Images</h3>
            <p>
              Linked images are downloaded first; uploaded images take priority
              for the same number.
            </p>
          </div>
        </div>

        <label className="toggle-row">
          <input
            type="checkbox"
            checked={autoLinks}
            onChange={(event) => setAutoLinks(event.target.checked)}
          />

          <span>
            <strong>Automatically retrieve linked images</strong>
            <small>
              Supports direct image URLs and configured Magnific / Freepik
              resource links.
            </small>
          </span>
        </label>

        {!batchMode ? (
          <>
            <FileField
              label="Manual images / image ZIP (optional)"
              accept=".jpg,.jpeg,.png,.webp,.zip"
              multiple
              onChange={setImages}
              help={
                images.length
                  ? `${images.length} file(s) selected`
                  : "Drag images or ZIP here as a fallback or override"
              }
            />

            {images.length > 0 && (
              <div className="file-list">
                {images.map((file) => (
                  <span key={`${file.name}-${file.size}`}>{file.name}</span>
                ))}
              </div>
            )}
          </>
        ) : (
          <small>
            ZIP batch mode retrieves linked images separately for each DOCX.
            Manual image overrides remain available in single-DOCX mode.
          </small>
        )}
      </section>

      {error && <div className="error-box">{error}</div>}

      <button
        className="button primary large"
        disabled={!document || busy}
        type="submit"
      >
        {busy
          ? batchMode
            ? "Formatting batch…"
            : "Formatting…"
          : batchMode
          ? "Format ZIP batch"
          : "Format document"}
      </button>

      {job && (
        <>
          <JobResult
            job={job}
            title={
              batchMode
                ? "Formatted ZIP batch ready"
                : "Formatted document ready"
            }
            downloadLabel={
              batchMode ? "Download formatted ZIP" : "Download formatted DOCX"
            }
          />

          {batchMode ? (
            <div className="metrics">
              <div>
                <span>Detected</span>
                <strong>{batchDetails?.documents_detected ?? 0}</strong>
              </div>

              <div>
                <span>Formatted</span>
                <strong>{batchDetails?.formatted ?? 0}</strong>
              </div>

              <div>
                <span>Skipped</span>
                <strong>{batchDetails?.skipped ?? 0}</strong>
              </div>

              <div>
                <span>Failed</span>
                <strong>{batchDetails?.failed ?? 0}</strong>
              </div>
            </div>
          ) : (
            <>
              <div className="metrics">
                <div>
                  <span>Placeholders</span>
                  <strong>{imageDetails?.placeholders ?? 0}</strong>
                </div>

                <div>
                  <span>Inserted</span>
                  <strong>{imageDetails?.inserted ?? 0}</strong>
                </div>

                <div>
                  <span>Missing</span>
                  <strong>{imageDetails?.missing?.length ?? 0}</strong>
                </div>

                <div>
                  <span>Linked downloads</span>
                  <strong>{linked?.downloaded ?? 0}</strong>
                </div>
              </div>

              <section className="card workflow-card">
                <div className="section-heading">
                  <span>04</span>

                  <div>
                    <h3>Preview, edit and export</h3>
                    <p>
                      Review the formatted document, make simple formatting
                      adjustments if needed, then convert the same file to PDF.
                    </p>
                  </div>
                </div>

                <div className="workflow-actions">
                  <button
                    className="button secondary"
                    type="button"
                    disabled={previewBusy || pdfBusy || editApplyBusy}
                    onClick={previewFormattedDocument}
                  >
                    {previewBusy ? "Generating preview…" : "Preview"}
                  </button>

                  <button
                    className="button secondary"
                    type="button"
                    disabled={previewBusy || pdfBusy || editApplyBusy}
                    onClick={toggleSimpleEditor}
                  >
                    {editorOpen ? "Close editor" : "Edit formatting"}
                  </button>

                  <button
                    className="button primary"
                    type="button"
                    disabled={pdfBusy || previewBusy || editApplyBusy}
                    onClick={convertFormattedDocumentToPdf}
                  >
                    {pdfBusy ? "Converting to PDF…" : "Convert to PDF"}
                  </button>
                </div>

                {editorOpen && (
                  <section className="simple-editor">
                    <div className="simple-editor-heading">
                      <div>
                        <h4>Document editor</h4>
                        <p>
                          Edit paragraph text, add new lines, change lists and
                          page flow, update the footer, or refresh the Table of
                          Contents. The preview refreshes after each saved edit.
                        </p>
                      </div>

                      <span className="selection-count">
                        {selectedParagraphs.length} selected
                      </span>
                    </div>

                    <div className="editor-tool-section">
                      <span className="editor-tool-label">Paragraph tools</span>

                      <div className="editor-toolbar">
                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length !== 1}
                          onClick={openTextEditor}
                        >
                          Edit text / next line
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("bullets")}
                        >
                          • Bullets
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("numbering")}
                        >
                          1. Numbering
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("normal")}
                        >
                          Normal text
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("blank_line_before")}
                        >
                          Add line above
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("blank_line_after")}
                        >
                          Add line below
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("page_break_before")}
                        >
                          Move to next page
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("remove_page_break_before")}
                        >
                          Normal page flow
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("keep_together")}
                        >
                          Keep paragraph together
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || selectedParagraphs.length === 0}
                          onClick={() => applySimpleEdit("allow_split")}
                        >
                          Allow page split
                        </button>
                      </div>
                    </div>

                    <div className="editor-tool-section">
                      <span className="editor-tool-label">Document tools</span>

                      <div className="editor-toolbar">
                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy || footerBusy}
                          onClick={toggleFooterEditor}
                        >
                          {footerBusy
                            ? "Loading footer…"
                            : footerEditorOpen
                            ? "Close footer editor"
                            : "Edit footer"}
                        </button>

                        <button
                          type="button"
                          className="button secondary small"
                          disabled={editApplyBusy}
                          onClick={updateTableOfContents}
                        >
                          Update TOC
                        </button>
                      </div>
                    </div>

                    {textEditorOpen && (
                      <div className="editor-subpanel">
                        <div className="editor-subpanel-heading">
                          <div>
                            <h5>Edit paragraph text</h5>
                            <p>
                              Change the wording directly. Press Enter anywhere
                              in the box to place text on the next line in Word.
                            </p>
                          </div>
                        </div>

                        <textarea
                          className="editor-textarea"
                          value={textDraft}
                          onChange={(event) => setTextDraft(event.target.value)}
                          rows={7}
                          autoFocus
                        />

                        <div className="editor-subpanel-actions">
                          <button
                            type="button"
                            className="button primary small"
                            disabled={editApplyBusy}
                            onClick={saveTextEdit}
                          >
                            {editApplyBusy ? "Saving…" : "Save text"}
                          </button>

                          <button
                            type="button"
                            className="button secondary small"
                            disabled={editApplyBusy}
                            onClick={() => setTextEditorOpen(false)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

                    {footerEditorOpen && (
                      <div className="editor-subpanel">
                        <div className="editor-subpanel-heading">
                          <div>
                            <h5>Edit footer</h5>
                            <p>
                              Update the course text, copyright text and page
                              label used in the generated footer.
                            </p>
                          </div>

                          <span className="selection-count">
                            {footerSettings.footer_parts} footer part(s)
                          </span>
                        </div>

                        <div className="footer-editor-grid">
                          <label className="input-label">
                            Course / centre footer text
                            <input
                              value={footerSettings.course_text}
                              onChange={(event) =>
                                setFooterSettings((current) => ({
                                  ...current,
                                  course_text: event.target.value,
                                }))
                              }
                            />
                          </label>

                          <label className="input-label">
                            Copyright text
                            <input
                              value={footerSettings.copyright_text}
                              onChange={(event) =>
                                setFooterSettings((current) => ({
                                  ...current,
                                  copyright_text: event.target.value,
                                }))
                              }
                            />
                          </label>

                          <label className="input-label">
                            Page label
                            <input
                              value={footerSettings.page_label}
                              onChange={(event) =>
                                setFooterSettings((current) => ({
                                  ...current,
                                  page_label: event.target.value,
                                }))
                              }
                            />
                          </label>
                        </div>

                        <div className="editor-subpanel-actions">
                          <button
                            type="button"
                            className="button primary small"
                            disabled={editApplyBusy}
                            onClick={saveFooterEdit}
                          >
                            {editApplyBusy ? "Saving…" : "Save footer"}
                          </button>

                          <button
                            type="button"
                            className="button secondary small"
                            disabled={editApplyBusy}
                            onClick={() => setFooterEditorOpen(false)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

                    <div className="editor-filter-row">
                      <input
                        value={editorSearch}
                        onChange={(event) => setEditorSearch(event.target.value)}
                        placeholder="Find a paragraph or heading…"
                        aria-label="Find a paragraph or heading"
                      />

                      <button
                        type="button"
                        className="text-button"
                        disabled={filteredEditableParagraphs.length === 0}
                        onClick={toggleAllVisibleParagraphs}
                      >
                        {allVisibleParagraphsSelected
                          ? "Clear shown"
                          : "Select shown"}
                      </button>
                    </div>

                    {editMessage && (
                      <div className="success-box compact-success">
                        {editMessage}
                      </div>
                    )}

                    {editorBusy ? (
                      <div className="editor-loading">Loading paragraphs…</div>
                    ) : (
                      <div className="editable-paragraph-list">
                        {filteredEditableParagraphs.map((paragraph) => {
                          const selected = selectedParagraphs.includes(
                            paragraph.index
                          );

                          return (
                            <label
                              className={`editable-paragraph ${
                                selected ? "selected" : ""
                              }`}
                              key={paragraph.index}
                            >
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={() =>
                                  toggleParagraphSelection(paragraph.index)
                                }
                              />

                              <span className="editable-paragraph-content">
                                <span className="paragraph-badges">
                                  <span>{paragraph.style}</span>
                                  {paragraph.list_type !== "none" && (
                                    <span>{
                                      paragraph.list_type === "bullet"
                                        ? "Bullets"
                                        : "Numbered"
                                    }</span>
                                  )}
                                  {paragraph.page_break_before && (
                                    <span>Starts new page</span>
                                  )}
                                  {paragraph.keep_together && (
                                    <span>Keeps together</span>
                                  )}
                                </span>

                                <span className="paragraph-text">
                                  {paragraph.text}
                                </span>
                              </span>
                            </label>
                          );
                        })}

                        {filteredEditableParagraphs.length === 0 && (
                          <div className="editor-empty">
                            No matching editable paragraphs were found.
                          </div>
                        )}
                      </div>
                    )}
                  </section>
                )}

                {previewUrl && (
                  <div className="preview-shell">
                    <iframe
                      className="document-preview"
                      src={previewUrl}
                      title="Formatted document preview"
                    />
                  </div>
                )}

                {pdfJob && (
                  <div className="pdf-result-stack">
                    <JobResult
                      job={pdfJob}
                      title="PDF ready"
                      downloadLabel="Download PDF"
                    />

                    {pdfJob.details?.second_blank_pages_removed > 0 && (
                      <div className="success-box">
                        The unwanted blank second page was removed automatically.
                      </div>
                    )}
                  </div>
                )}
              </section>
            </>
          )}

          {!batchMode && linked?.entries?.length > 0 && (
            <section className="card compact-card">
              <h3>Linked image results</h3>

              <div className="link-results">
                {linked.entries.map((entry: any) => (
                  <div key={`${entry.number}-${entry.url}`}>
                    <strong>Image {entry.number}</strong>

                    <span className={`status ${entry.status}`}>
                      {entry.status.replace("_", " ")}
                    </span>

                    <small>{entry.message || entry.source_type}</small>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </form>
  );
}

/* -------------------------------------------------------
   WORD TO PDF
------------------------------------------------------- */

function WordPdfPanel() {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<JobResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!files.length) return;

    setBusy(true);
    setError("");
    setJob(null);

    try {
      const form = new FormData();

      files.forEach((file) => {
        form.append("files", file);
      });

      const result = await apiRequest(
        "/api/v1/word-to-pdf",
        {
          method: "POST",
          body: form,
        }
      );

      setJob(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Conversion failed."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="panel-stack"
    >
      <section className="hero compact">
        <div>
          <span className="eyebrow">
            SLC Conversion Workflow
          </span>

          <h2>Word → PDF</h2>

          <p>
            Convert assignment briefs,
            written-assignment templates, or an entire ZIP
            without running the formatter.
          </p>
        </div>

        <div className="hero-badge">PDF</div>
      </section>

      <section className="card">
        <div className="section-heading">
          <span>01</span>

          <div>
            <h3>Upload Word files</h3>

            <p>
              Drag multiple DOCX files or ZIP packages into
              the area below.
            </p>
          </div>
        </div>

        <FileField
          label="DOCX / ZIP"
          accept=".docx,.zip"
          multiple
          onChange={setFiles}
          help={
            files.length
              ? `${files.length} file(s) selected`
              : "DOCX and ZIP files supported"
          }
        />

        {files.length > 0 && (
          <div className="file-list">
            {files.map((file) => (
              <span key={`${file.name}-${file.size}`}>
                {file.name}
              </span>
            ))}
          </div>
        )}
      </section>

      {error && (
        <div className="error-box">{error}</div>
      )}

      <button
        className="button primary large"
        disabled={!files.length || busy}
        type="submit"
      >
        {busy ? "Converting…" : "Convert to PDF"}
      </button>

      {job && (
        <>
          <JobResult
            job={job}
            title={`${
              job.details?.converted_documents || 1
            } PDF file(s) ready`}
          />

          {job.details?.garamond_exact && (
            <section className="card compact-card">
              <h3>PDF font rendering</h3>
              <p>
                PDF font: <strong>Garamond</strong>. Exact Garamond is installed on the server; font substitution is disabled.
              </p>
            </section>
          )}
        </>
      )}
    </form>
  );
}

/* -------------------------------------------------------
   PDF EDITOR
------------------------------------------------------- */

function PdfEditorPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [pageCount, setPageCount] = useState(0);

  const [selected, setSelected] = useState<Set<number>>(
    new Set()
  );

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<JobResponse | null>(null);

  const remaining = useMemo(
    () => Math.max(0, pageCount - selected.size),
    [pageCount, selected]
  );

  async function choosePdf(files: File[]) {
    const next = files[0] || null;

    setFile(next);
    setPageCount(0);
    setSelected(new Set());
    setJob(null);
    setError("");

    if (!next) return;

    try {
      const form = new FormData();

      form.append("file", next);

      const data = await apiRequest(
        "/api/v1/pdf/page-count",
        {
          method: "POST",
          body: form,
        }
      );

      setPageCount(data.pages);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The PDF could not be read."
      );
    }
  }

  function togglePage(page: number) {
    setSelected((current) => {
      const next = new Set(current);

      if (next.has(page)) {
        next.delete(page);
      } else {
        next.add(page);
      }

      return next;
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (!file || !selected.size) return;

    setBusy(true);
    setError("");
    setJob(null);

    try {
      const form = new FormData();

      form.append("file", file);

      form.append(
        "pages",
        Array.from(selected)
          .sort((a, b) => a - b)
          .join(",")
      );

      const result = await apiRequest(
        "/api/v1/pdf/remove-pages",
        {
          method: "POST",
          body: form,
        }
      );

      setJob(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "PDF editing failed."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="panel-stack"
    >
      <section className="hero compact">
        <div>
          <span className="eyebrow">
            SLC PDF Workflow
          </span>

          <h2>PDF Page Editor</h2>

          <p>
            Remove selected pages from a PDF and download a
            clean revised copy.
          </p>
        </div>

        <div className="hero-badge">EDIT</div>
      </section>

      <section className="card">
        <div className="section-heading">
          <span>01</span>

          <div>
            <h3>Choose PDF</h3>

            <p>
              Drag and drop a PDF below. The original file is
              never changed.
            </p>
          </div>
        </div>

        <FileField
          label="PDF file"
          accept=".pdf"
          onChange={choosePdf}
          help={
            file?.name ||
            "PDF only"
          }
        />
      </section>

      {pageCount > 0 && (
        <section className="card">
          <div className="summary-row">
            <div>
              <span>Total pages</span>
              <strong>{pageCount}</strong>
            </div>

            <div>
              <span>Selected</span>
              <strong>{selected.size}</strong>
            </div>

            <div>
              <span>Remaining</span>
              <strong>{remaining}</strong>
            </div>
          </div>

          <h3>Select pages to remove</h3>

          <div className="page-grid">
            {Array.from(
              { length: pageCount },
              (_, index) => index + 1
            ).map((page) => (
              <button
                type="button"
                onClick={() => togglePage(page)}
                className={
                  selected.has(page)
                    ? "page-chip selected"
                    : "page-chip"
                }
                key={page}
              >
                {page}
              </button>
            ))}
          </div>
        </section>
      )}

      {error && (
        <div className="error-box">{error}</div>
      )}

      <button
        className="button primary large"
        disabled={
          !file ||
          !selected.size ||
          selected.size >= pageCount ||
          busy
        }
        type="submit"
      >
        {busy
          ? "Updating PDF…"
          : "Remove selected pages"}
      </button>

      {job && (
        <JobResult
          job={job}
          title="Updated PDF ready"
        />
      )}
    </form>
  );
}

/* -------------------------------------------------------
   MAIN APP
------------------------------------------------------- */

export default function Home() {
  const [tab, setTab] = useState<Tab>("formatter");

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">SLC</span>

          <div>
            <strong>Document Tools</strong>
            <small>South London College</small>
          </div>
        </div>

        <span className="environment">Vercel + Railway</span>
      </header>

      <div className="workspace">
        <nav className="tabs" aria-label="Document tools">
          <button
            type="button"
            className={tab === "formatter" ? "active" : ""}
            onClick={() => setTab("formatter")}
          >
            Document Formatter
          </button>

          <button
            type="button"
            className={tab === "word-pdf" ? "active" : ""}
            onClick={() => setTab("word-pdf")}
          >
            Word → PDF
          </button>

          <button
            type="button"
            className={tab === "pdf-editor" ? "active" : ""}
            onClick={() => setTab("pdf-editor")}
          >
            PDF Editor
          </button>
        </nav>

        {tab === "formatter" && <FormatterPanel />}
        {tab === "word-pdf" && <WordPdfPanel />}
        {tab === "pdf-editor" && <PdfEditorPanel />}
      </div>
    </main>
  );
}
