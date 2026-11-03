"""Exercise request-scoped image overrides through HTTP, SQLite, and stub PaaS."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from secbaas.community.core.utils.env_utils import get_current_env
from tests.e2e.asgi.conftest import (
    APITestHelper,
    activate_bot,
    approve_and_complete,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.e2e_asgi]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,config,expected_image",
    [
        (operation, config, image)
        for operation in ("update", "update-devices")
        for config, image in [
            ("missing", None),
            (None, None),
            ({}, None),
            ({"deploy_config": None}, None),
            ({"deploy_config": {"docker_image": None}}, None),
            ({"deploy_config": {"docker_image": "requested:image"}}, "requested:image"),
        ]
    ]
    + [
        ("scale", "missing", "previous:image"),
        ("scale", None, "previous:image"),
        ("scale", {}, "previous:image"),
        ("scale", {"deploy_config": None}, "previous:image"),
        ("scale", {"deploy_config": {}}, None),
        (
            "scale",
            {"deploy_config": {"docker_image": "requested:image"}},
            "requested:image",
        ),
        ("restart", "missing", "previous:image"),
    ],
)
async def test_deployment_obeys_update_only_image_reset_scope(
    api: APITestHelper,
    unique_id: str,
    bootstrap_init,
    monkeypatch,
    operation,
    config,
    expected_image,
):
    bot = await create_test_bot(
        api,
        f"image-{unique_id}",
        device_count=1,
        deploy_config={
            "docker_image": "previous:image",
            "envs": {"IMAGE_TEST": "keep"},
            "ttl_in_minutes": 120,
        },
    )
    try:
        await activate_bot(api, bot)
        env = get_current_env()
        devices = bootstrap_init.repository.device_repository()
        old_devices = devices.list_by_bot_id(
            bot_id=bot["id"], tenant=api.tenant, env=env
        )
        assert len(old_devices) == 1
        assert (
            old_devices[0].extra_config["deploy_config"]["docker_image"]
            == "previous:image"
        )
        service = bootstrap_init.services.device_service()
        facade = service._paas_facade
        provision = AsyncMock(wraps=facade.create_device)
        monkeypatch.setattr(facade, "create_device", provision)
        body = {"operator": "test", "request_id": uuid4().hex}
        if operation == "scale":
            body["target_count"] = 2
        elif operation == "update-devices":
            body["device_uuids"] = [old_devices[0].device_uuid]
        elif operation == "update" and config in ("missing", None):
            # UPDATE requires a deployment/template change, not metadata only.
            body["template_uuid"] = bot["template_uuid"]
        if config != "missing":
            body["config"] = config
        response = await api.client.post(
            f"{api.bot_url(bot['bot_uuid'])}/{operation}",
            params=api.params(),
            json=body,
        )
        assert response.status_code == 200, response.text
        publish_id = response.json()["data"]["publish_id"]
        assert publish_id
        assert (
            await approve_and_complete(api, publish_id, max_iterations=5) == "SUCCESS"
        )
        publish = bootstrap_init.repository.publish_repository().get_by_id(
            publish_id, api.tenant, env
        )
        if operation != "restart":
            assert (
                publish.extra_config["deploy_config"].get("docker_image")
                == expected_image
            )
        batches = (
            bootstrap_init.repository.publish_batch_repository().list_by_publish_id(
                publish_id, api.tenant, env
            )
        )
        records = [
            record
            for batch in batches
            for record in bootstrap_init.repository.publish_record_repository().list_by_batch_id(
                batch.id, api.tenant, env
            )
        ]
        assert records
        for record in records:
            device = devices.get_by_id(record.device_id, api.tenant, env)
            assert (
                device.extra_config["deploy_config"].get("docker_image")
                == expected_image
            )
            assert device.extra_config["deploy_config"]["envs"] == {
                "IMAGE_TEST": "keep"
            }
            assert device.extra_config["deploy_config"]["ttl_in_minutes"] == 120
        assert provision.await_count > 0
        for call in provision.await_args_list:
            detail = call.kwargs["detail_config"]
            assert detail.docker_image == expected_image
            assert detail.envs["IMAGE_TEST"] == "keep"
    finally:
        await cleanup_bot(api, bot["bot_uuid"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,image",
    [
        ("update", None),
        ("update", "requested:image"),
        ("update-devices", None),
        ("update-devices", "requested:image"),
    ],
)
@pytest.mark.parametrize("write_failure", ["exception", "zero-rows"])
async def test_failed_config_write_is_terminal_even_with_retry_budget(
    api,
    unique_id,
    bootstrap_init,
    monkeypatch,
    operation,
    image,
    write_failure,
):
    bot = await create_test_bot(
        api,
        f"image-write-failure-{unique_id}",
        device_count=1,
        deploy_config={"docker_image": "previous:image"},
        publish_max_retry_times=2,
    )
    try:
        await activate_bot(api, bot)
        env = get_current_env()
        devices = bootstrap_init.repository.device_repository()
        old = devices.list_by_bot_id(bot_id=bot["id"], tenant=api.tenant, env=env)[0]
        body = {"operator": "test", "request_id": uuid4().hex}
        if operation != "restart":
            body["config"] = {"deploy_config": {"docker_image": image}}
        else:
            body["publish_max_retry_times"] = 2
        if operation == "update-devices":
            body["device_uuids"] = [old.device_uuid]
        service = bootstrap_init.services.publish_service()
        with monkeypatch.context() as patcher:

            def fail_write(**kwargs):
                # No PROCESSING record may be visible to timeout/sweep before
                # the intended configuration is durably persisted.
                assert not service._publish_record_repo.list_stale_processing_records(
                    publish_id, -1, api.tenant, env
                )
                if write_failure == "exception":
                    raise RuntimeError("simulated configuration write failure")
                return 0

            patcher.setattr(devices, "update_device", fail_write)
            retry = AsyncMock(wraps=service._drive_record_attempt)
            provision = AsyncMock(
                wraps=service._device_service._paas_facade.create_device
            )
            patcher.setattr(service, "_drive_record_attempt", retry)
            patcher.setattr(
                service._device_service._paas_facade, "create_device", provision
            )
            response = await api.client.post(
                f"{api.bot_url(bot['bot_uuid'])}/{operation}",
                params=api.params(),
                json=body,
            )
            assert response.status_code == 200, response.text
            publish_id = response.json()["data"]["publish_id"]
            assert (
                await approve_and_complete(api, publish_id, max_iterations=5)
                == "FAILED"
            )
            retry.assert_not_awaited()
            provision.assert_not_awaited()
        batches = service._publish_batch_repo.list_by_publish_id(
            publish_id, api.tenant, env
        )
        records = service._publish_record_repo.list_by_batch_id(
            batches[0].id, api.tenant, env
        )
        assert len(records) == 1 and records[0].result_status == "FAILED"
        assert records[0].result_message == (
            "simulated configuration write failure"
            if write_failure == "exception"
            else "Device configuration update affected no rows"
        )
        assert records[0].extra_config["publish_max_retry_times"] == 2
        assert not await service._drive_record_attempt(
            record=records[0],
            tenant=api.tenant,
            operator="test",
            publish_id=publish_id,
            failure_reason="timeout sweep replay",
        )
        assert (
            devices.get_by_id(old.id, api.tenant, env).extra_config["deploy_config"][
                "docker_image"
            ]
            == "previous:image"
        )
    finally:
        await cleanup_bot(api, bot["bot_uuid"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publish_type,config,expected_image",
    [
        ("UPDATE", "missing", None),
        ("UPDATE", None, None),
        ("UPDATE", {}, None),
        ("UPDATE", {"deploy_config": None}, None),
        ("UPDATE", {"deploy_config": {}}, None),
        ("UPDATE", {"deploy_config": {"docker_image": None}}, None),
        (
            "UPDATE",
            {"deploy_config": {"docker_image": "requested:image"}},
            "requested:image",
        ),
        ("UPDATE_DEVICE", {}, None),
        ("UPDATE_DEVICE", {"deploy_config": None}, None),
        ("UPDATE_DEVICE", {"deploy_config": {}}, None),
        ("UPDATE_DEVICE", {"deploy_config": {"docker_image": None}}, None),
        (
            "UPDATE_DEVICE",
            {"deploy_config": {"docker_image": "requested:image"}},
            "requested:image",
        ),
    ],
)
async def test_direct_publish_api_resolves_and_persists_update_image(
    api, unique_id, bootstrap_init, monkeypatch, publish_type, config, expected_image
):
    """The direct publish endpoint bypasses BotManagementService's merge helper."""
    bot = await create_test_bot(
        api,
        f"direct-image-{unique_id}",
        device_count=1,
        deploy_config={
            "docker_image": "previous:image",
            "envs": {"IMAGE_TEST": "keep"},
        },
    )
    try:
        await activate_bot(api, bot)
        env = get_current_env()
        bots = bootstrap_init.repository.bot_repository()
        source = bots.get_by_id(bot["id"], api.tenant, env)
        bots.update_bot(
            bot_id=bot["id"],
            tenant=api.tenant,
            env=env,
            modifier="test",
            extra_config={
                **source.extra_config,
                "entity_id": "preserve-entity",
                "custom_note": "keep",
            },
        )
        devices = bootstrap_init.repository.device_repository()
        old = devices.list_by_bot_id(bot_id=bot["id"], tenant=api.tenant, env=env)[0]
        body = {
            "bot_id": bot["id"],
            "publish_type": publish_type,
            "operator": "test",
            "request_id": uuid4().hex,
        }
        if publish_type == "UPDATE_DEVICE":
            config = {
                **config,
                "replica_desired": 1,
                "target_device_uuids": [old.device_uuid],
            }
        if config != "missing":
            body["config"] = config
        response = await api.client.post(
            api.publish_url(), params=api.params(), json=body
        )
        assert response.status_code == 200, response.text
        publish_id = response.json()["data"]["id"]
        publish = bootstrap_init.repository.publish_repository().get_by_id(
            publish_id, api.tenant, env
        )
        assert "deploy_config" in publish.extra_config
        snapshot = publish.extra_config["deploy_config"]
        assert snapshot.get("docker_image") == expected_image
        assert snapshot["envs"] == {"IMAGE_TEST": "keep"}
        if publish_type == "UPDATE":
            clone = bootstrap_init.repository.bot_repository().get_by_id(
                publish.extra_config["target_bot_id"], api.tenant, env
            )
            assert (
                clone.extra_config["deploy_config"].get("docker_image")
                == expected_image
            )
            assert clone.extra_config["deploy_config"]["envs"] == {"IMAGE_TEST": "keep"}
            if not isinstance(config, dict) or config.get("deploy_config") is None:
                assert clone.extra_config["entity_id"] == "preserve-entity"
                assert clone.extra_config["custom_note"] == "keep"
        facade = bootstrap_init.services.device_service()._paas_facade
        provision = AsyncMock(wraps=facade.create_device)
        monkeypatch.setattr(facade, "create_device", provision)
        assert (
            await approve_and_complete(api, publish_id, max_iterations=5) == "SUCCESS"
        )
        updated = devices.get_by_id(old.id, api.tenant, env)
        assert (
            updated.extra_config["deploy_config"].get("docker_image") == expected_image
        )
        assert updated.extra_config["deploy_config"]["envs"] == {"IMAGE_TEST": "keep"}
        assert provision.await_count > 0
        assert all(
            c.kwargs["detail_config"].docker_image == expected_image
            for c in provision.await_args_list
        )
    finally:
        await cleanup_bot(api, bot["bot_uuid"])


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_type", ["UPDATE", "UPDATE_DEVICE"])
async def test_new_publish_keeps_explicit_null_deployment_snapshot(
    api, unique_id, bootstrap_init, monkeypatch, publish_type
):
    bot = await create_test_bot(api, f"null-snapshot-{unique_id}", device_count=1)
    try:
        await activate_bot(api, bot)
        env = get_current_env()
        bots = bootstrap_init.repository.bot_repository()
        source = bots.get_by_id(bot["id"], api.tenant, env)
        assert (source.extra_config or {}).get("deploy_config") is None
        devices = bootstrap_init.repository.device_repository()
        old = devices.list_by_bot_id(bot_id=bot["id"], tenant=api.tenant, env=env)[0]
        body = {
            "bot_id": bot["id"],
            "publish_type": publish_type,
            "operator": "test",
            "request_id": uuid4().hex,
        }
        if publish_type == "UPDATE_DEVICE":
            body["config"] = {
                "replica_desired": 1,
                "target_device_uuids": [old.device_uuid],
            }
        response = await api.client.post(
            api.publish_url(), params=api.params(), json=body
        )
        assert response.status_code == 200, response.text
        publish_id = response.json()["data"]["id"]
        publish = bootstrap_init.repository.publish_repository().get_by_id(
            publish_id, api.tenant, env
        )
        assert "deploy_config" in publish.extra_config
        assert publish.extra_config["deploy_config"] is None
        if publish_type == "UPDATE":
            clone = bots.get_by_id(
                publish.extra_config["target_bot_id"], api.tenant, env
            )
            assert "deploy_config" in clone.extra_config
            assert clone.extra_config["deploy_config"] is None
        else:
            # The immutable null snapshot must beat a subsequently changed Bot row.
            bots.update_bot(
                bot_id=bot["id"],
                tenant=api.tenant,
                env=env,
                modifier="test",
                extra_config={
                    **(source.extra_config or {}),
                    "deploy_config": {"docker_image": "later:image"},
                },
            )
        facade = bootstrap_init.services.device_service()._paas_facade
        provision = AsyncMock(wraps=facade.create_device)
        monkeypatch.setattr(facade, "create_device", provision)
        assert (
            await approve_and_complete(api, publish_id, max_iterations=5) == "SUCCESS"
        )
        updated = devices.get_by_id(old.id, api.tenant, env)
        assert updated.extra_config.get("deploy_config") is None
        assert provision.await_count > 0
        assert all(
            c.kwargs["detail_config"].docker_image is None
            for c in provision.await_args_list
        )
    finally:
        await cleanup_bot(api, bot["bot_uuid"])
