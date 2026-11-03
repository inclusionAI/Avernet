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


class TestGetBotClassification:
    def test_authenticated_user_can_read_minimal_cross_owner_classification(
        self, client
    ):
        tc, svc, _passport = client
        tc.app.dependency_overrides[get_current_user] = _authenticated_user
        svc.get_bot_classification.return_value = {
            "bot_id": "other-service-bot",
            "bot_type": "service",
        }

        response = tc.get("/api/bots/other-service-bot/classification")

        assert response.json() == {
            "success": True,
            "message": "OK",
            "error_code": 200,
            "data": {
                "bot_id": "other-service-bot",
                "bot_type": "service",
            },
        }
        svc.get_bot_classification.assert_called_once_with(
            "other-service-bot", entity_id=None
        )

    def test_entity_id_disambiguates_legacy_bot_id(self, client):
        tc, svc, _passport = client
        tc.app.dependency_overrides[get_current_user] = _authenticated_user
        svc.get_bot_classification.return_value = {
            "bot_id": "default",
            "bot_type": "service",
        }

        response = tc.get("/api/bots/default/classification?entity_id=service-owner")

        assert response.json() == {
            "success": True,
            "message": "OK",
            "error_code": 200,
            "data": {
                "bot_id": "default",
                "bot_type": "service",
            },
        }
        svc.get_bot_classification.assert_called_once_with(
            "default", entity_id="service-owner"
        )

    def test_unknown_entity_returns_404_envelope(self, client):
        tc, svc, _passport = client
        tc.app.dependency_overrides[get_current_user] = _authenticated_user
        svc.get_bot_classification.return_value = None

        response = tc.get("/api/bots/default/classification?entity_id=missing-owner")

        assert response.json() == {
            "success": False,
            "message": "Bot不存在: default",
            "error_code": 404,
            "data": None,
        }
        svc.get_bot_classification.assert_called_once_with(
            "default", entity_id="missing-owner"
        )

    def test_missing_bot_returns_404_envelope(self, client):
        tc, svc, _passport = client
        tc.app.dependency_overrides[get_current_user] = _authenticated_user
        svc.get_bot_classification.return_value = None

        response = tc.get("/api/bots/missing/classification")

        assert response.json() == {
            "success": False,
            "message": "Bot不存在: missing",
            "error_code": 404,
            "data": None,
        }

    def test_ambiguous_bot_id_returns_409_envelope(self, client):
        tc, svc, _passport = client
        tc.app.dependency_overrides[get_current_user] = _authenticated_user
        svc.get_bot_classification.side_effect = BotLookupAmbiguousError

        response = tc.get("/api/bots/default/classification")

        assert response.json() == {
            "success": False,
            "message": "Bot ID 无法唯一定位: default",
            "error_code": 409,
            "data": None,
        }

    def test_unauthenticated_user_is_rejected(self, client):
        tc, _svc, _passport = client

        async def reject_unauthenticated_user():
            raise Unauthorized("missing login context")

        tc.app.dependency_overrides[get_current_user] = reject_unauthenticated_user

        response = tc.get("/api/bots/service-bot/classification")

        assert response.status_code == 401
