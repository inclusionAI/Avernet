"""Tests for the copy_state field: BotCopyStateSummary derive + Bot schema field.

`build_copy_state_summary` joins the ac_bot_copy ledger row with the dst's
recent apply records and wraps compute_copy_status into the FE-facing summary.
`Bot` schema gains an additive nullable `copy_state` (default None) so every
existing bot endpoint returns null without code changes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.adapters.http.openapi_v1.bots.schemas import Bot
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.triggers import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_copy.status_derive import (
    build_copy_state_summary,
)
from agentclaw.community.core.bot_copy.types import (
    BotCopyRow,
    BotCopyStateSummary,
    BotCopyStatus,
)

ENV = "dev"
TENANT = "teamclaw"


def _apply(trigger, status) -> SimpleNamespace:
    return SimpleNamespace(trigger=trigger, status=status)


def _copy_row() -> BotCopyRow:
    return BotCopyRow(
        operation_id="op_1",
        source_bot_id="src_1",
        source_owner_id="u1",
        dst_bot_id="d1",
        dst_owner_id="u1",
        operator_id="u1",
        status=BotCopyStatus.INITIAL,
        env=ENV,
        avernet_tenant=TENANT,
    )


def test_build_copy_state_summary_returns_none_when_no_copy_row():
    bot_copy_repo = MagicMock()
    apply_repo = MagicMock()
    bot_copy_repo.get_by_dst_bot_id.return_value = None
    assert (
        build_copy_state_summary(
            bot_copy_repo=bot_copy_repo,
            apply_repo=apply_repo,
            env=ENV,
            entity_id="u1",
            bot_id="legacy_bot",
            bot_status="ACTIVE",
        )
        is None
    )
    apply_repo.recent.assert_not_called()


def test_build_copy_state_summary_returns_none_when_converged():
    # Once the copy has converged the dst is an ordinary bot — copy_state is
    # dropped to null so FE stops showing copy progress / the retry button.
    # (compute_copy_status itself still returns CONVERGED; the null collapse is
    # build_copy_state_summary's job.)
    bot_copy_repo = MagicMock()
    apply_repo = MagicMock()
    bot_copy_repo.get_by_dst_bot_id.return_value = _copy_row()
    apply_repo.recent.return_value = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
    ]
    summary = build_copy_state_summary(
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        env=ENV,
        entity_id="u1",
        bot_id="d1",
        bot_status="ACTIVE",
    )
    assert summary is None


def test_build_copy_state_summary_returns_summary_with_operation_id_and_derived_status():
    bot_copy_repo = MagicMock()
    apply_repo = MagicMock()
    bot_copy_repo.get_by_dst_bot_id.return_value = _copy_row()
    # on_container RUNNING → BOT_STARTED (non-terminal, so summary is kept).
    apply_repo.recent.return_value = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.RUNNING.value),
    ]
    summary = build_copy_state_summary(
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        env=ENV,
        entity_id="u1",
        bot_id="d1",
        bot_status="ACTIVE",
    )
    assert isinstance(summary, BotCopyStateSummary)
    assert summary.operation_id == "op_1"
    assert summary.status is BotCopyStatus.BOT_STARTED
    assert summary.source_bot_id == "src_1"
    apply_repo.recent.assert_called_once_with(
        env=ENV, entity_id="u1", bot_id="d1", limit=10
    )


def test_build_copy_state_summary_propagates_failed_from_pre_container():
    bot_copy_repo = MagicMock()
    apply_repo = MagicMock()
    bot_copy_repo.get_by_dst_bot_id.return_value = _copy_row()
    apply_repo.recent.return_value = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.FAILED.value),
    ]
    summary = build_copy_state_summary(
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        env=ENV,
        entity_id="u1",
        bot_id="d1",
        bot_status="PENDING",
    )
    assert summary.status is BotCopyStatus.FAILED


def test_bot_schema_has_nullable_copy_state_defaulting_none():
    # Additive field: every existing bot endpoint that builds a Bot without
    # copy_state returns null — no code change required there.
    bot = Bot(
        bot_id="b1",
        bot_name="n",
        bot_desc="",
        engine="openclaw",
        cluster_name="ACRA",
        bot_type="personal",
        status="ACTIVE",
        owner_entity_id="u1",
    )
    assert bot.copy_state is None


def test_bot_schema_accepts_copy_state_summary():
    bot = Bot(
        bot_id="b1",
        bot_name="n",
        bot_desc="",
        engine="openclaw",
        cluster_name="ACRA",
        bot_type="personal",
        status="ACTIVE",
        owner_entity_id="u1",
        copy_state=BotCopyStateSummary(
            operation_id="op_1",
            status=BotCopyStatus.FILE_SYNCED,
            source_bot_id="src_1",
        ),
    )
    assert bot.copy_state is not None
    assert bot.copy_state.operation_id == "op_1"
    assert bot.copy_state.status is BotCopyStatus.FILE_SYNCED
