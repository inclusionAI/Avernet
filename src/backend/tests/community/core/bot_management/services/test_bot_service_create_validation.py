"""Unit tests for BotService create_bot input validation.

Covers the bug fix for:
- empty bot_name should be rejected
- bot_name containing '@' (or other illegal chars) should be rejected
- bot_name longer than 32 chars should be rejected
- per-owner bot count limit must be enforced
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotNameExistsError,
    BotNameInvalidError,
    BotLimitExceededError,
    DefaultBotTeclawNotAllowedError,
    DeviceLimitError,
    validate_bot_name,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus


def _make_service(max_bots: int = 5, current_bots: int = 0, policy_service=None) -> BotService:
    svc = BotService.__new__(BotService)
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = current_bots
    svc._repository.get_by_id_and_owner.return_value = None
    svc._allocation_config = SimpleNamespace(
        mode="multi",
        max_devices_per_entity=max_bots,
    )
    svc._passport_plugin = MagicMock()
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    svc._skill_set_factory = MagicMock()
    svc._oss_record_repo = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._cleanup_service = MagicMock()
    svc._bcn_service = MagicMock()
    svc._bot_publish_repo = MagicMock()
    teclaw_provision = MagicMock()
    teclaw_provision.is_teclaw.side_effect = lambda engine: (
        (engine or "").strip().lower() == "teclaw"
    )
    svc._teclaw_provision_provider = lambda: teclaw_provision
    svc._policy_service = policy_service
    return svc


# ---------------------------------------------------------------------------
# validate_bot_name (pure helper)
# ---------------------------------------------------------------------------


class TestValidateBotName:
    def test_none_is_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name(None)

    def test_empty_string_is_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("")

    def test_whitespace_only_is_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("   ")

    def test_at_sign_is_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("bad@name")

    def test_other_special_chars_rejected(self):
        for n in ["a#b", "a/b", "a$b", "a%b", "a&b", "a*b", "a!b"]:
            with pytest.raises(BotNameInvalidError):
                validate_bot_name(n)

    def test_too_long_is_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("a" * 33)

    def test_boundary_length_accepted(self):
        assert validate_bot_name("a" * 32) == "a" * 32

    def test_valid_names_accepted(self):
        assert validate_bot_name("My Bot") == "My Bot"
        assert validate_bot_name("助手_01") == "助手_01"
        assert validate_bot_name("dev-bot-9") == "dev-bot-9"

    def test_trims_surrounding_whitespace(self):
        assert validate_bot_name("  bot  ") == "bot"

    # ---------- unicode edge cases (F) ----------

    def test_fullwidth_at_sign_rejected(self):
        # 全角＠ (U+FF20) 也应被拒（不在白名单字符集内）
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("bad＠name")

    def test_emoji_rejected(self):
        # 表情符号超出白名单，应拒绝
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("bot😀")

    def test_zero_width_space_rejected(self):
        # 零宽字符（U+200B）易被用于伪装重名，应拒绝
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("bot​name")

    def test_tab_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("a\tb")

    def test_newline_rejected(self):
        with pytest.raises(BotNameInvalidError):
            validate_bot_name("a\nb")

    def test_traditional_chinese_accepted(self):
        # 繁体中文落在 U+4E00–U+9FFF 区间内，应通过
        assert validate_bot_name("機器人") == "機器人"

    def test_fullwidth_digits_accepted_documented(self):
        # 说明性测试：unicode \w 在 re.UNICODE 模式下匹配 Nd 类目，
        # 因此全角数字 １２３ 会被放行。此测试**固定当前行为**——
        # 若未来想收紧到仅 ASCII 数字，需先评估并修改测试。
        assert validate_bot_name("Bot１２３") == "Bot１２３"


# ---------------------------------------------------------------------------
# BotService.create_bot — validation paths
# ---------------------------------------------------------------------------


class TestCreateBotValidation:
    def test_empty_bot_name_raises_invalid(self):
        svc = _make_service()
        with pytest.raises(BotNameInvalidError):
            svc.create_bot(user_id="u1", nick_name="U1", bot_name="")

    def test_bot_name_with_at_sign_raises_invalid(self):
        svc = _make_service()
        with pytest.raises(BotNameInvalidError):
            svc.create_bot(user_id="u1", nick_name="U1", bot_name="bad@name")

    def test_too_long_bot_name_raises_invalid(self):
        svc = _make_service()
        with pytest.raises(BotNameInvalidError):
            svc.create_bot(user_id="u1", nick_name="U1", bot_name="a" * 33)

    def test_bot_limit_exceeded_raises(self):
        svc = _make_service(max_bots=3, current_bots=3)
        with pytest.raises(BotLimitExceededError):
            svc.create_bot(user_id="u1", nick_name="U1", bot_name="OkName")

    def test_under_limit_passes_count_check(self):
        svc = _make_service(max_bots=3, current_bots=2)
        # Should not raise BotLimitExceededError; we only care about the
        # validation gate here, so short-circuit downstream by making
        # exists_by_bot_name return True so the existing duplicate-name
        # path raises a known error before device allocation.
        svc._repository.exists_by_bot_name.return_value = True
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNameExistsError,
        )
        with pytest.raises(BotNameExistsError):
            svc.create_bot(user_id="u1", nick_name="U1", bot_name="OkName")

    def test_count_check_skipped_when_max_is_zero(self):
        # max_devices_per_entity=0 → 表示不启用上限校验，不应抛 limit 异常
        svc = _make_service(max_bots=0, current_bots=999)
        svc._check_bot_count_limit("u1")  # 不应抛异常

    def test_count_check_swallows_repo_errors(self):
        svc = _make_service(max_bots=3, current_bots=0)
        svc._repository.count_by_owner.side_effect = RuntimeError("db down")
        # 不阻断业务：异常被吞掉，按未达上限处理
        svc._check_bot_count_limit("u1")

    def test_desktop_bots_excluded_from_count_limit(self):
        # 桌面 Bot 不应计入数量限制，因此即使 mock 返回总数超限，
        # 只要 exclude_bot_type="desktop" 后的计数未超限就应通过。
        svc = _make_service(max_bots=3, current_bots=3)
        # 第一次调用带 exclude_bot_type="desktop" 时应返回 2（未超限）
        svc._repository.count_by_owner.side_effect = lambda owner_id, exclude_bot_type=None: (
            2 if exclude_bot_type == "desktop" else 3
        )
        # 不应抛出 BotLimitExceededError
        svc._check_bot_count_limit("u1")

    def test_preflight_uses_count_limit(self):
        svc = _make_service(max_bots=3, current_bots=3)
        with pytest.raises(BotLimitExceededError):
            svc.check_create_bot_preflight("u1", "bot001", "openclaw")

    def test_preflight_rejects_teclaw_for_default_bot(self):
        svc = _make_service()

        with pytest.raises(
            DefaultBotTeclawNotAllowedError,
            match="Teclaw Cloud Bot 不能作为 Default Bot，请先创建其他类型的 Bot。",
        ):
            svc.check_create_bot_preflight("u1", "default", "teclaw")

    @pytest.mark.parametrize(
        ("bot_id", "engine_type"),
        [("default", "openclaw"), ("bot001", "teclaw")],
    )
    def test_preflight_allows_non_conflicting_bot_engine_pairs(
        self, bot_id, engine_type
    ):
        svc = _make_service()

        svc.check_create_bot_preflight("u1", bot_id, engine_type)

    def test_create_bot_rejects_default_teclaw_before_persistence(self):
        svc = _make_service()

        with pytest.raises(DefaultBotTeclawNotAllowedError):
            svc.create_bot(
                user_id="u1",
                nick_name="U1",
                bot_id="default",
                engine_type="teclaw",
            )

        svc._repository.create.assert_not_called()


# ---------------------------------------------------------------------------
# get_bots_ceiling_for_owner: policy 优先于 config
# ---------------------------------------------------------------------------


class TestGetBotsCeilingForOwner:
    def test_no_policy_service_falls_back_to_config(self):
        svc = _make_service(max_bots=3, policy_service=None)
        assert svc.get_bots_ceiling_for_owner("u1") == 3

    def test_policy_service_returns_ceiling(self):
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 10
        svc = _make_service(max_bots=5, policy_service=ps)
        assert svc.get_bots_ceiling_for_owner("u1") == 10

    def test_policy_service_exception_falls_back_to_config(self):
        ps = MagicMock()
        ps.get_bots_ceiling.side_effect = Exception("db error")
        svc = _make_service(max_bots=5, policy_service=ps)
        assert svc.get_bots_ceiling_for_owner("u1") == 5


class TestCheckBotCountLimitWithPolicy:
    def test_policy_ceiling_overrides_config(self):
        """policy 返回 8，config 是 5，当前 6 个 bot → 不应超限"""
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 8
        svc = _make_service(max_bots=5, current_bots=6, policy_service=ps)
        # 6 < 8, should not raise
        svc._check_bot_count_limit("u1")

    def test_policy_ceiling_still_blocks_when_exceeded(self):
        """policy 返回 8，当前 8 个 bot → 应超限"""
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 8
        svc = _make_service(max_bots=5, current_bots=8, policy_service=ps)
        with pytest.raises(BotLimitExceededError):
            svc._check_bot_count_limit("u1")


# ---------------------------------------------------------------------------
# _check_device_limit: 上限来源复用 per-user ceiling
# ---------------------------------------------------------------------------


def _bound_bot(idx: int) -> dict:
    return {
        "bot_id": f"bot-{idx}",
        "binding_id": f"binding-{idx}",
        "status": "active",
        "bot_type": "cloud",
    }


def _make_device_service(active_count: int):
    """Device service whose get_device reports `active_count` ACTIVE bindings."""
    service = MagicMock()

    def _get_device(binding_id: str):
        idx = int(binding_id.split("-")[-1])
        status = (
            DeviceBindingStatus.ACTIVE.value
            if idx < active_count
            else DeviceBindingStatus.RELEASED.value
        )
        return SimpleNamespace(status=status)

    service.get_device.side_effect = _get_device
    return service


class TestCheckDeviceLimitUsesCeiling:
    def test_ceiling_overrides_config_for_device_limit(self):
        """ceiling=8、已绑定 5 个 device 的 bot → 仍可创建第 6 个。

        回归：device limit 之前写死 config 默认值 5，导致动态上限对
        绑定了 device 的 bot 不生效。现复用 ceiling 后应放行。
        """
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 8
        svc = _make_service(max_bots=5, policy_service=ps)
        bots = [_bound_bot(i) for i in range(5)]
        svc._repository.list_by_owner.return_value = (len(bots), bots)
        svc._device_service_provider = lambda: _make_device_service(active_count=5)
        # 5 active devices < ceiling 8 → must not raise
        svc._check_device_limit(entity_id="u1", entity_type="staff", owner_id="u1")

    def test_device_limit_blocks_at_ceiling(self):
        """ceiling=8、已绑定 8 个 active device → 应抛 DeviceLimitError"""
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 8
        svc = _make_service(max_bots=5, policy_service=ps)
        bots = [_bound_bot(i) for i in range(8)]
        svc._repository.list_by_owner.return_value = (len(bots), bots)
        svc._device_service_provider = lambda: _make_device_service(active_count=8)
        with pytest.raises(DeviceLimitError):
            svc._check_device_limit(entity_id="u1", entity_type="staff", owner_id="u1")

    def test_stopped_binding_counts_toward_device_limit(self):
        """STOPPED bindings remain restartable, so they still consume a device slot."""
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 1
        svc = _make_service(max_bots=5, policy_service=ps)
        svc._repository.list_by_owner.return_value = (1, [_bound_bot(0)])
        device_service = MagicMock()
        device_service.get_device.return_value = SimpleNamespace(
            status=DeviceBindingStatus.STOPPED.value,
        )
        svc._device_service_provider = lambda: device_service

        with pytest.raises(DeviceLimitError):
            svc._check_device_limit(entity_id="u1", entity_type="staff", owner_id="u1")

    def test_device_limit_skipped_when_ceiling_non_positive(self):
        """ceiling<=0（config 无效）→ 提前放行，不因 >=0 恒真拦住第一个 bot"""
        ps = MagicMock()
        ps.get_bots_ceiling.return_value = 0
        svc = _make_service(max_bots=0, policy_service=ps)
        bots = [_bound_bot(i) for i in range(3)]
        svc._repository.list_by_owner.return_value = (len(bots), bots)
        svc._device_service_provider = lambda: _make_device_service(active_count=3)
        # must not raise despite active devices present
        svc._check_device_limit(entity_id="u1", entity_type="staff", owner_id="u1")


# ---------------------------------------------------------------------------
# create_bot persists the CONFIGURED engine set, not the static list (R11/F44)
# ---------------------------------------------------------------------------


class TestCreateBotPersistsConfiguredEngines:
    """A bot's enabled-engine list must be able to contain its own engine.

    ``create_bot`` used to persist the static ``SUPPORTED_ENGINE_TYPES`` while
    validation and ``switch_engine`` both consult ``_get_engine_types()`` (the
    ``ENGINE_TYPES`` env). On a deployment that enables ``teclaw``, a teclaw bot
    was stored with an ``engine_types`` list omitting teclaw — so switching away
    and back was permanently rejected by ``switch_engine``'s per-bot check.
    """

    def test_active_engine_is_always_in_its_own_enabled_list(self, monkeypatch):
        """R15/F50: never persist a row whose active engine is not in its list.

        ``switch_engine`` validates against the bot's persisted ``engine_types``,
        so a row violating this can never return to the engine it was created
        on. The invariant held by accident while the static list was persisted
        (it contains ``DEFAULT_ENGINE_TYPE``); persisting the configured
        registry broke it wherever the two differ.
        """
        monkeypatch.setenv("ENGINE_TYPES", "teclaw")
        svc = _make_service(max_bots=10, current_bots=0)
        svc._repository.get_by_id_and_owner.return_value = None
        svc._repository.exists_by_bot_name.return_value = False
        svc._repository.insert.return_value = {"id": 1, "bot_id": "b1", "ext": {}}

        try:
            # No engine_type → defaults to openclaw, which this registry omits.
            svc.create_bot(user_id="u1", nick_name="u1", bot_name="Bot", bot_id="b1")
        except Exception:
            pass  # downstream provisioning is mocked; persistence is the subject

        assert svc._repository.insert.called, "create never reached persistence"
        persisted = svc._repository.insert.call_args[0][0]
        assert persisted["active_engine"] in persisted["engine_types"], persisted

    def test_a_supported_engine_outside_the_registry_still_creates(self, monkeypatch):
        """teclaw is absent from the default registry yet is a real engine.

        Rejecting an engine missing from the registry would break teclaw
        creation on every deployment that does not set ENGINE_TYPES — so the
        invariant is preserved by widening the persisted list, not by refusing.
        """
        svc = _make_service(max_bots=10, current_bots=0)
        svc._repository.get_by_id_and_owner.return_value = None
        svc._repository.exists_by_bot_name.return_value = False
        svc._repository.insert.return_value = {"id": 1, "bot_id": "b1", "ext": {}}

        try:
            svc.create_bot(
                user_id="u1", nick_name="u1", bot_name="Bot",
                engine_type="teclaw", bot_id="b1",
            )
        except Exception:
            pass

        assert svc._repository.insert.called, "teclaw creation was rejected"
        persisted = svc._repository.insert.call_args[0][0]
        assert "teclaw" in persisted["engine_types"], persisted["engine_types"]
        assert persisted["active_engine"] == "teclaw"

    def test_engine_types_come_from_the_configured_registry(self, monkeypatch):
        monkeypatch.setenv("ENGINE_TYPES", "openclaw,teclaw")
        from agentclaw.community.core.workspace.constants import _get_engine_types

        assert "teclaw" in _get_engine_types()

        svc = _make_service(max_bots=10, current_bots=0)
        svc._repository.insert.return_value = {"id": 1, "bot_id": "b1", "ext": {}}
        # No existing row and the name is free — otherwise create_bot returns
        # or raises before reaching persistence.
        svc._repository.get_by_id_and_owner.return_value = None
        svc._repository.exists_by_bot_name.return_value = False
        try:
            svc.create_bot(
                user_id="u1", nick_name="u1", bot_name="Bot",
                engine_type="teclaw", bot_id="b1",
            )
        except Exception:
            # Downstream provisioning is mocked out; the persisted payload is
            # what this test is about, and insert() is reached before it.
            pass

        assert svc._repository.insert.called, "create never reached persistence"
        persisted = svc._repository.insert.call_args[0][0]
        assert "teclaw" in persisted["engine_types"], persisted["engine_types"]
        # The active engine is always in its own enabled list — the invariant
        # switch_engine depends on.
        assert persisted["active_engine"] in persisted["engine_types"]


# ---------------------------------------------------------------------------
# preflight rejects a taken name BEFORE Passport is applied for (R13/F48)
# ---------------------------------------------------------------------------


class TestPreflightChecksNameUniqueness:
    """A duplicate name must not cost an external Passport identity.

    ``create_bot`` rejects the duplicate, but only after the Passport
    application has happened — leaving an identity behind with no bot, and
    repeating that side effect on every retry. The preflight hook exists
    precisely for checks that can run before authorization.
    """

    def test_taken_name_is_rejected_in_preflight(self):
        svc = _make_service(max_bots=10, current_bots=0)
        svc._repository.get_by_bot_name.return_value = {"id": 9, "bot_id": "other"}

        with pytest.raises(BotNameExistsError):
            svc.check_create_bot_preflight("u1", bot_name="Taken")

    def test_free_name_passes_preflight(self):
        svc = _make_service(max_bots=10, current_bots=0)
        svc._repository.get_by_bot_name.return_value = None

        svc.check_create_bot_preflight("u1", bot_name="Free")

    def test_preflight_without_a_name_is_unchanged(self):
        """The name is optional — callers that don't know it yet still work."""
        svc = _make_service(max_bots=10, current_bots=0)
        svc.check_create_bot_preflight("u1")
        svc._repository.get_by_bot_name.assert_not_called()

    def test_count_limit_still_takes_precedence(self):
        """A quota failure is still reported even when the name is free."""
        svc = _make_service(max_bots=1, current_bots=1)
        svc._repository.get_by_bot_name.return_value = None

        with pytest.raises(BotLimitExceededError):
            svc.check_create_bot_preflight("u1", bot_name="Free")
