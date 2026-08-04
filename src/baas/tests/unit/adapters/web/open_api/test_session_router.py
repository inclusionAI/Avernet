"""Unit tests for session_router.py.

Uses AsyncMock to invoke handler functions directly,
NOT TestClient. Covers get_session and get_session_messages
endpoints plus _check_app_type helper.

NOTE: When calling FastAPI handlers directly, Query defaults are not
applied — they arrive as FieldInfo objects.  All Query parameters
(bot_id, lifecycle_stage, limit) must be passed explicitly.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status

from secbaas.community.adapters.web.routers.open_api.session_router import (
    _check_app_type,
    get_session,
    get_session_messages,
    list_sessions,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import (
    BotChatContext,
    BotNotFoundError,
    BotServiceError,
    MessageInfo,
    SessionInfo,
    SessionNotFoundError,
)
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.core.service.bot_run import BotBindingNotFoundError, BotRunner

# ── helpers ──────────────────────────────────────────────────

ROUTER = "secbaas.community.adapters.web.routers.open_api.session_router"
RESOLVE_PATH = f"{ROUTER}.resolve_bot_id_from_api_key"
POLICY_PATH = f"{ROUTER}.validate_policy"

BOT_ID = "bot-1:entity-1"
SESSION_ID = "sess-001"

# Default Query-parameter values used when calling handlers directly.
# FastAPI Query defaults are NOT applied on direct invocation.
DEFAULT_LIFECYCLE_STAGE = "online"
DEFAULT_LIMIT = 1000


def _make_api_key_record(app_type="system", app_id="bot-1:entity-1", tenant="t1"):
    return APIKeyRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="h",
        api_key_prefix="kp-001",
        key_name="k",
        app_id=app_id,
        app_type=app_type,
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="o",
        tenant=tenant,
        env="test",
        creator="c",
        modifier=None,
        policy=None,
    )


def _make_context():
    return BotChatContext(
        api_key_prefix="kp-001",
        app_id="bot-1:entity-1",
        app_type="system",
        iam_token=None,
        tenant="t1",
    )


def _make_session_info(**overrides):
    defaults = {
        "session_id": SESSION_ID,
        "bot_id": BOT_ID,
        "status": "active",
        "created_at": datetime(2025, 1, 15, 10, 30, 0),
        "updated_at": datetime(2025, 1, 15, 11, 0, 0),
    }
    defaults.update(overrides)
    return SessionInfo(**defaults)


def _make_message_info(**overrides):
    defaults = {
        "id": "msg-001",
        "session_id": SESSION_ID,
        "role": "user",
        "content": "Hello",
        "meta": None,
        "created_at": "2025-01-15T10:30:00Z",
        "history_meta": None,
    }
    defaults.update(overrides)
    return MessageInfo(**defaults)


# ── _check_app_type ──────────────────────────────────────────


class TestCheckAppType:
    @pytest.mark.parametrize("app_type", ["system", "app", "bot"])
    def test_allowed_app_type_passes(self, app_type):
        """Allowed app_types should not raise."""
        _check_app_type(_make_api_key_record(app_type=app_type))

    @pytest.mark.parametrize("app_type", ["user", "unknown", None])
    def test_disallowed_app_type_raises_403(self, app_type):
        """Disallowed app_types should raise 403."""
        with pytest.raises(HTTPException) as exc:
            _check_app_type(_make_api_key_record(app_type=app_type))
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc.value.detail["code"] == OpenAPICode.FORBIDDEN


# ── bot_id resolution (shared by both endpoints) ─────────────


class TestBotIdResolution:
    """bot_id 解析逻辑：显式传入优先，bot 类型自动解析，其余必填。"""

    # --- get_session ---

    @pytest.mark.asyncio
    async def test_get_session_explicit_bot_id_used_directly(self):
        """显式传入 bot_id 时直接使用。"""
        api_key = _make_api_key_record(app_type="bot", app_id="bot-default:entity-1")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            await get_session(
                session_id=SESSION_ID,
                bot_id="bot-explicit:entity-1",
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )

        mock_runner.get_session_info.assert_called_once()
        assert (
            mock_runner.get_session_info.call_args[1]["bot_id"]
            == "bot-explicit:entity-1"
        )

    @pytest.mark.asyncio
    async def test_get_session_bot_app_type_resolves_from_api_key(self):
        """app_type='bot' 不传 bot_id 时从 API Key 解析。"""
        api_key = _make_api_key_record(app_type="bot", app_id="bot-1:entity-1")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with (
            patch(RESOLVE_PATH, return_value=BOT_ID) as mock_resolve,
            patch(POLICY_PATH),
        ):
            await get_session(
                session_id=SESSION_ID,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_resolve.assert_called_once_with(api_key)

    @pytest.mark.asyncio
    async def test_get_session_system_app_type_without_bot_id_returns_400(self):
        """app_type='system' 不传 bot_id 返回 400。"""
        api_key = _make_api_key_record(app_type="system")
        mock_runner = AsyncMock(spec=BotRunner)

        with pytest.raises(HTTPException) as exc:
            await get_session(
                session_id=SESSION_ID,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_get_session_app_app_type_without_bot_id_returns_400(self):
        """app_type='app' 不传 bot_id 也返回 400。"""
        api_key = _make_api_key_record(app_type="app")
        mock_runner = AsyncMock(spec=BotRunner)

        with pytest.raises(HTTPException) as exc:
            await get_session(
                session_id=SESSION_ID,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    # --- get_session_messages ---

    @pytest.mark.asyncio
    async def test_get_session_messages_explicit_bot_id_used_directly(self):
        """显式传入 bot_id 时直接使用。"""
        api_key = _make_api_key_record(app_type="bot", app_id="bot-default:entity-1")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id="bot-explicit:entity-1",
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert (
            mock_runner.get_session_messages.call_args[1]["bot_id"]
            == "bot-explicit:entity-1"
        )

    @pytest.mark.asyncio
    async def test_get_session_messages_bot_app_type_resolves_from_api_key(self):
        """app_type='bot' 不传 bot_id 时从 API Key 解析。"""
        api_key = _make_api_key_record(app_type="bot", app_id="bot-1:entity-1")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with (
            patch(RESOLVE_PATH, return_value=BOT_ID) as mock_resolve,
            patch(POLICY_PATH),
        ):
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_resolve.assert_called_once_with(api_key)

    @pytest.mark.asyncio
    async def test_get_session_messages_system_without_bot_id_returns_400(self):
        """app_type='system' 不传 bot_id 返回 400。"""
        api_key = _make_api_key_record(app_type="system")
        mock_runner = AsyncMock(spec=BotRunner)

        with pytest.raises(HTTPException) as exc:
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


# ── validate_policy conditional ──────────────────────────────


class TestValidatePolicyConditional:
    """app_type='bot' 时跳过 validate_policy，其余类型调用。"""

    @pytest.mark.asyncio
    async def test_get_session_bot_type_skips_validate_policy(self):
        api_key = _make_api_key_record(app_type="bot", app_id=BOT_ID)
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with (
            patch(POLICY_PATH) as mock_policy,
            patch(RESOLVE_PATH, return_value=BOT_ID),
        ):
            await get_session(
                session_id=SESSION_ID,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_session_system_type_calls_validate_policy(self):
        api_key = _make_api_key_record(app_type="system")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with patch(POLICY_PATH, return_value=BOT_ID) as mock_policy:
            await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_called_once_with(api_key, BOT_ID)

    @pytest.mark.asyncio
    async def test_get_session_app_type_calls_validate_policy(self):
        api_key = _make_api_key_record(app_type="app")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with patch(POLICY_PATH, return_value=BOT_ID) as mock_policy:
            await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_called_once_with(api_key, BOT_ID)

    @pytest.mark.asyncio
    async def test_get_session_messages_bot_type_skips_validate_policy(self):
        api_key = _make_api_key_record(app_type="bot", app_id=BOT_ID)
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with (
            patch(POLICY_PATH) as mock_policy,
            patch(RESOLVE_PATH, return_value=BOT_ID),
        ):
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_session_messages_system_type_calls_validate_policy(self):
        api_key = _make_api_key_record(app_type="system")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID) as mock_policy:
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_called_once_with(api_key, BOT_ID)


# ── lifecycle_stage passthrough ──────────────────────────────


class TestLifecycleStage:
    """lifecycle_stage 参数应通过 metadata 传递给 BotRunner。"""

    @pytest.mark.asyncio
    async def test_get_session_default_online(self):
        """默认 lifecycle_stage='online'。"""
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage="online",
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        mock_runner.get_session_info.assert_called_once_with(
            bot_id=BOT_ID,
            session_id=SESSION_ID,
            context=_make_context(),
            metadata={"bot_options": {"lifecycle_stage": "online"}},
        )

    @pytest.mark.asyncio
    async def test_get_session_explicit_draft(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage="draft",
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        mock_runner.get_session_info.assert_called_once_with(
            bot_id=BOT_ID,
            session_id=SESSION_ID,
            context=_make_context(),
            metadata={"bot_options": {"lifecycle_stage": "draft"}},
        )

    @pytest.mark.asyncio
    async def test_get_session_messages_default_online(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage="online",
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        mock_runner.get_session_messages.assert_called_once_with(
            bot_id=BOT_ID,
            session_id=SESSION_ID,
            context=_make_context(),
            metadata={"bot_options": {"lifecycle_stage": "online"}},
        )

    @pytest.mark.asyncio
    async def test_get_session_messages_explicit_verify(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage="verify",
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        mock_runner.get_session_messages.assert_called_once_with(
            bot_id=BOT_ID,
            session_id=SESSION_ID,
            context=_make_context(),
            metadata={"bot_options": {"lifecycle_stage": "verify"}},
        )


# ── get_session ──────────────────────────────────────────────


class TestGetSession:
    """get_session 端点核心逻辑测试。"""

    @pytest.mark.asyncio
    async def test_success_returns_session_info(self):
        api_key = _make_api_key_record()
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.code == 0
        assert result.message == "success"
        assert result.data.session_id == SESSION_ID
        assert result.data.bot_id == BOT_ID
        assert result.data.status == "active"

    @pytest.mark.asyncio
    async def test_preserves_created_at_and_updated_at(self):
        created = datetime(2025, 3, 10, 8, 0, 0)
        updated = datetime(2025, 3, 11, 9, 30, 0)
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            return_value=_make_session_info(created_at=created, updated_at=updated),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )
        assert result.data.created_at == created
        assert result.data.updated_at == updated

    @pytest.mark.asyncio
    async def test_non_allowed_app_type_returns_403(self):
        """app_type 不在 ('system','app','bot') -> 403。"""
        api_key = _make_api_key_record(app_type="user")
        mock_runner = AsyncMock(spec=BotRunner)

        with pytest.raises(HTTPException) as exc:
            await get_session(
                session_id=SESSION_ID,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_session_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            side_effect=SessionNotFoundError(SESSION_ID),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session(
                    session_id=SESSION_ID,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_bot_binding_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            side_effect=BotBindingNotFoundError(BOT_ID),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session(
                    session_id=SESSION_ID,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            side_effect=BotNotFoundError("bot-1"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session(
                    session_id=SESSION_ID,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            side_effect=BotServiceError("service unavailable"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session(
                    session_id=SESSION_ID,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_info = AsyncMock(
            side_effect=RuntimeError("unexpected failure"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session(
                    session_id=SESSION_ID,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc.value.detail["code"] == 50001
        assert "Internal server error" in exc.value.detail["message"]


# ── get_session_messages ─────────────────────────────────────


class TestGetSessionMessages:
    """get_session_messages 端点核心逻辑测试。"""

    @pytest.mark.asyncio
    async def test_success_with_messages(self):
        messages = [
            _make_message_info(id="msg-001", role="user", content="Hello"),
            _make_message_info(id="msg-002", role="assistant", content="Hi there"),
        ]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=messages)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.code == 0
        assert result.data.session_id == SESSION_ID
        assert result.data.total == 2
        assert result.data.has_more is False
        assert len(result.data.messages) == 2
        assert result.data.messages[0].id == "msg-001"
        assert result.data.messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_success_with_empty_messages(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.code == 0
        assert result.data.total == 0
        assert result.data.has_more is False
        assert result.data.messages == []

    @pytest.mark.asyncio
    async def test_limit_pagination(self):
        messages = [
            _make_message_info(id=f"msg-{i:03d}", role="user", content=f"msg {i}")
            for i in range(5)
        ]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=messages)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session_messages(
                session_id=SESSION_ID,
                limit=2,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.data.total == 5
        assert result.data.has_more is True
        assert len(result.data.messages) == 2

    @pytest.mark.asyncio
    async def test_limit_equal_to_count_no_has_more(self):
        messages = [
            _make_message_info(id=f"msg-{i:03d}", role="user", content=f"msg {i}")
            for i in range(3)
        ]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=messages)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session_messages(
                session_id=SESSION_ID,
                limit=3,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.data.total == 3
        assert result.data.has_more is False

    @pytest.mark.asyncio
    async def test_message_created_at_preserved(self):
        """created_at from MessageInfo is preserved in response."""
        messages = [
            _make_message_info(
                id="msg-001",
                role="user",
                content="Hello",
                created_at="2025-01-15T10:30:00Z",
            ),
        ]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=messages)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.data.messages[0].created_at == "2025-01-15T10:30:00Z"

    @pytest.mark.asyncio
    async def test_message_with_none_created_at(self):
        """Message with created_at=None results in None in response."""
        messages = [
            _make_message_info(
                id="msg-001", role="user", content="Hello", created_at=None
            ),
        ]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(return_value=messages)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.data.messages[0].created_at is None

    @pytest.mark.asyncio
    async def test_non_allowed_app_type_returns_403(self):
        api_key = _make_api_key_record(app_type="user")
        mock_runner = AsyncMock(spec=BotRunner)

        with pytest.raises(HTTPException) as exc:
            await get_session_messages(
                session_id=SESSION_ID,
                limit=DEFAULT_LIMIT,
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_session_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(
            side_effect=SessionNotFoundError(SESSION_ID),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session_messages(
                    session_id=SESSION_ID,
                    limit=DEFAULT_LIMIT,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_bot_binding_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(
            side_effect=BotBindingNotFoundError(BOT_ID),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session_messages(
                    session_id=SESSION_ID,
                    limit=DEFAULT_LIMIT,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(
            side_effect=BotNotFoundError("bot-1"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session_messages(
                    session_id=SESSION_ID,
                    limit=DEFAULT_LIMIT,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(
            side_effect=BotServiceError("service unavailable"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session_messages(
                    session_id=SESSION_ID,
                    limit=DEFAULT_LIMIT,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.get_session_messages = AsyncMock(
            side_effect=RuntimeError("unexpected failure"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await get_session_messages(
                    session_id=SESSION_ID,
                    limit=DEFAULT_LIMIT,
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc.value.detail["code"] == 50001
        assert "Internal server error" in exc.value.detail["message"]


# ── list_sessions ─────────────────────────────────────────────


class TestListSessions:
    """list_sessions 端点核心逻辑测试。"""

    @pytest.mark.asyncio
    async def test_bot_id_resolution_explicit(self):
        """显式传入 bot_id 时直接使用。"""
        api_key = _make_api_key_record(app_type="bot", app_id="bot-default:entity-1")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            await list_sessions(
                bot_id="bot-explicit:entity-1",
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )

        mock_runner.list_sessions.assert_called_once()
        assert (
            mock_runner.list_sessions.call_args[1]["bot_id"]
            == "bot-explicit:entity-1"
        )

    @pytest.mark.asyncio
    async def test_bot_id_resolution_from_api_key(self):
        """app_type='bot' 不传 bot_id 时从 API Key 解析。"""
        api_key = _make_api_key_record(app_type="bot", app_id="bot-1:entity-1")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with (
            patch(RESOLVE_PATH, return_value=BOT_ID) as mock_resolve,
            patch(POLICY_PATH),
        ):
            await list_sessions(
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_resolve.assert_called_once_with(api_key)

    @pytest.mark.asyncio
    async def test_missing_bot_id_system_type_returns_400(self):
        """app_type='system' 不传 bot_id 返回 400。"""
        api_key = _make_api_key_record(app_type="system")

        with pytest.raises(HTTPException) as exc:
            await list_sessions(
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=AsyncMock(spec=BotRunner),
            )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_disallowed_app_type_returns_403(self):
        """app_type 不在 ('system','app','bot') -> 403。"""
        api_key = _make_api_key_record(app_type="user")

        with pytest.raises(HTTPException) as exc:
            await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=AsyncMock(spec=BotRunner),
            )
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_bot_type_skips_validate_policy(self):
        """app_type='bot' 时跳过 validate_policy。"""
        api_key = _make_api_key_record(app_type="bot", app_id=BOT_ID)
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with (
            patch(POLICY_PATH) as mock_policy,
            patch(RESOLVE_PATH, return_value=BOT_ID),
        ):
            await list_sessions(
                bot_id=None,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_type_calls_validate_policy(self):
        """app_type='system' 时调用 validate_policy。"""
        api_key = _make_api_key_record(app_type="system")
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID) as mock_policy:
            await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=api_key,
                context=_make_context(),
                bot_runner=mock_runner,
            )
            mock_policy.assert_called_once_with(api_key, BOT_ID)

    @pytest.mark.asyncio
    async def test_success_with_sessions(self):
        """成功返回会话列表。"""
        sessions = [
            _make_session_info(session_id="sess-001"),
            _make_session_info(session_id="sess-002"),
        ]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=sessions)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.code == 0
        assert result.message == "success"
        assert len(result.data.items) == 2
        assert result.data.items[0].session_id == "sess-001"
        assert result.data.items[1].session_id == "sess-002"
        assert result.data.has_more is False

    @pytest.mark.asyncio
    async def test_success_with_empty_list(self):
        """空会话列表成功返回。"""
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=0,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.code == 0
        assert result.data.items == []
        assert result.data.total == 0
        assert result.data.has_more is False

    @pytest.mark.asyncio
    async def test_pagination_has_more(self):
        """limit+1 策略：结果超过 limit 时 has_more=True。"""
        sessions = [_make_session_info(session_id=f"sess-{i:03d}") for i in range(3)]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=sessions)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=2,
                offset=0,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.data.has_more is True
        assert len(result.data.items) == 2
        # Called with limit+1=3
        assert mock_runner.list_sessions.call_args[1]["limit"] == 3

    @pytest.mark.asyncio
    async def test_pagination_no_has_more(self):
        """结果不超过 limit 时 has_more=False。"""
        sessions = [_make_session_info(session_id=f"sess-{i:03d}") for i in range(2)]
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=sessions)

        with patch(POLICY_PATH, return_value=BOT_ID):
            result = await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=2,
                offset=0,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert result.data.has_more is False
        assert len(result.data.items) == 2

    @pytest.mark.asyncio
    async def test_offset_passed_through(self):
        """offset 参数传递给 BotRunner。"""
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                limit=20,
                offset=10,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert mock_runner.list_sessions.call_args[1]["offset"] == 10

    @pytest.mark.asyncio
    async def test_lifecycle_stage_passed_in_metadata(self):
        """lifecycle_stage 通过 metadata 传递给 BotRunner。"""
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(return_value=[])

        with patch(POLICY_PATH, return_value=BOT_ID):
            await list_sessions(
                bot_id=BOT_ID,
                lifecycle_stage="draft",
                limit=20,
                offset=0,
                api_key_record=_make_api_key_record(),
                context=_make_context(),
                bot_runner=mock_runner,
            )

        assert mock_runner.list_sessions.call_args[1]["metadata"] == {
            "bot_options": {"lifecycle_stage": "draft"}
        }

    @pytest.mark.asyncio
    async def test_bot_binding_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(
            side_effect=BotBindingNotFoundError(BOT_ID),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await list_sessions(
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    limit=20,
                    offset=0,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(
            side_effect=BotNotFoundError("bot-1"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await list_sessions(
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    limit=20,
                    offset=0,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail["code"] == 60001

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(
            side_effect=BotServiceError("service unavailable"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await list_sessions(
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    limit=20,
                    offset=0,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.value.detail["code"] == OpenAPICode.BUSINESS_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        mock_runner = AsyncMock(spec=BotRunner)
        mock_runner.list_sessions = AsyncMock(
            side_effect=RuntimeError("unexpected failure"),
        )

        with patch(POLICY_PATH, return_value=BOT_ID):
            with pytest.raises(HTTPException) as exc:
                await list_sessions(
                    bot_id=BOT_ID,
                    lifecycle_stage=DEFAULT_LIFECYCLE_STAGE,
                    limit=20,
                    offset=0,
                    api_key_record=_make_api_key_record(),
                    context=_make_context(),
                    bot_runner=mock_runner,
                )
        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc.value.detail["code"] == 50001
        assert "Internal server error" in exc.value.detail["message"]
