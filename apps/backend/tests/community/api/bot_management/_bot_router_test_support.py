"""Tests for bot_management API router.

Uses a minimal FastAPI test app — never imports agentclaw.servers.web.app.
"""

import pytest


from unittest.mock import MagicMock, AsyncMock, patch


from fastapi import FastAPI


from fastapi.testclient import TestClient


from fastapi_injector import attach_injector


from injector import Injector, Module


from agentclaw.community.adapters.http.auth.dependencies import require_operator


from agentclaw.community.adapters.http.auth.dependencies import get_current_user


from agentclaw.community.adapters.http.auth.models import AuthenticatedUser


from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)


from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
    DeviceAllocationError,
    BotNameExistsError,
    BotNameInvalidError,
    BotLimitExceededError,
    DefaultBotTeclawNotAllowedError,
    DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
    DeviceLimitError,
)


from agentclaw.community.core.bot_management.errors import (
    BotCreationRetainedError,
    BotLookupAmbiguousError,
)


from agentclaw.community.core.errors import Unauthorized


from agentclaw.community.plugin_api.passport import PassportError, PassportPlugin


from agentclaw.community.plugin_api.auth import AuthPlugin


from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin


from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory


from agentclaw.community.core.access.services.policy_service import PolicyService


def _bind_bot_service(
    svc,
    bot_repo=None,
    passport=None,
    auth=None,
    auth_rel=None,
    skill_set_factory=None,
    policy_service=None,
    create_bot_for_others_service=None,
    default_bot_passport_repair_service=None,
    bot_space_service=None,
):
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.api.bot_service import BotServiceProtocol
    from agentclaw.community.api.default_bot_passport_repair_service import (
        DefaultBotPassportRepairServiceProtocol,
    )
    from agentclaw.community.api.create_bot_for_others_service import (
        CreateBotForOthersServiceProtocol,
    )
    from agentclaw.community.api.bot_space_service import BotSpaceServiceProtocol

    if bot_space_service is None:
        bot_space_service = MagicMock()

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotService, to=svc)
            binder.bind(BotServiceProtocol, to=svc)
            binder.bind(BotSpaceServiceProtocol, to=bot_space_service)
            if create_bot_for_others_service is not None:
                binder.bind(
                    CreateBotForOthersServiceProtocol,
                    to=create_bot_for_others_service,
                )
            if default_bot_passport_repair_service is not None:
                binder.bind(
                    DefaultBotPassportRepairServiceProtocol,
                    to=default_bot_passport_repair_service,
                )
            if bot_repo is not None:
                # Explicit mutable-Bot fixture for route-unit tests.
                bot_repo.list_matches_by_legacy_locator.side_effect = (
                    lambda bot_id, locator: [dict(BOT_SAMPLE, bot_id=bot_id)]
                )
                binder.bind(BotRepository, to=bot_repo)
            if passport is not None:
                binder.bind(PassportPlugin, to=passport)
            if auth is not None:
                binder.bind(AuthPlugin, to=auth)
            if auth_rel is not None:
                binder.bind(AuthRelationshipPlugin, to=auth_rel)
            if skill_set_factory is not None:
                binder.bind(SkillSetServiceFactory, to=skill_set_factory)
            if policy_service is not None:
                from agentclaw.community.api.policy_service import PolicyServiceProtocol

                binder.bind(PolicyService, to=policy_service)
                binder.bind(PolicyServiceProtocol, to=policy_service)

    return _M()


def _stub_skill_set_factory(mcp_codes=None):
    """A SkillSetServiceFactory mock whose .create().get_bot_mcp_codes() returns the given list."""
    mcp_codes = mcp_codes if mcp_codes is not None else []
    skill_set_service = MagicMock()
    skill_set_service.get_bot_mcp_codes.return_value = mcp_codes
    factory = MagicMock()
    factory.create.return_value = skill_set_service
    return factory


BOT_SAMPLE = {
    "id": 1,
    "bot_id": "default",
    "owner_id": "test_user",
    "bot_name": "TestBot",
    "status": "ACTIVE",
    "entity_id": "test_user",
    "entity_type": "staff",
    "public": "0",
    "ext": {},
    "device_binding": {"status": "ACTIVE", "device_id": "dev123"},
}


def _make_ctx(user_id="test_user", nick_name="Test User"):
    return RequestContext(user_id=user_id, nick_name=nick_name)


@pytest.fixture
def mock_bot_service():
    svc = MagicMock()
    svc.get_bot.return_value = BOT_SAMPLE
    svc.list_bots_by_owner.return_value = {"total": 1, "items": [BOT_SAMPLE]}
    svc.list_bots.return_value = {"total": 1, "items": [BOT_SAMPLE]}
    svc.create_bot.return_value = BOT_SAMPLE
    svc.check_create_bot_preflight.return_value = None
    svc.update_bot.return_value = BOT_SAMPLE
    svc.delete_bot.return_value = None
    svc.restart_bot.return_value = BOT_SAMPLE
    svc.restart_bot_async = AsyncMock(return_value=BOT_SAMPLE)
    svc.check_bot_name_exists.return_value = False
    svc.get_engine_paths.return_value = {"openclaw": "/some/path"}
    svc.get_bot_work_path.return_value = "/some/path"
    svc.get_bot_config_path.return_value = "/some/config"
    svc.update_bot_ext.return_value = None
    svc.release_bot_for_others.return_value = {"message": "released"}
    svc.list_domain_bots.return_value = {"total": 1, "items": [BOT_SAMPLE]}
    svc.list_coding_bots_by_architect.return_value = [BOT_SAMPLE]
    return svc


@pytest.fixture
def mock_passport():
    p = MagicMock()
    p.apply_first_agent_passport.return_value = {
        "token": "tok123",
        "agent_code": "agent-test",
    }
    p.apply_agent_passport.return_value = {
        "token": "tok123",
        "agent_code": "agent-test",
    }
    p.query_auth_status.return_value = {"status": "ISSUED", "token": "tok123"}
    p.query_agent_passport.return_value = {"agent_code": "agent-test"}
    p.update_passport.return_value = None
    return p


@pytest.fixture
def client(mock_bot_service, mock_passport):
    """Build a minimal FastAPI app with the bot_management router under test."""
    from agentclaw.community.adapters.http.bot_management.router import router
    import agentclaw.community.adapters.http.bot_management.router as router_module
    from agentclaw.community.core.errors import DomainError, Forbidden, Unauthorized
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_operator] = lambda: MagicMock(staffId="test_user")

    # Mirror api/app.py: surface DomainError subclasses as HTTP codes so
    # tests can assert 401 / 403 rather than receiving an unhandled 500.
    _STATUS = {Unauthorized: 401, Forbidden: 403}

    @app.exception_handler(DomainError)
    async def _handle(_request, exc: DomainError):
        return JSONResponse(
            status_code=_STATUS.get(type(exc), 500),
            content={"detail": exc.detail},
        )

    # Override RequestContext dependency
    app.dependency_overrides[get_request_context] = lambda: _make_ctx()
    mock_auth = MagicMock()
    # Default: passthrough — matches LocalAuth.authorize_entity_access.
    # Tests that need stricter (prod) behaviour override this.
    mock_auth.authorize_entity_access = AsyncMock(
        side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
            requested_entity_id,
            requested_entity_type,
        )
    )
    repair_service = MagicMock()
    create_for_others_service = MagicMock()
    app.state.default_bot_passport_repair_service = repair_service
    app.state.create_bot_for_others_service = create_for_others_service
    attach_injector(
        app,
        Injector(
            [
                _bind_bot_service(
                    mock_bot_service,
                    bot_repo=MagicMock(),
                    passport=mock_passport,
                    auth=mock_auth,
                    auth_rel=MagicMock(),
                    skill_set_factory=_stub_skill_set_factory(),
                    create_bot_for_others_service=create_for_others_service,
                    default_bot_passport_repair_service=repair_service,
                )
            ]
        ),
    )

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        # Tests that need to drive auth behaviour read ``tc.app.state.mock_auth``.
        app.state.mock_auth = mock_auth
        yield TestClient(app), mock_bot_service, mock_passport


@pytest.fixture
def admin_client(mock_bot_service, mock_passport):
    """Same as client but authenticated as an admin super-user."""
    from agentclaw.community.adapters.http.bot_management.router import router
    import agentclaw.community.adapters.http.bot_management.router as router_module

    admin_id = "100000"  # seeded super_admin in application-test.yaml

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: _make_ctx(user_id=admin_id)
    mock_repo = MagicMock()
    mock_repo.exists_by_owner_and_bot_id.return_value = False
    repair_service = MagicMock()
    create_for_others_service = MagicMock()
    create_for_others_service.execute.return_value = {
        "target_user_id": "u1",
        "bot_id": "default",
        "action": "created",
        "bot": BOT_SAMPLE,
        "passport": {
            "status": "ISSUED",
            "agent_code": "agent-u1",
            "token_present": True,
            "source": "applied",
        },
        "runtime": {"restart_required": False},
    }
    app.state.default_bot_passport_repair_service = repair_service
    app.state.create_bot_for_others_service = create_for_others_service
    attach_injector(
        app,
        Injector(
            [
                _bind_bot_service(
                    mock_bot_service,
                    bot_repo=mock_repo,
                    passport=mock_passport,
                    auth=MagicMock(),
                    auth_rel=MagicMock(),
                    skill_set_factory=_stub_skill_set_factory(),
                    create_bot_for_others_service=create_for_others_service,
                    default_bot_passport_repair_service=repair_service,
                )
            ]
        ),
    )

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        yield TestClient(app), mock_bot_service, mock_passport, mock_repo


@pytest.fixture
def admin_update_client(mock_bot_service, mock_passport):
    """Client whose require_operator dependency is overridden, so admin_update_bot
    is reachable without buser cookie resolution — lets coverage trace the
    admin rename-validation path (the endpoint_test framework runs out-of-process
    and isn't traced)."""
    from agentclaw.community.adapters.http.bot_management.router import router
    import agentclaw.community.adapters.http.bot_management.router as router_module
    from agentclaw.community.adapters.http.auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: _make_ctx()
    app.dependency_overrides[require_operator] = lambda: MagicMock(staffId="admin001")
    attach_injector(
        app,
        Injector(
            [
                _bind_bot_service(
                    mock_bot_service,
                    bot_repo=MagicMock(),
                    passport=mock_passport,
                    auth=MagicMock(),
                    auth_rel=MagicMock(),
                    skill_set_factory=_stub_skill_set_factory(),
                )
            ]
        ),
    )

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        yield TestClient(app), mock_bot_service


@pytest.fixture
def whitelist_client(mock_bot_service, mock_passport):
    """Client with PolicyService mock for whitelist tests."""
    from agentclaw.community.adapters.http.bot_management.router import router
    import agentclaw.community.adapters.http.bot_management.router as router_module
    from agentclaw.community.adapters.http.auth.dependencies import require_operator

    mock_policy = MagicMock()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: _make_ctx()
    app.dependency_overrides[require_operator] = lambda: MagicMock(staffId="admin001")
    attach_injector(
        app,
        Injector(
            [
                _bind_bot_service(
                    mock_bot_service,
                    bot_repo=MagicMock(),
                    passport=mock_passport,
                    auth=MagicMock(),
                    auth_rel=MagicMock(),
                    skill_set_factory=_stub_skill_set_factory(),
                    policy_service=mock_policy,
                )
            ]
        ),
    )

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        yield TestClient(app), mock_bot_service, mock_policy


def _authenticated_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id="caller-id",
        staffId="caller",
        operatorName="caller",
        nickName="Caller",
    )


def _engine_config_app(mock_bot_service, mock_svc):
    """An app with the bot-management router + a mocked EngineConfigService bound."""
    from agentclaw.community.adapters.http.bot_management.router import router
    from agentclaw.community.api.engine_config_service import (
        EngineConfigServiceProtocol,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: _make_ctx()

    class _Extra(Module):
        def configure(self, binder):
            binder.bind(EngineConfigServiceProtocol, to=mock_svc)

    attach_injector(
        app,
        Injector(
            [
                _bind_bot_service(
                    mock_bot_service, bot_repo=MagicMock(), auth=MagicMock()
                ),
                _Extra(),
            ]
        ),
    )
    return TestClient(app)


@pytest.fixture
def space_admin_client(mock_bot_service, mock_passport):
    """Admin-authenticated client with a mock BotSpaceService bound."""
    from agentclaw.community.adapters.http.bot_management.router import router

    bot_space_service = MagicMock()
    admin_id = "100000"  # seeded super_admin in application-test.yaml

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: _make_ctx(user_id=admin_id)
    app.state.default_bot_passport_repair_service = MagicMock()
    app.state.create_bot_for_others_service = MagicMock()
    attach_injector(
        app,
        Injector(
            [
                _bind_bot_service(
                    mock_bot_service,
                    bot_repo=MagicMock(),
                    passport=mock_passport,
                    auth=MagicMock(),
                    auth_rel=MagicMock(),
                    skill_set_factory=_stub_skill_set_factory(),
                    bot_space_service=bot_space_service,
                )
            ]
        ),
    )
    yield TestClient(app), bot_space_service


def _assignment(changed):
    """A minimal BotSpaceAssignmentResult stand-in for the duck-typed route."""
    assignment = MagicMock()
    assignment.changed = changed
    assignment.space.id = 123
    return assignment
