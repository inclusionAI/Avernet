"""Multi-instance unit tests — §1 instance list + health four-state (PR2).

Covers only §1 of frontend-api-contract:
- InstanceHealthStatus.from_baas_fields (4-state mapping, §0.5)
- DeviceServiceRouter.get_instances (binding_id entry)
- DeviceServiceRouter.get_instances_by_bot (bot_id entry, ext.binding.online)
- _validate_binding_for_instances failure paths
- _resolve_binding_id_by_bot_id failure paths

Restart (§2) / conn-info (§3) / file (§4) are out of scope for PR2.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.errors import DeviceServiceError, InvalidDeviceStatusError
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER
from agentclaw.community.core.devices.services import device_instance_service as dsr_mod
from agentclaw.community.core.devices.services.device_service_router import (
    BindingNotFoundError,
    BotPublishNotFoundError,
    DeviceServiceRouter,
    InstanceHealthStatus,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    id: int = 1,
    device_id: str = "bot-uuid-001",
    device_provider: str = BAAS_DEVICE_PROVIDER,
    status: str = "ACTIVE",
    env: str = "dev",
    entity_id: str = "u001",
    device_props: dict | None = None,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=id,
        entity_id=entity_id,
        entity_type="staff",
        device_id=device_id,
        device_provider=device_provider,
        env=env,
        device_props=device_props if device_props is not None else {"bolt_id": "bot-001"},
        status=status,
        apply_reason=None,
        applied_by="u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


def _make_publish_record(
    *,
    publish_id: int = 1,
    source_bot_id: str = "bot-001",
    status: str = "success",
    ext: dict | None = None,
) -> BotPublishRecord:
    return BotPublishRecord(
        id=publish_id,
        source_bot_pk=1,
        source_bot_id=source_bot_id,
        publish_bot_id="bot-001",
        name="Bot",
        owner_id="u001",
        permission_owner="u001",
        status=status,
        env="dev",
        ext=ext if ext is not None else {"binding": {"online": 1001}},
    )


def _make_router(
    *,
    repo: MagicMock,
    baas_service: MagicMock | None = None,
    publish_repo: MagicMock | None = None,
    bot_repo: MagicMock | None = None,
) -> DeviceServiceRouter:
    """Build a router with a mock baas provider holding _baas_service."""
    baas_provider = MagicMock(name="baas_provider")
    baas_provider._baas_service = baas_service
    providers = {BAAS_DEVICE_PROVIDER: baas_provider}
    return DeviceServiceRouter(
        repository=repo,
        bot_query=MagicMock(),
        providers=providers,
        default_provider_key=BAAS_DEVICE_PROVIDER,
        publish_repo=publish_repo,
        bot_repo=bot_repo,
    )


@pytest.fixture(autouse=True)
def _env_dev(monkeypatch):
    """Pin env to dev so binding.env checks are deterministic."""
    monkeypatch.setattr(dsr_mod.env_utils, "get_current_env", lambda: "dev")


# ---------------------------------------------------------------------------
# InstanceHealthStatus.from_baas_fields (§0.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,health,expected",
    [
        ("PENDING", None, InstanceHealthStatus.RESTARTING),
        ("PENDING", "true", InstanceHealthStatus.RESTARTING),
        ("UPDATING", "false", InstanceHealthStatus.RESTARTING),
        ("updating", None, InstanceHealthStatus.RESTARTING),  # case-insensitive
        ("ACTIVE", "true", InstanceHealthStatus.ACTIVE),
        ("STOPPED", "false", InstanceHealthStatus.ABNORMAL),
        ("FAILED", "false", InstanceHealthStatus.ABNORMAL),
        ("ACTIVE", None, InstanceHealthStatus.UNKNOWN),
        (None, None, InstanceHealthStatus.UNKNOWN),
        ("ACTIVE", "unexpected", InstanceHealthStatus.UNKNOWN),
    ],
)
def test_health_four_state_mapping(status, health, expected):
    assert InstanceHealthStatus.from_baas_fields(status, health) == expected


# ---------------------------------------------------------------------------
# get_instances (binding_id entry)
# ---------------------------------------------------------------------------


def test_get_instances_returns_full_device_fields():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(id=1001, device_id="bot-uuid-abc")

    baas = MagicMock()
    baas.get_bot.return_value = {
        "bot_uuid": "bot-uuid-abc",
        "devices": [
            {
                "device_uuid": "DEVICE-001",
                "status": "ACTIVE",
                "health": "true",
                "provider_type": "baas",
                "provider_device_id": "prov-dev-001",
                "gmt_create": "2024-01-01T00:00:00Z",
            },
            {
                "device_uuid": "DEVICE-002",
                "status": "UPDATING",
                "health": None,
                "provider_type": "baas",
                "provider_device_id": "prov-dev-002",
                "gmt_create": "2024-01-01T00:00:00Z",
            },
        ],
    }
    bot_repo = MagicMock()
    bot_repo.get_by_id.return_value = {"active_engine": "openclaw"}

    router = _make_router(repo=repo, baas_service=baas, bot_repo=bot_repo)
    result = router.get_instances(binding_id=1001, health_check=True)

    # health_check + engine_type passthrough to BaaS get_bot
    baas.get_bot.assert_called_once_with(
        "bot-uuid-abc", health_check=True, engine_type="openclaw"
    )

    assert result["bot_uuid"] == "bot-uuid-abc"
    assert len(result["devices"]) == 2

    d0 = result["devices"][0]
    # BaaS raw 6 fields passthrough
    assert d0["device_uuid"] == "DEVICE-001"
    assert d0["status"] == "ACTIVE"
    assert d0["health"] == "true"
    assert d0["provider_type"] == "baas"
    assert d0["provider_device_id"] == "prov-dev-001"
    assert d0["gmt_create"] == "2024-01-01T00:00:00Z"
    # backend synthesized
    assert d0["health_status"] == InstanceHealthStatus.ACTIVE
    assert d0["engine_type"] == "openclaw"
    assert d0["bot_uuid"] == "bot-uuid-abc"

    # UPDATING → RESTARTING
    assert result["devices"][1]["health_status"] == InstanceHealthStatus.RESTARTING

    # bolt_id used to resolve engine_type, not binding_id
    bot_repo.get_by_id.assert_called_once_with("bot-001")


def test_get_instances_rejects_teclaw_binding():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(
        id=1001,
        device_id="bot-uuid-teclaw",
        device_provider=TECLAW_DEVICE_PROVIDER,
        device_props={},
    )

    baas = MagicMock()
    router = _make_router(repo=repo, baas_service=baas, bot_repo=None)
    with pytest.raises(BindingNotFoundError):
        router.get_instances(binding_id=1001)

    baas.get_bot.assert_not_called()


def test_list_devices_by_runtime_binding_queries_devices_by_bot_uuid():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(
        id=1001,
        device_id="bot-uuid-teclaw",
        device_provider=TECLAW_DEVICE_PROVIDER,
        device_props={},
    )

    baas = MagicMock()
    baas.get_bot.side_effect = AssertionError(
        "runtime device list must not query get_bot"
    )
    baas.list_devices_by_bot_uuid.return_value = [
        {
            "device_uuid": "DEVICE-T1",
            "status": "ACTIVE",
            "health": "true",
            "provider_type": "TECLAW",
            "provider_device_id": "teclaw-pds-1",
        }
    ]

    router = _make_router(repo=repo, baas_service=baas, bot_repo=None)
    result = router.list_devices_by_runtime_binding(binding_id=1001, timeout=8.0)

    baas.get_bot.assert_not_called()
    baas.list_devices_by_bot_uuid.assert_called_once_with(
        "bot-uuid-teclaw",
        timeout=8.0,
    )
    assert result == ["DEVICE-T1"]


def test_list_devices_by_runtime_binding_preserves_default_baas_call():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(
        id=1001,
        device_id="bot-uuid-teclaw",
        device_provider=TECLAW_DEVICE_PROVIDER,
        device_props={},
    )
    baas = MagicMock()
    baas.list_devices_by_bot_uuid.return_value = []

    router = _make_router(repo=repo, baas_service=baas, bot_repo=None)
    assert router.list_devices_by_runtime_binding(binding_id=1001) == []

    baas.list_devices_by_bot_uuid.assert_called_once_with("bot-uuid-teclaw")


def test_get_instances_engine_type_defaults_openclaw_without_bot_repo():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(id=1001)
    baas = MagicMock()
    baas.get_bot.return_value = {
        "devices": [
            {"device_uuid": "DEVICE-001", "status": "ACTIVE", "health": "true"}
        ]
    }
    router = _make_router(repo=repo, baas_service=baas, bot_repo=None)
    result = router.get_instances(binding_id=1001)
    assert result["devices"][0]["engine_type"] == "openclaw"


def test_get_instances_binding_not_found_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = None
    router = _make_router(repo=repo, baas_service=MagicMock())
    with pytest.raises(BindingNotFoundError):
        router.get_instances(binding_id=9999)


def test_get_instances_non_baas_binding_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(device_provider="arca")
    router = _make_router(repo=repo, baas_service=MagicMock())
    with pytest.raises(BindingNotFoundError):
        router.get_instances(binding_id=1001)


def test_get_instances_non_active_binding_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(status="RELEASED")
    router = _make_router(repo=repo, baas_service=MagicMock())
    with pytest.raises(BindingNotFoundError):
        router.get_instances(binding_id=1001)


def test_get_instances_env_mismatch_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(env="prod")
    router = _make_router(repo=repo, baas_service=MagicMock())
    with pytest.raises(BindingNotFoundError):
        router.get_instances(binding_id=1001)


def test_get_instances_no_baas_service_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record()
    router = _make_router(repo=repo, baas_service=None)
    with pytest.raises(DeviceServiceError):
        router.get_instances(binding_id=1001)


# ---------------------------------------------------------------------------
# get_instances_by_bot (bot_id entry, ext.binding.online)
# ---------------------------------------------------------------------------


def test_get_instances_by_bot_resolves_via_publish_ext():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(id=1001, device_id="bot-uuid-abc")

    publish_repo = MagicMock()
    publish_repo.get_latest_success_by_source_bot_id.return_value = _make_publish_record(
        ext={"binding": {"online": 1001}}
    )
    baas = MagicMock()
    baas.get_bot.return_value = {
        "devices": [
            {"device_uuid": "DEVICE-001", "status": "ACTIVE", "health": "true"}
        ]
    }
    router = _make_router(
        repo=repo, baas_service=baas, publish_repo=publish_repo, bot_repo=None
    )

    result = router.get_instances_by_bot(bot_id="bot-001", health_check=False)

    assert result["bot_uuid"] == "bot-uuid-abc"
    assert result["devices"][0]["device_uuid"] == "DEVICE-001"
    publish_repo.get_latest_success_by_source_bot_id.assert_called_once_with(
        "bot-001", "dev"
    )
    # resolved binding_id used for get_by_id
    repo.get_by_id.assert_called_once_with(1001)


def test_get_instances_by_bot_no_publish_raises():
    repo = MagicMock()
    publish_repo = MagicMock()
    publish_repo.get_latest_success_by_source_bot_id.return_value = None
    router = _make_router(repo=repo, baas_service=MagicMock(), publish_repo=publish_repo)
    with pytest.raises(BotPublishNotFoundError):
        router.get_instances_by_bot(bot_id="bot-001")


def test_get_instances_by_bot_missing_binding_online_raises():
    repo = MagicMock()
    publish_repo = MagicMock()
    publish_repo.get_latest_success_by_source_bot_id.return_value = _make_publish_record(
        ext={"binding": {}}
    )
    router = _make_router(repo=repo, baas_service=MagicMock(), publish_repo=publish_repo)
    with pytest.raises(BotPublishNotFoundError):
        router.get_instances_by_bot(bot_id="bot-001")


def test_get_instances_by_bot_no_publish_repo_raises():
    repo = MagicMock()
    router = _make_router(repo=repo, baas_service=MagicMock(), publish_repo=None)
    with pytest.raises(BotPublishNotFoundError):
        router.get_instances_by_bot(bot_id="bot-001")


# ---------------------------------------------------------------------------
# restart_device (binding_id entry, owner-only) — §2
# ---------------------------------------------------------------------------


def _make_operator(staff_id: str) -> OperatorContext:
    return OperatorContext(
        staff_id=staff_id,
        staff=staff_id,
        nick_name=staff_id,
        operator_name=staff_id,
    )


def test_restart_device_success_returns_publish_id():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(
        id=1001, device_id="bot-uuid-abc", entity_id="owner-001"
    )
    baas = MagicMock()
    baas.restart_devices.return_value = {"publish_id": 42}

    router = _make_router(repo=repo, baas_service=baas)
    result = router.restart_device(
        binding_id=1001,
        device_uuid="DEVICE-001",
        operator=_make_operator("owner-001"),
    )

    assert result == {"publish_id": 42}
    baas.restart_devices.assert_called_once_with(
        "bot-uuid-abc", device_uuids=["DEVICE-001"], operator="owner-001"
    )


def test_restart_device_rejects_teclaw_binding():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(
        id=1001,
        device_provider=TECLAW_DEVICE_PROVIDER,
        entity_id="owner-001",
    )
    baas = MagicMock()

    router = _make_router(repo=repo, baas_service=baas)
    with pytest.raises(BindingNotFoundError):
        router.restart_device(
            binding_id=1001,
            device_uuid="DEVICE-001",
            operator=_make_operator("owner-001"),
        )

    baas.restart_devices.assert_not_called()


def test_restart_device_non_owner_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(id=1001, entity_id="owner-001")
    baas = MagicMock()

    router = _make_router(repo=repo, baas_service=baas)
    with pytest.raises(InvalidDeviceStatusError):
        router.restart_device(
            binding_id=1001,
            device_uuid="DEVICE-001",
            operator=_make_operator("hacker-002"),
        )
    baas.restart_devices.assert_not_called()


def test_restart_device_binding_not_found_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = None

    router = _make_router(repo=repo, baas_service=MagicMock())
    with pytest.raises(BindingNotFoundError):
        router.restart_device(
            binding_id=9999,
            device_uuid="DEVICE-001",
            operator=_make_operator("owner-001"),
        )


def test_restart_device_no_baas_service_raises():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(id=1001, entity_id="owner-001")

    router = _make_router(repo=repo, baas_service=None)
    with pytest.raises(DeviceServiceError):
        router.restart_device(
            binding_id=1001,
            device_uuid="DEVICE-001",
            operator=_make_operator("owner-001"),
        )


# ---------------------------------------------------------------------------
# get_device_connection_by_bot (bot_id entry, ext.binding.online) — §3
# ---------------------------------------------------------------------------


def test_get_connection_by_bot_resolves_binding_then_delegates_with_device_uuid():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_record(id=1001, device_provider="baas")

    publish_repo = MagicMock()
    publish_repo.get_latest_success_by_source_bot_id.return_value = _make_publish_record(
        ext={"binding": {"online": 1001}}
    )

    # The baas provider is a MagicMock; get_device_connection returns a sentinel.
    router = _make_router(repo=repo, baas_service=MagicMock(), publish_repo=publish_repo)
    provider = router._providers[BAAS_DEVICE_PROVIDER]
    provider.get_device_connection.return_value = "conn-info"

    operator = _make_operator("owner-001")
    result = router.get_device_connection_by_bot(
        bot_id="bot-001", operator=operator, port=8080, device_uuid="DEVICE-001"
    )

    assert result == "conn-info"
    publish_repo.get_latest_success_by_source_bot_id.assert_called_once_with(
        "bot-001", "dev"
    )
    # resolved binding_id + device_uuid reach the provider connection call.
    call = provider.get_device_connection.call_args
    assert call.kwargs["binding_id"] == 1001
    assert call.kwargs["device_uuid"] == "DEVICE-001"
    assert call.kwargs["port"] == 8080


def test_get_connection_by_bot_no_publish_raises():
    repo = MagicMock()
    publish_repo = MagicMock()
    publish_repo.get_latest_success_by_source_bot_id.return_value = None

    router = _make_router(repo=repo, baas_service=MagicMock(), publish_repo=publish_repo)
    with pytest.raises(BotPublishNotFoundError):
        router.get_device_connection_by_bot(
            bot_id="bot-001", operator=_make_operator("owner-001")
        )
