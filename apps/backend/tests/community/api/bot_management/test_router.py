"""Bot router contracts grouped by endpoint responsibility."""

from tests.community.api.bot_management._bot_router_test_support import (
    AsyncMock as AsyncMock,
    AuthPlugin as AuthPlugin,
    AuthRelationshipPlugin as AuthRelationshipPlugin,
    AuthenticatedUser as AuthenticatedUser,
    BOT_SAMPLE as BOT_SAMPLE,
    BotCreationRetainedError as BotCreationRetainedError,
    BotInvalidLifecycleStateError as BotInvalidLifecycleStateError,
    BotLimitExceededError as BotLimitExceededError,
    BotLookupAmbiguousError as BotLookupAmbiguousError,
    BotNameExistsError as BotNameExistsError,
    BotNameInvalidError as BotNameInvalidError,
    BotNotFoundError as BotNotFoundError,
    BotOperationNotAllowedError as BotOperationNotAllowedError,
    BotService as BotService,
    BotServiceError as BotServiceError,
    DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE as DEFAULT_BOT_TECLAW_NOT_ALLOWED_MESSAGE,
    DefaultBotTeclawNotAllowedError as DefaultBotTeclawNotAllowedError,
    DeviceAllocationError as DeviceAllocationError,
    DeviceLimitError as DeviceLimitError,
    FastAPI as FastAPI,
    Injector as Injector,
    MagicMock as MagicMock,
    Module as Module,
    PassportError as PassportError,
    PassportPlugin as PassportPlugin,
    PolicyService as PolicyService,
    RequestContext as RequestContext,
    SkillSetServiceFactory as SkillSetServiceFactory,
    TestClient as TestClient,
    Unauthorized as Unauthorized,
    _assignment as _assignment,
    _authenticated_user as _authenticated_user,
    _bind_bot_service as _bind_bot_service,
    _engine_config_app as _engine_config_app,
    _make_ctx as _make_ctx,
    _stub_skill_set_factory as _stub_skill_set_factory,
    admin_client as admin_client,
    admin_update_client as admin_update_client,
    attach_injector as attach_injector,
    client as client,
    get_current_user as get_current_user,
    get_request_context as get_request_context,
    mock_bot_service as mock_bot_service,
    mock_passport as mock_passport,
    patch as patch,
    pytest as pytest,
    require_operator as require_operator,
    space_admin_client as space_admin_client,
    whitelist_client as whitelist_client,
)


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
                requested_entity_id,
                requested_entity_type,
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
        anon_app.dependency_overrides[get_request_context] = lambda: _make_ctx(
            user_id="anonymous"
        )
        attach_injector(
            anon_app,
            Injector(
                [
                    _bind_bot_service(
                        svc,
                        passport=MagicMock(),
                    )
                ]
            ),
        )
        tc2 = TestClient(anon_app)
        resp = tc2.get("/api/bots/default")
        assert resp.json()["error_code"] == 400


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
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "PENDING",
            "device_binding": {"status": "PENDING"},
        }
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
            # 明确仍在初始化：仓库还没 clone 完。
            "ext": {"start_status": "PENDING"},
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
    def test_claude_code_app_bot_active_binding_backfills_missing_marker(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
            "device_binding": {
                "status": "ACTIVE",
                "device_id": "BOT-ready",
                "device_provider": "baas",
            },
            "ext": {},
        }
        resp = tc.get("/api/bots/default/status")
        assert resp.json()["data"]["is_ready"] is True

    def test_app_bot_missing_marker_without_active_binding_is_not_ready(self, client):
        tc, svc, _ = client
        svc.get_bot.return_value = {
            **BOT_SAMPLE,
            "status": "ACTIVE",
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
            "device_binding": {"status": "PENDING", "device_provider": "baas"},
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


class TestUpdateBot:
    def test_collaborator_cannot_write_editor_policy_via_legacy_ext(self, client):
        tc, svc, _ = client
        with patch(
            "agentclaw.community.core.bot_collaborator.interceptor.collaborator.CollaboratorPermissionInterceptor.before",
            new=AsyncMock(side_effect=lambda ctx: ctx),
        ):
            response = tc.put(
                "/api/bots/default?owner_id=someone-else",
                json={"ext": {"editor_request_auto_approve": True}},
            )
        assert response.json()["error_code"] == 403
        assert (
            response.json()["message"]
            == "Only the Bot owner can change editor request policy"
        )
        svc.update_bot.assert_not_called()

    def test_owner_can_write_editor_policy_via_legacy_ext(self, client):
        tc, svc, _ = client
        response = tc.put(
            "/api/bots/default", json={"ext": {"editor_request_auto_approve": True}}
        )
        assert response.json()["success"] is True
        assert (
            svc.update_bot.call_args.kwargs["ext"]["editor_request_auto_approve"]
            is True
        )

    def test_collaborator_unrelated_ext_update_unchanged(self, client):
        tc, svc, _ = client
        with patch(
            "agentclaw.community.core.bot_collaborator.interceptor.collaborator.CollaboratorPermissionInterceptor.before",
            new=AsyncMock(side_effect=lambda ctx: ctx),
        ):
            response = tc.put(
                "/api/bots/default?owner_id=someone-else",
                json={"ext": {"read_only_rules": []}},
            )
        assert response.json()["success"] is True
        assert svc.update_bot.call_args.kwargs["ext"] == {"read_only_rules": []}

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


class TestAdminUpdateBotNameValidation:
    def test_invalid_name_special_char_rejected(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put(
            "/api/bots/default/admin",
            json={
                "owner_id": "u_owner",
                "bot_name": "bad@name#bot",
            },
        )
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "特殊字符" in body["message"]
        svc.admin_update_bot.assert_not_called()

    def test_invalid_name_empty_rejected(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put(
            "/api/bots/default/admin",
            json={
                "owner_id": "u_owner",
                "bot_name": "   ",
            },
        )
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 400
        assert "不能为空" in body["message"]
        svc.admin_update_bot.assert_not_called()

    def test_no_bot_name_skips_validation(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put(
            "/api/bots/default/admin",
            json={
                "owner_id": "u_owner",
                "bot_desc": "desc only",
            },
        )
        body = resp.json()
        assert body["success"] is True
        svc.admin_update_bot.assert_called_once()
        assert svc.admin_update_bot.call_args.kwargs.get("bot_name") is None

    def test_valid_name_passes(self, admin_update_client):
        tc, svc = admin_update_client
        resp = tc.put(
            "/api/bots/default/admin",
            json={
                "owner_id": "u_owner",
                "bot_name": "合规名称-1",
            },
        )
        assert resp.json()["success"] is True
        svc.admin_update_bot.assert_called_once()
        assert svc.admin_update_bot.call_args.kwargs.get("bot_name") == "合规名称-1"


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


class TestRestartBot:
    def test_success(self, client):
        tc, svc, _ = client
        resp = tc.post("/api/bots/default/restart")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_forwards_engine_agnostic_extra_configs(self, client):
        tc, svc, _ = client
        extra_configs = {
            "template_config": {
                "template_key": "architect",
                "template_version_id": 101,
            }
        }

        resp = tc.post(
            "/api/bots/default/restart",
            json={"extra_configs": extra_configs},
        )

        assert resp.status_code == 200
        svc.restart_bot_async.assert_called_once_with(
            bot_id="default",
            user_id="test_user",
            nick_name="Test User",
            extra_configs=extra_configs,
        )

    def test_ignores_unknown_legacy_body_fields(self, client):
        tc, svc, _ = client

        resp = tc.post(
            "/api/bots/default/restart",
            json={"template_config": {"template_version_id": 101}},
        )

        assert resp.status_code == 200
        svc.restart_bot_async.assert_called_once_with(
            bot_id="default",
            user_id="test_user",
            nick_name="Test User",
            extra_configs=None,
        )

    def test_bot_not_found(self, client):
        tc, svc, _ = client
        svc.restart_bot_async.side_effect = BotNotFoundError("nope")
        resp = tc.post("/api/bots/missing/restart")
        assert resp.json()["error_code"] == 404

    def test_service_error(self, client):
        tc, svc, _ = client
        svc.restart_bot_async.side_effect = BotServiceError("fail")
        resp = tc.post("/api/bots/default/restart")
        assert resp.json()["error_code"] == 500

    def test_recycled_bot_returns_conflict(self, client):
        tc, svc, _ = client
        svc.restart_bot_async.side_effect = BotInvalidLifecycleStateError(
            bot_id="default",
            current_status="RECYCLED",
        )

        resp = tc.post("/api/bots/default/restart")

        assert resp.status_code == 409
        assert resp.json()["success"] is False
        assert resp.json()["error_code"] == 409

    def test_rejects_teclaw_bot(self, client):
        tc, svc, _ = client
        svc.restart_bot_async.side_effect = BotOperationNotAllowedError(
            "teclaw 类型的 Bot 不支持重启"
        )

        resp = tc.post("/api/bots/default/restart")

        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "message": "teclaw 类型的 Bot 不支持重启",
            "error_code": 400,
            "data": None,
        }

    def test_activation_in_progress_returns_accepted(self, client):
        tc, svc, _ = client
        svc.restart_bot_async.return_value = {
            **BOT_SAMPLE,
            "status": "REACTIVATING",
            "restart_in_progress": True,
            "message": "Bot activation is in progress",
        }

        resp = tc.post("/api/bots/default/restart")

        assert resp.status_code == 202
        assert resp.json()["success"] is True
        assert resp.json()["data"]["restart_in_progress"] is True


class TestRestartScheduler:
    def test_rejects_non_admin(self, client):
        tc, svc, _ = client

        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )

        assert resp.json() == {
            "success": False,
            "message": "权限不足：您没有权限调用此接口",
            "error_code": 403,
            "data": None,
        }
        svc.restart_bot_async.assert_not_called()

    def test_success(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        svc.restart_bot_async.assert_called_once_with(
            bot_id="default",
            user_id="test_user",
            nick_name="test_user",
        )

    def test_bot_not_found(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotNotFoundError("nope")
        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "missing"},
        )
        assert resp.json()["error_code"] == 404

    def test_service_error(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotServiceError("fail")
        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )
        assert resp.json()["error_code"] == 500

    def test_recycled_bot_returns_conflict(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotInvalidLifecycleStateError(
            bot_id="default",
            current_status="RECYCLED",
        )

        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )

        assert resp.status_code == 409
        assert resp.json()["error_code"] == 409

    def test_rejects_teclaw_bot(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotOperationNotAllowedError(
            "teclaw 类型的 Bot 不支持重启"
        )

        resp = tc.post(
            "/api/bots/restart-scheduler",
            json={"user_id": "test_user", "bot_id": "default"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "message": "teclaw 类型的 Bot 不支持重启",
            "error_code": 400,
            "data": None,
        }

    def test_activation_in_progress_returns_accepted(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.return_value = {
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


class TestReleaseForOthers:
    def test_permission_denied_for_non_admin(self, client):
        tc, svc, _ = client
        resp = tc.post(
            "/api/bots/release-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1"},
        )
        assert resp.json()["error_code"] == 403

    def test_success_as_admin(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/release-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default"},
        )
        assert resp.json()["success"] is True

    def test_missing_target_user_id(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/release-for-others", json={"target_bot_id": "default"}
        )
        assert resp.json()["error_code"] == 400

    def test_missing_target_bot_id(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post("/api/bots/release-for-others", json={"target_user_id": "u1"})
        assert resp.json()["error_code"] == 400

    def test_bot_not_found(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.release_bot_for_others.side_effect = BotNotFoundError("nope")
        resp = tc.post(
            "/api/bots/release-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1"},
        )
        assert resp.json()["error_code"] == 404

    def test_service_error(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.release_bot_for_others.side_effect = BotServiceError("fail")
        resp = tc.post(
            "/api/bots/release-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1"},
        )
        assert resp.json()["error_code"] == 500


class TestRestartForOthers:
    def test_permission_denied(self, client):
        tc, svc, _ = client
        resp = tc.post(
            "/api/bots/restart-for-others",
            json={"target_user_id": "u1", "target_bot_id": "b1"},
        )
        assert resp.json()["error_code"] == 403

    def test_success_as_admin(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/restart-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default"},
        )
        assert resp.json()["success"] is True

    def test_rejects_teclaw_bot(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotOperationNotAllowedError(
            "teclaw 类型的 Bot 不支持重启"
        )

        resp = tc.post(
            "/api/bots/restart-for-others",
            json={"target_user_id": "u1", "target_bot_id": "default"},
        )

        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert data["message"] == "teclaw 类型的 Bot 不支持重启"
        svc.restart_bot_async.assert_called_once()

    def test_missing_target_user(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/restart-for-others", json={"target_bot_id": "default"}
        )
        assert resp.json()["error_code"] == 400

    def test_bot_not_found(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotNotFoundError("nope")
        resp = tc.post(
            "/api/bots/restart-for-others",
            json={"target_user_id": "u1", "target_bot_id": "missing"},
        )
        assert resp.json()["error_code"] == 404

    def test_recycled_bot_returns_conflict(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.restart_bot_async.side_effect = BotInvalidLifecycleStateError(
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
        svc.restart_bot_async.return_value = {
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
