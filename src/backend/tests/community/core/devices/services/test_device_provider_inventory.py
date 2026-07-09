"""Tests for device-provider inventory aggregation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter


def _record(
    *,
    id: int,
    provider: str,
    status: str,
    env: str = "prod",
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=id,
        entity_id="u001",
        entity_type="staff",
        device_id=f"device-{id}",
        device_provider=provider,
        env=env,
        device_props={},
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


def _service(repo: MagicMock) -> DeviceService:
    return DeviceService(
        repository=repo,
        bot_query=MagicMock(),
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
    )


def test_provider_inventory_counts_provider_status_and_env_by_pages():
    repo = MagicMock()
    repo.list_bindings.side_effect = [
        (
            5,
            [
                _record(id=1, provider="arca", status="ACTIVE", env="prod"),
                _record(id=2, provider="baas", status="ACTIVE", env="prod"),
            ],
        ),
        (
            5,
            [
                _record(id=3, provider="arca", status="FAILED", env="prod"),
                _record(id=4, provider="teclaw", status="ACTIVE", env="pre"),
            ],
        ),
    ]
    service = _service(repo)

    result = service.get_provider_inventory(
        entity_id=None,
        entity_type="staff",
        env=None,
        status=None,
        page_size=2,
        max_pages=2,
    )

    assert result["total"] == 5
    assert result["scanned"] == 4
    assert result["truncated"] is True
    assert result["by_provider"] == {
        "arca": {
            "total": 2,
            "by_status": {"ACTIVE": 1, "FAILED": 1},
            "by_env": {"prod": 2},
        },
        "baas": {
            "total": 1,
            "by_status": {"ACTIVE": 1},
            "by_env": {"prod": 1},
        },
        "teclaw": {
            "total": 1,
            "by_status": {"ACTIVE": 1},
            "by_env": {"pre": 1},
        },
    }
    assert repo.list_bindings.call_args_list[0].kwargs["page"] == 1
    assert repo.list_bindings.call_args_list[1].kwargs["page"] == 2


def test_provider_inventory_falls_back_to_current_env_when_env_is_none():
    """当 env=None 时,service 应该 fallback 到 get_current_env() 并透传给 repo。"""
    from unittest.mock import patch

    repo = MagicMock()
    repo.list_bindings.return_value = (
        1,
        [_record(id=1, provider="arca", status="ACTIVE", env="pre")],
    )
    service = _service(repo)

    with patch(
        "agentclaw.community.utils.env_utils.get_current_env", return_value="pre"
    ) as mock_get_env:
        result = service.get_provider_inventory(
            entity_id=None,
            entity_type="staff",
            env=None,
            status=None,
            page_size=100,
            max_pages=1,
        )

    mock_get_env.assert_called_once()
    # repo 应该收到 resolved env 而不是 None
    assert repo.list_bindings.call_args.kwargs["env"] == "pre"
    # filters 里的 env 也应该是 resolved 后的值
    assert result["filters"]["env"] == "pre"


def test_provider_inventory_stops_when_all_rows_are_scanned():
    repo = MagicMock()
    repo.list_bindings.return_value = (
        1,
        [_record(id=1, provider="arca", status="ACTIVE", env="prod")],
    )
    service = _service(repo)

    result = service.get_provider_inventory(
        entity_id="u001",
        entity_type="staff",
        env="prod",
        status="ACTIVE",
        page_size=100,
        max_pages=20,
    )

    assert result["total"] == 1
    assert result["scanned"] == 1
    assert result["truncated"] is False
    assert result["filters"] == {
        "entity_id": "u001",
        "entity_type": "staff",
        "env": "prod",
        "status": "ACTIVE",
    }
    repo.list_bindings.assert_called_once()


def test_router_delegates_provider_inventory_to_default_service():
    default_service = MagicMock()
    default_service.get_provider_inventory.return_value = {"by_provider": {"arca": {"total": 1}}}

    router = DeviceServiceRouter(
        repository=MagicMock(),
        bot_query=MagicMock(),
        providers={"arca": default_service},
        default_provider_key="arca",
    )

    result = router.get_provider_inventory(
        entity_id=None,
        entity_type=None,
        env="prod",
        status=None,
        page_size=100,
        max_pages=1,
    )

    assert result == {"by_provider": {"arca": {"total": 1}}}
    default_service.get_provider_inventory.assert_called_once_with(
        entity_id=None,
        entity_type=None,
        env="prod",
        status=None,
        page_size=100,
        max_pages=1,
    )
