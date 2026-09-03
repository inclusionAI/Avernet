"""Tests for ChannelService.sync_channel_to_openclaw stage logic."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.channel.errors import ChannelSyncError
from agentclaw.community.core.channel.services.channel_service import ChannelService
from agentclaw.community.core.channel.models import ChannelRecord


@pytest.fixture
def mock_repository():
    """Mock ChannelRepository."""
    return MagicMock()


@pytest.fixture
def mock_device_fs_dispatcher():
    """Mock DeviceFilesystemDispatcher.

    ChannelService resolves a DeviceContext and calls ``dispatch(ctx)``.
    ``for_bot`` remains on this generic mock because other filesystem fixtures
    still reference that compatibility method.
    """
    dispatcher = MagicMock()
    dispatcher.for_bot = MagicMock()
    dispatcher.dispatch = MagicMock()
    return dispatcher


@pytest.fixture
def mock_resolver():
    """Mock DeviceContextResolver — resolves to a stable DeviceContext."""
    from agentclaw.community.core.devices.services.device_context import DeviceContext

    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = DeviceContext(
        provider="local",
        conn_info={"engine_type": "openclaw"},
        binding_id=1,
        bot_id="bot1",
        user_id="user1",
    )
    return resolver


@pytest.fixture
def mock_bot_service():
    """Mock BotService — defaults to a non-teclaw (openclaw) bot."""
    svc = MagicMock()
    svc.get_bot.return_value = {"active_engine": "openclaw"}
    return svc


@pytest.fixture
def mock_device_sync_dispatcher():
    """Mock DeviceSyncDispatcher injected into ChannelService."""
    return MagicMock()


@pytest.fixture
def mock_bcs_client():
    """Mock BcsChannelBindingClientProtocol."""
    client = MagicMock()
    client.ensure_active = AsyncMock(return_value="bcs-binding-1")
    client.push_config = AsyncMock()
    client.set_active = AsyncMock()
    client.delete_binding = AsyncMock()
    return client


@pytest.fixture
def channel_service(
    mock_repository,
    mock_resolver,
    mock_device_fs_dispatcher,
    mock_bot_service,
    mock_device_sync_dispatcher,
    mock_bcs_client,
):
    """Create ChannelService with mocked dependencies."""
    return ChannelService(
        repository=mock_repository,
        resolver=mock_resolver,
        device_fs_dispatcher=mock_device_fs_dispatcher,
        bot_service=mock_bot_service,
        device_sync_dispatcher=mock_device_sync_dispatcher,
        bcs_client=mock_bcs_client,
    )


def _make_channel_record(
    channel_id: int = 1,
    channel_type: str = "dingding",
    stage: str | None = None,
    status: str = "1",
    config: dict | None = None,
    bind_bot_id: str = "bot1",
    identity_id: str = "user1",
) -> ChannelRecord:
    """Helper to create a ChannelRecord for testing."""
    return ChannelRecord(
        id=channel_id,
        type=channel_type,
        description="test channel",
        identity_id=identity_id,
        bind_bot_id=bind_bot_id,
        config=config or {"client_id": "test_client"},
        status=status,
        deleted=0,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        env="dev",
        stage=stage,
    )


class TestSyncChannelStageLogic:
    """Test sync_channel_to_openclaw stage filtering logic."""

    @pytest.mark.asyncio
    async def test_sync_skips_when_stage_is_verify(
        self, channel_service, mock_repository
    ):
        """stage=verify 时跳过同步并返回 False"""
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=1,
            channel_type="dingding",
            stage="verify",
        )

        result = await channel_service.sync_channel_to_openclaw(1, action="apply")

        assert result is False
        mock_repository.get_by_id.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_sync_skips_when_stage_is_online(
        self, channel_service, mock_repository
    ):
        """stage=online 时跳过同步并返回 False"""
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=2,
            channel_type="dingding",
            stage="online",
        )

        result = await channel_service.sync_channel_to_openclaw(2, action="apply")

        assert result is False
        mock_repository.get_by_id.assert_called_once_with(2)

    @pytest.mark.asyncio
    async def test_sync_skips_when_stage_is_verify_with_remove_action(
        self, channel_service, mock_repository
    ):
        """stage=verify 时 remove 操作也跳过同步"""
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=3,
            channel_type="dingding",
            stage="verify",
        )

        result = await channel_service.sync_channel_to_openclaw(3, action="remove")

        assert result is False

    @pytest.mark.asyncio
    async def test_sync_skips_when_stage_is_online_with_remove_action(
        self, channel_service, mock_repository
    ):
        """stage=online 时 remove 操作也跳过同步"""
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=4,
            channel_type="dingding",
            stage="online",
        )

        result = await channel_service.sync_channel_to_openclaw(4, action="remove")

        assert result is False

    @pytest.mark.asyncio
    async def test_sync_proceeds_when_stage_is_draft(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """stage=draft 时不跳过，继续同步逻辑（验证没有在 stage 判断处返回 False）"""

        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=5,
            channel_type="dingding",
            stage="draft",
        )

        # Mock bot_service 返回工作路径
        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")

        # Mock device_fs
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        # Mock JsonConfigFile.load 为异步方法返回 mock 对象
        mock_json_config = AsyncMock()
        mock_json_config.get.return_value = None
        mock_json_config.exists.return_value = False
        mock_json_config.save = AsyncMock()

        mock_json_class = MagicMock()
        mock_json_class.load = AsyncMock(return_value=mock_json_config)

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile",
            mock_json_class,
        ):
            result = await channel_service.sync_channel_to_openclaw(5, action="apply")

            # 应该继续同步逻辑，返回 True
            assert result is True
            mock_bot_service.get_bot_work_path.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_proceeds_when_stage_is_none(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """stage=None 时不跳过，继续同步逻辑（验证没有在 stage 判断处返回 False）"""

        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=6,
            channel_type="dingding",
            stage=None,
        )

        # Mock bot_service 返回工作路径
        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")

        # Mock device_fs
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        # Mock JsonConfigFile.load 为异步方法返回 mock 对象
        mock_json_config = AsyncMock()
        mock_json_config.get.return_value = None
        mock_json_config.exists.return_value = False
        mock_json_config.save = AsyncMock()

        mock_json_class = MagicMock()
        mock_json_class.load = AsyncMock(return_value=mock_json_config)

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile",
            mock_json_class,
        ):
            result = await channel_service.sync_channel_to_openclaw(6, action="apply")

            # 应该继续同步逻辑，返回 True
            assert result is True
            mock_bot_service.get_bot_work_path.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_skips_non_dingding_channel(
        self, channel_service, mock_repository
    ):
        """非钉钉类型渠道跳过同步"""
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=7,
            channel_type="feishu",
            stage=None,
        )

        result = await channel_service.sync_channel_to_openclaw(7, action="apply")

        assert result is False

    @pytest.mark.asyncio
    async def test_sync_raises_when_channel_not_found(
        self, channel_service, mock_repository
    ):
        """渠道不存在时抛出 ValueError"""
        mock_repository.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Channel not found"):
            await channel_service.sync_channel_to_openclaw(999, action="apply")


# ============== Tests for _extract_dingtalk_template ==============

class TestExtractDingtalkTemplate:
    """Test _extract_dingtalk_template method."""

    def test_single_account_extracts_inheritable_fields(self, channel_service):
        """单账号格式：正确抽取可继承字段（排除 clientId 等）"""
        config = {
            "enabled": True,
            "clientId": "old_client_id",
            "clientSecret": "old_secret",
            "dmPolicy": "restricted",
            "groupPolicy": "open",
            "messageType": "card",
            "cardTemplateId": "template_123",
            "customField": "custom_value",
        }
        result = channel_service._extract_dingtalk_template(config)

        # 可继承字段直接返回
        assert result["dmPolicy"] == "restricted"
        assert result["groupPolicy"] == "open"
        assert result["customField"] == "custom_value"
        # 排除字段不在结果中
        assert "clientId" not in result
        assert "clientSecret" not in result
        assert "messageType" not in result
        assert "cardTemplateId" not in result
        assert "enabled" not in result

    def test_multi_account_merges_all_accounts(self, channel_service):
        """多账号格式：合并所有账号的可继承字段（后覆盖前）"""
        config = {
            "enabled": True,
            "accounts": {
                "client_1": {
                    "clientId": "client_1",
                    "clientSecret": "secret1",
                    "dmPolicy": "restricted",
                    "groupPolicy": "restricted",
                    "messageType": "card",
                    "customField": "value1",
                },
                "client_2": {
                    "clientId": "client_2",
                    "dmPolicy": "open",
                    "anotherField": "value2",
                },
            },
        }
        result = channel_service._extract_dingtalk_template(config)

        # 合并所有账号的可继承字段，后面的账号覆盖前面的
        assert result["dmPolicy"] == "open"  # client_2 覆盖 client_1
        assert result["groupPolicy"] == "restricted"  # 来自 client_1
        assert result["customField"] == "value1"  # 来自 client_1
        assert result["anotherField"] == "value2"  # 来自 client_2
        # 排除字段不在结果中
        assert "clientId" not in result
        assert "messageType" not in result

    def test_multi_account_later_overwrites_earlier(self, channel_service):
        """多账号格式：后面的账号字段覆盖前面的同名字段"""
        config = {
            "accounts": {
                "dingdfnvt6bn4381av70": {
                    "dmPolicy": "open",
                    "mediaMaxMb": 30,
                    "customA": "a1",
                },
                "dingutiuxvcz2yoh9ts8": {
                    "mediaMaxMb": 50,
                    "customB": "b1",
                },
            },
        }
        result = channel_service._extract_dingtalk_template(config)

        # client_2 的 mediaMaxMb 覆盖 client_1 的
        assert result["dmPolicy"] == "open"  # 来自 client_1
        assert result["mediaMaxMb"] == 50  # client_2 覆盖 client_1
        assert result["customA"] == "a1"  # 来自 client_1
        assert result["customB"] == "b1"  # 来自 client_2

    def test_multi_account_single_account(self, channel_service):
        """多账号格式但只有一个账号：正常提取"""
        config = {
            "enabled": True,
            "accounts": {
                "client_1": {
                    "clientId": "client_1",
                    "dmPolicy": "restricted",
                    "customField": "value1",
                },
            },
        }
        result = channel_service._extract_dingtalk_template(config)

        # 单个账号也能正确提取
        assert result["dmPolicy"] == "restricted"
        assert result["customField"] == "value1"
        assert "clientId" not in result  # 排除字段不在结果中

    def test_multi_account_excludes_fields_from_all_accounts(self, channel_service):
        """多账号格式：所有账号的排除字段都不在结果中"""
        config = {
            "accounts": {
                "client_1": {
                    "clientId": "client_1",
                    "clientSecret": "secret1",
                    "robotCode": "robot1",
                    "cardTemplateId": "template_1",
                    "cardTemplateKey": "key_1",
                    "messageType": "card",
                    "dmPolicy": "restricted",
                },
                "client_2": {
                    "clientId": "client_2",
                    "clientSecret": "secret2",
                    "messageType": "markdown",
                    "customField": "value2",
                },
            },
        }
        result = channel_service._extract_dingtalk_template(config)

        # 排除字段都在结果中（无论是哪个账号）
        assert "clientId" not in result
        assert "clientSecret" not in result
        assert "robotCode" not in result
        assert "cardTemplateId" not in result
        assert "cardTemplateKey" not in result
        assert "messageType" not in result
        # 可继承字段在结果中
        assert result["dmPolicy"] == "restricted"
        assert result["customField"] == "value2"

    def test_no_dingtalk_config_returns_default(self, channel_service):
        """无钉钉配置时返回默认值"""
        result = channel_service._extract_dingtalk_template(None)
        assert result == {"dmPolicy": "open", "groupPolicy": "open"}

        result = channel_service._extract_dingtalk_template({})
        assert result == {"dmPolicy": "open", "groupPolicy": "open"}

    def test_empty_accounts_returns_default(self, channel_service):
        """空 accounts 时返回默认值"""
        config = {"enabled": True, "accounts": {}}
        result = channel_service._extract_dingtalk_template(config)
        assert result == {"dmPolicy": "open", "groupPolicy": "open"}

    def test_excluded_fields_not_in_template(self, channel_service):
        """排除字段不在结果中"""
        config = {
            "enabled": True,
            "clientId": "client_id",
            "clientSecret": "secret",
            "robotCode": "robot_code",
            "cardTemplateId": "template_id",
            "cardTemplateKey": "template_key",
            "messageType": "card",
            "dmPolicy": "restricted",
        }
        result = channel_service._extract_dingtalk_template(config)

        assert result["dmPolicy"] == "restricted"
        # 所有排除字段都不在结果中
        assert "clientId" not in result
        assert "clientSecret" not in result
        assert "robotCode" not in result
        assert "cardTemplateId" not in result
        assert "cardTemplateKey" not in result
        assert "messageType" not in result
        assert "enabled" not in result

    def test_deep_copy_not_modify_original(self, channel_service):
        """深拷贝不修改原配置"""
        config = {
            "enabled": True,
            "clientId": "client_id",
            "dmPolicy": "restricted",
            "accounts": {
                "client_1": {"clientId": "client_1", "dmPolicy": "open"},
            },
        }
        result = channel_service._extract_dingtalk_template(config)

        # 修改返回值不应影响原配置
        result["dmPolicy"] = "changed"

        assert config["dmPolicy"] == "restricted"
        assert config["accounts"]["client_1"]["dmPolicy"] == "open"


# ============== Tests for _apply_template ==============

class TestApplyTemplate:
    """Test _apply_template method."""

    def _call_apply_template(self, channel_service, template_config, channels):
        """Helper to call _apply_template with dingtalk_template extracted."""
        dingtalk_config = None
        if "channels" in template_config and "dingtalk" in template_config["channels"]:
            dingtalk_config = template_config["channels"]["dingtalk"]
        dingtalk_template = channel_service._extract_dingtalk_template(dingtalk_config)
        return channel_service._apply_template(template_config, channels, dingtalk_template)

    def test_without_channels_no_dingtalk_config(self, channel_service):
        """无数据库配置且无模版钉钉配置时，返回 enabled=false"""
        template_config = {"name": "test"}
        channels = []

        result = self._call_apply_template(channel_service, template_config, channels)

        # 无数据库配置时返回 enabled=false
        assert result["channels"]["dingtalk"]["enabled"] is False

    def test_no_config_with_template_keeps_dingtalk_structure(self, channel_service):
        """无数据库配置时，enabled=false"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "restricted",
                },
            },
        }
        channels = []

        result = self._call_apply_template(channel_service, template_config, channels)

        # enabled 应该被设为 false（无数据库配置）
        assert result["channels"]["dingtalk"]["enabled"] is False

    def test_no_config_with_disabled_template(self, channel_service):
        """无数据库配置时，enabled=false"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": False,
                    "dmPolicy": "restricted",
                },
            },
        }
        channels = []

        result = self._call_apply_template(channel_service, template_config, channels)

        # enabled 为 false
        assert result["channels"]["dingtalk"]["enabled"] is False

    def test_single_account_with_template(self, channel_service):
        """单账号配置：使用模版默认值"""
        template_config = {
            "name": "test",
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "restricted",
                    "groupPolicy": "restricted",
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        assert account["clientId"] == "client_1"
        assert account["clientSecret"] == "secret_1"
        assert account["robotCode"] == "client_1"
        # 模版默认值
        assert account["dmPolicy"] == "restricted"
        assert account["groupPolicy"] == "restricted"
        # 代码默认值
        assert account["messageType"] == "markdown"

    def test_multiple_accounts(self, channel_service):
        """数据库字段覆盖模版"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "restricted",
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={
                "client_id": "client_1",
                "client_secret": "secret_1",
                "card_template_id": "template_from_db",
            },
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        # 数据库值
        assert account["cardTemplateId"] == "template_from_db"
        # 模版值
        assert account["dmPolicy"] == "restricted"

    def test_enable_streaming_cards_sets_message_type(self, channel_service):
        """enable_streaming_cards=True 时 messageType 为 card"""
        template_config = {"name": "test"}
        channels = [_make_channel_record(
            channel_id=1,
            config={
                "client_id": "client_1",
                "client_secret": "secret_1",
                "enable_streaming_cards": True,
            },
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        assert account["messageType"] == "card"

    def test_no_template_uses_default_values(self, channel_service):
        """无模版时使用代码默认值"""
        template_config = {"name": "test"}
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        # 代码默认值
        assert account["dmPolicy"] == "open"
        assert account["groupPolicy"] == "open"
        assert account["messageType"] == "markdown"

    def test_skip_channel_without_client_id(self, channel_service):
        """跳过没有 client_id 的配置"""
        template_config = {"name": "test"}
        channels = [
            _make_channel_record(channel_id=1, config={"client_id": "client_1", "client_secret": "secret_1"}),
            _make_channel_record(channel_id=2, config={"client_secret": "secret_2"}),  # 无 client_id
        ]

        result = self._call_apply_template(channel_service, template_config, channels)

        accounts = result["channels"]["dingtalk"]["accounts"]
        assert len(accounts) == 1
        assert "client_1" in accounts

    def test_template_custom_fields_inherited(self, channel_service):
        """模版自定义字段被继承"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "open",
                    "customField": "custom_value",
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        assert account["customField"] == "custom_value"

    def test_template_message_type_excluded(self, channel_service):
        """模版 messageType 被排除，使用代码默认值"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "messageType": "card",  # 模版中的 messageType 应该被忽略
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        # 应该使用代码默认值，而不是模版值
        assert account["messageType"] == "markdown"

    def test_template_card_template_id_excluded(self, channel_service):
        """模版 cardTemplateId 被排除，使用数据库值"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "cardTemplateId": "template_from_template",  # 应该被忽略
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={
                "client_id": "client_1",
                "client_secret": "secret_1",
                "card_template_id": "template_from_db",
            },
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        # 应该使用数据库值，而不是模版值
        assert account["cardTemplateId"] == "template_from_db"

    def test_no_group_policy_uses_default(self, channel_service):
        """模版无 groupPolicy 时使用默认值"""
        template_config = {
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "restricted",
                    # 没有 groupPolicy
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        account = result["channels"]["dingtalk"]["accounts"]["client_1"]
        assert account["dmPolicy"] == "restricted"
        assert account["groupPolicy"] == "open"  # 代码默认值

    def test_other_channels_preserved(self, channel_service):
        """其他渠道（如 bcs）保持不变"""
        template_config = {
            "name": "test",
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "restricted",
                },
                "bcs": {
                    "enabled": True,
                    "url": "http://bcs.example.com",
                },
            },
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)

        # bcs 渠道保持不变
        assert result["channels"]["bcs"]["enabled"] is True
        assert result["channels"]["bcs"]["url"] == "http://bcs.example.com"
        # dingtalk 正常处理
        assert "accounts" in result["channels"]["dingtalk"]

    def test_deep_copy_not_modify_original(self, channel_service):
        """深拷贝不修改原配置"""
        template_config = {
            "name": "test",
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "dmPolicy": "restricted",
                },
            },
            "nested": {"key": "value"},
        }
        channels = [_make_channel_record(
            channel_id=1,
            config={"client_id": "client_1", "client_secret": "secret_1"},
        )]

        result = self._call_apply_template(channel_service, template_config, channels)
        result["nested"]["key"] = "changed"

        assert template_config["nested"]["key"] == "value"


# ============== Tests for generate_openclaw_configs ==============

class TestGenerateOpenclawConfigs:
    """Test generate_openclaw_configs method."""

    @pytest.mark.asyncio
    async def test_returns_base_config_when_no_verify_online_channels(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """无 verify/online 配置时返回 enabled=false"""
        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        # 基础配置包含钉钉配置
        mock_json_config = MagicMock()
        mock_json_config.to_dict.return_value = {
            "name": "test",
            "channels": {
                "dingtalk": {"enabled": True, "clientId": "draft_client", "dmPolicy": "restricted"},
                "other": {"enabled": False},
            },
        }

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(return_value=mock_json_config),
        ):
            # 无任何配置
            mock_repository.get_by_type_and_identity_ids.return_value = []

            result = await channel_service.generate_openclaw_configs(
                bot_id="bot1", owner_id="user1"
            )

            # 返回 enabled=false（无数据库配置时禁用）
            verify_data = json.loads(result.verify)
            online_data = json.loads(result.online)

            # 钉钉结构保留，enabled 应该是 false
            assert "dingtalk" in verify_data["channels"]
            assert verify_data["channels"]["dingtalk"]["enabled"] is False

            # 其他配置保留
            assert "other" in verify_data["channels"]
            assert "other" in online_data["channels"]

    @pytest.mark.asyncio
    async def test_returns_verify_config_only(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """只有 verify 配置时返回 verify 配置，online 返回基础配置（无钉钉时保持不变）"""

        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        mock_json_config = MagicMock()
        mock_json_config.to_dict.return_value = {"name": "test", "channels": {}}

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(return_value=mock_json_config),
        ):
            mock_repository.get_by_type_and_identity_ids.return_value = [
                _make_channel_record(
                    channel_id=1,
                    stage="verify",
                    status="1",
                    config={"client_id": "verify_client", "client_secret": "verify_secret"},
                ),
                # draft 配置，不应被选中
                _make_channel_record(
                    channel_id=2,
                    stage="draft",
                    status="1",
                    config={"client_id": "draft_client", "client_secret": "draft_secret"},
                ),
            ]

            result = await channel_service.generate_openclaw_configs(
                bot_id="bot1", owner_id="user1"
            )

            # verify 有钉钉配置
            verify_data = json.loads(result.verify)
            assert "verify_client" in verify_data["channels"]["dingtalk"]["accounts"]

            # online 无钉钉配置，返回 enabled=false
            online_data = json.loads(result.online)
            assert online_data["channels"]["dingtalk"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_returns_online_config_only(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """只有 online 配置时返回 online 配置，verify 返回基础配置（无钉钉时保持不变）"""
        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        mock_json_config = MagicMock()
        mock_json_config.to_dict.return_value = {"name": "test", "channels": {}}

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(return_value=mock_json_config),
        ):
            mock_repository.get_by_type_and_identity_ids.return_value = [
                _make_channel_record(
                    channel_id=1,
                    stage="online",
                    status="1",
                    config={"client_id": "online_client", "client_secret": "online_secret"},
                ),
            ]

            result = await channel_service.generate_openclaw_configs(
                bot_id="bot1", owner_id="user1"
            )

            # verify 无钉钉配置，返回 enabled=false
            verify_data = json.loads(result.verify)
            assert verify_data["channels"]["dingtalk"]["enabled"] is False

            # online 有钉钉配置
            online_data = json.loads(result.online)
            assert "online_client" in online_data["channels"]["dingtalk"]["accounts"]

    @pytest.mark.asyncio
    async def test_returns_both_configs(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """同时有 verify 和 online 配置时都返回"""

        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        mock_json_config = MagicMock()
        mock_json_config.to_dict.return_value = {"name": "test", "channels": {}}

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(return_value=mock_json_config),
        ):
            mock_repository.get_by_type_and_identity_ids.return_value = [
                _make_channel_record(
                    channel_id=1,
                    stage="verify",
                    status="1",
                    config={"client_id": "verify_client", "client_secret": "verify_secret"},
                ),
                _make_channel_record(
                    channel_id=2,
                    stage="online",
                    status="1",
                    config={"client_id": "online_client", "client_secret": "online_secret"},
                ),
            ]

            result = await channel_service.generate_openclaw_configs(
                bot_id="bot1", owner_id="user1"
            )

            verify_data = json.loads(result.verify)
            online_data = json.loads(result.online)

            assert "verify_client" in verify_data["channels"]["dingtalk"]["accounts"]
            assert "online_client" in online_data["channels"]["dingtalk"]["accounts"]

    @pytest.mark.asyncio
    async def test_filters_by_status(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """只选择 status='1' 的配置"""

        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        mock_json_config = MagicMock()
        mock_json_config.to_dict.return_value = {"name": "test", "channels": {}}

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(return_value=mock_json_config),
        ):
            mock_repository.get_by_type_and_identity_ids.return_value = [
                # 生效的 verify 配置
                _make_channel_record(
                    channel_id=1,
                    stage="verify",
                    status="1",
                    config={"client_id": "verify_client"},
                ),
                # 失效的 verify 配置
                _make_channel_record(
                    channel_id=2,
                    stage="verify",
                    status="0",
                    config={"client_id": "inactive_verify"},
                ),
            ]

            result = await channel_service.generate_openclaw_configs(
                bot_id="bot1", owner_id="user1"
            )

            verify_data = json.loads(result.verify)
            accounts = verify_data["channels"]["dingtalk"]["accounts"]
            assert "verify_client" in accounts
            assert "inactive_verify" not in accounts

    @pytest.mark.asyncio
    async def test_removes_existing_dingtalk_from_base_config(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """从基础配置中移除已有的钉钉配置，用生效配置替换"""

        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        # 基础配置包含 draft 钉钉配置
        mock_json_config = MagicMock()
        mock_json_config.to_dict.return_value = {
            "name": "test",
            "channels": {
                "dingtalk": {
                    "enabled": True,
                    "clientId": "draft_client",
                },
            },
        }

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(return_value=mock_json_config),
        ):
            mock_repository.get_by_type_and_identity_ids.return_value = [
                _make_channel_record(
                    channel_id=1,
                    stage="verify",
                    status="1",
                    config={"client_id": "verify_client", "client_secret": "verify_secret"},
                ),
            ]

            result = await channel_service.generate_openclaw_configs(
                bot_id="bot1", owner_id="user1"
            )

            verify_data = json.loads(result.verify)
            accounts = verify_data["channels"]["dingtalk"]["accounts"]
            # draft_client 被移除，只有 verify_client
            assert "draft_client" not in accounts
            assert "verify_client" in accounts
            # enabled 为 true
            assert verify_data["channels"]["dingtalk"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_raises_file_not_found(
        self, channel_service, mock_repository, mock_bot_service, mock_device_fs_dispatcher
    ):
        """openclaw.json 不存在时抛出 FileNotFoundError"""

        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs = MagicMock()
        mock_device_fs_dispatcher.dispatch.return_value = mock_device_fs

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile.load",
            AsyncMock(side_effect=FileNotFoundError("config not found")),
        ):
            with pytest.raises(FileNotFoundError):
                await channel_service.generate_openclaw_configs(
                    bot_id="bot1", owner_id="user1"
                )

class TestProviderDispatch:
    """ChannelService routes channel sync according to the bot engine.

    teclaw → best-effort recompose and deliver (persist-then-deliver);
    non-teclaw → the existing openclaw.json write (fail-closed, sync-then-persist).
    """

    def _openclaw_bot(self, mock_bot_service):
        mock_bot_service.get_bot.return_value = {"active_engine": "openclaw"}

    def _teclaw_bot(self, mock_bot_service):
        mock_bot_service.get_bot.return_value = {"active_engine": "teclaw"}

    # ── teclaw: persist-then-deliver, best-effort ───────────────────────────
    @pytest.mark.asyncio
    async def test_set_status_teclaw_persists_then_delivers(
        self, channel_service, mock_repository, mock_bot_service, mock_device_sync_dispatcher,
        mock_resolver,
    ):
        """teclaw enable: DB status persisted BEFORE delivery; delivery goes
        through the dispatcher (sync_symlinks), NOT the openclaw file write."""
        self._teclaw_bot(mock_bot_service)
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=1, bind_bot_id="botT", identity_id="owner1"
        )
        plugin = MagicMock()
        mock_device_sync_dispatcher.dispatch.return_value = plugin

        # Record call order across the persist + deliver seam.
        calls: list[str] = []
        mock_repository.update_status_by_id.side_effect = lambda **kw: calls.append("persist")
        plugin.sync_symlinks.side_effect = lambda *a, **k: calls.append("deliver")

        await channel_service.set_channel_status(1, "1")

        mock_repository.update_status_by_id.assert_called_once_with(channel_id=1, status="1")
        mock_resolver.resolve_for_bot.assert_any_call("botT", "owner1")
        mock_device_sync_dispatcher.dispatch.assert_called_once()
        plugin.sync_symlinks.assert_called_once_with([])
        assert calls == ["persist", "deliver"]   # persist precedes deliver

    @pytest.mark.asyncio
    async def test_set_status_teclaw_delivery_failure_still_persists(
        self, channel_service, mock_repository, mock_bot_service, mock_device_sync_dispatcher
    ):
        """teclaw delivery failure must NOT fail the channel write nor raise:
        the status stays persisted (DB is source of truth), best-effort delivery."""
        self._teclaw_bot(mock_bot_service)
        mock_repository.get_by_id.return_value = _make_channel_record(channel_id=2)
        plugin = MagicMock()
        plugin.sync_symlinks.side_effect = RuntimeError("container down")
        mock_device_sync_dispatcher.dispatch.return_value = plugin

        # Does not raise despite the delivery blowing up.
        await channel_service.set_channel_status(2, "1")

        mock_repository.update_status_by_id.assert_called_once_with(channel_id=2, status="1")

    @pytest.mark.asyncio
    async def test_set_status_teclaw_unavailable_device_skips(
        self, channel_service, mock_repository, mock_bot_service, mock_device_sync_dispatcher
    ):
        """No syncable device → DeviceSyncUnavailableError is swallowed; status persisted."""
        from agentclaw.community.core.devices.services.device_sync import DeviceSyncUnavailableError

        self._teclaw_bot(mock_bot_service)
        mock_repository.get_by_id.return_value = _make_channel_record(channel_id=3)
        mock_device_sync_dispatcher.dispatch.side_effect = DeviceSyncUnavailableError("no device")

        await channel_service.set_channel_status(3, "1")

        mock_repository.update_status_by_id.assert_called_once_with(channel_id=3, status="1")

    # ── openclaw: no dispatcher delivery, fail-closed ──────────────────────────────────
    @pytest.mark.asyncio
    async def test_set_status_openclaw_uses_file_write_not_supplier(
        self, channel_service, mock_repository, mock_bot_service,
        mock_device_fs_dispatcher, mock_device_sync_dispatcher
    ):
        """openclaw enable: routes through sync_channel_to_openclaw (file write);
        the teclaw dispatcher is never consulted for delivery."""
        self._openclaw_bot(mock_bot_service)
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=4, stage=None, config={"client_id": "cid"}
        )
        mock_bot_service.get_bot_work_path.return_value = Path("/tmp/bot")
        mock_device_fs_dispatcher.dispatch.return_value = MagicMock()

        mock_json_config = AsyncMock()
        mock_json_config.get.return_value = None
        mock_json_config.exists.return_value = False
        mock_json_config.save = AsyncMock()
        mock_json_class = MagicMock()
        mock_json_class.load = AsyncMock(return_value=mock_json_config)

        with patch(
            "agentclaw.community.core.channel.services.channel_service.JsonConfigFile",
            mock_json_class,
        ):
            await channel_service.set_channel_status(4, "1")

        mock_bot_service.get_bot_work_path.assert_called_once()        # file-write path ran
        mock_device_sync_dispatcher.dispatch.assert_not_called()        # no teclaw delivery
        mock_repository.update_status_by_id.assert_called_once_with(channel_id=4, status="1")

    @pytest.mark.asyncio
    async def test_set_status_openclaw_failclosed_does_not_persist(
        self, channel_service, mock_repository, mock_bot_service
    ):
        """openclaw file write raises → status is NOT persisted (fail-closed),
        and the error propagates for the router to map to HTTP."""
        self._openclaw_bot(mock_bot_service)
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=5, config={"client_id": "cid"}
        )
        # Drive a failure inside the openclaw write path.
        mock_bot_service.get_bot_work_path.side_effect = BotNotFoundError("bot gone")

        with pytest.raises(BotNotFoundError):
            await channel_service.set_channel_status(5, "1")

        mock_repository.update_status_by_id.assert_not_called()         # not persisted

    @pytest.mark.asyncio
    async def test_is_teclaw_bot_missing_bot_is_non_teclaw(
        self, channel_service, mock_bot_service
    ):
        """A missing bot resolves to non-teclaw so the openclaw path runs unchanged."""
        mock_bot_service.get_bot.side_effect = BotNotFoundError("nope")
        assert channel_service._is_teclaw_bot("botX", "u1") is False

    @pytest.mark.asyncio
    async def test_sync_active_channel_teclaw_redelivers(
        self, channel_service, mock_repository, mock_bot_service, mock_device_sync_dispatcher
    ):
        """update path: an already-active teclaw channel re-delivers via the dispatcher."""
        self._teclaw_bot(mock_bot_service)
        mock_repository.get_by_id.return_value = _make_channel_record(
            channel_id=6, bind_bot_id="botT", identity_id="owner1"
        )
        plugin = MagicMock()
        mock_device_sync_dispatcher.dispatch.return_value = plugin

        await channel_service.sync_active_channel(6)

        plugin.sync_symlinks.assert_called_once_with([])


def _bcn_record(status: str = "1", **config_extra) -> ChannelRecord:
    config = {
        "client_id": "client-1",
        "client_secret": "secret-1",
        "robot_code": "robot-1",
        "binding_mode": "bcn_gateway",
    }
    config.update(config_extra)
    return _make_channel_record(status=status, config=config)


class TestBcnGatewayLifecycle:
    @pytest.mark.asyncio
    async def test_activate_creates_binding_and_persists_id(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        record = _bcn_record(status="0")
        mock_repository.get_by_id.return_value = record
        mock_repository.update_by_id = MagicMock()
        mock_repository.update_status_by_id = MagicMock()

        await channel_service.set_channel_status(1, "1")

        mock_bcs_client.ensure_active.assert_awaited_once_with(record)
        stored = mock_repository.update_by_id.call_args.kwargs["config"]
        assert stored["bcs_binding_id"] == "bcs-binding-1"
        mock_repository.update_status_by_id.assert_called_once_with(
            channel_id=1, status="1"
        )

    @pytest.mark.asyncio
    async def test_activate_failure_does_not_persist_status(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(status="0")
        mock_repository.update_status_by_id = MagicMock()
        mock_bcs_client.ensure_active.side_effect = ChannelSyncError("BCS down")

        with pytest.raises(ChannelSyncError):
            await channel_service.set_channel_status(1, "1")
        mock_repository.update_status_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_deactivate_patches_binding_inactive(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(
            status="1", bcs_binding_id="bcs-binding-1"
        )
        mock_repository.update_status_by_id = MagicMock()

        await channel_service.set_channel_status(1, "0")

        mock_bcs_client.set_active.assert_awaited_once_with(
            "bcs-binding-1", active=False
        )
        mock_repository.update_status_by_id.assert_called_once_with(
            channel_id=1, status="0"
        )

    @pytest.mark.asyncio
    async def test_deactivate_without_binding_id_only_persists(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(status="0")
        mock_repository.update_status_by_id = MagicMock()

        await channel_service.set_channel_status(1, "0")

        mock_bcs_client.set_active.assert_not_awaited()
        mock_repository.update_status_by_id.assert_called_once_with(
            channel_id=1, status="0"
        )

    @pytest.mark.asyncio
    async def test_sync_active_ensures_and_pushes_config(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        record = _bcn_record(status="1", bcs_binding_id="bcs-binding-1")
        mock_repository.get_by_id.return_value = record

        await channel_service.sync_active_channel(1)

        mock_bcs_client.ensure_active.assert_awaited_once_with(record)
        mock_bcs_client.push_config.assert_awaited_once_with(
            record, binding_id="bcs-binding-1"
        )

    @pytest.mark.asyncio
    async def test_remove_channel_deletes_row_then_binding(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(
            bcs_binding_id="bcs-binding-1"
        )
        mock_repository.delete_by_id = MagicMock()

        await channel_service.remove_channel(1)

        mock_repository.delete_by_id.assert_called_once_with(channel_id=1)
        mock_bcs_client.delete_binding.assert_awaited_once_with("bcs-binding-1")

    @pytest.mark.asyncio
    async def test_remove_channel_swallows_binding_delete_failure(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _bcn_record(
            bcs_binding_id="bcs-binding-1"
        )
        mock_repository.delete_by_id = MagicMock()
        mock_bcs_client.delete_binding.side_effect = ChannelSyncError("BCS down")

        await channel_service.remove_channel(1)  # best-effort: 不抛

        mock_repository.delete_by_id.assert_called_once_with(channel_id=1)

    @pytest.mark.asyncio
    async def test_remove_plugin_channel_never_calls_bcs(
        self, channel_service, mock_repository, mock_bcs_client
    ):
        mock_repository.get_by_id.return_value = _make_channel_record()
        mock_repository.delete_by_id = MagicMock()

        await channel_service.remove_channel(1)

        mock_bcs_client.delete_binding.assert_not_awaited()
