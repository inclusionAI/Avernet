"""DI providers for backend-owned BBS task and model configuration blocks."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di.bbs_digest_config import (
    BbsContentImportTaskConfig,
    BbsDigestTaskConfig,
    BbsPollStatisticsTaskConfig,
    BbsTopicTagTaskConfig,
    BbsDingTalkConfig,
    BbsLLMConfig,
    BbsTaskConfig,
)
from agentclaw.community.di.modules.config_module import _as_bool, _as_int, _block

_TASK_KEYS = frozenset(field.name for field in fields(BbsTaskConfig))
_LLM_KEYS = frozenset(field.name for field in fields(BbsLLMConfig))
_DINGTALK_KEYS = frozenset(field.name for field in fields(BbsDingTalkConfig))
_BBS_KEYS = frozenset(
    {
        "digest_task",
        "poll_statistics_task",
        "topic_tag_task",
        "content_import_task",
        "llm",
        "dingtalk",
    }
)


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


class BbsConfigModule(Module):
    """Bind typed settings for the independent BBS background tasks."""

    @singleton
    @provider
    def bbs_digest_task(self) -> BbsDigestTaskConfig:
        return self._task_config(BbsDigestTaskConfig, block_name="digest_task")

    @singleton
    @provider
    def bbs_poll_statistics_task(self) -> BbsPollStatisticsTaskConfig:
        return self._task_config(
            BbsPollStatisticsTaskConfig,
            block_name="poll_statistics_task",
        )

    @singleton
    @provider
    def bbs_topic_tag_task(self) -> BbsTopicTagTaskConfig:
        return self._task_config(
            BbsTopicTagTaskConfig,
            block_name="topic_tag_task",
        )

    @singleton
    @provider
    def bbs_content_import_task(self) -> BbsContentImportTaskConfig:
        return self._task_config(
            BbsContentImportTaskConfig,
            block_name="content_import_task",
        )

    @singleton
    @provider
    def bbs_dingtalk(self) -> BbsDingTalkConfig:
        default = BbsDingTalkConfig()
        bbs_block = _as_mapping(_block("bbs"), where="bbs")
        reject_unknown_keys(bbs_block, where="bbs", allowed=_BBS_KEYS)
        raw_block = _mapping(bbs_block, key="dingtalk", where="bbs.dingtalk")
        reject_unknown_keys(raw_block, where="bbs.dingtalk", allowed=_DINGTALK_KEYS)

        values = {
            key: _required_block_string(raw_block, key, "bbs.dingtalk")
            for key in _DINGTALK_KEYS
        }
        configured = [key for key, value in values.items() if value]
        if configured and len(configured) != len(values):
            missing = ", ".join(
                key for key in sorted(values) if not values[key]
            )
            raise ValueError(
                "bbs.dingtalk is partially configured; missing "
                f"{missing}. Configure all account fields, or remove the "
                "block to disable BBS card delivery"
            )
        if not configured:
            return default
        return BbsDingTalkConfig(**values)

    @singleton
    @provider
    def bbs_llm(self) -> BbsLLMConfig:
        default = BbsLLMConfig()
        bbs_block = _as_mapping(_block("bbs"), where="bbs")
        raw_block = _mapping(bbs_block, key="llm", where="bbs.llm")
        reject_unknown_keys(raw_block, where="bbs.llm", allowed=_LLM_KEYS)

        base_url = _string(raw_block, "base_url", default.base_url, "bbs.llm")
        secret_name = _string(raw_block, "secret_name", default.secret_name, "bbs.llm")
        model = _string(raw_block, "model", default.model, "bbs.llm")
        if base_url and (not secret_name.strip() or not model.strip()):
            raise ValueError(
                "bbs.llm.secret_name and bbs.llm.model must be non-empty when "
                "bbs.llm.base_url is configured"
            )
        return BbsLLMConfig(
            base_url=base_url,
            secret_name=secret_name,
            model=model,
            timeout_ms=_positive_int(
                raw_block,
                "timeout_ms",
                default.timeout_ms,
                "bbs.llm",
            ),
        )

    def _task_config(
        self,
        config_type: type[BbsTaskConfig],
        *,
        block_name: str,
    ) -> BbsTaskConfig:
        default = config_type()
        bbs_block = _as_mapping(_block("bbs"), where="bbs")
        reject_unknown_keys(bbs_block, where="bbs", allowed=_BBS_KEYS)
        block = _mapping(bbs_block, key=block_name, where=f"bbs.{block_name}")
        reject_unknown_keys(block, where=f"bbs.{block_name}", allowed=_TASK_KEYS)

        enabled = _bool(block, "enabled", default.enabled, f"bbs.{block_name}")
        interval = _positive_int(
            block,
            "interval",
            default.interval,
            f"bbs.{block_name}",
        )
        jitter = _nonnegative_int(
            block,
            "jitter",
            default.jitter,
            f"bbs.{block_name}",
        )
        lock_ttl = _positive_int(
            block,
            "lock_ttl",
            default.lock_ttl,
            f"bbs.{block_name}",
        )
        heartbeat = _positive_int(
            block,
            "heartbeat",
            default.heartbeat,
            f"bbs.{block_name}",
        )
        if heartbeat >= lock_ttl:
            raise ValueError(
                f"bbs.{block_name}.heartbeat must be less than lock_ttl "
                f"({heartbeat} >= {lock_ttl})"
            )
        return config_type(
            enabled=enabled,
            interval=interval,
            jitter=jitter,
            lock_ttl=lock_ttl,
            heartbeat=heartbeat,
        )


def _mapping(block: dict[str, Any], *, key: str, where: str) -> dict[str, Any]:
    return _as_mapping(block.get(key, {}), where=where)


def _as_mapping(value: Any, *, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    return dict(value)


def _bool(block: dict[str, Any], key: str, fallback: bool, where: str) -> bool:
    if key not in block:
        return fallback
    try:
        return _as_bool(block.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}.{key} must be a boolean") from exc


def _positive_int(block: dict[str, Any], key: str, fallback: int, where: str) -> int:
    return _bounded_int(block, key, fallback, where, minimum=1)


def _nonnegative_int(block: dict[str, Any], key: str, fallback: int, where: str) -> int:
    return _bounded_int(block, key, fallback, where, minimum=0)


def _bounded_int(
    block: dict[str, Any],
    key: str,
    fallback: int,
    where: str,
    *,
    minimum: int,
) -> int:
    if key not in block:
        return fallback
    try:
        value = _as_int(block.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}.{key} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{where}.{key} must be at least {minimum}")
    return value


def _string(block: dict[str, Any], key: str, fallback: str, where: str) -> str:
    if key not in block:
        return fallback
    value = block.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{where}.{key} must be a string")
    return value


def _required_block_string(
    block: dict[str, Any], key: str, where: str
) -> str:
    if key not in block:
        return ""
    value = _string(block, key, "", where)
    if not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string")
    return value.strip()


__all__ = ["BbsConfigModule", "reject_unknown_keys"]
