"""Standalone Pydantic config models — only fields baas code actually accesses.

Used by ``ConfigLoader`` in both SOFA mode (pass-through) and baas mode
(direct YAML deserialisation).  NOT full parity with SOFA's ``Config`` —
only the subset consumed by ``src/secbaas/``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LogConfig(BaseModel):
    trace_log_dir: str = ""
    log_level: str = "INFO"
    log_dir: str = ""


class WebConfig(BaseModel):
    port: int = Field(default=8888, ge=1, le=65535)
    workers: int = Field(default=8, ge=1)
    threads: int = Field(default=8, ge=1)
    start: str = ""


class ModuleConfig(BaseModel):
    web: WebConfig = Field(default_factory=WebConfig)


class UserConfig(BaseModel, extra="allow"):
    """User-facing configuration namespace — keys vary by deployment.

    ``extra="allow"`` is intentional: baas code accesses arbitrary nested
    keys via dict-like lookups (``.get()``, ``[]``).
    """

    model_config = {"extra": "allow"}

    def get(self, key: str, default=None):
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


class Config(BaseModel):
    app_name: str = "secbaas"
    workers: int = Field(default=8, ge=1)
    module_config: ModuleConfig = Field(default_factory=ModuleConfig)
    user_config: UserConfig = Field(default_factory=UserConfig)
    log_config: LogConfig = Field(default_factory=LogConfig)

    @staticmethod
    def merge_configs(base: dict, overlay: dict) -> dict:
        """Deep-merge ``overlay`` into ``base`` (matching SOFA behaviour)."""
        result = dict(base)
        for key, value in overlay.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = Config.merge_configs(result[key], value)
            else:
                result[key] = value
        return result
