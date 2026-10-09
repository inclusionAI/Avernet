from dataclasses import replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.session_resources.materialization import (
    SessionResourceMaterializeHandler,
)
from agentclaw.community.core.task_queue.types import Complete

from .test_service import _service, _Resolver, _intent
from .test_materialization import _record, _payload, _Repo, _Transport


def test_upload_persists_instance_and_subsequent_resolution_uses_it():
    resolver = _Resolver()
    context = resolver._context()
    context.bot_type = "service"
    resolver.resolve_for_binding = Mock(return_value=context)
    service, repo, _ = _service(resolver=resolver)
    result = service.create_upload_intent(
        owner_id="owner-1",
        bot_id="bot-1",
        session_key="session/raw value",
        scope_type="personal_bot_chat",
        engine_type="openclaw",
        filename="report.txt",
        size_bytes=4,
        device_uuid="device-a",
    )
    assert result.resource.device_uuid == "device-a"
    assert repo.value.device_uuid == "device-a"
    service._resolve_record_context(repo.value)
    assert resolver.resolve_for_binding.call_count == 2
    resolver.resolve_for_binding.assert_called_with(
        42,
        "owner-1",
        bot_id="bot-1",
        device_uuid="device-a",
    )


@pytest.mark.parametrize("stored", [None, "device-a"])
def test_file_selector_never_retargets_an_existing_file(stored):
    service, repo, _ = _service()
    _intent(service)
    repo.value = replace(repo.value, device_uuid=stored)
    params = dict(owner_id="owner-1", bot_id="bot-1", session_key="session/raw value")
    assert service.list_resources(**params) == [repo.value]
    assert service.list_resources(**params, device_uuid="device-b") == []
    with pytest.raises(ValueError, match="resource_not_found"):
        service.get_status(
            **params, resource_id=repo.value.resource_id, device_uuid="device-b"
        )
    with pytest.raises(ValueError, match="resource_not_found"):
        service.delete(**params, resource_id=repo.value.resource_id, device_uuid="device-b")
    assert (
        service.get_status(**params, resource_id=repo.value.resource_id) == repo.value
    )
    if stored:
        assert service.list_resources(**params, device_uuid=stored) == [repo.value]
        deleted = service.delete(**params, resource_id=repo.value.resource_id, device_uuid=stored)
        assert deleted.status.value == "deleted"


@pytest.mark.parametrize("binding_id", [None, 42])
def test_background_materialization_reloads_original_instance(binding_id):
    vault = TokenVault(master_key="test-master-key")
    record = replace(_record(vault), binding_id=binding_id, device_uuid="device-a")
    resolver = Mock()
    context = _Resolver()._context()
    resolver.resolve_for_bot.return_value = context
    resolver.resolve_for_binding.return_value = context
    handler = SessionResourceMaterializeHandler(
        resolver, _Transport(), _Repo(record), vault
    )
    assert isinstance(handler.handle(_payload()), Complete)
    if binding_id is None:
        resolver.resolve_for_bot.assert_called_once_with(
            "bot-1", "owner-1", device_uuid="device-a"
        )
    else:
        resolver.resolve_for_binding.assert_called_once_with(
            42, "owner-1", bot_id="bot-1", device_uuid="device-a"
        )
