from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import (
    BotServiceBcsBotIdentityResolver,
)


class _BotService:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def list_bots_by_conditions(self, **kwargs):
        self.calls.append(kwargs)
        return {"total": len(self.items), "items": self.items}


def test_resolve_many_maps_product_ids_to_bcs_uuids():
    service = _BotService([
        {"bot_id": "bot_a", "owner_id": "u1"},
        {"bot_id": "bot_b", "owner_id": "u2"},
    ])
    resolver = BotServiceBcsBotIdentityResolver(service)

    assert resolver.resolve_many(["bot_a", "bot_b"]) == {
        "bot_a": "bot_a:u1",
        "bot_b": "bot_b:u2",
    }
    assert service.calls[0]["bot_ids"] == ["bot_a", "bot_b"]


def test_resolve_many_rejects_missing_bot():
    resolver = BotServiceBcsBotIdentityResolver(
        _BotService([{"bot_id": "bot_a", "owner_id": "u1"}])
    )

    try:
        resolver.resolve_many(["bot_a", "bot_missing"])
    except BotIdentityResolutionError as exc:
        assert "bot_missing" in str(exc)
    else:
        raise AssertionError("missing bot must fail identity resolution")


def test_resolve_many_rejects_missing_owner():
    resolver = BotServiceBcsBotIdentityResolver(
        _BotService([{"bot_id": "bot_a", "owner_id": ""}])
    )

    try:
        resolver.resolve_many(["bot_a"])
    except BotIdentityResolutionError as exc:
        assert "owner" in str(exc).lower()
    else:
        raise AssertionError("missing owner must fail identity resolution")


def test_resolve_many_passes_through_ids_already_carrying_owner():
    # 已带 ':'(如上游已 resolve 成 BCS 'bot_id:owner_id')视为身份完整,原样透传、不再查 BotService,
    # 也只把真正需要查询的无 ':' id 传进 BotService。
    service = _BotService([{"bot_id": "bot_b", "owner_id": "u2"}])
    resolver = BotServiceBcsBotIdentityResolver(service)

    assert resolver.resolve_many(["bot_a:u1", "bot_b"]) == {
        "bot_a:u1": "bot_a:u1",
        "bot_b": "bot_b:u2",
    }
    assert service.calls[0]["bot_ids"] == ["bot_b"]
    assert len(service.calls) == 1
