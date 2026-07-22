"""Configuration data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WebConfig:
    port: int = 8888
    start: str = "gateway.community.adapters.web.app:app"
    enable_api_docs: bool = True


@dataclass
class ModuleConfig:
    web: WebConfig | None = None


@dataclass
class LogConfig:
    trace_log_dir: str = ""
    log_level: str = "INFO"
    log_dir: str = ""


@dataclass
class Config:
    app_name: str = "gateway"
    enable_sidecar: bool = False
    workers: int = 1
    log_config: LogConfig = field(default_factory=LogConfig)
    module_config: ModuleConfig = field(default_factory=ModuleConfig)
    raw: dict = field(default_factory=dict)
