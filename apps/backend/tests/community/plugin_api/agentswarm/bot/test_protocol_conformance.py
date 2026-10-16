"""BotPlatform protocol surface conformance — pin the seam so contract
changes are deliberate. Contract changes require matching conformance
tests; this file is that pair for the provision seam (spec:
agentswarm-bot-provisioning, "Seam protocol conformance coverage")."""
from __future__ import annotations

import inspect

from agentclaw.community.core.agentswarm.domain.models import (
    BotCreateSpec, BotId, IdempotencyKey,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform


def _sig(name: str) -> inspect.Signature:
    return inspect.signature(getattr(BotPlatform, name), eval_str=True)


class TestBotPlatformConformance:

    def test_method_set_is_exactly_the_seam(self):
        public = {m for m in vars(BotPlatform) if not m.startswith("_")}
        assert public == {"create_bot", "bot_status", "delete_bot", "chat"}

    def test_create_bot_takes_structured_spec_keyword_only(self):
        sig = _sig("create_bot")
        params = list(sig.parameters.values())
        assert [p.name for p in params] == ["self", "spec", "idempotency_key"]
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[1:])
        assert params[1].annotation is BotCreateSpec
        assert params[2].annotation is IdempotencyKey
        assert sig.return_annotation is BotId

    def test_bot_status_takes_bot_id_returns_str(self):
        sig = _sig("bot_status")
        assert list(sig.parameters) == ["self", "bot_id"]
        assert sig.parameters["bot_id"].annotation is BotId
        assert sig.return_annotation is str

    def test_delete_bot_takes_bot_id_returns_bool(self):
        sig = _sig("delete_bot")
        assert list(sig.parameters) == ["self", "bot_id"]
        assert sig.parameters["bot_id"].annotation is BotId
        assert sig.return_annotation is bool

    def test_chat_takes_bot_id_and_message_returns_str(self):
        sig = _sig("chat")
        assert list(sig.parameters) == ["self", "bot_id", "message"]
        assert sig.parameters["bot_id"].annotation is BotId
        assert sig.return_annotation is str
