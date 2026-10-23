"""Configuration parsing tests for the independent BBS periodic tasks."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.di.modules import bbs_config_module
from agentclaw.community.di.bbs_digest_config import (
    BbsContentImportTaskConfig,
    BbsDigestTaskConfig,
    BbsPollStatisticsTaskConfig,
    BbsTopicTagTaskConfig,
    BbsDingTalkConfig,
    BbsLLMConfig,
)
from agentclaw.community.di.modules.bbs_config_module import BbsConfigModule


def _patch_block(monkeypatch: pytest.MonkeyPatch, value: dict[str, Any]) -> None:
    monkeypatch.setattr(bbs_config_module, "_block", lambda _name: value)


def test_bbs_task_defaults_use_independent_intervals() -> None:
    assert BbsDigestTaskConfig() == BbsDigestTaskConfig(
        enabled=False,
        interval=300,
        jitter=5,
        lock_ttl=240,
        heartbeat=45,
    )
    assert BbsPollStatisticsTaskConfig() == BbsPollStatisticsTaskConfig(
        enabled=True,
        interval=300,
        jitter=5,
        lock_ttl=240,
        heartbeat=45,
    )
    assert BbsTopicTagTaskConfig() == BbsTopicTagTaskConfig(
        enabled=True,
        interval=7200,
        jitter=30,
        lock_ttl=240,
        heartbeat=45,
    )
    assert BbsContentImportTaskConfig() == BbsContentImportTaskConfig(
        enabled=False,
        interval=60,
        jitter=5,
        lock_ttl=240,
        heartbeat=45,
    )


def test_bbs_config_module_parses_independent_task_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(
        monkeypatch,
        {
            "digest_task": {
                "enabled": True,
                "interval": "120",
                "jitter": 7,
                "lock_ttl": 180,
                "heartbeat": 30,
            },
            "poll_statistics_task": {
                "enabled": False,
                "interval": 45,
                "jitter": 0,
                "lock_ttl": 90,
                "heartbeat": 20,
            },
            "topic_tag_task": {
                "enabled": False,
                "interval": 7200,
                "jitter": 10,
                "lock_ttl": 180,
                "heartbeat": 30,
            },
            "content_import_task": {
                "enabled": False,
                "interval": 30,
                "jitter": 0,
                "lock_ttl": 60,
                "heartbeat": 15,
            },
            "llm": {},
        },
    )

    module = BbsConfigModule()

    assert module.bbs_digest_task() == BbsDigestTaskConfig(
        enabled=True,
        interval=120,
        jitter=7,
        lock_ttl=180,
        heartbeat=30,
    )
    assert module.bbs_poll_statistics_task() == BbsPollStatisticsTaskConfig(
        enabled=False,
        interval=45,
        jitter=0,
        lock_ttl=90,
        heartbeat=20,
    )
    assert module.bbs_topic_tag_task() == BbsTopicTagTaskConfig(
        enabled=False,
        interval=7200,
        jitter=10,
        lock_ttl=180,
        heartbeat=30,
    )
    assert module.bbs_content_import_task() == BbsContentImportTaskConfig(
        enabled=False,
        interval=30,
        jitter=0,
        lock_ttl=60,
        heartbeat=15,
    )


def test_bbs_config_module_rejects_unknown_task_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(monkeypatch, {"digest_task": {"enabled": True, "typo": 1}})

    with pytest.raises(ValueError, match=r"unknown bbs.digest_task key"):
        BbsConfigModule().bbs_digest_task()


def test_bbs_config_module_requires_heartbeat_shorter_than_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(
        monkeypatch,
        {"content_import_task": {"heartbeat": 60, "lock_ttl": 60}},
    )

    with pytest.raises(ValueError, match=r"heartbeat must be less than lock_ttl"):
        BbsConfigModule().bbs_content_import_task()


def test_bbs_dingtalk_config_is_parsed_without_a_literal_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(
        monkeypatch,
        {
            "dingtalk": {
                "ak_id": "bbs-ak-id",
                "ak_secret_name": "bbs_dingtalk_ak_secret",
                "robot_code": "bbs-robot-code",
                "card_template_id": "bbs-card-template.schema",
            }
        },
    )

    assert BbsConfigModule().bbs_dingtalk() == BbsDingTalkConfig(
        ak_id="bbs-ak-id",
        ak_secret_name="bbs_dingtalk_ak_secret",
        robot_code="bbs-robot-code",
        card_template_id="bbs-card-template.schema",
    )


def test_bbs_dingtalk_config_rejects_partial_card_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(
        monkeypatch,
        {
            "dingtalk": {
                "ak_id": "bbs-ak-id",
            }
        },
    )

    with pytest.raises(ValueError, match="partially configured"):
        BbsConfigModule().bbs_dingtalk()


def test_bbs_llm_config_keeps_bbs_secret_separate_from_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(
        monkeypatch,
        {
            "llm": {
                "base_url": "${BBS_LLM_BASE_URL:-}",
                "secret_name": "BBS_LLM_AUTH_TOKEN",
                "model": "glm-5.2",
                "timeout_ms": "180000",
            }
        },
    )

    config = BbsConfigModule().bbs_llm()

    assert config == BbsLLMConfig(
        base_url="${BBS_LLM_BASE_URL:-}",
        secret_name="BBS_LLM_AUTH_TOKEN",
        model="glm-5.2",
        timeout_ms=180_000,
    )


def test_bbs_llm_config_rejects_incomplete_live_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_block(
        monkeypatch,
        {"llm": {"base_url": "http://localhost:8000", "model": ""}},
    )

    with pytest.raises(ValueError, match="secret_name and bbs.llm.model"):
        BbsConfigModule().bbs_llm()
