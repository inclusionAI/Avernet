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
        bot_with_token = {
            **BOT_SAMPLE,
            "ext": {"iam_token": "secret-token", "is_domain_bot": True},
        }
        svc.list_domain_bots.return_value = {"total": 1, "items": [bot_with_token]}

        resp = tc.get("/api/bots/search/domain-bots")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        item = data["data"]["items"][0]
        assert "iam_token" not in item["ext"]
        assert item["ext"]["is_domain_bot"] is True


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
        app.dependency_overrides[get_request_context] = lambda: _make_ctx(
            user_id="anonymous"
        )

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
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

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


class TestUpdateBotExtForOthers:
    def test_permission_denied_for_non_admin(self, client):
        tc, svc, _ = client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={
                "target_user_id": "u1",
                "target_bot_id": "b1",
                "ext_update": {"k": "v"},
            },
        )
        assert resp.json()["error_code"] == 403

    def test_success_as_admin(self, admin_client):
        tc, svc, _, _ = admin_client
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={
                "target_user_id": "u1",
                "target_bot_id": "default",
                "ext_update": {"k": "v"},
            },
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
            json={
                "target_user_id": "u1",
                "target_bot_id": "default",
                "ext_update": "not-a-dict",
            },
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
            json={
                "target_user_id": "u1",
                "target_bot_id": "b1",
                "ext_update": {"k": "v"},
            },
        )
        assert resp.json()["error_code"] == 404

    def test_service_error(self, admin_client):
        tc, svc, _, _ = admin_client
        svc.update_bot_ext.side_effect = BotServiceError("fail")
        resp = tc.post(
            "/api/bots/update-bot-ext-for-others",
            json={
                "target_user_id": "u1",
                "target_bot_id": "b1",
                "ext_update": {"k": "v"},
            },
        )
        assert resp.json()["error_code"] == 500


class TestChangeSpaceForOthers:
    def test_permission_denied_for_non_admin(self, client):
        tc, svc, _ = client
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={"space_id": 123, "bot_list": [{"owner_id": "u1", "bot_id": "b1"}]},
        )
        assert resp.json()["error_code"] == 403

    def test_missing_space_id(self, space_admin_client):
        tc, space_svc = space_admin_client
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={"bot_list": [{"owner_id": "u1", "bot_id": "b1"}]},
        )
        assert resp.json()["error_code"] == 400
        space_svc.change_space.assert_not_called()

    def test_space_id_not_int(self, space_admin_client):
        tc, space_svc = space_admin_client
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={"space_id": "123", "bot_list": [{"owner_id": "u1", "bot_id": "b1"}]},
        )
        assert resp.json()["error_code"] == 400

    def test_missing_bot_list(self, space_admin_client):
        tc, space_svc = space_admin_client
        resp = tc.post("/api/bots/change-space-for-others", json={"space_id": 123})
        assert resp.json()["error_code"] == 400
        space_svc.change_space.assert_not_called()

    def test_empty_bot_list(self, space_admin_client):
        tc, space_svc = space_admin_client
        resp = tc.post(
            "/api/bots/change-space-for-others", json={"space_id": 123, "bot_list": []}
        )
        assert resp.json()["error_code"] == 400

    def test_bot_list_over_cap(self, space_admin_client):
        tc, space_svc = space_admin_client
        bot_list = [{"owner_id": "u1", "bot_id": f"b{i}"} for i in range(101)]
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={"space_id": 123, "bot_list": bot_list},
        )
        assert resp.json()["error_code"] == 400
        assert "100" in resp.json()["message"]
        space_svc.change_space.assert_not_called()

    def test_success_and_service_called_per_item(self, space_admin_client):
        tc, space_svc = space_admin_client
        space_svc.change_space.return_value = _assignment(changed=True)
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [
                    {"owner_id": "u1", "bot_id": "b1"},
                    {"owner_id": "u2", "bot_id": "b2"},
                ],
            },
        )
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 2
        assert body["data"]["succeeded"] == 2
        assert body["data"]["failed"] == 0
        assert all(r["success"] and r["changed"] for r in body["data"]["results"])
        assert space_svc.change_space.call_count == 2
        space_svc.change_space.assert_any_call(
            bot_id="b1", owner_id="u1", space_id=123, env=None
        )
        space_svc.change_space.assert_any_call(
            bot_id="b2", owner_id="u2", space_id=123, env=None
        )

    def test_mixed_batch_reports_per_item_outcome(self, space_admin_client):
        from agentclaw.community.core.bot_management.bot_quota import (
            BotQuotaExceededError,
            BotQuotaScope,
            BotQuotaSnapshot,
        )
        from agentclaw.community.core.spaces.models import SpaceType

        quota_snapshot = BotQuotaSnapshot(
            scope=BotQuotaScope(
                owner_id="u3", space_id=123, space_name="s", space_type=SpaceType.TEAM
            ),
            ceiling=1,
            used=1,
        )

        def _change(*, bot_id, owner_id, space_id, env=None):
            if bot_id == "missing":
                raise BotNotFoundError(f"Bot not found: {bot_id}")
            if bot_id == "quota":
                raise BotQuotaExceededError(quota_snapshot)
            if bot_id == "same":
                return _assignment(changed=False)
            return _assignment(changed=True)

        space_svc = space_admin_client[1]
        space_svc.change_space.side_effect = _change

        tc = space_admin_client[0]
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [
                    {"owner_id": "u1", "bot_id": "ok"},
                    {"owner_id": "u2", "bot_id": "missing"},
                    {"owner_id": "u3", "bot_id": "quota"},
                    {"owner_id": "u4", "bot_id": "same"},
                ],
            },
        )
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 4
        assert body["data"]["succeeded"] == 2
        assert body["data"]["failed"] == 2
        results = {r["bot_id"]: r for r in body["data"]["results"]}
        assert results["ok"]["success"] is True and results["ok"]["changed"] is True
        assert (
            results["same"]["success"] is True and results["same"]["changed"] is False
        )
        assert "已在目标空间" in results["same"]["message"]
        assert results["missing"]["success"] is False
        assert "Bot不存在" in results["missing"]["message"]
        assert results["quota"]["success"] is False
        assert "quota" in results["quota"]["message"]

    def test_item_with_blank_fields_fails_per_item(self, space_admin_client):
        tc, space_svc = space_admin_client
        space_svc.change_space.return_value = _assignment(changed=True)
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [
                    {"owner_id": "  ", "bot_id": "b1"},
                    {"owner_id": "u1", "bot_id": "b2"},
                ],
            },
        )
        body = resp.json()
        assert body["data"]["total"] == 2
        assert body["data"]["succeeded"] == 1
        assert body["data"]["failed"] == 1
        results = {r["bot_id"]: r for r in body["data"]["results"]}
        assert results["b1"]["success"] is False
        assert space_svc.change_space.call_count == 1

    def test_space_error_fails_item_only(self, space_admin_client):
        from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError

        space_svc = space_admin_client[1]
        space_svc.change_space.side_effect = SpaceAccessDeniedError(
            "personal space belongs to another user"
        )
        tc = space_admin_client[0]
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={"space_id": 123, "bot_list": [{"owner_id": "u1", "bot_id": "b1"}]},
        )
        body = resp.json()
        assert body["data"]["failed"] == 1
        assert "personal space" in body["data"]["results"][0]["message"]

    def test_env_threaded_to_service_and_echoed(self, space_admin_client):
        tc, space_svc = space_admin_client
        space_svc.change_space.return_value = _assignment(changed=True)
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                "env": "prod",
            },
        )
        body = resp.json()
        assert body["success"] is True
        space_svc.change_space.assert_called_once_with(
            bot_id="b1", owner_id="u1", space_id=123, env="prod"
        )
        assert body["data"]["env"] == "prod"

    def test_env_blank_treated_as_absent(self, space_admin_client):
        from agentclaw.community.utils.env_utils import get_current_env

        tc, space_svc = space_admin_client
        space_svc.change_space.return_value = _assignment(changed=True)
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                "env": "   ",
            },
        )
        body = resp.json()
        assert body["success"] is True
        space_svc.change_space.assert_called_once_with(
            bot_id="b1", owner_id="u1", space_id=123, env=None
        )
        assert body["data"]["env"] == get_current_env()

    def test_env_wrong_type_rejected(self, space_admin_client):
        tc, space_svc = space_admin_client
        resp = tc.post(
            "/api/bots/change-space-for-others",
            json={
                "space_id": 123,
                "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                "env": 123,
            },
        )
        assert resp.json()["error_code"] == 400
        space_svc.change_space.assert_not_called()

    def test_failed_reasons_appear_in_summary_log(self, space_admin_client, caplog):
        import logging as _logging

        tc, space_svc = space_admin_client
        space_svc.change_space.side_effect = BotNotFoundError("Bot not found: b1")
        with caplog.at_level(_logging.INFO, logger="start"):
            tc.post(
                "/api/bots/change-space-for-others",
                json={
                    "space_id": 123,
                    "bot_list": [{"owner_id": "u1", "bot_id": "b1"}],
                },
            )
        assert any(
            "failed_reasons={'BotNotFound': 1}" in r.message
            for r in caplog.records
            if "change_space_for_others" in r.message
        )
