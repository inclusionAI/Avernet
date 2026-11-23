"""Authorized resource selectors locate bindings and current replica sets."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.runtime_binding.container_locator import RuntimeContainerLocator
from agentclaw.community.core.runtime_binding.errors import CallerInstanceNotReadyError, RuntimeBindingNotFoundError, RuntimeBotNotFoundError
from agentclaw.community.core.runtime_binding.models import (
    RuntimeBindingRequest, RuntimeBindingSource, RuntimeBindingTarget, RuntimeContainerRequest,
)
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.service_bot.services.baas_service import BaasService, BaasOutboundTargetError


def world(*, kind="service", caller=True, engine="openclaw", instance=True):
    owner = "new-owner"
    bot = {"id": 12, "bot_id": "default", "entity_id": "historical-entity", "owner_id": owner,
           "bot_type": kind, "call_type": "caller" if caller else "owner", "active_engine": engine, "binding_id": 1}
    bots = Mock()
    bots.get_by_id_and_entity.return_value = bot
    bots.get_by_id_and_owner.return_value = bot
    bindings = {i: SimpleNamespace(id=i, status="ACTIVE", device_provider="baas", device_id=f"logical-{i}",
                                  entity_id=owner, applied_by="friend", env="test", apply_reason="caller_instance:default")
                for i in (1, 2, 3, 4)}
    if engine == "teclaw":
        for binding in bindings.values():
            binding.device_provider = "teclaw"
    repository = Mock()
    repository.get_by_id.side_effect = bindings.get
    publish = Mock()
    publish.list_by_source_bot.return_value = [SimpleNamespace(status="success", ext={"binding": {"verify": 2, "online": 3}})]
    instances = Mock()
    instances.get_instance.return_value = {"status": "success", "ext": {"binding_id": 4}} if instance else None
    service = RuntimeBindingResolutionService(bot_repository=bots, publish_repository=publish,
                                             binding_repository=repository, caller_instance_repository=instances,
                                             environment_provider=lambda: "test")
    baas = object.__new__(BaasService)
    baas._tenant = "test"
    baas._active_token_replica_devices = Mock(return_value=[
        {"device_uuid": "a", "status": "ACTIVE", "provider_device_id": "ARCA-SANDBOX-one@0"},
        {"device_uuid": "b/副本", "status": "ACTIVE", "provider_device_id": "ARCA-SANDBOX-two@0"},
        {"device_uuid": "old", "status": "RELEASED", "provider_device_id": "ARCA-SANDBOX-old@0"},
    ])
    locator = RuntimeContainerLocator(bots=bots, runtime_bindings=service, binding_repository=repository, baas_service=baas)
    return locator, bots, bindings, instances, baas


def request(**overrides):
    return RuntimeContainerRequest(bot_id="default", entity_id="historical-entity", actor_user_id="actor", **overrides)


@pytest.mark.parametrize(("stage", "binding_id"), [("draft", 1), ("verify", 2), ("online", 3)])
@pytest.mark.parametrize("caller", [True, False])
def test_no_friend_selects_exact_service_stage_not_auto_instance(stage, binding_id, caller):
    locator, bots, _, instances, baas = world(caller=caller)
    result = locator.resolve(request(stage=stage))
    assert result.runtime.binding.id == binding_id
    assert result.runtime.owner_id == "new-owner"
    assert [x.device_uuid for x in result.targets] == ["a", "b/副本"]
    assert [x.provider_device_id for x in result.targets] == ["ARCA-SANDBOX-one@0", "ARCA-SANDBOX-two@0"]
    assert [call.args for call in bots.get_by_id_and_entity.call_args_list] == [("default", "historical-entity")] * 2
    bots.get_by_id_and_owner.assert_not_called()
    instances.get_instance.assert_not_called()
    baas._active_token_replica_devices.assert_called_once_with(f"logical-{binding_id}")


def test_friend_uses_exact_successful_existing_caller_instance():
    locator, _, _, instances, _ = world()
    result = locator.resolve(request(stage="verify", f_user_id="friend", device_uuid="b/副本"))
    assert result.runtime.source is RuntimeBindingSource.CALLER_INSTANCE
    assert result.runtime.binding.id == 4
    assert [x.provider_device_id for x in result.targets] == ["ARCA-SANDBOX-two@0"]
    instances.get_instance.assert_called_once_with("friend", "default", "new-owner")


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
def test_ordinary_service_friend_keeps_requested_stage(stage):
    locator, _, _, instances, _ = world(caller=False)
    runtime = locator.resolve_binding(request(stage=stage, f_user_id="friend"))
    assert runtime.binding.id == {"draft": 1, "verify": 2, "online": 3}[stage]
    instances.get_instance.assert_not_called()


@pytest.mark.parametrize("friend", [None, "", "friend"])
def test_personal_without_stage_uses_only_personal_binding(friend):
    locator, _, _, instances, _ = world(kind="personal")
    runtime = locator.resolve_binding(request(f_user_id=friend))
    assert runtime.binding.id == 1
    assert runtime.source is RuntimeBindingSource.PERSONAL
    instances.get_instance.assert_not_called()


@pytest.mark.parametrize("friend", [None, "friend"])
def test_teclaw_shared_strategy_returns_typed_real_provider_without_caller_lookup(friend):
    locator, _, _, instances, baas = world(engine="teclaw")
    baas._active_token_replica_devices.return_value = [{"status": "ACTIVE", "device_uuid": "shared", "provider_device_id": "teclaw-shared@0"}]
    result = locator.resolve(request(stage="online", f_user_id=friend))
    assert result.runtime.binding.id == 3
    assert result.targets[0].provider == "teclaw"
    assert result.targets[0].provider_device_id == "teclaw-shared@0"
    instances.get_instance.assert_not_called()


def test_service_without_stage_is_explicitly_rejected(caplog):
    locator, _, _, instances, baas = world()
    with pytest.raises(RuntimeBindingNotFoundError, match="stage is required"):
        locator.resolve(request())
    instances.get_instance.assert_not_called()
    baas._active_token_replica_devices.assert_not_called()
    assert "runtime_container_binding_failed" in caplog.text


def test_missing_entity_never_falls_back_to_owner_lookup():
    locator, bots, _, _, _ = world()
    bots.get_by_id_and_entity.return_value = None
    with pytest.raises(RuntimeBotNotFoundError):
        locator.resolve(request(stage="online"))
    bots.get_by_id_and_owner.assert_not_called()


@pytest.mark.parametrize("field,value", [("env", "other"), ("entity_id", "other"), ("applied_by", "other"),
                                        ("apply_reason", "other"), ("device_provider", "arca"), ("status", "RELEASED")])
def test_caller_scope_failure_never_falls_back_to_shared(field, value):
    locator, _, bindings, _, baas = world()
    setattr(bindings[4], field, value)
    with pytest.raises(RuntimeBindingNotFoundError):
        locator.resolve(request(stage="online", f_user_id="friend"))
    baas._active_token_replica_devices.assert_not_called()


@pytest.mark.parametrize("instance", [None, {"status": "init", "ext": {"binding_id": 4}}, {"status": "success", "ext": {}}])
def test_missing_or_not_ready_caller_never_creates_or_falls_back(instance):
    locator, _, _, instances, baas = world()
    instances.get_instance.return_value = instance
    with pytest.raises(CallerInstanceNotReadyError):
        locator.resolve(request(stage="online", f_user_id="friend"))
    baas._active_token_replica_devices.assert_not_called()


@pytest.mark.parametrize("uuid", ["foreign", "old"])
def test_exact_device_rejects_missing_or_inactive_without_fallback(uuid):
    locator, _, _, _, _ = world()
    with pytest.raises(BaasOutboundTargetError, match="target_not_found"):
        locator.resolve(request(stage="online", device_uuid=uuid))


def test_duplicate_device_uuid_is_ambiguous():
    locator, _, _, _, baas = world()
    baas._active_token_replica_devices.return_value *= 2
    with pytest.raises(BaasOutboundTargetError, match="target_ambiguous"):
        locator.resolve(request(stage="online", device_uuid="a"))


@pytest.mark.parametrize("template", ["12", "template-v2"])
def test_native_arca_exact_uuid_and_template_are_preserved(template):
    locator, _, bindings, _, _ = world(kind="personal")
    bindings[1].device_provider = "arca"
    bindings[1].device_props = {"sandbox_id": f"ARCA-SANDBOX-native@{template}", "device_uuid": "native"}
    result = locator.resolve(request(device_uuid="native"))
    assert result.targets[0].provider_device_id == f"ARCA-SANDBOX-native@{template}"
    assert result.targets[0].provider == "arca"


def test_inventory_uses_current_record_for_all_replicas_and_logs_projection(caplog):
    caplog.set_level("INFO")
    locator, _, _, _, baas = world()
    del baas._active_token_replica_devices
    status = {"bot_uuid": "logical-3", "bot_status": "ACTIVE", "bot_id": 55}
    detail = {"bot_uuid": "logical-3", "status": "ACTIVE", "id": 55, "token": "secret-sentinel",
              "devices": [{"device_uuid": "current", "provider_device_id": "ARCA-SANDBOX-live@0", "status": "ACTIVE"}]}
    baas._get_bots_api = Mock(side_effect=[status, detail])
    result = locator.resolve(request(stage="online"))
    assert result.targets[0].device_uuid == "current"
    assert [call.args[0] for call in baas._get_bots_api.call_args_list] == [
        "/api/v1/bots/logical-3/device-status", "/api/v1/bots/55/detail-by-id"]
    assert "runtime_device_inventory_started" in caplog.text
    assert "runtime_device_inventory_succeeded" in caplog.text
    assert "runtime_container_binding_succeeded" in caplog.text
    assert "secret-sentinel" not in caplog.text


@pytest.mark.parametrize("detail", [{"bot_uuid": "logical-3", "id": 54, "status": "ACTIVE"},
                                   {"bot_uuid": "old-bot", "id": 55, "status": "ACTIVE"},
                                   {"bot_uuid": "logical-3", "id": 55, "status": "RELEASED"}])
def test_inventory_rejects_stale_or_foreign_record(detail):
    locator, _, _, _, baas = world()
    del baas._active_token_replica_devices
    baas._get_bots_api = Mock(side_effect=[{"bot_uuid": "logical-3", "bot_status": "ACTIVE", "bot_id": 55}, detail])
    with pytest.raises(BaasOutboundTargetError, match="target_not_found"):
        locator.resolve(request(stage="online"))


def test_exact_entity_survives_same_owner_duplicate_bot_names():
    locator, bots, _, _, _ = world()
    bots.get_by_id_and_owner.side_effect = AssertionError("must not lookup an ambiguous owner")
    assert locator.resolve_binding(request(stage="draft")).binding.id == 1


def test_exact_entity_lookup_does_not_require_a_matching_owner_hint():
    locator, bots, _, _, _ = world()
    resolved = locator._runtime_bindings.resolve(RuntimeBindingRequest(
        bot_id="default", entity_id="historical-entity", owner_id="historical-owner",
        actor_user_id="actor", stage="draft", target=RuntimeBindingTarget.CALLER_SERVICE,
    ))
    assert resolved.binding_id == 1
    bots.get_by_id_and_entity.assert_called_once_with("default", "historical-entity")
    bots.get_by_id_and_owner.assert_not_called()


@pytest.mark.parametrize(("provider", "props", "device_id", "status", "reason"), [
    ("unknown", {}, "logical", "ACTIVE", "unsupported_provider"),
    ("baas", {}, "../foreign", "ACTIVE", "invalid_target"),
    ("arca", {"sandbox_id": "ARCA-SANDBOX-native@../bad"}, "native", "ACTIVE", "invalid_target"),
    ("arca", {"sandbox_id": "ARCA-SANDBOX-native@bad\n"}, "native", "ACTIVE", "invalid_target"),
    ("arca", {"sandbox_id": "OTHER@0"}, "native", "ACTIVE", "invalid_target"),
    ("arca", {}, "native", "ACTIVE", "invalid_target"),
    ("arca", {"sandbox_id": "ARCA-SANDBOX-native@0", "device_uuid": "native"}, "native", "RELEASED", "binding_unavailable"),
])
def test_inventory_failure_reasons_are_explicit_and_do_not_log_secrets(provider, props, device_id, status, reason, caplog):
    _, _, _, _, baas = world()
    binding = SimpleNamespace(id=1, device_provider=provider, device_props=props, device_id=device_id, status=status)
    with pytest.raises(BaasOutboundTargetError, match=reason):
        baas.list_runtime_devices(binding)
    assert "runtime_device_inventory_failed" in caplog.text
    assert f"reason={reason}" in caplog.text


def test_native_arca_foreign_uuid_is_not_selected():
    _, _, _, _, baas = world()
    binding = SimpleNamespace(id=1, device_provider="arca", status="ACTIVE", device_props={
        "sandbox_id": "ARCA-SANDBOX-native@0", "device_uuid": "native",
    })
    with pytest.raises(BaasOutboundTargetError, match="target_not_found"):
        baas.list_runtime_devices(binding, device_uuid="foreign")


def test_missing_binding_on_second_read_is_rejected():
    locator, _, _, _, baas = world(kind="personal")
    locator._binding_repository.get_by_id.side_effect = [SimpleNamespace(status="ACTIVE"), None]
    with pytest.raises(RuntimeBindingNotFoundError):
        locator.resolve(request())
    baas._active_token_replica_devices.assert_not_called()


def test_inventory_query_failure_is_propagated_without_secret_response(caplog):
    locator, _, _, _, baas = world()
    del baas._active_token_replica_devices
    failure = RuntimeError("upstream token=credential-sentinel")
    baas._get_bots_api = Mock(side_effect=failure)
    with pytest.raises(RuntimeError) as caught:
        locator.resolve(request(stage="online"))
    assert caught.value is failure
    assert "token_replica_inventory_failed" in caplog.text
    assert "runtime_device_inventory_failed" in caplog.text
    assert "credential-sentinel" not in caplog.text


@pytest.mark.parametrize("provider", ["baas", "teclaw", "arca"])
def test_empty_uuid_enumerates_the_same_targets_as_omitted(provider, caplog):
    caplog.set_level("INFO")
    locator, _, bindings, _, _ = world(kind="personal")
    binding = bindings[1]
    binding.device_provider = provider
    if provider == "arca":
        binding.device_props = {"sandbox_id": "ARCA-SANDBOX-native@template-v2", "device_uuid": "native"}
    omitted = locator.resolve(request())
    empty = locator.resolve(request(device_uuid=""))
    assert empty.targets == omitted.targets
    assert len(empty.targets) == (1 if provider == "arca" else 2)
    assert "runtime_device_uuid_normalized" in caplog.text
    assert "reason=empty" in caplog.text
