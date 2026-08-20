from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.config import Config


class StorageError(RuntimeError):
    pass


class Storage:
    """Small storage abstraction.

    Locally it writes to STORAGE_DIR. On Railway, set S3_BUCKET plus the usual
    S3 endpoint/credential variables exposed for a Railway storage bucket.
    """

    def __init__(self) -> None:
        self.bucket = os.getenv("S3_BUCKET") or os.getenv("AWS_S3_BUCKET")
        self.local_dir = Path(os.getenv("STORAGE_DIR", "/tmp/slc-document-tools"))
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.s3 = None

        if self.bucket:
            endpoint = os.getenv("S3_ENDPOINT") or os.getenv("AWS_ENDPOINT_URL_S3")
            access_key = os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
            secret_key = os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
            region = os.getenv("S3_REGION") or os.getenv("AWS_REGION") or "auto"
            self.s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version="s3v4"),
            )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        if self.s3:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
            return
        path = self.local_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        if self.s3:
            try:
                response = self.s3.get_object(Bucket=self.bucket, Key=key)
                return response["Body"].read()
            except Exception as exc:
                raise StorageError(f"Unable to read stored file: {key}") from exc
        path = self.local_dir / key
        if not path.exists():
            raise StorageError(f"Stored file not found: {key}")
        return path.read_bytes()


storage = Storage()
