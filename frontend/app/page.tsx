"use client";

import { FormEvent, useMemo, useState } from "react";

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

const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080").replace(/\/$/, "");

function apiUrl(path?: string | null) {
  if (!path) return "#";
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

async function apiRequest(path: string, options: RequestInit) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "The request could not be completed.");
  }
  return payload;
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
  onChange: (files: FileList | null) => void;
  help?: string;
}) {
  return (
    <label className="file-field">
      <span className="field-label">{label}</span>
      <span className="drop-zone">
        <span className="upload-icon">⇧</span>
        <span>Choose {multiple ? "files" : "file"}</span>
        <small>{help}</small>
      </span>
      <input type="file" accept={accept} multiple={multiple} onChange={(e) => onChange(e.target.files)} />
    </label>
  );
}

function JobResult({ job, title = "Output ready" }: { job: JobResponse; title?: string }) {
  return (
    <section className="result-card">
      <div>
        <span className="success-dot" />
        <strong>{title}</strong>
        <p>{job.output_filename}</p>
      </div>
      <div className="result-actions">
        {job.download_url && (
          <a className="button primary" href={apiUrl(job.download_url)}>
            Download output
          </a>
        )}
        {job.report_url && (
          <a className="button secondary" href={apiUrl(job.report_url)}>
            Validation report
          </a>
        )}
      </div>
    </section>
  );
}

function FormatterPanel() {
  const [document, setDocument] = useState<File | null>(null);
  const [cover, setCover] = useState<File | null>(null);
  const [images, setImages] = useState<File[]>([]);
  const [awardingBody, setAwardingBody] = useState("");
  const [courseName, setCourseName] = useState("");
  const [chapter, setChapter] = useState("");
  const [autoLinks, setAutoLinks] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<JobResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!document) return;
    setBusy(true);
    setError("");
    setJob(null);
    try {
      const form = new FormData();
      form.append("document", document);
      form.append("awarding_body", awardingBody);
      form.append("course_name", courseName);
      form.append("chapter", chapter);
      form.append("auto_download_links", String(autoLinks));
      if (cover) form.append("cover_image", cover);
      images.forEach((image) => form.append("images", image));
      setJob(await apiRequest("/api/v1/formatter/format", { method: "POST", body: form }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Formatting failed.");
    } finally {
      setBusy(false);
    }
  }

  const imageDetails = job?.details?.images;
  const linked = job?.details?.linked_images;

  return (
    <form onSubmit={submit} className="panel-stack">
      <section className="hero compact">
        <div>
          <span className="eyebrow">SLC Document Workflow</span>
          <h2>Document Formatter</h2>
          <p>Format the DOCX, retrieve linked images automatically, and use manual uploads only where needed.</p>
        </div>
        <div className="hero-badge">DOCX</div>
      </section>

      <div className="two-column">
        <section className="card">
          <div className="section-heading"><span>01</span><div><h3>Document details</h3><p>Used on the formatted cover page.</p></div></div>
          <label className="input-label">Awarding Body<input value={awardingBody} onChange={(e) => setAwardingBody(e.target.value)} /></label>
          <label className="input-label">Course Name / Centre Text<input value={courseName} onChange={(e) => setCourseName(e.target.value)} /></label>
          <label className="input-label">Chapter / Unit Title<input value={chapter} onChange={(e) => setChapter(e.target.value)} /></label>
        </section>

        <section className="card">
          <div className="section-heading"><span>02</span><div><h3>Source document</h3><p>Upload the DOCX and optional cover image.</p></div></div>
          <FileField label="Source DOCX" accept=".docx" onChange={(files) => setDocument(files?.[0] || null)} help={document?.name || "DOCX only"} />
          <FileField label="Cover image (optional)" accept=".jpg,.jpeg,.png" onChange={(files) => setCover(files?.[0] || null)} help={cover?.name || "JPG or PNG"} />
        </section>
      </div>

      <section className="card">
        <div className="section-heading"><span>03</span><div><h3>Images</h3><p>Linked images are downloaded first; uploaded images take priority for the same number.</p></div></div>
        <label className="toggle-row">
          <input type="checkbox" checked={autoLinks} onChange={(e) => setAutoLinks(e.target.checked)} />
          <span><strong>Automatically retrieve linked images</strong><small>Supports direct image URLs and configured Freepik resource links.</small></span>
        </label>
        <FileField label="Manual images / image ZIP (optional)" accept=".jpg,.jpeg,.png,.webp,.zip" multiple onChange={(files) => setImages(Array.from(files || []))} help={images.length ? `${images.length} file(s) selected` : "Use as a fallback or override"} />
      </section>

      {error && <div className="error-box">{error}</div>}
      <button className="button primary large" disabled={!document || busy} type="submit">
        {busy ? "Formatting…" : "Format document"}
      </button>

      {job && (
        <>
          <JobResult job={job} title="Formatted document ready" />
          <div className="metrics">
            <div><span>Placeholders</span><strong>{imageDetails?.placeholders ?? 0}</strong></div>
            <div><span>Inserted</span><strong>{imageDetails?.inserted ?? 0}</strong></div>
            <div><span>Missing</span><strong>{imageDetails?.missing?.length ?? 0}</strong></div>
            <div><span>Linked downloads</span><strong>{linked?.downloaded ?? 0}</strong></div>
          </div>
          {linked?.entries?.length > 0 && (
            <section className="card compact-card">
              <h3>Linked image results</h3>
              <div className="link-results">
                {linked.entries.map((entry: any) => (
                  <div key={`${entry.number}-${entry.url}`}>
                    <strong>Image {entry.number}</strong>
                    <span className={`status ${entry.status}`}>{entry.status.replace("_", " ")}</span>
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

function WordPdfPanel() {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<JobResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!files.length) return;
    setBusy(true); setError(""); setJob(null);
    try {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      setJob(await apiRequest("/api/v1/word-to-pdf", { method: "POST", body: form }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Conversion failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="panel-stack">
      <section className="hero compact"><div><span className="eyebrow">SLC Conversion Workflow</span><h2>Word → PDF</h2><p>Convert assignment briefs, written-assignment templates, or an entire ZIP without running the formatter.</p></div><div className="hero-badge">PDF</div></section>
      <section className="card">
        <div className="section-heading"><span>01</span><div><h3>Upload Word files</h3><p>Multiple DOCX files and ZIP packages are supported.</p></div></div>
        <FileField label="DOCX / ZIP" accept=".docx,.zip" multiple onChange={(list) => setFiles(Array.from(list || []))} help={files.length ? `${files.length} file(s) selected` : "Choose one or more files"} />
        {files.length > 0 && <div className="file-list">{files.map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}</div>}
      </section>
      {error && <div className="error-box">{error}</div>}
      <button className="button primary large" disabled={!files.length || busy}>{busy ? "Converting…" : "Convert to PDF"}</button>
      {job && <JobResult job={job} title={`${job.details?.converted_documents || 1} PDF file(s) ready`} />}
    </form>
  );
}

function PdfEditorPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<JobResponse | null>(null);

  const remaining = useMemo(() => Math.max(0, pageCount - selected.size), [pageCount, selected]);

  async function choosePdf(files: FileList | null) {
    const next = files?.[0] || null;
    setFile(next); setPageCount(0); setSelected(new Set()); setJob(null); setError("");
    if (!next) return;
    try {
      const form = new FormData(); form.append("file", next);
      const data = await apiRequest("/api/v1/pdf/page-count", { method: "POST", body: form });
      setPageCount(data.pages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The PDF could not be read.");
    }
  }

  function togglePage(page: number) {
    setSelected((current) => {
      const next = new Set(current);
      next.has(page) ? next.delete(page) : next.add(page);
      return next;
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !selected.size) return;
    setBusy(true); setError(""); setJob(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("pages", Array.from(selected).sort((a, b) => a - b).join(","));
      setJob(await apiRequest("/api/v1/pdf/remove-pages", { method: "POST", body: form }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "PDF editing failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="panel-stack">
      <section className="hero compact"><div><span className="eyebrow">SLC PDF Workflow</span><h2>PDF Page Editor</h2><p>Remove selected pages from a PDF and download a clean revised copy.</p></div><div className="hero-badge">EDIT</div></section>
      <section className="card">
        <div className="section-heading"><span>01</span><div><h3>Choose PDF</h3><p>The original file is never changed.</p></div></div>
        <FileField label="PDF file" accept=".pdf" onChange={choosePdf} help={file?.name || "PDF only"} />
      </section>
      {pageCount > 0 && (
        <section className="card">
          <div className="summary-row"><div><span>Total pages</span><strong>{pageCount}</strong></div><div><span>Selected</span><strong>{selected.size}</strong></div><div><span>Remaining</span><strong>{remaining}</strong></div></div>
          <h3>Select pages to remove</h3>
          <div className="page-grid">{Array.from({ length: pageCount }, (_, index) => index + 1).map((page) => <button type="button" onClick={() => togglePage(page)} className={selected.has(page) ? "page-chip selected" : "page-chip"} key={page}>{page}</button>)}</div>
        </section>
      )}
      {error && <div className="error-box">{error}</div>}
      <button className="button primary large" disabled={!file || !selected.size || selected.size >= pageCount || busy}>{busy ? "Updating PDF…" : "Remove selected pages"}</button>
      {job && <JobResult job={job} title="Updated PDF ready" />}
    </form>
  );
}

export default function Home() {
  const [tab, setTab] = useState<Tab>("formatter");
  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">SLC</span><div><strong>Document Tools</strong><small>South London College</small></div></div>
        <span className="environment">Vercel + Railway</span>
      </header>
      <div className="workspace">
        <nav className="tabs" aria-label="Document tools">
          <button className={tab === "formatter" ? "active" : ""} onClick={() => setTab("formatter")}>Document Formatter</button>
          <button className={tab === "word-pdf" ? "active" : ""} onClick={() => setTab("word-pdf")}>Word → PDF</button>
          <button className={tab === "pdf-editor" ? "active" : ""} onClick={() => setTab("pdf-editor")}>PDF Editor</button>
        </nav>
        {tab === "formatter" && <FormatterPanel />}
        {tab === "word-pdf" && <WordPdfPanel />}
        {tab === "pdf-editor" && <PdfEditorPanel />}
      </div>
    </main>
  );
}
