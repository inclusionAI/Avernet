"""Owner changes never create a second BCN provider identity."""
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.bcn_identity import provider_address_kwargs
from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError
from tests.community.core.bot_management.services.test_bcn_service_register_provider import (
    service, http, _ok_response,
)
from tests.community.core.bot_management.services.test_bot_service_bcn_sync import _make_bot_service
from tests.community.core.bot_management.services.test_bot_service_misc import _make_service, _make_bot


@pytest.mark.parametrize("owner", ["old", "new", "third"])
def test_provider_registration_keeps_address_and_uses_current_owner(service, http, owner):
    bot = {"bot_id": "bot", "entity_id": "old", "owner_id": owner, "bot_type": "service"}
    http.set_response("post", _ok_response(200, {"bot_uuid": "existing-bcn-uuid"}))
    with patch("agentclaw.community.core.bot_management.services.bcn_service.get_current_env", return_value="pre"):
        result = service.register_provider_bot(
            teamclaw_bot_uuid="bot", owner_workno=owner, name="Bot", summary="",
            **provider_address_kwargs(bot),
        )
    payload = http.calls_to("post")[0].kwargs["json"]
    assert payload["provider_bot_ref"] == "bot:old"
    assert payload["owners"] == [owner]
    assert result["bot_uuid"] == "existing-bcn-uuid"


@pytest.mark.parametrize("bot_type,bot_id", [("personal", "bot"), ("personal", "default"), ("desktop", "bot")])
def test_nontransferable_bots_keep_legacy_registration(service, http, bot_type, bot_id):
    bot = {"bot_id": bot_id, "entity_id": "staff_owner", "owner_id": "owner", "bot_type": bot_type}
    assert provider_address_kwargs(bot) == {}
    http.set_response("post", _ok_response(200))
    with patch("agentclaw.community.core.bot_management.services.bcn_service.get_current_env", return_value="pre"):
        service.register_provider_bot(teamclaw_bot_uuid=bot_id, owner_workno="owner", name="Bot", summary="", **provider_address_kwargs(bot))
    payload = http.calls_to("post")[0].kwargs["json"]
    assert payload["provider_bot_ref"] == f"{bot_id}:owner"
    assert payload["owners"] == ["owner"]


@pytest.mark.parametrize("entity", [None, "", "  "])
def test_service_without_entity_cannot_register_under_current_owner(entity):
    with pytest.raises(ValueError, match="entity_id"):
        provider_address_kwargs({"bot_id": "bot", "bot_type": "service", "entity_id": entity, "owner_id": "new"})


@pytest.mark.parametrize("locator", ["old", "new"])
def test_name_sync_resolves_legacy_locator_before_addressing_bcn(locator):
    svc = _make_bot_service()
    bot = {"id": 1, "bot_id": "bot", "entity_id": "old", "owner_id": "new", "bot_type": "service", "bot_name": "Name", "bot_desc": "Description"}
    svc._repository.list_matches_by_legacy_locator.return_value = [bot]
    svc._sync_bot_to_bcn(bot_id="bot", owner_id=locator)
    svc._bcn_service.onboard_bot.assert_called_once_with(bot_id="bot:old", name="Name", summary="Description")
    svc._repository.list_matches_by_legacy_locator.assert_called_once_with("bot", locator)


def test_ambiguous_name_sync_never_onboards_another_bot():
    svc = _make_bot_service()
    svc._repository.list_matches_by_legacy_locator.return_value = [{"id": 1}, {"id": 2}]
    svc._sync_bot_to_bcn(bot_id="bot", owner_id="new")
    svc._bcn_service.onboard_bot.assert_not_called()


def test_provider_wrapper_keeps_owner_separate_from_stable_ref():
    svc = _make_bot_service()
    svc._bcn_service.register_provider_bot.return_value = {"bot_uuid": "existing"}
    svc.update_bot_ext = Mock()
    svc._register_bot_to_bcn_as_provider(bot_id="bot", user_id="new", owner_workno="new", bot_name="Name", bot_summary="", provider_bot_ref="bot:old", respect_drm=False)
    svc._bcn_service.register_provider_bot.assert_called_once_with(teamclaw_bot_uuid="bot", owner_workno="new", name="Name", summary="", provider_bot_ref="bot:old")
    svc.update_bot_ext.assert_called_once_with(bot_id="bot", user_id="new", ext_update={"bcn_registered": True})


def test_delete_after_handoff_uses_pre_delete_bot_identity():
    svc = _make_service()
    bot = _make_bot(bot_id="bot001", binding_id=None, bot_type="service", owner_id="new", entity_id="old")
    svc._repository.get_by_id_and_owner.return_value = bot
    svc._repository.soft_delete_by_owner.return_value = True
    assert svc.delete_bot("bot001", "new") is True
    svc._bcn_service.delete_provider_bot.assert_called_once_with(teamclaw_bot_uuid="bot001", owner_workno="new", provider_bot_ref="bot001:old")
    svc._repository.soft_delete_by_owner.assert_called_once_with("bot001", "new")


def test_http_delete_uses_original_provider_ref(service, http):
    http.set_response("delete", _ok_response(200, {"deleted": True}))
    with patch("agentclaw.community.core.bot_management.services.bcn_service.get_current_env", return_value="pre"):
        service.delete_provider_bot(teamclaw_bot_uuid="bot", owner_workno="new", provider_bot_ref="bot:old")
    assert http.calls_to("delete")[0].args[0].endswith("/bots/bot:old")


@pytest.mark.parametrize("method", ["register_provider_bot", "delete_provider_bot"])
@pytest.mark.parametrize("ref", ["", "another:entity"])
def test_invalid_explicit_reference_is_not_replaced_with_owner(service, http, method, ref):
    kwargs = {"teamclaw_bot_uuid": "bot", "owner_workno": "new", "provider_bot_ref": ref}
    if method == "register_provider_bot":
        kwargs.update(name="Bot", summary="")
    with pytest.raises(BcnServiceError, match="Invalid provider_bot_ref"):
        getattr(service, method)(**kwargs)
    assert not http.calls_to("post") and not http.calls_to("delete")


def test_personal_conversion_retains_binding_when_entity_has_staff_prefix():
    from tests.community.core.service_bot.services.test_bot_publish_service import _make_service as make_publish_service
    repo, publishes, bcn, bots = Mock(), Mock(), Mock(), Mock()
    personal = {"id": 100, "bot_id": "bot_001", "owner_id": "person", "entity_id": "staff_person", "bot_type": "personal", "active_engine": "teclaw", "ext": {"keep": "value"}}
    repo.get_by_id_and_owner.return_value = personal
    repo.update_by_owner.side_effect = lambda bot_id, owner, fields: {**personal, **fields}
    bots.is_teclaw_bot.return_value = True
    bcn.switch_bot.return_value = {"bot_id": "existing-bcn-uuid"}
    svc = make_publish_service(publishes, bot_repo=repo, bot_service=bots, bcn_service=bcn)
    result = svc.upgrade_bot_to_service("bot_001", "person")
    upgraded = result["bot"]
    bcn.switch_bot.assert_called_once_with(teamclaw_bot_uuid="bot_001", owner_workno="person", name="bot_001", summary="")
    assert upgraded["ext"] == {"keep": "value", "bcn_provider_bot_ref": "bot_001:person"}
    assert personal["ext"] == {"keep": "value"}
    assert provider_address_kwargs(upgraded) == {"provider_bot_ref": "bot_001:person"}
    upgraded["owner_id"] = "new"
    assert provider_address_kwargs(upgraded) == {"provider_bot_ref": "bot_001:person"}
    bots.restart_bot.assert_not_called()


@pytest.mark.parametrize("ref", ["", "another:person", "bot:"])
def test_corrupt_stored_reference_does_not_fall_back_to_current_owner(ref):
    with pytest.raises(ValueError, match="stored BCN provider reference"):
        provider_address_kwargs({"bot_id": "bot", "bot_type": "service", "entity_id": "original", "owner_id": "new", "ext": {"bcn_provider_bot_ref": ref}})


@pytest.mark.parametrize("bot_type,bot_id,entity,owner,stored,expected", [
    ("personal", "bot", "staff_person", "person", None, "bot:person"),
    ("personal", "default", "staff_person", "person", None, "default:person"),
    ("desktop", "bot", "staff_person", "person", None, "bot:person"),
    ("service", "bot", "original", "new", None, "bot:original"),
    ("service", "bot", "staff_person", "person", "bot:person", "bot:person"),
    ("service", "bot", "staff_person", "new", "bot:person", "bot:person"),
])
def test_task_identity_and_provider_registration_share_one_rule(
    service, http, bot_type, bot_id, entity, owner, stored, expected,
):
    from types import SimpleNamespace
    from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import BotServiceBcsBotIdentityResolver
    bot = {"bot_type": bot_type, "bot_id": bot_id, "entity_id": entity, "owner_id": owner, "ext": {}}
    if stored is not None:
        bot["ext"]["bcn_provider_bot_ref"] = stored
    bots = SimpleNamespace(list_bots_by_conditions=lambda **_: {"total": 1, "items": [bot]})
    resolver = BotServiceBcsBotIdentityResolver(bots)
    assert resolver.resolve_many([bot_id]) == {bot_id: expected}
    http.set_response("post", _ok_response(200, {"bot_uuid": "external-uuid"}))
    with patch("agentclaw.community.core.bot_management.services.bcn_service.get_current_env", return_value="pre"):
        result = service.register_provider_bot(
            teamclaw_bot_uuid=bot_id, owner_workno=owner, name="Bot", summary="",
            **provider_address_kwargs(bot),
        )
    payload = http.calls_to("post")[0].kwargs["json"]
    assert payload["provider_bot_ref"] == expected
    assert payload["owners"] == [owner]
    # A generic Provider's returned UUID is not overwritten by its reference.
    assert result["bot_uuid"] == "external-uuid"


@pytest.mark.parametrize("stored", ["", "another:person", "bot001:", 23])
def test_invalid_provider_reference_stops_delete_before_any_side_effect(stored):
    from agentclaw.community.core.bot_management.services.bot_service import BotServiceError
    svc = _make_service()
    bot = _make_bot(bot_id="bot001", binding_id=42, bot_type="service", owner_id="new", entity_id="original", ext={"bcn_provider_bot_ref": stored})
    svc._repository.get_by_id_and_owner.return_value = bot
    grant, device = Mock(), Mock()
    svc._bot_app_grant_provider = Mock(return_value=grant)
    svc._device_service_provider = Mock(return_value=device)
    with pytest.raises(BotServiceError, match="stored BCN provider reference"):
        svc.delete_bot("bot001", "new")
    grant.revoke_all_for_bot.assert_not_called()
    svc._device_service_provider.assert_not_called()
    svc._passport_plugin.destroy_passport.assert_not_called()
    svc._repository.soft_delete_by_owner.assert_not_called()
    svc._bcn_service.delete_provider_bot.assert_not_called()
