"""Unit tests for open_api/dependencies.py.

Uses Patch + MagicMock to invoke dependency functions directly,
NOT TestClient. Covers every function in dependencies.py.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, status

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    get_api_key_from_header,
    get_bot_chat_context,
    get_iam_token_from_cookie,
    match_allowed_bots,
    parse_bot_id,
    validate_api_key,
    validate_policy,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import BotChatContext
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.core.service.bot_run import BotServiceSelector

# ── helper ────────────────────────────────────────────────────


def _make_api_key_record(**overrides) -> APIKeyRecord:
    defaults = {
        "id": 1,
        "gmt_create": datetime.now(),
        "gmt_modified": datetime.now(),
        "api_key_hash": "fake_hash",
        "api_key_prefix": "kp-001",
        "key_name": "test-key",
        "app_id": "bot-1:entity-1",
        "app_type": "system",
        "description": None,
        "rate_limit_rpm": None,
        "rate_limit_rpd": None,
        "status": "ACTIVE",
        "owner": "owner-1",
        "tenant": "tenant-1",
        "env": "test",
        "creator": "creator-1",
        "modifier": None,
        "policy": None,
    }
    defaults.update(overrides)
    return APIKeyRecord(**defaults)


# ── get_api_key_from_header ───────────────────────────────────


class TestGetApiKeyFromHeader:
    def test_valid_bearer_token(self):
        result = get_api_key_from_header("Bearer sk-abc123xyz")
        assert result == "sk-abc123xyz"

    def test_bearer_lowercase(self):
        result = get_api_key_from_header("bearer sk-abc123xyz")
        assert result == "sk-abc123xyz"

    def test_bearer_mixed_case(self):
        result = get_api_key_from_header("BeArEr sk-abc123xyz")
        assert result == "sk-abc123xyz"

    def test_three_part_value(self):
        """3 parts should fail since len(parts) != 2."""
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header("Bearer sk-abc extra")
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_bearer_scheme(self):
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header("Basic sk-abc123xyz")
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    def test_one_word_no_scheme(self):
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header("BearerOnly")
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_string_raises_401(self):
        """Empty string treated as falsy → 401."""
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header("")
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.value.detail["code"] == 40101

    def test_none_value_raises_401(self):
        """None authorization header."""
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header(None)
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.value.detail["code"] == 40101

    def test_bearer_with_empty_token(self):
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header("Bearer ")
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    def test_token_with_spaces(self):
        with pytest.raises(HTTPException) as exc:
            get_api_key_from_header("Bearer token with spaces")
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


# ── get_iam_token_from_cookie ─────────────────────────────────


class TestGetIamTokenFromCookie:
    def test_case_insensitive_match(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"iam_token": "tok-abc"}
        result = get_iam_token_from_cookie(mock_request)
        assert result == "tok-abc"

    def test_uppercase_key(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"IAM_TOKEN": "tok-abc"}
        result = get_iam_token_from_cookie(mock_request)
        assert result == "tok-abc"

    def test_mixed_case_key(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"Iam_Token": "tok-abc"}
        result = get_iam_token_from_cookie(mock_request)
        assert result == "tok-abc"

    def test_empty_value_returns_none(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"IAM_TOKEN": ""}
        result = get_iam_token_from_cookie(mock_request)
        assert result is None

    def test_no_iam_token_cookie(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"session_id": "sess-1"}
        result = get_iam_token_from_cookie(mock_request)
        assert result is None

    def test_empty_cookies(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {}
        result = get_iam_token_from_cookie(mock_request)
        assert result is None

    def test_multiple_cookies_finds_iam_token(self):
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"a": "1", "IAM_TOKEN": "tok-xyz", "b": "2"}
        result = get_iam_token_from_cookie(mock_request)
        assert result == "tok-xyz"

    def test_none_value_in_cookie(self):
        """Cookie value is None (not empty string)."""
        mock_request = MagicMock(spec=Request)
        mock_request.cookies = {"IAM_TOKEN": None}
        result = get_iam_token_from_cookie(mock_request)
        assert result is None


# ── validate_api_key ──────────────────────────────────────────


class TestValidateApiKey:
    @pytest.mark.asyncio
    async def test_valid_api_key_returns_record(self):
        record = _make_api_key_record()
        mock_validator = MagicMock()
        mock_validator.verify = AsyncMock(return_value=record)

        result = await validate_api_key("sk-abc", validator=mock_validator)
        assert result == record
        mock_validator.verify.assert_awaited_once_with("sk-abc")

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_none_raises_401(self):
        mock_validator = MagicMock()
        mock_validator.verify = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await validate_api_key("invalid-key", validator=mock_validator)
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.value.detail["code"] == 40103

    @pytest.mark.asyncio
    async def test_different_record_fields_preserved(self):
        """Verify record is returned as-is."""
        record = _make_api_key_record(
            app_id="bot-2:entity-2",
            tenant="tenant-2",
            app_type="app",
            api_key_prefix="kp-002",
        )
        mock_validator = MagicMock()
        mock_validator.verify = AsyncMock(return_value=record)

        result = await validate_api_key("sk-xyz", validator=mock_validator)
        assert result.app_id == "bot-2:entity-2"
        assert result.tenant == "tenant-2"
        assert result.app_type == "app"
        assert result.api_key_prefix == "kp-002"


# ── get_bot_chat_context ──────────────────────────────────────


class TestGetBotChatContext:
    def test_full_context_from_record_with_iam_token(self):
        record = _make_api_key_record(
            app_id="bot-1:entity-1",
            app_type="app",
            tenant="tenant-1",
            api_key_prefix="kp-001",
        )
        result = get_bot_chat_context(
            api_key_record=record,
            iam_token="tok-abc",
        )
        assert isinstance(result, BotChatContext)
        assert result.api_key_prefix == "kp-001"
        assert result.app_id == "bot-1:entity-1"
        assert result.app_type == "app"
        assert result.iam_token == "tok-abc"
        assert result.tenant == "tenant-1"

    def test_iam_token_none(self):
        record = _make_api_key_record()
        result = get_bot_chat_context(
            api_key_record=record,
            iam_token=None,
        )
        assert result.iam_token is None

    def test_app_type_none_defaults_to_unknown(self):
        """app_type=None → defaults to 'UNKNOWN'."""
        record = _make_api_key_record(app_type=None)
        result = get_bot_chat_context(
            api_key_record=record,
            iam_token=None,
        )
        assert result.app_type == "UNKNOWN"

    def test_tenant_none_defaults_to_empty_string(self):
        """tenant=None → defaults to ''."""
        record = _make_api_key_record(tenant=None)
        result = get_bot_chat_context(
            api_key_record=record,
            iam_token=None,
        )
        assert result.tenant == ""


# ── parse_bot_id ──────────────────────────────────────────────


class TestParseBotId:
    def test_standard_format(self):
        """标准格式 bot_id:entity_id"""
        result = parse_bot_id("bot-1:entity-1")
        assert result == ("bot-1", "entity-1")

    def test_no_entity_id(self):
        """只有 bot_id，没有 entity_id"""
        result = parse_bot_id("bot-1")
        assert result == ("bot-1", "")

    def test_empty_string(self):
        """空字符串"""
        result = parse_bot_id("")
        assert result == ("", "")

    def test_multiple_colons(self):
        """多个冒号，只分割第一个"""
        result = parse_bot_id("bot-1:entity-1:extra")
        assert result == ("bot-1", "entity-1:extra")

    def test_trailing_colon(self):
        """末尾冒号"""
        result = parse_bot_id("bot-1:")
        assert result == ("bot-1", "")


# ── match_allowed_bots ────────────────────────────────────────


class TestMatchAllowedBots:
    def test_exact_match(self):
        """完全匹配，返回 allowed_bots 中的权威格式"""
        result = match_allowed_bots("bot-1:entity-1", ["bot-1:entity-1"])
        assert result == "bot-1:entity-1"

    def test_non_default_match_by_real_bot_id(self):
        """非 default 模式，只匹配 real_bot_id，返回 allowed 中的权威 bot_id"""
        result = match_allowed_bots("bot-1:any-entity", ["bot-1:entity-1"])
        assert result == "bot-1:entity-1"

    def test_default_requires_full_match(self):
        """default 模式需要完整匹配"""
        result = match_allowed_bots("default:entity-1", ["default:entity-1"])
        assert result == "default:entity-1"

    def test_default_partial_no_match(self):
        """default 模式部分匹配不通过"""
        result = match_allowed_bots("default:entity-2", ["default:entity-1"])
        assert result is None

    def test_no_match(self):
        """完全不匹配"""
        result = match_allowed_bots("bot-2:entity-1", ["bot-1:entity-1"])
        assert result is None

    def test_empty_allowed_bots(self):
        """空列表返回 None"""
        result = match_allowed_bots("bot-1:entity-1", [])
        assert result is None

    def test_multiple_allowed_bots_match(self):
        """多个 allowed_bots 中匹配一个，返回匹配到的条目"""
        result = match_allowed_bots(
            "bot-2:entity-2", ["bot-1:entity-1", "bot-2:entity-2", "bot-3:entity-3"]
        )
        assert result == "bot-2:entity-2"


# ── BotServiceSelector ────────────────────────────────────────


class TestBotServiceSelector:
    def test_select_claw_when_no_binding(self):
        """No binding info → select claw service."""
        claw_service = MagicMock()
        baas_service = MagicMock()
        selector = BotServiceSelector(claw_service, baas_service)

        result = selector.select(None)

        assert result == claw_service

    def test_select_claw_when_non_baas_provider(self):
        """Non-baas device_provider → select claw service."""
        claw_service = MagicMock()
        baas_service = MagicMock()
        selector = BotServiceSelector(claw_service, baas_service)

        mock_binding = MagicMock()
        mock_binding.device_provider = "arca"

        result = selector.select(mock_binding)

        assert result == claw_service

    def test_select_baas_when_baas_provider(self):
        """baas device_provider → select baas service."""
        claw_service = MagicMock()
        baas_service = MagicMock()
        selector = BotServiceSelector(claw_service, baas_service)

        mock_binding = MagicMock()
        mock_binding.device_provider = "baas"

        result = selector.select(mock_binding)

        assert result == baas_service


# ── validate_policy ───────────────────────────────────────────


class TestValidatePolicy:
    def test_no_policy_returns_target_bot_id(self):
        """None policy → 允许所有，返回原始 target_bot_id。"""
        record = _make_api_key_record(policy=None)
        result = validate_policy(record, "bot-1:entity-1")
        assert result == "bot-1:entity-1"

    def test_empty_policy_string_returns_target_bot_id(self):
        record = _make_api_key_record(policy="")
        result = validate_policy(record, "bot-1:entity-1")
        assert result == "bot-1:entity-1"

    def test_empty_allowed_bots_list_denies_all(self):
        """Empty allowed_bots → deny all (fail-closed)，返回 403。"""
        record = _make_api_key_record(policy='{"allowed_bots": []}')
        with pytest.raises(HTTPException) as exc:
            validate_policy(record, "any-bot")
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_none_sentinel_denies_all(self):
        """Lone ['NONE'] sentinel → deny all (normalized to empty)。返回 403。"""
        record = _make_api_key_record(policy='{"allowed_bots": ["NONE"]}')
        with pytest.raises(HTTPException) as exc:
            validate_policy(record, "any-bot")
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_explicit_allow_all(self):
        """allowed_bots 含 '*' → 允许所有 bot，返回原始 target_bot_id。"""
        record = _make_api_key_record(policy='{"allowed_bots": ["*"]}')
        result = validate_policy(record, "any-bot")
        assert result == "any-bot"

    def test_no_allowed_bots_key_allows_all(self):
        """Policy without allowed_bots key → legacy allow-all，返回原始 target_bot_id。"""
        record = _make_api_key_record(policy='{"other_key": "value"}')
        result = validate_policy(record, "any-bot")
        assert result == "any-bot"

    def test_bot_id_in_allowed_list_returns_matched_bot_id(self):
        """匹配时返回 allowed_bots 中的权威 bot_id。"""
        record = _make_api_key_record(
            policy='{"allowed_bots": ["bot-1:entity-1", "bot-2:entity-2"]}'
        )
        result = validate_policy(record, "bot-1:entity-1")
        assert result == "bot-1:entity-1"

    def test_non_exact_match_returns_canonical_bot_id(self):
        """非精确匹配（非 default 模式只匹配 real_bot_id），返回 allowed_bots 中的权威 bot_id。"""
        record = _make_api_key_record(policy='{"allowed_bots": ["bot-1:entity-1"]}')
        result = validate_policy(record, "bot-1:any-entity")
        # 请求传入 target_bot_id="bot-1:any-entity"，但返回的是 policy 中的 "bot-1:entity-1"
        assert result == "bot-1:entity-1"

    def test_bot_id_not_in_allowed_list_raises_403(self):
        record = _make_api_key_record(policy='{"allowed_bots": ["bot-1:entity-1"]}')
        with pytest.raises(HTTPException) as exc:
            validate_policy(record, "bot-unauthorized")
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc.value.detail["code"] == OpenAPICode.FORBIDDEN
        assert "bot-unauthorized" in exc.value.detail["message"]

    def test_default_requires_full_match(self):
        """default 模式需要完整匹配"""
        record = _make_api_key_record(policy='{"allowed_bots": ["default:entity-1"]}')
        with pytest.raises(HTTPException) as exc:
            validate_policy(record, "default:entity-2")
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_invalid_json_policy_denies_all(self):
        """Invalid JSON → fail-closed deny all，返回 403。"""
        record = _make_api_key_record(policy="{invalid json}")
        with pytest.raises(HTTPException) as exc:
            validate_policy(record, "any-bot")
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_policy_with_non_dict_parsed_value_denies_all(self):
        """parse_policy returns empty (non-dict) → fail-closed deny all，返回 403。"""
        with patch(
            "secbaas.community.adapters.web.routers.open_api.dependencies.parse_policy",
            return_value=MagicMock(allowed_bots=[]),
        ):
            record = _make_api_key_record(policy='["list"]')
            with pytest.raises(HTTPException) as exc:
                validate_policy(record, "any-bot")
            assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_multiple_bots_in_policy(self):
        """Ensure any matching bot_id passes and returns the matched entry."""
        record = _make_api_key_record(policy='{"allowed_bots": ["a:1", "b:2", "c:3"]}')
        # all three should pass and return the canonical form
        for bid in ["a:1", "b:2", "c:3"]:
            result = validate_policy(record, bid)
            assert result == bid

    def test_multiple_bots_one_unauthorized(self):
        """Only the listed ones pass."""
        record = _make_api_key_record(policy='{"allowed_bots": ["a:1", "b:2"]}')
        with pytest.raises(HTTPException):
            validate_policy(record, "d:4")
