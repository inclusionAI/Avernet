"""Unit tests for online_instance_guard — the duplicate-online-instance check."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.publish_flow.online_instance_guard import (
    check_existing_online_instance,
    DuplicateOnlineInstanceError,
)
from agentclaw.community.core.service_bot.types import PublishStage


def _make_record(ext=None, **kwargs):
    from datetime import datetime
    from agentclaw.community.core.service_bot.repository.models import PublishStatus
    data = dict(
        id=kwargs.get('id', 1),
        source_bot_pk=kwargs.get('source_bot_pk', 11),
        source_bot_id=kwargs.get('source_bot_id', 'bot-source'),
        publish_bot_id=kwargs.get('publish_bot_id', 'bot-pub-1'),
        name=kwargs.get('name', 'demo'),
        description=kwargs.get('description', 'desc'),
        owner_id=kwargs.get('owner_id', 'u1'),
        owner_name=kwargs.get('owner_name', 'user1'),
        status=kwargs.get('status', PublishStatus.SUCCESS.value),
        version=kwargs.get('version', 1),
        last_pub_id=kwargs.get('last_pub_id', 0),
        env=kwargs.get('env', 'dev'),
        ext=ext if ext is not None else {},
        permission_owner=kwargs.get('permission_owner', 'u1'),
        gmt_create=kwargs.get('gmt_create', datetime.now()),
        gmt_modified=kwargs.get('gmt_modified', datetime.now()),
    )
    return BotPublishRecord(**data)


def test_check_passes_when_no_binding():
    """No binding for stage -- passes without error."""
    publish_service = Mock()
    record = _make_record(ext={})
    publish_service.get_publish_by_id.return_value = record

    # Should not raise
    check_existing_online_instance(
        publish_service=publish_service,
        publish_id=1,
        stage=PublishStage.ONLINE,
    )

    # get_device_binding_by_id is never called when there is no binding_id
    publish_service.get_device_binding_by_id.assert_not_called()


def test_check_passes_when_binding_has_released_status():
    """Binding exists but status is RELEASED -- slot is free, passes without error."""
    publish_service = Mock()
    record = _make_record(ext={"binding": {"online": 42}})
    publish_service.get_publish_by_id.return_value = record
    binding = Mock(status=DeviceBindingStatus.RELEASED)
    publish_service.get_device_binding_by_id.return_value = binding

    # Should not raise -- RELEASED means the slot is free
    check_existing_online_instance(
        publish_service=publish_service,
        publish_id=1,
        stage=PublishStage.ONLINE,
    )

    publish_service.get_device_binding_by_id.assert_called_once_with(42)


def test_check_raises_when_binding_has_active_status():
    """Binding has ACTIVE status -- raises DuplicateOnlineInstanceError."""
    publish_service = Mock()
    record = _make_record(ext={"binding": {"online": 42}})
    publish_service.get_publish_by_id.return_value = record
    binding = Mock(status=DeviceBindingStatus.ACTIVE)
    publish_service.get_device_binding_by_id.return_value = binding

    with pytest.raises(DuplicateOnlineInstanceError):
        check_existing_online_instance(
            publish_service=publish_service,
            publish_id=1,
            stage=PublishStage.ONLINE,
        )


def test_check_raises_when_binding_has_failed_status():
    """Binding has FAILED status -- raises DuplicateOnlineInstanceError.

    This is the core bug scenario: a FAILED online bot still occupies the slot,
    and recreating would produce duplicate baas_device rows.
    """
    publish_service = Mock()
    record = _make_record(ext={"binding": {"online": 42}})
    publish_service.get_publish_by_id.return_value = record
    binding = Mock(status=DeviceBindingStatus.FAILED)
    publish_service.get_device_binding_by_id.return_value = binding

    with pytest.raises(DuplicateOnlineInstanceError):
        check_existing_online_instance(
            publish_service=publish_service,
            publish_id=1,
            stage=PublishStage.ONLINE,
        )


def test_check_raises_message_includes_publish_id_and_stage():
    """Error message contains publish_id and stage for debugging."""
    publish_service = Mock()
    record = _make_record(ext={"binding": {"online": 42}})
    publish_service.get_publish_by_id.return_value = record
    binding = Mock(status=DeviceBindingStatus.ACTIVE)
    publish_service.get_device_binding_by_id.return_value = binding

    with pytest.raises(DuplicateOnlineInstanceError, match=r"publish 1.*stage online"):
        check_existing_online_instance(
            publish_service=publish_service,
            publish_id=1,
            stage=PublishStage.ONLINE,
        )


def test_check_passes_when_record_not_found():
    """Publish record not found -- passes without error (no record to guard)."""
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = None

    check_existing_online_instance(
        publish_service=publish_service,
        publish_id=999,
        stage=PublishStage.ONLINE,
    )

    publish_service.get_device_binding_by_id.assert_not_called()


def test_check_passes_when_binding_not_found():
    """Binding record not found -- passes without error."""
    publish_service = Mock()
    record = _make_record(ext={"binding": {"online": 42}})
    publish_service.get_publish_by_id.return_value = record
    publish_service.get_device_binding_by_id.return_value = None

    check_existing_online_instance(
        publish_service=publish_service,
        publish_id=1,
        stage=PublishStage.ONLINE,
    )


def test_check_passes_when_binding_has_stopped_status():
    """Binding with STOPPED status -- not in occupied set, passes."""
    publish_service = Mock()
    record = _make_record(ext={"binding": {"online": 42}})
    publish_service.get_publish_by_id.return_value = record
    binding = Mock(status=DeviceBindingStatus.STOPPED)
    publish_service.get_device_binding_by_id.return_value = binding

    # STOPPED is not in _OCCUPIED_STATUSES, so check passes
    check_existing_online_instance(
        publish_service=publish_service,
        publish_id=1,
        stage=PublishStage.ONLINE,
    )