"""Tests for ExpertChatService."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from agentclaw.community.core.expert_chat.services.expert_chat_service import ExpertChatService
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError,
    BotNotActiveError,
    BotNotPublishedError,
    SessionCreateError,
    ConnectionError,
    ChatPermissionError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    mock_repository,
    mock_bot_repo,
    mock_device_provider,
    mock_baas_service=None,
    *,
    mock_resolver=None,
    mock_collaborator_service=None,
    mock_transport=None,
    mock_instance_service=None,
):
    """构造 ExpertChatService。

    Task 2.5 新增 ``resolver`` / ``collaborator_service`` 入参。默认值用
    MagicMock 兜底:
    - resolver 返回 ctx,其 ``conn_info`` 复刻旧 v2 的 DEVICE_CONN(覆盖
      _get_connection 透传字段断言);
    - collaborator_service 默认 has_permission=True (历史 case 大多 owner
      自调或 user1 vs owner1,但旧测试不区分,保持兼容)。
    Caller 模式新增 ``instance_service`` 入参。
    """
    # Default resolver returns a ctx whose conn_info matches DEVICE_CONN
    if mock_resolver is None:
        mock_resolver = MagicMock()
        # 默认: ctx.conn_info 复刻 v2 mock 的返回值,保持透传断言兼容
        default_conn = mock_device_provider.get_device_connection_v2.return_value
        default_ctx = MagicMock()
        default_ctx.conn_info = default_conn if isinstance(default_conn, dict) else dict(DEVICE_CONN)
        # 两个入口共享同一个 ctx — _get_connection 按 bot["binding_id"] 是否存在分流,
        # fixture 默认两条路径都返一致结果,case 不需要关心走哪条。
        mock_resolver.resolve_for_bot = MagicMock(return_value=default_ctx)
        mock_resolver.resolve_for_binding = MagicMock(return_value=default_ctx)
    if mock_collaborator_service is None:
        mock_collaborator_service = MagicMock()
        mock_collaborator_service.check_collaborator_permission = MagicMock(
            return_value={"has_permission": True, "level": "MEMBER", "level_value": 1}
        )
    if mock_transport is None:
        mock_transport = MagicMock()
        mock_transport.invoke = AsyncMock(return_value={"data": {"id": "raw-session-id"}})
    if mock_instance_service is None:
        mock_instance_service = MagicMock()
        mock_instance_service.get_caller_connection = AsyncMock()

    return ExpertChatService(
        repository=mock_repository,
        bot_repo=mock_bot_repo,
        device_provider=mock_device_provider,
        baas_service=mock_baas_service or MagicMock(),
        resolver=mock_resolver,
        collaborator_service=mock_collaborator_service,
        transport=mock_transport,
        instance_service=mock_instance_service,
    )


ACTIVE_BOT = {
    "bot_id": "bot1",
    "bot_name": "Test Bot",
    "owner_id": "owner1",
    "owner_name": "Owner",
    "status": "ACTIVE",
    "binding_id": "binding-123",
}

DEVICE_CONN = {
    "url": "http://localhost:8080",
    "headers": {},
    "use_proxy": False,
    "engine_type": "openclaw",
    "type": "local",  # 'local' or 'remote', comes from device service
}


# ---------------------------------------------------------------------------
# add_chat_bot
# ---------------------------------------------------------------------------

class TestAddChatBot:

    def test_success(self, mock_repository, mock_bot_repo, mock_device_provider):
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)
        result = svc.add_chat_bot("user1", "bot1", "owner1")

        assert result["status"] == "ACTIVE"
        mock_bot_repo.get_by_id_and_owner.assert_called_once_with("bot1", "owner1")
        mock_repository.add_chat_bot.assert_called_once_with("user1", "bot1", "owner1")

    def test_bot_not_found(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with pytest.raises(BotNotFoundError):
            svc.add_chat_bot("user1", "bot1", "owner1")

    def test_bot_not_active(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "status": "INACTIVE"}
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with pytest.raises(BotNotActiveError):
            svc.add_chat_bot("user1", "bot1", "owner1")

    def test_bot_not_published(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot1", "status": "ACTIVE", "binding_id": None
        }
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with pytest.raises(BotNotPublishedError):
            svc.add_chat_bot("user1", "bot1", "owner1")


# ---------------------------------------------------------------------------
# list_chat_bots
# ---------------------------------------------------------------------------

class TestListChatBots:

    def test_success_with_bots(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_repository.list_chat_bots.return_value = [
            {"bot_id": "bot1", "owner_id": "owner1"}
        ]
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot1",
            "owner_id": "owner1",
            "bot_name": "Test Bot",
            "owner_name": "Test Owner",
            "status": "ACTIVE",
            "binding_id": 12345,
        }
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        result = svc.list_chat_bots("user1")

        assert len(result) == 1
        assert result[0]["bot_id"] == "bot1"
        assert result[0]["bot_name"] == "Test Bot"
        assert result[0]["binding_available"] is True

    def test_empty(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_repository.list_chat_bots.return_value = []
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        assert svc.list_chat_bots("user1") == []

    def test_bot_not_found_in_lookup_is_skipped(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Bots that disappeared from bot_repo should be silently skipped."""
        mock_repository.list_chat_bots.return_value = [
            {"bot_id": "ghost", "owner_id": "owner1"}
        ]
        mock_bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        result = svc.list_chat_bots("user1")

        assert result == []

    def test_bot_without_binding_id_shows_unavailable(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_repository.list_chat_bots.return_value = [
            {"bot_id": "bot1", "owner_id": "owner1"}
        ]
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot1",
            "bot_name": "Bot",
            "owner_name": "Owner",
            "status": "ACTIVE",
            "binding_id": None,
        }
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        result = svc.list_chat_bots("user1")

        assert result[0]["binding_available"] is False

    def test_bot_name_falls_back_to_bot_id(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_repository.list_chat_bots.return_value = [
            {"bot_id": "bot1", "owner_id": "owner1"}
        ]
        mock_bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot1",
            "bot_name": None,
            "owner_name": "Owner",
            "status": "ACTIVE",
            "binding_id": "b1",
        }
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        result = svc.list_chat_bots("user1")

        assert result[0]["bot_name"] == "bot1"


# ---------------------------------------------------------------------------
# remove_chat_bot
# ---------------------------------------------------------------------------

class TestRemoveChatBot:

    @pytest.mark.asyncio
    async def test_success(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_repository.remove_chat_bot.return_value = True
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)
        svc.delete_chat_session = AsyncMock(return_value=True)

        result = await svc.remove_chat_bot("user1", "bot1", "owner1")

        assert result is True
        mock_repository.remove_chat_bot.assert_called_once_with("user1", "bot1", "owner1")

    @pytest.mark.asyncio
    async def test_bot_not_found_during_session_delete_is_ignored(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """BotNotFoundError from delete_chat_session should not propagate."""
        mock_repository.remove_chat_bot.return_value = True
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)
        svc.delete_chat_session = AsyncMock(side_effect=BotNotFoundError("not found"))

        result = await svc.remove_chat_bot("user1", "bot1", "owner1")

        assert result is True

    @pytest.mark.asyncio
    async def test_generic_exception_during_session_delete_is_logged_not_raised(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """Non-BotNotFoundError exceptions should be caught and logged."""
        mock_repository.remove_chat_bot.return_value = True
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)
        svc.delete_chat_session = AsyncMock(side_effect=RuntimeError("boom"))

        result = await svc.remove_chat_bot("user1", "bot1", "owner1")

        assert result is True


# ---------------------------------------------------------------------------
# get_chat_session
# ---------------------------------------------------------------------------

class TestGetChatSession:

    @pytest.mark.asyncio
    async def test_bot_not_found_raises(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with pytest.raises(BotNotFoundError):
            await svc.get_chat_session("user1", "bot1", "owner1")

    @pytest.mark.asyncio
    async def test_bot_not_active_raises(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "status": "DISABLED"}
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with pytest.raises(BotNotActiveError):
            await svc.get_chat_session("user1", "bot1", "owner1")

    @pytest.mark.asyncio
    async def test_existing_valid_session_reused(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = ACTIVE_BOT
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = "session:existing-123"
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_check_session_exists", new=AsyncMock(return_value=True)):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        assert result["session_key"] == "session:existing-123"
        assert result["is_new"] is False
        assert result["connection"]["type"] == "local"  # passthrough from device service

    @pytest.mark.asyncio
    async def test_stale_session_triggers_new_creation(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = ACTIVE_BOT
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = "session:stale"
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_check_session_exists", new=AsyncMock(return_value=False)):
            with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new-456")):
                result = await svc.get_chat_session("user1", "bot1", "owner1")

        assert result["session_key"] == "session:new-456"
        assert result["is_new"] is True
        mock_repository.delete_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_existing_session_creates_new(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = ACTIVE_BOT
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:brand-new")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        assert result["session_key"] == "session:brand-new"
        assert result["is_new"] is True
        mock_repository.save_session.assert_called_once_with("user1", "bot1", "owner1", "session:brand-new")

    @pytest.mark.asyncio
    async def test_service_bot_fetches_binding_id_from_baas(self, mock_repository, mock_bot_repo, mock_device_provider):
        """service bot 应该从 BaasService 获取 binding_id，而不是从 bot 对象获取"""
        service_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "binding_id": None,  # service bot 的 binding_id 不在 bot 对象中
            "bot_type": "service",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = service_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)
        mock_baas = MagicMock()
        mock_baas.get_bind_id.return_value = "baas-binding-123"
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_baas)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证 BaasService.get_bind_id 被正确调用
        mock_baas.get_bind_id.assert_called_once_with(
            bot_id="bot1",
            owner_id="owner1",
            bot_type="service",
            publish_status="success",
        )
        # 验证 binding_id 被正确设置到 bot 对象中
        assert service_bot["binding_id"] == "baas-binding-123"
        assert result["session_key"] == "session:new"
        assert result["is_new"] is True

    @pytest.mark.asyncio
    async def test_personal_bot_uses_binding_id_from_bot(self, mock_repository, mock_bot_repo, mock_device_provider):
        """personal bot 应该直接使用 bot 对象中的 binding_id，不调用 BaasService"""
        personal_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "binding_id": "personal-binding-456",
            "bot_type": "personal",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = personal_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)
        mock_baas_service = MagicMock()
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_baas_service=mock_baas_service,
        )

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证 BaasService 没有被调用 (personal bot uses bot.binding_id directly)
        mock_baas_service.assert_not_called()
        # 验证 binding_id 保持不变
        assert personal_bot["binding_id"] == "personal-binding-456"
        assert result["session_key"] == "session:new"

    # ========== Caller Mode Tests ==========

    @pytest.mark.asyncio
    async def test_caller_mode_with_need_poll(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Caller 模式：容器正在创建，需要轮询"""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",  # Caller 模式
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]

        # Mock instance_service 返回需要轮询
        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 123}},
            "connection": None,
            "need_poll": True,
        })

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证返回值
        assert result["session_key"] is None
        assert result["is_new"] is True
        assert result["connection"] is None
        assert result["need_poll"] is True

        # 验证 get_caller_connection 被正确调用
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="user1", bot_id="bot1", owner_id="owner1", iam_token=None
        )

    @pytest.mark.asyncio
    async def test_caller_mode_container_ready_creates_new_session(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Caller 模式：容器就绪，创建新 session"""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None  # 没有 session

        # Mock instance_service 返回容器就绪
        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 456}},
            "connection": {"ws_url": "ws://test", "token": "abc"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:caller-new")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证返回值
        assert result["session_key"] == "session:caller-new"
        assert result["is_new"] is True
        # connection 来自 _get_connection，使用 DEVICE_CONN 格式
        assert result["connection"]["type"] == "local"

        # 验证 binding_id 被正确设置
        assert caller_bot["binding_id"] == 456

        # 验证 get_caller_connection 被正确调用
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="user1", bot_id="bot1", owner_id="owner1", iam_token=None
        )

    @pytest.mark.asyncio
    async def test_caller_mode_container_ready_reuses_session(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Caller 模式：容器就绪，复用已有 session"""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = "session:caller-existing"

        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 789}},
            "connection": {"ws_url": "ws://test2", "token": "xyz"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        with patch.object(svc, "_check_session_exists", new=AsyncMock(return_value=True)):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证返回值
        assert result["session_key"] == "session:caller-existing"
        assert result["is_new"] is False
        # connection 来自 _get_connection，使用 DEVICE_CONN 格式
        assert result["connection"]["type"] == "local"

        # 验证 binding_id 被正确设置
        assert caller_bot["binding_id"] == 789

        # 验证 get_caller_connection 被正确调用
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="user1", bot_id="bot1", owner_id="owner1", iam_token=None
        )

    @pytest.mark.asyncio
    async def test_owner_mode_default_when_call_type_is_none(self, mock_repository, mock_bot_repo, mock_device_provider):
        """call_type 为 None 时默认走 Owner 模式"""
        bot_without_call_type = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "binding_id": "binding-123",
            # 没有 call_type 字段
        }
        mock_bot_repo.get_by_id_and_owner.return_value = bot_without_call_type
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:owner-mode")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证走的是 Owner 模式的逻辑（没有调用 instance_service）
        assert result["session_key"] == "session:owner-mode"
        assert result["is_new"] is True

    @pytest.mark.asyncio
    async def test_owner_mode_when_call_type_is_empty_string(self, mock_repository, mock_bot_repo, mock_device_provider):
        """call_type 为空字符串时走 Owner 模式（空字符串转为 None，parse 返回 OWNER）"""
        bot_with_empty_call_type = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "binding_id": "binding-123",
            "call_type": "",  # 空字符串
        }
        mock_bot_repo.get_by_id_and_owner.return_value = bot_with_empty_call_type
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:owner-mode")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证走的是 Owner 模式的逻辑（没有调用 instance_service）
        assert result["session_key"] == "session:owner-mode"
        assert result["is_new"] is True

    @pytest.mark.asyncio
    async def test_owner_mode_when_call_type_is_explicit_owner(self, mock_repository, mock_bot_repo, mock_device_provider):
        """call_type 显式为 "owner" 时走 Owner 模式"""
        bot_with_owner_call_type = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "binding_id": "binding-123",
            "call_type": "owner",  # 显式 owner
        }
        mock_bot_repo.get_by_id_and_owner.return_value = bot_with_owner_call_type
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:owner-mode")):
            result = await svc.get_chat_session("user1", "bot1", "owner1")

        # 验证走的是 Owner 模式的逻辑（没有调用 instance_service）
        assert result["session_key"] == "session:owner-mode"
        assert result["is_new"] is True

    @pytest.mark.asyncio
    async def test_caller_mode_binding_id_not_provided_raises_error(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Caller 模式：binding_id 未提供时抛出错误"""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None

        # Mock instance_service 返回没有 binding_id
        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {}},  # 没有 binding_id
            "connection": {"ws_url": "ws://test", "token": "abc"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:caller-new")):
            with pytest.raises(ConnectionError, match="服务未发布"):
                await svc.get_chat_session("user1", "bot1", "owner1")

    @pytest.mark.asyncio
    async def test_get_chat_session_passes_iam_token_to_caller_connection(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Caller 模式：iam_token 应该被传递到 get_caller_connection"""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
        mock_repository.get_session.return_value = None

        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 999}},
            "connection": {"ws_url": "ws://test", "token": "xyz"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        iam_token = "test-iam-token-12345"
        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new")):
            await svc.get_chat_session("user1", "bot1", "owner1", iam_token=iam_token)

        # 验证 iam_token 被传递给 get_caller_connection
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="user1",
            bot_id="bot1",
            owner_id="owner1",
            iam_token=iam_token
        )


# ---------------------------------------------------------------------------
# delete_chat_session
# ---------------------------------------------------------------------------

class TestDeleteChatSession:

    @pytest.mark.asyncio
    async def test_bot_not_found_raises(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with pytest.raises(BotNotFoundError):
            await svc.delete_chat_session("user1", "bot1", "owner1")

    @pytest.mark.asyncio
    async def test_no_session_returns_true(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = ACTIVE_BOT
        mock_repository.get_session.return_value = None
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        result = await svc.delete_chat_session("user1", "bot1", "owner1")

        assert result is True
        mock_repository.delete_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_deletes_adapter_and_local(self, mock_repository, mock_bot_repo, mock_device_provider):
        mock_bot_repo.get_by_id_and_owner.return_value = ACTIVE_BOT
        mock_repository.get_session.return_value = "session:abc"
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_delete_adapter_session", new=AsyncMock(return_value=None)):
            result = await svc.delete_chat_session("user1", "bot1", "owner1")

        assert result is True
        mock_repository.delete_session.assert_called_once_with("user1", "bot1", "owner1")

    @pytest.mark.asyncio
    async def test_adapter_delete_failure_still_deletes_local(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        mock_bot_repo.get_by_id_and_owner.return_value = ACTIVE_BOT
        mock_repository.get_session.return_value = "session:abc"
        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider)

        with patch.object(svc, "_delete_adapter_session", new=AsyncMock(side_effect=RuntimeError("adapter down"))):
            result = await svc.delete_chat_session("user1", "bot1", "owner1")

        assert result is True
        mock_repository.delete_session.assert_called_once()


# ---------------------------------------------------------------------------
# _get_connection
# ---------------------------------------------------------------------------

class TestGetConnection:
    """Task 2.5 改造后: _get_connection 不再调 v2,走 resolver。

    - 权限失败 → ChatPermissionError (覆盖见 test_expert_chat_permission.py)
    - resolver 抛 DeviceNotBoundError → 翻成 BotNotPublishedError
    - resolver 抛其他异常 → 翻成 ConnectionError 5001
    """

    def test_no_binding_id_raises_connection_error(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """binding_id 不存在时抛出 ConnectionError(5001)。"""
        resolver = MagicMock()
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
        )
        # bot 没有 binding_id
        bot = {"bot_id": "bot1", "owner_id": "user1", "public": "0", "binding_id": None}

        with pytest.raises(ConnectionError) as exc_info:
            svc._get_connection(bot, "user1")
        assert exc_info.value.error_code == "5002"
        # resolver 不应被调用
        resolver.resolve_for_binding.assert_not_called()
        resolver.resolve_for_bot.assert_not_called()

    def test_no_active_binding_raises_bot_not_published(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """resolver 抛 DeviceNotBoundError → 翻译为 BotNotPublishedError(契约不变)。"""
        from agentclaw.community.core.devices.services.device_context import DeviceNotBoundError

        resolver = MagicMock()
        resolver.resolve_for_binding = MagicMock(
            side_effect=DeviceNotBoundError("no active binding for binding_id=123")
        )
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
        )
        # owner 调自己的 bot → 权限直通,resolver 被调到
        bot = {"bot_id": "bot1", "owner_id": "user1", "public": "0", "binding_id": 123}

        with pytest.raises(BotNotPublishedError):
            svc._get_connection(bot, "user1")

    def test_success_passes_through_type(self, mock_repository, mock_bot_repo, mock_device_provider):
        """type 字段由 resolver 透传(类似旧 v2 行为)。"""
        resolver = MagicMock()
        ctx = MagicMock()
        ctx.conn_info = {
            "url": "http://localhost:8080",
            "headers": {},
            "use_proxy": False,
            "engine_type": "openclaw",
            "type": "local",  # 透传自 LocalConnInfoBuilder
        }
        resolver.resolve_for_binding = MagicMock(return_value=ctx)
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
        )
        bot = {"bot_id": "bot1", "owner_id": "user1", "public": "0", "binding_id": 123}

        conn = svc._get_connection(bot, "user1")

        assert conn["type"] == "local"
        assert conn["url"] == "http://localhost:8080"

    def test_success_with_remote_connection(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Remote 连接字段同样由 resolver 透传 type='remote'。"""
        resolver = MagicMock()
        ctx = MagicMock()
        ctx.conn_info = {
            "url": "http://proxy-host",
            "headers": {"X-Token": "abc"},
            "use_proxy": True,
            "sandbox_id": "sandbox-1",
            "engine_type": "openclaw",
            "type": "remote",  # 透传自 ArcaConnInfoBuilder
        }
        resolver.resolve_for_binding = MagicMock(return_value=ctx)
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
        )
        bot = {"bot_id": "bot1", "owner_id": "user1", "public": "0", "binding_id": 123}

        conn = svc._get_connection(bot, "user1")

        assert conn["use_proxy"] is True
        assert conn["type"] == "remote"

    def test_permission_error_raises_chat_permission_error(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """改造后语义:权限失败 → ChatPermissionError(早失败,resolver 不被调)。

        改造前: 旧 v2 抛 ``Exception("非公开Bot只能...")``,_get_connection
        识别字符串包成 ``ConnectionError(error_code=4002)``。
        改造后: 权限上移到 ``_check_chat_access``,直接 raise
        ``ChatPermissionError`` (业务异常,caller 自行处理是否转 HTTP 4xx)。
        """
        from agentclaw.community.core.expert_chat.errors import ChatPermissionError

        resolver = MagicMock()
        collab = MagicMock()
        collab.check_collaborator_permission = MagicMock(
            return_value={"has_permission": False, "level": "NONE", "level_value": 0}
        )
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
            mock_collaborator_service=collab,
        )
        # 非 owner、非 public、非 collaborator
        bot = {"bot_id": "bot1", "owner_id": "owner1", "public": "0", "binding_id": 123}

        with pytest.raises(ChatPermissionError):
            svc._get_connection(bot, "stranger1")
        # 早失败 — resolver 完全不应被调
        resolver.resolve_for_binding.assert_not_called()

    def test_generic_error_raises_connection_error_with_5001(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """resolver 抛通用异常 → ConnectionError(5001)。"""
        resolver = MagicMock()
        resolver.resolve_for_binding = MagicMock(side_effect=Exception("network timeout"))
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
        )
        bot = {"bot_id": "bot1", "owner_id": "user1", "public": "0", "binding_id": 123}

        with pytest.raises(ConnectionError) as exc_info:
            svc._get_connection(bot, "user1")
        assert exc_info.value.error_code == "5001"

    def test_service_bot_uses_binding_id_path(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        """service bot 路径:bot["binding_id"]=42 时走 resolve_for_binding(42, user_id),
        不走 resolve_for_bot(否则会错查 ac_bots.binding_id 上的 DRAFT binding)。"""
        resolver = MagicMock()
        ctx = MagicMock()
        ctx.conn_info = {"url": "http://x", "headers": {}, "use_proxy": False, "engine_type": "openclaw"}
        resolver.resolve_for_binding = MagicMock(return_value=ctx)
        svc = _make_service(
            mock_repository, mock_bot_repo, mock_device_provider,
            mock_resolver=resolver,
        )
        # service bot:caller(list_chat_bots / get_chat_session)已把 SUCCESS binding_id 塞入
        bot = {
            "bot_id": "svc-bot",
            "owner_id": "owner1",
            "public": "0",
            "binding_id": 42,  # 来自 get_bind_id(SUCCESS) → 发布单 ext.binding.online
        }

        svc._get_connection(bot, "owner1")

        # bot_id 显式 keyword 传给 resolver,供下游 _build_binding_ctx 做 owner 校验
        resolver.resolve_for_binding.assert_called_once_with(42, "owner1", bot_id="svc-bot")
        # by-bot 入口未被调
        resolver.resolve_for_bot.assert_not_called()


# ---------------------------------------------------------------------------
# _check_session_exists
# ---------------------------------------------------------------------------

class TestCheckSessionExists:

    @pytest.mark.asyncio
    async def test_returns_true_on_200(self, mock_repository, mock_bot_repo, mock_device_provider):
        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"data": {"id": "session:abc"}})
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        result = await svc._check_session_exists(ACTIVE_BOT, "session:abc", "user1")

        assert result is True
        expected_conn = mock_device_provider.get_device_connection_v2.return_value
        transport.invoke.assert_awaited_once_with(expected_conn, "GET", "/api/sessions/session:abc")

    @pytest.mark.asyncio
    async def test_returns_false_on_404(self, mock_repository, mock_bot_repo, mock_device_provider):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 404: Not Found"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        result = await svc._check_session_exists(ACTIVE_BOT, "session:abc", "user1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_unexpected_status(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Conservative: unexpected status codes should be treated as session existing."""
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 500: Broken"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        result = await svc._check_session_exists(ACTIVE_BOT, "session:abc", "user1")

        assert result is True

    @pytest.mark.asyncio
    async def test_network_error_returns_true(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Conservative: network errors treated as session still existing."""
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("timeout"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        result = await svc._check_session_exists(ACTIVE_BOT, "session:abc", "user1")

        assert result is True

    @pytest.mark.asyncio
    async def test_claude_code_returns_true_without_transport_call(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        conn = dict(DEVICE_CONN, engine_type="claude_code")
        resolver = MagicMock()
        ctx = MagicMock()
        ctx.conn_info = conn
        resolver.resolve_for_binding = MagicMock(return_value=ctx)
        transport = MagicMock()
        transport.invoke = AsyncMock()
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_resolver=resolver,
            mock_transport=transport,
        )

        result = await svc._check_session_exists(ACTIVE_BOT, "session:abc", "user1")

        assert result is True
        transport.invoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# _delete_adapter_session
# ---------------------------------------------------------------------------

class TestDeleteAdapterSession:

    @pytest.mark.asyncio
    async def test_success_on_2xx(self, mock_repository, mock_bot_repo, mock_device_provider):
        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={})
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        await svc._delete_adapter_session(ACTIVE_BOT, "session:abc", "user1")

        expected_conn = mock_device_provider.get_device_connection_v2.return_value
        transport.invoke.assert_awaited_once_with(expected_conn, "DELETE", "/api/sessions/session:abc")

    @pytest.mark.asyncio
    async def test_404_is_silently_ignored(self, mock_repository, mock_bot_repo, mock_device_provider):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 404: Not Found"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        await svc._delete_adapter_session(ACTIVE_BOT, "session:abc", "user1")

    @pytest.mark.asyncio
    async def test_4xx_raises_exception(self, mock_repository, mock_bot_repo, mock_device_provider):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 403: Forbidden"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )

        with pytest.raises(ValueError, match="Adapter returned HTTP 403"):
            await svc._delete_adapter_session(ACTIVE_BOT, "session:abc", "user1")

    @pytest.mark.asyncio
    async def test_resolved_aicoding_connection_skips_transport_call(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        conn = dict(DEVICE_CONN, engine_type="aicoding")
        resolver = MagicMock()
        ctx = MagicMock()
        ctx.conn_info = conn
        resolver.resolve_for_binding = MagicMock(return_value=ctx)
        transport = MagicMock()
        transport.invoke = AsyncMock()
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_resolver=resolver,
            mock_transport=transport,
        )

        await svc._delete_adapter_session(ACTIVE_BOT, "session:abc", "user1")

        transport.invoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# _create_openclaw_session
# ---------------------------------------------------------------------------

class TestCreateOpenclawSession:

    @pytest.mark.asyncio
    async def test_create_session_uses_device_adapter_transport(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=[
            {"data": {"id": "raw-id"}},
            {"data": [{"id": "session:raw-id:user:user1"}]},
        ])
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {
            "url": "https://secbaas-pre.example/invoke-http/20003",
            "headers": {"x-proxypass-token": "stale"},
            "use_proxy": True,
            "engine_type": "openclaw",
            "binding_id": 1358026,
            "type": "teclaw",
        }
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        result = await svc._create_openclaw_session(conn, bot, "user1")

        assert result == "session:raw-id:user:user1"
        transport.invoke.assert_any_await(
            conn,
            "POST",
            "/api/sessions",
            body={
                "title": "Chat with Bot",
                "user_id": "user1",
                "agent_id": "bot1",
                "engine": "openclaw",
            },
        )
        transport.invoke.assert_any_await(conn, "GET", "/api/sessions")

    @pytest.mark.asyncio
    async def test_success_returns_raw_session_key_when_list_fails(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=[
            {"data": {"id": "raw-session-id"}},
            ValueError("Adapter returned HTTP 500: Broken"),
        ])
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        result = await svc._create_openclaw_session(conn, bot, "user1")

        assert result == "raw-session-id"

    @pytest.mark.asyncio
    async def test_success_returns_raw_session_key_when_list_data_is_not_list(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=[
            {"data": {"id": "raw-session-id"}},
            {"data": {"id": "not-a-list"}},
        ])
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        result = await svc._create_openclaw_session(conn, bot, "user1")

        assert result == "raw-session-id"

    @pytest.mark.asyncio
    async def test_success_returns_prefixed_session_key_from_list(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=[
            {"data": {"id": "raw-id"}},
            {"data": [{"id": "prefix:raw-id"}]},
        ])
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        result = await svc._create_openclaw_session(conn, bot, "user1")

        assert result == "prefix:raw-id"

    @pytest.mark.asyncio
    async def test_adapter_400_raises_session_create_error(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 400: Bad Request"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        with pytest.raises(SessionCreateError):
            await svc._create_openclaw_session(conn, bot, "user1")

    @pytest.mark.asyncio
    async def test_connection_refused_raises_session_create_error_50201(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Failed to connect: Connection refused"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        with pytest.raises(SessionCreateError) as exc_info:
            await svc._create_openclaw_session(conn, bot, "user1")
        assert exc_info.value.error_code in ("50201", "5002")

    @pytest.mark.asyncio
    async def test_failed_to_connect_raises_session_create_error_5002(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Failed to connect to adapter: timeout"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        with pytest.raises(SessionCreateError) as exc_info:
            await svc._create_openclaw_session(conn, bot, "user1")

        assert exc_info.value.error_code == "5002"

    @pytest.mark.asyncio
    async def test_adapter_502_raises_session_create_error_50201(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 502: Bad Gateway"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        with pytest.raises(SessionCreateError) as exc_info:
            await svc._create_openclaw_session(conn, bot, "user1")
        assert exc_info.value.error_code == "50201"

    @pytest.mark.asyncio
    async def test_adapter_404_raises_session_create_error_40402(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=ValueError("Adapter returned HTTP 404: Not Found"))
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        with pytest.raises(SessionCreateError) as exc_info:
            await svc._create_openclaw_session(conn, bot, "user1")
        assert exc_info.value.error_code == "40402"

    @pytest.mark.asyncio
    async def test_no_session_key_in_response_raises(
        self, mock_repository, mock_bot_repo, mock_device_provider
    ):
        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"data": {}})
        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_transport=transport,
        )
        conn = {"url": "http://localhost:8080", "headers": {}, "use_proxy": False}
        bot = {"bot_id": "bot1", "bot_name": "Bot"}

        with pytest.raises(SessionCreateError):
            await svc._create_openclaw_session(conn, bot, "user1")


# ---------------------------------------------------------------------------
# errors module
# ---------------------------------------------------------------------------

class TestErrors:

    def test_session_create_error_stores_fields(self):
        err = SessionCreateError("create failed", error_code="5002", original_error="timeout")
        assert str(err) == "create failed"
        assert err.error_code == "5002"
        assert err.original_error == "timeout"

    def test_session_create_error_defaults_to_none(self):
        err = SessionCreateError("failed")
        assert err.error_code is None
        assert err.original_error is None

    def test_connection_error_stores_fields(self):
        err = ConnectionError("conn failed", error_code="4002", original_error="permission denied")
        assert str(err) == "conn failed"
        assert err.error_code == "4002"
        assert err.original_error == "permission denied"

    def test_connection_error_defaults_to_none(self):
        err = ConnectionError("failed")
        assert err.error_code is None
        assert err.original_error is None


# ---------------------------------------------------------------------------
# Authorization before expensive operations (Caller mode)
# ---------------------------------------------------------------------------

class TestGetChatSessionCallerAuthorization:
    """Tests verifying authorization checks happen BEFORE expensive operations.

    Security: Unauthorized users must NOT trigger get_caller_connection
    (container creation/upgrade) before authorization is verified.

    Note: Chat list check (list_chat_bots) was removed - users with permission
    (owner/public/collaborator) can directly use the bot without adding it first.
    """

    @pytest.mark.asyncio
    async def test_no_permission_no_instance_call(self, mock_repository, mock_bot_repo, mock_device_provider):
        """User has no chat permission: instance_service must NOT be called."""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
            "owner_id": "owner1",  # Different from user
            "public": "0",  # Not public
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot

        # User is NOT a collaborator
        mock_collab = MagicMock()
        mock_collab.check_collaborator_permission = MagicMock(
            return_value={"has_permission": False}
        )

        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock()

        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_instance_service=mock_instance,
            mock_collaborator_service=mock_collab,
        )

        with pytest.raises(ChatPermissionError):
            await svc.get_chat_session("user1", "bot1", "owner1")

        # CRITICAL: instance_service.get_caller_connection must NOT be called
        mock_instance.get_caller_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_owner_passes_instance_called(self, mock_repository, mock_bot_repo, mock_device_provider):
        """User is owner: authorization passes, instance_service IS called."""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
            "owner_id": "owner1",
            "public": "0",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.get_session.return_value = None

        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 123}},
            "connection": {"ws_url": "ws://test", "token": "abc"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new")):
            await svc.get_chat_session("owner1", "bot1", "owner1")

        # instance_service.get_caller_connection SHOULD be called for authorized user
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="owner1", bot_id="bot1", owner_id="owner1", iam_token=None
        )

    @pytest.mark.asyncio
    async def test_collaborator_passes_instance_called(self, mock_repository, mock_bot_repo, mock_device_provider):
        """User is collaborator: authorization passes, instance_service IS called."""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
            "owner_id": "owner1",  # Different from user
            "public": "0",  # Not public
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.get_session.return_value = None

        # User IS a collaborator
        mock_collab = MagicMock()
        mock_collab.check_collaborator_permission = MagicMock(
            return_value={"has_permission": True, "level": "MEMBER"}
        )

        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 456}},
            "connection": {"ws_url": "ws://test", "token": "xyz"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(
            mock_repository,
            mock_bot_repo,
            mock_device_provider,
            mock_instance_service=mock_instance,
            mock_collaborator_service=mock_collab,
        )

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new")):
            await svc.get_chat_session("user1", "bot1", "owner1")

        # instance_service.get_caller_connection SHOULD be called for authorized user
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="user1", bot_id="bot1", owner_id="owner1", iam_token=None
        )

    @pytest.mark.asyncio
    async def test_public_bot_passes_instance_called(self, mock_repository, mock_bot_repo, mock_device_provider):
        """Bot is public: authorization passes, instance_service IS called."""
        caller_bot = {
            "bot_id": "bot1",
            "status": "ACTIVE",
            "call_type": "caller",
            "owner_id": "owner1",  # Different from user
            "public": "1",  # Public bot
        }
        mock_bot_repo.get_by_id_and_owner.return_value = caller_bot
        mock_repository.get_session.return_value = None

        mock_instance = MagicMock()
        mock_instance.get_caller_connection = AsyncMock(return_value={
            "instance": {"ext": {"binding_id": 789}},
            "connection": {"ws_url": "ws://test", "token": "pub"},
            "need_poll": False,
        })
        mock_device_provider.get_device_connection_v2.return_value = dict(DEVICE_CONN)

        svc = _make_service(mock_repository, mock_bot_repo, mock_device_provider, mock_instance_service=mock_instance)

        with patch.object(svc, "_create_session", new=AsyncMock(return_value="session:new")):
            await svc.get_chat_session("user1", "bot1", "owner1")

        # instance_service.get_caller_connection SHOULD be called for public bot
        mock_instance.get_caller_connection.assert_called_once_with(
            user_id="user1", bot_id="bot1", owner_id="owner1", iam_token=None
        )
