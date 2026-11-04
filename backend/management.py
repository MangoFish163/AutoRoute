from __future__ import annotations
import asyncio
import json
import os
import uuid
from typing import Any
import httpx
from fastapi import HTTPException, Query, Request
from responses_adapter import uses_responses
from fastapi import APIRouter
from db import APP_DIR, connect, now_iso
from schemas import ConnectionInput, RuleInput, DeploymentInput, NetworkConfig
from security import redact, client_key, protect_secret, reveal_secret
from rules import safe_deployments, get_rule_settings, safe_deployment
from upstream import proxy_options

router = APIRouter()


@router.get("/health")
def health(request: Request):
    return {
        "status": "ok",
        "version": request.app.version,
        "usage_schema": 2,
        "application": "com.localmodelrouter.desktop",
        "api_version": 1,
        "instance": os.environ.get("LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE", ""),
    }


@router.get("/api/bootstrap")
def bootstrap(request: Request):
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
    return {
        "base_url": f"http://127.0.0.1:{getattr(request.app.state, 'listener_port', (request.scope.get('server') or ('127.0.0.1', 80))[1])}/v1",
        "port_changed_from": getattr(request.app.state, "port_changed_from", None),
        "client_key": client_key(),
        "deployment_count": count,
        "data_dir": str(APP_DIR.resolve()),
    }


@router.get("/api/network")
def network_config():
    from network import configured_port

    return {"port": configured_port()}


@router.put("/api/network")
def update_network(body: NetworkConfig):
    from network import save_port

    save_port(body.port)
    return {"port": body.port}


@router.get("/api/network/recommendations")
def network_recommendations():
    from network import recommended_ports

    return {"ports": recommended_ports()}


@router.get("/api/deployments")
def list_deployments():
    with connect() as db:
        rows = db.execute("SELECT * FROM deployments ORDER BY alias,name").fetchall()
    return safe_deployments(rows)


@router.post("/api/deployments/test-connection")
async def test_deployment_connection(body: ConnectionInput):
    body = body.model_dump()
    provider = body.get("provider", "openai")
    base_url = str(body.get("base_url", "")).rstrip("/")
    token = str(body.get("api_key", ""))
    if token == "__saved__":
        dep_id = body.get("deployment_id")

        def _database_operation_1():
            with connect() as db:
                row = db.execute(
                    "SELECT api_keys FROM deployments WHERE id=?", (dep_id,)
                ).fetchone()
            return (row,)

        (row,) = await asyncio.to_thread(_database_operation_1)
        keys = [reveal_secret(key) for key in json.loads(row["api_keys"])] if row else []
        token = keys[0] if keys else ""
    if not base_url or not token:
        raise HTTPException(400, "请填写 API Base URL 和 API Token")
    if provider not in ("openai", "anthropic", "responses"):
        raise HTTPException(400, "不支持的上游协议")
    headers = {"Authorization": f"Bearer {token}"}
    if uses_responses({"provider": provider, "base_url": base_url}):
        base_url = base_url.removesuffix("/responses")
    if provider == "anthropic":
        headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
        urls = [base_url + ("/models" if base_url.endswith("/v1") else "/v1/models")]
    else:
        urls = [base_url + ("/models" if base_url.endswith("/v1") else "/models")]
        fallback = base_url + ("/v1/models" if not base_url.endswith("/v1") else "/models")
        if fallback != urls[0]:
            urls.append(fallback)
    models_url = str(body.get("models_url", "")).strip()
    if models_url:
        urls = [models_url]
    try:
        async with httpx.AsyncClient(
            timeout=min(body.get("timeout_seconds", 30), 60), **proxy_options(body)
        ) as client:
            response = await client.get(urls[0], headers=headers)
            if response.status_code == 404 and len(urls) > 1:
                response = await client.get(urls[1], headers=headers)
        if response.is_success:
            payload = response.json()
            models = [
                str(item.get("id") or item.get("name"))
                for item in payload.get("data", [])
                if item.get("id") or item.get("name")
            ]
            return {
                "ok": True,
                "message": "连接成功，已获取模型列表"
                if models
                else "连接成功，但上游未返回模型列表",
                "models": models,
            }
        raise HTTPException(
            response.status_code,
            redact(f"上游返回 HTTP {response.status_code}：{response.text[:240]}", [token]),
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise HTTPException(502, redact(f"连接上游失败：{str(exc)[:200]}", [token])) from exc


@router.post("/api/deployments")
def create_deployment(body: DeploymentInput):
    if body.provider not in ("openai", "anthropic", "responses"):
        raise HTTPException(400, "Provider must be openai, anthropic or responses")
    dep_id = "dep_" + uuid.uuid4().hex[:12]
    values = body.model_dump()
    values["api_keys"] = json.dumps(
        [protect_secret(key) for key in values["api_keys"] if key.strip()]
    )
    values["enabled"] = int(values["enabled"])
    values["weight_locked"] = int(values["weight_locked"])
    values["use_system_proxy"] = int(values["use_system_proxy"])
    columns = ["id", *values.keys(), "created_at"]
    with connect() as db:
        db.execute(
            f"INSERT INTO deployments({','.join(columns)}) VALUES({','.join(('?' for _ in columns))})",
            [dep_id, *values.values(), now_iso()],
        )
        db.execute(
            "INSERT INTO deployment_state(deployment_id,updated_at) VALUES(?,?)",
            (dep_id, now_iso()),
        )
        row = db.execute("SELECT * FROM deployments WHERE id=?", (dep_id,)).fetchone()
    return safe_deployment(row)


@router.put("/api/deployments/{dep_id}")
def update_deployment(dep_id: str, body: DeploymentInput):
    if body.provider not in ("openai", "anthropic", "responses"):
        raise HTTPException(400, "Provider must be openai, anthropic or responses")
    values = body.model_dump()
    with connect() as db:
        old = db.execute("SELECT api_keys FROM deployments WHERE id=?", (dep_id,)).fetchone()
    if old is None:
        raise HTTPException(404, "Deployment not found")
    values["api_keys"] = (
        json.dumps([protect_secret(key) for key in values["api_keys"] if key.strip()])
        if values["api_keys"]
        else old["api_keys"]
    )
    for key in ("enabled", "weight_locked", "use_system_proxy", "full_url", "thinking_mode"):
        values[key] = int(values[key])
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT auto_weight FROM deployments WHERE id=?", (dep_id,)).fetchone()
        if not current:
            raise HTTPException(404, "Deployment not found")
        values["auto_weight"] = 1
        db.execute(
            "UPDATE deployments SET "
            + ",".join((f"{key}=?" for key in values))
            + ",config_revision=config_revision+1 WHERE id=?",
            [*values.values(), dep_id],
        )
        if current["auto_weight"] != 1:
            db.execute(
                "INSERT INTO weight_events VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    dep_id,
                    "manual",
                    current["auto_weight"],
                    1,
                    "用户保存部署，重置自动权重因子",
                    now_iso(),
                ),
            )
        db.execute(
            "UPDATE deployment_state SET cooldown_until=0,consecutive_failures=0,updated_at=? WHERE deployment_id=?",
            (now_iso(), dep_id),
        )
        db.execute("DELETE FROM credential_state WHERE deployment_id=?", (dep_id,))
        db.execute("DELETE FROM settings WHERE key=?", (f"key_cursor:{dep_id}",))
        row = db.execute("SELECT * FROM deployments WHERE id=?", (dep_id,)).fetchone()
    return safe_deployment(row)


@router.patch("/api/deployments/{dep_id}/enabled")
def set_deployment_enabled(dep_id: str, body: dict[str, Any]):
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(400, "enabled must be a boolean")
    with connect() as db:
        cur = db.execute(
            "UPDATE deployments SET enabled=? WHERE id=?", (int(body["enabled"]), dep_id)
        )
        if not cur.rowcount:
            raise HTTPException(404, "Deployment not found")
        row = db.execute("SELECT * FROM deployments WHERE id=?", (dep_id,)).fetchone()
    return safe_deployment(row)


@router.delete("/api/deployments/{dep_id}")
def delete_deployment(dep_id: str):
    with connect() as db:
        cur = db.execute("DELETE FROM deployments WHERE id=?", (dep_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Deployment not found")
    return {"deleted": True}


@router.get("/api/settings")
def settings():
    rules = get_rule_settings()
    with connect() as db:
        audits = db.execute("SELECT * FROM ai_audits ORDER BY created_at DESC LIMIT 1").fetchall()
        weight_events = db.execute(
            "SELECT w.*,d.name,d.alias FROM weight_events w LEFT JOIN deployments d ON d.id=w.deployment_id ORDER BY w.created_at DESC LIMIT 1"
        ).fetchall()
        ai_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
        preset_row = db.execute(
            "SELECT value FROM settings WHERE key='provider_presets'"
        ).fetchone()
    ai = (
        json.loads(ai_row[0])
        if ai_row
        else {
            "enabled": False,
            "provider": "openai",
            "base_url": "https://api.openai.com",
            "model": "",
            "api_key": "",
        }
    )
    ai_view = {k: v for k, v in ai.items() if k != "api_key"}
    ai_view["key_saved"] = bool(ai.get("api_key"))
    return {
        "rules": rules,
        "ai": ai_view,
        "provider_presets": json.loads(preset_row[0]) if preset_row else {},
        "ai_audits": [
            {
                **dict(row),
                "report": json.loads(row["report"]),
                "changes": json.loads(row["changes"]),
            }
            for row in audits
        ],
        "weight_events": [dict(row) for row in weight_events],
    }


@router.put("/api/settings/provider-presets")
def save_provider_presets(body: dict[str, Any]):
    presets = body.get("presets", {})
    if not isinstance(presets, dict) or len(presets) > 100:
        raise HTTPException(400, "Invalid provider presets")
    allowed = {
        "name",
        "avatar_url",
        "base_url",
        "models_url",
        "provider",
        "base_weight",
        "rpm",
        "max_input_tokens",
        "max_output_tokens",
        "timeout_seconds",
        "multimodal",
        "thinking_mode",
        "enabled",
        "proxy_url",
        "use_system_proxy",
        "note",
    }
    cleaned = {}
    for key, value in presets.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise HTTPException(400, "Invalid provider preset entry")
        item = {field: value[field] for field in allowed if field in value}
        for field in (
            "base_weight",
            "rpm",
            "max_input_tokens",
            "max_output_tokens",
            "timeout_seconds",
        ):
            if field in item:
                try:
                    item[field] = (
                        float(item[field])
                        if field in ("base_weight", "timeout_seconds")
                        else int(item[field])
                    )
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Invalid {field} in preset {key}")
        for field in ("multimodal", "thinking_mode", "enabled", "use_system_proxy"):
            if field in item:
                item[field] = bool(item[field])
        cleaned[key] = item
    with connect() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('provider_presets',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(cleaned, ensure_ascii=False),),
        )
    return {"presets": cleaned}


@router.get("/api/history/{kind}")
def history(kind: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    if kind not in ("weights", "audits"):
        raise HTTPException(404, "History type not found")
    with connect() as db:
        if kind == "weights":
            total = db.execute("SELECT COUNT(*) FROM weight_events").fetchone()[0]
            rows = db.execute(
                "SELECT w.*,d.name,d.alias FROM weight_events w LEFT JOIN deployments d ON d.id=w.deployment_id ORDER BY w.created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            items = [dict(row) for row in rows]
        else:
            total = db.execute("SELECT COUNT(*) FROM ai_audits").fetchone()[0]
            rows = db.execute(
                "SELECT * FROM ai_audits ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            items = [
                {
                    **dict(row),
                    "report": json.loads(row["report"]),
                    "changes": json.loads(row["changes"]),
                }
                for row in rows
            ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.put("/api/settings/ai")
def save_ai_config(body: dict[str, Any]):
    provider = body.get("provider", "openai")
    if provider not in ("openai", "anthropic"):
        raise HTTPException(400, "AI provider must be openai or anthropic")
    model = str(body.get("model", "")).strip()
    base_url = str(body.get("base_url", "")).strip()
    api_key = str(body.get("api_key", "")).strip()
    enabled = bool(body.get("enabled", False))
    if enabled and (not model):
        raise HTTPException(400, "Analysis model is required to enable AI analysis")
    with connect() as db:
        old_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
    old = json.loads(old_row[0]) if old_row else {}
    stored_key = protect_secret(api_key) if api_key else old.get("api_key", "")
    config = {
        "enabled": enabled,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": stored_key,
    }
    with connect() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('ai_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(config),),
        )
    return {
        "enabled": config["enabled"],
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "key_saved": bool(stored_key),
    }


@router.put("/api/settings/rules")
def save_rules(body: RuleInput):
    body = body.model_dump() if isinstance(body, RuleInput) else body
    rules = {
        "enabled": bool(body.get("enabled", True)),
        "cooldown_seconds": max(10, min(3600, int(body.get("cooldown_seconds", 90)))),
        "failure_threshold": max(3, min(10, int(body.get("failure_threshold", 3)))),
        "max_auto_factor": max(1, min(3, float(body.get("max_auto_factor", 1.5)))),
        "min_auto_factor": max(0.1, min(1, float(body.get("min_auto_factor", 0.25)))),
    }
    with connect() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('rule_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(rules),),
        )
    return rules
