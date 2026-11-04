from __future__ import annotations
import asyncio
import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from fastapi import HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi import APIRouter

from event_bus import bus
from db import connect, now_iso

router = APIRouter()


def token_usage(usage: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    """Normalize totals only; retain provider extensions in the original usage object."""

    def count(value):
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    usage = usage or {}
    input_tokens = count(usage.get("prompt_tokens", usage.get("input_tokens")))
    output_tokens = count(usage.get("completion_tokens", usage.get("output_tokens")))
    total = count(usage.get("total_tokens"))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return input_tokens, output_tokens, total


def merge_usage(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Stream usage values are cumulative snapshots, not increments."""
    result = dict(previous)
    for key, value in incoming.items():
        result[key] = (
            merge_usage(result.get(key, {}), value)
            if isinstance(value, dict) and isinstance(result.get(key, {}), dict)
            else value
        )
    return result


class StreamUsage:
    def __init__(self):
        self.pending = b""
        self.usage = None
        self.events = []

    def feed(self, chunk: bytes, final: bool = False):
        self.pending += chunk
        lines = self.pending.split(b"\n")
        self.pending = b"" if final else lines.pop()
        for line in lines:
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
                usage = event.get("usage")
                if not isinstance(usage, dict) and isinstance(event.get("message"), dict):
                    usage = event["message"].get("usage")
                if not isinstance(usage, dict) and isinstance(event.get("response"), dict):
                    usage = event["response"].get("usage")
                if isinstance(usage, dict):
                    self.events.append(usage)
                    self.usage = merge_usage(self.usage or {}, usage)
            except (ValueError, TypeError, AttributeError):
                continue


def supplier_snapshot(deployment: dict[str, Any]) -> dict[str, str | None]:
    if deployment.get("supplier_name"):
        return {
            "supplier_id": deployment.get("supplier_id") or None,
            "supplier_name": deployment["supplier_name"],
        }
    host = urlsplit(deployment.get("base_url", "")).hostname
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='provider_presets'").fetchone()
    presets = json.loads(row[0]) if row else {}
    matches = [
        (key, preset)
        for key, preset in presets.items()
        if host and urlsplit(preset.get("base_url", "")).hostname == host
    ]
    if len(matches) == 1:
        key, preset = matches[0]
        return {"supplier_id": key, "supplier_name": preset.get("name") or key}
    builtin = {
        "api.deepseek.com": ("deepseek", "DeepSeek"),
        "api.openai.com": ("openai", "OpenAI"),
        "api.anthropic.com": ("anthropic", "Anthropic"),
        "apihub.agnes-ai.com": ("agnes", "Agnes"),
        "openrouter.ai": ("openrouter", "OpenRouter"),
    }
    key, name = builtin.get(host, (host, host))
    return {"supplier_id": key, "supplier_name": name}


def save_event(
    request_id: str,
    protocol: str,
    alias: str,
    deployment: dict[str, Any] | None,
    status: str,
    status_code: int | None,
    latency_ms: int,
    error_kind: str | None,
    error_message: str | None,
    attempts: list[dict[str, Any]],
    total_tokens: int | None = None,
    usage: dict[str, Any] | None = None,
    usage_events: list[dict[str, Any]] | None = None,
) -> None:
    usage = usage if isinstance(usage, dict) else None
    input_tokens, output_tokens, normalized_total = token_usage(usage)
    if normalized_total is not None:
        total_tokens = normalized_total
    supplier = supplier_snapshot(deployment) if deployment else (attempts[-1] if attempts else {})
    created_at = now_iso()
    with connect() as db:
        db.execute(
            "INSERT INTO request_events(id,request_id,protocol,alias,deployment_id,deployment_name,status,status_code,latency_ms,error_kind,error_message,attempts,total_tokens,input_tokens,output_tokens,usage,usage_events,supplier_id,supplier_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                request_id,
                protocol,
                alias,
                deployment["id"] if deployment else None,
                deployment["name"] if deployment else None,
                status,
                status_code,
                latency_ms,
                error_kind,
                (error_message or "")[:500],
                json.dumps(attempts),
                total_tokens,
                input_tokens,
                output_tokens,
                json.dumps(usage, ensure_ascii=False) if usage is not None else None,
                json.dumps(usage_events, ensure_ascii=False) if usage_events is not None else None,
                supplier.get("supplier_id"),
                supplier.get("supplier_name"),
                created_at,
            ),
        )
    bus.publish(created_at)


def decode_events(rows):
    result = []
    with connect() as db:
        deployments = {r["id"]: dict(r) for r in db.execute("SELECT * FROM deployments")}
    inferred = {}
    for row in rows:
        event = dict(row)
        for key, default in (("attempts", []), ("usage", None), ("usage_events", None)):
            event[key] = json.loads(event[key]) if event[key] else default
        event["supplier_source"] = "snapshot" if event["supplier_name"] else None
        dep_id = event["deployment_id"]
        if not event["supplier_name"] and dep_id in deployments:
            if dep_id not in inferred:
                inferred[dep_id] = supplier_snapshot(deployments[dep_id])
            event.update(inferred[dep_id])
            event["supplier_source"] = "current_deployment"
        result.append(event)
    return result


def event_filter(start, end, q="", status="", alias=""):
    if end < start:
        raise HTTPException(400, "Invalid time range")
    try:
        bounds = [datetime.fromtimestamp(v / 1000, timezone.utc).isoformat() for v in (start, end)]
    except (ValueError, OverflowError, OSError):
        raise HTTPException(400, "Invalid time range")
    where = "created_at >= ? AND created_at <= ?"
    if q:
        where += " AND instr(lower(coalesce(request_id,'')||' '||alias||' '||coalesce(deployment_name,'')||' '||coalesce(supplier_name,'')||' '||coalesce(error_message,'')||' '||coalesce(error_kind,'')),lower(?)) > 0"
        bounds.append(q)
    for field, value in (("status", status), ("alias", alias)):
        if value:
            where += f" AND {field}=?"
            bounds.append(value)
    return where, bounds


@router.get("/api/events")
def events(limit: int = 100):
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM request_events ORDER BY created_at DESC,id DESC LIMIT ?",
            (min(max(limit, 1), 500),),
        ).fetchall()
    return decode_events(rows)


@router.get("/api/events/page")
def events_page(
    start: int = Query(..., ge=0),
    end: int = Query(..., ge=0),
    q: str = "",
    status: str = "",
    alias: str = "",
    cursor: str = "",
    limit: int = Query(25, ge=1, le=100),
):
    where, args = event_filter(start, end, q, status, alias)
    if cursor:
        try:
            stamp, event_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if not isinstance(stamp, str) or not isinstance(event_id, str):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(400, "Invalid cursor")
        where += " AND (created_at,id) < (?,?)"
        args.extend((stamp, event_id))
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM request_events WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ?",
            (*args, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps([rows[-1]["created_at"], rows[-1]["id"]]).encode()
        ).decode()
        if more
        else None
    )
    return {"items": decode_events(rows), "has_more": more, "next_cursor": next_cursor}


def range_stats(start, end, q="", status="", alias=""):
    where, args = event_filter(start, end, q, status, alias)
    with connect() as db:
        row = dict(
            db.execute(
                f"SELECT COUNT(*) total, coalesce(SUM(status!='success'),0) failed, coalesce(SUM(total_tokens),0) tokens, COUNT(total_tokens) tokenEvents, coalesce(AVG(CASE WHEN latency_ms>=0 THEN latency_ms END),0) avg FROM request_events WHERE {where}",
                args,
            ).fetchone()
        )
        count = db.execute(
            f"SELECT COUNT(*) FROM request_events WHERE {where} AND latency_ms>=0", args
        ).fetchone()[0]
        p95 = db.execute(
            f"SELECT latency_ms FROM request_events WHERE {where} AND latency_ms>=0 ORDER BY latency_ms LIMIT 1 OFFSET ?",
            (*args, max(0, __import__("math").ceil(count * 0.95) - 1)),
        ).fetchone()
        plot_start = start // 3600000 * 3600000
        plot_end = max(plot_start + 3600000, ((end + 3599999) // 3600000) * 3600000)
        step = (plot_end - plot_start) / 10
        buckets = [{"success": 0, "error": 0} for _ in range(10)]
        grouped = db.execute(
            f"SELECT min(9,max(0,CAST(((julianday(created_at)-2440587.5)*86400000-?)/? AS INTEGER))) bucket,status,COUNT(*) count FROM request_events WHERE {where} GROUP BY bucket,status",
            (plot_start, step, *args),
        ).fetchall()
    for r in grouped:
        buckets[r["bucket"]]["success" if r["status"] == "success" else "error"] += r["count"]
    row.update(
        rate=(row["total"] - row["failed"]) / max(row["total"], 1) * 100,
        p95=p95[0] if p95 else 0,
        buckets=buckets,
    )
    return row


@router.get("/api/events/stats")
def events_stats(
    start: int = Query(..., ge=0),
    end: int = Query(..., ge=0),
    q: str = "",
    status: str = "",
    alias: str = "",
):
    return range_stats(start, end, q, status, alias)


@router.get("/api/overview")
def overview(start: int = Query(..., ge=0), end: int = Query(..., ge=0)):
    where, args = event_filter(start, end)
    with connect() as db:

        def aggregate(field):
            return [
                dict(r)
                for r in db.execute(
                    f"SELECT coalesce(nullif({field},''),deployment_name,'未知') name,COUNT(*) count,coalesce(SUM(total_tokens),0) tokens,coalesce(SUM(CASE WHEN latency_ms>=0 THEN latency_ms END),0) latency,COUNT(CASE WHEN latency_ms>=0 THEN latency_ms END) latencyCount FROM request_events WHERE {where} GROUP BY name",
                    args,
                )
            ]

        providers, models = aggregate("supplier_name"), aggregate("alias")
    return {"providers": providers, "models": models, "stats": range_stats(start, end)}


@router.get("/api/events/stream")
async def stream_events(
    start: int = Query(..., ge=0), end: int = Query(..., ge=0), live: bool = False
):
    event_filter(start, end)
    if end - start > 7 * 86400000:
        raise HTTPException(400, "Invalid log range")

    async def updates():
        async with bus.subscribe() as queue:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    low, high = await asyncio.wait_for(queue.get(), timeout=15)
                    first = datetime.fromisoformat(low).timestamp() * 1000
                    last = datetime.fromisoformat(high).timestamp() * 1000
                    if last >= start and (live or first <= end):
                        yield "event: new\ndata: {}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"

    return StreamingResponse(
        updates(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/summary")
def summary():
    from datetime import timedelta
    from rules import safe_deployments

    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with connect() as db:
        deployments = db.execute(
            "SELECT COUNT(*) total, SUM(enabled) enabled FROM deployments"
        ).fetchone()
        counts = db.execute(
            "SELECT COUNT(*) total, SUM(status='success') success, SUM(status='error') errors FROM request_events WHERE created_at>=?",
            (cutoff,),
        ).fetchone()
        latest = db.execute("SELECT MAX(created_at) FROM request_events").fetchone()[0]
    return {
        "deployments": dict(deployments),
        "requests_24h": dict(counts),
        "latest_request": latest,
        "cooldowns": sum(d["health"] == "cooldown" for d in safe_deployments()),
        "success_rate": (counts["success"] or 0) / max(counts["total"], 1),
    }
