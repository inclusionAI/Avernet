"""UPFS storage contracts from persisted DeployConfig to project-owned ARCA DTOs."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.api.device_manage import (
    DeployConfig,
    DeviceConfig,
    Storage,
    VolumeMountSpec,
)
from secbaas.community.api.template_manage import ArcaTemplateConfig
from secbaas.community.core.repository.device_template import DeviceTemplateRecord
from secbaas.community.core.service.device_manage import DefaultDeviceService
from secbaas.community.core.service.device_manage._device_service import (
    _build_arca_detail_config,
)
from secbaas.community.core.service.template_manage import DefaultDeviceTemplateService

DS = "secbaas.community.core.service.device_manage._device_service"


def template(**kwargs):
    return ArcaTemplateConfig(
        type="ARCA",
        base_url="",
        api_key="secret-not-for-response",
        upfs_volume_id_pre="volume-pre",
        upfs_volume_id_prod="volume-prod",
        **kwargs,
    )


def deploy(**kwargs):
    fields = dict(
        type="upfs",
        path="/home/admin",
        storage_id="bot_{device_uuid}",
        quota="ignored",
        permission="0000",
    )
    fields.update(kwargs)
    return DeployConfig(storage=Storage(**fields))


def build(config=None, *, device="DEVICE-one", env="pre", tpl=None, size=1073741824):
    return _build_arca_detail_config(
        deploy_config=config or deploy(),
        oss_mount_id=None,
        env=env,
        device_uuid=device,
        secret_plugin=MagicMock(),
        arca_template_config=tpl or template(),
        upfs_subpath_size_bytes=size,
    )[0]


@pytest.mark.parametrize(
    "env,expected", [("pre", "volume-pre"), ("prod", "volume-prod")]
)
def test_upfs_mapping_uses_environment_volume_and_ignores_nas_fields(env, expected):
    config = deploy()
    detail = build(config, env=env, size=2048)
    assert detail.storage is None
    assert detail.volume_mounts[0].model_dump(by_alias=True) == {
        "volumeId": expected,
        "subpath": "bot_DEVICE-one",
        "subpath_size_bytes": 2048,
        "mountPath": "/home/admin",
        "readOnly": False,
    }
    assert detail.to_create_config().volume_mounts == detail.volume_mounts
    assert (
        config.storage.storage_id == "bot_{device_uuid}"
    )  # stored template not mutated


def test_restart_and_scale_device_identity():
    config = deploy()
    first = build(config)
    restart = build(config)
    scaled = build(config, device="DEVICE-two")
    assert restart.volume_mounts == first.volume_mounts
    assert scaled.volume_mounts[0].volume_id == first.volume_mounts[0].volume_id
    assert scaled.volume_mounts[0].subpath != first.volume_mounts[0].subpath
    assert (
        DeviceConfig.model_validate(
            DeviceConfig(deploy_config=config).model_dump()
        ).deploy_config.storage.type
        == "upfs"
    )


@pytest.mark.parametrize(
    "subpath",
    [
        None,
        "",
        " ",
        "/absolute",
        "trailing/",
        "../escape",
        "a/../b",
        "a//b",
        ".",
        "a/./b",
        "a\\b",
        "a\x00b",
        "{unknown}",
    ],
)
def test_invalid_subpaths_fail_without_trimming(subpath):
    with pytest.raises(ValueError):
        build(deploy(storage_id=subpath))


@pytest.mark.parametrize("path", [None, "", "relative", "bad\x00path"])
def test_invalid_mount_path_fails(path):
    with pytest.raises(ValueError):
        build(deploy(path=path))


@pytest.mark.parametrize(
    "env,volume",
    [
        ("pre", None),
        ("pre", ""),
        ("pre", " "),
        ("pre", " volume "),
        ("dev", "anything"),
    ],
)
def test_missing_or_invalid_environment_volume_never_downgrades(env, volume):
    tpl = template()
    tpl.upfs_volume_id_pre = volume
    with pytest.raises(ValueError, match="volume_id"):
        build(env=env, tpl=tpl)


def test_nas_storage_unchanged_even_without_volume():
    config = deploy(
        type="nas", storage_id="/old_{device_uuid}/", quota="2Gi", permission="0777"
    )
    detail = build(config, env="dev")
    assert detail.volume_mounts is None
    assert detail.storage.model_dump() == dict(
        config.storage.model_dump(), storage_id="/old_DEVICE-one/"
    )


def service(config_service=None):
    return DefaultDeviceService(
        paas_facade=MagicMock(create_device=AsyncMock(), destroy_device=AsyncMock()),
        repository=MagicMock(),
        device_template_service=MagicMock(),
        secret_plugin=MagicMock(),
        callback_handler=MagicMock(),
        system_config_service=config_service,
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 1073741824),
        ("", 1073741824),
        ("0", 1073741824),
        ("-1", 1073741824),
        ("1.5", 1073741824),
        ("x", 1073741824),
        (True, 1073741824),
        (1.5, 1073741824),
        ("9223372036854775808", 1073741824),
        ("1", 1),
        ("2147483648", 2147483648),
    ],
)
def test_quota_config_values(value, expected):
    config = MagicMock()
    config.get_config.return_value = SimpleNamespace(conf_value=value)
    assert service(config)._upfs_subpath_size_bytes(deploy()) == expected
    config.get_config.assert_called_once_with("upfs.subpath_size_bytes")


def test_quota_missing_error_and_nas():
    config = MagicMock()
    config.get_config.return_value = None
    assert service(config)._upfs_subpath_size_bytes(deploy()) == 1073741824
    config.get_config.side_effect = RuntimeError("database unavailable")
    assert service(config)._upfs_subpath_size_bytes(deploy()) == 1073741824
    config.reset_mock()
    assert service(config)._upfs_subpath_size_bytes(deploy(type="nas")) == 1073741824
    config.get_config.assert_not_called()


@pytest.mark.asyncio
async def test_precheck_volume_deleted_blocks_restart_before_destroy(monkeypatch):
    monkeypatch.setattr(f"{DS}.get_current_env", lambda: "pre")
    svc = service()
    tpl = template()
    tpl.upfs_volume_id_pre = None
    svc._resolve_device_for_operation = MagicMock(
        return_value=(
            SimpleNamespace(provider_device_id="running-sandbox"),
            DeviceConfig(deploy_config=deploy()),
            SimpleNamespace(config=tpl),
            "ARCA",
        )
    )
    with pytest.raises(ValueError, match="volume_id"):
        await svc.restart_device("tenant", "DEVICE-one", "operator")
    svc._paas_facade.destroy_device.assert_not_called()
    svc._paas_facade.create_device.assert_not_called()


def template_service(monkeypatch, deployment_env="pre"):
    now = datetime(2026, 9, 15)
    record = DeviceTemplateRecord(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        template_uuid="TEMPLATE-one",
        tenant="tenant",
        is_deleted=0,
        creator="op",
        modifier="op",
        status="ONLINE",
        name="template",
        description=None,
        config=template().model_dump(),
        template_id=1,
        type="ARCA",
    )
    repo = MagicMock()
    repo.get_online_by_template_uuid.side_effect = lambda template_uuid, tenant: (
        record if tenant == "tenant" and template_uuid == "TEMPLATE-one" else None
    )
    svc = DefaultDeviceTemplateService(
        repository=repo,
        tenant_service=MagicMock(),
        secret_plugin=MagicMock(),
        deployment_env=deployment_env,
    )
    return svc, record, repo


def test_capability_tenant_environment_and_safe_serialization(monkeypatch):
    svc, record, repo = template_service(monkeypatch)
    result = svc.get_storage_capability("tenant", "TEMPLATE-one", "pre")
    assert result.model_dump() == dict(
        template_uuid="TEMPLATE-one", env="pre", upfs_ready=True, reason=""
    )
    repo.get_online_by_template_uuid.assert_called_once_with(
        template_uuid="TEMPLATE-one", tenant="tenant"
    )
    assert (
        svc.get_storage_capability("other", "TEMPLATE-one").reason
        == "template_not_found"
    )
    assert (
        svc.get_storage_capability("tenant", "TEMPLATE-one", "prod").reason
        == "environment_mismatch"
    )
    record.config.pop("upfs_volume_id_pre")
    assert (
        svc.get_storage_capability("tenant", "TEMPLATE-one").reason
        == "volume_id_not_configured"
    )
    record.config = {"type": "LOCAL"}
    record.type = "LOCAL"
    assert (
        svc.get_storage_capability("tenant", "TEMPLATE-one").reason
        == "unsupported_template_type"
    )


def test_capability_http_envelope_does_not_expose_template_secrets(monkeypatch):
    from secbaas.community.adapters.web.routers.config_management.device_template_router import (
        router,
    )

    svc, _, _ = template_service(monkeypatch)
    app = FastAPI()
    app.include_router(router)
    route = next(r for r in router.routes if r.path.endswith("/storage-capability"))
    dependency = next(d for d in route.dependant.dependencies if d.name == "service")
    app.dependency_overrides[dependency.call] = lambda: svc
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/device-templates/TEMPLATE-one/storage-capability",
            params={"tenant": "tenant", "env": "pre"},
        )
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"] == dict(
        template_uuid="TEMPLATE-one", env="pre", upfs_ready=True, reason=""
    )
    assert "api_key" not in response.text
    assert "secret-not-for-response" not in response.text


def test_stub_records_mounts_and_non_upfs_provider_rejects():
    from secbaas.community.plugins.sandbox.arca._stub import StubArcaSandboxPlugin
    from secbaas.community.plugins.sandbox.arca.local_proc._sandbox_plugin import (
        LocalProcessArcaSandboxPlugin,
    )

    mounts = build().volume_mounts
    sandbox = StubArcaSandboxPlugin().create_sync_sandbox(
        "template", volume_mounts=mounts
    )
    assert sandbox.volume_mounts == mounts
    # Refuse before local process/disk initialization; no fake NAS success.
    plugin = LocalProcessArcaSandboxPlugin.__new__(LocalProcessArcaSandboxPlugin)
    with pytest.raises(NotImplementedError, match="UPFS"):
        plugin.create_sync_sandbox("template", volume_mounts=mounts)


def test_facade_allowlist_preserves_upfs_and_clears_legacy_nas_default(monkeypatch):
    from secbaas.community.api.device_manage import ArcaCreateConfig
    from secbaas.community.core.service.paas import PaasServiceFacade

    monkeypatch.setattr(
        "secbaas.community.core.service.paas._facade.get_current_env", lambda: "pre"
    )
    facade = PaasServiceFacade(
        device_repository=MagicMock(),
        device_template_service=MagicMock(),
        factory=MagicMock(),
    )
    tpl = template()
    tpl.__pydantic_extra__["storage"] = {"type": "nas", "storage_id": "legacy-default"}
    detail = build()
    merged = facade._merge_config(tpl, detail, "ARCA")
    final = ArcaCreateConfig.model_validate(merged)
    assert final.volume_mounts == detail.volume_mounts
    assert final.storage is None
    tpl.upfs_volume_id_pre = "different-volume"
    with pytest.raises(ValueError, match="missing or changed"):
        facade._merge_config(tpl, detail, "ARCA")
    tpl.upfs_volume_id_pre = None
    with pytest.raises(ValueError, match="missing or changed"):
        facade._merge_config(tpl, detail, "ARCA")


def test_capability_does_not_parse_or_expose_invalid_secrets(monkeypatch):
    svc, record, _ = template_service(monkeypatch)
    record.config["api_key"] = {"invalid": "must-never-appear-in-validation-errors"}
    result = svc.get_storage_capability("tenant", "TEMPLATE-one", "pre")
    assert result.upfs_ready is True
    assert "must-never" not in result.model_dump_json()


def test_capability_rejects_non_arca_config_even_if_record_type_is_arca(monkeypatch):
    svc, record, _ = template_service(monkeypatch)
    record.config["type"] = "LOCAL"
    assert (
        svc.get_storage_capability("tenant", "TEMPLATE-one").reason
        == "unsupported_template_type"
    )


def test_capability_uses_injected_environment(monkeypatch):
    svc, _, _ = template_service(monkeypatch, deployment_env="prod")
    assert svc.get_storage_capability("tenant", "TEMPLATE-one", "prod").upfs_ready
    assert (
        svc.get_storage_capability("tenant", "TEMPLATE-one", "pre").reason
        == "environment_mismatch"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["restart_device", "update_device"])
async def test_unsupported_upfs_template_fails_before_destroy(monkeypatch, operation):
    monkeypatch.setattr(f"{DS}.get_current_env", lambda: "pre")
    svc = service()
    svc._resolve_device_for_operation = MagicMock(
        return_value=(
            SimpleNamespace(provider_device_id="old-sandbox"),
            DeviceConfig(deploy_config=deploy()),
            SimpleNamespace(config=object()),
            "LOCAL",
        )
    )
    with pytest.raises(ValueError, match="UPFS storage requires an ARCA template"):
        await getattr(svc, operation)("tenant", "DEVICE-one", "operator")
    svc._paas_facade.destroy_device.assert_not_called()
    svc._paas_facade.create_device.assert_not_called()
