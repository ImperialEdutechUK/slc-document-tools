"""Load backend settings from AWS when running on Lambda.

Railway injects configuration as environment variables. On AWS the same
variables are kept in SSM Parameter Store (SecureString, free standard tier)
under ``SSM_PARAMETER_PATH``; values larger than the 4 KB SSM limit (for
example a captured Freepik browser session) are kept as private objects under
``S3_ENV_PREFIX`` in ``S3_BUCKET``. The last path segment of each parameter or
object key is the environment variable name.

Nothing happens unless ``SSM_PARAMETER_PATH`` or ``S3_ENV_PREFIX`` is set, so
Railway and local development are unaffected. Variables already present in the
environment always win.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _set_default(name: str, value: str) -> bool:
    if not name or not name.replace("_", "").isalnum():
        return False
    if os.environ.get(name, "").strip():
        return False
    os.environ[name] = value
    return True


def _load_ssm(path: str) -> int:
    import boto3

    client = boto3.client("ssm")
    loaded = 0
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=path, Recursive=True, WithDecryption=True):
        for parameter in page.get("Parameters", []):
            name = parameter["Name"].rstrip("/").rsplit("/", 1)[-1]
            loaded += _set_default(name, parameter.get("Value", ""))
    return loaded


def _load_s3(bucket: str, prefix: str) -> int:
    import boto3

    client = boto3.client("s3")
    loaded = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            name = item["Key"][len(prefix):].strip("/")
            if not name or "/" in name:
                continue
            body = client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            loaded += _set_default(name, body.decode("utf-8").strip())
    return loaded


def load_runtime_environment() -> None:
    ssm_path = os.getenv("SSM_PARAMETER_PATH", "").strip()
    s3_prefix = os.getenv("S3_ENV_PREFIX", "").strip()
    bucket = os.getenv("S3_BUCKET", "").strip()

    if ssm_path:
        try:
            count = _load_ssm(ssm_path)
            logger.info("Loaded %d setting(s) from SSM path %s", count, ssm_path)
        except Exception:  # pragma: no cover - surfaced in CloudWatch logs
            logger.exception("Could not load settings from SSM path %s", ssm_path)

    if s3_prefix and bucket:
        try:
            count = _load_s3(bucket, s3_prefix.rstrip("/") + "/")
            logger.info("Loaded %d large setting(s) from s3://%s/%s", count, bucket, s3_prefix)
        except Exception:  # pragma: no cover - surfaced in CloudWatch logs
            logger.exception("Could not load large settings from S3 prefix %s", s3_prefix)
