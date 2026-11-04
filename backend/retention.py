import asyncio
import json
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Literal
from db import connect

router = APIRouter()


class RetentionInput(BaseModel):
    retention_days: Literal[0, 30, 90, 180] = 0


@router.get("/api/settings/retention")
def retention_settings():
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()
    return {"retention_days": int(json.loads(row[0])) if row else 0}


@router.put("/api/settings/retention")
def save_retention(body: RetentionInput):
    with connect() as db:
        db.execute(
            "INSERT INTO settings VALUES('retention_days',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(body.retention_days),),
        )
    return body.model_dump()


def cleanup_batch():
    days = retention_settings()["retention_days"]
    if not days:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    deleted = 0
    for table in ("request_events", "weight_events", "ai_audits"):
        with connect() as db:
            deleted += db.execute(
                f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE created_at < ? LIMIT 500)",
                (cutoff,),
            ).rowcount
    return deleted


async def maintenance():
    import logging

    while True:
        try:
            while await asyncio.to_thread(cleanup_batch):
                await asyncio.sleep(0)
        except Exception:
            logging.getLogger(__name__).error("History cleanup failed; retrying next day")
        await asyncio.sleep(86400)
