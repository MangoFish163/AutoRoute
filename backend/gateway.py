from __future__ import annotations
import asyncio
import json
import time
import uuid
from typing import Any
from urllib.parse import quote
import httpx
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from responses_adapter import (
    uses_responses,
    to_responses,
    responses_to_chat,
    responses_chat_stream,
    anthropic_for_responses,
)
from fastapi import APIRouter
from events import StreamUsage, save_event, supplier_snapshot, token_usage
from adapters import (
    IncompatibleRequest,
    anthropic_to_openai_response,
    openai_to_anthropic_response,
    to_anthropic,
    to_openai,
)
from rules import (
    NoAvailableCredentials,
    candidates,
    choose_credential,
    record_result,
    weighted_order,
)
from db import connect
from upstream import endpoint, client_pool
from contextlib import AsyncExitStack
import anyio
from security import redact, require_client_key
from request_validation import validate_payload

router = APIRouter()


async def forward(
    payload: dict[str, Any], inbound: str, request_id: str
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    int,
    int,
    int | None,
    dict[str, Any] | None,
]:
    request_started = time.perf_counter()
    alias = payload.get("model", "")
    pool = await asyncio.to_thread(candidates, alias)
    if not pool:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": f"No available deployment for model '{alias}'",
                    "type": "model_unavailable",
                    "request_id": request_id,
                }
            },
        )
    pool = weighted_order(pool)
    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for dep in pool:
        try:
            upstream = (
                to_anthropic(payload, dep["upstream_model"])
                if inbound == "openai" and dep["provider"] == "anthropic"
                else to_openai(payload, dep["upstream_model"])
                if inbound == "anthropic" and dep["provider"] == "openai"
                else dict(payload)
            )
            upstream["model"] = dep["upstream_model"]
            responses = uses_responses(dep)
            if responses:
                try:
                    chat = (
                        anthropic_for_responses(payload, dep["upstream_model"])
                        if inbound == "anthropic"
                        else payload
                    )
                    upstream = to_responses(chat, dep["upstream_model"])
                except ValueError as exc:
                    raise IncompatibleRequest(str(exc)) from exc
            api_key, key_index = await asyncio.to_thread(choose_credential, dep)
            url = endpoint(
                dep["base_url"], dep["provider"], inbound, bool(dep.get("full_url", False))
            )
            headers = {"content-type": "application/json", "x-request-id": request_id}
            if dep["provider"] == "anthropic":
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["authorization"] = f"Bearer {api_key}"
            async with client_pool.lease(dep) as client:
                response = await client.post(
                    url, headers=headers, json=upstream, timeout=dep["timeout_seconds"]
                )
            elapsed = int((time.perf_counter() - request_started) * 1000)
            if response.status_code >= 400:
                try:
                    retry_seconds = float(response.headers.get("retry-after", ""))
                except ValueError:
                    retry_seconds = None
                await asyncio.to_thread(
                    record_result,
                    dep["id"],
                    False,
                    response.status_code,
                    retry_seconds,
                    key_index=key_index,
                    config_revision=dep.get("config_revision"),
                )
                attempts.append(
                    {
                        **await asyncio.to_thread(supplier_snapshot, dep),
                        "deployment_id": dep["id"],
                        "deployment": dep["name"],
                        "credential": key_index + 1,
                        "status": response.status_code,
                    }
                )
                last_error = RuntimeError(
                    redact(
                        f"Upstream returned HTTP {response.status_code}: {response.text[:350]}",
                        dep["api_keys"],
                    )
                )
                if response.status_code == 400:
                    break
                continue
            data = response.json()
            raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
            if responses:
                data = responses_to_chat(data, alias)
                if inbound == "anthropic":
                    data = openai_to_anthropic_response(data, alias)
            elif inbound == "openai" and dep["provider"] == "anthropic":
                data = anthropic_to_openai_response(data, alias)
            elif inbound == "anthropic" and dep["provider"] == "openai":
                data = openai_to_anthropic_response(data, alias)
            else:
                data["model"] = alias
            _, _, total_tokens = token_usage(raw_usage)
            await asyncio.to_thread(
                record_result,
                dep["id"],
                True,
                response.status_code,
                key_index=key_index,
                config_revision=dep.get("config_revision"),
            )
            attempts.append(
                {
                    **await asyncio.to_thread(supplier_snapshot, dep),
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "credential": key_index + 1,
                    "status": response.status_code,
                }
            )
            return (data, dep, attempts, response.status_code, elapsed, total_tokens, raw_usage)
        except NoAvailableCredentials as exc:
            attempts.append(
                {
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "status": "skipped",
                    "error_kind": "credentials_unavailable",
                    "message": str(exc),
                }
            )
            continue
        except IncompatibleRequest as exc:
            attempts.append(
                {
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "status": "skipped",
                    "error_kind": "incompatible_request",
                    "message": str(exc),
                }
            )
            last_error = RuntimeError(redact(str(exc), dep["api_keys"]))
            continue
        except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
            await asyncio.to_thread(
                record_result,
                dep["id"],
                False,
                408 if isinstance(exc, httpx.TimeoutException) else 502,
                key_index=key_index,
                config_revision=dep.get("config_revision"),
            )
            attempts.append(
                {
                    **await asyncio.to_thread(supplier_snapshot, dep),
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "credential": key_index + 1,
                    "status": "error",
                    "error_kind": "timeout"
                    if isinstance(exc, httpx.TimeoutException)
                    else "network_error",
                }
            )
            last_error = RuntimeError(redact(str(exc), dep["api_keys"]))
            continue
    await routing_failed(request_id, inbound, alias, request_started, attempts, last_error)


async def forward_stream(payload: dict[str, Any], inbound: str, request_id: str):
    request_started = time.perf_counter()
    alias = payload.get("model", "")
    pool = [
        dep
        for dep in await asyncio.to_thread(candidates, alias)
        if dep["provider"] == inbound or (inbound == "openai" and uses_responses(dep))
    ]
    pool = weighted_order(pool)
    if not pool:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": f"No streaming-compatible deployment available for model '{alias}'",
                    "type": "model_unavailable",
                    "request_id": request_id,
                }
            },
        )
    attempts = []
    last_error: Exception | None = None
    for dep in pool:
        upstream = dict(payload)
        upstream["model"] = dep["upstream_model"]
        responses = uses_responses(dep)
        if responses:
            try:
                upstream = to_responses(payload, dep["upstream_model"])
            except ValueError as exc:
                attempts.append(
                    {
                        "deployment_id": dep["id"],
                        "deployment": dep["name"],
                        "status": "skipped",
                        "error_kind": "incompatible_request",
                        "message": str(exc),
                    }
                )
                last_error = IncompatibleRequest(str(exc))
                continue
        elif dep["provider"] == "openai":
            upstream.setdefault("stream_options", {}).setdefault("include_usage", True)
        try:
            api_key, key_index = await asyncio.to_thread(choose_credential, dep)
        except NoAvailableCredentials as exc:
            attempts.append(
                {
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "status": "skipped",
                    "error_kind": "credentials_unavailable",
                    "message": str(exc),
                }
            )
            continue
        headers = {"content-type": "application/json", "x-request-id": request_id}
        if dep["provider"] == "anthropic":
            headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
            url = endpoint(
                dep["base_url"], dep["provider"], inbound, bool(dep.get("full_url", False))
            )
        else:
            headers["authorization"] = f"Bearer {api_key}"
            url = endpoint(
                dep["base_url"], dep["provider"], inbound, bool(dep.get("full_url", False))
            )
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(client_pool.lease(dep))
            response = await stack.enter_async_context(
                client.stream(
                    "POST", url, headers=headers, json=upstream, timeout=dep["timeout_seconds"]
                )
            )
            if response.status_code >= 400:
                message = (await response.aread()).decode("utf-8", errors="replace")[:350]
                status = response.status_code
                await close_stack(stack)
                try:
                    retry_seconds = float(response.headers.get("retry-after", ""))
                except ValueError:
                    retry_seconds = None
                await asyncio.to_thread(
                    record_result,
                    dep["id"],
                    False,
                    status,
                    retry_seconds,
                    key_index=key_index,
                    config_revision=dep.get("config_revision"),
                )
                attempts.append(
                    {
                        **await asyncio.to_thread(supplier_snapshot, dep),
                        "deployment_id": dep["id"],
                        "deployment": dep["name"],
                        "credential": key_index + 1,
                        "status": status,
                    }
                )
                last_error = RuntimeError(
                    redact(f"Upstream returned HTTP {status}: {message}", dep["api_keys"])
                )
                if status == 400:
                    break
                continue
            attempts.append(
                {
                    **await asyncio.to_thread(supplier_snapshot, dep),
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "credential": key_index + 1,
                    "status": response.status_code,
                }
            )

            async def chunks():
                status = "success"
                message = None
                error_kind = None
                collected_usage = StreamUsage()
                try:
                    if responses:
                        async for chunk in responses_chat_stream(
                            response,
                            alias,
                            collected_usage,
                            bool((payload.get("stream_options") or {}).get("include_usage")),
                        ):
                            yield chunk
                    else:
                        async for chunk in response.aiter_bytes():
                            collected_usage.feed(chunk)
                            yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    status, message = ("error", "Client disconnected during stream")
                    error_kind = "client_disconnected"
                    raise
                except Exception as exc:
                    status, message = ("error", redact(str(exc)[:350], dep["api_keys"]))
                    error_kind = "stream_interrupted"
                    raise
                finally:
                    with anyio.CancelScope(shield=True):
                        await close_stack(stack)
                        await asyncio.to_thread(
                            record_result,
                            dep["id"],
                            status == "success",
                            response.status_code if status == "success" else 502,
                            key_index=key_index,
                            config_revision=dep.get("config_revision"),
                        )
                        collected_usage.feed(b"", final=True)
                        elapsed = int((time.perf_counter() - request_started) * 1000)
                        await asyncio.to_thread(
                            save_event,
                            request_id,
                            inbound,
                            alias,
                            dep,
                            status,
                            response.status_code,
                            elapsed,
                            error_kind,
                            message,
                            attempts,
                            usage=collected_usage.usage,
                            usage_events=collected_usage.events,
                        )

            media_type = response.headers.get("content-type", "text/event-stream")
            return OwnedStreamingResponse(
                stack,
                chunks(),
                status_code=response.status_code,
                media_type=None,
                headers={
                    "content-type": media_type,
                    "x-request-id": request_id,
                    "x-local-router-deployment": quote(dep["name"]),
                },
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            await close_stack(stack)
            await asyncio.to_thread(
                record_result,
                dep["id"],
                False,
                408 if isinstance(exc, httpx.TimeoutException) else 502,
                key_index=key_index,
                config_revision=dep.get("config_revision"),
            )
            attempts.append(
                {
                    **await asyncio.to_thread(supplier_snapshot, dep),
                    "deployment_id": dep["id"],
                    "deployment": dep["name"],
                    "credential": key_index + 1,
                    "status": "error",
                    "error_kind": "timeout"
                    if isinstance(exc, httpx.TimeoutException)
                    else "network_error",
                }
            )
            last_error = RuntimeError(redact(str(exc), dep["api_keys"]))
        except BaseException:
            await close_stack(stack)
            raise
    await routing_failed(request_id, inbound, alias, request_started, attempts, last_error)


async def routing_failed(request_id, inbound, alias, started, attempts, last_error):
    only_skipped = attempts and all(a.get("status") == "skipped" for a in attempts)
    if only_skipped and any(a.get("error_kind") == "credentials_unavailable" for a in attempts):
        status, kind = 503, "model_unavailable"
        message = f"No available credentials for model '{alias}'"
    elif only_skipped:
        status, kind = 400, "invalid_request_error"
        message = str(last_error or "No compatible deployment")
    else:
        status, kind = 502, "upstream_error"
        message = str(last_error or "All deployments failed")
    await asyncio.to_thread(
        save_event,
        request_id,
        inbound,
        alias,
        None,
        "error",
        status,
        int((time.perf_counter() - started) * 1000),
        kind,
        redact(message),
        attempts,
    )
    raise HTTPException(
        status_code=status,
        detail={"error": {"message": message, "type": kind, "request_id": request_id}},
        headers={"x-local-event-saved": "1"},
    )


async def proxy_request(request: Request, inbound: str, authorization: str | None, protocol: str):
    require_client_key(authorization)
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    started = time.perf_counter()
    alias = ""
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 20 * 1024 * 1024:
                raise HTTPException(
                    413,
                    {
                        "error": {
                            "message": "Request body exceeds 20 MiB",
                            "type": "invalid_request_error",
                        }
                    },
                )
            raw.extend(chunk)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(
                400,
                {
                    "error": {
                        "message": "Request body must be valid JSON",
                        "type": "invalid_request_error",
                    }
                },
            ) from None
        if isinstance(payload, dict) and isinstance(payload.get("model"), str):
            alias = payload["model"]
        try:
            validate_payload(payload, inbound)
        except ValueError as exc:
            raise HTTPException(
                400,
                {"error": {"message": str(exc), "type": "invalid_request_error"}},
            ) from None
        if payload.get("stream"):
            response = await forward_stream(payload, inbound, request_id)
            return response
        data, dep, attempts, status, latency, total_tokens, usage = await forward(
            payload, inbound, request_id
        )
        await asyncio.to_thread(
            save_event,
            request_id,
            protocol,
            alias,
            dep,
            "success",
            status,
            latency,
            None,
            None,
            attempts,
            total_tokens,
            usage,
        )
        return JSONResponse(
            data,
            status_code=status,
            headers={"x-request-id": request_id, "x-local-router-deployment": quote(dep["name"])},
        )
    except HTTPException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        detail = (
            exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail)}}
        )
        message = detail.get("error", {}).get("message", str(exc.detail))
        if not (exc.headers or {}).get("x-local-event-saved"):
            await asyncio.to_thread(
                save_event,
                request_id,
                protocol,
                alias,
                None,
                "error",
                exc.status_code,
                elapsed,
                detail.get("error", {}).get("type"),
                message,
                [],
            )
        raise


@router.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    require_client_key(authorization)

    def _database_operation_1():
        with connect() as db:
            rows = db.execute(
                "SELECT DISTINCT alias FROM deployments WHERE enabled=1 ORDER BY alias"
            ).fetchall()
        return (rows,)

    (rows,) = await asyncio.to_thread(_database_operation_1)
    return {
        "object": "list",
        "data": [
            {
                "id": row["alias"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local-model-router",
            }
            for row in rows
        ],
    }


@router.post("/v1/chat/completions")
async def openai_chat(request: Request, authorization: str | None = Header(default=None)):
    return await proxy_request(request, "openai", authorization, "openai")


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    return await proxy_request(
        request, "anthropic", "Bearer " + x_api_key if x_api_key else authorization, "anthropic"
    )


async def close_stack(stack):
    with anyio.CancelScope(shield=True):
        await stack.aclose()


class OwnedStreamingResponse(StreamingResponse):
    def __init__(self, stack, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stack = stack

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await close_stack(self.stack)
