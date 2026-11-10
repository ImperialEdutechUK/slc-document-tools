"""AWS-only code paths: DynamoDB job store, S3 body offload, SSM settings.

Uses moto to emulate AWS; skipped automatically when moto is not installed.
"""
from __future__ import annotations

import os
import re

import pytest

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    with moto.mock_aws():
        yield


def test_dynamo_job_session_round_trip(aws, monkeypatch):
    from app import jobs_store
    from app.models import Job

    client = boto3.client("dynamodb")
    client.create_table(
        TableName="jobs",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table = boto3.resource("dynamodb").Table("jobs")

    db = jobs_store.DynamoJobSession(table)
    job = Job(id="abc", job_type="format", status="processing", original_filename="a.docx")
    db.add(job)
    db.commit()
    db.refresh(job)
    assert job.created_at is not None

    job.status = "completed"
    job.output_key = "jobs/abc/out.docx"
    db.add(job)
    db.commit()

    other = jobs_store.DynamoJobSession(table)
    loaded = other.get(Job, "abc")
    assert loaded.status == "completed"
    assert loaded.output_key == "jobs/abc/out.docx"
    assert loaded.created_at == job.created_at
    assert loaded.completed_at is None
    assert other.get(Job, "missing") is None
    assert "expires_at" not in table.get_item(Key={"id": "abc"})["Item"]  # kept indefinitely by default

    # Reading a job and committing a different one must not rewrite the read job.
    reader = jobs_store.DynamoJobSession(table)
    stale = reader.get(Job, "abc")
    other.get(Job, "abc").status = "failed"
    other.commit()
    reader.add(Job(id="new", job_type="format", status="processing"))
    reader.commit()
    assert stale.status == "completed"
    assert table.get_item(Key={"id": "abc"})["Item"]["status"] == "failed"


def test_retention_days_sets_ttl(aws, monkeypatch):
    from app import jobs_store
    from app.models import Job

    monkeypatch.setenv("JOBS_RETENTION_DAYS", "30")
    job = Job(id="x", job_type="format", status="processing")
    jobs_store.DynamoJobSession(table=object()).add(job)
    item = jobs_store._to_item(job)
    assert item["expires_at"] - int(job.created_at.timestamp()) == 30 * 86400


def _offload_app(monkeypatch, threshold: int):
    from app import lambda_io

    lambda_io._s3.cache_clear()
    boto3.client("s3").create_bucket(
        Bucket="data", CreateBucketConfiguration={"LocationConstraint": "eu-west-2"}
    )
    monkeypatch.setenv("BODY_OFFLOAD_BUCKET", "data")
    monkeypatch.setenv("BODY_OFFLOAD_THRESHOLD", str(threshold))

    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        form = await request.form()
        data = await form["document"].read()
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="out.bin"'},
        )

    @app.get("/small")
    def small():
        return {"ok": True}

    assert lambda_io.install_body_offload(app)
    return app


def test_large_upload_and_download_go_through_s3(aws, monkeypatch):
    app = _offload_app(monkeypatch, threshold=1000)
    client = TestClient(app)
    s3 = boto3.client("s3")

    ticket = client.post("/api/v1/transfer/upload-url", json={"size": 5000}).json()
    assert re.match(r"^transfer/uploads/[0-9a-f]{32}$", ticket["key"])
    assert "X-Amz-Signature" in ticket["url"]

    payload = os.urandom(5000)
    boundary = "testboundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"a.docx\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    s3.put_object(Bucket="data", Key=ticket["key"], Body=body)

    response = client.post(
        "/echo",
        headers={
            "X-Body-S3-Key": ticket["key"],
            "X-Body-Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    key = location.split("amazonaws.com/")[1].split("?")[0]
    assert key.startswith("transfer/responses/")
    stored = s3.get_object(Bucket="data", Key=key)
    assert stored["Body"].read() == payload
    assert stored["ContentDisposition"] == 'attachment; filename="out.bin"'
    # the temporary upload copy is removed after use
    assert s3.list_objects_v2(Bucket="data", Prefix="transfer/uploads/").get("KeyCount", 0) == 0

    assert client.get("/small").json() == {"ok": True}


def test_rejects_foreign_upload_keys(aws, monkeypatch):
    client = TestClient(_offload_app(monkeypatch, threshold=1000))
    response = client.post("/echo", headers={"X-Body-S3-Key": "jobs/other/secret.docx"})
    assert response.status_code == 400


def test_offload_disabled_without_bucket(monkeypatch):
    from app import lambda_io

    monkeypatch.delenv("BODY_OFFLOAD_BUCKET", raising=False)
    assert lambda_io.install_body_offload(FastAPI()) is False


def test_settings_loaded_from_ssm_without_overriding(aws, monkeypatch):
    from app import aws_runtime

    ssm = boto3.client("ssm")
    ssm.put_parameter(Name="/slc/backend/PEXELS_API_KEY", Value="from-ssm", Type="SecureString")
    ssm.put_parameter(Name="/slc/backend/PIXABAY_API_KEY", Value="from-ssm", Type="SecureString")
    monkeypatch.setenv("SSM_PARAMETER_PATH", "/slc/backend")
    monkeypatch.setenv("PIXABAY_API_KEY", "already-set")
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("S3_ENV_PREFIX", raising=False)

    aws_runtime.load_runtime_environment()
    assert os.environ["PEXELS_API_KEY"] == "from-ssm"
    assert os.environ["PIXABAY_API_KEY"] == "already-set"
    monkeypatch.delenv("PEXELS_API_KEY")


def test_storage_uses_session_token_for_role_credentials(monkeypatch):
    from app.services import storage as storage_module

    captured = {}
    monkeypatch.setattr(storage_module.boto3, "client", lambda *a, **kw: captured.update(kw) or object())
    for name in ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL", "AWS_S3_ENDPOINT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("S3_BUCKET", "data")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIAROLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
    storage_module.Storage()
    assert captured["aws_session_token"] == "token"

    monkeypatch.setenv("S3_ACCESS_KEY_ID", "railway-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "railway-secret")
    storage_module.Storage()
    assert captured["aws_access_key_id"] == "railway-key"
    assert captured["aws_session_token"] is None
