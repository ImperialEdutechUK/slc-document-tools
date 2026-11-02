"""Durable job output storage (local directory or S3-compatible bucket).

Railway's bucket credentials can be injected using either the application's
S3_* names or the SDK's AWS_* names. All parts of a credential set must come
from the same bucket and Railway environment.
"""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class StorageError(RuntimeError):
    """Storage is unavailable or a write/read could not be completed."""


class StorageNotFoundError(StorageError):
    """A requested stored output does not exist (as opposed to storage outage)."""


def _setting(*names: str) -> str | None:
    """Return the first non-empty configured alias (without surrounding whitespace)."""
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _s3_error(exc: ClientError, operation: str) -> StorageError:
    code = str(exc.response.get("Error", {}).get("Code", "UnknownError"))
    if operation == "read" and code in {"NoSuchKey", "NotFound", "404"}:
        return StorageNotFoundError("The requested stored file no longer exists.")
    if code in {"SignatureDoesNotMatch", "AuthorizationHeaderMalformed", "XAmzContentSHA256Mismatch"}:
        return StorageError(
            "Object storage rejected the request signature (" + code + "). "
            "Check the Railway bucket Credentials tab and backend Variables: "
            "bucket name, endpoint, access key, secret key, region and URL style "
            "must all match the SAME bucket/environment. Then redeploy the backend."
        )
    if code in {"InvalidAccessKeyId", "AccessDenied", "InvalidToken", "ExpiredToken"}:
        return StorageError(
            "Object storage denied the " + operation + " (" + code + "). "
            "Check the current Railway bucket credentials and backend variable references."
        )
    return StorageError(
        "Object storage could not " + operation + " the file (" + code + "). "
        "Check the bucket's status, endpoint and permissions."
    )


class Storage:
    """Use a Railway/AWS-compatible S3 bucket if configured, otherwise local disk."""

    def __init__(self) -> None:
        self.bucket = _setting("S3_BUCKET", "AWS_S3_BUCKET", "AWS_S3_BUCKET_NAME")
        self.local_dir = Path(os.getenv("STORAGE_DIR", "/tmp/slc-document-tools"))
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.s3 = None
        self.addressing_style = "auto"

        if self.bucket:
            endpoint = _setting(
                "S3_ENDPOINT", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL", "AWS_S3_ENDPOINT_URL"
            )
            access_key = _setting("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
            secret_key = _setting("S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
            region = _setting("S3_REGION", "AWS_REGION", "AWS_DEFAULT_REGION") or (
                "auto" if endpoint else "us-east-1"
            )
            style = (
                _setting("S3_ADDRESSING_STYLE", "AWS_S3_URL_STYLE", "AWS_S3_ADDRESSING_STYLE")
                or "auto"
            ).lower()
            if style not in {"auto", "path", "virtual"}:
                raise StorageError(
                    "S3_ADDRESSING_STYLE/AWS_S3_URL_STYLE must be 'auto', 'path' or 'virtual'. "
                    "Use the URL style shown in the Railway bucket Credentials tab."
                )
            self.addressing_style = style
            self.s3 = boto3.client(
                "s3",
                endpoint_url=endpoint.rstrip("/") if endpoint else None,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": style},
                    # Recent botocore versions add trailing checksums to PUTs by
                    # default. Some S3-compatible services reject that framing.
                    # Required checksums remain enabled.
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                    retries={"mode": "standard", "max_attempts": 3},
                ),
            )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        if self.s3:
            try:
                self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
            except ClientError as exc:
                raise _s3_error(exc, "write") from exc
            except BotoCoreError as exc:
                raise StorageError("Unable to write the output to object storage. Check the endpoint and network.") from exc
            return
        path = self.local_dir / key
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise StorageError("Unable to save the output to local storage.") from exc

    def get_bytes(self, key: str) -> bytes:
        if self.s3:
            try:
                response = self.s3.get_object(Bucket=self.bucket, Key=key)
                return response["Body"].read()
            except ClientError as exc:
                raise _s3_error(exc, "read") from exc
            except (BotoCoreError, OSError) as exc:
                raise StorageError("Unable to read from object storage. Check the endpoint and network.") from exc
        path = self.local_dir / key
        if not path.is_file():
            raise StorageNotFoundError("The requested stored file no longer exists.")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError("Unable to read from local storage.") from exc

    def delete_bytes(self, key: str) -> None:
        """Delete a job file; primarily used for the read/write diagnostic."""
        if self.s3:
            try:
                self.s3.delete_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                raise _s3_error(exc, "delete") from exc
            except BotoCoreError as exc:
                raise StorageError("Unable to delete an object from storage.") from exc
            return
        try:
            (self.local_dir / key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Unable to delete a file from local storage.") from exc


storage = Storage()
