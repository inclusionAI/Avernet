"""Tests for bot_management API router.

Uses a minimal FastAPI test app — never imports agentclaw.servers.web.app.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
    BotInvalidLifecycleStateError,
    BotNotFoundError,
    BotOperationNotAllowedError,
    BotPermissionError,
    DeviceAllocationError,
    BotNameExistsError,
    BotNameInvalidError,
    BotLimitExceededError,
    DefaultBotTeclawNotAllowedError,
    DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
    DeviceLimitError,
)
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
):
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.api.bot_service import BotServiceProtocol
    from agentclaw.community.api.default_bot_passport_repair_service import (
        DefaultBotPassportRepairServiceProtocol,
    )
    from agentclaw.community.api.create_bot_for_others_service import (
        CreateBotForOthersServiceProtocol,
    )

    class _M(Module):
        def configure(self, binder):
            binder.bind(BotService, to=svc)
            binder.bind(BotServiceProtocol, to=svc)
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    svc.check_bot_name_exists.return_value = False
    svc.switch_engine.return_value = BOT_SAMPLE
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
    p.apply_first_agent_passport.return_value = {"token": "tok123"}
    p.apply_agent_passport.return_value = {"token": "tok123"}
    p.query_auth_status.return_value = {"status": "ISSUED", "token": "tok123"}
    p.query_agent_passport.return_value = {"status": "ISSUED", "token": "tok123"}
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
            requested_entity_id, requested_entity_type,
        )
    )
    repair_service = MagicMock()
    create_for_others_service = MagicMock()
    app.state.default_bot_passport_repair_service = repair_service
    app.state.create_bot_for_others_service = create_for_others_service
    attach_injector(app, Injector([_bind_bot_service(
        mock_bot_service,
        bot_repo=MagicMock(),
        passport=mock_passport,
        auth=mock_auth,
        auth_rel=MagicMock(),
        skill_set_factory=_stub_skill_set_factory(),
        create_bot_for_others_service=create_for_others_service,
        default_bot_passport_repair_service=repair_service,
    )]))

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
    attach_injector(app, Injector([_bind_bot_service(
        mock_bot_service,
        bot_repo=mock_repo,
        passport=mock_passport,
        auth=MagicMock(),
        auth_rel=MagicMock(),
        skill_set_factory=_stub_skill_set_factory(),
        create_bot_for_others_service=create_for_others_service,
        default_bot_passport_repair_service=repair_service,
    )]))

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        yield TestClient(app), mock_bot_service, mock_passport, mock_repo


# ---------------------------------------------------------------------------
# GET /api/bots  (list_bots)
# ---------------------------------------------------------------------------

class TestListBots:
    """Auth policy now lives on AuthPlugin.authorize_entity_access (Rule 14).
    Local impl is passthrough; prod impl raises Unauthorized/Forbidden.
    Tests drive that contract by replacing the mock_auth's
    ``authorize_entity_access`` coroutine."""

    @staticmethod
    def _passthrough(tc):
        # Default fixture already sets passthrough; this is for clarity.
        tc.app.state.mock_auth.authorize_entity_access = AsyncMock(
            side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
                requested_entity_id, requested_entity_type,
            )
        )

    @staticmethod
    def _force_self(tc, staff_id="test_user"):
        """Prod-style: defaults missing entity to self, rejects mismatches."""
        from agentclaw.community.core.errors import Forbidden, Unauthorized  # noqa: F401

        async def _auth(ctx, requested_entity_id, requested_entity_type):
            if requested_entity_id and requested_entity_id != staff_id:
                from agentclaw.community.core.errors import Forbidden
                raise Forbidden("无权查询其他用户的Bot列表")
            return staff_id, requested_entity_type or "staff"

        tc.app.state.mock_auth.authorize_entity_access = AsyncMock(side_effect=_auth)

    @staticmethod
    def _unauthorized(tc):
        from agentclaw.community.core.errors import Unauthorized

        async def _raise(*_args, **_kwargs):
            raise Unauthorized("Authentication required")

        tc.app.state.mock_auth.authorize_entity_access = AsyncMock(side_effect=_raise)

    def test_local_mode_no_auth(self, client):
        """Local mode: AuthPlugin passthrough — list_bots gets the
        request params verbatim (no defaulting)."""
        tc, svc, _ = client
        resp = tc.get("/api/bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total"] == 1

    def test_prod_mode_no_params_defaults_to_self(self, client):
        """Prod policy: no entity_id → defaults to current user's bots."""
        tc, svc, _ = client
        self._force_self(tc, staff_id="test_user")
        resp = tc.get("/api/bots", cookies={"session": "fake"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        svc.list_bots.assert_called_once_with(
            entity_id="test_user", entity_type="staff", page=1, page_size=20
        )

    def test_prod_mode_own_entity_id_allowed(self, client):
        """Prod policy: querying own entity_id is allowed."""
        tc, svc, _ = client
        self._force_self(tc, staff_id="test_user")
        resp = tc.get(
            "/api/bots?entity_id=test_user&entity_type=staff",
            cookies={"session": "fake"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_prod_mode_other_entity_id_forbidden(self, client):
        """Prod policy: querying another user's entity_id raises Forbidden → 403."""
        tc, svc, _ = client
        self._force_self(tc, staff_id="test_user")
        resp = tc.get(
            "/api/bots?entity_id=other_user&entity_type=staff",
            cookies={"session": "fake"},
        )
        assert resp.status_code == 403

    def test_prod_mode_no_cookies_returns_401(self, client):
        """Prod policy: missing auth raises Unauthorized → 401."""
        tc, _svc, _ = client
        self._unauthorized(tc)
        resp = tc.get("/api/bots")
        assert resp.status_code == 401

    def test_prod_mode_auth_failure(self, client):
        """Prod policy: any auth-plugin error becomes Unauthorized → 401."""
        tc, _svc, _ = client
        self._unauthorized(tc)
        resp = tc.get("/api/bots", cookies={"session": "fake"})
        assert resp.status_code == 401

    def test_prod_mode_auth_returns_no_user(self, client):
        """Prod policy: auth resolves to no user → Unauthorized → 401."""
        tc, _svc, _ = client
        self._unauthorized(tc)
        resp = tc.get("/api/bots", cookies={"session": "fake"})
        assert resp.status_code == 401

    def test_service_error(self, client):
        """Downstream BotService failure surfaces as 500 in ApiResponse."""
        tc, svc, _ = client
        svc.list_bots.side_effect = RuntimeError("db down")
        resp = tc.get("/api/bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


# ---------------------------------------------------------------------------
# GET /api/bots/check/name
# ---------------------------------------------------------------------------

class TestCheckBotName:
    def test_name_available(self, client):
        tc, svc, _ = client
        svc.check_bot_name_exists.return_value = False
        resp = tc.get("/api/bots/check/name?bot_name=NewBot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["exists"] is False

    def test_name_taken(self, client):
        tc, svc, _ = client
        svc.check_bot_name_exists.return_value = True
        resp = tc.get("/api/bots/check/name?bot_name=ExistingBot")
        assert resp.status_code == 200
        assert resp.json()["data"]["exists"] is True

    def test_empty_name_returns_400(self, client):
        tc, svc, _ = client
        resp = tc.get("/api/bots/check/name?bot_name=")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400


# ---------------------------------------------------------------------------
# GET /api/bots/by-owner
# ---------------------------------------------------------------------------

class TestListBotsByOwner:
    def test_success_returns_items(self, client):
        tc, svc, _ = client
        svc.list_bots_by_owner.return_value = {"total": 1, "items": [BOT_SAMPLE]}
        resp = tc.get("/api/bots/by-owner")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total"] == 1

    def test_empty_list(self, client):
        tc, svc, _ = client
        svc.list_bots_by_owner.return_value = {"total": 0, "items": []}
        resp = tc.get("/api/bots/by-owner")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] == 0
        assert data["data"]["default_bot"] is None

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.list_bots_by_owner.side_effect = RuntimeError("error")
        resp = tc.get("/api/bots/by-owner")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# GET /api/bots/{bot_id}
# ---------------------------------------------------------------------------

class TestGetBot:
    def test_success(self, client):
        tc, svc, _ = client
        resp = tc.get("/api/bots/default")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.get_bot.side_effect = BotNotFoundError("not found")
        resp = tc.get("/api/bots/missing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_anonymous_user_rejected(self, client):
        tc, svc, _ = client
        # Override context mid-test via a fresh app
        from agentclaw.community.adapters.http.bot_management.router import router
        anon_app = FastAPI()
        anon_app.include_router(router)
        anon_app.dependency_overrides[get_request_context] = lambda: _make_ctx(user_id="anonymous")
        attach_injector(anon_app, Injector([_bind_bot_service(
            svc,
            passport=MagicMock(),
        )]))
        tc2 = TestClient(anon_app)
        resp = tc2.get("/api/bots/default")
        assert resp.json()["error_code"] == 400


# ---------------------------------------------------------------------------
# GET /api/bots/{bot_id}/status
# ---------------------------------------------------------------------------

class TestGetBotStatus:
    def test_active_bot(self, client):
        tc, svc, _ = client
        resp = tc.get("/api/bots/default/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["bot_status"] == "ACTIVE"
        assert data["data"]["is_ready"] is True

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.get_bot.side_effect = BotNotFoundError("nope")
        resp = tc.get("/api/bots/missing/status")
        assert resp.json()["error_code"] == 404

    def test_pending_bot(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {**BOT_SAMPLE, "status": "PENDING", "device_binding": {"status": "PENDING"}}
        resp = tc.get("/api/bots/default/status")
        data = resp.json()
        assert data["data"]["is_ready"] is False

    # —— aicoding 应用 bot：必须 ext.start_status==SUCCEEDED 才算 ready ——
    # finalize.sh Step 5.4 同步阻塞等 .repos/_meta.json 出现，
    # marker 写 SUCCEEDED 后 starting_watchdog 才上报 status=SUCCEEDED，
    # 所以 start_status 是 ".repos/ 已 clone 完" 的代理信号。
    def test_aicoding_app_bot_repos_not_ready(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "aicoding",
            "template_type": "applicationCoding",
            # start_status 缺失 / 仍在 STARTING：仓库还没 clone 完
            "ext": {},
        }
        resp = tc.get("/api/bots/default/status")
        data = resp.json()
        assert data["data"]["is_ready"] is False

    def test_aicoding_app_bot_repos_ready(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "aicoding",
            "template_type": "applicationCoding",
            "ext": {"start_status": "SUCCEEDED"},
        }
        resp = tc.get("/api/bots/default/status")
        data = resp.json()
        assert data["data"]["is_ready"] is True

    def test_status_application_coding_clone_failure_still_surfaces(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "aicoding",
            "template_type": "applicationCoding",
            "ext": {
                "start_status": "FAILED",
                "start_message": "clone failed: timeout",
            },
        }
        resp = tc.get("/api/bots/default/status")
        data = resp.json()
        assert data["data"]["is_ready"] is False
        assert data["data"]["error_message"] == "clone failed: timeout"

    def test_status_active_baas_hides_stale_baas_publish_failure_ext(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "aicoding",
            "template_type": "applicationCoding",
            "device_binding": {
                "status": "ACTIVE",
                "device_id": "BOT-uuid",
                "device_provider": "baas",
            },
            "ext": {
                "start_status": "FAILED",
                "start_message": "BaaS publish FAILED: publish_id=10377",
                "other": "kept",
            },
        }

        resp = tc.get("/api/bots/default/status")

        data = resp.json()["data"]
        assert data["bot_status"] == "ACTIVE"
        assert data["binding_status"] == "ACTIVE"
        assert data["is_ready"] is True
        assert data["error_message"] is None
        assert data["ext"] == {"other": "kept"}

    # claude_code + applicationCoding 在创建链路里被路由成 aicoding 引擎，
    # 但 ac_bots.active_engine 写库时保留 claude_code。两种取值都要覆盖。
    def test_claude_code_app_bot_repos_gating(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
            "ext": {},
        }
        resp = tc.get("/api/bots/default/status")
        assert resp.json()["data"]["is_ready"] is False

    # 非应用 bot：start_status 不参与判定，保持旧行为。
    def test_non_app_bot_unaffected(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "openclaw",
            "template_type": None,
            "ext": {},  # 无 start_status 也照样 ready
        }
        resp = tc.get("/api/bots/default/status")
        assert resp.json()["data"]["is_ready"] is True

    def test_personal_coding_bot_unaffected(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "claude_code",
            "template_type": "personalCoding",
            "ext": {},
        }
        resp = tc.get("/api/bots/default/status")
        assert resp.json()["data"]["is_ready"] is True


# ---------------------------------------------------------------------------
# PUT /api/bots/{bot_id}  (update_bot)
# ---------------------------------------------------------------------------

class TestUpdateBot:
    def test_success(self, client):
        tc, svc, passport = client
        resp = tc.put("/api/bots/default", json={"bot_name": "NewName"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        passport.update_passport.assert_called_once()
        kwargs = passport.update_passport.call_args.kwargs
        assert "mcp_codes" not in kwargs
        assert "cli_items" not in kwargs

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.update_bot.side_effect = BotNotFoundError("nope")
        resp = tc.put("/api/bots/missing", json={"bot_name": "x"})
        assert resp.json()["error_code"] == 404

    def test_name_conflict(self, client):
        tc, svc, _ = client
        svc.update_bot.side_effect = BotNameExistsError("exists")
        resp = tc.put("/api/bots/default", json={"bot_name": "Taken"})
        assert resp.json()["error_code"] == 409

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.update_bot.side_effect = BotServiceError("failure")
        resp = tc.put("/api/bots/default", json={"bot_name": "x"})
        assert resp.json()["error_code"] == 500

    def test_invalid_name_special_char_rejected(self, client):
        # 改名漏校验修复：特殊字符必须被 422/400 拒，不落库、不调 service。
        tc, svc, _ = client
        resp = tc.put("/api/bots/default", json={"bot_name": "bad@name#bot"})
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "特殊字符" in body["message"]
        svc.update_bot.assert_not_called()

    def test_invalid_name_empty_rejected(self, client):
        # strip 后为空 → 400，且不调 service。
        tc, svc, _ = client
        resp = tc.put("/api/bots/default", json={"bot_name": "   "})
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "不能为空" in body["message"]
        svc.update_bot.assert_not_called()

    def test_legacy_name_empty_rejected(self, client):
        # 兼容旧字段 name，不能静默忽略空名称并返回成功。
        tc, svc, _ = client
        resp = tc.put("/api/bots/default", json={"name": ""})
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "不能为空" in body["message"]
        svc.update_bot.assert_not_called()

    def test_invalid_name_too_long_rejected(self, client):
        tc, svc, _ = client
        resp = tc.put("/api/bots/default", json={"bot_name": "x" * 33})
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "32" in body["message"]
        svc.update_bot.assert_not_called()

    def test_no_bot_name_skips_validation(self, client):
        # 只改 desc 等、不传 bot_name → 校验跳过，正常放行调 service。
        tc, svc, _ = client
        resp = tc.put("/api/bots/default", json={"bot_desc": "new desc"})
        body = resp.json()
        assert body["success"] is True
        svc.update_bot.assert_called_once()
        assert svc.update_bot.call_args.kwargs.get("bot_name") is None

    def test_valid_name_strips_and_passes(self, client):
        # 合法名首尾空格被 strip 后透传给 service。
        tc, svc, _ = client
        resp = tc.put("/api/bots/default", json={"bot_name": "  新名-1  "})
        assert resp.json()["success"] is True
        svc.update_bot.assert_called_once()
        assert svc.update_bot.call_args.kwargs.get("bot_name") == "新名-1"


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
    attach_injector(app, Injector([_bind_bot_service(
        mock_bot_service,
        bot_repo=MagicMock(),
        passport=mock_passport,
        auth=MagicMock(),
        auth_rel=MagicMock(),
        skill_set_factory=_stub_skill_set_factory(),
    )]))

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        yield TestClient(app), mock_bot_service


class TestAdminUpdateBotNameValidation:
    def test_invalid_name_special_char_rejected(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put("/api/bots/default/admin", json={
            "owner_id": "u_owner",
            "bot_name": "bad@name#bot",
        })
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "特殊字符" in body["message"]
        svc.admin_update_bot.assert_not_called()

    def test_invalid_name_empty_rejected(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put("/api/bots/default/admin", json={
            "owner_id": "u_owner",
            "bot_name": "   ",
        })
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "不能为空" in body["message"]
        svc.admin_update_bot.assert_not_called()

    def test_no_bot_name_skips_validation(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put("/api/bots/default/admin", json={
            "owner_id": "u_owner",
            "bot_desc": "desc only",
        })
        body = resp.json()
        assert body["success"] is True
        svc.admin_update_bot.assert_called_once()
        assert svc.admin_update_bot.call_args.kwargs.get("bot_name") is None

    def test_valid_name_passes(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put("/api/bots/default/admin", json={
            "owner_id": "u_owner",
            "bot_name": "合规名称-1",
        })
        assert resp.json()["success"] is True
        svc.admin_update_bot.assert_called_once()
        assert svc.admin_update_bot.call_args.kwargs.get("bot_name") == "合规名称-1"


# ---------------------------------------------------------------------------
# DELETE /api/bots/{bot_id}
# ---------------------------------------------------------------------------

class TestDeleteBot:
    def test_success(self, client):
        tc, svc, _ = client
        resp = tc.delete("/api/bots/default")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.delete_bot.side_effect = BotNotFoundError("nope")
        resp = tc.delete("/api/bots/missing")
        assert resp.json()["error_code"] == 404

    def test_passport_error(self, client):
        tc, svc, _ = client
        svc.delete_bot.side_effect = PassportError("auth fail")
        resp = tc.delete("/api/bots/default")
        assert resp.json()["error_code"] == 500

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.delete_bot.side_effect = BotServiceError("fail")
        resp = tc.delete("/api/bots/default")
        assert resp.json()["error_code"] == 500


# ---------------------------------------------------------------------------
# POST /api/bots/{bot_id}/restart
# ---------------------------------------------------------------------------

class TestRestartBot:
    def test_success(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/default/restart")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.restart_bot.side_effect = BotNotFoundError("nope")
        resp = tc.post("/api/bots/missing/restart")
        assert resp.json()["error_code"] == 404

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.restart_bot.side_effect = BotServiceError("fail")
        resp = tc.post("/api/bots/default/restart")
        assert resp.json()["error_code"] == 500

    def test_recycled_bot_returns_conflict(self, client):
        tc, svc, _ = client
        svc.restart_bot.side_effect = BotInvalidLifecycleStateError(
            bot_id="default",
            current_status="RECYCLED",
        )

        resp = tc.post("/api/bots/default/restart")

        assert resp.status_code == 409
        assert resp.json()["success"] is False
        assert resp.json()["error_code"] == 409

    def test_activation_in_progress_returns_accepted(self, client):
        tc, svc, _ = client
        svc.restart_bot.return_value = {
            **BOT_SAMPLE,
            "status": "REACTIVATING",
            "restart_in_progress": True,
            "message": "Bot activation is in progress",
        }

        resp = tc.post("/api/bots/default/restart")

        assert resp.status_code == 202
        assert resp.json()["success"] is True
        assert resp.json()["data"]["restart_in_progress"] is True


# ---------------------------------------------------------------------------
# POST /api/bots/switch-engine
# ---------------------------------------------------------------------------

class TestSwitchEngine:
    def test_success(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/switch-engine", json={"bot_id": "default", "engine_type": "openclaw"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_missing_bot_id(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/switch-engine", json={"engine_type": "openclaw"})
        assert resp.json()["error_code"] == 400

    def test_missing_engine_type(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/switch-engine", json={"bot_id": "default"})
        assert resp.json()["error_code"] == 400

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.switch_engine.side_effect = BotNotFoundError("nope")
        resp = tc.post("/api/bots/switch-engine", json={"bot_id": "missing", "engine_type": "openclaw"})
        assert resp.json()["error_code"] == 404

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.switch_engine.side_effect = BotServiceError("fail")
        resp = tc.post("/api/bots/switch-engine", json={"bot_id": "default", "engine_type": "openclaw"})
        assert resp.json()["error_code"] == 400

    def test_default_teclaw_error_preserves_business_message(self, client):
        tc, svc, _ = client
        svc.switch_engine.side_effect = DefaultBotTeclawNotAllowedError()

        resp = tc.post(
            "/api/bots/switch-engine",
            json={"bot_id": "default", "engine_type": "teclaw"},
        )

        assert resp.json() == {
            "success": False,
            "message": DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
            "error_code": 400,
            "data": None,
        }


# ---------------------------------------------------------------------------
# POST /api/bots/restart-scheduler
# ---------------------------------------------------------------------------

class TestRestartScheduler:
    def test_success(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/restart-scheduler", json={"user_id": "test_user", "bot_id": "default"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.restart_bot.side_effect = BotNotFoundError("nope")
        resp = tc.post("/api/bots/restart-scheduler", json={"user_id": "test_user", "bot_id": "missing"})
        assert resp.json()["error_code"] == 404

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.restart_bot.side_effect = BotServiceError("fail")
        resp = tc.post("/api/bots/restart-scheduler", json={"user_id": "test_user", "bot_id": "default"})
        assert resp.json()["error_code"] == 500

    def test_recycled_bot_returns_conflict(self, client):
        tc, svc, _ = client
        svc.restart_bot.side_effect = BotInvalidLifecycleStateError(
            bot_id="default",
            current_status="RECYCLED",
        )

        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )

        assert resp.status_code == 409
        assert resp.json()["error_code"] == 409

    def test_activation_in_progress_returns_accepted(self, client):
        tc, svc, _ = client
        svc.restart_bot.return_value = {
            **BOT_SAMPLE,
            "status": "PENDING",
            "restart_in_progress": True,
        }

        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )

        assert resp.status_code == 202
        assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# POST /api/bots/release-for-others  (admin only)
# ---------------------------------------------------------------------------

class TestReleaseForOthers:
    def test_permission_denied_for_non_admin(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/release-for-others", json={"target_user_id": "u1", "target_bot_id": "b1"})
        assert resp.json()["error_code"] == 403

    def test_success_as_admin(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/release-for-others", json={"target_user_id": "u1", "target_bot_id": "default"})
        assert resp.json()["success"] is True

    def test_missing_target_user_id(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/release-for-others", json={"target_bot_id": "default"})
        assert resp.json()["error_code"] == 400

    def test_missing_target_bot_id(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/release-for-others", json={"target_user_id": "u1"})
        assert resp.json()["error_code"] == 400

    def test_bot_not_found(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.release_bot_for_others.side_effect = BotNotFoundError("nope")
        resp = tc.post("/api/bots/release-for-others", json={"target_user_id": "u1", "target_bot_id": "b1"})
        assert resp.json()["error_code"] == 404

    def test_service_error(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.release_bot_for_others.side_effect = BotServiceError("fail")
        resp = tc.post("/api/bots/release-for-others", json={"target_user_id": "u1", "target_bot_id": "b1"})
        assert resp.json()["error_code"] == 500


# ---------------------------------------------------------------------------
# POST /api/bots/restart-for-others  (admin only)
# ---------------------------------------------------------------------------

class TestRestartForOthers:
    def test_permission_denied(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/restart-for-others", json={"target_user_id": "u1", "target_bot_id": "b1"})
        assert resp.json()["error_code"] == 403

    def test_success_as_admin(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/restart-for-others", json={"target_user_id": "u1", "target_bot_id": "default"})
        assert resp.json()["success"] is True

    def test_rejects_teclaw_bot(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot.side_effect = BotOperationNotAllowedError("teclaw 类型的 Bot 不支持重启")

        resp = tc.post("/api/bots/restart-for-others", json={"target_user_id": "u1", "target_bot_id": "default"})

        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert data["message"] == "teclaw 类型的 Bot 不支持重启"
        svc.restart_bot.assert_called_once()

    def test_missing_target_user(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/restart-for-others", json={"target_bot_id": "default"})
        assert resp.json()["error_code"] == 400

    def test_bot_not_found(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot.side_effect = BotNotFoundError("nope")
        resp = tc.post("/api/bots/restart-for-others", json={"target_user_id": "u1", "target_bot_id": "missing"})
        assert resp.json()["error_code"] == 404

    def test_recycled_bot_returns_conflict(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot.side_effect = BotInvalidLifecycleStateError(
            bot_id="default",
            current_status="RECYCLED",
        )

        resp = tc.post(
            "/api/bots/restart-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default"},
        )

        assert resp.status_code == 409
        assert resp.json()["error_code"] == 409

    def test_activation_in_progress_returns_accepted(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot.return_value = {
            **BOT_SAMPLE,
            "status": "REACTIVATING",
            "restart_in_progress": True,
        }

        resp = tc.post(
            "/api/bots/restart-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default"},
        )

        assert resp.status_code == 202
        assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# POST /api/bots/create-for-others  (admin only)
# ---------------------------------------------------------------------------

class TestCreateForOthers:
    def test_permission_denied(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/create-for-others", json={"target_user_id": "u1", "target_nick_name": "Alice"})
        assert resp.json()["error_code"] == 403

    def test_missing_nick_name(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/create-for-others", json={"target_user_id": "u1"})
        assert resp.json()["error_code"] == 400

    def test_creates_bot_when_no_default_exists(self, admin_client):
        tc, svc, _, mock_repo = admin_client
        resp = tc.post(
            "/api/bots/create-for-others",
            headers={"cookie": "session-cookie"},
            json={
                "target_user_id": " u1 ",
                "target_nick_name": " Alice ",
                "bot_type": "personal",
            },
        )
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["action"] == "created"
        tc.app.state.create_bot_for_others_service.execute.assert_called_once_with(
            target_user_id="u1",
            target_nick_name="Alice",
            bot_type="personal",
            operator_user_id="100000",
            operator_name="Test User",
            cookie="session-cookie",
        )

    def test_skips_if_active_default_bot_exists(self, admin_client):
        tc, svc, _, mock_repo = admin_client
        tc.app.state.create_bot_for_others_service.execute.return_value = {
            "target_user_id": "u1",
            "bot_id": "default",
            "status": "ACTIVE",
            "action": "skipped",
            "passport": {
                "status": "ISSUED",
                "agent_code": "agent-u1",
                "token_present": True,
                "source": "existing",
            },
            "runtime": {"restart_required": False},
        }
        resp = tc.post("/api/bots/create-for-others", json={"target_user_id": "u1", "target_nick_name": "Alice"})
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["action"] == "skipped"

    def test_device_limit_error(self, admin_client):
        tc, svc, _, mock_repo = admin_client
        tc.app.state.create_bot_for_others_service.execute.side_effect = DeviceLimitError("limit")
        resp = tc.post("/api/bots/create-for-others", json={"target_user_id": "u1", "target_nick_name": "Alice"})
        assert resp.json()["error_code"] == 429

    def test_bot_limit_error(self, admin_client):
        tc, _, _, _ = admin_client
        tc.app.state.create_bot_for_others_service.execute.side_effect = (
            BotLimitExceededError("limit")
        )
        resp = tc.post(
            "/api/bots/create-for-others",
            json={"target_user_id": "u1", "target_nick_name": "Alice"},
        )
        assert resp.json()["error_code"] == 429

    def test_device_allocation_error(self, admin_client):
        tc, svc, _, mock_repo = admin_client
        tc.app.state.create_bot_for_others_service.execute.side_effect = DeviceAllocationError("alloc fail")
        resp = tc.post("/api/bots/create-for-others", json={"target_user_id": "u1", "target_nick_name": "Alice"})
        assert resp.json()["error_code"] == 500

    def test_passport_preparation_error_preserves_control_plane_error_code(
        self, admin_client
    ):
        from agentclaw.community.core.bot_management.errors import (
            CreateBotForOthersError,
        )

        tc, _, _, _ = admin_client
        tc.app.state.create_bot_for_others_service.execute.side_effect = (
            CreateBotForOthersError(
                "apply_first_agent_passport returned no token",
                error_code=5401,
            )
        )

        resp = tc.post(
            "/api/bots/create-for-others",
            json={"target_user_id": "u1", "target_nick_name": "Alice"},
        )

        assert resp.json() == {
            "success": False,
            "message": "apply_first_agent_passport returned no token",
            "error_code": 5401,
            "data": None,
        }


# ---------------------------------------------------------------------------
# POST /api/bots/repair-default-passport-for-others  (admin only)
# ---------------------------------------------------------------------------

class TestRepairDefaultPassportForOthers:
    def test_permission_denied(self, client):
        tc, _, _ = client
        resp = tc.post(
            "/api/bots/repair-default-passport-for-others",
            json={"target_user_id": "172168", "target_env": "prod"},
        )
        assert resp.json()["error_code"] == 403

    def test_success_forwards_trimmed_target_and_authenticated_operator(
        self, admin_client
    ):
        tc, _, _, _ = admin_client
        repair_service = tc.app.state.default_bot_passport_repair_service
        repair_service.repair.return_value = {
            "target_user_id": "172168",
            "bot_id": "default",
            "target_env": "prod",
            "action": "repaired",
            "runtime": {
                "restart_required": True,
                "restart_environment": "prod",
            },
        }

        resp = tc.post(
            "/api/bots/repair-default-passport-for-others",
            json={"target_user_id": " 172168 ", "target_env": "prod"},
        )

        data = resp.json()
        assert data["success"] is True
        assert data["data"]["bot_id"] == "default"
        assert data["data"]["runtime"]["restart_required"] is True
        repair_service.repair.assert_called_once_with(
            target_user_id="172168",
            target_env="prod",
            operator_user_id="100000",
            operator_name="Test User",
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"target_user_id": "172168"},
            {"target_user_id": "172168", "target_env": "gray"},
            {"target_user_id": " ", "target_env": "prod"},
            {
                "target_user_id": "172168",
                "target_env": "prod",
                "bot_id": "another-bot",
            },
        ],
    )
    def test_invalid_request_returns_400_without_calling_service(
        self, admin_client, payload
    ):
        tc, _, _, _ = admin_client
        repair_service = tc.app.state.default_bot_passport_repair_service

        resp = tc.post(
            "/api/bots/repair-default-passport-for-others", json=payload
        )

        assert resp.json()["error_code"] == 400
        repair_service.repair.assert_not_called()

    def test_maps_typed_service_error(self, admin_client):
        from agentclaw.community.core.bot_management.errors import (
            DefaultBotPassportRepairError,
        )

        tc, _, _, _ = admin_client
        repair_service = tc.app.state.default_bot_passport_repair_service
        repair_service.repair.side_effect = DefaultBotPassportRepairError(
            "owner relationship verification failed", error_code=5402
        )

        resp = tc.post(
            "/api/bots/repair-default-passport-for-others",
            json={"target_user_id": "172168", "target_env": "prod"},
        )

        assert resp.json() == {
            "success": False,
            "message": "owner relationship verification failed",
            "error_code": 5402,
            "data": None,
        }

    def test_operator_name_falls_back_to_authenticated_user_id(self, admin_client):
        tc, _, _, _ = admin_client
        tc.app.dependency_overrides[get_request_context] = lambda: RequestContext(
            user_id="100000", nick_name=None
        )
        repair_service = tc.app.state.default_bot_passport_repair_service
        repair_service.repair.return_value = {
            "target_user_id": "172168",
            "bot_id": "default",
            "target_env": "prod",
        }

        resp = tc.post(
            "/api/bots/repair-default-passport-for-others",
            json={"target_user_id": "172168", "target_env": "prod"},
        )

        assert resp.json()["success"] is True
        assert repair_service.repair.call_args.kwargs["operator_name"] == "100000"


# ---------------------------------------------------------------------------
# POST /api/bots  (create_bot)
# ---------------------------------------------------------------------------

class TestCreateBot:
    def test_needs_authorization_when_no_token(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"iframe_url": "http://auth", "token": None}
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 401
        assert data["data"]["need_authorization"] is True

    def test_token_present_creates_bot_immediately(self, client):
        """When Passport returns a token (no iframe needed), the bot
        is created inline and a success envelope is returned."""
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123", "agent_code": "ac1",
        }
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["bot"] == BOT_SAMPLE
        assert data["data"]["passport"]["token"] == "tok123"
        svc.create_bot.assert_called_once()

    def test_create_filters_local_mcp_codes_before_passport(
        self, mock_bot_service, mock_passport
    ):
        """Passport creation receives only remote MCP codes."""
        from agentclaw.community.adapters.http.bot_management.router import router
        import agentclaw.community.adapters.http.bot_management.router as router_module

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = lambda: _make_ctx()
        mock_auth = MagicMock()
        mock_auth.authorize_entity_access = AsyncMock(
            side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
                requested_entity_id, requested_entity_type,
            )
        )
        attach_injector(app, Injector([_bind_bot_service(
            mock_bot_service,
            bot_repo=MagicMock(),
            passport=mock_passport,
            auth=mock_auth,
            auth_rel=MagicMock(),
            skill_set_factory=_stub_skill_set_factory(["mcp.remote.1", "hitl"]),
        )]))

        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post("/api/bots", json={"bot_name": "NewBot"})

        assert resp.json()["success"] is True
        passport_kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert passport_kwargs["mcp_codes"] == ["mcp.remote.1"]
        # openclaw (default engine) carries no CLI items — fail-closed for non-aicoding
        assert passport_kwargs["cli_items"] == []

    def test_create_passport_carries_default_cli_items_for_aicoding(
        self, mock_bot_service, mock_passport
    ):
        """aicoding engine create path carries the 9 default CLI items.

        Mirrors test_create_filters_local_mcp_codes_before_passport but posts
        engine_type=aicoding, then asserts the passport call receives the full
        default CLI list (verifies router.py wiring end-to-end, not just the
        _defaults getter in isolation).
        """
        from agentclaw.community.adapters.http.bot_management.router import router
        import agentclaw.community.adapters.http.bot_management.router as router_module

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = lambda: _make_ctx()
        mock_auth = MagicMock()
        mock_auth.authorize_entity_access = AsyncMock(
            side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
                requested_entity_id, requested_entity_type,
            )
        )
        attach_injector(app, Injector([_bind_bot_service(
            mock_bot_service,
            bot_repo=MagicMock(),
            passport=mock_passport,
            auth=mock_auth,
            auth_rel=MagicMock(),
            skill_set_factory=_stub_skill_set_factory(["mcp.remote.1"]),
        )]))

        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post(
                "/api/bots", json={"bot_name": "NewBot", "engine_type": "aicoding"}
            )

        assert resp.json()["success"] is True
        passport_kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert passport_kwargs["engine_type"] == "aicoding"
        # MCP codes still passed through independently of CLI items
        assert passport_kwargs["mcp_codes"] == ["mcp.remote.1"]
        cli_codes = [c["cli_code"] for c in passport_kwargs["cli_items"]]
        assert len(cli_codes) == 9
        assert "antcode-cli" in cli_codes
        assert "adev-cli" in cli_codes
        assert "derisk-cli" in cli_codes
        assert "yuque-cli" in cli_codes

    def test_create_passport_carries_default_cli_items_for_claude_code_coding_templates(
        self, mock_bot_service, mock_passport
    ):
        """claude_code engine + personalCoding/applicationCoding templates share
        the aicoding default-CLI link (end-to-end router wiring).

        Mirrors the aicoding case but posts engine_type=claude_code with a
        personalCoding template — verifies the router passes template_type into
        get_default_cli_items and the aicoding CLI link fires.
        """
        from agentclaw.community.adapters.http.bot_management.router import router
        import agentclaw.community.adapters.http.bot_management.router as router_module

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = lambda: _make_ctx()
        mock_auth = MagicMock()
        mock_auth.authorize_entity_access = AsyncMock(
            side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
                requested_entity_id, requested_entity_type,
            )
        )
        attach_injector(app, Injector([_bind_bot_service(
            mock_bot_service,
            bot_repo=MagicMock(),
            passport=mock_passport,
            auth=mock_auth,
            auth_rel=MagicMock(),
            skill_set_factory=_stub_skill_set_factory(["mcp.remote.1"]),
        )]))

        # personalCoding → aicoding CLI link fires.
        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post(
                "/api/bots",
                json={"bot_name": "NewBot", "engine_type": "claude_code",
                      "template_type": "personalCoding"},
            )
        assert resp.json()["success"] is True
        kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert kwargs["engine_type"] == "claude_code"
        cli_codes = [c["cli_code"] for c in kwargs["cli_items"]]
        assert len(cli_codes) == 9
        assert {"antcode-cli", "adev-cli", "derisk-cli", "yuque-cli"} <= set(cli_codes)

        # applicationCoding → 同样走 aicoding CLI 链路。
        mock_passport.apply_first_agent_passport.reset_mock()
        mock_passport.apply_first_agent_passport.return_value = {
            "token": "tok123", "agent_code": "ac1",
        }
        with patch.object(router_module, "generate_bot_id", return_value="default"):
            TestClient(app).post(
                "/api/bots",
                json={"bot_name": "NewBot", "engine_type": "claude_code",
                      "template_type": "applicationCoding"},
            )
        kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert kwargs["engine_type"] == "claude_code"
        assert len(kwargs["cli_items"]) == 9

        # claude_code 不带 template_type / 带 service 等非研发模板 → fail-closed 空。
        mock_passport.apply_first_agent_passport.reset_mock()
        mock_passport.apply_first_agent_passport.return_value = {
            "token": "tok123", "agent_code": "ac1",
        }
        with patch.object(router_module, "generate_bot_id", return_value="default"):
            TestClient(app).post(
                "/api/bots", json={"bot_name": "NewBot", "engine_type": "claude_code"},
            )
        kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert kwargs["engine_type"] == "claude_code"
        assert kwargs["cli_items"] == []

    def test_passport_error(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.side_effect = PassportError("auth fail")
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        assert resp.json()["error_code"] == 5400

    def test_default_teclaw_service_guard_preserves_business_message(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        svc.create_bot.side_effect = DefaultBotTeclawNotAllowedError()

        resp = tc.post(
            "/api/bots",
            json={"bot_name": "NewBot", "engine_type": "teclaw"},
        )

        assert resp.json() == {
            "success": False,
            "message": DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
            "error_code": 400,
            "data": None,
        }

    def test_device_allocation_error(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        svc.create_bot.side_effect = DeviceAllocationError("fail")
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        assert resp.json()["error_code"] == 500

    def test_device_limit_error(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        svc.create_bot.side_effect = DeviceLimitError("limit")
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        assert resp.json()["error_code"] == 429

    # ----- Input validation: rejected at the API boundary -------------------
    # Regression for: 空名、含 @ 字符、超长名应被拒绝；达到 Bot 数量上限应被拒绝。

    def test_empty_bot_name_returns_400(self, client):
        tc, svc, passport = client
        resp = tc.post("/api/bots", json={"bot_name": ""})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        # 不应继续走 passport / create 流程
        passport.apply_first_agent_passport.assert_not_called()
        svc.create_bot.assert_not_called()

    def test_bot_name_with_at_sign_returns_400(self, client):
        tc, svc, passport = client
        resp = tc.post("/api/bots", json={"bot_name": "bad@name"})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        passport.apply_first_agent_passport.assert_not_called()
        svc.create_bot.assert_not_called()

    def test_overlong_bot_name_returns_400(self, client):
        tc, svc, passport = client
        resp = tc.post("/api/bots", json={"bot_name": "a" * 33})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        passport.apply_first_agent_passport.assert_not_called()
        svc.create_bot.assert_not_called()

    def test_default_teclaw_is_rejected_before_passport(self, client):
        tc, svc, passport = client
        svc.check_create_bot_preflight.side_effect = (
            DefaultBotTeclawNotAllowedError()
        )

        resp = tc.post(
            "/api/bots",
            json={"bot_name": "NewBot", "engine_type": "teclaw"},
        )

        assert resp.json() == {
            "success": False,
            "message": DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
            "error_code": 400,
            "data": None,
        }
        svc.check_create_bot_preflight.assert_called_once_with(
            user_id="test_user",
            bot_id="default",
            engine_type="teclaw",
            bot_name="NewBot",
        )
        passport.apply_first_agent_passport.assert_not_called()
        passport.apply_agent_passport.assert_not_called()
        svc.create_bot.assert_not_called()

    def test_bot_count_limit_returns_429(self, client):
        # 数量上限需要在 Passport 前置校验，避免先返回授权 iframe，
        # 用户授权完成后才在 auth-status 阶段失败。
        tc, svc, passport = client
        svc.check_create_bot_preflight.side_effect = BotLimitExceededError("limit")
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 429
        passport.apply_first_agent_passport.assert_not_called()
        passport.apply_agent_passport.assert_not_called()
        svc.create_bot.assert_not_called()

    # ----- Positive regression & boundary cases (A / B / C / D) ------------
    # 防止过度校验把正常请求误杀；覆盖 trim、缺字段、长度边界。

    def test_valid_name_under_limit_succeeds(self, client):
        """A: 合法名 + 未到上限 → 正常走完 passport+create 流程。"""
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        resp = tc.post("/api/bots", json={"bot_name": "My Bot 1"})
        data = resp.json()
        assert data["success"] is True
        passport.apply_first_agent_passport.assert_called_once()
        svc.create_bot.assert_called_once()

    def test_bot_name_missing_is_allowed(self, client):
        """B: 不传 bot_name → 走默认命名规则，不被 400 拦截。"""
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        resp = tc.post("/api/bots", json={})
        data = resp.json()
        assert data["success"] is True
        # service 收到 bot_name=None（沿用默认命名）
        svc.create_bot.assert_called_once()
        kwargs = svc.create_bot.call_args.kwargs
        assert kwargs.get("bot_name") is None

    def test_bot_name_surrounding_whitespace_is_trimmed(self, client):
        """C: 首尾空格被 trim 后再传给 service / passport。"""
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        resp = tc.post("/api/bots", json={"bot_name": "  Bot 1  "})
        assert resp.json()["success"] is True
        # service 收到 trim 后的 "Bot 1"
        kwargs = svc.create_bot.call_args.kwargs
        assert kwargs.get("bot_name") == "Bot 1"
        # passport 也收到 trim 后的值
        passport_kwargs = passport.apply_first_agent_passport.call_args.kwargs
        assert passport_kwargs.get("bot_name") == "Bot 1"

    def test_bot_name_at_32_char_boundary_passes(self, client):
        """D: 32 字符为允许的最长长度，不应被 400 拦截。"""
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {"token": "tok123"}
        name = "a" * 32
        resp = tc.post("/api/bots", json={"bot_name": name})
        data = resp.json()
        assert data["success"] is True
        kwargs = svc.create_bot.call_args.kwargs
        assert kwargs.get("bot_name") == name


# ---------------------------------------------------------------------------
# POST /api/bots/auth-status
# ---------------------------------------------------------------------------

class TestGetAuthStatus:
    def test_missing_bot_id(self, client):
        tc, svc, passport = client
        resp = tc.post("/api/bots/auth-status", json={})
        assert resp.json()["error_code"] == 400

    def test_pending_status(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "PENDING"}
        resp = tc.post("/api/bots/auth-status", json={"bot_id": "bot1"})
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "PENDING"

    def test_issued_status_creates_bot(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "ISSUED", "token": "tok456"}
        resp = tc.post("/api/bots/auth-status", json={"bot_id": "bot1", "bot_name": "NewBot"})
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "ISSUED"
        svc.create_bot.assert_called_once()

    def test_query_returns_none(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = None
        resp = tc.post("/api/bots/auth-status", json={"bot_id": "bot1"})
        assert resp.json()["error_code"] == 500

    def test_passport_error(self, client):
        tc, svc, passport = client
        passport.query_auth_status.side_effect = PassportError("fail")
        resp = tc.post("/api/bots/auth-status", json={"bot_id": "bot1"})
        assert resp.json()["error_code"] == 5400

    def test_unexpected_status(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "REJECTED"}
        resp = tc.post("/api/bots/auth-status", json={"bot_id": "bot1"})
        assert resp.json()["error_code"] == 400

    # ----- Defense-in-depth: service-layer guard surfaces on /auth-status -----
    # /auth-status 不在 router 入口预校验，依赖 service 层 create_bot 兜底；
    # router 必须把对应异常映射成 400 / 429（而不是 500）。

    def test_issued_invalid_bot_name_returns_400(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "ISSUED", "token": "tok"}
        svc.create_bot.side_effect = BotNameInvalidError("bad name")
        resp = tc.post(
            "/api/bots/auth-status",
            json={"bot_id": "bot1", "bot_name": "bad@name"},
        )
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400

    def test_issued_default_teclaw_returns_business_error(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "ISSUED", "token": "tok"}
        svc.create_bot.side_effect = DefaultBotTeclawNotAllowedError()

        resp = tc.post(
            "/api/bots/auth-status",
            json={"bot_id": "default", "engine_type": "teclaw"},
        )

        assert resp.json() == {
            "success": False,
            "message": DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
            "error_code": 400,
            "data": None,
        }

    def test_issued_bot_limit_exceeded_returns_429(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "ISSUED", "token": "tok"}
        svc.create_bot.side_effect = BotLimitExceededError("limit")
        resp = tc.post(
            "/api/bots/auth-status",
            json={"bot_id": "bot1", "bot_name": "NewBot"},
        )
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 429


# ---------------------------------------------------------------------------
# GET /api/bots/{bot_id}/passport
# ---------------------------------------------------------------------------

class TestGetBotPassport:
    def test_success(self, client):
        tc, svc, passport = client
        resp = tc.get("/api/bots/default/passport")
        assert resp.json()["success"] is True

    def test_bot_not_found_returns_none(self, client):
        tc, svc, passport = client
        svc.get_bot.return_value = None
        resp = tc.get("/api/bots/missing/passport")
        assert resp.json()["error_code"] == 404

    def test_passport_not_found(self, client):
        tc, svc, passport = client
        passport.query_agent_passport.return_value = None
        resp = tc.get("/api/bots/default/passport")
        assert resp.json()["error_code"] == 404

    def test_passport_error(self, client):
        tc, svc, passport = client
        passport.query_agent_passport.side_effect = PassportError("fail")
        resp = tc.get("/api/bots/default/passport")
        assert resp.json()["error_code"] == 5400


# ---------------------------------------------------------------------------
# GET /api/bots/{bot_id}/detail-by-owner
# ---------------------------------------------------------------------------

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
    attach_injector(app, Injector([_bind_bot_service(
        mock_bot_service,
        bot_repo=MagicMock(),
        passport=mock_passport,
        auth=MagicMock(),
        auth_rel=MagicMock(),
        skill_set_factory=_stub_skill_set_factory(),
        policy_service=mock_policy,
    )]))

    with patch.object(router_module, "generate_bot_id", return_value="default"):
        yield TestClient(app), mock_bot_service, mock_policy


class TestGetBotDetailByOwner:
    def test_operator_gets_detail_by_owner_without_owner_policy_check(self, whitelist_client):
        tc, svc, policy = whitelist_client
        policy.check.return_value = False
        resp = tc.get("/api/bots/default/detail-by-owner?owner_id=staff001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["bot_id"] == "default"
        policy.check.assert_not_called()
        svc.get_bot.assert_called_once_with("default", "staff001")

    def test_missing_owner_id(self, whitelist_client):
        tc, svc, policy = whitelist_client
        resp = tc.get("/api/bots/default/detail-by-owner")
        assert resp.status_code == 422  # FastAPI validation error for missing Query(...)

    def test_bot_not_found(self, whitelist_client):
        tc, svc, policy = whitelist_client
        policy.check.return_value = True
        svc.get_bot.side_effect = BotNotFoundError("not found")
        resp = tc.get("/api/bots/missing/detail-by-owner?owner_id=staff001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404


# ---------------------------------------------------------------------------
# GET /api/bots/search/domain-bots
# ---------------------------------------------------------------------------

class TestListDomainBots:
    """Tests for GET /api/bots/search/domain-bots endpoint."""

    def test_list_domain_bots_happy(self, client):
        """Successfully list domain bots without pagination (returns all)."""
        tc, svc, _ = client
        resp = tc.get("/api/bots/search/domain-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "data" in data
        assert data["data"]["total"] == 1
        assert len(data["data"]["items"]) == 1
        svc.list_domain_bots.assert_called_once()
        call_args = svc.list_domain_bots.call_args
        assert call_args.kwargs.get("page") is None
        assert call_args.kwargs.get("page_size") is None
        assert call_args.kwargs.get("keyword") is None

    def test_list_domain_bots_with_pagination(self, client):
        """List domain bots with custom pagination."""
        tc, svc, _ = client
        resp = tc.get("/api/bots/search/domain-bots?page=2&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        svc.list_domain_bots.assert_called_once()
        call_args = svc.list_domain_bots.call_args
        assert call_args.kwargs.get("page") == 2
        assert call_args.kwargs.get("page_size") == 10

    def test_list_domain_bots_with_keyword(self, client):
        """List domain bots with keyword search."""
        tc, svc, _ = client
        resp = tc.get("/api/bots/search/domain-bots?keyword=arch")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        svc.list_domain_bots.assert_called_once()
        call_args = svc.list_domain_bots.call_args
        assert call_args.kwargs.get("keyword") == "arch"

    def test_list_domain_bots_error(self, client):
        """Domain bots list returns error on service failure."""
        tc, svc, _ = client
        svc.list_domain_bots.side_effect = Exception("Database error")
        resp = tc.get("/api/bots/search/domain-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        assert "查询失败" in data["message"] or "失败" in data["message"]

    def test_list_domain_bots_strips_iam_token(self, client):
        """iam_token 是调用方 IAM 凭据,公开域 Bot 列表响应中必须剔除。"""
        tc, svc, _ = client
        bot_with_token = {**BOT_SAMPLE, "ext": {"iam_token": "secret-token", "is_domain_bot": True}}
        svc.list_domain_bots.return_value = {"total": 1, "items": [bot_with_token]}

        resp = tc.get("/api/bots/search/domain-bots")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        item = data["data"]["items"][0]
        assert "iam_token" not in item["ext"]
        assert item["ext"]["is_domain_bot"] is True


# ---------------------------------------------------------------------------
# GET /api/bots/{bot_id}/appcoding-bots
# ---------------------------------------------------------------------------

class TestListCodingBotsByArchitect:
    """Tests for GET /api/bots/{bot_id}/appcoding-bots endpoint."""

    def test_list_coding_bots_by_architect_happy(self, client):
        """Successfully list coding bots for an architect bot."""
        tc, svc, _ = client
        resp = tc.get("/api/bots/architect-bot/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "data" in data
        assert len(data["data"]) == 1
        svc.list_coding_bots_by_architect.assert_called_once_with("architect-bot")

    def test_list_coding_bots_anonymous_user(self, client):
        """Anonymous user cannot list coding bots."""
        from agentclaw.community.adapters.http.dependencies import get_request_context

        tc, svc, _ = client
        app = tc.app
        # Override to simulate anonymous user
        app.dependency_overrides[get_request_context] = lambda: _make_ctx(user_id="anonymous")

        resp = tc.get("/api/bots/architect-bot/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert "无法获取" in data["message"]

    def test_list_coding_bots_error(self, client):
        """Coding bots list returns error on service failure."""
        tc, svc, _ = client
        svc.list_coding_bots_by_architect.side_effect = Exception("Service unavailable")
        resp = tc.get("/api/bots/architect-bot/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        assert "失败" in data["message"]


# ---------------------------------------------------------------------------
# PUT /api/bots/{bot_id}/engine-config  (update_engine_config)
# ---------------------------------------------------------------------------

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

    attach_injector(app, Injector([
        _bind_bot_service(mock_bot_service, bot_repo=MagicMock(), auth=MagicMock()),
        _Extra(),
    ]))
    return TestClient(app)


class TestUpdateEngineConfig:
    """update_engine_config delegates to EngineConfigService.write_bot_config — one
    provider-blind path (no device_provider branching, no arca special-case)."""

    @pytest.fixture
    def engine_cfg_client(self, mock_bot_service):
        mock_svc = MagicMock()
        mock_svc.write_bot_config = AsyncMock(return_value=None)
        return _engine_config_app(mock_bot_service, mock_svc), mock_svc

    def test_writes_via_engine_config_service(self, engine_cfg_client):
        tc, svc = engine_cfg_client
        resp = tc.put("/api/bots/default/engine-config", json={"k": "v"})
        assert resp.json()["success"] is True
        svc.write_bot_config.assert_awaited_once()
        _, kwargs = svc.write_bot_config.call_args
        assert kwargs["config"] == {"k": "v"}
        assert kwargs["bot_id"] == "default"
        assert kwargs["engine_type"] == "openclaw"  # bot has no active_engine → DEFAULT

    def test_write_failure_surfaces_error(self, engine_cfg_client):
        from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError

        tc, svc = engine_cfg_client
        svc.write_bot_config.side_effect = DeviceNotBoundError("unbound")
        resp = tc.put("/api/bots/default/engine-config", json={"k": "v"})
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 500

    def test_invalid_json_rejected(self, engine_cfg_client):
        tc, svc = engine_cfg_client
        resp = tc.put(
            "/api/bots/default/engine-config",
            content="this is not valid json {{{",
            headers={"content-type": "application/json"},
        )
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        svc.write_bot_config.assert_not_awaited()

    def test_non_object_json_rejected(self, engine_cfg_client):
        tc, svc = engine_cfg_client
        resp = tc.put("/api/bots/default/engine-config", json=["not", "an", "object"])
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        svc.write_bot_config.assert_not_awaited()


class TestGetEngineConfig:
    """get_engine_config delegates to EngineConfigService.read_bot_config (provider-blind)."""

    @pytest.fixture
    def engine_cfg_client(self, mock_bot_service):
        mock_svc = MagicMock()
        mock_svc.read_bot_config = AsyncMock(return_value={"model": "x"})
        return _engine_config_app(mock_bot_service, mock_svc), mock_svc

    def test_reads_via_engine_config_service(self, engine_cfg_client):
        tc, svc = engine_cfg_client
        resp = tc.get("/api/bots/default/engine-config")
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {"model": "x"}
        svc.read_bot_config.assert_awaited_once()
        _, kwargs = svc.read_bot_config.call_args
        assert kwargs["bot_id"] == "default"
        assert kwargs["engine_type"] == "openclaw"

    def test_malformed_config_surfaces_error(self, engine_cfg_client):
        import json

        tc, svc = engine_cfg_client
        svc.read_bot_config.side_effect = json.JSONDecodeError("bad", "{", 0)
        resp = tc.get("/api/bots/default/engine-config")
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 500


# ---------------------------------------------------------------------------
# POST /api/bots/update-bot-ext-for-others  (admin only)
# ---------------------------------------------------------------------------

class TestUpdateBotExtForOthers:
    def test_permission_denied_for_non_admin(self, client):
        tc, svc, _ = client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1", "ext_update": {"k": "v"}},
        )
        assert resp.json()["error_code"] == 403

    def test_success_as_admin(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default", "ext_update": {"k": "v"}},
        )
        assert resp.json()["success"] is True
        svc.update_bot_ext.assert_called_once_with("default", "u1", {"k": "v"})

    def test_missing_target_user_id(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_bot_id": "default", "ext_update": {"k": "v"}},
        )
        assert resp.json()["error_code"] == 400

    def test_missing_target_bot_id(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "ext_update": {"k": "v"}},
        )
        assert resp.json()["error_code"] == 400

    def test_missing_ext_update(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default"},
        )
        assert resp.json()["error_code"] == 400

    def test_ext_update_not_dict(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default", "ext_update": "not-a-dict"},
        )
        assert resp.json()["error_code"] == 400

    def test_ext_update_empty_dict(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default", "ext_update": {}},
        )
        assert resp.json()["error_code"] == 400

    def test_bot_not_found(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.update_bot_ext.side_effect = BotNotFoundError("nope")
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1", "ext_update": {"k": "v"}},
        )
        assert resp.json()["error_code"] == 404

    def test_service_error(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.update_bot_ext.side_effect = BotServiceError("fail")
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1", "ext_update": {"k": "v"}},
        )
        assert resp.json()["error_code"] == 500
