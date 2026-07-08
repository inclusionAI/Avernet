"""Tests for agentclaw.community.core.devices.services.device_service_router.DeviceServiceRouter."""

from __future__ import annotations

import inspect
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.models import (
    DeviceBindingInfo,
    DeviceBindingStatus,
    DeviceConnectionInfo,
    OperatorContext,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutDecision,
)
from agentclaw.community.core.devices.services.device_service import (
    ARCA_DEVICE_PROVIDER,
    BAAS_DEVICE_PROVIDER,
    LOCAL_DEVICE_PROVIDER,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    id: int = 1,
    entity_id: str = "u001",
    device_id: str = "staff_u001_default",
    device_provider: str = LOCAL_DEVICE_PROVIDER,
    status: str = DeviceBindingStatus.ACTIVE.value,
    env: str = "dev",
) -> DeviceBindingRecord:
    r = DeviceBindingRecord(
        id=id,
        entity_id=entity_id,
        entity_type="staff",
        device_id=device_id,
        device_provider=device_provider,
        env=env,
        device_props={"callback_token": "tok"},
        status=status,
        apply_reason=None,
        applied_by=entity_id,
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )
    return r


def _make_operator(staff_id: str = "u001") -> OperatorContext:
    return OperatorContext(
        staff_id=staff_id,
        staff=staff_id,
        nick_name="User",
        operator_name="User",
        tenant_id="default",
    )


class _FakeArcaBotCreateBaasRolloutPolicy:
    def __init__(self, provider: str = ARCA_DEVICE_PROVIDER) -> None:
        self.provider = provider
        self.decide = MagicMock(side_effect=self._decide)

    def _decide(self, **kwargs):
        return ArcaBotCreateBaasRolloutDecision(
            target_provider=self.provider,
            reason="test",
            rollout_version="test-version",
            engine_bucket=kwargs.get("engine_type") or "openclaw",
        )


def _make_router(is_local: bool = True):
    """Build a DeviceServiceRouter with a stub providers dict.

    Mirrors the composition root:
    - Local boot (``TestingDevicesModule``): only ``local`` registered,
      ``local`` is default.
    - Prod boot (``DevicesModule``): ``arca`` + ``baas`` registered
      (+ ``teclaw`` aliased to the baas service; no ``local``), ``arca``
      is default.
    """
    from agentclaw.community.core.devices.services.device_service_router import DeviceServiceRouter

    repo = MagicMock()
    bot_query = MagicMock()
    bot_sync = MagicMock()  # kept for return-tuple parity with old helper

    if is_local:
        providers: dict[str, object] = {LOCAL_DEVICE_PROVIDER: MagicMock()}
        default_key = LOCAL_DEVICE_PROVIDER
        policy = _FakeArcaBotCreateBaasRolloutPolicy(LOCAL_DEVICE_PROVIDER)
    else:
        baas = MagicMock()
        providers = {
            ARCA_DEVICE_PROVIDER: MagicMock(),
            BAAS_DEVICE_PROVIDER: baas,
            # teclaw is baas-managed → same service instance as baas.
            TECLAW_DEVICE_PROVIDER: baas,
        }
        default_key = ARCA_DEVICE_PROVIDER
        policy = _FakeArcaBotCreateBaasRolloutPolicy(ARCA_DEVICE_PROVIDER)

    router = DeviceServiceRouter(
        repository=repo,
        bot_query=bot_query,
        providers=providers,  # type: ignore[arg-type]
        default_provider_key=default_key,
        arca_baas_rollout_policy=policy,
    )

    return router, repo, bot_query, bot_sync


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestDeviceServiceRouterInit:
    def test_local_mode_has_local_provider_only(self):
        router, _, _, _ = _make_router(is_local=True)
        assert LOCAL_DEVICE_PROVIDER in router._providers
        assert ARCA_DEVICE_PROVIDER not in router._providers
        assert BAAS_DEVICE_PROVIDER not in router._providers

    def test_prod_mode_has_remote_providers_only(self):
        router, _, _, _ = _make_router(is_local=False)
        assert LOCAL_DEVICE_PROVIDER not in router._providers
        assert ARCA_DEVICE_PROVIDER in router._providers
        assert BAAS_DEVICE_PROVIDER in router._providers

    def test_local_mode_default_service_is_local_provider(self):
        router, _, _, _ = _make_router(is_local=True)
        assert router._default_service is router._providers[LOCAL_DEVICE_PROVIDER]

    def test_prod_mode_default_service_is_arca_provider(self):
        router, _, _, _ = _make_router(is_local=False)
        assert router._default_service is router._providers[ARCA_DEVICE_PROVIDER]

    def test_prod_mode_teclaw_aliased_to_baas_service(self):
        # teclaw is baas-managed: it maps to the baas service, not the arca
        # default (so get_device_connection uses baas get_ws_info, etc.).
        router, _, _, _ = _make_router(is_local=False)
        assert TECLAW_DEVICE_PROVIDER in router._providers
        assert (
            router._providers[TECLAW_DEVICE_PROVIDER]
            is router._providers[BAAS_DEVICE_PROVIDER]
        )

    def test_invalid_default_provider_key_raises(self):
        import pytest

        from agentclaw.community.core.devices.services.device_service_router import (
            DeviceServiceRouter,
        )

        with pytest.raises(ValueError, match="default_provider_key"):
            DeviceServiceRouter(
                repository=MagicMock(),
                bot_query=MagicMock(),
                providers={LOCAL_DEVICE_PROVIDER: MagicMock()},  # type: ignore[dict-item]
                default_provider_key="not-in-dict",
            )

    def test_create_routing_has_no_legacy_device_config_dependency(self):
        from agentclaw.community.core.devices.services.device_service_router import (
            DeviceServiceRouter,
        )

        params = inspect.signature(DeviceServiceRouter.__init__).parameters
        assert "arca_baas_rollout_policy" in params
        assert "device_config_service" not in params

    def test_testing_module_create_policy_returns_local_provider(self):
        # B11 (3.2): use the corp-free TestDevicesModule (the test/singlebox column's
        # device doubles) — same local create policy as the corp-flavored
        # TestingDevicesModule, without the corp import.
        from agentclaw.community.di.modules.infrastructure.test.devices import TestDevicesModule

        policy = TestDevicesModule().arca_bot_create_baas_rollout_policy()

        decision = policy.decide(
            user_id="u001",
            bot_type="personal",
            engine_type="openclaw",
        )

        assert decision.target_provider == LOCAL_DEVICE_PROVIDER
        assert decision.reason == "local_create_policy"


# ---------------------------------------------------------------------------
# apply_device explicit provider observability
# ---------------------------------------------------------------------------


class TestApplyDeviceObservability:
    def test_explicit_provider_route_logs_bot_context(self):
        router, _, _, _ = _make_router(is_local=False)
        operator = _make_operator("u001")

        with patch("agentclaw.community.core.devices.services.device_service_router.logger.info") as log_info:
            router.apply_device(
                apply_reason="restart",
                entity_id="u001",
                entity_type="staff",
                operator=operator,
                bot_id="bot001",
                engine="openclaw",
                bot_type="personal",
                device_provider=ARCA_DEVICE_PROVIDER,
            )

        messages = [
            str(call.args[0])
            for call in log_info.call_args_list
            if call.args
        ]
        assert any(
            "[apply_device] explicit provider route" in msg
            and "bot_id=bot001" in msg
            and "staff_id=u001" in msg
            and "engine=openclaw" in msg
            and "bot_type=personal" in msg
            and "device_provider=arca" in msg
            for msg in messages
        )


# ---------------------------------------------------------------------------
# _get_provider_for_binding
# ---------------------------------------------------------------------------


class TestGetProviderForBinding:
    def test_routes_to_correct_provider(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_id.return_value = record

        provider = router._get_provider_for_binding(1)
        assert provider is router._providers[LOCAL_DEVICE_PROVIDER]

    def test_unknown_provider_returns_default(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider="unknown_provider")
        repo.get_by_id.return_value = record

        provider = router._get_provider_for_binding(1)
        assert provider is router._default_service

    def test_not_found_returns_default(self):
        router, repo, _, _ = _make_router(is_local=True)
        repo.get_by_id.return_value = None

        provider = router._get_provider_for_binding(99)
        assert provider is router._default_service

    def test_teclaw_binding_routes_to_baas_not_default(self):
        # A teclaw (baas-managed) binding must resolve to the baas provider,
        # NOT fall to the arca default — else get_device_connection would use
        # the generic base composition instead of baas get_ws_info.
        router, repo, _, _ = _make_router(is_local=False)
        record = _make_record(device_provider=TECLAW_DEVICE_PROVIDER)
        repo.get_by_id.return_value = record

        provider = router._get_provider_for_binding(1)
        assert provider is router._providers[BAAS_DEVICE_PROVIDER]
        assert provider is not router._default_service  # not arca


# ---------------------------------------------------------------------------
# _get_provider_for_device_id
# ---------------------------------------------------------------------------


class TestGetProviderForDeviceId:
    def test_routes_to_correct_provider(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_device_id.return_value = record

        provider = router._get_provider_for_device_id("staff_u001_default")
        assert provider is router._providers[LOCAL_DEVICE_PROVIDER]

    def test_not_found_returns_default(self):
        router, repo, _, _ = _make_router(is_local=True)
        repo.get_by_device_id.return_value = None

        provider = router._get_provider_for_device_id("nonexistent")
        assert provider is router._default_service


# ---------------------------------------------------------------------------
# _get_provider_for_new_device
# ---------------------------------------------------------------------------


class TestGetProviderForNewDevice:
    def test_known_provider_returned(self):
        router, _, _, _ = _make_router(is_local=True)
        router._arca_baas_rollout_policy = _FakeArcaBotCreateBaasRolloutPolicy(LOCAL_DEVICE_PROVIDER)

        provider = router._get_provider_for_new_device("u001")
        assert provider is router._providers[LOCAL_DEVICE_PROVIDER]

    def test_unknown_create_provider_raises(self):
        router, _, _, _ = _make_router(is_local=True)
        router._arca_baas_rollout_policy = _FakeArcaBotCreateBaasRolloutPolicy("mystery_provider")

        with pytest.raises(DeviceServiceError, match="unknown create provider"):
            router._get_provider_for_new_device("u001")

    def test_bot_type_forwarded_to_arca_baas_rollout_policy(self):
        """New bot provider rollout is decided by the create policy
        (staff_id + bot_type + engine bucket), not by default-provider
        allocation lists."""
        router, _, _, _ = _make_router(is_local=False)
        router._arca_baas_rollout_policy = _FakeArcaBotCreateBaasRolloutPolicy(BAAS_DEVICE_PROVIDER)

        provider = router._get_provider_for_new_device(
            "u001",
            template_type="personalCoding",
            bot_type="personal",
            engine_type="claude_code",
        )

        router._arca_baas_rollout_policy.decide.assert_called_once_with(
            user_id="u001",
            bot_type="personal",
            engine_type="claude_code",
            template_type="personalCoding",
        )
        assert provider is router._providers[BAAS_DEVICE_PROVIDER]

    def test_missing_rollout_policy_fails_closed_instead_of_defaulting_to_arca(self):
        from agentclaw.community.core.devices.services.device_service_router import (
            DeviceServiceRouter,
        )

        router = DeviceServiceRouter(
            repository=MagicMock(),
            bot_query=MagicMock(),
            providers={ARCA_DEVICE_PROVIDER: MagicMock()},
            default_provider_key=ARCA_DEVICE_PROVIDER,
        )

        with pytest.raises(DeviceServiceError, match="rollout policy is not injected"):
            router._get_provider_for_new_device(
                "u001",
                bot_type="personal",
                engine_type="openclaw",
            )


# ---------------------------------------------------------------------------
# apply_device — routing delegation
# ---------------------------------------------------------------------------


class TestApplyDeviceRouting:
    def test_apply_delegates_to_provider(self):
        router, repo, _, _ = _make_router(is_local=True)

        # Mock the resolved provider
        mock_service = MagicMock()
        record = _make_record()
        mock_service.apply_device.return_value = record
        router._get_provider_for_new_device = MagicMock(return_value=mock_service)

        result = router.apply_device(
            apply_reason="test",
            entity_id="u001",
            entity_type="staff",
            operator=_make_operator(),
            bot_id="bot1",
        )

        mock_service.apply_device.assert_called_once()
        assert result is record

    def test_apply_forwards_template_type_and_bot_type_to_routing(self):
        """When BotService passes engine/template_type/bot_type through apply_device,
        the router must pass them to ``_get_provider_for_new_device`` so the
        provider decision can consume them."""
        router, _, _, _ = _make_router(is_local=False)
        mock_service = MagicMock()
        mock_service.apply_device.return_value = _make_record()
        spy = MagicMock(return_value=mock_service)
        router._get_provider_for_new_device = spy

        router.apply_device(
            apply_reason="test",
            entity_id="u001",
            entity_type="staff",
            operator=_make_operator(),
            bot_id="bot1",
            engine="claude_code",
            template_type="personalCoding",
            bot_type="personal",
        )

        spy.assert_called_once_with(
            "u001",
            engine_type="claude_code",
            template_type="personalCoding",
            bot_type="personal",
        )

    def test_device_provider_bypasses_create_policy(self):
        """Restart/start flows pass the previous lifecycle provider explicitly;
        this must skip creation rollout so an existing arca bot is never moved
        to BaaS by a later whitelist change."""
        router, _, _, _ = _make_router(is_local=False)
        arca_service = router._providers[ARCA_DEVICE_PROVIDER]
        arca_service.apply_device.return_value = _make_record(
            device_provider=ARCA_DEVICE_PROVIDER
        )
        router._arca_baas_rollout_policy = _FakeArcaBotCreateBaasRolloutPolicy(BAAS_DEVICE_PROVIDER)

        result = router.apply_device(
            apply_reason="restart",
            entity_id="u001",
            entity_type="staff",
            operator=_make_operator(),
            bot_id="bot1",
            bot_type="personal",
            device_provider=ARCA_DEVICE_PROVIDER,
        )

        assert result.device_provider == ARCA_DEVICE_PROVIDER
        arca_service.apply_device.assert_called_once()
        router._arca_baas_rollout_policy.decide.assert_not_called()

    def test_explicit_baas_device_provider_bypasses_create_policy(self):
        """BaaS-native create branches can opt out of ARCA rollout by passing
        their provider fact explicitly."""
        router, _, _, _ = _make_router(is_local=False)
        baas_service = router._providers[BAAS_DEVICE_PROVIDER]
        baas_service.apply_device.return_value = _make_record(
            device_provider=BAAS_DEVICE_PROVIDER
        )
        router._arca_baas_rollout_policy = _FakeArcaBotCreateBaasRolloutPolicy(ARCA_DEVICE_PROVIDER)

        result = router.apply_device(
            apply_reason="create",
            entity_id="u001",
            entity_type="staff",
            operator=_make_operator(),
            bot_id="bot1",
            bot_type="personal",
            device_provider=BAAS_DEVICE_PROVIDER,
        )

        assert result.device_provider == BAAS_DEVICE_PROVIDER
        baas_service.apply_device.assert_called_once()
        router._arca_baas_rollout_policy.decide.assert_not_called()

    def test_invalid_device_provider_fails_closed(self):
        """device_provider is a historical provider fact from restart.
        If the provider is missing, do not fall back to creation rollout."""
        router, _, _, _ = _make_router(is_local=False)
        router._arca_baas_rollout_policy = _FakeArcaBotCreateBaasRolloutPolicy(BAAS_DEVICE_PROVIDER)

        with pytest.raises(DeviceServiceError, match="device_provider"):
            router.apply_device(
                apply_reason="restart",
                entity_id="u001",
                entity_type="staff",
                operator=_make_operator(),
                bot_id="bot1",
                bot_type="personal",
                device_provider="some_new_provider",
            )

        router._arca_baas_rollout_policy.decide.assert_not_called()


# ---------------------------------------------------------------------------
# release_device — routing delegation
# ---------------------------------------------------------------------------


class TestReleaseDeviceRouting:
    def test_release_delegates_to_correct_provider(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_id.return_value = record

        mock_service = MagicMock()
        released = _make_record(status=DeviceBindingStatus.RELEASED.value)
        mock_service.release_device.return_value = released
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        result = router.release_device(
            binding_id=1,
            release_reason="test",
            operator=_make_operator(),
        )

        mock_service.release_device.assert_called_once_with(
            binding_id=1,
            release_reason="test",
            reset=False,
            operator=_make_operator(),
        )
        assert result is released


# ---------------------------------------------------------------------------
# get_device / get_device_by_device_id routing
# ---------------------------------------------------------------------------


class TestGetDeviceRouting:
    def test_get_device_delegates_to_provider(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_id.return_value = record

        mock_service = MagicMock()
        mock_service.get_device.return_value = record
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        result = router.get_device(binding_id=1)
        mock_service.get_device.assert_called_once_with(binding_id=1)
        assert result is record

    def test_get_device_by_device_id_delegates(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_device_id.return_value = record

        mock_service = MagicMock()
        mock_service.get_device_by_device_id.return_value = record
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        result = router.get_device_by_device_id(device_id="staff_u001_default")
        mock_service.get_device_by_device_id.assert_called_once_with(device_id="staff_u001_default")
        assert result is record


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


class TestListDevicesRouting:
    def test_list_devices_uses_default_service(self):
        router, _, _, _ = _make_router(is_local=True)
        record = _make_record()
        router._default_service = MagicMock()
        router._default_service.list_devices.return_value = (1, [record])

        total, items = router.list_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            status="ACTIVE",
        )
        assert total == 1
        router._default_service.list_devices.assert_called_once()


# ---------------------------------------------------------------------------
# report_device_alive / report_device_status routing
# ---------------------------------------------------------------------------


class TestReportDeviceRouting:
    def test_report_alive_routes_by_device_id(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_device_id.return_value = record

        mock_service = MagicMock()
        updated = _make_record(status=DeviceBindingStatus.ACTIVE.value)
        mock_service.report_device_alive.return_value = updated
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        result = router.report_device_alive(device_id="staff_u001_default", token="tok")
        mock_service.report_device_alive.assert_called_once_with(
            device_id="staff_u001_default", token="tok", skip_token_check=False
        )
        assert result is updated

    def test_report_status_routes_by_device_id(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_device_id.return_value = record

        mock_service = MagicMock()
        updated = _make_record()
        mock_service.report_device_status.return_value = updated
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        result = router.report_device_status(
            device_id="staff_u001_default",
            status="FAILED",
            message="oops",
            token="tok",
        )
        mock_service.report_device_status.assert_called_once()
        assert result is updated


# ---------------------------------------------------------------------------
# exec_shell routing
# ---------------------------------------------------------------------------


class TestExecShellRouting:
    def test_exec_shell_routes_by_device_id(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_device_id.return_value = record

        mock_service = MagicMock()
        mock_service.exec_shell.return_value = "output"
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        result = router.exec_shell("staff_u001_default", "ls -la")
        mock_service.exec_shell.assert_called_once_with("staff_u001_default", "ls -la")
        assert result == "output"


# ---------------------------------------------------------------------------
# batch_set_env
# ---------------------------------------------------------------------------


class TestBatchSetEnvRouting:
    def test_uses_default_service(self):
        router, _, _, _ = _make_router(is_local=True)
        router._default_service = MagicMock()
        router._default_service.batch_set_env.return_value = (2, [1, 2])

        count, updated_ids = router.batch_set_env(binding_ids=[1, 2], env="pre")
        assert count == 2
        router._default_service.batch_set_env.assert_called_once_with(
            binding_ids=[1, 2], env="pre"
        )


# ---------------------------------------------------------------------------
# get_device_connection routing
# ---------------------------------------------------------------------------


class TestGetDeviceConnectionRouting:
    def test_routes_to_correct_provider(self):
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        repo.get_by_id.return_value = record

        fake_conn = DeviceConnectionInfo(
            type="local", target="localhost:20003", token="", engine_type="openclaw"
        )
        mock_service = MagicMock()
        mock_service.get_device_connection.return_value = fake_conn
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_service

        conn = router.get_device_connection(
            binding_id=1,
            operator=_make_operator("u001"),
        )
        assert conn is fake_conn
        mock_service.get_device_connection.assert_called_once()


# ---------------------------------------------------------------------------
# list_connectable_devices routing
# ---------------------------------------------------------------------------


class TestListConnectableDevicesRouting:
    def test_without_connection_delegates_to_default(self):
        router, _, _, _ = _make_router(is_local=True)
        record = _make_record()
        info = DeviceBindingInfo(record=record)
        router._default_service = MagicMock()
        router._default_service.list_connectable_devices.return_value = (1, [info])

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=False,
        )
        assert total == 1
        router._default_service.list_connectable_devices.assert_called_once()

    def test_with_connection_enriches_cross_provider(self):
        """Items without connection info get enriched from the correct provider."""
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        info_no_conn = DeviceBindingInfo(record=record, connection=None)

        router._default_service = MagicMock()
        router._default_service.list_connectable_devices.return_value = (1, [info_no_conn])

        # The provider for this binding returns a conn
        fake_conn = DeviceConnectionInfo(
            type="local", target="localhost:20003", token="", engine_type="openclaw"
        )
        mock_local = MagicMock()
        mock_local.get_device_connection.return_value = fake_conn
        repo.get_by_id.return_value = record
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_local

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        assert items[0].connection is fake_conn

    def test_with_connection_item_already_has_connection_is_refreshed_by_provider(self):
        """List-stage connections are ignored and refreshed from the real provider."""
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        existing_conn = DeviceConnectionInfo(
            type="local", target="localhost:8888", token="t", engine_type="openclaw"
        )
        provider_conn = DeviceConnectionInfo(
            type="local", target="localhost:20003", token="t2", engine_type="openclaw"
        )
        info_with_conn = DeviceBindingInfo(record=record, connection=existing_conn)

        router._default_service = MagicMock()
        router._default_service.list_connectable_devices.return_value = (1, [info_with_conn])
        mock_local = MagicMock()
        mock_local.get_device_connection.return_value = provider_conn
        repo.get_by_id.return_value = record
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_local

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        assert items[0].connection is provider_conn

    def test_with_connection_default_service_fetches_bindings_only(self):
        """Default service must not prebuild provider-specific connection info."""
        router, repo, _, _ = _make_router(is_local=False)
        operator = _make_operator("u001")
        record = _make_record(
            id=1358189,
            device_provider=TECLAW_DEVICE_PROVIDER,
            env="pre",
        )
        wrong_default_conn = DeviceConnectionInfo(
            type="remote",
            target="ARCA_None:20003",
            token="bad-token",
            engine_type="teclaw",
        )
        baas_conn = DeviceConnectionInfo(
            type="baas",
            target="TECLAW_b_01KWENS22QDFZJGQBH6B7R77GF@4:20003",
            token="good-token",
            engine_type="teclaw",
        )
        arca_service = router._providers[ARCA_DEVICE_PROVIDER]
        baas_service = router._providers[BAAS_DEVICE_PROVIDER]
        arca_service.list_connectable_devices.return_value = (
            1,
            [DeviceBindingInfo(record=record, connection=wrong_default_conn)],
        )
        baas_service.get_device_connection.return_value = baas_conn
        repo.get_by_id.return_value = record

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="pre",
            with_connection=True,
            port=20003,
            operator=operator,
        )

        assert total == 1
        assert arca_service.list_connectable_devices.call_args.kwargs["with_connection"] is False
        baas_service.get_device_connection.assert_called_once_with(
            binding_id=1358189,
            operator=operator,
            port=20003,
        )
        assert items[0].connection is baas_conn

    def test_with_connection_baas_binding_uses_baas_provider(self):
        router, repo, _, _ = _make_router(is_local=False)
        operator = _make_operator("u001")
        record = _make_record(
            id=1358190,
            device_provider=BAAS_DEVICE_PROVIDER,
            env="pre",
        )
        baas_conn = DeviceConnectionInfo(
            type="baas",
            target="BAAS_b_01KWENS22QDFZJGQBH6B7R77GF@4:20003",
            token="good-token",
            engine_type="openclaw",
        )
        arca_service = router._providers[ARCA_DEVICE_PROVIDER]
        baas_service = router._providers[BAAS_DEVICE_PROVIDER]
        arca_service.list_connectable_devices.return_value = (
            1,
            [DeviceBindingInfo(record=record, connection=None)],
        )
        baas_service.get_device_connection.return_value = baas_conn
        repo.get_by_id.return_value = record

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="pre",
            with_connection=True,
            port=20003,
            operator=operator,
        )

        assert total == 1
        baas_service.get_device_connection.assert_called_once_with(
            binding_id=1358190,
            operator=operator,
            port=20003,
        )
        assert items[0].connection is baas_conn

    def test_with_connection_arca_binding_uses_arca_provider(self):
        router, repo, _, _ = _make_router(is_local=False)
        operator = _make_operator("u001")
        record = _make_record(
            id=1358191,
            device_provider=ARCA_DEVICE_PROVIDER,
            env="pre",
        )
        arca_conn = DeviceConnectionInfo(
            type="remote",
            target="ARCA_ARCA-SANDBOX-1@0:20003",
            token="arca-token",
            engine_type="openclaw",
        )
        arca_service = router._providers[ARCA_DEVICE_PROVIDER]
        arca_service.list_connectable_devices.return_value = (
            1,
            [DeviceBindingInfo(record=record, connection=None)],
        )
        arca_service.get_device_connection.return_value = arca_conn
        repo.get_by_id.return_value = record

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="pre",
            with_connection=True,
            port=20003,
            operator=operator,
        )

        assert total == 1
        arca_service.get_device_connection.assert_called_once_with(
            binding_id=1358191,
            operator=operator,
            port=20003,
        )
        assert items[0].connection is arca_conn

    def test_with_connection_error_yields_none(self):
        """Connection retrieval error results in None connection (no exception propagated)."""
        router, repo, _, _ = _make_router(is_local=True)
        record = _make_record(device_provider=LOCAL_DEVICE_PROVIDER)
        info_no_conn = DeviceBindingInfo(record=record, connection=None)

        router._default_service = MagicMock()
        router._default_service.list_connectable_devices.return_value = (1, [info_no_conn])

        mock_local = MagicMock()
        mock_local.get_device_connection.side_effect = RuntimeError("provider error")
        repo.get_by_id.return_value = record
        router._providers[LOCAL_DEVICE_PROVIDER] = mock_local

        total, items = router.list_connectable_devices(
            entity_id="u001",
            entity_type="staff",
            env="dev",
            with_connection=True,
            operator=_make_operator("u001"),
        )
        assert total == 1
        assert items[0].connection is None


class TestApplyDeviceTemplateConfigPassthrough:
    """Regression: `apply_device(template_config=...)` must reach the
    underlying provider. Guards the signature-drift bug from commit
    db9030298 where router/local apply_device dropped the new kwarg
    and broke bot creation with `TypeError: unexpected keyword argument
    'template_config'`."""

    def test_router_passes_template_config_to_provider(self):
        router, _, _, _ = _make_router(is_local=True)
        provider = router._providers[LOCAL_DEVICE_PROVIDER]

        tc = {"image": "reg/repo:tag", "resource_spec": {"cpu": 4, "memory": 8}}
        router.apply_device(
            apply_reason="test",
            entity_id="u001",
            entity_type="staff",
            operator=_make_operator("u001"),
            bot_id="bot001",
            template_config=tc,
        )

        provider.apply_device.assert_called_once()
        assert provider.apply_device.call_args.kwargs.get("template_config") == tc

    def test_local_apply_device_accepts_template_config(self):
        """Direct call into LocalDeviceService.apply_device must not raise
        TypeError when caller passes template_config."""
        from agentclaw.community.core.devices.services.local_device_service import LocalDeviceService

        svc = LocalDeviceService.__new__(LocalDeviceService)
        svc._repo = MagicMock()
        svc._default_engine = "openclaw"

        with patch.object(svc, "_generate_device_id", return_value=("dev_id", "bolt_id")), \
             patch.object(svc, "_setup_directory", return_value=[]), \
             patch.object(svc, "_do_allocate") as mock_alloc, \
             patch("agentclaw.community.utils.env_utils.get_current_env", return_value="dev"):
            mock_alloc.return_value = MagicMock(device_props={})
            svc._repo.get_released_binding.return_value = None
            svc._repo.create_binding = MagicMock()

            try:
                svc.apply_device(
                    apply_reason="test",
                    entity_id="u001",
                    entity_type="staff",
                    operator=_make_operator("u001"),
                    bot_id="bot001",
                    template_config={"image": "img"},
                )
            except TypeError as e:
                if "template_config" in str(e):
                    raise AssertionError(
                        "LocalDeviceService.apply_device rejected template_config kwarg"
                    ) from e
                # Other TypeErrors from downstream mocks are fine — we only
                # care that the signature itself accepts template_config.

            # Confirm template_config was forwarded to _do_allocate
            assert mock_alloc.call_args.kwargs.get("template_config") == {"image": "img"}
