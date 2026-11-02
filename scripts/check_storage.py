"""Verify that the deployed Railway backend can PUT, GET and DELETE one small file.

Run inside the backend environment with credentials already configured:
    python scripts/check_storage.py
This never prints credentials or the full endpoint URL.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.services.storage import Storage, StorageError  # noqa: E402


def main() -> int:
    key = f"diagnostics/storage-check-{uuid4().hex}.txt"
    payload = b"SLC storage health check\n"
    try:
        storage = Storage()
        if not storage.s3:
            print("LOCAL storage is active. Railway production requires valid S3 bucket settings.")
            return 1
        print(f"Checking S3 read/write/delete (URL style: {storage.addressing_style}) ...")
        uploaded = False
        try:
            storage.put_bytes(key, payload, "text/plain")
            uploaded = True
            if storage.get_bytes(key) != payload:
                print("FAIL: Downloaded data did not match the uploaded bytes.")
                return 1
        finally:
            if uploaded:
                storage.delete_bytes(key)
        print("PASS: Object storage upload, download and delete all succeeded.")
        return 0
    except StorageError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
