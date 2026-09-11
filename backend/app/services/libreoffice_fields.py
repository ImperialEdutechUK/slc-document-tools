from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4
import zipfile
from io import BytesIO


class LibreOfficeFieldError(RuntimeError):
    pass


def _validate_docx(payload: bytes) -> None:
    try:
        with zipfile.ZipFile(BytesIO(payload), "r") as archive:
            if "word/document.xml" not in archive.namelist():
                raise LibreOfficeFieldError("LibreOffice returned an invalid DOCX file.")
    except zipfile.BadZipFile as exc:
        raise LibreOfficeFieldError("LibreOffice returned an unreadable DOCX file.") from exc


def update_docx_fields(docx_bytes: bytes) -> tuple[bytes, dict]:
    """Refresh live Word indexes/fields using LibreOffice's UNO API.

    The normal ``--convert-to`` command does not reliably refresh TOC page
    numbers before rendering. UNO's ``DocumentIndex.update()`` does, so this
    function opens the edited DOCX invisibly, updates its live TOC/index and
    saves the DOCX before the application's normal preview/PDF conversion.
    """

    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    system_python = Path("/usr/bin/python3")
    helper = Path(__file__).with_name("uno_update_fields.py")

    if not libreoffice:
        raise LibreOfficeFieldError("LibreOffice is not installed on the processing server.")
    if not system_python.exists():
        raise LibreOfficeFieldError("System Python required for LibreOffice UNO is unavailable.")
    if not helper.exists():
        raise LibreOfficeFieldError("The LibreOffice field-refresh helper is missing.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        document_path = tmp_path / "document.docx"
        profile_dir = tmp_path / "libreoffice-profile"
        profile_dir.mkdir()
        document_path.write_bytes(docx_bytes)

        pipe_name = f"slc_fields_{uuid4().hex}"
        profile_arg = f"-env:UserInstallation={profile_dir.resolve().as_uri()}"
        accept_arg = f"--accept=pipe,name={pipe_name};urp;StarOffice.ServiceManager"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env.setdefault("TERM", "dumb")

        server = subprocess.Popen(
            [
                libreoffice,
                profile_arg,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--norestore",
                accept_arg,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            completed = subprocess.run(
                [str(system_python), str(helper), str(document_path), pipe_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
                env=env,
            )
            if completed.returncode != 0:
                message = (completed.stderr or completed.stdout or "UNO field refresh failed").strip()
                raise LibreOfficeFieldError(message)

            try:
                helper_details = json.loads((completed.stdout or "{}").strip() or "{}")
            except json.JSONDecodeError:
                helper_details = {}

            updated = document_path.read_bytes()
            _validate_docx(updated)
            return updated, {
                "exact_layout_refresh": True,
                "indexes_updated": int(helper_details.get("indexes_updated", 0)),
                "fields_refreshed": bool(helper_details.get("fields_refreshed", False)),
            }
        except subprocess.TimeoutExpired as exc:
            raise LibreOfficeFieldError("Timed out while refreshing the Table of Contents.") from exc
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
