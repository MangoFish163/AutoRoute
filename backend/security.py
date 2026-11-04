from __future__ import annotations
import base64
import ctypes
import os
import secrets
from fastapi import HTTPException
from fastapi.responses import JSONResponse


import db
import re

_key_cache = None


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
    if not crypt.CryptProtectData(
        ctypes.byref(in_blob), "Local Model Router", None, None, None, 0, ctypes.byref(out_blob)
    ):
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
        raise RuntimeError(
            "This credential was encrypted on Windows and can only be decrypted there"
        )

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value[6:])
    in_buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    in_blob = DATA_BLOB(len(raw), in_buf)
    out_blob = DATA_BLOB()
    crypt = ctypes.windll.crypt32
    if not crypt.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise OSError("Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class ManagementAuthMiddleware:
    """Authenticate all management routes before parsing bodies or opening streams."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == "/api" or path.startswith("/api/")):
            headers = [
                value for name, value in scope["headers"] if name.lower() == b"x-management-token"
            ]
            expected = scope["app"].state.management_token.encode("utf-8")
            if (
                not expected
                or len(headers) != 1
                or not secrets.compare_digest(headers[0], expected)
            ):
                response = JSONResponse(
                    {"detail": "Invalid management token"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return

            async def no_cache_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() != b"cache-control"
                    ] + [(b"cache-control", b"no-store")]
                await send(message)

            await self.app(scope, receive, no_cache_send)
            return
        await self.app(scope, receive, send)


def client_key() -> str:
    global _key_cache
    path = db.BOOTSTRAP_KEY_PATH
    if _key_cache is None or _key_cache[0] != path:
        _key_cache = (path, path.read_text(encoding="utf-8").strip())
    return _key_cache[1]


def require_client_key(authorization: str | None) -> None:
    if not authorization or not secrets.compare_digest(
        authorization.removeprefix("Bearer "), client_key()
    ):
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid local API key", "type": "authentication_error"}},
        )


def redact(message: str, keys: list[str] | None = None) -> str:
    text = str(message or "")
    for key in keys or []:
        if key:
            text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"(?i)([?&](?:api[_-]?key|token|key|access_token)=)[^&\s]+", r"\1[REDACTED]", text
    )
    return text[:500]
