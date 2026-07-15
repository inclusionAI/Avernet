"""Coverage tests for PaasServiceFacade — targets uncovered branches."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ArcaCreateConfig,
    ArcaCreationResult,
    ArcaDeviceConfig,
    CommandResult,
    DeviceCreationError,
    DeviceFacadeException,
    DeviceInfo,
    DockerCreationResult,
    DockerDeviceConfig,
    K8sCreationResult,
    K8sDeviceConfig,
    LocalCreateConfig,
    LocalCreationResult,
    LocalDeviceConfig,
    PaasError,
    PoolabCreateConfig,
    PoolabCreationResult,
    PoolabDeviceConfig,
    SigmaDeviceConfig,
    TeClawCreationResult,
    TeClawDeviceConfig,
)
from secbaas.community.api.health_check.bot import TTLInfo
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DockerTemplateConfig,
    K8sTemplateConfig,
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
    TeClawTemplateConfig,
)
from secbaas.community.api.tenant_manage import TenantType
from secbaas.community.core.service.paas import ErrorCode, PaasServiceFacade
from secbaas.community.core.service.paas._factory import PaasServiceFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_template(
    tenant="test-tenant",
    template_uuid="tpl-uuid",
    template_id=42,
    platform_type="ARCA",
):
    type_map = {
        "ARCA": (
            ArcaTemplateConfig,
            dict(
                type="ARCA",
                base_url="https://arca.test",
                api_key="key",
                template_id="tpl-123",
                oss_mount_id="mount-1",
                arca_template_id_pre=None,
                arca_template_id_prod=None,
            ),
        ),
        "SIGMA": (
            SigmaTemplateConfig,
            dict(
                type="Sigma",
                endpoint="https://sigma.test",
                access_key="ak",
                secret_key="sk",
            ),
        ),
        "LOCAL": (LocalTemplateConfig, dict(type="LOCAL")),
        "POOLAB": (
            PoolabTemplateConfig,
            dict(
                type="POOLAB",
                poolab_tenant_id="ptid",
                poolab_tenant_token="ptoken",
                poolab_image_id_pre="img-pre",
                poolab_image_id_prod="img-prod",
            ),
        ),
        "TECLAW": (
            TeClawTemplateConfig,
            dict(
                type="TECLAW",
                teclaw_endpoint="https://teclaw.test",
            ),
        ),
        "K8S": (
            K8sTemplateConfig,
            dict(
                type="K8s",
                kubeconfig="kc",
                namespace="ns",
                image="img",
            ),
        ),
        "DOCKER": (
            DockerTemplateConfig,
            dict(
                type="DOCKER",
                image="img",
                container_port=8080,
                memory_limit="512m",
            ),
        ),
    }
    config_cls, kwargs = type_map.get(platform_type, type_map["ARCA"])
    config = config_cls(**kwargs)
    t = MagicMock()
    t.id = 1
    t.template_id = template_id
    t.template_uuid = template_uuid
    t.type = platform_type
    t.tenant = tenant
    t.name = "Test"
    t.config = config
    return t


def _make_facade(platform_type="ARCA"):
    mock_template_svc = MagicMock()
    mock_device_repo = MagicMock()
    mock_factory = MagicMock()
    f = PaasServiceFacade(
        device_repository=mock_device_repo,
        device_template_service=mock_template_svc,
        factory=mock_factory,
    )
    template = _make_template(platform_type=platform_type)
    mock_template_svc.get_default_or_explicit_template.return_value = template
    mock_template_svc.get_by_template_id.return_value = template
    return f, mock_template_svc, mock_factory


def _make_mock_service(platform_type="ARCA"):
    mock = MagicMock()
    mock.create_device = AsyncMock()
    mock.destroy_device = AsyncMock(return_value=True)
    mock.execute_command = AsyncMock()
    mock.get_credentials = AsyncMock()
    mock.get_platform_type = AsyncMock()
    mock.get_device_info = AsyncMock()
    mock.update_outbound_operation_rule = AsyncMock(return_value=True)
    mock.update_device_ttl = AsyncMock()
    mock.restart_device = AsyncMock(return_value=True)
    mock.update_device = AsyncMock(return_value=True)
    mock.open_folder = AsyncMock(return_value=True)
    mock.fetch_start_progress = AsyncMock()
    mock.invoke_http_in_device = AsyncMock(return_value={"status_code": 200})
    mock.resolve_ws_conn_info = AsyncMock(
        return_value=WsConnectionInfo(
            ws_url="wss://test",
            token="tok",
            target="t",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    mock.resolve_invoke_http_info = AsyncMock(
        return_value=HttpConnectionInfo(
            http_url="https://test",
            token="tok",
        )
    )
    return mock


# ---------------------------------------------------------------------------
# _parse_device_id edge cases
# ---------------------------------------------------------------------------


class TestParseDeviceIdEdgeCases:
    def test_parse_empty_string(self):
        assert PaasServiceFacade._parse_device_id("") == ("", 0)

    def test_parse_only_at_symbol(self):
        device_id, template_id = PaasServiceFacade._parse_device_id("@")
        assert template_id == 0

    def test_parse_at_with_number(self):
        device_id, template_id = PaasServiceFacade._parse_device_id("@123")
        assert device_id == ""
        assert template_id == 123


# ---------------------------------------------------------------------------
# _validate_port
# ---------------------------------------------------------------------------


class TestValidatePort:
    def test_valid_port(self):
        PaasServiceFacade._validate_port(8080)

    def test_port_zero_raises(self):
        with pytest.raises(ValueError, match="1-65535"):
            PaasServiceFacade._validate_port(0)

    def test_port_too_large_raises(self):
        with pytest.raises(ValueError, match="1-65535"):
            PaasServiceFacade._validate_port(70000)

    def test_port_negative_raises(self):
        with pytest.raises(ValueError, match="1-65535"):
            PaasServiceFacade._validate_port(-1)

    def test_port_bool_raises(self):
        with pytest.raises(ValueError, match="1-65535"):
            PaasServiceFacade._validate_port(True)

    def test_port_one(self):
        PaasServiceFacade._validate_port(1)

    def test_port_65535(self):
        PaasServiceFacade._validate_port(65535)


# ---------------------------------------------------------------------------
# _get_platform_type
# ---------------------------------------------------------------------------


class TestGetPlatformType:
    @pytest.mark.asyncio
    async def test_none_service(self):
        assert await PaasServiceFacade._get_platform_type(None) == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_with_get_platform_type_method(self):
        svc = MagicMock()
        from enum import StrEnum

        class FakePlatform(StrEnum):
            value = "ARCA"

        svc.get_platform_type = AsyncMock(return_value=FakePlatform("ARCA"))
        result = await PaasServiceFacade._get_platform_type(svc)
        assert result == "ARCA"

    @pytest.mark.asyncio
    async def test_get_platform_type_returns_none(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        # Falls through to class name inference
        svc.__class__.__name__ = "ArcaService"
        result = await PaasServiceFacade._get_platform_type(svc)
        assert result == "ARCA"

    @pytest.mark.asyncio
    async def test_get_platform_type_no_value_attr(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value="plain-string")
        svc.__class__.__name__ = "SigmaService"
        result = await PaasServiceFacade._get_platform_type(svc)
        assert result == "SIGMA"

    @pytest.mark.asyncio
    async def test_class_name_fallback_arca(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "ArcaPaasService"
        assert await PaasServiceFacade._get_platform_type(svc) == "ARCA"

    @pytest.mark.asyncio
    async def test_class_name_fallback_local(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "LocalPaasService"
        assert await PaasServiceFacade._get_platform_type(svc) == "LOCAL"

    @pytest.mark.asyncio
    async def test_class_name_fallback_poolab(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "PoolabPaasService"
        assert await PaasServiceFacade._get_platform_type(svc) == "POOLAB"

    @pytest.mark.asyncio
    async def test_class_name_fallback_teclaw(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "TeClawPaasService"
        assert await PaasServiceFacade._get_platform_type(svc) == "TECLAW"

    @pytest.mark.asyncio
    async def test_class_name_fallback_k8s(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "K8sPaasService"
        assert await PaasServiceFacade._get_platform_type(svc) == "K8S"

    @pytest.mark.asyncio
    async def test_class_name_fallback_standalone_docker(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "StandalonePaasService"
        assert await PaasServiceFacade._get_platform_type(svc) == "DOCKER"

    @pytest.mark.asyncio
    async def test_class_name_unknown(self):
        svc = MagicMock()
        svc.get_platform_type = AsyncMock(return_value=None)
        svc.__class__.__name__ = "SomethingElse"
        assert await PaasServiceFacade._get_platform_type(svc) == "UNKNOWN"


# ---------------------------------------------------------------------------
# _is_provided
# ---------------------------------------------------------------------------


class TestIsProvided:
    def test_none_not_provided(self):
        assert not PaasServiceFacade._is_provided(None)

    def test_empty_string_not_provided(self):
        assert not PaasServiceFacade._is_provided("")

    def test_non_empty_string_provided(self):
        assert PaasServiceFacade._is_provided("hello")

    def test_zero_provided(self):
        assert PaasServiceFacade._is_provided(0)

    def test_false_provided(self):
        assert PaasServiceFacade._is_provided(False)

    def test_list_provided(self):
        assert PaasServiceFacade._is_provided([1, 2])


# ---------------------------------------------------------------------------
# _merge_config
# ---------------------------------------------------------------------------


class TestMergeConfig:
    def test_merge_no_template_no_detail(self):
        f, _, _ = _make_facade()
        result = f._merge_config(None, None, "ARCA")
        assert result == {}

    def test_merge_template_only_arca(self):
        f, _, _ = _make_facade()
        tpl_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.test",
            api_key="key",
            template_id="tpl-123",
            oss_mount_id="mount-1",
        )
        result = f._merge_config(tpl_config, None, "ARCA")
        assert "base_url" in result
        assert result["template_id"] == "tpl-123"

    def test_merge_detail_config_type_mismatch_raises(self):
        f, _, _ = _make_facade()
        detail = SigmaDeviceConfig(
            endpoint="",
            access_key="",
            secret_key="",
            region="default",
        )
        with pytest.raises(ValueError, match="must be ArcaDeviceConfig"):
            f._merge_config(None, detail, "ARCA")

    def test_merge_detail_config_type_mismatch_sigma(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig()
        with pytest.raises(ValueError, match="must be SigmaDeviceConfig"):
            f._merge_config(None, detail, "SIGMA")

    def test_merge_detail_config_type_mismatch_local(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig()
        with pytest.raises(ValueError, match="must be LocalDeviceConfig"):
            f._merge_config(None, detail, "LOCAL")

    def test_merge_detail_config_type_mismatch_poolab(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig()
        with pytest.raises(ValueError, match="must be PoolabDeviceConfig"):
            f._merge_config(None, detail, "POOLAB")

    def test_merge_detail_config_type_mismatch_teclaw(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig()
        with pytest.raises(ValueError, match="must be TeClawDeviceConfig"):
            f._merge_config(None, detail, "TECLAW")

    def test_merge_detail_config_type_mismatch_k8s(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig()
        with pytest.raises(ValueError, match="must be K8sDeviceConfig"):
            f._merge_config(None, detail, "K8S")

    def test_merge_detail_config_type_mismatch_docker(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig()
        with pytest.raises(ValueError, match="must be DockerDeviceConfig"):
            f._merge_config(None, detail, "DOCKER")

    def test_merge_detail_config_filters_disallowed_fields(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig(
            ttl_in_minutes=120,
            name="override-name",
        )
        result = f._merge_config(None, detail, "ARCA")
        assert result["ttl_in_minutes"] == 120
        assert result["name"] == "override-name"

    def test_merge_detail_config_empty_string_not_provided(self):
        f, _, _ = _make_facade()
        detail = ArcaDeviceConfig(
            name="",
            description="desc",
        )
        result = f._merge_config(None, detail, "ARCA")
        assert "name" not in result
        assert result["description"] == "desc"

    def test_merge_unknown_platform_no_allowed_fields(self):
        f, _, _ = _make_facade()
        detail = MagicMock()
        detail.to_create_config = MagicMock()
        detail.__class__.__name__ = "UnknownDeviceConfig"
        detail.model_dump = MagicMock(return_value={"foo": "bar"})
        result = f._merge_config(None, detail, "UNKNOWN_PLATFORM")
        # All fields filtered out since not in allowed set
        assert result == {}

    def test_merge_arca_template_with_env_specific_template_id(self):
        f, _, _ = _make_facade()
        tpl_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.test",
            api_key="key",
            template_id="tpl-base",
            arca_template_id_pre="tpl-pre",
            arca_template_id_prod="tpl-prod",
        )
        with patch(
            "secbaas.community.core.service.paas._facade.get_current_env",
            return_value="pre",
        ):
            result = f._merge_config(tpl_config, None, "ARCA")
        # Should use effective_template_id from get_effective_template_id("pre")
        assert result["template_id"] == "tpl-pre"
        assert "arca_template_id_pre" not in result
        assert "arca_template_id_prod" not in result

    def test_merge_arca_template_with_detail_override(self):
        f, _, _ = _make_facade()
        tpl_config = ArcaTemplateConfig(
            type="ARCA",
            base_url="https://arca.test",
            api_key="key",
            template_id="tpl-base",
        )
        detail = ArcaDeviceConfig(ttl_in_minutes=60, name="my-device")
        result = f._merge_config(tpl_config, detail, "ARCA")
        assert result["ttl_in_minutes"] == 60
        assert result["name"] == "my-device"
        assert result["template_id"] == "tpl-base"

    def test_merge_sigma_template_with_detail(self):
        f, _, _ = _make_facade()
        tpl_config = SigmaTemplateConfig(
            type="Sigma",
            endpoint="https://sigma.test",
            access_key="ak",
            secret_key="sk",
        )
        detail = SigmaDeviceConfig(
            endpoint="",
            access_key="",
            secret_key="",
            region="default",
            zone="zone-1",
        )
        result = f._merge_config(tpl_config, detail, "SIGMA")
        assert result["zone"] == "zone-1"

    def test_merge_local_template_with_detail(self):
        f, _, _ = _make_facade()
        tpl_config = LocalTemplateConfig(type="LOCAL")
        detail = LocalDeviceConfig(
            user_id="u1",
            machine_id="m1",
            tc_bot_id="b1",
            agent_code="a1",
        )
        result = f._merge_config(tpl_config, detail, "LOCAL")
        assert result["user_id"] == "u1"
        assert result["machine_id"] == "m1"

    def test_merge_poolab_template_with_detail(self):
        f, _, _ = _make_facade()
        tpl_config = PoolabTemplateConfig(
            type="POOLAB",
            poolab_tenant_id="ptid",
            poolab_tenant_token="ptoken",
        )
        detail = PoolabDeviceConfig(
            poolab_user_id="puid",
        )
        result = f._merge_config(tpl_config, detail, "POOLAB")
        assert result["poolab_user_id"] == "puid"

    def test_merge_teclaw_template_with_detail(self):
        f, _, _ = _make_facade()
        tpl_config = TeClawTemplateConfig(
            type="TECLAW",
            teclaw_endpoint="https://teclaw.test",
        )
        detail = TeClawDeviceConfig(
            name="my-teclaw",
        )
        result = f._merge_config(tpl_config, detail, "TECLAW")
        assert result["name"] == "my-teclaw"

    def test_merge_k8s_template_with_detail(self):
        f, _, _ = _make_facade()
        tpl_config = K8sTemplateConfig(
            type="K8s",
            kubeconfig="kc",
            namespace="ns",
            image="img",
        )
        detail = K8sDeviceConfig(name="k8s-device")
        result = f._merge_config(tpl_config, detail, "K8S")
        assert result["name"] == "k8s-device"

    def test_merge_docker_template_with_detail(self):
        f, _, _ = _make_facade()
        tpl_config = DockerTemplateConfig(
            type="DOCKER",
            image="base-img",
            container_port=8080,
            memory_limit="512m",
        )
        detail = DockerDeviceConfig(
            image="override-img",
            container_port=9090,
            memory_limit="1g",
        )
        result = f._merge_config(tpl_config, detail, "DOCKER")
        assert result["image"] == "override-img"


# ---------------------------------------------------------------------------
# create_device
# ---------------------------------------------------------------------------


class TestCreateDevice:
    @pytest.mark.asyncio
    async def test_empty_tenant_raises(self):
        f, _, _ = _make_facade()
        with pytest.raises(ValueError, match="tenant_name parameter is required"):
            await f.create_device("")

    @pytest.mark.asyncio
    async def test_unknown_platform_raises(self):
        f, tpl_svc, factory = _make_facade()
        template = _make_template(platform_type="UNKNOWN")
        tpl_svc.get_default_or_explicit_template.return_value = template
        mock_svc = _make_mock_service()
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=0))
        factory.create.return_value = mock_svc
        with pytest.raises(ValueError, match="Unknown platform type"):
            await f.create_device("test-tenant")

    @pytest.mark.asyncio
    async def test_create_arca_success(self):
        f, tpl_svc, factory = _make_facade("ARCA")
        mock_svc = _make_mock_service("ARCA")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=42))
        mock_svc.create_device = AsyncMock(
            return_value=ArcaCreationResult(
                platform="ARCA",
                status="RUNNING",
                template_id="tpl-123",
                sandbox_id="sb-123",
            )
        )
        factory.create.return_value = mock_svc

        result = await f.create_device(
            "test-tenant", detail_config=ArcaDeviceConfig(ttl_in_minutes=60)
        )
        assert isinstance(result, ArcaCreationResult)
        assert result.sandbox_id == "sb-123@42"

    @pytest.mark.asyncio
    async def test_create_arca_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade("ARCA")
        mock_svc = _make_mock_service("ARCA")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=42))
        mock_svc.create_device = AsyncMock(
            side_effect=PaasError(
                ErrorCode.DEVICE_CREATION_FAILED,
                "create failed",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.create_device("test-tenant")
        assert exc_info.value.operation == "create_device"

    @pytest.mark.asyncio
    async def test_create_sigma_raises_not_implemented(self):
        f, tpl_svc, factory = _make_facade("SIGMA")
        mock_svc = _make_mock_service("SIGMA")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=0))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.create_device("test-tenant")

    @pytest.mark.asyncio
    async def test_create_local_success(self):
        f, tpl_svc, factory = _make_facade("LOCAL")
        mock_svc = _make_mock_service("LOCAL")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=7))
        mock_svc.create_device = AsyncMock(
            return_value=LocalCreationResult(
                container_id="c-123",
                platform="LOCAL",
                status="ACTIVE",
            )
        )
        factory.create.return_value = mock_svc

        detail = LocalDeviceConfig(
            user_id="u1",
            machine_id="m1",
            tc_bot_id="b1",
            agent_code="a1",
        )
        result = await f.create_device("test-tenant", detail_config=detail)
        assert isinstance(result, LocalCreationResult)
        assert result.container_id == "c-123@7"

    @pytest.mark.asyncio
    async def test_create_poolab_success(self):
        f, tpl_svc, factory = _make_facade("POOLAB")
        mock_svc = _make_mock_service("POOLAB")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=5))
        mock_svc.create_device = AsyncMock(
            return_value=PoolabCreationResult(
                platform="POOLAB",
                status="ACTIVE",
                poolab_id="pid-1",
                poolab_user_id="puid",
            )
        )
        factory.create.return_value = mock_svc

        detail = PoolabDeviceConfig(poolab_user_id="puid")
        result = await f.create_device("test-tenant", detail_config=detail)
        assert isinstance(result, PoolabCreationResult)
        assert result.poolab_id == "pid-1@5"

    @pytest.mark.asyncio
    async def test_create_teclaw_success(self):
        f, tpl_svc, factory = _make_facade("TECLAW")
        mock_svc = _make_mock_service("TECLAW")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=9))
        mock_svc.create_device = AsyncMock(
            return_value=TeClawCreationResult(
                platform="TECLAW",
                status="ACTIVE",
                teclaw_bot_id="bot-1",
            )
        )
        factory.create.return_value = mock_svc

        detail = TeClawDeviceConfig(name="my-teclaw")
        result = await f.create_device("test-tenant", detail_config=detail)
        assert isinstance(result, TeClawCreationResult)
        assert result.teclaw_bot_id == "bot-1@9"

    @pytest.mark.asyncio
    async def test_create_k8s_success(self):
        f, tpl_svc, factory = _make_facade("K8S")
        mock_svc = _make_mock_service("K8S")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=3))
        mock_svc.create_device = AsyncMock(
            return_value=K8sCreationResult(
                device_id="k8s-dev-1",
            )
        )
        factory.create.return_value = mock_svc

        detail = K8sDeviceConfig(name="k8s-device")
        result = await f.create_device("test-tenant", detail_config=detail)
        assert isinstance(result, K8sCreationResult)
        assert result.device_id == "k8s-dev-1@3"

    @pytest.mark.asyncio
    async def test_create_docker_success(self):
        f, tpl_svc, factory = _make_facade("DOCKER")
        mock_svc = _make_mock_service("DOCKER")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=11))
        mock_svc.create_device = AsyncMock(
            return_value=DockerCreationResult(
                container_id="docker-1",
                host_port=8080,
                platform="DOCKER",
                status="ACTIVE",
            )
        )
        factory.create.return_value = mock_svc

        detail = DockerDeviceConfig(
            image="img",
            name="docker-dev",
            container_port=8080,
            memory_limit="512m",
        )
        result = await f.create_device("test-tenant", detail_config=detail)
        assert isinstance(result, DockerCreationResult)
        assert result.container_id == "docker-1@11"

    @pytest.mark.asyncio
    async def test_create_device_returns_unexpected_type(self):
        f, tpl_svc, factory = _make_facade("LOCAL")
        mock_svc = _make_mock_service("LOCAL")
        mock_svc.get_credentials = AsyncMock(return_value=MagicMock(template_id=0))
        mock_svc.create_device = AsyncMock(return_value=MagicMock())
        factory.create.return_value = mock_svc

        detail = LocalDeviceConfig(
            user_id="u1",
            machine_id="m1",
            tc_bot_id="b1",
            agent_code="a1",
        )
        result = await f.create_device("test-tenant", detail_config=detail)
        assert result is not None


# ---------------------------------------------------------------------------
# destroy_device
# ---------------------------------------------------------------------------


class TestDestroyDevice:
    @pytest.mark.asyncio
    async def test_destroy_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.destroy_device("dev-1@42")
        assert result is True
        mock_svc.destroy_device.assert_awaited_once_with("dev-1")

    @pytest.mark.asyncio
    async def test_destroy_no_suffix(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        await f.destroy_device("legacy-device")
        mock_svc.destroy_device.assert_awaited_once_with("legacy-device")

    @pytest.mark.asyncio
    async def test_destroy_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.destroy_device("dev@42")
        assert exc_info.value.operation == "destroy_device"

    @pytest.mark.asyncio
    async def test_destroy_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.destroy_device = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "destroy failed",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.destroy_device("dev@42")
        assert exc_info.value.operation == "destroy_device"


# ---------------------------------------------------------------------------
# execute_command
# ---------------------------------------------------------------------------


class TestExecuteCommand:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.execute_command = AsyncMock(
            return_value=CommandResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                execution_time_ms=10,
                command="ls -la",
            )
        )
        factory.create.return_value = mock_svc

        result = await f.execute_command("dev@42", "ls -la")
        assert result.exit_code == 0
        mock_svc.execute_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.execute_command("dev@42", "ls")
        assert exc_info.value.operation == "execute_command"

    @pytest.mark.asyncio
    async def test_execute_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.execute_command = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "exec failed",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.execute_command("dev@42", "ls")


# ---------------------------------------------------------------------------
# resolve_ws_conn_info
# ---------------------------------------------------------------------------


class TestResolveWsConnInfo:
    @pytest.mark.asyncio
    async def test_resolve_ws_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.resolve_ws_conn_info("dev@42", 8080, "/ws")
        assert isinstance(result, WsConnectionInfo)
        mock_svc.resolve_ws_conn_info.assert_awaited_once_with(
            "dev", 8080, "/ws", ws_conn_mode=None
        )

    @pytest.mark.asyncio
    async def test_resolve_ws_invalid_port(self):
        f, _, _ = _make_facade()
        with pytest.raises(ValueError, match="1-65535"):
            await f.resolve_ws_conn_info("dev@42", 0, "/ws")

    @pytest.mark.asyncio
    async def test_resolve_ws_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.resolve_ws_conn_info("dev@42", 8080, "/ws")
        assert exc_info.value.operation == "resolve_ws_conn_info"

    @pytest.mark.asyncio
    async def test_resolve_ws_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_ws_conn_info = AsyncMock(
            side_effect=NotImplementedError("not supported")
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.resolve_ws_conn_info("dev@42", 8080, "/ws")
        assert "not support" in str(exc_info.value.original_error.message)

    @pytest.mark.asyncio
    async def test_resolve_ws_device_creation_error_machine_offline(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_ws_conn_info = AsyncMock(
            side_effect=DeviceCreationError(
                error_code="MACHINE_OFFLINE",
                message="offline",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_ws_conn_info("dev@42", 8080, "/ws")

    @pytest.mark.asyncio
    async def test_resolve_ws_device_creation_error_other(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_ws_conn_info = AsyncMock(
            side_effect=DeviceCreationError(
                error_code="OTHER_ERROR",
                message="something",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_ws_conn_info("dev@42", 8080, "/ws")

    @pytest.mark.asyncio
    async def test_resolve_ws_paas_error_reraised(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_ws_conn_info = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "paas err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_ws_conn_info("dev@42", 8080, "/ws")

    @pytest.mark.asyncio
    async def test_resolve_ws_generic_exception(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_ws_conn_info = AsyncMock(side_effect=RuntimeError("boom"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_ws_conn_info("dev@42", 8080, "/ws")

    @pytest.mark.asyncio
    async def test_resolve_ws_no_suffix(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        await f.resolve_ws_conn_info("legacy-dev", 8080, "/ws")
        mock_svc.resolve_ws_conn_info.assert_awaited_once_with(
            "legacy-dev", 8080, "/ws", ws_conn_mode=None
        )


# ---------------------------------------------------------------------------
# resolve_invoke_http_info
# ---------------------------------------------------------------------------


class TestResolveInvokeHttpInfo:
    @pytest.mark.asyncio
    async def test_resolve_http_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.resolve_invoke_http_info("dev@42", 8080, "/api")
        assert isinstance(result, HttpConnectionInfo)
        mock_svc.resolve_invoke_http_info.assert_awaited_once_with("dev", 8080, "/api")

    @pytest.mark.asyncio
    async def test_resolve_http_path_none_defaults_to_slash(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        await f.resolve_invoke_http_info("dev@42", 8080, None)
        mock_svc.resolve_invoke_http_info.assert_awaited_once_with("dev", 8080, "/")

    @pytest.mark.asyncio
    async def test_resolve_http_invalid_port(self):
        f, _, _ = _make_facade()
        with pytest.raises(ValueError, match="1-65535"):
            await f.resolve_invoke_http_info("dev@42", 99999, "/api")

    @pytest.mark.asyncio
    async def test_resolve_http_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.resolve_invoke_http_info("dev@42", 8080, "/api")
        assert exc_info.value.operation == "resolve_invoke_http_info"

    @pytest.mark.asyncio
    async def test_resolve_http_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_invoke_http_info = AsyncMock(
            side_effect=NotImplementedError("nope")
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_invoke_http_info("dev@42", 8080, "/api")

    @pytest.mark.asyncio
    async def test_resolve_http_device_creation_error_machine_offline(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_invoke_http_info = AsyncMock(
            side_effect=DeviceCreationError(
                error_code="MACHINE_OFFLINE",
                message="offline",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_invoke_http_info("dev@42", 8080, "/api")

    @pytest.mark.asyncio
    async def test_resolve_http_device_creation_error_other(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_invoke_http_info = AsyncMock(
            side_effect=DeviceCreationError(
                error_code="OTHER",
                message="err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_invoke_http_info("dev@42", 8080, "/api")

    @pytest.mark.asyncio
    async def test_resolve_http_paas_error_reraised(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_invoke_http_info = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(PaasError):
            await f.resolve_invoke_http_info("dev@42", 8080, "/api")

    @pytest.mark.asyncio
    async def test_resolve_http_generic_exception(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.resolve_invoke_http_info = AsyncMock(side_effect=RuntimeError("boom"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.resolve_invoke_http_info("dev@42", 8080, "/api")


# ---------------------------------------------------------------------------
# get_device_info
# ---------------------------------------------------------------------------


class TestGetDeviceInfo:
    @pytest.mark.asyncio
    async def test_get_info_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.get_device_info = AsyncMock(
            return_value=MagicMock(
                platform="ARCA",
                status="RUNNING",
            )
        )
        factory.create.return_value = mock_svc

        result = await f.get_device_info("dev@42")
        assert result.status == "RUNNING"
        mock_svc.get_device_info.assert_awaited_once_with("dev")

    @pytest.mark.asyncio
    async def test_get_info_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.get_device_info("dev@42")
        assert exc_info.value.operation == "get_device_info"

    @pytest.mark.asyncio
    async def test_get_info_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.get_device_info = AsyncMock(
            side_effect=PaasError(
                ErrorCode.DEVICE_NOT_FOUND,
                "not found",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.get_device_info("dev@42")


# ---------------------------------------------------------------------------
# update_outbound_operation_rule
# ---------------------------------------------------------------------------


class TestUpdateOutboundOperationRule:
    @pytest.mark.asyncio
    async def test_update_rule_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc
        rule = MagicMock()

        result = await f.update_outbound_operation_rule("dev@42", rule)
        assert result is True
        mock_svc.update_outbound_operation_rule.assert_awaited_once_with("dev", rule)

    @pytest.mark.asyncio
    async def test_update_rule_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None
        rule = MagicMock()

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.update_outbound_operation_rule("dev@42", rule)
        assert exc_info.value.operation == "update_outbound_operation_rule"

    @pytest.mark.asyncio
    async def test_update_rule_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_outbound_operation_rule = AsyncMock(
            side_effect=NotImplementedError("nope")
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.update_outbound_operation_rule("dev@42", MagicMock())

    @pytest.mark.asyncio
    async def test_update_rule_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_outbound_operation_rule = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.update_outbound_operation_rule("dev@42", MagicMock())


# ---------------------------------------------------------------------------
# update_device_ttl
# ---------------------------------------------------------------------------


class TestUpdateDeviceTtl:
    @pytest.mark.asyncio
    async def test_update_ttl_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_device_ttl = AsyncMock(
            return_value=TTLInfo(
                success=True,
                old_expiration_time=None,
                new_expiration_time=None,
                paas_device_id="dev@42",
            )
        )
        factory.create.return_value = mock_svc

        result = await f.update_device_ttl("dev@42")
        assert result.success is True
        assert result.paas_device_id == "dev@42"

    @pytest.mark.asyncio
    async def test_update_ttl_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.update_device_ttl("dev@42")
        assert exc_info.value.operation == "update_device_ttl"

    @pytest.mark.asyncio
    async def test_update_ttl_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_device_ttl = AsyncMock(side_effect=NotImplementedError("nope"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.update_device_ttl("dev@42")

    @pytest.mark.asyncio
    async def test_update_ttl_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_device_ttl = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.update_device_ttl("dev@42")


# ---------------------------------------------------------------------------
# invoke_http_in_device
# ---------------------------------------------------------------------------


class TestInvokeHttpInDevice:
    @pytest.mark.asyncio
    async def test_invoke_http_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.invoke_http_in_device(
            "dev@42",
            "GET",
            8080,
            "/api",
            None,
            {},
            b"",
        )
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_invoke_http_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.invoke_http_in_device("dev@42", "GET", 8080, "/api", None, {}, b"")
        assert exc_info.value.operation == "invoke_http_in_device"

    @pytest.mark.asyncio
    async def test_invoke_http_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.invoke_http_in_device = AsyncMock(
            side_effect=NotImplementedError("nope")
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.invoke_http_in_device("dev@42", "GET", 8080, "/api", None, {}, b"")

    @pytest.mark.asyncio
    async def test_invoke_http_timeout(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        import asyncio

        async def slow_call(**kw):
            await asyncio.sleep(100)

        mock_svc.invoke_http_in_device = slow_call
        factory.create.return_value = mock_svc

        with patch(
            "secbaas.community.core.service.paas._facade.HTTP_INVOCATION_TIMEOUT", 0.01
        ):
            with pytest.raises(DeviceFacadeException) as exc_info:
                await f.invoke_http_in_device(
                    "dev@42", "GET", 8080, "/api", None, {}, b""
                )
        assert "timed out" in str(exc_info.value.original_error.message).lower()

    @pytest.mark.asyncio
    async def test_invoke_http_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.invoke_http_in_device = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.invoke_http_in_device("dev@42", "GET", 8080, "/api", None, {}, b"")


# ---------------------------------------------------------------------------
# restart_device
# ---------------------------------------------------------------------------


class TestRestartDevice:
    @pytest.mark.asyncio
    async def test_restart_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.restart_device("dev@42")
        assert result is True

    @pytest.mark.asyncio
    async def test_restart_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.restart_device("dev@42")
        assert exc_info.value.operation == "restart_device"

    @pytest.mark.asyncio
    async def test_restart_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.restart_device = AsyncMock(side_effect=NotImplementedError("nope"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.restart_device("dev@42")

    @pytest.mark.asyncio
    async def test_restart_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.restart_device = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.restart_device("dev@42")


# ---------------------------------------------------------------------------
# update_device
# ---------------------------------------------------------------------------


class TestUpdateDeviceFacade:
    @pytest.mark.asyncio
    async def test_update_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.update_device("dev@42")
        assert result is True

    @pytest.mark.asyncio
    async def test_update_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.update_device("dev@42")
        assert exc_info.value.operation == "update_device"

    @pytest.mark.asyncio
    async def test_update_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_device = AsyncMock(side_effect=NotImplementedError("nope"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.update_device("dev@42")

    @pytest.mark.asyncio
    async def test_update_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.update_device = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.update_device("dev@42")


# ---------------------------------------------------------------------------
# open_folder
# ---------------------------------------------------------------------------


class TestOpenFolder:
    @pytest.mark.asyncio
    async def test_open_folder_success(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        factory.create.return_value = mock_svc

        result = await f.open_folder("dev@42", "/home/user")
        assert result is True

    @pytest.mark.asyncio
    async def test_open_folder_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.open_folder("dev@42")
        assert exc_info.value.operation == "open_folder"

    @pytest.mark.asyncio
    async def test_open_folder_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.open_folder = AsyncMock(side_effect=NotImplementedError("nope"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.open_folder("dev@42")

    @pytest.mark.asyncio
    async def test_open_folder_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.open_folder = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.open_folder("dev@42")


# ---------------------------------------------------------------------------
# fetch_start_progress
# ---------------------------------------------------------------------------


class TestFetchStartProgress:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.fetch_start_progress = AsyncMock(
            return_value=FetchStartProgressResult(
                progress="running",
                extra_data={},
            )
        )
        factory.create.return_value = mock_svc

        result = await f.fetch_start_progress("dev@42")
        assert result.progress == "running"

    @pytest.mark.asyncio
    async def test_fetch_template_not_found(self):
        f, tpl_svc, factory = _make_facade()
        tpl_svc.get_by_template_id.return_value = None

        with pytest.raises(DeviceFacadeException) as exc_info:
            await f.fetch_start_progress("dev@42")
        assert exc_info.value.operation == "fetch_start_progress"

    @pytest.mark.asyncio
    async def test_fetch_not_implemented(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.fetch_start_progress = AsyncMock(
            side_effect=NotImplementedError("nope")
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.fetch_start_progress("dev@42")

    @pytest.mark.asyncio
    async def test_fetch_paas_error_wrapped(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.fetch_start_progress = AsyncMock(
            side_effect=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.fetch_start_progress("dev@42")

    @pytest.mark.asyncio
    async def test_fetch_device_creation_error_machine_offline(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.fetch_start_progress = AsyncMock(
            side_effect=DeviceCreationError(
                error_code="MACHINE_OFFLINE",
                message="offline",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.fetch_start_progress("dev@42")

    @pytest.mark.asyncio
    async def test_fetch_device_creation_error_other(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.fetch_start_progress = AsyncMock(
            side_effect=DeviceCreationError(
                error_code="OTHER",
                message="err",
            )
        )
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.fetch_start_progress("dev@42")

    @pytest.mark.asyncio
    async def test_fetch_generic_exception(self):
        f, tpl_svc, factory = _make_facade()
        mock_svc = _make_mock_service()
        mock_svc.fetch_start_progress = AsyncMock(side_effect=RuntimeError("boom"))
        factory.create.return_value = mock_svc

        with pytest.raises(DeviceFacadeException):
            await f.fetch_start_progress("dev@42")
