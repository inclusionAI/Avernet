"""Unit tests for bot_run_utils.

Covers:
- resolve_user_id: 从 metadata 中解析 user_id
- extract_lifecycle_stage: 从 metadata 中提取 lifecycle_stage
- parse_bot_id: 解析 bot_id 为 real_bot_id 和 entity_id
- resolve_bot_id: 根据 binding_info 解析实际 bot_id
- extract_session_id_from_record: 从运行记录中提取 session_id
- parse_wait_result: 从 metadata 解析 ignore_content / ignore_result 标志
"""

from unittest.mock import MagicMock

import pytest

from secbaas.api.bot_runtime import BotBindingInfo
from secbaas.core.service.bot_run import (
    binding_data_to_info,
    extract_lifecycle_stage,
    extract_session_id_from_record,
    parse_bot_id,
    parse_wait_result,
    resolve_bot_id,
    resolve_user_id,
)
from secbaas.spi.bot_service import BotBindingData

BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"
APP_ID_BAAS = "301516dd13a942639420174eaa63190e"


# ==================== Mock helpers ====================


class MockBotBindingInfo:
    """Mock BotBindingInfo for testing."""

    def __init__(self, entity_id: str = "entity123", bot_type: str = "team"):
        self.entity_id = entity_id
        self.bot_type = bot_type


class MockBotChatContext:
    """Mock BotChatContext for testing."""

    def __init__(
        self,
        app_id: str = "app123",
        app_type: str = "app",
    ):
        self.app_id = app_id
        self.app_type = app_type


# ==================== Tests: resolve_user_id ====================


class TestResolveUserId:
    """测试 resolve_user_id 函数"""

    def test_from_sender_options_owner(self):
        """优先级1: sender_options.from = owner 时，取 binding_info.entity_id"""
        metadata = {"sender_options": {"from": "owner"}}
        binding_info = MockBotBindingInfo(entity_id="entity456")
        context = MockBotChatContext(app_id="app123")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "entity456"

    def test_from_sender_options_other(self):
        """优先级1: sender_options.from 非 owner 时，回退到 context.app_id"""
        metadata = {"sender_options": {"from": "other"}}
        binding_info = MockBotBindingInfo(entity_id="entity456")
        context = MockBotChatContext(app_id="app789")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app789"

    def test_from_context_app_id(self):
        """优先级2: 取 context.app_id"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(app_id="app456")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app456"

    def test_fallback_to_bot_id(self):
        """优先级3: fallback 到 bot_id"""
        metadata = {}
        binding_info = None
        context = None
        bot_id = "bot_default"

        result = resolve_user_id(metadata, binding_info, context, bot_id)
        assert result == "bot_default"

    def test_none_sender_options(self):
        """sender_options 为 None 时，回退到 context"""
        metadata = {"sender_options": None}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(app_id="app789")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app789"

    def test_empty_sender_options(self):
        """sender_options 为空时，回退到 context"""
        metadata = {"sender_options": {}}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(app_id="app789")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app789"

    def test_sender_options_without_from(self):
        """sender_options 没有 from 字段时，回退到 context"""
        metadata = {"sender_options": {"other_field": "value"}}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(app_id="app789")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app789"

    def test_none_binding_info_with_owner_flag(self):
        """binding_info 为 None 但 from=owner 时，回退到 context"""
        metadata = {"sender_options": {"from": "owner"}}
        binding_info = None
        context = MockBotChatContext(app_id="app789")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app789"

    def test_none_context(self):
        """context 为 None 时，回退到 bot_id"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = None

        result = resolve_user_id(metadata, binding_info, context, "bot_fallback")
        assert result == "bot_fallback"

    def test_empty_metadata(self):
        """空 metadata 时，回退到 context"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(app_id="app123")

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app123"

    def test_bot_app_type_extracts_entity_id_from_app_id(self):
        """优先级3: app_type='bot' 时，从 app_id（bot_id:entity_id）解析 entity_id"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(
            app_id="bot-abc:user-xyz",
            app_type="bot",
        )

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "user-xyz"

    def test_bot_app_type_app_id_no_colon_falls_back_to_app_id(self):
        """app_type='bot' 但 app_id 不含冒号时，回退到 context.app_id"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(
            app_id="no-colon-value",
            app_type="bot",
        )

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "no-colon-value"

    def test_bot_app_type_empty_app_id_falls_back_to_bot_id(self):
        """app_type='bot' 但 app_id 为空时，回退到 bot_id"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(
            app_id="",
            app_type="bot",
        )

        result = resolve_user_id(metadata, binding_info, context, "bot_fallback")
        assert result == "bot_fallback"

    def test_non_bot_app_type_uses_app_id(self):
        """app_type 非 'bot' 时，使用 context.app_id"""
        metadata = {}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(
            app_id="app456",
            app_type="app",
        )

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "app456"

    def test_bot_app_type_overrides_sender_options_non_owner(self):
        """app_type='bot' 在 sender_options 非 owner 时生效"""
        metadata = {"sender_options": {"from": "other"}}
        binding_info = MockBotBindingInfo(entity_id="entity123")
        context = MockBotChatContext(
            app_id="bot-abc:user-from-app-id",
            app_type="bot",
        )

        result = resolve_user_id(metadata, binding_info, context, "bot_id")
        assert result == "user-from-app-id"


# ==================== Tests: extract_lifecycle_stage ====================


class TestExtractLifecycleStage:
    def test_extracts_from_metadata(self):
        """Extracts lifecycle_stage from metadata.bot_options."""
        metadata = {"bot_options": {"lifecycle_stage": "draft"}}
        assert extract_lifecycle_stage(metadata) == "draft"

    def test_defaults_to_online_when_missing(self):
        """Defaults to 'online' when bot_options has no lifecycle_stage."""
        metadata = {"bot_options": {}}
        assert extract_lifecycle_stage(metadata) == "online"

    def test_defaults_to_online_when_no_bot_options(self):
        """Defaults to 'online' when metadata has no bot_options."""
        metadata = {}
        assert extract_lifecycle_stage(metadata) == "online"

    def test_defaults_to_online_when_none(self):
        """Defaults to 'online' when metadata is None."""
        assert extract_lifecycle_stage(None) == "online"

    def test_defaults_to_online_when_bot_options_not_dict(self):
        """Defaults to 'online' when bot_options is not a dict."""
        metadata = {"bot_options": "invalid"}
        assert extract_lifecycle_stage(metadata) == "online"

    def test_extracts_verify_stage(self):
        """Extracts 'verify' lifecycle_stage."""
        metadata = {"bot_options": {"lifecycle_stage": "verify"}}
        assert extract_lifecycle_stage(metadata) == "verify"

    def test_empty_string_lifecycle_stage_defaults_to_online(self):
        """Empty string lifecycle_stage defaults to 'online'."""
        metadata = {"bot_options": {"lifecycle_stage": ""}}
        assert extract_lifecycle_stage(metadata) == "online"


# ==================== Tests: parse_bot_id ====================


class TestParseBotId:
    def test_parses_full_bot_id(self):
        real_bot_id, entity_id = parse_bot_id(f"{BOT_ID}:{ENTITY_ID}")
        assert real_bot_id == BOT_ID
        assert entity_id == ENTITY_ID

    def test_parses_bot_id_without_entity(self):
        real_bot_id, entity_id = parse_bot_id(BOT_ID)
        assert real_bot_id == BOT_ID
        assert entity_id == ""

    def test_parses_empty_string(self):
        real_bot_id, entity_id = parse_bot_id("")
        assert real_bot_id == ""
        assert entity_id == ""


# ==================== Tests: resolve_bot_id ====================


@pytest.fixture
def baas_binding():
    return BotBindingInfo(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        sandbox_id=None,
        device_id=APP_ID_BAAS,
        device_provider="baas",
        binding_id=100002,
        bot_type="service",
    )


@pytest.fixture
def arca_binding():
    return BotBindingInfo(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        sandbox_id="ARCA-SANDBOX-abc@0",
        device_id="staff_bot_123",
        device_provider="arca",
        binding_id=100101,
        bot_type="personal",
    )


class TestResolveBotId:
    def test_baas_binding_returns_device_id(self, baas_binding):
        result = resolve_bot_id(f"{BOT_ID}:{ENTITY_ID}", baas_binding)
        assert result == APP_ID_BAAS

    def test_arca_binding_returns_bot_id(self, arca_binding):
        result = resolve_bot_id(f"{BOT_ID}:{ENTITY_ID}", arca_binding)
        assert result == BOT_ID

    def test_none_binding_returns_original(self):
        result = resolve_bot_id(f"{BOT_ID}:{ENTITY_ID}", None)
        assert result == f"{BOT_ID}:{ENTITY_ID}"


# ==================== Tests: extract_session_id_from_record ====================


class TestExtractSessionIdFromRecord:
    def test_extracts_from_result_extra(self):
        """优先从 result_extra JSON 中取 session_id"""
        record = MagicMock(
            result_extra={"session_id": "sess-from-extra"},
            metadata={"session_id": "sess-from-meta"},
        )
        assert extract_session_id_from_record(record) == "sess-from-extra"

    def test_falls_back_to_metadata(self):
        """result_extra 无 session_id 时，从 metadata 中取"""
        record = MagicMock(
            result_extra={"other_key": "value"},
            metadata={"session_id": "sess-from-meta"},
        )
        assert extract_session_id_from_record(record) == "sess-from-meta"

    def test_result_extra_not_dict(self):
        """result_extra 不是 dict 时，从 metadata 中取"""
        record = MagicMock(
            result_extra="not-a-dict",
            metadata={"session_id": "sess-from-meta"},
        )
        assert extract_session_id_from_record(record) == "sess-from-meta"

    def test_both_none(self):
        """result_extra 和 metadata 都为 None 时，返回 None"""
        record = MagicMock(
            result_extra=None,
            metadata=None,
        )
        assert extract_session_id_from_record(record) is None

    def test_result_extra_none_metadata_has_session(self):
        """result_extra 为 None，metadata 有 session_id"""
        record = MagicMock(
            result_extra=None,
            metadata={"session_id": "sess-from-meta"},
        )
        assert extract_session_id_from_record(record) == "sess-from-meta"

    def test_metadata_not_dict(self):
        """metadata 不是 dict 时，返回 None"""
        record = MagicMock(
            result_extra=None,
            metadata="not-a-dict",
        )
        assert extract_session_id_from_record(record) is None


# ==================== Tests: parse_wait_result ====================


class TestParseWaitResult:
    def test_no_key(self):
        assert parse_wait_result({}) is True

    # ── ignore_result (旧，兼容) ────────────────────────────────────────

    def test_ignore_result_true_bool(self):
        assert parse_wait_result({"ignore_result": True}) is False

    def test_ignore_result_false_bool(self):
        assert parse_wait_result({"ignore_result": False}) is True

    def test_ignore_result_true_string(self):
        assert parse_wait_result({"ignore_result": "true"}) is False

    def test_ignore_result_false_string(self):
        assert parse_wait_result({"ignore_result": "false"}) is True

    def test_ignore_result_zero(self):
        assert parse_wait_result({"ignore_result": 0}) is True

    # ── ignore_content (新) ─────────────────────────────────────────────

    def test_ignore_content_true_bool(self):
        assert parse_wait_result({"ignore_content": True}) is False

    def test_ignore_content_false_bool(self):
        assert parse_wait_result({"ignore_content": False}) is True

    def test_ignore_content_true_string(self):
        assert parse_wait_result({"ignore_content": "true"}) is False

    def test_ignore_content_false_string(self):
        assert parse_wait_result({"ignore_content": "false"}) is True

    # ── ignore_content 优先于 ignore_result ─────────────────────────────

    def test_ignore_content_takes_priority(self):
        """ignore_content 存在时忽略 ignore_result"""
        assert (
            parse_wait_result({"ignore_result": False, "ignore_content": True}) is False
        )


# ==================== Tests: binding_data_to_info ====================


class TestBindingDataToInfo:
    def test_basic_field_mapping(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="service",
            engine_type="openclaw",
            binding_id=100,
            device_provider="arca",
            device_id="device-001",
        )
        info = binding_data_to_info(data)

        assert info.bot_id == "bot-001"
        assert info.entity_id == "entity-001"  # owner_id → entity_id
        assert info.bot_type == "service"
        assert info.engine_type == "openclaw"
        assert info.binding_id == 100
        assert info.device_provider == "arca"
        assert info.device_id == "device-001"

    def test_arca_provider_sandbox_id_equals_device_id(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="personal",
            engine_type="openclaw",
            binding_id=100,
            device_provider="arca",
            device_id="sandbox-abc@0",
        )
        info = binding_data_to_info(data)

        assert info.sandbox_id == "sandbox-abc@0"
        assert info.device_id == "sandbox-abc@0"

    def test_non_arca_provider_sandbox_id_is_none(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="service",
            engine_type="openclaw",
            binding_id=100,
            device_provider="baas",
            device_id="app-id-123",
        )
        info = binding_data_to_info(data)

        assert info.sandbox_id is None
        assert info.device_id == "app-id-123"

    def test_owner_id_maps_to_entity_id(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="owner-xyz",
            bot_type="personal",
            engine_type="openclaw",
        )
        info = binding_data_to_info(data)
        assert info.entity_id == "owner-xyz"

    def test_empty_engine_type_defaults_to_openclaw(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="service",
            engine_type="",
        )
        info = binding_data_to_info(data)
        assert info.engine_type == "openclaw"

    def test_device_props_is_always_empty_dict(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="service",
            engine_type="openclaw",
        )
        info = binding_data_to_info(data)
        assert info.device_props == {}

    def test_baas_session_id_is_always_none(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="service",
            engine_type="openclaw",
        )
        info = binding_data_to_info(data)
        assert info.baas_session_id is None

    def test_publish_fields_not_carried_over(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="service",
            engine_type="openclaw",
            publish_id=42,
            publish_status="success",
        )
        info = binding_data_to_info(data)
        # BotBindingInfo has no publish_id/publish_status fields
        assert not hasattr(info, "publish_id")
        assert not hasattr(info, "publish_status")

    def test_local_provider_sandbox_id_is_none(self):
        data = BotBindingData(
            bot_id="bot-001",
            owner_id="entity-001",
            bot_type="personal",
            engine_type="openclaw",
            binding_id=1,
            device_provider="local",
            device_id="local-device",
        )
        info = binding_data_to_info(data)

        assert info.sandbox_id is None
        assert info.device_id == "local-device"
