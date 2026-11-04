from __future__ import annotations
from pydantic import BaseModel, Field


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


class NetworkConfig(BaseModel):
    port: int = Field(strict=True, ge=1024, le=65535)


class ConnectionInput(BaseModel):
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    deployment_id: str | None = None
    models_url: str = ""
    proxy_url: str = ""
    use_system_proxy: bool = True
    timeout_seconds: float = Field(default=30, ge=1, le=600)


class RuleInput(BaseModel):
    enabled: bool = True
    cooldown_seconds: int = Field(default=90, ge=10, le=3600)
    failure_threshold: int = Field(default=3, ge=3, le=10)
    max_auto_factor: float = Field(default=1.5, ge=1, le=3)
    min_auto_factor: float = Field(default=0.25, ge=0.1, le=1)
