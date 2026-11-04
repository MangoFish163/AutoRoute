from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit
import httpx


def endpoint(base_url: str, provider: str, protocol: str, full_url: bool = False) -> str:
    base = base_url.rstrip("/")
    if full_url or (
        provider in ("openai", "responses") and urlsplit(base).path.endswith("/responses")
    ):
        return base
    if provider == "responses":
        return base + ("/responses" if urlsplit(base).path else "/v1/responses")
    if provider == "anthropic":
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    # Preserve vendor prefixes such as Ark's /api/v3.
    return base + ("/chat/completions" if urlsplit(base).path else "/v1/chat/completions")


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
    # The checkbox is the master switch, including a previously saved custom URL.
    if not dep.get("use_system_proxy", True):
        return None
    custom = str(dep.get("proxy_url") or "").strip()
    return custom or windows_system_proxy()


def proxy_options(dep: dict[str, Any]) -> dict[str, Any]:
    proxy = proxy_for(dep)
    # Only consult environment defaults when enabled and no explicit proxy was found.
    # Direct connections must ignore HTTP_PROXY/HTTPS_PROXY/ALL_PROXY entirely.
    return {"proxy": proxy, "trust_env": bool(dep.get("use_system_proxy", True)) and proxy is None}


from collections import OrderedDict
from contextlib import asynccontextmanager


class ClientPool:
    """Keep idle clients bounded; leases protect clients still streaming."""

    def __init__(self):
        self.active = False
        self.clients = OrderedDict()

    @asynccontextmanager
    async def lease(self, dep):
        options = proxy_options(dep)
        if not self.active:
            async with httpx.AsyncClient(
                timeout=dep.get("timeout_seconds", 90), **options
            ) as client:
                yield client
            return
        env = (
            tuple(
                os.environ.get(k, "")
                for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
            )
            if options["trust_env"]
            else ()
        )
        key = (options["proxy"], options["trust_env"], env)
        entry = self.clients.get(key)
        if entry is None:
            entry = [httpx.AsyncClient(timeout=90, **options), 0]
            self.clients[key] = entry
        self.clients.move_to_end(key)
        entry[1] += 1
        try:
            yield entry[0]
        finally:
            entry[1] -= 1
            for old, value in list(self.clients.items()):
                if len(self.clients) <= 16:
                    break
                if value[1] == 0:
                    self.clients.pop(old)
                    await value[0].aclose()

    async def close(self):
        self.active = False
        entries = list(self.clients.values())
        self.clients.clear()
        for client, _ in entries:
            await client.aclose()


client_pool = ClientPool()
