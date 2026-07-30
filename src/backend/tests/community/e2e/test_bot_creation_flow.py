"""
端到端测试: Bot Passport 授权流程

从 Service 层开始测试，Mock PassportPlugin 和外部依赖。
测试场景：
1. 首Bot创建（token 非空 / token 为空）
2. 非首Bot创建
3. 授权状态轮询
"""
import json
import pytest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch, MagicMock, AsyncMock

from agentclaw.community.core.bot_management.services.bot_service import BotService, BotNotFoundError


# ============================================================
# Fake Implementations
# ============================================================

@dataclass
class FakeBot:
    """Fake bot record for testing."""
    bot_id: str
    owner_id: str
    bot_name: str
    status: str = "ACTIVE"
    entity_id: str = ""
    entity_type: str = "staff"
    ext: dict = None

    def __post_init__(self):
        if self.ext is None:
            self.ext = {}
        if not self.entity_id:
            self.entity_id = f"staff_{self.owner_id}"


class FakeBotRepository:
    """内存 Bot 仓库，用于测试。"""

    def __init__(self):
        self._store: dict[str, FakeBot] = {}

    def _key(self, owner_id: str, bot_id: str) -> str:
        return f"{owner_id}:{bot_id}"

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> Optional[dict]:
        key = self._key(owner_id, bot_id)
        bot = self._store.get(key)
        return bot.__dict__ if bot else None

    def exists_by_owner_and_bot_id(self, owner_id: str, bot_id: str) -> bool:
        key = self._key(owner_id, bot_id)
        return key in self._store

    def insert(self, bot_data: dict) -> dict:
        bot_id = bot_data["bot_id"]
        owner_id = bot_data["owner_id"]
        bot = FakeBot(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name=bot_data.get("bot_name", bot_id),
            status=bot_data.get("status", "PENDING"),
            entity_id=bot_data.get("entity_id", f"staff_{owner_id}"),
            entity_type=bot_data.get("entity_type", "staff"),
            ext=bot_data.get("ext", {}),
        )
        self._store[self._key(owner_id, bot_id)] = bot
        return bot.__dict__

    def update_by_owner(self, bot_id: str, owner_id: str, update_data: dict) -> dict:
        key = self._key(owner_id, bot_id)
        if key not in self._store:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        bot = self._store[key]
        for k, v in update_data.items():
            if k == "ext":
                existing = bot.ext or {}
                if isinstance(existing, str):
                    try:
                        existing = json.loads(existing)
                    except json.JSONDecodeError:
                        existing = {}
                if isinstance(v, dict):
                    bot.ext = {**existing, **v}
                else:
                    bot.ext = v
            else:
                setattr(bot, k, v)
        return bot.__dict__

    def list_by_owner(self, owner_id: str, page: int = 1, page_size: int = 100) -> tuple:
        items = [bot.__dict__ for bot in self._store.values() if bot.owner_id == owner_id]
        offset = (page - 1) * page_size
        return len(items), items[offset:offset + page_size]

    def exists_by_bot_name(self, bot_name: str) -> bool:
        return any(bot.bot_name == bot_name for bot in self._store.values())


class FakeDeviceBinding:
    """Fake device binding for testing."""
    id: int = 1
    device_id: str = "fake-device-001"
    status: str = "ACTIVE"


# ============================================================
# TestClasses
# ============================================================

class TestBotCreationFlow:
    """Bot 创建与更新流程测试（从 Service 层开始）。"""

    @pytest.fixture
    def fake_repo(self):
        """创建 Fake 仓库。"""
        return FakeBotRepository()

    @pytest.fixture
    def mock_device_service(self):
        """Mock 设备服务。"""
        mock_service = MagicMock()
        mock_binding = MagicMock()
        mock_binding.id = 1
        mock_binding.device_id = "device_001"
        mock_binding.status = "ACTIVE"
        mock_service.apply_device.return_value = mock_binding
        return mock_service

    @pytest.fixture
    def bot_service(self, fake_repo, mock_device_service):
        """创建 BotService 实例，注入 Fake 仓库 + mock device_service。"""
        service = BotService(
            drm_reader=MagicMock(),
            repository=fake_repo,
            allocation_config=MagicMock(mode="multi", max_devices_per_entity=5),
            device_binding_repo=MagicMock(),
            skill_set_factory=MagicMock(),
            cleanup_service=MagicMock(),
            bcn_service=MagicMock(),
            bot_publish_repo=MagicMock(),
            passport_plugin=MagicMock(),
            oss_record_repo=MagicMock(),
            bot_publish_service_provider=lambda: MagicMock(),
            device_service_provider=lambda: mock_device_service,
            path_factory=MagicMock(),
            template_service=MagicMock(),
            workspace_hosting_service=MagicMock(),
            collaborator_repo=MagicMock(),
            restart_lock_repo=MagicMock(),
            teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
            device_status_client=MagicMock(),
            cron_auto_setup_service_provider=lambda: MagicMock(),
        )
        return service

    # ==================== 场景 1: 首Bot创建 ====================

    def test_create_first_bot(self, bot_service, fake_repo, mock_device_service):
        """首Bot创建成功。"""
        with patch('agentclaw.community.core.bot_management.services.bot_service._get_engine_types', return_value=['moltis']), \
             patch('agentclaw.community.core.bot_management.services.bot_service.DEFAULT_ENGINE_TYPE', 'moltis'):

            result = bot_service.create_bot(
                user_id="user_001",
                nick_name="Test User",
                bot_name="My First Bot",
                bot_id="default",
                bot_type="personal",
            )

        assert result["bot_id"] == "default"
        assert result["owner_id"] == "user_001"

        stored = fake_repo.get_by_id_and_owner("default", "user_001")
        assert stored is not None

    def test_create_first_bot_idempotent(self, bot_service, fake_repo, mock_device_service):
        """首Bot创建幂等性 - 已存在则返回已有 Bot。"""
        fake_repo.insert({
            "bot_id": "default",
            "owner_id": "user_001",
            "bot_name": "Existing Bot",
            "status": "ACTIVE",
        })

        result = bot_service.create_bot(
            user_id="user_001",
            nick_name="Test User",
            bot_name="My First Bot",
            bot_id="default",
            bot_type="personal",
        )

        assert result["bot_id"] == "default"
        assert result["bot_name"] == "Existing Bot"

    # ==================== 场景 2: 非首Bot创建 ====================

    def test_create_non_first_bot(self, bot_service, fake_repo, mock_device_service):
        """非首Bot创建成功。"""
        fake_repo.insert({
            "bot_id": "default",
            "owner_id": "user_001",
            "bot_name": "Default Bot",
            "status": "ACTIVE",
        })

        with patch('agentclaw.community.core.bot_management.services.bot_service._get_engine_types', return_value=['moltis']), \
             patch('agentclaw.community.core.bot_management.services.bot_service.DEFAULT_ENGINE_TYPE', 'moltis'):

            result = bot_service.create_bot(
                user_id="user_001",
                nick_name="Test User",
                bot_name="My Second Bot",
                bot_id="20260408_abc12345",
                bot_type="personal",
            )

        assert result["bot_id"] == "20260408_abc12345"
        assert result["owner_id"] == "user_001"

    # ==================== 场景 3: update_bot_ext ====================

    def test_update_bot_ext_adds_passport(self, bot_service, fake_repo):
        """update_bot_ext 正确合并 ext 字段。"""
        fake_repo.insert({
            "bot_id": "default",
            "owner_id": "user_001",
            "bot_name": "Test Bot",
            "ext": {"avatar_url": "https://example.com/avatar.png"},
        })

        bot_service.update_bot_ext(
            bot_id="default",
            user_id="user_001",
            ext_update={"passport": {"token": "abc123", "status": "ISSUED"}},
        )

        updated = fake_repo.get_by_id_and_owner("default", "user_001")
        assert "passport" in updated["ext"]
        assert updated["ext"]["passport"]["token"] == "abc123"
        assert updated["ext"]["avatar_url"] == "https://example.com/avatar.png"

    def test_update_bot_ext_with_empty_ext(self, bot_service, fake_repo):
        """update_bot_ext 当 ext 为空时。"""
        fake_repo.insert({
            "bot_id": "default",
            "owner_id": "user_001",
            "bot_name": "Test Bot",
            "ext": None,
        })

        bot_service.update_bot_ext(
            bot_id="default",
            user_id="user_001",
            ext_update={"passport": {"token": "abc123"}},
        )

        updated = fake_repo.get_by_id_and_owner("default", "user_001")
        assert updated["ext"]["passport"]["token"] == "abc123"

    def test_update_bot_ext_with_string_ext(self, bot_service, fake_repo):
        """update_bot_ext 当 ext 是 JSON 字符串时。"""
        fake_repo.insert({
            "bot_id": "default",
            "owner_id": "user_001",
            "bot_name": "Test Bot",
            "ext": '{"avatar_url": "https://example.com/avatar.png"}',
        })

        bot_service.update_bot_ext(
            bot_id="default",
            user_id="user_001",
            ext_update={"passport": {"token": "abc123"}},
        )

        updated = fake_repo.get_by_id_and_owner("default", "user_001")
        assert updated["ext"]["passport"]["token"] == "abc123"
        assert updated["ext"]["avatar_url"] == "https://example.com/avatar.png"


class TestGenerateBotId:
    """测试 generate_bot_id 函数。"""

    def test_generate_first_bot_id(self):
        """首Bot应返回 'default'。"""
        fake_repo = FakeBotRepository()

        from agentclaw.community.core.bot_management.services.bot_service import generate_bot_id

        result = generate_bot_id("user_001", fake_repo)
        assert result == "default"

    def test_generate_non_first_bot_id(self):
        """非首Bot应返回日期格式 ID。"""
        fake_repo = FakeBotRepository()
        fake_repo.insert({
            "bot_id": "default",
            "owner_id": "user_001",
            "bot_name": "Default Bot",
        })

        from agentclaw.community.core.bot_management.services.bot_service import generate_bot_id

        result = generate_bot_id("user_001", fake_repo)
        assert result != "default"
        assert "_" in result
        assert len(result) == 17  # yyyymmdd_xxxxxxxx


class TestRouterLogic:
    """测试 Router 层逻辑（更接近端到端的测试）。"""

    @pytest.mark.asyncio
    async def test_create_bot_router_first_bot_success(self):
        """
        从 Router 视角测试首Bot创建成功流程。
        Mock get_passport_plugin 返回 token，验证整个流程。
        """
        from agentclaw.community.adapters.http.bot_management.router import create_bot
        from agentclaw.community.adapters.http.dependencies import RequestContext
        from unittest.mock import AsyncMock as NewAsyncMock

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "user_001"
        mock_ctx.nick_name = "Test User"

        mock_passport_plugin = MagicMock()
        mock_passport_plugin.apply_first_agent_passport.return_value = {
            "token": "passport_token_123",
            "iframe_url": None,
            "redirect_url": None,
        }

        mock_bot_service = MagicMock()
        mock_bot_service.check_create_bot_preflight.return_value = None
        mock_bot_service.create_bot.return_value = {
            "bot_id": "default",
            "owner_id": "user_001",
            "status": "ACTIVE",
        }

        mock_request = MagicMock()
        mock_request.json = NewAsyncMock(return_value={
            "bot_name": "My First Bot",
            "bot_desc": "Test",
        })

        mock_factory = MagicMock()
        mock_factory.create.return_value.get_bot_mcp_codes.return_value = ["mcp_1"]
        mock_bot_repo = MagicMock()
        mock_bot_repo.exists_by_owner_and_bot_type.return_value = False
        with patch('agentclaw.community.adapters.http.bot_management.router.generate_bot_id', return_value="default"):
            result = await create_bot(
                mock_request,
                mock_ctx,
                bot_service=mock_bot_service,
                bot_repo=mock_bot_repo,
                passport_plugin=mock_passport_plugin,
                auth_rel_plugin=MagicMock(),
                skill_set_factory=mock_factory,
            )

        # token 非空：create_bot 直接创建 Bot 并返回 success（保留原 frontend 契约）。
        assert result.success is True
        assert result.data["bot"]["bot_id"] == "default"
        assert result.data["passport"]["token"] == "passport_token_123"
        assert result.data["passport"]["is_first_bot"] is True
        # bot_name is passed so a taken name is rejected before the external
        # Passport application, not after it (R13/F48).
        mock_bot_service.check_create_bot_preflight.assert_called_once_with(
            user_id="user_001",
            bot_name="My First Bot",
        )
        mock_bot_service.create_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_bot_router_need_authorization(self):
        """
        从 Router 视角测试首Bot创建需要授权流程。
        Mock get_passport_plugin 返回 token 为空，验证返回授权页面。
        """
        from agentclaw.community.adapters.http.bot_management.router import create_bot
        from agentclaw.community.adapters.http.dependencies import RequestContext
        from unittest.mock import AsyncMock as NewAsyncMock

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "user_001"
        mock_ctx.nick_name = "Test User"

        mock_passport_plugin = MagicMock()
        mock_passport_plugin.apply_first_agent_passport.return_value = {
            "token": None,  # 需要授权
            "iframe_url": "https://auth.example.com/iframe",
            "redirect_url": None,
        }

        mock_request = MagicMock()
        mock_request.json = NewAsyncMock(return_value={
            "bot_name": "My First Bot",
        })

        mock_bot_service = MagicMock()
        mock_bot_service.check_create_bot_preflight.return_value = None

        mock_factory = MagicMock()
        mock_factory.create.return_value.get_bot_mcp_codes.return_value = ["mcp_1"]
        mock_bot_repo = MagicMock()
        mock_bot_repo.exists_by_owner_and_bot_type.return_value = False
        with patch('agentclaw.community.adapters.http.bot_management.router.generate_bot_id', return_value="default"):
            result = await create_bot(
                mock_request,
                mock_ctx,
                bot_service=mock_bot_service,
                bot_repo=mock_bot_repo,
                passport_plugin=mock_passport_plugin,
                auth_rel_plugin=MagicMock(),
                skill_set_factory=mock_factory,
            )

        assert result.success is False
        assert result.error_code == 401
        assert result.data["need_authorization"] is True
        assert result.data["bot_id"] == "default"
        assert result.data["iframe_url"] == "https://auth.example.com/iframe"
        # bot_name is passed so a taken name is rejected before the external
        # Passport application, not after it (R13/F48).
        mock_bot_service.check_create_bot_preflight.assert_called_once_with(
            user_id="user_001",
            bot_name="My First Bot",
        )
        mock_bot_service.create_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_status_router_pending(self):
        """
        从 Router 视角测试授权状态轮询 - PENDING。
        """
        from agentclaw.community.adapters.http.bot_management.router import get_auth_status
        from agentclaw.community.adapters.http.dependencies import RequestContext

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "user_001"
        mock_ctx.nick_name = "Test User"

        mock_passport_plugin = MagicMock()
        mock_passport_plugin.query_auth_status.return_value = {
            "status": "PENDING",
            "token": None,
        }

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"bot_id": "default"})

        result = await get_auth_status(
            mock_request,
            ctx=mock_ctx,
            passport_plugin=mock_passport_plugin,
            auth_rel_plugin=MagicMock(),
        )

        assert result.success is True
        assert result.data["status"] == "PENDING"
        assert result.data["message"] == "授权处理中"

    @pytest.mark.asyncio
    async def test_auth_status_router_issued(self):
        """
        从 Router 视角测试授权状态轮询 - ISSUED。
        """
        from agentclaw.community.adapters.http.bot_management.router import get_auth_status
        from agentclaw.community.adapters.http.dependencies import RequestContext

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "user_001"
        mock_ctx.nick_name = "Test User"

        mock_passport_plugin = MagicMock()
        mock_passport_plugin.query_auth_status.return_value = {
            "status": "ISSUED",
            "token": "passport_token_issued",
        }

        mock_bot_service = MagicMock()
        mock_bot_service.create_bot.return_value = {
            "bot_id": "default",
            "owner_id": "user_001",
            "status": "ACTIVE",
        }

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"bot_id": "default"})

        result = await get_auth_status(
            mock_request,
            ctx=mock_ctx,
            bot_service=mock_bot_service,
            passport_plugin=mock_passport_plugin,
            auth_rel_plugin=MagicMock(),
        )

        assert result.success is True
        assert result.data["status"] == "ISSUED"
        assert "bot" in result.data

    @pytest.mark.asyncio
    async def test_auth_status_router_rejected(self):
        """
        从 Router 视角测试授权状态轮询 - REJECTED。
        """
        from agentclaw.community.adapters.http.bot_management.router import get_auth_status
        from agentclaw.community.adapters.http.dependencies import RequestContext

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "user_001"

        mock_passport_plugin = MagicMock()
        mock_passport_plugin.query_auth_status.return_value = {
            "status": "REJECTED",
            "token": None,
        }

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"bot_id": "default"})

        result = await get_auth_status(
            mock_request,
            ctx=mock_ctx,
            passport_plugin=mock_passport_plugin,
            auth_rel_plugin=MagicMock(),
        )

        assert result.success is False
        assert result.data["status"] == "REJECTED"
