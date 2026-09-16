"""UPFS storage contracts from persisted DeployConfig to project-owned ARCA DTOs."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.api.device_manage import (
    DeployConfig,
    DeviceConfig,
    Storage,
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
        quota="1G",
        permission="0000",
    )
    fields.update(kwargs)
    return DeployConfig(storage=Storage(**fields))


def build(config=None, *, device="DEVICE-one", env="pre", tpl=None, size=None):
    config = config or deploy()
    if size is not None:
        config.storage.quota = str(size)
    return _build_arca_detail_config(
        deploy_config=config,
        oss_mount_id=None,
        env=env,
        device_uuid=device,
        secret_plugin=MagicMock(),
        arca_template_config=tpl or template(),
    )[0]


@pytest.mark.parametrize(
    "env,expected", [("pre", "volume-pre"), ("prod", "volume-prod")]
)
def test_upfs_mapping_uses_environment_volume_and_ignores_nas_fields(env, expected):
    config = deploy()
    detail = build(config, env=env, size=2048)
    assert detail.storage.type == "upfs"
    assert (detail.metadata or {}).get("upfs_volume_id") == expected
    assert detail.storage.quota == "2048"
    assert detail.storage.storage_id == "bot_DEVICE-one"
    assert detail.storage.path == "/home/admin"
    assert detail.to_create_config().storage == detail.storage
    assert (detail.to_create_config().metadata or {}).get("upfs_volume_id") == (
        detail.metadata or {}
    ).get("upfs_volume_id")
    assert (
        config.storage.storage_id == "bot_{device_uuid}"
    )  # stored template not mutated


def test_restart_and_scale_device_identity():
    config = deploy()
    first = build(config)
    restart = build(config)
    scaled = build(config, device="DEVICE-two")
    assert restart.storage == first.storage
    assert (scaled.metadata or {}).get("upfs_volume_id") == (first.metadata or {}).get(
        "upfs_volume_id"
    )
    assert scaled.storage.storage_id != first.storage.storage_id
    assert (
        DeviceConfig.model_validate(
            DeviceConfig(deploy_config=config).model_dump()
        ).deploy_config.storage.type
        == "upfs"
    )


def test_nas_storage_unchanged_even_without_volume():
    config = deploy(
        type="nas", storage_id="/old_{device_uuid}/", quota="2Gi", permission="0777"
    )
    detail = build(config, env="dev")
    assert (detail.metadata or {}).get("upfs_volume_id") is None
    assert detail.storage.model_dump() == dict(
        config.storage.model_dump(), storage_id="/old_DEVICE-one/"
    )


def service():
    return DefaultDeviceService(
        paas_facade=MagicMock(create_device=AsyncMock(), destroy_device=AsyncMock()),
        repository=MagicMock(),
        device_template_service=MagicMock(),
        secret_plugin=MagicMock(),
        callback_handler=MagicMock(),
    )


def template_service(monkeypatch):
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
    )
    return svc, record, repo


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
    assert final.storage == detail.storage
    assert final.storage.type == "upfs"


@pytest.mark.parametrize("exclude_none", [True, False])
def test_storage_roundtrip_preserves_upfs_and_legacy_nas_wire_shape(exclude_none):
    from secbaas.community.api.device_manage import ArcaCreateConfig, ArcaDeviceConfig

    nas = dict(
        type="nas", path="/nas", storage_id="legacy", quota=None, permission="0777"
    )
    expected = {
        key: value
        for key, value in nas.items()
        if not exclude_none or value is not None
    }
    assert Storage(**nas).model_dump(exclude_none=exclude_none) == expected
    assert (
        ArcaDeviceConfig(storage=Storage(**nas))
        .to_create_config()
        .storage.model_dump(exclude_none=exclude_none)
        == expected
    )
    original = build()
    # Storage retains its old shape; mounts belong only to ARCA create config.
    detail = ArcaDeviceConfig.model_validate_json(
        original.model_dump_json(exclude_none=exclude_none)
    )
    config = ArcaCreateConfig.model_validate_json(
        detail.to_create_config().model_dump_json(exclude_none=exclude_none)
    )
    assert config.storage == original.storage
    assert (config.metadata or {}).get("upfs_volume_id") == (
        original.metadata or {}
    ).get("upfs_volume_id")
    assert "volume_mounts" not in config.model_dump()
    assert "upfs" not in config.storage.model_dump()
    assert "volume_mounts" not in config.storage.model_dump()


def test_client_extras_cannot_override_server_template_volume():
    config = deploy(storage_id="bot_DEVICE-one", volume_id="client-volume")
    detail = build(config, size=2048)
    assert (detail.metadata or {}).get("upfs_volume_id") == "volume-pre"
    assert detail.storage.quota == "2048"


def test_reused_template_endpoint_is_online_and_tenant_scoped(monkeypatch):
    from secbaas.community.adapters.web.routers.config_management.device_template_router import (
        router,
    )

    svc, _, repo = template_service(monkeypatch)
    app = FastAPI()
    app.include_router(router)
    route = next(
        r
        for r in router.routes
        if r.path == "/api/v1/device-templates/{template_uuid}" and "GET" in r.methods
    )
    dependency = next(d for d in route.dependant.dependencies if d.name == "service")
    app.dependency_overrides[dependency.call] = lambda: svc
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/device-templates/TEMPLATE-one", params={"tenant": "tenant"}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "ONLINE" and data["type"] == "ARCA"
        assert data["config"]["upfs_volume_id_pre"] == "volume-pre"
        assert (
            client.get(
                "/api/v1/device-templates/TEMPLATE-one", params={"tenant": "other"}
            ).status_code
            == 404
        )
    repo.get_online_by_template_uuid.assert_any_call(
        template_uuid="TEMPLATE-one", tenant="tenant"
    )
    assert not any(r.path.endswith("/storage-capability") for r in router.routes)


def test_template_volume_replaces_untrusted_metadata_without_mutation():
    config = deploy()
    config.arca_metadata = {"upfs_volume_id": "untrusted", "trace": "keep"}
    detail = build(config)
    assert detail.metadata == {"upfs_volume_id": "volume-pre", "trace": "keep"}
    assert config.arca_metadata == {"upfs_volume_id": "untrusted", "trace": "keep"}


@pytest.mark.parametrize("storage_type", ["nas", "upfs"])
@pytest.mark.parametrize("volume", [None, "", " ", " volume ", "volume-pre"])
def test_public_builder_forwards_template_context_without_storage_validation(
    storage_type, volume
):
    tpl = template()
    tpl.upfs_volume_id_pre = volume
    config = deploy(
        type=storage_type, storage_id="../unvalidated", path="relative", quota="invalid"
    )
    detail = build(config, tpl=tpl)
    assert (detail.metadata or {}).get("upfs_volume_id") == (volume or None)
    assert detail.storage.quota == "invalid"
    assert detail.storage.path == "relative"
    assert detail.storage.storage_id == "../unvalidated"
    assert detail.storage.type == storage_type


def test_public_storage_schema_and_api_have_no_upfs_parser():
    import secbaas.community.api.device_manage as api

    assert set(Storage.model_fields) == {
        "type",
        "storage_id",
        "path",
        "quota",
        "permission",
    }
    assert not hasattr(api, "validate_upfs_storage")
    assert not hasattr(api, "parse_upfs_quota")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["restart_device", "update_device"])
async def test_recreate_retains_destroy_then_create_order_without_upfs_precheck(
    monkeypatch, operation
):
    monkeypatch.setattr(f"{DS}.get_current_env", lambda: "pre")
    svc = service()
    tpl = template()
    tpl.upfs_volume_id_pre = None
    svc._resolve_device_for_operation = MagicMock(
        return_value=(
            SimpleNamespace(id=1, provider_device_id="old", status="ACTIVE"),
            DeviceConfig(
                deploy_config=deploy(),
                metadata={"trace": "keep"},
            ),
            SimpleNamespace(config=tpl, template_uuid="TEMPLATE-one"),
            "ARCA",
        )
    )
    events = []

    async def destroy(*args, **kwargs):
        events.append("destroy")
        return True

    async def create(*args, **kwargs):
        events.append("create")
        assert kwargs["detail_config"].metadata == {"trace": "keep"}
        raise ValueError("plugin rejected missing volume")

    svc._paas_facade.destroy_device.side_effect = destroy
    svc._paas_facade.create_device.side_effect = create
    monkeypatch.setattr(f"{DS}.device_record_to_response", lambda record: record)
    await getattr(svc, operation)("tenant", "DEVICE-one", "operator")
    assert events == ["destroy", "create"]
    updated = svc._repository.update_device.call_args.kwargs
    assert updated["status"] == "FAILED"
    assert "plugin rejected" in updated["err_msg"]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_type", ["nas", "upfs"])
async def test_start_merges_existing_metadata_and_template_volume(
    monkeypatch, storage_type
):
    monkeypatch.setattr(f"{DS}.get_current_env", lambda: "pre")
    svc = service()
    config = DeviceConfig(
        deploy_config=deploy(type=storage_type),
        metadata={"trace": "keep"},
    )
    record = SimpleNamespace(id=1, extra_config=config.model_dump())
    svc._repository.get_by_device_uuid.return_value = record
    svc._device_template_service.get_default_or_explicit_template.return_value = (
        SimpleNamespace(config=template(), template_uuid="TEMPLATE-one")
    )
    # Stop at the PaaS boundary: this test checks real start-path assembly.
    svc._paas_facade.create_device.side_effect = RuntimeError("test boundary")
    monkeypatch.setattr(f"{DS}.device_record_to_response", lambda value: value)
    await svc.start_device("tenant", "DEVICE-one", publish_id=7)
    svc._paas_facade.create_device.assert_awaited_once()
    detail = svc._paas_facade.create_device.call_args.kwargs["detail_config"]
    assert detail.metadata == {
        "upfs_volume_id": "volume-pre",
        "trace": "keep",
        "publish_id": "7",
        "device_uuid": "DEVICE-one",
        "tenant": "tenant",
    }
    assert detail.storage.type == storage_type
    assert config.metadata == {"trace": "keep"}


@pytest.mark.parametrize("env", ["pre", "prod", "dev", "test", "", None])
@pytest.mark.parametrize("specific", [None, ""])
def test_volume_fallback_reaches_create_config(env, specific):
    tpl = template(upfs_volume_id="volume-default")
    tpl.upfs_volume_id_pre = specific
    tpl.upfs_volume_id_prod = specific
    detail = build(env=env, tpl=tpl)
    assert detail.to_create_config().metadata["upfs_volume_id"] == "volume-default"


@pytest.mark.parametrize(
    "env,expected", [("pre", "volume-pre"), ("prod", "volume-prod")]
)
def test_environment_volume_takes_priority_over_default(env, expected):
    detail = build(env=env, tpl=template(upfs_volume_id="volume-default"))
    assert detail.metadata["upfs_volume_id"] == expected
