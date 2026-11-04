"""Validate client-owned nested values before routing or credential selection.

Unknown block types remain a deployment compatibility decision. Known fields
are checked here even for native passthrough requests, without logging content.
"""

import json


def object_value(value, path):
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def string_value(value, path):
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def content_value(value, path, depth=0):
    if depth > 20:
        raise ValueError("Content nesting exceeds 20 levels")
    if value is None or isinstance(value, str):
        return
    if not isinstance(value, list):
        raise ValueError(f"{path} must be text or an array of content blocks")
    for index, block in enumerate(value):
        location = f"{path}[{index}]"
        object_value(block, location)
        kind = string_value(block.get("type"), f"{location}.type")
        if kind == "text":
            string_value(block.get("text"), f"{location}.text")
        elif kind == "image_url":
            image = block.get("image_url")
            if isinstance(image, str):
                continue
            object_value(image, f"{location}.image_url")
            string_value(image.get("url"), f"{location}.image_url.url")
        elif kind == "image":
            source = object_value(block.get("source"), f"{location}.source")
            source_type = string_value(source.get("type"), f"{location}.source.type")
            if source_type not in ("url", "base64"):
                raise ValueError(f"{location}.source.type must be url or base64")
            for field in {"url": ("url",), "base64": ("media_type", "data")}.get(source_type, ()):
                string_value(source.get(field), f"{location}.source.{field}")
        elif kind == "tool_use":
            for field in ("id", "name"):
                string_value(block.get(field), f"{location}.{field}")
            object_value(block.get("input"), f"{location}.input")
        elif kind == "tool_result":
            string_value(block.get("tool_use_id"), f"{location}.tool_use_id")
            if "content" in block and block["content"] is None:
                raise ValueError(f"{location}.content must be text or an array of content blocks")
            content_value(block.get("content", ""), f"{location}.content", depth + 1)


def validate_payload(payload, inbound):
    object_value(payload, "Request body")
    model = string_value(payload.get("model"), "model")
    if not model.strip():
        raise ValueError("model must be a nonempty string")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages must be an array")
    for index, message in enumerate(messages):
        path = f"messages[{index}]"
        object_value(message, path)
        string_value(message.get("role"), f"{path}.role")
        content_value(message.get("content", ""), f"{path}.content")
        if message.get("role") == "tool":
            string_value(message.get("tool_call_id"), f"{path}.tool_call_id")
        if "tool_calls" in message:
            calls = message["tool_calls"]
            if not isinstance(calls, list):
                raise ValueError(f"{path}.tool_calls must be an array")
            for i, call in enumerate(calls):
                location = f"{path}.tool_calls[{i}]"
                object_value(call, location)
                string_value(call.get("id"), f"{location}.id")
                fn = object_value(call.get("function"), f"{location}.function")
                string_value(fn.get("name"), f"{location}.function.name")
                arguments = string_value(fn.get("arguments"), f"{location}.function.arguments")
                try:
                    parsed = json.loads(arguments)
                except (ValueError, RecursionError):
                    raise ValueError(
                        f"{location}.function.arguments must be a JSON object"
                    ) from None
                object_value(parsed, f"{location}.function.arguments")
    if "system" in payload:
        content_value(payload["system"], "system")
    if "tools" in payload:
        if not isinstance(payload["tools"], list):
            raise ValueError("tools must be an array")
        for index, tool in enumerate(payload["tools"]):
            path = f"tools[{index}]"
            object_value(tool, path)
            fn = (
                object_value(tool.get("function"), f"{path}.function")
                if inbound == "openai"
                else tool
            )
            string_value(fn.get("name"), f"{path}.name")
            field = "parameters" if inbound == "openai" else "input_schema"
            if field in fn:
                object_value(fn[field], f"{path}.{field}")
    if "stream_options" in payload:
        object_value(payload["stream_options"], "stream_options")
    if "tool_choice" in payload:
        choice = payload["tool_choice"]
        if inbound == "anthropic":
            object_value(choice, "tool_choice")
            kind = string_value(choice.get("type"), "tool_choice.type")
            if kind == "tool":
                string_value(choice.get("name"), "tool_choice.name")
        elif not isinstance(choice, str):
            object_value(choice, "tool_choice")
            if "function" in choice:
                fn = object_value(choice["function"], "tool_choice.function")
                string_value(fn.get("name"), "tool_choice.function.name")
