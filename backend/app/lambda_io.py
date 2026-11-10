"""Move large request/response bodies through S3 when running behind a Lambda Function URL.

Lambda Function URLs accept and return at most ~6 MB per invocation (binary
bodies are base64-encoded, so ~4.5 MB of real data). Railway had no such limit.

* Uploads: the frontend asks ``POST /api/v1/transfer/upload-url`` for a
  pre-signed S3 PUT URL, uploads its exact multipart body there, then sends the
  normal API request with no body and an ``X-Body-S3-Key`` header. This
  middleware restores the original body before FastAPI sees it, so no endpoint
  changes.
* Downloads: any successful response larger than the threshold is written to
  S3 and replaced by a ``303 See Other`` redirect to a short-lived pre-signed
  URL. Browsers and ``fetch`` follow it transparently.

Transfer objects live under ``transfer/`` and are deleted by an S3 lifecycle
rule after one day; they are transport copies only, not application data.

Enabled only when ``BODY_OFFLOAD_BUCKET`` is set, so Railway is unaffected.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

UPLOAD_PREFIX = "transfer/uploads/"
RESPONSE_PREFIX = "transfer/responses/"
DEFAULT_THRESHOLD = 4_000_000
URL_EXPIRY_SECONDS = 3600
_UPLOAD_KEY = re.compile(r"^transfer/uploads/[0-9a-f]{32}$")


def _bucket() -> str:
    return os.getenv("BODY_OFFLOAD_BUCKET", "").strip()


def _threshold() -> int:
    try:
        return int(os.getenv("BODY_OFFLOAD_THRESHOLD", str(DEFAULT_THRESHOLD)))
    except ValueError:
        return DEFAULT_THRESHOLD


@lru_cache(maxsize=1)
def _s3():
    import boto3
    from botocore.config import Config

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-2"
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


class UploadUrlRequest(BaseModel):
    size: int | None = None


router = APIRouter()


@router.post("/api/v1/transfer/upload-url")
def create_upload_url(request: UploadUrlRequest) -> dict:
    if request.size is not None and request.size > 2 * 1024 ** 3:
        raise HTTPException(status_code=413, detail="The upload is larger than 2 GB.")
    key = f"{UPLOAD_PREFIX}{uuid4().hex}"
    url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=900,
    )
    return {"key": key, "url": url}


_warmed = False


@router.get("/internal/warm")
def warm_instance() -> dict:
    """Called by the keep-warm schedule. The first call on a new Lambda instance
    runs one tiny DOCX->PDF conversion so LibreOffice's files are loaded from the
    container image before a user needs them (~25 s once, then ~2 s per job)."""
    global _warmed
    if not _warmed:
        from io import BytesIO

        from docx import Document

        from .services.word_to_pdf import convert_word_files

        buffer = BytesIO()
        document = Document()
        document.add_paragraph("warm-up")
        document.save(buffer)
        convert_word_files([("warm-up.docx", buffer.getvalue())])
        _warmed = True
        return {"status": "warmed"}
    return {"status": "warm"}


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class BodyOffloadMiddleware:
    def __init__(self, app, bucket: str, threshold: int) -> None:
        self.app = app
        self.bucket = bucket
        self.threshold = threshold

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        body_key = _header(headers, b"x-body-s3-key")
        if body_key:
            if not _UPLOAD_KEY.match(body_key):
                await self._send_error(send, 400, b'{"detail":"Invalid upload reference."}')
                return
            try:
                body = await run_in_threadpool(self._take_upload, body_key)
            except Exception:
                await self._send_error(
                    send, 400, b'{"detail":"The uploaded file has expired or could not be read. Please try again."}'
                )
                return
            content_type = _header(headers, b"x-body-content-type") or "application/octet-stream"
            headers = [
                (k, v)
                for k, v in headers
                if k.lower() not in {b"content-type", b"content-length", b"x-body-s3-key", b"x-body-content-type", b"transfer-encoding"}
            ]
            headers += [(b"content-type", content_type.encode("latin-1")), (b"content-length", str(len(body)).encode())]
            scope = dict(scope, headers=headers)
            receive = self._replay(body, receive)

        start: dict | None = None
        chunks: list[bytes] = []

        async def capture(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            payload = b"".join(chunks)
            assert start is not None
            if 200 <= start["status"] < 300 and len(payload) > self.threshold:
                location = await run_in_threadpool(self._store_response, start, payload)
                passthrough = [
                    (k, v) for k, v in start.get("headers", []) if k.lower().startswith(b"access-control-") or k.lower() == b"vary"
                ]
                await send(
                    {
                        "type": "http.response.start",
                        "status": 303,
                        "headers": passthrough
                        + [(b"location", location.encode("latin-1")), (b"content-length", b"0"), (b"cache-control", b"no-store")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
            await send(start)
            await send({"type": "http.response.body", "body": payload})

        await self.app(scope, receive, capture)

    @staticmethod
    def _replay(body: bytes, original_receive):
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await original_receive()

        return receive

    @staticmethod
    async def _send_error(send, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _take_upload(self, key: str) -> bytes:
        s3 = _s3()
        body = s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        try:
            s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass  # the one-day lifecycle rule removes it anyway
        return body

    def _store_response(self, start: dict, payload: bytes) -> str:
        headers = start.get("headers", [])
        extra: dict[str, str] = {}
        content_type = _header(headers, b"content-type")
        disposition = _header(headers, b"content-disposition")
        if content_type:
            extra["ContentType"] = content_type
        if disposition:
            extra["ContentDisposition"] = disposition
        key = f"{RESPONSE_PREFIX}{uuid4().hex}"
        s3 = _s3()
        s3.put_object(Bucket=self.bucket, Key=key, Body=payload, **extra)
        return s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=URL_EXPIRY_SECONDS
        )


def install_body_offload(app: FastAPI) -> bool:
    """Enable S3 body offloading when configured. Returns True when active."""
    bucket = _bucket()
    if not bucket:
        return False
    app.include_router(router)
    app.add_middleware(BodyOffloadMiddleware, bucket=bucket, threshold=_threshold())
    return True
