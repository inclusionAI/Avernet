"""ArcaConnInfoBuilder 单测 — plan §Task 1.4 Step 1。

3 个 case:
1. 返回 arca proxy conn_info(含 /proxypass/ url)
2. 委托给 device_service.get_device_connection_v2,binding_id / user_id 正确透传
3. v2 抛异常时包装成 ConnInfoBuildError
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import (
    ArcaConnInfoBuilder,
)
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError


@pytest.fixture
def fake_binding():
    binding = MagicMock()
    binding.id = 42
    binding.bot_id = "bot-1"
    binding.device_provider = "arca"
    return binding


@pytest.fixture
def fake_device_service():
    svc = MagicMock()
    svc.get_device_connection_v2.return_value = {
        "url": "http://arca-proxy/proxypass/ARCA_x@y:1",
        "headers": {"x-proxypass-token": "tok"},
        "use_proxy": True,
        "sandbox_id": "ARCA_x@y:1",
        "target": "ARCA_x@y:1",
        "token": "tok",
        "engine_type": "openclaw",
        "type": "arca",
    }
    return svc


def test_build_returns_arca_proxy_conn_info(fake_binding, fake_device_service):
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert "/proxypass/" in conn_info["url"]
    assert conn_info["use_proxy"] is True
    assert "sandbox_id" in conn_info


def test_build_delegates_to_v2_with_binding_id(fake_binding, fake_device_service):
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    builder.build(fake_binding, user_id="user-1")

    fake_device_service.get_device_connection_v2.assert_called_once()
    call_kwargs = fake_device_service.get_device_connection_v2.call_args.kwargs
    assert call_kwargs.get("binding_id") == 42
    assert call_kwargs.get("user_id") == "user-1"


def test_build_raises_conn_info_build_error_on_v2_failure(
    fake_binding, fake_device_service
):
    fake_device_service.get_device_connection_v2.side_effect = Exception("arca down")
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    with pytest.raises(ConnInfoBuildError):
        builder.build(fake_binding, user_id="user-1")


# ── P1: the binding row is read once for the whole resolution ──


@pytest.fixture
def arca_record():
    """A real record — the chain reads ``device_props`` and ``entity_id``."""
    from datetime import datetime

    from agentclaw.community.core.devices.models import DeviceBindingStatus
    from agentclaw.community.core.devices.repository.record import DeviceBindingRecord

    return DeviceBindingRecord(
        id=42,
        entity_id="u001",
        entity_type="staff",
        device_id="staff_u001_default",
        device_provider="arca",
        env="dev",
        device_props={"sandbox_id": "ARCA-SANDBOX-x@0", "callback_token": "tok"},
        status=DeviceBindingStatus.ACTIVE.value,
        apply_reason=None,
        applied_by="u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


def _counting_repo(record):
    """A binding repository that records every read of the row."""
    repo = MagicMock()
    repo.get_by_id = MagicMock(return_value=record)
    repo.get_active_by_bot_and_owner = MagicMock(return_value=record)
    return repo


def _real_arca_stack(record):
    """A real router over a real DeviceService, both on one counting repo.

    Deliberately not mocks: the four reads this test pins down come from the
    router routing twice and the provider re-reading behind each hop, so a
    mocked router or provider would assert nothing.
    """
    from agentclaw.community.core.devices.services.device_service import (
        ARCA_DEVICE_PROVIDER,
        DeviceService,
    )
    from agentclaw.community.core.devices.services.device_service_router import (
        DeviceServiceRouter,
    )

    repo = _counting_repo(record)
    sandbox_client = MagicMock()
    sandbox_client.proxy_base_url = MagicMock(return_value="http://arca-proxy")
    provider = DeviceService(
        repo,
        bot_query=MagicMock(),
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
        sandbox_client=sandbox_client,
    )
    router = DeviceServiceRouter(
        repository=repo,
        bot_query=MagicMock(),
        providers={ARCA_DEVICE_PROVIDER: provider},
        default_provider_key=ARCA_DEVICE_PROVIDER,
        sandbox_client=sandbox_client,
    )
    return router, repo


def test_build_passes_the_loaded_record_to_v2(fake_binding, fake_device_service):
    builder = ArcaConnInfoBuilder(device_service=fake_device_service)

    builder.build(fake_binding, user_id="user-1")

    call_kwargs = fake_device_service.get_device_connection_v2.call_args.kwargs
    assert call_kwargs.get("record") is fake_binding


def test_resolution_reads_the_binding_row_zero_extra_times(arca_record):
    """The resolver already loaded the row; the chain below must not re-read it.

    Before P1 this path issued four ``get_by_id`` calls for one address
    resolution — the router's two routing hops plus the provider's own read
    behind each. The resolver's ``get_active_by_bot_and_owner`` is the one
    read that legitimately happens, and it is outside this call.
    """
    router, repo = _real_arca_stack(arca_record)
    builder = ArcaConnInfoBuilder(device_service=router)

    builder.build(arca_record, user_id="u001")

    assert repo.get_by_id.call_count == 0


def test_resolution_without_a_record_still_reads_the_row(arca_record):
    """The record is an optimisation, not a requirement — omitting it works.

    The count is pinned at four rather than "more than zero" because that is
    what the test above removes: ``get_device`` routes then reads, and
    ``get_device_connection`` routes then reads. Anything that changes this
    number should change the test above too.
    """
    router, repo = _real_arca_stack(arca_record)

    router.get_device_connection_v2(
        user_id="u001", nick_name="u001", binding_id=arca_record.id
    )

    assert repo.get_by_id.call_count == 4


def test_a_record_for_another_binding_is_refused(arca_record):
    """The record short-circuits the lookup that defines which binding this is.

    A mismatched row would route by one binding's provider and run the
    ownership check against another's owner, so it is refused rather than
    quietly trusted. ValueError, not a device error: two arguments contradict
    each other, which is neither "device not found" (a 404 that sends clients
    down a re-provision path) nor "device service failed" (rendered as
    "retry later", with the message discarded).
    """
    router, _ = _real_arca_stack(arca_record)

    with pytest.raises(ValueError):
        router.get_device_connection_v2(
            user_id="u001",
            nick_name="u001",
            binding_id=arca_record.id + 1,
            record=arca_record,
        )


def test_the_router_refuses_a_mismatched_record_before_it_routes(arca_record):
    """The routing decision itself reads record.device_provider.

    Enforcing only inside the provider would mean the wrong provider had
    already been chosen by the time anything checked — so the assertion is
    that no provider was ever reached, not merely that something raised.
    """
    from agentclaw.community.core.devices.models import OperatorContext

    router, repo = _real_arca_stack(arca_record)
    provider = MagicMock()
    router._providers["arca"] = provider
    router._default_service = provider
    operator = OperatorContext(
        staff_id="u001", staff="u001", nick_name="u001",
        operator_name="u001", tenant_id="default",
    )

    with pytest.raises(ValueError):
        router.get_device_connection(
            binding_id=arca_record.id + 1, operator=operator, record=arca_record
        )
    provider.get_device_connection.assert_not_called()
    assert repo.get_by_id.call_count == 0


def test_the_local_provider_refuses_a_mismatched_record(arca_record):
    """The local provider skips its own lookup when handed a record, so its
    ownership check would otherwise read another binding's entity_id."""
    from agentclaw.community.core.devices.services.local_device_service import (
        LocalDeviceService,
    )

    service = LocalDeviceService(
        _counting_repo(arca_record),
        baas_service=MagicMock(),
        publish_poller=MagicMock(),
        bot_query=MagicMock(),
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
    )

    with pytest.raises(ValueError):
        service.get_device_connection(
            binding_id=arca_record.id + 1,
            operator=MagicMock(staff_id="u001"),
            record=arca_record,
        )
