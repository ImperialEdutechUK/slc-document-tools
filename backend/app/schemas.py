from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    original_filename: str | None = None
    output_filename: str | None = None
    download_url: str | None = None
    report_url: str | None = None
    report: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
