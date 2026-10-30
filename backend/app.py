from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import random
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_DIR = Path(os.environ.get("LOCAL_MODEL_ROUTER_HOME", Path.home() / ".local-model-router"))
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "router.db"
BOOTSTRAP_KEY_PATH = APP_DIR / "client.key"
DB_LOCK = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def protect_secret(value: str) -> str:
    """Protect API keys with Windows DPAPI. Plain fallback is limited to non-Windows dev runs."""
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "plain:" + value
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    in_buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    in_blob = DATA_BLOB(len(raw), in_buf)
    out_blob = DATA_BLOB()
    crypt = ctypes.windll.crypt32
    if not crypt.CryptProtectData(ctypes.byref(in_blob), "Local Model Router", None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("Windows DPAPI encryption failed")
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def reveal_secret(value: str) -> str:
    if value.startswith("plain:"):
        return value[6:]
    if not value.startswith("dpapi:"):
        return value  # pre-encryption databases from early development
    if os.name != "nt":
        raise RuntimeError("This credential was encrypted on Windows and can only be decrypted there")
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(value[6:])
    in_buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    in_blob = DATA_BLOB(len(raw), in_buf)
    out_blob = DATA_BLOB()
    crypt = ctypes.windll.crypt32
    if not crypt.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            alias TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'openai',
            base_url TEXT NOT NULL,
            full_url INTEGER NOT NULL DEFAULT 0,
            thinking_mode INTEGER NOT NULL DEFAULT 0,
            upstream_model TEXT NOT NULL,
            api_keys TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            base_weight REAL NOT NULL DEFAULT 1,
            auto_weight REAL NOT NULL DEFAULT 1,
            weight_locked INTEGER NOT NULL DEFAULT 0,
            proxy_url TEXT NOT NULL DEFAULT '',
            use_system_proxy INTEGER NOT NULL DEFAULT 1,
            rpm INTEGER NOT NULL DEFAULT 0,
            max_input_tokens INTEGER NOT NULL DEFAULT 0,
            max_output_tokens INTEGER NOT NULL DEFAULT 0,
            timeout_seconds REAL NOT NULL DEFAULT 90,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS request_events (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            protocol TEXT NOT NULL,
            alias TEXT NOT NULL,
            deployment_id TEXT,
            deployment_name TEXT,
            status TEXT NOT NULL,
            status_code INTEGER,
            latency_ms INTEGER,
            error_kind TEXT,
            error_message TEXT,
            attempts TEXT NOT NULL DEFAULT '[]',
            total_tokens INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            usage TEXT,
            usage_events TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_request_events_created_at ON request_events(created_at);
        CREATE TABLE IF NOT EXISTS deployment_state (
            deployment_id TEXT PRIMARY KEY REFERENCES deployments(id) ON DELETE CASCADE,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            cooldown_until REAL NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS credential_state (
            deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            key_index INTEGER NOT NULL,
            cooldown_until REAL NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(deployment_id,key_index)
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_audits (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            report TEXT NOT NULL,
            changes TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS weight_events (
            id TEXT PRIMARY KEY,
            deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            before_weight REAL NOT NULL,
            after_weight REAL NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        event_columns = {row[1] for row in db.execute("PRAGMA table_info(request_events)").fetchall()}
        deployment_columns = {row[1] for row in db.execute("PRAGMA table_info(deployments)").fetchall()}
        if "full_url" not in deployment_columns:
            db.execute("ALTER TABLE deployments ADD COLUMN full_url INTEGER NOT NULL DEFAULT 0")
        if "thinking_mode" not in deployment_columns:
            db.execute("ALTER TABLE deployments ADD COLUMN thinking_mode INTEGER NOT NULL DEFAULT 0")
        if "total_tokens" not in event_columns:
            db.execute("ALTER TABLE request_events ADD COLUMN total_tokens INTEGER")
        if "input_tokens" not in event_columns:
            db.execute("ALTER TABLE request_events ADD COLUMN input_tokens INTEGER")
        if "output_tokens" not in event_columns:
            db.execute("ALTER TABLE request_events ADD COLUMN output_tokens INTEGER")
        if "usage" not in event_columns:
            db.execute("ALTER TABLE request_events ADD COLUMN usage TEXT")
        if "usage_events" not in event_columns:
            db.execute("ALTER TABLE request_events ADD COLUMN usage_events TEXT")
        for column in ("supplier_id", "supplier_name"):
            if column not in event_columns:
                db.execute(f"ALTER TABLE request_events ADD COLUMN {column} TEXT")
            if column not in deployment_columns:
                db.execute(f"ALTER TABLE deployments ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if not db.execute("SELECT 1 FROM settings WHERE key='rule_settings'").fetchone():
            db.execute("INSERT INTO settings(key,value) VALUES('rule_settings',?)", (json.dumps({
                "enabled": True, "cooldown_seconds": 90, "failure_threshold": 3,
                "max_auto_factor": 1.5, "min_auto_factor": 0.25
            }),))
        if not BOOTSTRAP_KEY_PATH.exists():
            BOOTSTRAP_KEY_PATH.write_text("sk-local-" + secrets.token_urlsafe(28), encoding="utf-8")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Local Model Router", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"],
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/v1/"):
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail), "type": "api_error"}}
        error = detail.get("error", {"message": str(exc.detail), "type": "api_error"})
        if request.url.path == "/v1/messages":
            body = {"type": "error", "error": {"type": error.get("type", "api_error"), "message": error.get("message", "Request failed")}}
        else:
            body = {"error": error}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def client_key() -> str:
    return BOOTSTRAP_KEY_PATH.read_text(encoding="utf-8").strip()


def require_client_key(authorization: str | None) -> None:
    if not authorization or not secrets.compare_digest(authorization.removeprefix("Bearer "), client_key()):
        raise HTTPException(status_code=401, detail={"error": {"message": "Invalid local API key", "type": "authentication_error"}})


class DeploymentInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    alias: str = Field(min_length=1, max_length=100)
    provider: str = "openai"
    supplier_id: str = Field(default="", max_length=100)
    supplier_name: str = Field(default="", max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    full_url: bool = False
    thinking_mode: bool = False
    upstream_model: str = Field(min_length=1, max_length=200)
    api_keys: list[str] = []
    enabled: bool = True
    base_weight: float = Field(default=1, ge=0, le=100)
    auto_weight: float = Field(default=1, ge=0.1, le=3)
    weight_locked: bool = False
    proxy_url: str = ""
    use_system_proxy: bool = True
    rpm: int = Field(default=0, ge=0)
    max_input_tokens: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=0, ge=0)
    timeout_seconds: float = Field(default=90, ge=1, le=600)
    note: str = ""


def safe_deployment(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    keys = [reveal_secret(key) for key in json.loads(item.pop("api_keys", "[]"))]
    item["key_count"] = len(keys)
    item["key_masked"] = [f"••••{key[-4:]}" for key in keys]
    item["enabled"] = bool(item["enabled"])
    item["weight_locked"] = bool(item["weight_locked"])
    item["use_system_proxy"] = bool(item["use_system_proxy"])
    item["thinking_mode"] = bool(item["thinking_mode"])
    item["full_url"] = bool(item["full_url"])
    st = get_state(item["id"])
    item.update(st)
    item["available_keys"] = len(available_key_indices(item["id"], item["key_count"]))
    if not item["key_count"]:
        item["health"] = "no_credentials"
    elif item["available_keys"] == 0:
        item["health"] = "cooldown"
    item["effective_weight"] = item["base_weight"] if item["weight_locked"] else item["base_weight"] * item["auto_weight"]
    return item


def get_state(deployment_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM deployment_state WHERE deployment_id=?", (deployment_id,)).fetchone()
    if not row:
        return {"consecutive_failures": 0, "cooldown_until": 0, "successes": 0, "failures": 0, "health": "ready"}
    result = dict(row)
    result["health"] = "cooldown" if result["cooldown_until"] > time.time() else ("degraded" if result["consecutive_failures"] else "ready")
    return result


def get_rule_settings() -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='rule_settings'").fetchone()
    return json.loads(row[0]) if row else {}


def redact(message: str, keys: list[str] | None = None) -> str:
    text = str(message or "")
    for key in keys or []:
        if key:
            text = text.replace(key, "[REDACTED]")
    return text[:500]


def record_result(deployment_id: str, success: bool, status_code: int | None, retry_after: float | None = None) -> None:
    rules = get_rule_settings()
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO deployment_state(deployment_id,updated_at) VALUES(?,?)", (deployment_id, now_iso()))
        row = db.execute("SELECT * FROM deployment_state WHERE deployment_id=?", (deployment_id,)).fetchone()
        failures = row["failures"] + (0 if success else 1)
        successes = row["successes"] + (1 if success else 0)
        consecutive = 0 if success else row["consecutive_failures"] + 1
        cooldown_until = row["cooldown_until"]
        if not success and rules.get("enabled", True):
            if status_code != 429 and consecutive >= rules.get("failure_threshold", 3):
                cooldown_until = time.time() + max(rules.get("cooldown_seconds", 90), retry_after or 0)
        auto_weight = db.execute("SELECT auto_weight,weight_locked FROM deployments WHERE id=?", (deployment_id,)).fetchone()
        if auto_weight and rules.get("enabled", True) and not auto_weight["weight_locked"]:
            if success and successes > 0 and successes % 20 == 0:
                factor = min(rules.get("max_auto_factor", 1.5), auto_weight["auto_weight"] + 0.03)
                db.execute("UPDATE deployments SET auto_weight=? WHERE id=?", (factor, deployment_id))
                if factor != auto_weight["auto_weight"]:
                    db.execute("INSERT INTO weight_events VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), deployment_id, "rule", auto_weight["auto_weight"], factor, "累计成功请求达到 20 的倍数，逐步提高自动权重", now_iso()))
            elif not success and (status_code == 429 or consecutive >= 2):
                factor = max(rules.get("min_auto_factor", 0.25), auto_weight["auto_weight"] * (0.8 if status_code == 429 else 0.9))
                db.execute("UPDATE deployments SET auto_weight=? WHERE id=?", (factor, deployment_id))
                if factor != auto_weight["auto_weight"]:
                    reason = "上游返回 429，自动降低权重" if status_code == 429 else f"连续失败 {consecutive} 次，自动降低权重"
                    db.execute("INSERT INTO weight_events VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), deployment_id, "rule", auto_weight["auto_weight"], factor, reason, now_iso()))
        db.execute("UPDATE deployment_state SET consecutive_failures=?,cooldown_until=?,successes=?,failures=?,updated_at=? WHERE deployment_id=?",
                   (consecutive, cooldown_until, successes, failures, now_iso(), deployment_id))


def candidates(alias: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM deployments WHERE alias=? AND enabled=1", (alias,)).fetchall()
    available = []
    for row in rows:
        dep = dict(row)
        dep["api_keys"] = [reveal_secret(key) for key in json.loads(dep["api_keys"])]
        state = get_state(dep["id"])
        available_keys = available_key_indices(dep["id"], len(dep["api_keys"]))
        if state["cooldown_until"] > time.time() or not available_keys:
            continue
        dep["weight"] = dep["base_weight"] if dep["weight_locked"] else dep["base_weight"] * dep["auto_weight"]
        available.append(dep)
    return available


def available_key_indices(deployment_id: str, count: int) -> list[int]:
    with connect() as db:
        states = {row["key_index"]: row["cooldown_until"] for row in db.execute("SELECT key_index,cooldown_until FROM credential_state WHERE deployment_id=?", (deployment_id,)).fetchall()}
    now = time.time()
    return [index for index in range(count) if states.get(index, 0) <= now]


def cooldown_credential(deployment_id: str, key_index: int, status_code: int, retry_after: float | None = None) -> None:
    if status_code not in (401, 403, 429):
        return
    cooldown = max(retry_after or 0, 3600 if status_code in (401, 403) else 90)
    with connect() as db:
        db.execute("INSERT INTO credential_state(deployment_id,key_index,cooldown_until,consecutive_failures,updated_at) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(deployment_id,key_index) DO UPDATE SET cooldown_until=excluded.cooldown_until,consecutive_failures=credential_state.consecutive_failures+1,updated_at=excluded.updated_at",
                   (deployment_id, key_index, time.time() + cooldown, 1, now_iso()))


def weighted_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pool = list(items)
    ordered = []
    while pool:
        weights = [max(0.01, float(item.get("weight", 1))) for item in pool]
        selected = random.choices(pool, weights=weights, k=1)[0]
        ordered.append(selected)
        pool.remove(selected)
    return ordered


def choose_credential(dep: dict[str, Any]) -> tuple[str, int]:
    available = available_key_indices(dep["id"], len(dep["api_keys"]))
    if not available:
        raise RuntimeError("No available credentials for deployment")
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (f"key_cursor:{dep['id']}",)).fetchone()
        cursor = int(row[0]) if row else random.randrange(len(available))
        next_cursor = (cursor + 1) % len(available)
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"key_cursor:{dep['id']}", str(next_cursor)))
    key_index = available[cursor % len(available)]
    return dep["api_keys"][key_index], key_index


def token_usage(usage: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    """Normalize totals only; retain provider extensions in the original usage object."""
    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
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
        result[key] = merge_usage(result.get(key, {}), value) if isinstance(value, dict) and isinstance(result.get(key, {}), dict) else value
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
                if isinstance(usage, dict):
                    self.events.append(usage)
                    self.usage = merge_usage(self.usage or {}, usage)
            except (ValueError, TypeError, AttributeError):
                continue


def supplier_snapshot(deployment: dict[str, Any]) -> dict[str, str | None]:
    if deployment.get("supplier_name"):
        return {"supplier_id": deployment.get("supplier_id") or None, "supplier_name": deployment["supplier_name"]}
    host = urlsplit(deployment.get("base_url", "")).hostname
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='provider_presets'").fetchone()
    presets = json.loads(row[0]) if row else {}
    matches = [(key, preset) for key, preset in presets.items() if host and urlsplit(preset.get("base_url", "")).hostname == host]
    if len(matches) == 1:
        key, preset = matches[0]
        return {"supplier_id": key, "supplier_name": preset.get("name") or key}
    builtin = {"api.deepseek.com": ("deepseek", "DeepSeek"), "api.openai.com": ("openai", "OpenAI"),
               "api.anthropic.com": ("anthropic", "Anthropic"), "apihub.agnes-ai.com": ("agnes", "Agnes"),
               "openrouter.ai": ("openrouter", "OpenRouter")}
    key, name = builtin.get(host, (host, host))
    return {"supplier_id": key, "supplier_name": name}


def save_event(request_id: str, protocol: str, alias: str, deployment: dict[str, Any] | None, status: str,
               status_code: int | None, latency_ms: int, error_kind: str | None, error_message: str | None,
               attempts: list[dict[str, Any]], total_tokens: int | None = None,
               usage: dict[str, Any] | None = None, usage_events: list[dict[str, Any]] | None = None) -> None:
    usage = usage if isinstance(usage, dict) else None
    input_tokens, output_tokens, normalized_total = token_usage(usage)
    if normalized_total is not None:
        total_tokens = normalized_total
    supplier = supplier_snapshot(deployment) if deployment else (attempts[-1] if attempts else {})
    with connect() as db:
        db.execute("INSERT INTO request_events(id,request_id,protocol,alias,deployment_id,deployment_name,status,status_code,latency_ms,error_kind,error_message,attempts,total_tokens,input_tokens,output_tokens,usage,usage_events,supplier_id,supplier_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), request_id, protocol, alias, deployment["id"] if deployment else None,
            deployment["name"] if deployment else None, status, status_code, latency_ms, error_kind,
            (error_message or "")[:500], json.dumps(attempts), total_tokens, input_tokens, output_tokens,
            json.dumps(usage, ensure_ascii=False) if usage is not None else None,
            json.dumps(usage_events, ensure_ascii=False) if usage_events is not None else None,
            supplier.get("supplier_id"), supplier.get("supplier_name"), now_iso()))


def endpoint(base_url: str, provider: str, protocol: str, full_url: bool = False) -> str:
    base = base_url.rstrip("/")
    if full_url:
        return base
    if base.endswith("/v1"):
        base = base[:-3]
    if provider == "anthropic":
        return base + "/v1/messages"
    return base + "/v1/chat/completions"


def windows_system_proxy() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        if not enabled or not server:
            return None
        entries = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
        proxy = entries.get("https") or entries.get("http") or server.split(";", 1)[0]
        return proxy if "://" in proxy else "http://" + proxy
    except (OSError, ImportError, ValueError):
        return None


def proxy_for(dep: dict[str, Any]) -> str | None:
    if dep.get("proxy_url"):
        return dep["proxy_url"]
    if dep.get("use_system_proxy"):
        return windows_system_proxy()
    return None


def to_anthropic(payload: dict[str, Any], model: str) -> dict[str, Any]:
    messages = payload.get("messages", [])
    system = []
    converted = []
    for msg in messages:
        role, content = msg.get("role", "user"), msg.get("content", "")
        if role == "system":
            system.append(content if isinstance(content, str) else json.dumps(content))
        elif role == "tool":
            converted.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""), "content": content}]})
        elif role == "assistant" and msg.get("tool_calls"):
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content if isinstance(content, str) else json.dumps(content)})
            for call in msg["tool_calls"]:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": call.get("id", "toolu_" + uuid.uuid4().hex[:12]), "name": fn.get("name", "tool"), "input": args})
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    out = {"model": model, "messages": converted, "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024,
           "stream": bool(payload.get("stream", False))}
    if system:
        out["system"] = "\n\n".join(system)
    for key in ("temperature", "top_p", "stop_sequences", "metadata"):
        if key in payload:
            out[key] = payload[key]
    if "tools" in payload:
        out["tools"] = [{"name": t.get("function", {}).get("name", "tool"), "description": t.get("function", {}).get("description", ""),
                         "input_schema": t.get("function", {}).get("parameters", {"type": "object", "properties": {}})} for t in payload["tools"]]
    return out


def to_openai(payload: dict[str, Any], model: str) -> dict[str, Any]:
    messages = []
    for msg in payload.get("messages", []):
        content = msg.get("content", "")
        tool_calls = []
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
            tool_results = [part for part in content if part.get("type") == "tool_result"]
            tool_uses = [part for part in content if part.get("type") == "tool_use"]
            if tool_results:
                for result in tool_results:
                    messages.append({"role": "tool", "tool_call_id": result.get("tool_use_id", ""), "content": result.get("content", "")})
            content = "".join(text_parts)
            tool_calls = [{"id": part.get("id"), "type": "function", "function": {"name": part.get("name"), "arguments": json.dumps(part.get("input", {}))}} for part in tool_uses]
            if tool_results and not content and not tool_calls:
                continue
        message = {"role": msg.get("role", "user"), "content": content or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
    out = {"model": model, "messages": messages, "stream": bool(payload.get("stream", False)),
           "max_tokens": payload.get("max_tokens", 1024)}
    for key in ("temperature", "top_p", "stop", "tools", "tool_choice", "response_format"):
        if key in payload:
            out[key] = payload[key]
    if "tools" in payload and payload["tools"] and payload["tools"][0].get("name"):
        out["tools"] = [{"type": "function", "function": {"name": tool.get("name"), "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}})}} for tool in payload["tools"]]
    return out


def openai_to_anthropic_response(data: dict[str, Any], model_alias: str) -> dict[str, Any]:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls", []):
        fn = call.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        content.append({"type": "tool_use", "id": call.get("id", "toolu_" + uuid.uuid4().hex[:12]), "name": fn.get("name", "tool"), "input": args})
    usage = data.get("usage", {})
    return {"id": data.get("id", "msg_" + uuid.uuid4().hex), "type": "message", "role": "assistant", "model": model_alias,
            "content": content, "stop_reason": "tool_use" if msg.get("tool_calls") else ("max_tokens" if choice.get("finish_reason") == "length" else "end_turn"),
            "stop_sequence": None, "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}}


def anthropic_to_openai_response(data: dict[str, Any], model_alias: str) -> dict[str, Any]:
    content = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
    calls = []
    for part in data.get("content", []):
        if part.get("type") == "tool_use":
            calls.append({"id": part.get("id"), "type": "function", "function": {"name": part.get("name"), "arguments": json.dumps(part.get("input", {}))}})
    usage = data.get("usage", {})
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if calls:
        message["tool_calls"] = calls
    return {"id": data.get("id", "chatcmpl-" + uuid.uuid4().hex), "object": "chat.completion", "created": int(time.time()),
            "model": model_alias, "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if calls else ("length" if data.get("stop_reason") == "max_tokens" else "stop")}],
            "usage": {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0), "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)}}


async def forward(payload: dict[str, Any], inbound: str, request_id: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], int, int, int | None, dict[str, Any] | None]:
    request_started = time.perf_counter()
    alias = payload.get("model", "")
    pool = candidates(alias)
    if not pool:
        raise HTTPException(status_code=503, detail={"error": {"message": f"No available deployment for model '{alias}'", "type": "model_unavailable", "request_id": request_id}})
    pool = weighted_order(pool)
    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for dep in pool:
        api_key, key_index = choose_credential(dep)
        try:
            upstream = to_anthropic(payload, dep["upstream_model"]) if (inbound == "openai" and dep["provider"] == "anthropic") else (
                to_openai(payload, dep["upstream_model"]) if (inbound == "anthropic" and dep["provider"] == "openai") else dict(payload))
            upstream["model"] = dep["upstream_model"]
            url = endpoint(dep["base_url"], dep["provider"], inbound, bool(dep.get("full_url", False)))
            headers = {"content-type": "application/json", "x-request-id": request_id}
            if dep["provider"] == "anthropic":
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["authorization"] = f"Bearer {api_key}"
            proxy = proxy_for(dep)
            async with httpx.AsyncClient(timeout=dep["timeout_seconds"], proxy=proxy, trust_env=bool(dep["use_system_proxy"])) as client:
                response = await client.post(url, headers=headers, json=upstream)
            elapsed = int((time.perf_counter() - request_started) * 1000)
            if response.status_code >= 400:
                try:
                    retry_seconds = float(response.headers.get("retry-after", ""))
                except ValueError:
                    retry_seconds = None
                cooldown_credential(dep["id"], key_index, response.status_code, retry_seconds)
                record_result(dep["id"], False, response.status_code, retry_seconds)
                attempts.append({**supplier_snapshot(dep), "deployment_id": dep["id"], "deployment": dep["name"], "credential": key_index + 1, "status": response.status_code})
                last_error = RuntimeError(redact(f"Upstream returned HTTP {response.status_code}: {response.text[:350]}", dep["api_keys"]))
                if response.status_code == 400:
                    break
                continue
            data = response.json()
            raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
            if inbound == "openai" and dep["provider"] == "anthropic":
                data = anthropic_to_openai_response(data, alias)
            elif inbound == "anthropic" and dep["provider"] == "openai":
                data = openai_to_anthropic_response(data, alias)
            else:
                data["model"] = alias
            _, _, total_tokens = token_usage(raw_usage)
            record_result(dep["id"], True, response.status_code)
            attempts.append({**supplier_snapshot(dep), "deployment_id": dep["id"], "deployment": dep["name"], "credential": key_index + 1, "status": response.status_code})
            return data, dep, attempts, response.status_code, elapsed, total_tokens, raw_usage
        except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
            record_result(dep["id"], False, 408 if isinstance(exc, httpx.TimeoutException) else 502)
            attempts.append({**supplier_snapshot(dep), "deployment_id": dep["id"], "deployment": dep["name"], "credential": key_index + 1, "status": "error", "error_kind": "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"})
            last_error = exc
            continue
    message = str(last_error or "All deployments failed")
    save_event(request_id, inbound, alias, None, "error", 502, int((time.perf_counter() - request_started) * 1000), "upstream_error", redact(message), attempts)
    raise HTTPException(status_code=502, detail={"error": {"message": message, "type": "upstream_error", "request_id": request_id}}, headers={"x-local-event-saved": "1"})


async def forward_stream(payload: dict[str, Any], inbound: str, request_id: str):
    request_started = time.perf_counter()
    alias = payload.get("model", "")
    # Cross-protocol event translation is intentionally excluded from v0.1; same-protocol SSE is passed through.
    pool = [dep for dep in candidates(alias) if dep["provider"] == inbound]
    pool = weighted_order(pool)
    if not pool:
        raise HTTPException(status_code=503, detail={"error": {"message": f"No streaming-compatible deployment available for model '{alias}'", "type": "model_unavailable", "request_id": request_id}})
    attempts = []
    last_error: Exception | None = None
    for dep in pool:
        api_key, key_index = choose_credential(dep)
        upstream = dict(payload)
        upstream["model"] = dep["upstream_model"]
        if dep["provider"] == "openai":
            upstream.setdefault("stream_options", {}).setdefault("include_usage", True)
        headers = {"content-type": "application/json", "x-request-id": request_id}
        if dep["provider"] == "anthropic":
            headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
            url = endpoint(dep["base_url"], dep["provider"], inbound, bool(dep.get("full_url", False)))
        else:
            headers["authorization"] = f"Bearer {api_key}"
            url = endpoint(dep["base_url"], dep["provider"], inbound, bool(dep.get("full_url", False)))
        client = httpx.AsyncClient(timeout=dep["timeout_seconds"], proxy=proxy_for(dep), trust_env=bool(dep["use_system_proxy"]))
        cm = client.stream("POST", url, headers=headers, json=upstream)
        try:
            response = await cm.__aenter__()
            if response.status_code >= 400:
                message = (await response.aread()).decode("utf-8", errors="replace")[:350]
                status = response.status_code
                await cm.__aexit__(None, None, None)
                await client.aclose()
                try:
                    retry_seconds = float(response.headers.get("retry-after", ""))
                except ValueError:
                    retry_seconds = None
                cooldown_credential(dep["id"], key_index, status, retry_seconds)
                record_result(dep["id"], False, status, retry_seconds)
                attempts.append({**supplier_snapshot(dep), "deployment_id": dep["id"], "deployment": dep["name"], "credential": key_index + 1, "status": status})
                last_error = RuntimeError(redact(f"Upstream returned HTTP {status}: {message}", dep["api_keys"]))
                if status == 400:
                    break
                continue
            record_result(dep["id"], True, response.status_code)
            attempts.append({**supplier_snapshot(dep), "deployment_id": dep["id"], "deployment": dep["name"], "credential": key_index + 1, "status": response.status_code})
            async def chunks():
                status = "success"
                message = None
                collected_usage = StreamUsage()
                try:
                    async for chunk in response.aiter_bytes():
                        collected_usage.feed(chunk)
                        yield chunk
                except asyncio.CancelledError:
                    status, message = "error", "Client disconnected during stream"
                    record_result(dep["id"], False, 499)
                    raise
                except Exception as exc:
                    status, message = "error", str(exc)[:350]
                    record_result(dep["id"], False, 408 if isinstance(exc, httpx.TimeoutException) else 502)
                    raise
                finally:
                    collected_usage.feed(b"", final=True)
                    elapsed = int((time.perf_counter() - request_started) * 1000)
                    save_event(request_id, inbound, alias, dep, status, response.status_code, elapsed, "stream_error" if message else None, message, attempts, usage=collected_usage.usage, usage_events=collected_usage.events)
                    await cm.__aexit__(None, None, None)
                    await client.aclose()
            media_type = response.headers.get("content-type", "text/event-stream")
            return StreamingResponse(chunks(), status_code=response.status_code, media_type=None, headers={"content-type": media_type, "x-request-id": request_id, "x-local-router-deployment": dep["name"]})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            await cm.__aexit__(None, None, None)
            await client.aclose()
            record_result(dep["id"], False, 408 if isinstance(exc, httpx.TimeoutException) else 502)
            attempts.append({**supplier_snapshot(dep), "deployment_id": dep["id"], "deployment": dep["name"], "credential": key_index + 1, "status": "error", "error_kind": "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"})
            last_error = exc
    message = str(last_error or "All streaming deployments failed")
    save_event(request_id, inbound, alias, None, "error", 502, int((time.perf_counter() - request_started) * 1000), "upstream_error", redact(message), attempts)
    raise HTTPException(status_code=502, detail={"error": {"message": message, "type": "upstream_error", "request_id": request_id}}, headers={"x-local-event-saved": "1"})


async def proxy_request(request: Request, inbound: str, authorization: str | None, protocol: str):
    require_client_key(authorization)
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": {"message": "Request body must be valid JSON", "type": "invalid_request_error"}})
    alias = str(payload.get("model", ""))
    started = time.perf_counter()
    try:
        if payload.get("stream"):
            response = await forward_stream(payload, inbound, request_id)
            return response
        data, dep, attempts, status, latency, total_tokens, usage = await forward(payload, inbound, request_id)
        save_event(request_id, protocol, alias, dep, "success", status, latency, None, None, attempts, total_tokens, usage)
        return JSONResponse(data, status_code=status, headers={"x-request-id": request_id, "x-local-router-deployment": dep["name"]})
    except HTTPException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail)}}
        message = detail.get("error", {}).get("message", str(exc.detail))
        if not (exc.headers or {}).get("x-local-event-saved"):
            save_event(request_id, protocol, alias, None, "error", exc.status_code, elapsed, detail.get("error", {}).get("type"), message, [])
        raise


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version, "usage_schema": 2}


@app.get("/api/bootstrap")
def bootstrap():
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0]
    return {"base_url": "http://127.0.0.1:8787/v1", "client_key": client_key(), "deployment_count": count}


@app.get("/api/deployments")
def list_deployments():
    with connect() as db:
        rows = db.execute("SELECT * FROM deployments ORDER BY alias,name").fetchall()
    return [safe_deployment(row) for row in rows]


@app.post("/api/deployments/test-connection")
async def test_deployment_connection(request: Request):
    body = await request.json()
    provider = body.get("provider", "openai")
    base_url = str(body.get("base_url", "")).rstrip("/")
    token = str(body.get("api_key", ""))
    if token == "__saved__":
        dep_id = body.get("deployment_id")
        with connect() as db:
            row = db.execute("SELECT api_keys FROM deployments WHERE id=?", (dep_id,)).fetchone()
        keys = [reveal_secret(key) for key in json.loads(row["api_keys"])] if row else []
        token = keys[0] if keys else ""
    if not base_url or not token:
        raise HTTPException(400, "请填写 API Base URL 和 API Token")
    if provider not in ("openai", "anthropic"):
        raise HTTPException(400, "不支持的上游协议")
    headers = {"Authorization": f"Bearer {token}"}
    if provider == "anthropic":
        headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
        urls = [base_url + ("/models" if base_url.endswith("/v1") else "/v1/models")]
    else:
        # DeepSeek exposes GET /models at the base URL. Many other OpenAI-compatible
        # gateways expose GET /v1/models, so retry that path only when /models is absent.
        urls = [base_url + ("/models" if base_url.endswith("/v1") else "/models")]
        fallback = base_url + ("/v1/models" if not base_url.endswith("/v1") else "/models")
        if fallback != urls[0]:
            urls.append(fallback)
    models_url = str(body.get("models_url", "")).strip()
    if models_url:
        urls = [models_url]
    proxy = body.get("proxy_url") or (windows_system_proxy() if body.get("use_system_proxy", True) else None)
    try:
        async with httpx.AsyncClient(timeout=min(int(body.get("timeout_seconds", 30)), 60), proxy=proxy, trust_env=bool(body.get("use_system_proxy", True))) as client:
            response = await client.get(urls[0], headers=headers)
            if response.status_code == 404 and len(urls) > 1:
                response = await client.get(urls[1], headers=headers)
        if response.is_success:
            payload = response.json()
            models = [str(item.get("id") or item.get("name")) for item in payload.get("data", []) if item.get("id") or item.get("name")]
            return {"ok": True, "message": "连接成功，已获取模型列表" if models else "连接成功，但上游未返回模型列表", "models": models}
        raise HTTPException(response.status_code, f"上游返回 HTTP {response.status_code}：{response.text[:240]}")
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise HTTPException(502, f"连接上游失败：{str(exc)[:200]}") from exc


@app.post("/api/deployments")
def create_deployment(body: DeploymentInput):
    if body.provider not in ("openai", "anthropic"):
        raise HTTPException(400, "Provider must be openai or anthropic")
    dep_id = "dep_" + uuid.uuid4().hex[:12]
    values = body.model_dump()
    values["api_keys"] = json.dumps([protect_secret(key) for key in values["api_keys"] if key.strip()])
    values["enabled"] = int(values["enabled"])
    values["weight_locked"] = int(values["weight_locked"])
    values["use_system_proxy"] = int(values["use_system_proxy"])
    columns = ["id", *values.keys(), "created_at"]
    with connect() as db:
        db.execute(f"INSERT INTO deployments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [dep_id, *values.values(), now_iso()])
        db.execute("INSERT INTO deployment_state(deployment_id,updated_at) VALUES(?,?)", (dep_id, now_iso()))
        row = db.execute("SELECT * FROM deployments WHERE id=?", (dep_id,)).fetchone()
    return safe_deployment(row)


@app.put("/api/deployments/{dep_id}")
def update_deployment(dep_id: str, body: DeploymentInput):
    if body.provider not in ("openai", "anthropic"):
        raise HTTPException(400, "Provider must be openai or anthropic")
    values = body.model_dump()
    replace_keys = bool(values["api_keys"])
    with connect() as db:
        old = db.execute("SELECT api_keys FROM deployments WHERE id=?", (dep_id,)).fetchone()
    if old is None:
        raise HTTPException(404, "Deployment not found")
    values["api_keys"] = json.dumps([protect_secret(key) for key in values["api_keys"] if key.strip()]) if values["api_keys"] else old["api_keys"]
    for key in ("enabled", "weight_locked", "use_system_proxy", "full_url", "thinking_mode"):
        values[key] = int(values[key])
    with connect() as db:
        exists = db.execute("SELECT 1 FROM deployments WHERE id=?", (dep_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Deployment not found")
        db.execute("UPDATE deployments SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", [*values.values(), dep_id])
        if replace_keys:
            db.execute("DELETE FROM credential_state WHERE deployment_id=?", (dep_id,))
        row = db.execute("SELECT * FROM deployments WHERE id=?", (dep_id,)).fetchone()
    return safe_deployment(row)


@app.patch("/api/deployments/{dep_id}/enabled")
def set_deployment_enabled(dep_id: str, body: dict[str, Any]):
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(400, "enabled must be a boolean")
    with connect() as db:
        cur = db.execute("UPDATE deployments SET enabled=? WHERE id=?", (int(body["enabled"]), dep_id))
        if not cur.rowcount:
            raise HTTPException(404, "Deployment not found")
        row = db.execute("SELECT * FROM deployments WHERE id=?", (dep_id,)).fetchone()
    return safe_deployment(row)


@app.delete("/api/deployments/{dep_id}")
def delete_deployment(dep_id: str):
    with connect() as db:
        cur = db.execute("DELETE FROM deployments WHERE id=?", (dep_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Deployment not found")
    return {"deleted": True}


@app.get("/api/events")
def events(limit: int = 100):
    with connect() as db:
        rows = db.execute("SELECT * FROM request_events ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
        deployments = {row["id"]: dict(row) for row in db.execute("SELECT * FROM deployments").fetchall()}
    historical_suppliers = {}
    result = [{**dict(row), "attempts": json.loads(row["attempts"]), "usage": json.loads(row["usage"]) if row["usage"] else None,
             "usage_events": json.loads(row["usage_events"]) if row["usage_events"] else None} for row in rows]
    for event in result:
        event["supplier_source"] = "snapshot" if event["supplier_name"] else None
        dep_id = event["deployment_id"]
        if not event["supplier_name"] and dep_id in deployments:
            if dep_id not in historical_suppliers:
                historical_suppliers[dep_id] = supplier_snapshot(deployments[dep_id])
            event.update(historical_suppliers[dep_id])
            event["supplier_source"] = "current_deployment"
    return result


@app.get("/api/events/stream")
async def stream_events(start: int = Query(..., ge=0), end: int = Query(..., ge=0)):
    """Keep a lightweight live subscription for the currently viewed log range."""
    if end < start or end - start > 7 * 24 * 60 * 60 * 1000:
        raise HTTPException(400, "Invalid log range")

    async def updates():
        last_check = now_iso()
        seen: set[str] = set()
        yield "event: ready\ndata: {}\n\n"
        while True:
            await asyncio.sleep(0.5)
            start_iso = datetime.fromtimestamp(start / 1000, timezone.utc).isoformat()
            with connect() as db:
                rows = db.execute(
                    "SELECT id, created_at FROM request_events WHERE created_at >= ? AND created_at >= ? ORDER BY created_at ASC LIMIT 100",
                    (start_iso, last_check),
                ).fetchall()
            fresh = [row for row in rows if row["id"] not in seen]
            if fresh:
                seen.update(row["id"] for row in fresh)
                yield f"event: new\ndata: {json.dumps({'ids': [row['id'] for row in fresh]})}\n\n"
            last_check = datetime.fromtimestamp(time.time() - 1, timezone.utc).isoformat()
            if not fresh:
                yield ": keep-alive\n\n"

    return StreamingResponse(updates(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@app.get("/api/summary")
def summary():
    with connect() as db:
        deployments = db.execute("SELECT COUNT(*) total, SUM(enabled) enabled FROM deployments").fetchone()
        events = db.execute("SELECT COUNT(*) total, SUM(status='success') success, SUM(status='error') errors FROM request_events WHERE created_at >= datetime('now','-1 day')").fetchone()
        latest = db.execute("SELECT created_at FROM request_events ORDER BY created_at DESC LIMIT 1").fetchone()
    with connect() as db:
        dep_rows = db.execute("SELECT * FROM deployments").fetchall()
    deps = [safe_deployment(r) for r in dep_rows]
    return {"deployments": dict(deployments), "requests_24h": dict(events), "latest_request": latest[0] if latest else None,
            "cooldowns": sum(1 for d in deps if d["health"] == "cooldown"), "success_rate": (events["success"] or 0) / max(events["total"] or 0, 1)}


@app.get("/api/settings")
def settings():
    rules = get_rule_settings()
    with connect() as db:
        audits = db.execute("SELECT * FROM ai_audits ORDER BY created_at DESC LIMIT 1").fetchall()
        weight_events = db.execute("SELECT w.*,d.name,d.alias FROM weight_events w LEFT JOIN deployments d ON d.id=w.deployment_id ORDER BY w.created_at DESC LIMIT 1").fetchall()
        ai_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
        preset_row = db.execute("SELECT value FROM settings WHERE key='provider_presets'").fetchone()
    ai = json.loads(ai_row[0]) if ai_row else {"enabled": False, "provider": "openai", "base_url": "https://api.openai.com", "model": "", "api_key": ""}
    ai_view = {k: v for k, v in ai.items() if k != "api_key"}
    ai_view["key_saved"] = bool(ai.get("api_key"))
    return {"rules": rules, "ai": ai_view, "provider_presets": json.loads(preset_row[0]) if preset_row else {}, "ai_audits": [{**dict(row), "report": json.loads(row["report"]), "changes": json.loads(row["changes"])} for row in audits],
            "weight_events": [dict(row) for row in weight_events]}


@app.put("/api/settings/provider-presets")
def save_provider_presets(body: dict[str, Any]):
    presets = body.get("presets", {})
    if not isinstance(presets, dict) or len(presets) > 100:
        raise HTTPException(400, "Invalid provider presets")
    allowed = {"name", "avatar_url", "base_url", "models_url", "provider", "base_weight", "rpm", "max_input_tokens", "max_output_tokens", "timeout_seconds", "multimodal", "thinking_mode", "enabled", "proxy_url", "use_system_proxy", "note"}
    cleaned = {}
    for key, value in presets.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise HTTPException(400, "Invalid provider preset entry")
        item = {field: value[field] for field in allowed if field in value}
        for field in ("base_weight", "rpm", "max_input_tokens", "max_output_tokens", "timeout_seconds"):
            if field in item:
                try:
                    item[field] = float(item[field]) if field in ("base_weight", "timeout_seconds") else int(item[field])
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Invalid {field} in preset {key}")
        for field in ("multimodal", "thinking_mode", "enabled", "use_system_proxy"):
            if field in item:
                item[field] = bool(item[field])
        cleaned[key] = item
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('provider_presets',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(cleaned, ensure_ascii=False),))
    return {"presets": cleaned}


@app.get("/api/history/{kind}")
def history(kind: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    if kind not in ("weights", "audits"):
        raise HTTPException(404, "History type not found")
    with connect() as db:
        if kind == "weights":
            total = db.execute("SELECT COUNT(*) FROM weight_events").fetchone()[0]
            rows = db.execute("SELECT w.*,d.name,d.alias FROM weight_events w LEFT JOIN deployments d ON d.id=w.deployment_id ORDER BY w.created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            items = [dict(row) for row in rows]
        else:
            total = db.execute("SELECT COUNT(*) FROM ai_audits").fetchone()[0]
            rows = db.execute("SELECT * FROM ai_audits ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            items = [{**dict(row), "report": json.loads(row["report"]), "changes": json.loads(row["changes"])} for row in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.put("/api/settings/ai")
def save_ai_config(body: dict[str, Any]):
    provider = body.get("provider", "openai")
    if provider not in ("openai", "anthropic"):
        raise HTTPException(400, "AI provider must be openai or anthropic")
    model = str(body.get("model", "")).strip()
    base_url = str(body.get("base_url", "")).strip()
    api_key = str(body.get("api_key", "")).strip()
    with connect() as db:
        old_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
    old = json.loads(old_row[0]) if old_row else {}
    stored_key = protect_secret(api_key) if api_key else old.get("api_key", "")
    config = {"enabled": bool(body.get("enabled", False)), "provider": provider, "base_url": base_url,
              "model": model, "api_key": stored_key}
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('ai_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(config),))
    return {"enabled": config["enabled"], "provider": provider, "base_url": base_url, "model": model, "key_saved": bool(stored_key)}


@app.put("/api/settings/rules")
def save_rules(body: dict[str, Any]):
    rules = {"enabled": bool(body.get("enabled", True)),
             "cooldown_seconds": max(10, min(3600, int(body.get("cooldown_seconds", 90)))),
             "failure_threshold": max(1, min(10, int(body.get("failure_threshold", 3)))),
             "max_auto_factor": max(1, min(3, float(body.get("max_auto_factor", 1.5)))),
             "min_auto_factor": max(0.1, min(1, float(body.get("min_auto_factor", 0.25))))}
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('rule_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(rules),))
    return rules


@app.post("/api/ai/analyze")
async def ai_analyze(body: dict[str, Any]):
    # The configured analyzer receives only aggregate metrics and a bounded list of redacted error summaries.
    with connect() as db:
        rows = db.execute("SELECT * FROM deployments").fetchall()
        ev = db.execute("SELECT status,error_kind,status_code,latency_ms,created_at,alias,error_message,deployment_id,deployment_name,attempts FROM request_events ORDER BY created_at DESC LIMIT 200").fetchall()
        cfg_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
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
            counts = attempt_counts.setdefault(key, {"deployment_id": attempt.get("deployment_id"), "deployment": attempt.get("deployment"),
                                                      "requests": 0, "successes": 0, "failures": 0,
                                                      "rate_limits_429": 0, "timeouts": 0, "latencies": []})
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
        performance.append({**counts, "sampled_requests": counts["requests"],
                            "success_rate": round(counts["successes"] / max(counts["requests"], 1), 4),
                            "latency_p50_ms": percentile(latency_values[:], .50), "latency_p95_ms": percentile(latency_values[:], .95)})
    metrics = {"deployment_count": len(rows), "sampled_events": len(ev), "performance_by_deployment": performance,
               "deployments": [{"name": r["name"], "alias": r["alias"], "provider": r["provider"], "upstream_model": r["upstream_model"],
                                "enabled": bool(r["enabled"]), "base_weight": r["base_weight"], "auto_weight": r["auto_weight"],
                                "weight_locked": bool(r["weight_locked"]), "rpm": r["rpm"], "max_input_tokens": r["max_input_tokens"],
                                "max_output_tokens": r["max_output_tokens"], "note": r["note"]} for r in rows],
               "recent_events": [{"alias": r["alias"], "status": r["status"], "error_kind": r["error_kind"],
                                  "latency_ms": r["latency_ms"], "error": r["error_message"], "attempts": json.loads(r["attempts"] or "[]")} for r in ev[:40]]}
    report: dict[str, Any] = {"summary": "本地指标快照已生成。", "metrics": metrics,
                              "note": "未向 AI 发送请求正文或 API Token。"}
    if config.get("enabled") and config.get("api_key") and config.get("model"):
        prompt = ("你是模型路由服务的只读运维分析器。分析 JSON 指标，仅输出 JSON 对象，格式为 "
                  '{"summary":"...","findings":[{"alias":"...","severity":"low|medium|high","evidence":"...","recommendation":"..."}],"uncertainties":["..."]}。'
                  "不得臆造缺失数据，不要提出修改凭据/端点，不执行任何操作。以下为脱敏数据：\n" + json.dumps(metrics, ensure_ascii=False))
        try:
            report = await call_analysis_model(config, prompt)
            report["metrics"] = metrics
            report["note"] = "AI 接收的是脱敏汇总指标，不含请求正文或 API Token。"
        except Exception as exc:
            report["analysis_error"] = str(exc)[:400]
            report["summary"] = "AI 分析调用失败；保留了本地指标快照。"
    audit_id = "audit_" + uuid.uuid4().hex[:12]
    with connect() as db:
        db.execute("INSERT INTO ai_audits(id,kind,status,report,changes,created_at) VALUES(?,?,?,?,?,?)",
                   (audit_id, "analysis", "complete" if "analysis_error" not in report else "failed", json.dumps(report), "[]", now_iso()))
    return {"id": audit_id, "kind": "analysis", "status": "complete" if "analysis_error" not in report else "failed", "report": report, "changes": []}


async def call_analysis_model(config: dict[str, Any], prompt: str) -> dict[str, Any]:
    key = reveal_secret(config["api_key"])
    headers = {"content-type": "application/json"}
    if config["provider"] == "anthropic":
        base = config["base_url"].rstrip("/")
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        payload = {"model": config["model"], "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]}
    else:
        base = config["base_url"].rstrip("/")
        url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
        headers["authorization"] = "Bearer " + key
        payload = {"model": config["model"], "temperature": 0, "response_format": {"type": "json_object"},
                   "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    raw = data["content"][0]["text"] if config["provider"] == "anthropic" else data["choices"][0]["message"]["content"]
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("AI response was not a JSON object")
    return result


@app.post("/api/ai/tune")
async def ai_tune():
    with connect() as db:
        report_row = db.execute("SELECT * FROM ai_audits WHERE kind='analysis' AND status='complete' ORDER BY created_at DESC LIMIT 1").fetchone()
        cfg_row = db.execute("SELECT value FROM settings WHERE key='ai_config'").fetchone()
        deployments = db.execute("SELECT * FROM deployments").fetchall()
    if not report_row:
        raise HTTPException(400, "Generate a successful analysis report first")
    config = json.loads(cfg_row[0]) if cfg_row else {}
    if not config.get("enabled") or not config.get("api_key") or not config.get("model"):
        raise HTTPException(400, "Configure and enable an AI analysis model first")
    live = [{"id": r["id"], "alias": r["alias"], "name": r["name"], "base_weight": r["base_weight"],
             "auto_weight": r["auto_weight"], "weight_locked": bool(r["weight_locked"]), "enabled": bool(r["enabled"])} for r in deployments]
    prompt = ("基于综合分析报告和当前权重提出必要的自动权重建议。只输出 JSON："
              '{"changes":[{"deployment_id":"...","proposed_weight":1.0,"reason":"..."}],"summary":"..."}。'
              "proposed_weight 是 auto_weight 因子，范围 0.1 到 3.0；单次相对变化最多 25%。不需要变化则 changes 为空。"
              "不要调整锁定或停用项；deployment_id 必须来自部署清单。\n报告：" + report_row["report"] + "\n当前部署：" + json.dumps(live, ensure_ascii=False))
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
                raise ValueError("recommendation contains an unknown, disabled, or locked deployment")
            if isinstance(proposed, bool) or not isinstance(proposed, (int, float)) or not 0.1 <= proposed <= 3:
                raise ValueError("recommended weight is outside the allowed range")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("every weight change must include a reason")
            if dep["auto_weight"] and abs(proposed - dep["auto_weight"]) / dep["auto_weight"] > 0.250001:
                raise ValueError("recommended change exceeds the 25% per-run limit")
            if abs(float(proposed) - dep["auto_weight"]) > 0.0001:
                accepted.append({"deployment_id": dep_id, "name": dep["name"], "before": dep["auto_weight"], "after": float(proposed), "reason": reason.strip()})
        with connect() as db:
            for change in accepted:
                if abs(change["after"] - change["before"]) > 0.0001:
                    db.execute("UPDATE deployments SET auto_weight=? WHERE id=? AND weight_locked=0", (change["after"], change["deployment_id"]))
                    db.execute("INSERT INTO weight_events VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), change["deployment_id"], "ai", change["before"], change["after"], change["reason"], now_iso()))
            db.execute("INSERT INTO ai_audits(id,kind,status,report,changes,created_at) VALUES(?,?,?,?,?,?)",
                       (audit_id, "tune", "applied" if accepted else "no_change", json.dumps(recommendation), json.dumps(accepted), now_iso()))
        return {"id": audit_id, "kind": "tune", "status": "applied" if accepted else "no_change", "report": recommendation, "changes": accepted}
    except Exception as exc:
        with connect() as db:
            db.execute("INSERT INTO ai_audits(id,kind,status,report,changes,created_at) VALUES(?,?,?,?,?,?)",
                       (audit_id, "tune", "rejected", json.dumps({"error": str(exc)[:400]}), "[]", now_iso()))
        raise HTTPException(422, f"AI tuning result rejected: {str(exc)[:250]}")


@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    require_client_key(authorization)
    with connect() as db:
        rows = db.execute("SELECT alias,MAX(created_at) created_at FROM deployments WHERE enabled=1 GROUP BY alias ORDER BY alias").fetchall()
    return {"object": "list", "data": [{"id": row["alias"], "object": "model", "created": int(time.time()), "owned_by": "local-model-router"} for row in rows]}


@app.post("/v1/chat/completions")
async def openai_chat(request: Request, authorization: str | None = Header(default=None)):
    return await proxy_request(request, "openai", authorization, "openai")


@app.post("/v1/messages")
async def anthropic_messages(request: Request, x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    return await proxy_request(request, "anthropic", "Bearer " + x_api_key if x_api_key else authorization, "anthropic")


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
