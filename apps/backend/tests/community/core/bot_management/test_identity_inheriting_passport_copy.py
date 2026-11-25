"""An identity-inheriting Bot carries its source's passport verbatim.

This is the property the whole design rests on: the child stores
``ext.passport`` exactly as an issued Bot would, so every downstream consumer
(``resolve_agent_code``, the CLI scope reconciler, the passport endpoint) stays
unaware that the identity was borrowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.create_context import (
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
)
from agentclaw.community.core.bot_management.create_flow import (
    complete_bot_authorization,
)

SOURCE_CODE = "source-agent-code"


@dataclass
class _SpecHolder:
    identity_bot_id: str | None = "source"


def _spec(identity_bot_id: str | None) -> BotCreateSpec:
    return BotCreateSpec(
        entity_id="owner-1",
        entity_type="staff",
        engine_type="openclaw",
        bot_type="personal",
        bot_name="child",
        bot_desc="desc",
        share_policy={},
        identity_bot_id=identity_bot_id,
    )


def _context() -> BotCreateContext:
    return BotCreateContext(
        deployment_mode=BotCreateDeploymentMode.CLOUD,
        space_kind="personal",
        space_quota=False,
    )


def _bot_service(source_passport: dict) -> MagicMock:
    svc = MagicMock()
    svc.create_bot.return_value = {"bot_id": "child"}
    svc.get_identity_source_row.return_value = {
        "bot_id": "source",
        "owner_id": "owner-1",
        "ext": {"passport": source_passport},
    }
    return svc


class TestTheChildCarriesTheSourcesPassport:
    def test_the_childs_ext_holds_the_sources_agent_code_and_is_issued(self):
        svc = _bot_service({"agent_code": SOURCE_CODE, "status": "ISSUED"})
        complete_bot_authorization(
            user_id="owner-1",
            nick_name="owner-1",
            bot_id="child",
            spec=_spec("source"),
            context=_context(),
            bot_service=svc,
            passport_plugin=MagicMock(),  # never consulted
            auth_rel_plugin=MagicMock(),
            authorized=True,
        )
        ext = svc.create_bot.call_args.kwargs["ext"]
        assert ext["passport"]["agent_code"] == SOURCE_CODE
        assert ext["passport"]["status"] == "ISSUED"

    def test_the_child_never_queries_its_own_passport(self):
        """An inheriting Bot applied for no passport: the poll must not run."""
        svc = _bot_service({"agent_code": SOURCE_CODE, "status": "ISSUED"})
        passport = MagicMock()
        complete_bot_authorization(
            user_id="owner-1", nick_name="owner-1", bot_id="child",
            spec=_spec("source"), context=_context(),
            bot_service=svc, passport_plugin=passport,
            auth_rel_plugin=MagicMock(), authorized=True,
        )
        passport.query_auth_status.assert_not_called()
        passport.query_agent_passport.assert_not_called()

    def test_the_row_records_which_bot_it_inherits_from(self):
        svc = _bot_service({"agent_code": SOURCE_CODE})
        complete_bot_authorization(
            user_id="owner-1", nick_name="owner-1", bot_id="child",
            spec=_spec("source"), context=_context(),
            bot_service=svc, passport_plugin=MagicMock(),
            auth_rel_plugin=MagicMock(), authorized=True,
        )
        assert svc.create_bot.call_args.kwargs["identity_bot_id"] == "source"

    def test_a_source_with_no_agent_code_is_loud_at_creation(self):
        """Better to fail here than to start a Bot with no identity."""
        svc = _bot_service({})
        with pytest.raises(Exception) as exc:
            complete_bot_authorization(
                user_id="owner-1", nick_name="owner-1", bot_id="child",
                spec=_spec("source"), context=_context(),
                bot_service=svc, passport_plugin=MagicMock(),
                auth_rel_plugin=MagicMock(), authorized=True,
            )
        assert "agent code" in str(exc.value)
        svc.create_bot.assert_not_called()

    def test_an_ordinary_bot_is_unchanged(self):
        """The ordinary path still polls and records its own issued code."""
        svc = _bot_service({})
        passport = MagicMock()
        passport.query_auth_status.return_value = {"status": "ISSUED"}
        passport.query_agent_passport.return_value = {"agent_code": "own-code"}
        complete_bot_authorization(
            user_id="owner-1", nick_name="owner-1", bot_id="child",
            spec=_spec(None), context=_context(),
            bot_service=svc, passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
        )
        passport.query_auth_status.assert_called_once()
        ext = svc.create_bot.call_args.kwargs["ext"]
        assert ext["passport"]["agent_code"] == "own-code"