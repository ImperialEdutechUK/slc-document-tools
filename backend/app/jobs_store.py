"""DynamoDB-backed replacement for the SQLAlchemy session used for ``Job`` rows.

The API only ever loads a job by primary key (``db.get(Job, id)``), adds new or
changed jobs (``db.add``) and commits. This class implements exactly that
surface so the endpoints are unchanged. It is used only when ``JOBS_TABLE`` is
set (AWS deployment); Railway keeps using PostgreSQL through SQLAlchemy.

Records are kept indefinitely, as on Railway. Setting ``JOBS_RETENTION_DAYS``
to a positive number stamps an ``expires_at`` attribute that the table's
DynamoDB TTL setting uses to remove old records.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from .models import Job, utcnow

_STRING_FIELDS = (
    "job_type",
    "status",
    "original_filename",
    "output_filename",
    "output_key",
    "report_key",
    "meta_json",
    "error_message",
)
_DATETIME_FIELDS = ("created_at", "completed_at")


@lru_cache(maxsize=1)
def jobs_table():
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"].strip())


def _retention_days() -> int:
    try:
        return max(0, int(os.getenv("JOBS_RETENTION_DAYS", "0")))
    except ValueError:
        return 0


def _to_item(job: Job) -> dict[str, Any]:
    item: dict[str, Any] = {"id": job.id}
    for field in _STRING_FIELDS:
        value = getattr(job, field)
        if value is not None:
            item[field] = value
    for field in _DATETIME_FIELDS:
        value = getattr(job, field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            item[field] = value.isoformat()
    days = _retention_days()
    if days and job.created_at is not None:
        created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=timezone.utc)
        item["expires_at"] = int((created + timedelta(days=days)).timestamp())
    return item


def _from_item(item: dict[str, Any]) -> Job:
    values: dict[str, Any] = {"id": str(item["id"])}
    for field in _STRING_FIELDS:
        values[field] = item.get(field)
    for field in _DATETIME_FIELDS:
        raw = item.get(field)
        values[field] = datetime.fromisoformat(raw) if raw else None
    return Job(**values)


class DynamoJobSession:
    """Minimal unit-of-work over the DynamoDB jobs table."""

    def __init__(self, table=None) -> None:
        self._table = table if table is not None else jobs_table()
        self._tracked: dict[str, Job] = {}
        self._saved: dict[str, dict[str, Any]] = {}

    def get(self, model, ident):
        if model is not Job:
            raise TypeError("Only Job records are stored in DynamoDB.")
        if ident is None:
            return None
        key = str(ident)
        if key in self._tracked:
            return self._tracked[key]
        item = self._table.get_item(Key={"id": key}, ConsistentRead=True).get("Item")
        if not item:
            return None
        job = _from_item(item)
        self._tracked[key] = job
        self._saved[key] = _to_item(job)
        return job

    def add(self, job: Job) -> None:
        if not isinstance(job, Job):
            raise TypeError("Only Job records are stored in DynamoDB.")
        if job.created_at is None:
            job.created_at = utcnow()
        self._tracked[job.id] = job

    def commit(self) -> None:
        # Write only new or changed jobs, so a request that merely read another
        # job never overwrites a concurrent update to it.
        for key, job in self._tracked.items():
            item = _to_item(job)
            if self._saved.get(key) != item:
                self._table.put_item(Item=item)
                self._saved[key] = item

    def refresh(self, job: Job) -> None:  # objects are already current in memory
        return None

    def flush(self) -> None:
        self.commit()

    def rollback(self) -> None:
        for key in list(self._tracked):
            if key not in self._saved:
                del self._tracked[key]

    def close(self) -> None:
        self._tracked.clear()
        self._saved.clear()
