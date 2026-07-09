"""Unit tests for DeviceBindingQueryService.

Tests the Python-orchestrated multi-step query logic that replaces
the original complex JOIN SQL in DeviceBindingRepository.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from secbaas.core.repository.ac_bot import AcBotRecord
from secbaas.core.repository.device_binding import DeviceBindingRecord
from secbaas.core.service.device_binding_query import DeviceBindingQueryService

# ── Helpers ──


def _make_ac_bot(
    bot_id: str = "bot-001",
    entity_id: str = "entity-001",
    binding_id: int | None = 100,
    bot_type: str = "service",
    env: str = "prod",
    status: str = "ACTIVE",
    active_engine: str | None = "openclaw",
    device_id: str | None = None,
) -> AcBotRecord:
    return AcBotRecord(
        id=1,
        bot_id=bot_id,
        bot_name="Test Bot",
        bot_desc=None,
        entity_id=entity_id,
        entity_type="staff",
        creator_id="creator",
        owner_id="owner",
        engine_types=None,
        status=status,
        binding_id=binding_id,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
        modifier_id=None,
        share_policy=None,
        is_delete=0,
        active_engine=active_engine,
        device_id=device_id or f"staff_{entity_id}_{bot_id}",
        env=env,
        owner_name="owner",
        public="",
        ext=None,
        bot_type=bot_type,
    )


def _make_binding(
    binding_id: int = 100,
    device_id: str = "device-001",
    device_provider: str = "arca",
    status: str = "ACTIVE",
    env: str = "prod",
    device_props: dict[str, Any] | None = None,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=binding_id,
        entity_id="entity-001",
        entity_type="staff",
        device_id=device_id,
        device_provider=device_provider,
        env=env,
        device_props=device_props or {"sandbox_id": "ARCA-SANDBOX-001@0"},
        status=status,
        apply_reason=None,
        applied_by="system",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


@dataclass(slots=True)
class _FakeBotRecord:
    """Minimal bot record for _resolve_devices_from_binding tests."""

    id: int
    bot_uuid: str
    tenant: str
    env: str
    status: str
    is_deleted: int
    name: str = "fake-bot"
    description: str | None = None
    template_uuid: str | None = None
    replica_desired: int = 1
    replica_minimum: int = 1
    replica_maximum: int = 10
    auto_scaling_enabled: int = 0
    sla_grade: str = "standard"
    extra_config: dict[str, Any] | None = None
    creator: str = "system"
    modifier: str = "system"
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None


@dataclass(slots=True)
class _FakeDeviceRecord:
    """Minimal device record for _resolve_devices_from_binding tests."""

    id: int
    device_uuid: str
    tenant: str
    env: str
    domain: str
    is_deleted: int
    status: str
    provider_type: str | None = "ARCA"
    provider_device_id: str | None = "ARCA-SANDBOX-002@0"
    provider_device_props: dict[str, Any] | None = None
    extra_config: dict[str, Any] | None = None
    err_msg: str | None = None
    creator: str = "system"
    modifier: str = "system"
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None


# ── Fixtures ──


@pytest.fixture
def mock_ac_bot_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_ac_bot_publish_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_binding_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_bot_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_device_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(
    mock_ac_bot_repo: MagicMock,
    mock_ac_bot_publish_repo: MagicMock,
    mock_binding_repo: MagicMock,
    mock_bot_repo: MagicMock,
    mock_device_repo: MagicMock,
) -> DeviceBindingQueryService:
    return DeviceBindingQueryService(
        ac_bot_repo=mock_ac_bot_repo,
        ac_bot_publish_repo=mock_ac_bot_publish_repo,
        binding_repo=mock_binding_repo,
        bot_repo=mock_bot_repo,
        device_repo=mock_device_repo,
    )


# ── TestListPaasDeviceByBotPersonal ──


class TestListPaasDeviceByBotPersonal:
    def test_returns_device_when_binding_active(self, service, mock_binding_repo):
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            status="ACTIVE",
            device_props={
                "sandbox_id": "ARCA-SANDBOX-001@0",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "refresh_fail_count": 0,
            },
        )

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert len(result) == 1
        device = result[0]
        assert device["paas_device_id"] == "ARCA-SANDBOX-001@0"
        assert device["query_status"] == "personal"
        assert device["source_table"] == "ac_binding"
        assert device["source_table_id"] == 100
        assert device["status"] == "ACTIVE"

    def test_returns_empty_when_binding_not_active(self, service, mock_binding_repo):
        """Bug 1 fix: non-ACTIVE binding should be filtered out."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            status="STOPPED",
        )

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert result == []

    def test_returns_empty_when_binding_not_found(self, service, mock_binding_repo):
        mock_binding_repo.get_by_id.return_value = None

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=999
        )

        assert result == []


# ── TestQueryServiceDevicesDraft ──


class TestQueryServiceDevicesDraft:
    def test_returns_device_when_binding_active(
        self, service, mock_ac_bot_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_active_by_entity_id_bot_id_env.return_value = _make_ac_bot(
            binding_id=100
        )
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100, status="ACTIVE"
        )

        result = service._query_service_devices_draft("bot-001", "entity-001", "prod")

        assert len(result) == 1
        assert result[0]["query_status"] == "draft"
        assert result[0]["source_table"] == "ac_binding"

    def test_returns_empty_when_binding_not_active(
        self, service, mock_ac_bot_repo, mock_binding_repo
    ):
        """Bug 1 fix: draft path should filter non-ACTIVE binding."""
        mock_ac_bot_repo.get_active_by_entity_id_bot_id_env.return_value = _make_ac_bot(
            binding_id=100
        )
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100, status="STOPPED"
        )

        result = service._query_service_devices_draft("bot-001", "entity-001", "prod")

        assert result == []

    def test_returns_empty_when_bot_not_found(self, service, mock_ac_bot_repo):
        mock_ac_bot_repo.get_active_by_entity_id_bot_id_env.return_value = None

        result = service._query_service_devices_draft("bot-001", "entity-001", "prod")

        assert result == []

    def test_returns_empty_when_no_binding_id(self, service, mock_ac_bot_repo):
        mock_ac_bot_repo.get_active_by_entity_id_bot_id_env.return_value = _make_ac_bot(
            binding_id=None
        )

        result = service._query_service_devices_draft("bot-001", "entity-001", "prod")

        assert result == []

    def test_returns_empty_when_binding_not_found(
        self, service, mock_ac_bot_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_active_by_entity_id_bot_id_env.return_value = _make_ac_bot(
            binding_id=100
        )
        mock_binding_repo.get_by_id.return_value = None

        result = service._query_service_devices_draft("bot-001", "entity-001", "prod")

        assert result == []


# ── TestQueryServiceDevicesValidating ──


class TestQueryServiceDevicesValidating:
    def test_returns_devices_from_single_binding(
        self,
        service,
        mock_ac_bot_publish_repo,
        mock_binding_repo,
        mock_bot_repo,
        mock_device_repo,
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [200]
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id="BOT-uuid-001"
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
                provider_device_id="ARCA-SANDBOX-VALID-001@0",
            )
        ]

        result = service._query_service_devices_validating(
            "bot-001", "entity-001", "prod"
        )

        assert len(result) == 1
        assert result[0]["query_status"] == "validating"
        assert result[0]["paas_device_id"] == "ARCA-SANDBOX-VALID-001@0"
        assert result[0]["source_table"] == "baas_device"
        mock_ac_bot_publish_repo.get_binding_ids.assert_called_once_with(
            source_bot_id="bot-001",
            status="validating",
            owner_id="entity-001",
            env="prod",
        )

    def test_returns_devices_from_multiple_bindings(
        self,
        service,
        mock_ac_bot_publish_repo,
        mock_binding_repo,
        mock_bot_repo,
        mock_device_repo,
    ):
        """Bug 3 fix: multiple publish records → multiple binding_ids → merged device list."""
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [200, 300]

        binding_200 = _make_binding(binding_id=200, device_id="BOT-uuid-001")
        binding_300 = _make_binding(binding_id=300, device_id="BOT-uuid-002")

        def get_by_id_side_effect(binding_id):
            if binding_id == 200:
                return binding_200
            elif binding_id == 300:
                return binding_300
            return None

        mock_binding_repo.get_by_id.side_effect = get_by_id_side_effect

        bot_1 = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        bot_2 = _FakeBotRecord(
            id=2,
            bot_uuid="BOT-uuid-002",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_bot_repo.get_active_by_bot_uuid_only.side_effect = [bot_1, bot_2]

        device_1 = _FakeDeviceRecord(
            id=10,
            device_uuid="DEV-uuid-001",
            tenant="team_claw",
            env="prod",
            domain="default",
            is_deleted=0,
            status="ACTIVE",
            provider_device_id="ARCA-SANDBOX-V1@0",
        )
        device_2 = _FakeDeviceRecord(
            id=20,
            device_uuid="DEV-uuid-002",
            tenant="team_claw",
            env="prod",
            domain="default",
            is_deleted=0,
            status="ACTIVE",
            provider_device_id="ARCA-SANDBOX-V2@0",
        )
        mock_device_repo.list_active_devices_by_bot_id.side_effect = [
            [device_1],
            [device_2],
        ]

        result = service._query_service_devices_validating(
            "bot-001", "entity-001", "prod"
        )

        assert len(result) == 2
        assert result[0]["paas_device_id"] == "ARCA-SANDBOX-V1@0"
        assert result[1]["paas_device_id"] == "ARCA-SANDBOX-V2@0"

    def test_deduplicates_binding_ids(
        self,
        service,
        mock_ac_bot_publish_repo,
        mock_binding_repo,
        mock_bot_repo,
        mock_device_repo,
    ):
        """Multiple publish records pointing to the same binding_id should be deduplicated."""
        # get_binding_ids deduplicates at the repository level, returns [200] only
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [200]

        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id="BOT-uuid-001"
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
            )
        ]

        result = service._query_service_devices_validating(
            "bot-001", "entity-001", "prod"
        )

        # Dedup happens in get_binding_ids, so service sees [200] only → 1 device
        assert len(result) == 1

    def test_returns_empty_when_no_publish_records(
        self, service, mock_ac_bot_publish_repo
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = []

        result = service._query_service_devices_validating(
            "bot-001", "entity-001", "prod"
        )

        assert result == []

    def test_passes_env_to_get_binding_ids(self, service, mock_ac_bot_publish_repo):
        """Bug 2 verification: env parameter should be passed to get_binding_ids."""
        mock_ac_bot_publish_repo.get_binding_ids.return_value = []

        service._query_service_devices_validating("bot-001", "entity-001", "pre")

        mock_ac_bot_publish_repo.get_binding_ids.assert_called_once_with(
            source_bot_id="bot-001",
            status="validating",
            owner_id="entity-001",
            env="pre",
        )

    def test_returns_empty_when_binding_has_no_device_id(
        self, service, mock_ac_bot_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [200]
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id=""
        )

        result = service._query_service_devices_validating(
            "bot-001", "entity-001", "prod"
        )

        assert result == []

    def test_returns_empty_when_bot_not_found(
        self, service, mock_ac_bot_publish_repo, mock_binding_repo, mock_bot_repo
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [200]
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id="BOT-uuid-001"
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = None

        result = service._query_service_devices_validating(
            "bot-001", "entity-001", "prod"
        )

        assert result == []


# ── TestQueryServiceDevicesOnline ──


class TestQueryServiceDevicesOnline:
    def test_returns_devices_from_single_binding(
        self,
        service,
        mock_ac_bot_publish_repo,
        mock_binding_repo,
        mock_bot_repo,
        mock_device_repo,
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [300]
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=300, device_id="BOT-uuid-001"
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
                provider_device_id="ARCA-SANDBOX-ONLINE-001@0",
            )
        ]

        result = service._query_service_devices_online("bot-001", "entity-001", "prod")

        assert len(result) == 1
        assert result[0]["query_status"] == "online"
        assert result[0]["paas_device_id"] == "ARCA-SANDBOX-ONLINE-001@0"
        mock_ac_bot_publish_repo.get_binding_ids.assert_called_once_with(
            source_bot_id="bot-001", status="success", owner_id="entity-001", env="prod"
        )

    def test_returns_devices_from_multiple_bindings(
        self,
        service,
        mock_ac_bot_publish_repo,
        mock_binding_repo,
        mock_bot_repo,
        mock_device_repo,
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [300, 400]

        binding_300 = _make_binding(binding_id=300, device_id="BOT-uuid-001")
        binding_400 = _make_binding(binding_id=400, device_id="BOT-uuid-002")
        mock_binding_repo.get_by_id.side_effect = [binding_300, binding_400]

        bot_1 = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        bot_2 = _FakeBotRecord(
            id=2,
            bot_uuid="BOT-uuid-002",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_bot_repo.get_active_by_bot_uuid_only.side_effect = [bot_1, bot_2]

        device_1 = _FakeDeviceRecord(
            id=10,
            device_uuid="DEV-uuid-001",
            tenant="team_claw",
            env="prod",
            domain="default",
            is_deleted=0,
            status="ACTIVE",
            provider_device_id="ARCA-SANDBOX-O1@0",
        )
        device_2 = _FakeDeviceRecord(
            id=20,
            device_uuid="DEV-uuid-002",
            tenant="team_claw",
            env="prod",
            domain="default",
            is_deleted=0,
            status="ACTIVE",
            provider_device_id="ARCA-SANDBOX-O2@0",
        )
        mock_device_repo.list_active_devices_by_bot_id.side_effect = [
            [device_1],
            [device_2],
        ]

        result = service._query_service_devices_online("bot-001", "entity-001", "prod")

        assert len(result) == 2
        assert result[0]["paas_device_id"] == "ARCA-SANDBOX-O1@0"
        assert result[1]["paas_device_id"] == "ARCA-SANDBOX-O2@0"

    def test_returns_empty_when_no_publish_records(
        self, service, mock_ac_bot_publish_repo
    ):
        mock_ac_bot_publish_repo.get_binding_ids.return_value = []

        result = service._query_service_devices_online("bot-001", "entity-001", "prod")

        assert result == []


# ── TestListPaasDeviceByBotService ──


class TestListPaasDeviceByBotService:
    def test_combines_draft_validating_online(
        self,
        service,
        mock_ac_bot_repo,
        mock_ac_bot_publish_repo,
        mock_binding_repo,
        mock_bot_repo,
        mock_device_repo,
    ):
        # draft path
        mock_ac_bot_repo.get_active_by_entity_id_bot_id_env.return_value = _make_ac_bot(
            binding_id=100
        )
        draft_binding = _make_binding(binding_id=100, status="ACTIVE")

        # validating + online paths: same binding_id=200 for both
        mock_ac_bot_publish_repo.get_binding_ids.return_value = [200]
        validate_binding = _make_binding(binding_id=200, device_id="BOT-uuid-001")

        # get_by_id is called for draft (binding_id=100) and validating/online (binding_id=200)
        # Use a side_effect function to return the right binding based on the id
        def get_by_id_side_effect(binding_id):
            if binding_id == 100:
                return draft_binding
            elif binding_id == 200:
                return validate_binding
            return None

        mock_binding_repo.get_by_id.side_effect = get_by_id_side_effect

        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
                provider_device_id="ARCA-SANDBOX-ONLINE@0",
            )
        ]

        result = service.list_paas_device_by_bot_service(
            bot_id="bot-001",
            entity_id="entity-001",
            statuses=["draft", "validating", "online"],
            env="prod",
        )

        # draft: 1 device (from ac_binding)
        # + validating: 1 device (from baas_device via binding_id=200)
        # + online: 1 device (from baas_device via same binding_id=200)
        # Total: 3
        assert len(result) == 3
        statuses_found = [d["query_status"] for d in result]
        assert "draft" in statuses_found
        assert "validating" in statuses_found
        assert "online" in statuses_found

    def test_skips_unknown_status(self, service):
        result = service.list_paas_device_by_bot_service(
            bot_id="bot-001",
            entity_id="entity-001",
            statuses=["unknown"],
            env="prod",
        )

        assert result == []

    def test_empty_statuses(self, service):
        result = service.list_paas_device_by_bot_service(
            bot_id="bot-001",
            entity_id="entity-001",
            statuses=[],
            env="prod",
        )

        assert result == []


# ── TestResolveDevicesFromBinding ──


class TestResolveDevicesFromBinding:
    def test_returns_devices_when_bot_found(
        self, service, mock_binding_repo, mock_bot_repo, mock_device_repo
    ):
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id="BOT-uuid-001"
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
                provider_device_id="ARCA-SANDBOX-001@0",
            )
        ]

        result = service._resolve_devices_from_binding(200, "validating")

        assert len(result) == 1
        assert result[0]["device_uuid"] == "DEV-uuid-001"
        assert result[0]["query_status"] == "validating"
        assert result[0]["source_table"] == "baas_device"

    def test_returns_empty_when_binding_not_found(self, service, mock_binding_repo):
        mock_binding_repo.get_by_id.return_value = None

        result = service._resolve_devices_from_binding(999, "online")

        assert result == []

    def test_returns_empty_when_no_device_id(self, service, mock_binding_repo):
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id=""
        )

        result = service._resolve_devices_from_binding(200, "online")

        assert result == []

    def test_returns_empty_when_bot_not_found(
        self, service, mock_binding_repo, mock_bot_repo
    ):
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=200, device_id="BOT-uuid-001"
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = None

        result = service._resolve_devices_from_binding(200, "validating")

        assert result == []


# ── TestListPaasDeviceByBotPersonalBaas ──


class TestListPaasDeviceByBotPersonalBaas:
    """Tests for the baas provider branch of list_paas_device_by_bot_personal."""

    def test_baas_provider_returns_devices_from_resolve(
        self, service, mock_binding_repo, mock_bot_repo, mock_device_repo
    ):
        """When device_provider='baas', should call _resolve_devices_from_binding."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            device_id="BOT-uuid-001",
            device_provider="baas",
            status="ACTIVE",
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
                provider_type="ARCA",
                provider_device_id="ARCA-SANDBOX-c7be9acb@10",
            )
        ]

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert len(result) == 1
        device = result[0]
        assert device["paas_device_id"] == "ARCA-SANDBOX-c7be9acb@10"
        assert device["device_uuid"] == "DEV-uuid-001"
        assert device["query_status"] == "personal"
        assert device["source_table"] == "baas_device"
        assert device["provider_type"] == "ARCA"

    def test_baas_provider_returns_empty_when_bot_not_found(
        self, service, mock_binding_repo, mock_bot_repo
    ):
        """When baas bot not found, should return empty list."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            device_id="BOT-uuid-001",
            device_provider="baas",
            status="ACTIVE",
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = None

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert result == []

    def test_baas_provider_returns_empty_when_no_active_devices(
        self, service, mock_binding_repo, mock_bot_repo, mock_device_repo
    ):
        """When baas_device has no ACTIVE records, should return empty list."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            device_id="BOT-uuid-001",
            device_provider="baas",
            status="ACTIVE",
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = []

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert result == []

    def test_baas_provider_case_insensitive(
        self, service, mock_binding_repo, mock_bot_repo, mock_device_repo
    ):
        """device_provider='BAAS' (uppercase) should also trigger baas branch."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            device_id="BOT-uuid-001",
            device_provider="BAAS",
            status="ACTIVE",
        )
        mock_bot_repo.get_active_by_bot_uuid_only.return_value = _FakeBotRecord(
            id=1,
            bot_uuid="BOT-uuid-001",
            tenant="team_claw",
            env="prod",
            status="ACTIVE",
            is_deleted=0,
        )
        mock_device_repo.list_active_devices_by_bot_id.return_value = [
            _FakeDeviceRecord(
                id=10,
                device_uuid="DEV-uuid-001",
                tenant="team_claw",
                env="prod",
                domain="default",
                is_deleted=0,
                status="ACTIVE",
                provider_device_id="ARCA-SANDBOX-001@0",
            )
        ]

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert len(result) == 1
        assert result[0]["source_table"] == "baas_device"


# ── TestListPaasDeviceByBotPersonalArcaRegression ──


class TestListPaasDeviceByBotPersonalArcaRegression:
    """Regression tests: arca provider behavior must be unchanged."""

    def test_arca_provider_returns_sandbox_id(
        self, service, mock_binding_repo, mock_bot_repo, mock_device_repo
    ):
        """When device_provider='arca', should return sandbox_id from device_props."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            device_id="device-001",
            device_provider="arca",
            status="ACTIVE",
            device_props={
                "sandbox_id": "ARCA-SANDBOX-001@0",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "refresh_fail_count": 2,
            },
        )

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert len(result) == 1
        device = result[0]
        assert device["paas_device_id"] == "ARCA-SANDBOX-001@0"
        assert device["provider_type"] == "arca"
        assert device["query_status"] == "personal"
        assert device["source_table"] == "ac_binding"
        assert device["source_table_id"] == 100
        assert device["device_uuid"] is None
        assert device["refresh_fail_count"] == 2
        # Should NOT call baas repos
        mock_bot_repo.get_active_by_bot_uuid_only.assert_not_called()
        mock_device_repo.list_active_devices_by_bot_id.assert_not_called()

    def test_arca_provider_default_when_no_device_provider(
        self, service, mock_binding_repo, mock_bot_repo, mock_device_repo
    ):
        """When device_provider is None/empty, should default to arca path."""
        mock_binding_repo.get_by_id.return_value = _make_binding(
            binding_id=100,
            device_id="device-001",
            device_provider="",
            status="ACTIVE",
            device_props={"sandbox_id": "ARCA-SANDBOX-001@0"},
        )

        result = service.list_paas_device_by_bot_personal(
            bot_id="bot-001", binding_id=100
        )

        assert len(result) == 1
        assert result[0]["source_table"] == "ac_binding"
        mock_bot_repo.get_active_by_bot_uuid_only.assert_not_called()
