"""Shared Teclaw IAM refresh follows the existing published chat runtime."""
from dataclasses import replace

import pytest

from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeBinding, RuntimeBindingSource, RuntimeBindingTarget,
)
from tests.community.core.caller_identity.test_iam_token_service import _context, _request, _service


@pytest.mark.asyncio
async def test_shared_online_refresh_succeeds_without_independent_caller_instance():
    context = replace(_context(), shared_teclaw_runtime=True)
    service, identity, _, _, _, bindings, locks = _service(context=context, lock_holder="owner-1")
    shared = ResolvedRuntimeBinding(81, RuntimeBindingSource.SERVICE_ONLINE)

    def resolve(request):
        if request.target is RuntimeBindingTarget.CALLER_INSTANCE:
            raise RuntimeBindingResolutionError("no independent Caller instance")
        return shared

    bindings.resolve.side_effect = resolve
    result = await service.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE, publish_id=None, entity_id="entity-1",
        is_test_exchange=False, session_key="owned-session",
    )

    assert result.error is None
    assert result.iam_token == "iam"
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 81
    assert identity.exchange_caller_identity.call_args.kwargs["session_key"] == "owned-session"
    assert identity.exchange_caller_identity.call_count == 1
    assert all(call.args[0].target is RuntimeBindingTarget.AUTO for call in bindings.resolve.call_args_list)
    locks.get_by_key.assert_not_called()


@pytest.fixture
def shared_world(tmp_path, monkeypatch):
    """Real DI, Caller services, resolver, SQLite ledger and BaaS HTTP boundary."""
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import httpx
    from injector import Injector, InstanceProvider

    from agentclaw.community.core.caller_identity.credential import CallerToken
    from agentclaw.community.core.repository.implementations.chat import expert_chat as chat_module
    from agentclaw.community.di.modules import caller_identity_module as di
    from agentclaw.community.kernel.device_dto import HeaderOperationRule, OutBoundOperationRule
    from tests.community.core.caller_identity.test_service import _bot
    from tests.community.core.runtime_binding.test_service import _PublishRecord
    from tests.community.repository.chat.test_expert_chat_unified import _make_db

    env = {"value": "test"}
    monkeypatch.setattr(chat_module, "get_current_env", lambda: env["value"])
    repo = chat_module.ExpertChatRepository(_make_db(tmp_path))
    bot = _bot(call_type="caller")
    bot.update(active_engine="teclaw", binding_id=9)
    bots = MagicMock()
    for method in ("get_by_id_and_entity", "get_by_id_and_owner", "get_unique_by_id"):
        getattr(bots, method).return_value = bot
    binding = SimpleNamespace(
        id=81, status="ACTIVE", device_provider="teclaw", device_id="logical-runtime",
    )
    bindings = MagicMock()
    bindings.get_by_id.return_value = binding
    publications = MagicMock()
    publications.list_by_source_bot.return_value = [_PublishRecord(81)]
    instances = MagicMock()
    instances.get_instance.return_value = None
    runtime = di.RuntimeBindingResolutionService(
        bot_repository=bots, publish_repository=publications,
        binding_repository=bindings, caller_instance_repository=instances,
        environment_provider=lambda: env["value"],
    )
    auth = MagicMock()
    auth.resolve_user_from_request = AsyncMock(return_value=SimpleNamespace(staffId="caller-1"))
    signing = MagicMock()
    signing.exchange.return_value = CallerToken(
        access_token="private-caller-token", subject_user_id="caller-1",
        expires_at=datetime.now(), fingerprint="test-fingerprint",
    )
    requests = []
    http_status = {"put": 200}

    def transport(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": [{"items": [{"provider_device_id": "shared-runtime@template"}]}]})
        return httpx.Response(http_status["put"], json={"code": 0})

    client = httpx.Client(base_url="https://runtime.example", transport=httpx.MockTransport(transport))
    baas = object.__new__(di.BaasService)
    baas._bot_repo = bots
    baas._device_binding_repo = bindings
    baas._tenant = "test"
    baas._http = client
    baas._outbound_rule_provider = MagicMock()
    baas._outbound_rule_provider.build_caller_rule.side_effect = lambda caller_token: OutBoundOperationRule(
        header_operation_rules=[HeaderOperationRule(
            domains=["mcp.example"], action="set", header_name="x-caller-token", value=caller_token,
        )],
    )
    locks = MagicMock()
    locks.get_by_key.return_value = SimpleNamespace(holder_user_id="owner-1")

    def boundaries(binder):
        for interface, value in {
            di.BotRepository: bots, di.DeviceBindingRepository: bindings,
            di.RuntimeBindingResolutionService: runtime,
            di.AuthPlugin: auth, di.CallerTokenProvider: signing, di.BaasService: baas,
            di.BotCollabLockRepositoryProtocol: locks,
            di.CollaboratorRepositoryProtocol: MagicMock(), di.BotMCPProvider: MagicMock(),
            di.CallerIdentityRepositoryProtocol: MagicMock(), di.MCPSyncServiceProtocol: MagicMock(),
            di.PassportPlugin: MagicMock(), di.CoreCollaboratorServiceProtocol: MagicMock(),
            di.BcsCallerConfigSyncProtocol: MagicMock(),
        }.items():
            binder.bind(interface, to=InstanceProvider(value))

    container = Injector([di.CallerIdentityModule(), boundaries], auto_bind=False)
    repo.add_owned_session("caller-1", "bot-1", "owner-1", "owned-session")
    yield SimpleNamespace(
        container=container, repo=repo, bot=bot, bots=bots, binding=binding, publications=publications,
        signing=signing, requests=requests, env=env, instances=instances, auth=auth, locks=locks,
        http_status=http_status,
    )
    client.close()


async def _http_iam(world, session_key="owned-session"):
    from starlette.requests import Request
    from agentclaw.community.adapters.http.token_exchange.router import get_iam_token
    from agentclaw.community.api.caller_iam_token_service import CallerIamTokenServiceProtocol

    request = Request({"type": "http", "method": "GET", "path": "/api/v1/token/iam",
                       "headers": [(b"cookie", b"IAM_TOKEN=private-iam-token")],
                       "query_string": b"", "scheme": "http", "server": ("test", 80)})
    return await get_iam_token(
        request=request, bot_id="bot-1", stage=CallerIdentityStage.ONLINE,
        publish_id=None, entity_id="entity-1", is_test_exchange=False,
        session_key=session_key, service=world.container.get(CallerIamTokenServiceProtocol),
    )


@pytest.mark.asyncio
async def test_real_shared_refresh_uses_owned_online_session_and_append(shared_world, caplog):
    import json
    import logging

    with caplog.at_level(logging.INFO):
        response = await _http_iam(shared_world)

    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True, "iam_token": "private-iam-token"}
    puts = [request for request in shared_world.requests if request.method == "PUT"]
    assert len(puts) == 1
    assert puts[0].url.path == "/api/v1/paas/devices/shared-runtime@template/outbound-rule"
    assert dict(puts[0].url.params) == {"mode": "append", "session_key": "owned-session"}
    assert json.loads(puts[0].content)["header_operation_rules"][0]["value"] == "private-caller-token"
    shared_world.instances.get_instance.assert_not_called()
    shared_world.locks.get_by_key.assert_not_called()
    assert "caller_shared_session_validated" in caplog.text
    assert "ownership_check=not_required" in caplog.text
    assert "caller_shared_target_selected" in caplog.text
    assert "caller_shared_target_fenced" in caplog.text
    assert "caller_shared_target_refresh_succeeded" in caplog.text
    assert "owned-session" in caplog.text
    assert "private-caller-token" not in caplog.text
    assert "private-iam-token" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("session_key", [None, "", " \t"])
async def test_shared_missing_session_rejected_before_signing(shared_world, session_key):
    response = await _http_iam(shared_world, session_key)
    assert response.status_code == 400
    shared_world.signing.exchange.assert_not_called()
    assert shared_world.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["user", "bot", "owner", "environment", "deleted", "absent"])
async def test_shared_refresh_does_not_require_matching_owned_session(shared_world, scope):
    shared_world.repo.delete_owned_session("caller-1", "bot-1", "owner-1", "owned-session")
    if scope == "user":
        shared_world.repo.add_owned_session("another-user", "bot-1", "owner-1", "owned-session")
    elif scope == "bot":
        shared_world.repo.add_owned_session("caller-1", "another-bot", "owner-1", "owned-session")
    elif scope == "owner":
        shared_world.repo.add_owned_session("caller-1", "bot-1", "another-owner", "owned-session")
    elif scope == "environment":
        shared_world.env["value"] = "another-environment"
        shared_world.repo.add_owned_session("caller-1", "bot-1", "owner-1", "owned-session")
        shared_world.env["value"] = "test"
    response = await _http_iam(shared_world)
    assert response.status_code == 200
    shared_world.signing.exchange.assert_called_once()
    puts = [request for request in shared_world.requests if request.method == "PUT"]
    assert len(puts) == 1
    assert dict(puts[0].url.params) == {"mode": "append", "session_key": "owned-session"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["publication", "provider", "engine", "session", "inactive"])
async def test_shared_target_changes_still_reject_but_ledger_deletion_allows_append(shared_world, change):
    original = shared_world.signing.exchange.return_value

    def exchange(**kwargs):
        if change == "publication":
            shared_world.publications.list_by_source_bot.return_value[0].ext["binding"]["online"] = 82
        elif change == "provider":
            shared_world.binding.device_provider = "arca"
        elif change == "engine":
            shared_world.bot["active_engine"] = "openclaw"
        elif change == "inactive":
            shared_world.binding.status = "RELEASED"
        else:
            shared_world.repo.delete_owned_session("caller-1", "bot-1", "owner-1", "owned-session")
        return original

    shared_world.signing.exchange.side_effect = exchange
    response = await _http_iam(shared_world)
    if change == "session":
        assert response.status_code == 200
        puts = [request for request in shared_world.requests if request.method == "PUT"]
        assert len(puts) == 1
        assert dict(puts[0].url.params) == {"mode": "append", "session_key": "owned-session"}
    else:
        assert response.status_code == 502
        assert shared_world.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["signing", "subject", "append", "missing_publication", "wrong_provider"])
async def test_shared_required_failures_remain_errors(shared_world, failure, caplog):
    from dataclasses import replace

    if failure == "signing":
        shared_world.signing.exchange.side_effect = RuntimeError("private-upstream-secret")
    elif failure == "subject":
        shared_world.signing.exchange.return_value = replace(shared_world.signing.exchange.return_value, subject_user_id="another-user")
    elif failure == "append":
        shared_world.http_status["put"] = 503
    elif failure == "missing_publication":
        shared_world.publications.list_by_source_bot.return_value = []
    else:
        shared_world.binding.device_provider = "arca"
    response = await _http_iam(shared_world)
    assert response.status_code == (400 if failure == "subject" else 502)
    assert "private-upstream-secret" not in caplog.text
    assert "private-caller-token" not in caplog.text
    if failure in {"missing_publication", "wrong_provider"}:
        shared_world.signing.exchange.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["caller-1", "owner-1"])
async def test_shared_friend_identity_and_owner_use_the_subjects_owned_session(shared_world, subject):
    from dataclasses import replace
    from types import SimpleNamespace
    from agentclaw.community.api.caller_iam_token_service import CallerIamTokenServiceProtocol

    shared_world.auth.resolve_user_from_request.return_value = SimpleNamespace(staffId=subject)
    shared_world.signing.exchange.return_value = replace(
        shared_world.signing.exchange.return_value, subject_user_id=subject,
    )
    shared_world.repo.add_owned_session(subject, "bot-1", "owner-1", "friend-session")
    service = shared_world.container.get(CallerIamTokenServiceProtocol)
    result = await service.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE, publish_id=None, entity_id="entity-1",
        is_test_exchange=False, session_key="friend-session", friend_user_id=subject,
    )
    assert result.error is None
    puts = [request for request in shared_world.requests if request.method == "PUT"]
    assert len(puts) == 1
    assert puts[0].url.params["session_key"] == "friend-session"
    shared_world.instances.get_instance.assert_not_called()
    shared_world.locks.get_by_key.assert_not_called()


@pytest.mark.asyncio
async def test_shared_friend_mismatch_is_rejected_before_signing(shared_world):
    from agentclaw.community.api.caller_iam_token_service import CallerIamTokenServiceProtocol

    service = shared_world.container.get(CallerIamTokenServiceProtocol)
    result = await service.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE, publish_id=None, entity_id="entity-1",
        is_test_exchange=False, session_key="owned-session", friend_user_id="another-user",
    )
    assert result.error == "CALLER_IDENTITY_FORBIDDEN"
    shared_world.signing.exchange.assert_not_called()
    assert shared_world.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [CallerIdentityStage.DRAFT, CallerIdentityStage.VERIFY])
async def test_shared_capability_does_not_grant_callers_other_stage_service_access(shared_world, stage):
    from agentclaw.community.api.caller_iam_token_service import CallerIamTokenServiceProtocol

    service = shared_world.container.get(CallerIamTokenServiceProtocol)
    result = await service.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1", stage=stage,
        publish_id=None, entity_id="entity-1", is_test_exchange=False, session_key="owned-session",
    )
    assert result.error == "CALLER_OUTBOUND_UPDATE_FAILED"
    assert shared_world.requests == []
    shared_world.instances.get_instance.assert_called_once()


@pytest.mark.asyncio
async def test_shared_refresh_does_not_create_or_mutate_session_ownership(shared_world):
    from sqlalchemy import select

    def snapshot():
        repo = shared_world.repo
        with repo._db.orm_session() as db:
            return list(db.execute(select(repo.OwnedSessionModel.__table__)).tuples())

    before = snapshot()
    response = await _http_iam(shared_world)
    assert response.status_code == 200
    assert snapshot() == before


@pytest.mark.asyncio
async def test_shared_first_target_lookup_failure_is_rejected_before_signing(shared_world):
    import json

    shared_world.bots.get_by_id_and_owner.return_value = None
    response = await _http_iam(shared_world)
    assert response.status_code == 502
    assert json.loads(response.body)["error"] == "CALLER_TARGET_NOT_FOUND"
    shared_world.signing.exchange.assert_not_called()
    assert shared_world.requests == []


@pytest.mark.asyncio
async def test_shared_context_cannot_install_into_an_unexpected_runtime_source():
    context = replace(_context(), shared_teclaw_runtime=True)
    service, identity, _, _, _, bindings, _ = _service(context=context)
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(81, RuntimeBindingSource.CALLER_INSTANCE)
    result = await service.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE, publish_id=None, entity_id="entity-1",
        is_test_exchange=False, session_key="owned-session",
    )
    assert result.error == "CALLER_TARGET_NOT_FOUND"
    identity.exchange_caller_token.assert_not_called()
    identity.exchange_caller_identity.assert_not_called()
