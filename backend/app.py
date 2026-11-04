from __future__ import annotations
import asyncio
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from responses_adapter import (
    uses_responses,
    to_responses,
    responses_to_chat,
    responses_chat_stream,
    anthropic_for_responses,
)

import db
import security
from db import now_iso, ClosingConnection, connect, init_db
from security import (
    protect_secret,
    reveal_secret,
    redact,
    ManagementAuthMiddleware,
    client_key,
    require_client_key,
)
from schemas import DeploymentInput, NetworkConfig
from rules import (
    safe_deployment,
    get_state,
    get_rule_settings,
    effective_weight,
    cooldown_allowed,
    record_result,
    candidates,
    available_key_indices,
    weighted_order,
    choose_credential,
)
from events import (
    token_usage,
    merge_usage,
    StreamUsage,
    supplier_snapshot,
    save_event,
    events,
    stream_events,
    summary,
)
from upstream import endpoint, windows_system_proxy, proxy_for, proxy_options
from adapters import (
    to_anthropic,
    to_openai,
    openai_to_anthropic_response,
    anthropic_to_openai_response,
)
from gateway import forward, forward_stream, proxy_request, models, openai_chat, anthropic_messages
from management import (
    health,
    bootstrap,
    network_config,
    update_network,
    network_recommendations,
    list_deployments,
    test_deployment_connection,
    create_deployment,
    update_deployment,
    set_deployment_enabled,
    delete_deployment,
    settings,
    save_provider_presets,
    history,
    save_ai_config,
    save_rules,
)
from ai import ai_analyze, call_analysis_model, ai_tune


@asynccontextmanager
async def lifespan(application: FastAPI):
    await asyncio.to_thread(db.init_db)
    security._key_cache = None
    security.client_key()
    from upstream import client_pool
    from retention import maintenance

    client_pool.active = True
    task = asyncio.create_task(maintenance())
    try:
        yield
    finally:
        task.cancel()
        from contextlib import suppress

        with suppress(asyncio.CancelledError):
            await task
        await client_pool.close()


_version_file = (
    (Path(sys._MEIPASS) / "metadata" / "package.json")
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1] / "frontend" / "package.json"
)
APP_VERSION = json.loads(_version_file.read_text(encoding="utf-8"))["version"]
app = FastAPI(title="Local Model Router", version=APP_VERSION, lifespan=lifespan)
app.state.management_token = os.environ.pop(
    "LOCAL_MODEL_ROUTER_MANAGEMENT_TOKEN", ""
) or secrets.token_hex(32)
app.add_middleware(ManagementAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
        os.environ.get("LOCAL_MODEL_ROUTER_DEV_ORIGIN", "http://127.0.0.1:5175"),
    ],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Management-Token"],
)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/v1/"):
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"error": {"message": str(exc.detail), "type": "api_error"}}
        )
        error = detail.get("error", {"message": str(exc.detail), "type": "api_error"})
        if request.url.path == "/v1/messages":
            body = {
                "type": "error",
                "error": {
                    "type": error.get("type", "api_error"),
                    "message": error.get("message", "Request failed"),
                },
            }
        else:
            body = {"error": error}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


from management import router as management_router

app.include_router(management_router)
from events import router as events_router

app.include_router(events_router)
from ai import router as ai_router

app.include_router(ai_router)
from gateway import router as gateway_router

app.include_router(gateway_router)
from retention import router as retention_router

app.include_router(retention_router)

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if not getattr(sys, "frozen", False) and FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
