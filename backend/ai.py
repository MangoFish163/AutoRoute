from __future__ import annotations
import asyncio
import json
import uuid
from typing import Any
import httpx
from fastapi import HTTPException
from fastapi import APIRouter
from db import connect, now_iso
from security import redact, reveal_secret

router = APIRouter()


@router.post("/api/ai/analyze")
async def ai_analyze(body: dict[str, Any]):
    def _database_operation_1():
        with connect() as db:
            rows = db.execute("SELECT * FROM deployments").fetchall()
            ev = db.execute(
                "SELECT status,error_kind,status_code,latency_ms,created_at,alias,error_message,deployment_id,deployment_name,attempts FROM request_events ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            cfg_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
        return (rows, ev, cfg_row)

    rows, ev, cfg_row = await asyncio.to_thread(_database_operation_1)
    config = json.loads(cfg_row[0]) if cfg_row else {}

    def percentile(values: list[int], fraction: float) -> int | None:
        if not values:
            return None
        values.sort()
        return values[min(len(values) - 1, int((len(values) - 1) * fraction))]

    attempt_counts: dict[str, dict[str, Any]] = {}
    for event in ev:
        for attempt in json.loads(event["attempts"] or "[]"):
            key = attempt.get("deployment_id") or attempt.get("deployment") or "unrouted"
            counts = attempt_counts.setdefault(
                key,
                {
                    "deployment_id": attempt.get("deployment_id"),
                    "deployment": attempt.get("deployment"),
                    "requests": 0,
                    "successes": 0,
                    "failures": 0,
                    "rate_limits_429": 0,
                    "timeouts": 0,
                    "latencies": [],
                },
            )
            counts["requests"] += 1
            attempt_status = attempt.get("status")
            is_success = isinstance(attempt_status, int) and 200 <= attempt_status < 300
            counts["successes"] += int(is_success)
            counts["failures"] += int(not is_success)
            counts["rate_limits_429"] += int(attempt.get("status") == 429)
            counts["timeouts"] += int(attempt.get("error_kind") == "timeout")
            if is_success and event["latency_ms"] is not None:
                counts["latencies"].append(int(event["latency_ms"]))
    performance = []
    for counts in attempt_counts.values():
        latency_values = counts.pop("latencies")
        performance.append(
            {
                **counts,
                "sampled_requests": counts["requests"],
                "success_rate": round(counts["successes"] / max(counts["requests"], 1), 4),
                "latency_p50_ms": percentile(latency_values[:], 0.5),
                "latency_p95_ms": percentile(latency_values[:], 0.95),
            }
        )
    metrics = {
        "deployment_count": len(rows),
        "sampled_events": len(ev),
        "performance_by_deployment": performance,
        "deployments": [
            {
                "name": r["name"],
                "alias": r["alias"],
                "provider": r["provider"],
                "upstream_model": r["upstream_model"],
                "enabled": bool(r["enabled"]),
                "base_weight": r["base_weight"],
                "auto_weight": r["auto_weight"],
                "weight_locked": bool(r["weight_locked"]),
                "rpm": r["rpm"],
                "max_input_tokens": r["max_input_tokens"],
                "max_output_tokens": r["max_output_tokens"],
                "note": r["note"],
            }
            for r in rows
        ],
        "recent_events": [
            {
                "alias": r["alias"],
                "status": r["status"],
                "error_kind": r["error_kind"],
                "latency_ms": r["latency_ms"],
                "error": r["error_message"],
                "attempts": json.loads(r["attempts"] or "[]"),
            }
            for r in ev[:40]
        ],
    }
    report: dict[str, Any] = {
        "summary": "本地指标快照已生成。",
        "metrics": metrics,
        "note": "未向 AI 发送请求正文或 API Token。",
    }
    if config.get("enabled") and config.get("api_key") and config.get("model"):
        prompt = (
            '你是模型路由服务的只读运维分析器。分析 JSON 指标，仅输出 JSON 对象，格式为 {"summary":"...","findings":[{"alias":"...","severity":"low|medium|high","evidence":"...","recommendation":"..."}],"uncertainties":["..."]}。不得臆造缺失数据，不要提出修改凭据/端点，不执行任何操作。以下为脱敏数据：\n'
            + json.dumps(metrics, ensure_ascii=False)
        )
        try:
            report = await call_analysis_model(config, prompt)
            report["metrics"] = metrics
            report["note"] = "AI 接收的是脱敏汇总指标，不含请求正文或 API Token。"
        except Exception as exc:
            report["analysis_error"] = redact(
                str(exc), [reveal_secret(config["api_key"])] if config.get("api_key") else []
            )
            report["summary"] = "AI 分析调用失败；保留了本地指标快照。"
    audit_id = "audit_" + uuid.uuid4().hex[:12]

    def _database_operation_2():
        with connect() as db:
            db.execute(
                "INSERT INTO ai_audits(id,kind,status,report,changes,created_at) VALUES(?,?,?,?,?,?)",
                (
                    audit_id,
                    "analysis",
                    "complete" if "analysis_error" not in report else "failed",
                    json.dumps(report),
                    "[]",
                    now_iso(),
                ),
            )
        return ()

    await asyncio.to_thread(_database_operation_2)
    return {
        "id": audit_id,
        "kind": "analysis",
        "status": "complete" if "analysis_error" not in report else "failed",
        "report": report,
        "changes": [],
    }


async def call_analysis_model(config: dict[str, Any], prompt: str) -> dict[str, Any]:
    key = reveal_secret(config["api_key"])
    headers = {"content-type": "application/json"}
    if config["provider"] == "anthropic":
        base = config["base_url"].rstrip("/")
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        payload = {
            "model": config["model"],
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        base = config["base_url"].rstrip("/")
        url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
        headers["authorization"] = "Bearer " + key
        payload = {
            "model": config["model"],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    raw = (
        data["content"][0]["text"]
        if config["provider"] == "anthropic"
        else data["choices"][0]["message"]["content"]
    )
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("AI response was not a JSON object")
    return result


@router.post("/api/ai/tune")
async def ai_tune():
    def _database_operation_3():
        with connect() as db:
            report_row = db.execute(
                "SELECT * FROM ai_audits WHERE kind='analysis' AND status='complete' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            cfg_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
            deployments = db.execute("SELECT * FROM deployments").fetchall()
        return (report_row, cfg_row, deployments)

    report_row, cfg_row, deployments = await asyncio.to_thread(_database_operation_3)
    if not report_row:
        raise HTTPException(400, "Generate a successful analysis report first")
    config = json.loads(cfg_row[0]) if cfg_row else {}
    if not config.get("enabled") or not config.get("api_key") or (not config.get("model")):
        raise HTTPException(400, "Configure and enable an AI analysis model first")
    live = [
        {
            "id": r["id"],
            "alias": r["alias"],
            "name": r["name"],
            "base_weight": r["base_weight"],
            "auto_weight": r["auto_weight"],
            "weight_locked": bool(r["weight_locked"]),
            "enabled": bool(r["enabled"]),
        }
        for r in deployments
    ]
    prompt = (
        '基于综合分析报告和当前权重提出必要的自动权重建议。只输出 JSON：{"changes":[{"deployment_id":"...","proposed_weight":1.0,"reason":"..."}],"summary":"..."}。proposed_weight 是 auto_weight 因子，范围 0.1 到 3.0；单次相对变化最多 25%。不需要变化则 changes 为空。不要调整锁定或停用项；deployment_id 必须来自部署清单。\n报告：'
        + report_row["report"]
        + "\n当前部署："
        + json.dumps(live, ensure_ascii=False)
    )
    audit_id = "audit_" + uuid.uuid4().hex[:12]
    try:
        recommendation = await call_analysis_model(config, prompt)
        changes = recommendation.get("changes")
        if not isinstance(changes, list) or not isinstance(recommendation.get("summary"), str):
            raise ValueError("changes must be an array")
        by_id = {d["id"]: d for d in live}
        accepted = []
        changed_ids = set()
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("each change must be an object")
            dep_id = change.get("deployment_id")
            if dep_id in changed_ids:
                raise ValueError("recommendation contains a duplicate deployment")
            changed_ids.add(dep_id)
            dep = by_id.get(dep_id)
            proposed = change.get("proposed_weight")
            reason = change.get("reason")
            if not dep or not dep["enabled"] or dep["weight_locked"]:
                raise ValueError(
                    "recommendation contains an unknown, disabled, or locked deployment"
                )
            if (
                isinstance(proposed, bool)
                or not isinstance(proposed, (int, float))
                or (not 0.1 <= proposed <= 3)
            ):
                raise ValueError("recommended weight is outside the allowed range")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("every weight change must include a reason")
            if (
                dep["auto_weight"]
                and abs(proposed - dep["auto_weight"]) / dep["auto_weight"] > 0.250001
            ):
                raise ValueError("recommended change exceeds the 25% per-run limit")
            if abs(float(proposed) - dep["auto_weight"]) > 0.0001:
                accepted.append(
                    {
                        "deployment_id": dep_id,
                        "name": dep["name"],
                        "before": dep["auto_weight"],
                        "after": float(proposed),
                        "reason": reason.strip(),
                    }
                )

        def _database_operation_4():
            with connect() as db:
                for change in accepted:
                    if abs(change["after"] - change["before"]) > 0.0001:
                        db.execute(
                            "UPDATE deployments SET auto_weight=? WHERE id=? AND weight_locked=0",
                            (change["after"], change["deployment_id"]),
                        )
                        db.execute(
                            "INSERT INTO weight_events VALUES(?,?,?,?,?,?,?)",
                            (
                                str(uuid.uuid4()),
                                change["deployment_id"],
                                "ai",
                                change["before"],
                                change["after"],
                                change["reason"],
                                now_iso(),
                            ),
                        )
                db.execute(
                    "INSERT INTO ai_audits(id,kind,status,report,changes,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        audit_id,
                        "tune",
                        "applied" if accepted else "no_change",
                        json.dumps(recommendation),
                        json.dumps(accepted),
                        now_iso(),
                    ),
                )
            return ()

        await asyncio.to_thread(_database_operation_4)
        return {
            "id": audit_id,
            "kind": "tune",
            "status": "applied" if accepted else "no_change",
            "report": recommendation,
            "changes": accepted,
        }
    except Exception as exc:
        rejection_message = redact(
            str(exc), [reveal_secret(config["api_key"])] if config.get("api_key") else []
        )

        def _database_operation_5():
            with connect() as db:
                db.execute(
                    "INSERT INTO ai_audits(id,kind,status,report,changes,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        audit_id,
                        "tune",
                        "rejected",
                        json.dumps({"error": rejection_message}),
                        "[]",
                        now_iso(),
                    ),
                )
            return ()

        await asyncio.to_thread(_database_operation_5)
        raise HTTPException(
            422,
            "AI tuning result rejected: "
            + redact(str(exc), [reveal_secret(config["api_key"])] if config.get("api_key") else []),
        )
