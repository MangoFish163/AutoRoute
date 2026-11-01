"""Built-in providers, persisted once when the settings entry is missing."""

DEFAULT_PROVIDER_PRESETS = {
    key: {
        "name": name,
        "avatar_url": "",
        "base_url": base_url,
        "models_url": f"{base_url}/models" if base_url else "",
        "provider": protocol,
        "base_weight": 1,
        "rpm": 0,
        "max_input_tokens": 0,
        "max_output_tokens": 0,
        "timeout_seconds": 90,
        "multimodal": multimodal,
        "thinking_mode": thinking,
        "enabled": True,
        "proxy_url": "",
        "use_system_proxy": True,
        "note": "",
    }
    for key, name, base_url, protocol, multimodal, thinking in (
        ("openai", "OpenAI", "https://api.openai.com/v1", "openai", True, False),
        ("anthropic", "Anthropic", "https://api.anthropic.com/v1", "anthropic", True, False),
        ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "openai", False, True),
        ("agnes", "Agnes", "https://apihub.agnes-ai.com/v1", "openai", True, True),
        ("mimo", "XiaoMi MIIMO", "", "openai", False, False),
        ("openrouter", "OpenRouter", "", "openai", False, False),
        ("bigmodel", "BigModel", "", "openai", False, False),
    )
}
