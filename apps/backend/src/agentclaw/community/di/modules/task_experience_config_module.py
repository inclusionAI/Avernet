"""DI provider for the ``task_experience.llm`` user_config block.

Mirrors ``BbsConfigModule.bbs_llm`` exactly: load the named block, reject
unknown keys, pull each typed field with safe fallbacks, and refuse a
half-configured block (``base_url`` set without ``secret_name`` or
``model``). The community-default user_config has no ``task_experience``
block, so the provider returns the all-empty
:class:`TaskExperienceLLMConfig` and the LLM stays disabled.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di.modules.config_module import _as_int, _block
from agentclaw.community.di.task_experience_config import (
    TaskExperienceLLMConfig,
)

__all__ = ["TaskExperienceConfigModule"]

_LLM_KEYS = frozenset(field.name for field in fields(TaskExperienceLLMConfig))

_TASK_EXPERIENCE_KEYS = frozenset({"llm"})


def reject_unknown_keys(
    block: dict[str, Any], *, where: str, allowed: set[str] | frozenset[str]
) -> None:
    unknown = sorted(str(key) for key in block if str(key) not in allowed)
    if unknown:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(
            f"unknown {where} key(s) {', '.join(repr(key) for key in unknown)}; "
            f"expected {allowed_text}"
        )


def _as_mapping(value: Any, *, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    return dict(value)


def _mapping(block: dict[str, Any], *, key: str, where: str) -> dict[str, Any]:
    return _as_mapping(block.get(key, {}), where=where)


def _string(block: dict[str, Any], key: str, fallback: str, where: str) -> str:
    if key not in block:
        return fallback
    value = block.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{where}.{key} must be a string")
    return value


def _positive_int(
    block: dict[str, Any], key: str, fallback: int, where: str
) -> int:
    if key not in block:
        return fallback
    try:
        value = _as_int(block.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}.{key} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{where}.{key} must be at least 1")
    return value


class TaskExperienceConfigModule(Module):
    """Bind the ``task_experience.llm`` user_config block."""

    @singleton
    @provider
    def task_experience_llm_config(self) -> TaskExperienceLLMConfig:
        default = TaskExperienceLLMConfig()
        block = _as_mapping(_block("task_experience"), where="task_experience")
        reject_unknown_keys(
            block, where="task_experience", allowed=_TASK_EXPERIENCE_KEYS
        )
        raw_block = _mapping(
            block, key="llm", where="task_experience.llm"
        )
        reject_unknown_keys(
            raw_block, where="task_experience.llm", allowed=_LLM_KEYS
        )

        base_url = _string(
            raw_block, "base_url", default.base_url, "task_experience.llm"
        )
        secret_name = _string(
            raw_block, "secret_name", default.secret_name, "task_experience.llm"
        )
        model = _string(raw_block, "model", default.model, "task_experience.llm")
        if base_url and (not secret_name.strip() or not model.strip()):
            raise ValueError(
                "task_experience.llm.secret_name and task_experience.llm.model "
                "must be non-empty when task_experience.llm.base_url is configured"
            )
        return TaskExperienceLLMConfig(
            base_url=base_url,
            secret_name=secret_name,
            model=model,
            timeout_ms=_positive_int(
                raw_block,
                "timeout_ms",
                default.timeout_ms,
                "task_experience.llm",
            ),
        )
