"""Unit tests for :class:`ChannelEngineOverridesReader`.

Locks the stage filtering, identity scoping, account mapping, and dedup behavior
that both the config-compose collector (draft) and the publish flow (verify/online)
depend on.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.channel.services.engine_overrides_reader import (
    ChannelEngineOverridesReader,
)
from agentclaw.community.core.channel.models import ChannelRecord

pytestmark = pytest.mark.unit

DRAFT_STAGES: set[str | None] = {None, "", "draft"}


def _record(
    *,
    client_id: str,
    stage: str | None,
    status: str = "1",
    config: dict | None = None,
    record_id: int = 1,
) -> ChannelRecord:
    cfg = {"client_id": client_id}
    if config:
        cfg.update(config)
    return ChannelRecord(
        id=record_id,
        type="dingding",
        description=None,
        identity_id="user1",
        bind_bot_id="bot1",
        config=cfg,
        status=status,
        deleted=0,
        gmt_create=datetime(2026, 6, 17),
        gmt_modified=datetime(2026, 6, 17),
        env="prod",
        stage=stage,
    )


def _reader(records: list[ChannelRecord]) -> tuple[ChannelEngineOverridesReader, MagicMock]:
    repo = MagicMock()
    repo.get_by_type_and_identity_ids.return_value = records
    return ChannelEngineOverridesReader(channel_repo=repo), repo


def _accounts(result: dict) -> list[dict]:
    return result["channels"]["dingding"]["accounts"]


# ── stage filtering ─────────────────────────────────────────────────────

def test_draft_set_selects_only_draft_and_no_stage_rows():
    records = [
        _record(client_id="draft-a", stage="draft", record_id=1),
        _record(client_id="none-b", stage=None, record_id=2),
        _record(client_id="empty-c", stage="", record_id=3),
        _record(client_id="verify-d", stage="verify", record_id=4),
        _record(client_id="online-e", stage="online", record_id=5),
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages=DRAFT_STAGES
    )
    ids = {a["client_id"] for a in _accounts(result)}
    assert ids == {"draft-a", "none-b", "empty-c"}


def test_verify_set_selects_only_verify_rows():
    records = [
        _record(client_id="draft-a", stage="draft", record_id=1),
        _record(client_id="verify-d", stage="verify", record_id=2),
        _record(client_id="online-e", stage="online", record_id=3),
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    assert {a["client_id"] for a in _accounts(result)} == {"verify-d"}


def test_online_set_selects_only_online_rows():
    records = [
        _record(client_id="verify-d", stage="verify", record_id=1),
        _record(client_id="online-e", stage="online", record_id=2),
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"online"}
    )
    assert {a["client_id"] for a in _accounts(result)} == {"online-e"}


def test_inactive_rows_excluded():
    records = [
        _record(client_id="active", stage="verify", status="1", record_id=1),
        _record(client_id="inactive", stage="verify", status="0", record_id=2),
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    assert {a["client_id"] for a in _accounts(result)} == {"active"}


# ── dedup + empty ───────────────────────────────────────────────────────

def test_dedup_by_client_id_keeps_first():
    records = [
        _record(client_id="dup", stage="verify", record_id=1),
        _record(client_id="dup", stage="verify", record_id=2),
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    assert len(_accounts(result)) == 1


def test_no_matching_rows_returns_empty_dict():
    records = [_record(client_id="online-e", stage="online", record_id=1)]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    assert result == {}


def test_no_records_returns_empty_dict():
    reader, _ = _reader([])
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages=DRAFT_STAGES
    )
    assert result == {}


# ── identity scoping + query shape ──────────────────────────────────────

def test_query_uses_user_and_shared_default_identity():
    reader, repo = _reader([])
    reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    repo.get_by_type_and_identity_ids.assert_called_once_with(
        type="dingding",
        identity_ids=["user1", "aideskdingding"],
        bind_bot_id="bot1",
    )


# ── account mapping shape ───────────────────────────────────────────────

def test_account_mapping_fields():
    records = [
        _record(
            client_id="cid",
            stage="verify",
            config={
                "enable_streaming_cards": True,
                "client_secret": "sec",
                "card_template_id": "tid",
                "card_template_key": "tkey",
            },
        )
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    account = _accounts(result)[0]
    assert account == {
        "client_id": "cid",
        "robot_code": "cid",
        "dm_policy": "open",
        "group_policy": "open",
        "message_type": "card",
        "enable_streaming_cards": True,
        "client_secret": "sec",
        "card_template_id": "tid",
        "card_template_key": "tkey",
    }


def test_account_mapping_omits_empty_optionals_and_defaults_message_type():
    records = [_record(client_id="cid", stage="verify")]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    account = _accounts(result)[0]
    assert account["message_type"] == "markdown"
    assert account["enable_streaming_cards"] is False
    assert "client_secret" not in account
    assert "card_template_id" not in account
    assert "card_template_key" not in account


def test_enabled_flag_present_when_accounts_exist():
    records = [_record(client_id="cid", stage="verify")]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages={"verify"}
    )
    assert result["channels"]["dingding"]["enabled"] is True


# ── binding mode ────────────────────────────────────────────────────────


def test_bcn_gateway_rows_are_skipped():
    """bcn_gateway 行不进 engine_overrides —— 凭证在 BCS 侧，不在引擎直连配置。"""
    records = [
        _record(
            client_id="bcn-1",
            stage="draft",
            config={"binding_mode": "bcn_gateway"},
        )
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages=DRAFT_STAGES
    )
    assert result == {}


def test_plugin_rows_still_delivered():
    """插件直连行（缺省或显式 binding_mode=plugin）照常进入 channels 载荷。"""
    records = [
        _record(client_id="implicit", stage=None),
        _record(
            client_id="explicit",
            stage="draft",
            config={"binding_mode": "plugin"},
        ),
    ]
    reader, _ = _reader(records)
    result = reader.overrides_for_stage(
        user_id="user1", bot_id="bot1", accept_stages=DRAFT_STAGES
    )
    assert "dingding" in result["channels"]
    assert result["channels"]["dingding"]["enabled"] is True
    assert {a["client_id"] for a in _accounts(result)} == {"implicit", "explicit"}
