from __future__ import annotations
import base64
import json
import time
import uuid
from typing import Any


class IncompatibleRequest(ValueError):
    pass


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or any(p.get("type") != "text" for p in content):
        raise IncompatibleRequest("System messages support text blocks only")
    return "\n\n".join(p.get("text", "") for p in content)


def image_to_anthropic(image):
    url = image if isinstance(image, str) else image.get("url", "")
    if url.startswith("data:"):
        try:
            meta, data = url[5:].split(",", 1)
            media, encoding = meta.split(";")
            if encoding != "base64" or media not in (
                "image/png",
                "image/jpeg",
                "image/gif",
                "image/webp",
            ):
                raise ValueError()
            base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            raise IncompatibleRequest("Invalid base64 image")
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    if not url.startswith(("https://", "http://")):
        raise IncompatibleRequest("Image URL must use HTTP(S) or base64 data URL")
    return {"type": "image", "source": {"type": "url", "url": url}}


def anthropic_blocks(content):
    if isinstance(content, str) or content is None:
        return content or ""
    result = []
    for part in content:
        if part.get("type") == "text":
            result.append(dict(part))
        elif part.get("type") == "image_url":
            result.append(image_to_anthropic(part.get("image_url", {})))
        else:
            raise IncompatibleRequest("Unsupported content for Anthropic deployment")
    return result


def openai_blocks(content):
    if isinstance(content, str):
        return content
    result = []
    for part in content:
        kind = part.get("type")
        if kind == "text":
            result.append({"type": "text", "text": part.get("text", "")})
        elif kind == "image":
            source = part.get("source", {})
            if source.get("type") == "url":
                url = source.get("url", "")
            elif source.get("type") == "base64":
                url = f"data:{source.get('media_type', '')};base64,{source.get('data', '')}"
            else:
                raise IncompatibleRequest("Unsupported image source")
            image_to_anthropic(url)
            result.append({"type": "image_url", "image_url": {"url": url}})
        elif kind not in ("tool_use", "tool_result"):
            raise IncompatibleRequest("Unsupported content for OpenAI deployment")
    return result


def to_anthropic(payload: dict[str, Any], model: str) -> dict[str, Any]:
    messages = payload.get("messages", [])
    system = []
    converted = []
    for msg in messages:
        role, content = msg.get("role", "user"), msg.get("content", "")
        if role == "system":
            system.append(text_content(content))
        elif role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": content,
                        }
                    ],
                }
            )
        elif role == "assistant" and msg.get("tool_calls"):
            blocks = []
            if content:
                blocks.extend(
                    anthropic_blocks(content)
                    if isinstance(content, list)
                    else [{"type": "text", "text": content}]
                )
            for call in msg["tool_calls"]:
                fn = call.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    raise IncompatibleRequest("Tool arguments must be a JSON object")
                if not isinstance(args, dict):
                    raise IncompatibleRequest("Tool arguments must be a JSON object")
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id", "toolu_" + uuid.uuid4().hex[:12]),
                        "name": fn.get("name", "tool"),
                        "input": args,
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": anthropic_blocks(content),
                }
            )
    out = {
        "model": model,
        "messages": converted,
        "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024,
        "stream": bool(payload.get("stream", False)),
    }
    if system:
        out["system"] = "\n\n".join(system)
    for key in ("temperature", "top_p", "stop_sequences", "metadata"):
        if key in payload:
            out[key] = payload[key]
    if "tools" in payload:
        out["tools"] = [
            {
                "name": t.get("function", {}).get("name", "tool"),
                "description": t.get("function", {}).get("description", ""),
                "input_schema": t.get("function", {}).get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
            for t in payload["tools"]
        ]
    return out


def to_openai(payload: dict[str, Any], model: str) -> dict[str, Any]:
    messages = []
    if payload.get("system"):
        messages.append({"role": "system", "content": text_content(payload["system"])})
    for msg in payload.get("messages", []):
        content = msg.get("content", "")
        tool_calls = []
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
            tool_results = [part for part in content if part.get("type") == "tool_result"]
            tool_uses = [part for part in content if part.get("type") == "tool_use"]
            if tool_results:
                for result in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.get("tool_use_id", ""),
                            "content": openai_blocks(result.get("content", "")),
                        }
                    )
            converted_content = openai_blocks(content)
            content = (
                converted_content
                if any(p["type"] == "image_url" for p in converted_content)
                else "".join(p["text"] for p in converted_content)
            )
            tool_calls = [
                {
                    "id": part.get("id"),
                    "type": "function",
                    "function": {
                        "name": part.get("name"),
                        "arguments": json.dumps(part.get("input", {})),
                    },
                }
                for part in tool_uses
            ]
            if tool_results and not content and not tool_calls:
                continue
        message = {"role": msg.get("role", "user"), "content": content or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
    out = {
        "model": model,
        "messages": messages,
        "stream": bool(payload.get("stream", False)),
        "max_tokens": payload.get("max_tokens", 1024),
    }
    for key in ("temperature", "top_p", "stop", "tools", "tool_choice", "response_format"):
        if key in payload:
            out[key] = payload[key]
    if "tools" in payload and payload["tools"] and payload["tools"][0].get("name"):
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in payload["tools"]
        ]
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
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Upstream tool arguments must be valid JSON") from exc
        if not isinstance(args, dict):
            raise ValueError("Upstream tool arguments must be a JSON object")
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id", "toolu_" + uuid.uuid4().hex[:12]),
                "name": fn.get("name", "tool"),
                "input": args,
            }
        )
    usage = data.get("usage", {})
    return {
        "id": data.get("id", "msg_" + uuid.uuid4().hex),
        "type": "message",
        "role": "assistant",
        "model": model_alias,
        "content": content,
        "stop_reason": "tool_use"
        if msg.get("tool_calls")
        else ("max_tokens" if choice.get("finish_reason") == "length" else "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def anthropic_to_openai_response(data: dict[str, Any], model_alias: str) -> dict[str, Any]:
    content = "".join(
        part.get("text", "") for part in data.get("content", []) if part.get("type") == "text"
    )
    calls = []
    for part in data.get("content", []):
        if part.get("type") == "tool_use":
            calls.append(
                {
                    "id": part.get("id"),
                    "type": "function",
                    "function": {
                        "name": part.get("name"),
                        "arguments": json.dumps(part.get("input", {})),
                    },
                }
            )
    usage = data.get("usage", {})
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": data.get("id", "chatcmpl-" + uuid.uuid4().hex),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_alias,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls"
                if calls
                else ("length" if data.get("stop_reason") == "max_tokens" else "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }
