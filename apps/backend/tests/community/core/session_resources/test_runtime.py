"""File targeting reuses the JIT locator without its credential-write policy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingNotFoundError
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest, RuntimeBindingSource
from agentclaw.community.core.session_resources.runtime import SessionFileRuntimeResolver


@pytest.mark.parametrize("source,subject,affinity", [
    (RuntimeBindingSource.SERVICE_ONLINE, None, "actor"),
    (RuntimeBindingSource.CALLER_INSTANCE, "actor", "actor"),
    (RuntimeBindingSource.CALLER_INSTANCE, "friend", "friend"),
    (RuntimeBindingSource.SERVICE_ONLINE, "friend", "owner"),
])
def test_locator_selects_binding_and_exact_device(source, subject, affinity, caplog):
    locator = Mock()
    locator.resolve_binding.return_value = SimpleNamespace(
        binding=SimpleNamespace(id=91, device_provider="baas"), owner_id="owner", source=source,
    )
    locator.resolve_targets.return_value = (SimpleNamespace(device_uuid="DEVICE-B"),)
    actor = "owner" if subject == "friend" else "actor"
    service = SessionFileRuntimeResolver(locator)
    request = RuntimeBindingRequest("bot", "entity", actor, "online")
    with caplog.at_level("INFO"):
        selected = service.resolve(request, session_user_id=subject, device_uuid="DEVICE-B")
    passed = locator.resolve_binding.call_args.args[0]
    assert (passed.bot_id, passed.entity_id, passed.f_user_id, passed.stage) == ("bot", "entity", subject, "online")
    assert selected.binding_id == 91 and selected.owner_id == "owner"
    assert selected.device_uuid == "DEVICE-B" and selected.affinity_id == affinity
    locator.resolve_targets.assert_called_once_with(locator.resolve_binding.return_value, device_uuid="DEVICE-B")
    assert "session_files.runtime.selected" in caplog.text


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_runtime_never_selects_first_replica(count):
    locator = Mock()
    locator.resolve_binding.return_value = SimpleNamespace(
        binding=SimpleNamespace(id=91, device_provider="baas"), owner_id="owner", source=RuntimeBindingSource.SERVICE_ONLINE,
    )
    locator.resolve_targets.return_value = tuple(SimpleNamespace(device_uuid=f"DEVICE-{i}") for i in range(count))
    with pytest.raises(RuntimeBindingNotFoundError):
        SessionFileRuntimeResolver(locator).resolve(RuntimeBindingRequest("bot", "owner", "owner", "online"))


def test_reader_cannot_replace_friend_subject_without_owner_authority():
    locator = Mock()
    locator.resolve_binding.return_value = SimpleNamespace(owner_id="owner")
    with pytest.raises(EngineResourceNotFoundError):
        SessionFileRuntimeResolver(locator).resolve(RuntimeBindingRequest("bot", "entity", "outsider"), session_user_id="friend")
    locator.resolve_targets.assert_not_called()


@pytest.mark.parametrize("kind,caller,engine,subject,stage,expected", [
    ("service", True, "openclaw", None, "draft", 1),
    ("service", True, "openclaw", None, "verify", 2),
    ("service", True, "openclaw", None, "online", 3),
    ("service", True, "openclaw", "friend", "online", 4),
    ("service", False, "openclaw", None, "online", 3),
    ("service", True, "teclaw", "friend", "online", 3),
    ("personal", False, "openclaw", None, "draft", 1),
])
def test_file_resolver_reuses_real_jit_locator_for_each_runtime(kind, caller, engine, subject, stage, expected):
    from tests.community.core.runtime_binding.test_container_locator import world

    locator, bots, _, instances, _ = world(kind=kind, caller=caller, engine=engine)
    request = RuntimeBindingRequest("default", "historical-entity", "new-owner", stage)
    target = SessionFileRuntimeResolver(locator).resolve(request, session_user_id=subject, device_uuid="a")
    assert target.binding_id == expected
    assert target.owner_id == "new-owner" and target.device_uuid == "a"
    assert target.affinity_id == ("friend" if expected == 4 else "new-owner")
    assert all(call.args == ("default", "historical-entity") for call in bots.get_by_id_and_entity.call_args_list)
    if expected == 4:
        instances.get_instance.assert_called_once_with("friend", "default", "new-owner")
    else:
        instances.get_instance.assert_not_called()


@pytest.mark.parametrize("uuid", [None, "DEVICE-MISSING"])
def test_inventory_failure_does_not_fall_back_or_expose_provider_error(uuid):
    locator = Mock()
    locator.resolve_binding.return_value = SimpleNamespace(
        binding=SimpleNamespace(id=91, device_provider="baas"), owner_id="owner",
    )
    locator.resolve_targets.side_effect = ValueError("private-provider-detail")
    with pytest.raises(RuntimeBindingNotFoundError, match="^session file device unavailable$"):
        SessionFileRuntimeResolver(locator).resolve(RuntimeBindingRequest("bot", "owner", "owner"), device_uuid=uuid)
    locator.resolve_targets.assert_called_once_with(locator.resolve_binding.return_value, device_uuid=uuid)


@pytest.mark.parametrize("uuid", [None, "DEVICE-B"])
def test_local_runtime_retains_legacy_connection_without_device_inventory(uuid):
    locator = Mock()
    locator.resolve_binding.return_value = SimpleNamespace(
        binding=SimpleNamespace(id=91, device_provider="local"), owner_id="owner", source=RuntimeBindingSource.PERSONAL,
    )
    resolver = SessionFileRuntimeResolver(locator)
    request = RuntimeBindingRequest("bot", "owner", "owner")
    if uuid:
        with pytest.raises(RuntimeBindingNotFoundError):
            resolver.resolve(request, device_uuid=uuid)
    else:
        assert resolver.resolve(request).device_uuid is None
    locator.resolve_targets.assert_not_called()
