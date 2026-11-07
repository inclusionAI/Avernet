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
        {"bot_type": "service", "bot_id": "bot_a", "entity_id": "u1", "owner_id": "u1"},
        {"bot_type": "service", "bot_id": "bot_b", "entity_id": "u2", "owner_id": "u2"},
    ])
    resolver = BotServiceBcsBotIdentityResolver(service)

    assert resolver.resolve_many(["bot_a", "bot_b"]) == {
        "bot_a": "bot_a:u1",
        "bot_b": "bot_b:u2",
    }
    assert service.calls[0]["bot_ids"] == ["bot_a", "bot_b"]


def test_resolve_many_rejects_missing_bot():
    resolver = BotServiceBcsBotIdentityResolver(
        _BotService([{"bot_type": "service", "bot_id": "bot_a", "entity_id": "u1", "owner_id": "u1"}])
    )

    try:
        resolver.resolve_many(["bot_a", "bot_missing"])
    except BotIdentityResolutionError as exc:
        assert "bot_missing" in str(exc)
    else:
        raise AssertionError("missing bot must fail identity resolution")


def test_resolve_many_rejects_missing_entity():
    resolver = BotServiceBcsBotIdentityResolver(
        _BotService([{"bot_type": "service", "bot_id": "bot_a", "entity_id": "", "owner_id": ""}])
    )

    try:
        resolver.resolve_many(["bot_a"])
    except BotIdentityResolutionError as exc:
        assert "entity" in str(exc).lower()
    else:
        raise AssertionError("missing entity must fail identity resolution")


def test_resolve_many_rejects_ambiguous_product_bot_id() -> None:
    """A bare ``default`` bot_id belongs to many owners and must fail closed."""
    service = _BotService([
        {"bot_type": "service", "bot_id": "default", "entity_id": "410025", "owner_id": "410025"},
        {"bot_type": "service", "bot_id": "default", "entity_id": "399002", "owner_id": "399002"},
    ])
    resolver = BotServiceBcsBotIdentityResolver(service)

    try:
        resolver.resolve_many(["default"])
    except BotIdentityResolutionError as exc:
        assert "ambiguous bot owner" in str(exc)
        assert "default" in str(exc)
    else:
        raise AssertionError("an ambiguous bare bot id must fail identity resolution")


def test_resolve_many_rejects_malformed_full_identity():
    resolver = BotServiceBcsBotIdentityResolver(_BotService([]))

    for malformed in ("default:", ":user", "default:user:extra"):
        try:
            resolver.resolve_many([malformed])
        except BotIdentityResolutionError as exc:
            assert malformed in str(exc)
        else:
            raise AssertionError(f"malformed identity must fail: {malformed}")


def test_resolve_many_passes_through_ids_already_carrying_owner():
    # 已带 ':'(如上游已 resolve 成 BCS 'bot_id:owner_id')视为身份完整,原样透传、不再查 BotService,
    # 也只把真正需要查询的无 ':' id 传进 BotService。
    service = _BotService([{"bot_type": "service", "bot_id": "bot_b", "entity_id": "u2", "owner_id": "u2"}])
    resolver = BotServiceBcsBotIdentityResolver(service)

    assert resolver.resolve_many(["bot_a:u1", "bot_b"]) == {
        "bot_a:u1": "bot_a:u1",
        "bot_b": "bot_b:u2",
    }
    assert service.calls[0]["bot_ids"] == ["bot_b"]
    assert len(service.calls) == 1


def test_handoff_changes_owner_but_never_bcs_identity():
    service = _BotService([{"bot_type": "service", "bot_id": "bot", "entity_id": "original", "owner_id": "new"}])
    resolver = BotServiceBcsBotIdentityResolver(service)
    assert resolver.resolve_many(["bot", "bot:original"]) == {"bot": "bot:original", "bot:original": "bot:original"}
    service.items[0]["owner_id"] = "third"
    assert resolver.resolve_many(["bot"]) == {"bot": "bot:original"}


def test_missing_entity_does_not_fall_back_to_current_owner():
    resolver = BotServiceBcsBotIdentityResolver(_BotService([{"bot_type": "service", "bot_id": "bot", "owner_id": "new"}]))
    try:
        resolver.resolve_many(["bot"])
    except BotIdentityResolutionError as exc:
        assert "entity_id" in str(exc)
    else:
        raise AssertionError("missing entity must not produce a different BCS identity")
