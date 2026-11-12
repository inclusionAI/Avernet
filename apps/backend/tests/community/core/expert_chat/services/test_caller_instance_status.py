"""Read-only caller allocation and live-provider observation."""
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService
from agentclaw.community.core.expert_chat.errors import ChatPermissionError, BotNotFoundError

import pytest

from tests.community.core.expert_chat.services.test_expert_chat_instance_service import (
    _make_service, BOT_ID, OWNER_ID, USER_ID, BOT_UUID,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError


# Synthetic marker used to assert that response details stay out of logs.
MARKER = "sentinel-credential"


def assert_read_only(svc):
    svc._instance_repo.upsert_instance.assert_not_called()
    svc._instance_repo.update_instance.assert_not_called()
    svc._binding_repo.update_status.assert_not_called()
    svc._bot_build_service.release_async.assert_not_called()
    svc._bot_build_service.upgrade_async.assert_not_called()
    svc._baas.create_bot.assert_not_called()
    svc._baas.upgrade_bot.assert_not_called()
    svc._baas.get_publish_progress.assert_not_called()
    svc._caller_identity.exchange.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("instance", [None, {}, {"ext": None}, {"ext": "bad"}, *[{"ext": {"bot_uuid": value}} for value in [None, "", "  ", 23]]])
async def test_absent_status(instance):
    svc, repo, baas, *_ = _make_service()
    repo.get_instance.return_value = instance
    assert await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID) == {"exists": False, "alive": False}
    baas.get_bot.assert_not_called()
    assert_read_only(svc)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,devices,alive", [
    ("ACTIVE", [{"status": "ACTIVE", "health": "true"}], True),
    ("ACTIVE", [{"status": "ACTIVE", "health": "false"}], False),
    ("ACTIVE", [{"status": "ACTIVE"}], False),
    ("ACTIVE", [{"status": "RELEASED", "health": "true"}], False),
    ("ACTIVE", [{"status": "PENDING", "health": "true"}], False),
    ("ACTIVE", [{"status": "UPDATING", "health": "true"}], False),
    ("ACTIVE", [{"status": "UNKNOWN", "health": "true"}], False),
    ("RELEASED", [{"status": "ACTIVE", "health": "true"}], False),
    ("RELEASED", [], False), ("ACTIVE", [], False),
    ("ACTIVE", [{"status": "PENDING", "health": "false"}, {"status": "ACTIVE", "health": "true"}], True),
])
async def test_live_observation(status, devices, alive, caplog):
    svc, repo, baas, *_ = _make_service()
    repo.get_instance.return_value = {"status": "success", "ext": {"bot_uuid": BOT_UUID}}
    baas.get_bot.return_value = {"status": status, "devices": devices, "token": MARKER}
    with caplog.at_level("INFO"):
        result = await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID)
    assert result == {"exists": True, "alive": alive}
    baas.get_bot.assert_called_once_with(BOT_UUID, health_check=True, engine_type="openclaw", log_response=False)
    repo.get_instance.assert_called_once_with(USER_ID, BOT_ID, OWNER_ID)
    assert "provider.request" in caplog.text and "provider.success" in caplog.text
    assert MARKER not in caplog.text
    assert_read_only(svc)


@pytest.mark.asyncio
async def test_provider_error_is_not_false_success(caplog):
    svc, repo, baas, *_ = _make_service()
    repo.get_instance.return_value = {"ext": {"bot_uuid": BOT_UUID}}
    baas.get_bot.side_effect = BaasServiceError(MARKER)
    with caplog.at_level("INFO"), pytest.raises(BaasServiceError):
        await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID)
    assert "provider.failed" in caplog.text and "error_type=BaasServiceError" in caplog.text
    assert MARKER not in caplog.text
    assert "provider.success" not in caplog.text
    assert_read_only(svc)


@pytest.mark.asyncio
async def test_instance_lookup_uses_real_owner_for_entity_alias():
    svc, repo, baas, _, bots, *_ = _make_service()
    bots.get_by_id_and_owner.return_value = {"bot_id": BOT_ID, "owner_id": OWNER_ID, "entity_id": "space"}
    repo.get_instance.return_value = None
    await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id="space")
    repo.get_instance.assert_called_once_with(USER_ID, BOT_ID, OWNER_ID)
    assert_read_only(svc)


@pytest.mark.asyncio
async def test_missing_source_bot_does_not_read_ledger():
    svc, repo, baas, _, bots, *_ = _make_service()
    bots.get_by_id_and_owner.return_value = None
    with pytest.raises(ChatPermissionError):
        await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID)
    repo.get_instance.assert_not_called()
    baas.get_bot.assert_not_called()


def authorized_service(public="1", permission=False):
    svc = object.__new__(ExpertChatService)
    svc._find_chat_bot = MagicMock(return_value={"bot_id": BOT_ID, "owner_id": OWNER_ID, "public": public})
    svc._collaborator_service = MagicMock()
    svc._collaborator_service.check_collaborator_permission.return_value = {"has_permission": permission}
    svc._instance_service = MagicMock()
    svc._instance_service.get_caller_instance_status = AsyncMock(return_value={"exists": True, "alive": False})
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("public,permission,user", [("1", False, USER_ID), ("0", True, USER_ID), ("0", False, OWNER_ID)])
async def test_chat_authorization(public, permission, user):
    svc = authorized_service(public, permission)
    assert await svc.get_caller_instance_status(user_id=user, bot_id=BOT_ID, owner_id="space") == {"exists": True, "alive": False}
    svc._instance_service.get_caller_instance_status.assert_awaited_once_with(user_id=user, bot_id=BOT_ID, owner_id=OWNER_ID)


@pytest.mark.asyncio
async def test_private_bot_denies_before_observation():
    svc = authorized_service(public="0")
    with pytest.raises(ChatPermissionError):
        await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID)
    svc._instance_service.get_caller_instance_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_bot_denies_before_observation():
    svc = authorized_service()
    svc._find_chat_bot.return_value = None
    with pytest.raises(BotNotFoundError):
        await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID)
    svc._instance_service.get_caller_instance_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["healthy", "released", "http_error", "api_error", "invalid_json", "transport"])
async def test_real_baas_read_does_not_log_provider_credentials(mode, caplog):
    import httpx
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    def respond(request):
        assert request.url.params["health_check"] == "true"
        assert request.url.params["engine_type"] == "openclaw"
        if mode == "transport":
            raise httpx.ConnectError(MARKER, request=request)
        if mode == "http_error":
            return httpx.Response(503, text=MARKER)
        if mode == "released":
            return httpx.Response(404, text=MARKER)
        if mode == "invalid_json":
            return httpx.Response(200, text=MARKER)
        return httpx.Response(200, json={
            "code": 1 if mode == "api_error" else 0,
            "message": MARKER,
            "data": {"status": "ACTIVE", "token": MARKER, "devices": [
                {"status": "ACTIVE", "health": "true", "secret": MARKER},
            ]},
        })

    baas = object.__new__(BaasService)
    baas._tenant = "test"
    with httpx.Client(transport=httpx.MockTransport(respond), base_url="http://test") as client:
        baas._http = client
        svc, repo, *_ = _make_service(baas=baas)
        repo.get_instance.return_value = {"ext": {"bot_uuid": BOT_UUID}}
        with caplog.at_level("INFO"):
            if mode in {"healthy", "released"}:
                assert await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID) == {"exists": True, "alive": mode == "healthy"}
            else:
                with pytest.raises(BaasServiceError, match="(failed|error)"):
                    await svc.get_caller_instance_status(user_id=USER_ID, bot_id=BOT_ID, owner_id=OWNER_ID)
    assert MARKER not in caplog.text
    assert "BaaS raw response" not in caplog.text
