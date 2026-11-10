from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./slc_tools.db")
# AWS deployment: job records live in DynamoDB instead of PostgreSQL.
JOBS_TABLE = os.getenv("JOBS_TABLE", "").strip()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def uses_dynamodb_jobs() -> bool:
    return bool(JOBS_TABLE)


def get_db():
    if JOBS_TABLE:
        from .jobs_store import DynamoJobSession

        dynamo_db = DynamoJobSession()
        try:
            yield dynamo_db
        finally:
            dynamo_db.close()
        return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
