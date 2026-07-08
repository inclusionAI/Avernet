"""Unit tests for api_gateway permission module.

Covers:
- is_admin: admin staffIds, non-admin staffIds
- parse_bot_entity_id: valid format, invalid format, no colon
- check_bot_permission: matching owner, non-matching owner, invalid app_id
- check_permission: key owner match, bot entity match, bot owner mismatch, non-owner non-bot reject
- APIKeyPermissionChecker.check
"""

from unittest.mock import patch

from secbaas.api.api_gateway import APIKeyResponse


class TestIsAdmin:
    def test_admin_operator(self):
        from secbaas.api.api_gateway import is_admin
        from secbaas.config._models import Config, UserConfig

        cfg = Config(
            user_config=UserConfig(api_gateway={"admin_operators": ["u1", "u2"]})
        )
        with patch("secbaas.api.api_gateway._permission.get_config", return_value=cfg):
            assert is_admin("u1") is True
            assert is_admin("u2") is True

    def test_non_admin_operator(self):
        from secbaas.api.api_gateway import is_admin
        from secbaas.config._models import Config, UserConfig

        cfg = Config(
            user_config=UserConfig(api_gateway={"admin_operators": ["u1", "u2"]})
        )
        with patch("secbaas.api.api_gateway._permission.get_config", return_value=cfg):
            assert is_admin("unknown") is False
            assert is_admin("") is False


class TestParseBotEntityId:
    def test_valid_format(self):
        from secbaas.api.api_gateway import parse_bot_entity_id

        result = parse_bot_entity_id("bot-123:user-456")
        assert result == "user-456"

    def test_invalid_format_no_colon(self):
        from secbaas.api.api_gateway import parse_bot_entity_id

        result = parse_bot_entity_id("bot-123")
        assert result is None

    def test_empty_entity_id(self):
        from secbaas.api.api_gateway import parse_bot_entity_id

        result = parse_bot_entity_id("bot-123:")
        assert result is None


class TestCheckBotPermission:
    def test_operator_matches_entity(self):
        from secbaas.api.api_gateway import check_bot_permission

        assert check_bot_permission("user-456", "bot-123:user-456") is True

    def test_operator_mismatch(self):
        from secbaas.api.api_gateway import check_bot_permission

        assert check_bot_permission("attacker", "bot-123:user-456") is False

    def test_invalid_app_id(self):
        from secbaas.api.api_gateway import check_bot_permission

        assert check_bot_permission("user-456", "no-colon") is False


class TestCheckPermission:
    def test_key_owner_match(self):
        from secbaas.api.api_gateway import check_permission

        # _make_response sets owner="owner" by default
        api_key = _make_response(app_type="app", app_id="app-1")
        assert check_permission("owner", api_key) is True

    def test_key_owner_match_for_bot(self):
        from secbaas.api.api_gateway import check_permission

        # owner field matches, even if entity_id differs
        api_key = _make_response(app_type="bot", app_id="bot-1:entity-1", owner="owner")
        assert check_permission("owner", api_key) is True

    def test_bot_entity_match(self):
        from secbaas.api.api_gateway import check_permission

        # entity_id matches but owner doesn't — still allowed for bot keys
        api_key = _make_response(app_type="bot", app_id="bot-1:entity-1", owner="other")
        assert check_permission("entity-1", api_key) is True

    def test_bot_owner_mismatch(self):
        from secbaas.api.api_gateway import check_permission

        api_key = _make_response(app_type="bot", app_id="bot-1:owner-1", owner="other")
        assert check_permission("attacker", api_key) is False

    def test_non_owner_non_bot_rejected(self):
        from secbaas.api.api_gateway import check_permission

        # app key: only owner can operate, admin is handled by admin endpoint
        api_key = _make_response(app_type="app", app_id="any", owner="owner")
        assert check_permission("non-owner", api_key) is False

    def test_admin_not_bypassed(self):
        from secbaas.api.api_gateway import check_permission

        # admin is no longer a bypass — admin operations go through admin endpoint
        api_key = _make_response(app_type="app", app_id="any", owner="owner")
        assert check_permission("admin-test-001", api_key) is False


class TestAPIKeyPermissionChecker:
    def test_check_key_owner(self):
        from secbaas.api.api_gateway import APIKeyPermissionChecker

        checker = APIKeyPermissionChecker("owner")
        api_key = _make_response(app_type="app", app_id="any")
        assert checker.check(api_key) is True

    def test_check_bot_entity(self):
        from secbaas.api.api_gateway import APIKeyPermissionChecker

        checker = APIKeyPermissionChecker("owner-1")
        api_key = _make_response(app_type="bot", app_id="bot-1:owner-1", owner="other")
        assert checker.check(api_key) is True

    def test_check_denied(self):
        from secbaas.api.api_gateway import APIKeyPermissionChecker

        checker = APIKeyPermissionChecker("stranger")
        api_key = _make_response(app_type="bot", app_id="bot-1:owner-1", owner="other")
        assert checker.check(api_key) is False


def _make_response(app_type="app", app_id="app-1", owner="owner") -> APIKeyResponse:
    from datetime import datetime

    return APIKeyResponse(
        id=1,
        app_id=app_id,
        app_type=app_type,
        key_name="test",
        api_key_prefix="pref",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner=owner,
        tenant="t1",
        env="test",
        creator="creator",
        modifier=None,
        policy=None,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )
