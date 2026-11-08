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


class TestCreateForOthers:
    def test_permission_denied(self, client):
        tc, svc, _ = client
        resp = tc.post(
            "/api/bots/create-for-others",
            json={"target_user_id": "u1", "target_nick_name": "Alice"},
        )
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
        resp = tc.post(
            "/api/bots/create-for-others",
            json={"target_user_id": "u1", "target_nick_name": "Alice"},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["action"] == "skipped"

    def test_device_limit_error(self, admin_client):
        tc, svc, _, mock_repo = admin_client
        tc.app.state.create_bot_for_others_service.execute.side_effect = (
            DeviceLimitError("limit")
        )
        resp = tc.post(
            "/api/bots/create-for-others",
            json={"target_user_id": "u1", "target_nick_name": "Alice"},
        )
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
        tc.app.state.create_bot_for_others_service.execute.side_effect = (
            DeviceAllocationError("alloc fail")
        )
        resp = tc.post(
            "/api/bots/create-for-others",
            json={"target_user_id": "u1", "target_nick_name": "Alice"},
        )
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

        resp = tc.post("/api/bots/repair-default-passport-for-others", json=payload)

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


class TestCreateBot:
    def test_application_coding_policy_error_is_mapped_before_passport(self, client):
        tc, svc, passport = client
        svc.is_workspace_hosting_available.return_value = False

        resp = tc.post(
            "/api/bots",
            json={
                "bot_name": "Coding Bot",
                "engine_type": "claude_code",
                "template_type": "applicationCoding",
            },
        )

        assert resp.json()["error_code"] == 503
        passport.apply_first_agent_passport.assert_not_called()
        passport.apply_agent_passport.assert_not_called()

    def test_needs_authorization_when_no_token(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {
            "iframe_url": "http://auth",
            "token": None,
        }
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
            "token": "tok123",
            "agent_code": "ac1",
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
                requested_entity_id,
                requested_entity_type,
            )
        )
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
                        skill_set_factory=_stub_skill_set_factory(
                            ["mcp.remote.1", "hitl"]
                        ),
                    )
                ]
            ),
        )

        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post("/api/bots", json={"bot_name": "NewBot"})

        assert resp.json()["success"] is True
        passport_kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert passport_kwargs["mcp_codes"] == ["mcp.remote.1"]
        # openclaw (default engine) carries no CLI items — fail-closed for non-aicoding
        assert passport_kwargs["cli_items"] == []

    def test_create_passport_folds_plain_aicoding_engine_into_claude_code(
        self, mock_bot_service, mock_passport
    ):
        """Plain engine_type=aicoding folds into claude_code with no CLI items.

        The old link's engine_type=aicoding is a legacy internal-engine value:
        the bot is created on the real engine (claude_code), and the aicoding
        default CLI grants follow the aicoding *form* — a plain no-template
        bot has no form, so it is authorized nothing (fail-closed), like the
        openclaw default in the sibling test above.
        """
        from agentclaw.community.adapters.http.bot_management.router import router
        import agentclaw.community.adapters.http.bot_management.router as router_module

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = lambda: _make_ctx()
        mock_auth = MagicMock()
        mock_auth.authorize_entity_access = AsyncMock(
            side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
                requested_entity_id,
                requested_entity_type,
            )
        )
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
                        skill_set_factory=_stub_skill_set_factory(["mcp.remote.1"]),
                    )
                ]
            ),
        )

        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post(
                "/api/bots", json={"bot_name": "NewBot", "engine_type": "aicoding"}
            )

        assert resp.json()["success"] is True
        passport_kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert passport_kwargs["engine_type"] == "claude_code"  # folded alias
        assert passport_kwargs["mcp_codes"] == ["mcp.remote.1"]
        assert passport_kwargs["cli_items"] == []

    def test_create_passport_carries_aicoding_cli_items_for_form_marked_template(
        self, mock_bot_service, mock_passport
    ):
        """Template-backed engine_type=aicoding keeps the 9 default CLI items.

        The fold records the server-managed engine_form marker in the template
        snapshot and the default-capability bucket resolution reads it, so the
        passport still authorizes the full aicoding CLI set — end-to-end
        through the router, not just the _defaults getter in isolation.
        """
        from agentclaw.community.adapters.http.bot_management.router import router
        import agentclaw.community.adapters.http.bot_management.router as router_module

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = lambda: _make_ctx()
        mock_auth = MagicMock()
        mock_auth.authorize_entity_access = AsyncMock(
            side_effect=lambda ctx, requested_entity_id, requested_entity_type: (
                requested_entity_id,
                requested_entity_type,
            )
        )
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
                        skill_set_factory=_stub_skill_set_factory(["mcp.remote.1"]),
                    )
                ]
            ),
        )

        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post(
                "/api/bots",
                json={
                    "bot_name": "NewBot",
                    "engine_type": "aicoding",
                    "template_type": "applicationCoding",
                    "template_config": {"devflow_workflow": "x"},
                },
            )

        assert resp.json()["success"] is True
        passport_kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert passport_kwargs["engine_type"] == "claude_code"  # folded alias
        # The aicoding form (marker in the template snapshot) keeps the CLI set.
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
                requested_entity_id,
                requested_entity_type,
            )
        )
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
                        skill_set_factory=_stub_skill_set_factory(["mcp.remote.1"]),
                    )
                ]
            ),
        )

        # personalCoding → aicoding CLI link fires.
        with patch.object(router_module, "generate_bot_id", return_value="default"):
            resp = TestClient(app).post(
                "/api/bots",
                json={
                    "bot_name": "NewBot",
                    "engine_type": "claude_code",
                    "template_type": "personalCoding",
                },
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
            "token": "tok123",
            "agent_code": "ac1",
        }
        with patch.object(router_module, "generate_bot_id", return_value="default"):
            TestClient(app).post(
                "/api/bots",
                json={
                    "bot_name": "NewBot",
                    "engine_type": "claude_code",
                    "template_type": "applicationCoding",
                },
            )
        kwargs = mock_passport.apply_first_agent_passport.call_args.kwargs
        assert kwargs["engine_type"] == "claude_code"
        assert len(kwargs["cli_items"]) == 9

        # claude_code 不带 template_type / 带 service 等非研发模板 → fail-closed 空。
        mock_passport.apply_first_agent_passport.reset_mock()
        mock_passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "ac1",
        }
        with patch.object(router_module, "generate_bot_id", return_value="default"):
            TestClient(app).post(
                "/api/bots",
                json={"bot_name": "NewBot", "engine_type": "claude_code"},
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
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
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
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
        svc.create_bot.side_effect = DeviceAllocationError("fail")
        resp = tc.post("/api/bots", json={"bot_name": "NewBot"})
        assert resp.json()["error_code"] == 500

    def test_device_limit_error(self, client):
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
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
        svc.check_create_bot_preflight.side_effect = DefaultBotTeclawNotAllowedError()

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
            space_id=None,
            space_quota=False,
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
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
        resp = tc.post("/api/bots", json={"bot_name": "My Bot 1"})
        data = resp.json()
        assert data["success"] is True
        passport.apply_first_agent_passport.assert_called_once()
        svc.create_bot.assert_called_once()

    def test_bot_name_missing_is_allowed(self, client):
        """B: 不传 bot_name → 走默认命名规则，不被 400 拦截。"""
        tc, svc, passport = client
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
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
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
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
        passport.apply_first_agent_passport.return_value = {
            "token": "tok123",
            "agent_code": "agent-test",
        }
        name = "a" * 32
        resp = tc.post("/api/bots", json={"bot_name": name})
        data = resp.json()
        assert data["success"] is True
        kwargs = svc.create_bot.call_args.kwargs
        assert kwargs.get("bot_name") == name


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
        passport.query_auth_status.return_value = {
            "status": "ISSUED",
            "token": "tok456",
        }
        resp = tc.post(
            "/api/bots/auth-status", json={"bot_id": "bot1", "bot_name": "NewBot"}
        )
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

    def test_issued_retained_creation_returns_retry_handle(self, client):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {
            "status": "ISSUED",
            "token": "tok",
        }
        svc.create_bot.side_effect = BotCreationRetainedError(
            bot_id="bot1",
            message="device allocation interrupted",
        )

        response = tc.post(
            "/api/bots/auth-status",
            json={"bot_id": "bot1", "bot_name": "NewBot"},
        )

        assert response.json() == {
            "success": False,
            "message": (
                "创建Bot失败，可使用 bot_id 重试: device allocation interrupted"
            ),
            "error_code": 500,
            "data": {"bot_id": "bot1", "retryable": True},
        }


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


class TestGetBotDetailByOwner:
    def test_operator_gets_detail_by_owner_without_owner_policy_check(
        self, whitelist_client
    ):
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
        assert (
            resp.status_code == 422
        )  # FastAPI validation error for missing Query(...)

    def test_bot_not_found(self, whitelist_client):
        tc, svc, policy = whitelist_client
        policy.check.return_value = True
        svc.get_bot.side_effect = BotNotFoundError("not found")
        resp = tc.get("/api/bots/missing/detail-by-owner?owner_id=staff001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404
