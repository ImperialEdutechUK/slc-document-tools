from __future__ import annotations

from io import BytesIO
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile

from .zip_utils import safe_zip_member


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
ZIP_MIME = "application/zip"


class WordToPdfError(ValueError):
    pass


def expand_word_inputs(file_items: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    documents: list[tuple[str, bytes]] = []
    for name, payload in file_items:
        suffix = Path(name).suffix.lower()
        if suffix == ".docx":
            documents.append((Path(name).name, payload))
            continue
        if suffix != ".zip":
            raise WordToPdfError(f"Unsupported file: {name}. Upload DOCX files or a ZIP containing DOCX files.")

        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or not safe_zip_member(info.filename):
                        continue
                    if Path(info.filename).suffix.lower() != ".docx":
                        continue
                    documents.append((PurePosixPath(info.filename).name, archive.read(info)))
        except zipfile.BadZipFile as exc:
            raise WordToPdfError(f"{name} is not a readable ZIP file.") from exc

    if not documents:
        raise WordToPdfError("No DOCX files were found in the upload.")
    return documents


def _unique_name(name: str, used: set[str]) -> str:
    candidate = name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used.add(candidate.lower())
    return candidate


def _font_match(family: str) -> str | None:
    """Return the Linux font family LibreOffice/fontconfig will use."""

    fc_match = shutil.which("fc-match")
    if not fc_match:
        return None
    try:
        completed = subprocess.run(
            [fc_match, "-f", "%{family}", family],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    result = (completed.stdout or "").strip()
    return result or None


def _font_environment() -> dict[str, str | None]:
    # These are the Microsoft families used by the formatter template or
    # commonly encountered in source Word documents. The Docker image maps
    # them to metric/similar Linux families where Microsoft fonts cannot be
    # legally bundled with the application.
    return {
        "Garamond": _font_match("Garamond"),
        "Calibri": _font_match("Calibri"),
        "Cambria": _font_match("Cambria"),
        "Arial": _font_match("Arial"),
        "Times New Roman": _font_match("Times New Roman"),
    }


def convert_word_files(file_items: list[tuple[str, bytes]]) -> tuple[str, bytes, str, dict]:
    documents = expand_word_inputs(file_items)
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not libreoffice:
        raise WordToPdfError(
            "LibreOffice is not installed on the processing server. The Railway Docker image must include LibreOffice."
        )

    converted: list[tuple[str, bytes]] = []
    used_names: set[str] = set()

    with tempfile.TemporaryDirectory() as tmp:
        input_dir = Path(tmp) / "input"
        output_dir = Path(tmp) / "output"
        profile_dir = Path(tmp) / "libreoffice-profile"
        input_dir.mkdir()
        output_dir.mkdir()
        profile_dir.mkdir()

        # Each request gets a separate LibreOffice profile. This avoids stale
        # font caches and lock conflicts when Railway processes conversions in
        # parallel.
        profile_arg = f"-env:UserInstallation={profile_dir.resolve().as_uri()}"

        for name, payload in documents:
            safe_docx_name = _unique_name(Path(name).name, set(p.name.lower() for p in input_dir.iterdir()))
            input_path = input_dir / safe_docx_name
            input_path.write_bytes(payload)

            completed = subprocess.run(
                [
                    libreoffice,
                    profile_arg,
                    "--headless",
                    "--convert-to",
                    "pdf:writer_pdf_Export",
                    "--outdir",
                    str(output_dir),
                    str(input_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
            if completed.returncode != 0:
                message = (completed.stderr or completed.stdout or "LibreOffice conversion failed").strip()
                raise WordToPdfError(f"Could not convert {name}: {message}")

            generated = output_dir / (input_path.stem + ".pdf")
            if not generated.exists():
                raise WordToPdfError(f"LibreOffice did not produce a PDF for {name}.")

            pdf_name = _unique_name(Path(name).stem + ".pdf", used_names)
            converted.append((pdf_name, generated.read_bytes()))
            generated.unlink(missing_ok=True)

    details = {
        "input_documents": len(documents),
        "converted_documents": len(converted),
        "font_matches": _font_environment(),
    }
    if len(converted) == 1:
        return converted[0][0], converted[0][1], PDF_MIME, details

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in converted:
            archive.writestr(name, payload)
    return "converted_pdfs.zip", buffer.getvalue(), ZIP_MIME, details
