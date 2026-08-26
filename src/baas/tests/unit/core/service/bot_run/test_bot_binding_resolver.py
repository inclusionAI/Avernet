"""Unit tests for BotBindingResolver.

Covers:
- Personal bot: uses ac_bots.binding_id directly
- Service bot: resolves binding_id via lifecycle_stage (draft/online/verify)
- Default bot (bot_id="default"): uses get_by_entity_id_bot_id_env
- Edge cases: bot not found, binding not found, device_props=None, entity_id=None
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.api.eval_env import DYNAMIC_ENV_TAG_KEY
from secbaas.community.core.repository.ac_bot import AcBotRecord
from secbaas.community.core.repository.device_binding import (
    DeviceBindingRecord,
)
from secbaas.community.core.service.bot_run import BotBindingResolver

# ==================== Fixtures ====================

BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"
ENV = "prod"
BINDING_ID_DRAFT = 100001
BINDING_ID_ONLINE = 100002
BINDING_ID_VERIFY = 100003
DEVICE_ID_BAAS = "test-device-uuid-00000000000000000000000000001"
DEVICE_ID_ARCA = "staff_test_uuid_test_bot_foo"


def _make_bot_record(
    bot_type="personal",
    binding_id=BINDING_ID_DRAFT,
    device_id=DEVICE_ID_ARCA,
    bot_id=BOT_ID,
    entity_id=ENTITY_ID,
    active_engine="openclaw",
    template_type=None,
):
    now = datetime.now()
    return AcBotRecord(
        id=1,
        bot_id=bot_id,
        bot_name="test-bot",
        bot_desc=None,
        entity_id=entity_id,
        entity_type="staff",
        creator_id=entity_id,
        owner_id=entity_id,
        engine_types=["openclaw"],
        status="ACTIVE",
        binding_id=binding_id,
        gmt_create=now,
        gmt_modified=now,
        modifier_id=None,
        share_policy=None,
        is_delete=0,
        active_engine=active_engine,
        device_id=device_id,
        env=ENV,
        owner_name="test",
        public="0",
        ext=None,
        template_type=template_type,
        bot_type=bot_type,
    )


def _make_binding_record(
    device_provider="baas",
    device_id=DEVICE_ID_BAAS,
    binding_id=BINDING_ID_ONLINE,
    props=None,
):
    now = datetime.now()
    return DeviceBindingRecord(
        id=binding_id,
        entity_id=ENTITY_ID,
        entity_type="staff",
        device_id=device_id,
        device_provider=device_provider,
        env=ENV,
        device_props=props or {},
        status="ACTIVE",
        apply_reason=None,
        applied_by="system",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=now,
        gmt_create=now,
        gmt_modified=now,
    )


@pytest.fixture
def mock_ac_bot_repo():
    return MagicMock()


@pytest.fixture
def mock_publish_repo():
    return MagicMock()


@pytest.fixture
def mock_binding_repo():
    return MagicMock()


@pytest.fixture
def resolver(mock_ac_bot_repo, mock_publish_repo, mock_binding_repo):
    return BotBindingResolver(
        ac_bot_repo=mock_ac_bot_repo,
        publish_repo=mock_publish_repo,
        binding_repo=mock_binding_repo,
    )


# ==================== Tests: personal bot ====================


class TestPersonalBot:
    def test_personal_bot_uses_ac_bots_binding_id(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="personal", binding_id=BINDING_ID_DRAFT)
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="arca",
            device_id=DEVICE_ID_ARCA,
            binding_id=BINDING_ID_DRAFT,
            props={"sandbox_id": "ARCA-SANDBOX-abc@0"},
        )

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is not None
        assert result.bot_type == "personal"
        assert result.binding_id == BINDING_ID_DRAFT
        assert result.device_id == DEVICE_ID_ARCA
        assert result.device_provider == "arca"
        assert result.sandbox_id == "ARCA-SANDBOX-abc@0"
        mock_publish_repo.get_binding_id.assert_not_called()


# ==================== Tests: service bot ====================


class TestServiceBot:
    def test_service_bot_uses_online_binding(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_ONLINE
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_ONLINE,
        )

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is not None
        assert result.bot_type == "service"
        assert result.binding_id == BINDING_ID_ONLINE
        assert result.device_id == DEVICE_ID_BAAS
        assert result.device_provider == "baas"
        assert result.sandbox_id is None
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )

    def test_service_bot_online_no_publish_returns_none(
        self, resolver, mock_ac_bot_repo, mock_publish_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = None

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is None
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )

    def test_service_bot_draft_stage_uses_draft_binding_id(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="arca",
            device_id=DEVICE_ID_ARCA,
            binding_id=BINDING_ID_DRAFT,
            props={"sandbox_id": "ARCA-SANDBOX-draft@0"},
        )

        result = resolver.resolve(
            bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV, lifecycle_stage="draft"
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_DRAFT
        assert result.sandbox_id == "ARCA-SANDBOX-draft@0"
        mock_publish_repo.get_binding_id.assert_not_called()

    def test_service_bot_draft_stage_no_binding_returns_none(
        self, resolver, mock_ac_bot_repo, mock_publish_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=None)
        )

        result = resolver.resolve(
            bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV, lifecycle_stage="draft"
        )

        assert result is None

    def test_service_bot_verify_stage_queries_validating(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_VERIFY
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_VERIFY,
        )

        result = resolver.resolve(
            bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV, lifecycle_stage="verify"
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_VERIFY
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="validating", owner_id=ENTITY_ID
        )

    def test_service_bot_verify_no_publish_returns_none(
        self, resolver, mock_ac_bot_repo, mock_publish_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = None

        result = resolver.resolve(
            bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV, lifecycle_stage="verify"
        )

        assert result is None
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="validating", owner_id=ENTITY_ID
        )

    def test_service_bot_unknown_stage_defaults_to_success(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_ONLINE
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_ONLINE,
        )

        result = resolver.resolve(
            bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV, lifecycle_stage="unknown"
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )


# ==================== Tests: default bot ====================


class TestDefaultBot:
    def test_default_bot_with_entity_id(
        self, resolver, mock_ac_bot_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_entity_id_bot_id_env.return_value = _make_bot_record(
            bot_type="personal",
            binding_id=BINDING_ID_DRAFT,
            bot_id="default",
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="arca",
            device_id=DEVICE_ID_ARCA,
            binding_id=BINDING_ID_DRAFT,
        )

        result = resolver.resolve(bot_id="default", entity_id=ENTITY_ID, env=ENV)

        assert result is not None
        assert result.binding_id == BINDING_ID_DRAFT
        mock_ac_bot_repo.get_by_entity_id_bot_id_env.assert_called_once_with(
            entity_id=ENTITY_ID, bot_id="default", env=ENV
        )
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.assert_not_called()

    def test_default_bot_without_entity_id_returns_none(
        self, resolver, mock_ac_bot_repo
    ):
        result = resolver.resolve(bot_id="default", entity_id=None, env=ENV)

        assert result is None

    def test_default_bot_not_found_returns_none(self, resolver, mock_ac_bot_repo):
        mock_ac_bot_repo.get_by_entity_id_bot_id_env.return_value = None

        result = resolver.resolve(bot_id="default", entity_id=ENTITY_ID, env=ENV)

        assert result is None


# ==================== Tests: edge cases ====================


class TestEdgeCases:
    def test_bot_not_found_returns_none(self, resolver, mock_ac_bot_repo):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = None

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is None

    def test_personal_bot_no_binding_returns_none(self, resolver, mock_ac_bot_repo):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="personal", binding_id=None)
        )

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is None

    def test_binding_not_found_returns_none(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_ONLINE
        mock_binding_repo.get_by_id.return_value = None

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is None

    def test_binding_with_none_device_props(
        self, resolver, mock_ac_bot_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="personal", binding_id=BINDING_ID_DRAFT)
        )
        binding = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_DRAFT,
        )
        binding.device_props = None
        mock_binding_repo.get_by_id.return_value = binding

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is not None
        assert result.sandbox_id is None
        assert result.device_props == {}

    def test_entity_id_none_for_non_default_bot(
        self, resolver, mock_ac_bot_repo, mock_binding_repo
    ):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="personal", binding_id=BINDING_ID_DRAFT)
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="arca",
            device_id=DEVICE_ID_ARCA,
            binding_id=BINDING_ID_DRAFT,
        )

        result = resolver.resolve(bot_id=BOT_ID, entity_id=None, env=ENV)

        assert result is not None
        assert result.entity_id == ENTITY_ID


class TestDefaultBotId:
    """Tests for default bot_id query path."""

    def test_default_bot_without_entity_id_returns_none(
        self, resolver, mock_ac_bot_repo
    ):
        """bot_id='default' with entity_id=None returns None."""
        result = resolver.resolve(bot_id="default", entity_id=None, env=ENV)
        assert result is None

    def test_default_bot_with_entity_id(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """bot_id='default' with valid entity_id succeeds."""
        mock_ac_bot_repo.get_by_entity_id_bot_id_env.return_value = _make_bot_record(
            bot_type="personal",
            binding_id=BINDING_ID_DRAFT,
            bot_id="default",
            entity_id=ENTITY_ID,
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_DRAFT,
        )

        result = resolver.resolve(bot_id="default", entity_id=ENTITY_ID, env=ENV)

        assert result is not None
        assert result.bot_id == "default"
        assert result.entity_id == ENTITY_ID
        mock_ac_bot_repo.get_by_entity_id_bot_id_env.assert_called_once()
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.assert_not_called()


class TestServiceBotDraft:
    """Tests for service bot draft lifecycle_stage."""

    def test_service_bot_draft_uses_draft_binding_id(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Service bot in draft stage uses ac_bots.binding_id directly."""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="arca",
            device_id=DEVICE_ID_ARCA,
            binding_id=BINDING_ID_DRAFT,
        )

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="draft",
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_DRAFT
        mock_publish_repo.get_binding_id.assert_not_called()

    def test_service_bot_draft_without_binding_returns_none(
        self, resolver, mock_ac_bot_repo
    ):
        """Service bot in draft without binding_id returns None."""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=None)
        )

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="draft",
        )

        assert result is None


class TestServiceBotVerify:
    """Tests for service bot verify lifecycle_stage."""

    def test_service_bot_verify_queries_validating_status(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Service bot in verify stage queries publish with status='validating'."""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_ONLINE
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_ONLINE,
        )

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="verify",
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="validating", owner_id=ENTITY_ID
        )

    def test_service_bot_unknown_lifecycle_falls_back_to_success(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Unknown lifecycle_stage falls back to status='success'."""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_ONLINE
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_ONLINE,
        )

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="unknown_stage",
        )

        assert result is not None
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )


class TestNonDefaultBotWithoutEntityId:
    """Tests for non-default bot_id where entity_id is auto-resolved."""

    def test_non_default_bot_auto_resolves_entity_id(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Non-default bot_id resolves entity_id from ac_bot record."""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(
                bot_type="personal",
                binding_id=BINDING_ID_DRAFT,
                bot_id=BOT_ID,
                entity_id="auto-resolved-entity",
            )
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_DRAFT,
        )

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=None,  # not provided
            env=ENV,
        )

        assert result is not None
        assert result.entity_id == "auto-resolved-entity"


class TestBotBindingInfoEdgeCases:
    """Tests for BotBindingInfo dataclass edge cases."""

    def test_binding_info_defaults(self):
        """BotBindingInfo defaults are correctly set."""
        from secbaas.community.api.bot_runtime import BotBindingInfo

        info = BotBindingInfo(bot_id=BOT_ID, entity_id=ENTITY_ID)

        assert info.bot_id == BOT_ID
        assert info.entity_id == ENTITY_ID
        assert info.sandbox_id is None
        assert info.device_id == ""
        assert info.device_provider == ""
        assert info.binding_id == 0
        assert info.device_props == {}
        assert info.bot_type == "personal"

    def test_resolve_handles_none_device_props(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """resolve() converts None device_props to empty dict."""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="personal", binding_id=BINDING_ID_DRAFT)
        )
        # Binding with None device_props
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_DRAFT,
            props=None,
        )

        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)

        assert result is not None
        assert result.device_props == {}
        assert result.sandbox_id is None


class TestEngineTypeWhitelist:
    """§7 — active_engine 白名单校验（unknown → openclaw + warn）。"""

    def _setup(self, mock_ac_bot_repo, mock_binding_repo, active_engine):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(
                bot_type="personal",
                binding_id=BINDING_ID_DRAFT,
                active_engine=active_engine,
            )
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_DRAFT,
        )

    @pytest.mark.parametrize(
        "engine", ["openclaw", "teclaw", "aicoding", "hermes", "claude_code"]
    )
    def test_whitelisted_engine_preserved(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, engine
    ):
        self._setup(mock_ac_bot_repo, mock_binding_repo, engine)
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == engine

    def test_unknown_engine_falls_back_to_openclaw(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        self._setup(mock_ac_bot_repo, mock_binding_repo, "moltis")
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "openclaw"

    def test_empty_engine_defaults_to_openclaw(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        self._setup(mock_ac_bot_repo, mock_binding_repo, None)
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "openclaw"


class TestTemplateTypeNormalization:
    """template_type 归一化:aicoding 家族(空/personalCoding/applicationCoding)→ aicoding。

    兼容生产数据中 active_engine 与沙箱实际引擎不一致的历史 bot。
    """

    def _setup(self, mock_ac_bot_repo, mock_binding_repo, active_engine, template_type):
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(
                bot_type="personal",
                binding_id=BINDING_ID_DRAFT,
                active_engine=active_engine,
                template_type=template_type,
            )
        )
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_DRAFT,
        )

    @pytest.mark.parametrize("template_type", ["personalCoding", "applicationCoding"])
    def test_aicoding_template_overrides_active_engine(
        self,
        resolver,
        mock_ac_bot_repo,
        mock_publish_repo,
        mock_binding_repo,
        template_type,
    ):
        # personalCoding/applicationCoding + active_engine=claude_code(历史脏数据)→ aicoding
        self._setup(mock_ac_bot_repo, mock_binding_repo, "claude_code", template_type)
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "aicoding"

    @pytest.mark.parametrize("template_type", [None, ""])
    def test_empty_template_respects_active_engine(
        self,
        resolver,
        mock_ac_bot_repo,
        mock_publish_repo,
        mock_binding_repo,
        template_type,
    ):
        # template_type 为空(None 或 "")→ 以 active_engine 为准,不归一化
        self._setup(mock_ac_bot_repo, mock_binding_repo, "claude_code", template_type)
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "claude_code"

    @pytest.mark.parametrize("template_type", ["personalCoding", "applicationCoding"])
    def test_aicoding_template_only_when_active_is_claude_code(
        self,
        resolver,
        mock_ac_bot_repo,
        mock_publish_repo,
        mock_binding_repo,
        template_type,
    ):
        # template_type 属 aicoding 家族,但 active_engine 不是 claude_code → 不归一化,走 active_engine
        self._setup(mock_ac_bot_repo, mock_binding_repo, "hermes", template_type)
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "hermes"

    def test_non_aicoding_template_respects_active_engine(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        # template_type 不在 aicoding 家族(如 normalCC)→ 走 active_engine
        self._setup(mock_ac_bot_repo, mock_binding_repo, "hermes", "normalCC")
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "hermes"

    def test_none_template_respects_active_engine(
        self, resolver, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        # template_type=None(老 bot 没这字段)→ 走 active_engine,不误归一化
        self._setup(mock_ac_bot_repo, mock_binding_repo, "hermes", None)
        result = resolver.resolve(bot_id=BOT_ID, entity_id=ENTITY_ID, env=ENV)
        assert result is not None
        assert result.engine_type == "hermes"


# ==================== Tests: eval lifecycle_stage ====================


_MISSING = object()


def _make_eval_binding_resolver_plugin(enabled=True, resolved_id=_MISSING):
    """创建 mock EvalBindingResolverProtocol。"""
    from secbaas.community.spi.eval_env import EvalBindingResolverProtocol

    plugin = MagicMock(spec=EvalBindingResolverProtocol)
    plugin.is_eval_env_enabled.return_value = enabled
    if resolved_id is _MISSING:
        plugin.resolve_eval_binding.return_value = 100099
    else:
        plugin.resolve_eval_binding.return_value = resolved_id
    return plugin


class TestServiceBotEvalStage:
    """Tests for service bot eval lifecycle_stage — Plugin 委托验证。

    评测绑定的详细过滤逻辑（优先选 eval tag、过滤 RELEASED 等）已
    迁移至 EvalBindingResolverProtocol Plugin，由 RealEvalBindingResolver
    实现。此处仅验证 BotBindingResolver 对 Plugin 的委托行为。
    """

    BINDING_ID_EVAL = 100099

    def _make_eval_resolver(
        self,
        mock_ac_bot_repo,
        mock_publish_repo,
        mock_binding_repo,
        *,
        resolved_id=_MISSING,
    ):
        """创建带 eval_binding_resolver 的 BotBindingResolver。"""
        eval_plugin = _make_eval_binding_resolver_plugin(
            enabled=True,
            resolved_id=resolved_id,
        )
        resolver = BotBindingResolver(
            ac_bot_repo=mock_ac_bot_repo,
            publish_repo=mock_publish_repo,
            binding_repo=mock_binding_repo,
            eval_binding_resolver=eval_plugin,
        )
        return resolver, eval_plugin

    def test_eval_stage_delegates_to_plugin(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """lifecycle_stage='eval' 委托 EvalBindingResolverPlugin 解析 binding。"""
        resolver, eval_plugin = self._make_eval_resolver(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
        )
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )
        eval_binding = _make_binding_record(
            device_provider="baas",
            device_id="eval-uuid-001",
            binding_id=self.BINDING_ID_EVAL,
            props={DYNAMIC_ENV_TAG_KEY: "eval", "bot_id": BOT_ID},
        )
        mock_binding_repo.get_by_id.return_value = eval_binding

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        assert result is not None
        assert result.binding_id == self.BINDING_ID_EVAL
        # 验证委托调用
        eval_plugin.resolve_eval_binding.assert_called_once_with(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
        )
        # 走 eval 路径，不应查 publish 表
        mock_publish_repo.get_binding_id.assert_not_called()

    def test_eval_stage_plugin_returns_none(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Plugin 返回 None → resolve 返回 None。"""
        resolver, _ = self._make_eval_resolver(
            mock_ac_bot_repo,
            mock_publish_repo,
            mock_binding_repo,
            resolved_id=None,
        )
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        assert result is None


# ==================== Tests: eval 开关降级 ====================


class TestEvalSwitchControl:
    """Tests for eval 环境开关降级逻辑。

    当 Plugin 未注入、功能关闭或异常时，eval/default lifecycle_stage
    降级走 online binding；当 Plugin 启用时，走 eval binding。
    """

    BINDING_ID_EVAL = 100099

    def _make_resolver_with_plugin(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
    ):
        """创建带 eval_binding_resolver 的 BotBindingResolver。"""
        return BotBindingResolver(
            ac_bot_repo=mock_ac_bot_repo,
            publish_repo=mock_publish_repo,
            binding_repo=mock_binding_repo,
            eval_binding_resolver=eval_plugin,
        )

    def _setup_service_bot(self, mock_ac_bot_repo):
        """设置 service bot 记录。"""
        mock_ac_bot_repo.get_by_bot_id_env_exclude_default.return_value = (
            _make_bot_record(bot_type="service", binding_id=BINDING_ID_DRAFT)
        )

    def _setup_online_binding(self, mock_publish_repo, mock_binding_repo):
        """设置 online binding 返回。"""
        mock_publish_repo.get_binding_id.return_value = BINDING_ID_ONLINE
        mock_binding_repo.get_by_id.return_value = _make_binding_record(
            device_provider="baas",
            device_id=DEVICE_ID_BAAS,
            binding_id=BINDING_ID_ONLINE,
        )

    def _setup_eval_binding(self, mock_binding_repo):
        """设置 eval binding 返回。"""
        eval_binding = _make_binding_record(
            device_provider="baas",
            device_id="eval-uuid-001",
            binding_id=self.BINDING_ID_EVAL,
            props={DYNAMIC_ENV_TAG_KEY: "eval", "bot_id": BOT_ID},
        )
        mock_binding_repo.get_by_id.return_value = eval_binding

    # ---------- 1. eval stage + Plugin disabled → 降级走 online binding ----------

    def test_eval_stage_disabled_falls_back(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """lifecycle_stage='eval' + Plugin disabled → 降级走 online binding。"""
        eval_plugin = _make_eval_binding_resolver_plugin(enabled=False)
        resolver = self._make_resolver_with_plugin(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
        )
        self._setup_service_bot(mock_ac_bot_repo)
        self._setup_online_binding(mock_publish_repo, mock_binding_repo)

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        # 降级走 online，应查询 publish_repo
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )

    # ---------- 2. default stage + Plugin disabled → 降级走 online binding ----------

    def test_default_stage_disabled_falls_back(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """lifecycle_stage='default' + Plugin disabled → 降级走 online binding。"""
        eval_plugin = _make_eval_binding_resolver_plugin(enabled=False)
        resolver = self._make_resolver_with_plugin(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
        )
        self._setup_service_bot(mock_ac_bot_repo)
        self._setup_online_binding(mock_publish_repo, mock_binding_repo)

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="default",
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )

    # ---------- 3. Plugin enabled → 走 eval binding ----------

    def test_eval_stage_enabled_uses_eval_binding(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Plugin enabled → lifecycle_stage='eval' 走 eval binding。"""
        eval_plugin = _make_eval_binding_resolver_plugin(
            enabled=True,
            resolved_id=self.BINDING_ID_EVAL,
        )
        resolver = self._make_resolver_with_plugin(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
        )
        self._setup_service_bot(mock_ac_bot_repo)
        self._setup_eval_binding(mock_binding_repo)

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        assert result is not None
        assert result.binding_id == self.BINDING_ID_EVAL
        # 走 eval 路径，不应查询 publish_repo
        mock_publish_repo.get_binding_id.assert_not_called()

    # ---------- 4. eval_binding_resolver=None → 降级走 online ----------

    def test_no_plugin_defaults_disabled(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """eval_binding_resolver=None → 降级走 online binding。"""
        resolver = BotBindingResolver(
            ac_bot_repo=mock_ac_bot_repo,
            publish_repo=mock_publish_repo,
            binding_repo=mock_binding_repo,
        )
        self._setup_service_bot(mock_ac_bot_repo)
        self._setup_online_binding(mock_publish_repo, mock_binding_repo)

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )

    # ---------- 5. is_eval_env_enabled 抛异常 → 降级走 online ----------

    def test_plugin_exception_defaults_disabled(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """is_eval_env_enabled() 抛异常 → 降级走 online binding。"""
        eval_plugin = _make_eval_binding_resolver_plugin(enabled=False)
        eval_plugin.is_eval_env_enabled.side_effect = Exception("service unavailable")

        resolver = self._make_resolver_with_plugin(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
        )
        self._setup_service_bot(mock_ac_bot_repo)
        self._setup_online_binding(mock_publish_repo, mock_binding_repo)

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        # 异常导致 is_eval_env_enabled 返回 falsy → 降级走 online
        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )

    # ---------- 6. 降级走 online 但 online 也找不到 → 返回 None ----------

    def test_eval_disabled_online_binding_not_found_returns_none(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """Plugin disabled → 降级走 online，但 online binding 不存在 → 返回 None。"""
        eval_plugin = _make_eval_binding_resolver_plugin(enabled=False)
        resolver = self._make_resolver_with_plugin(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
        )
        self._setup_service_bot(mock_ac_bot_repo)
        # online binding 查不到
        mock_publish_repo.get_binding_id.return_value = None

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="eval",
        )

        assert result is None

    # ---------- 7. online stage 不受 Plugin 影响 ----------

    def test_online_stage_not_affected(
        self, mock_ac_bot_repo, mock_publish_repo, mock_binding_repo
    ):
        """lifecycle_stage='online' 不受 eval Plugin 影响，始终走 online binding。"""
        eval_plugin = _make_eval_binding_resolver_plugin(enabled=True)
        resolver = self._make_resolver_with_plugin(
            mock_ac_bot_repo, mock_publish_repo, mock_binding_repo, eval_plugin
        )
        self._setup_service_bot(mock_ac_bot_repo)
        self._setup_online_binding(mock_publish_repo, mock_binding_repo)

        result = resolver.resolve(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            env=ENV,
            lifecycle_stage="online",
        )

        assert result is not None
        assert result.binding_id == BINDING_ID_ONLINE
        mock_publish_repo.get_binding_id.assert_called_once_with(
            source_bot_id=BOT_ID, status="success", owner_id=ENTITY_ID
        )
