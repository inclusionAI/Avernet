from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError
from agentclaw.community.core.devices.services.conn_info_builders.baas_builder import (
    BaasConnInfoBuilder,
)


@pytest.fixture
def fake_binding():
    """Desktop bot binding —— ac_bots.binding_id 直接命中(Step 1)。"""
    binding = MagicMock()
    binding.id = 42
    binding.bot_id = "bot-1"
    binding.device_provider = "baas"
    binding.bot_type = "personal"
    binding.device_id = "BOT-desktop-xyz"
    binding.entity_id = "100019"
    binding.device_props = {"bolt_id": "20260527_lqk6d4og"}
    return binding


@pytest.fixture
def fake_baas_service():
    svc = MagicMock()
    # 模拟 get_ws_info 返回(覆盖 build_baas_conn_info_for_http 读到的全部字段)
    ws_info = MagicMock()
    ws_info.ws_url = "wss://baas.test/proxypass/foo/api/openclaw/ws"
    ws_info.token = "tok-xyz"
    ws_info.target = "foo"
    ws_info.expires_at = "2026-01-01T00:00:00Z"
    ws_info.paas_device_id = "BOT-some-device-id"
    ws_info.baas_base_url = "https://baas.test"
    ws_info.engine_port = 20003
    ws_info.tenant = "team_claw"
    ws_info.bot_uuid = "BOT-some-device-id"
    svc.get_ws_info.return_value = ws_info
    return svc


@pytest.fixture
def fake_bot_repo():
    """默认 Step 1 命中,返 active_engine='openclaw'。"""
    repo = MagicMock()
    repo.get_by_binding_id.return_value = {"active_engine": "openclaw"}
    repo.get_by_id_and_owner.return_value = None
    return repo


@pytest.fixture
def fake_device_repo():
    """device binding repository,for Step 2 反查路径。"""
    return MagicMock()


def _make_builder(baas_service, bot_repo, device_repo):
    return BaasConnInfoBuilder(
        baas_service=baas_service,
        bot_repository=bot_repo,
        device_binding_repository=device_repo,
    )


# ── 基础字段 / 调用契约 ───────────────────────────────────────────


def test_build_returns_dict_with_required_baas_fields(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    # 必有字段(基于现有 build_baas_conn_info_for_http 输出)
    assert "bind_id" in conn_info or "binding_id" in conn_info
    assert "bot_uuid" in conn_info
    assert "baas_base_url" in conn_info
    assert "engine_port" in conn_info
    assert "tenant" in conn_info


def test_build_calls_get_ws_info_with_binding_id(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    builder.build(fake_binding, user_id="user-1")

    fake_baas_service.get_ws_info.assert_called_once()
    call_kwargs = fake_baas_service.get_ws_info.call_args.kwargs
    assert call_kwargs.get("bind_id") == 42
    assert call_kwargs.get("device_affinity") == "user-1"


def test_build_raises_conn_info_build_error_on_get_ws_info_failure(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    fake_baas_service.get_ws_info.side_effect = Exception("baas down")
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    with pytest.raises(ConnInfoBuildError):
        builder.build(fake_binding, user_id="user-1")


# ── Step 1 命中:ac_bots.binding_id 直接关联(桌面 bot) ────────────
#
# 现场:desktop bot owner 自己访问,ac_bots.binding_id 直接挂在 device binding 上
# 跟进:docs/superpowers/baas-refactor-dirty-work.md §7


def test_step1_hit_engine_type_claude_code(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """Step 1 直接命中,返 claude_code engine。修复目标 case。"""
    fake_bot_repo.get_by_binding_id.return_value = {
        "active_engine": "claude_code",
        "bot_type": "desktop",
    }
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "claude_code"
    fake_bot_repo.get_by_binding_id.assert_called_once_with(42)
    # Step 1 命中后不应触发 Step 2 fallback
    fake_bot_repo.get_by_id_and_owner.assert_not_called()


def test_step1_hit_engine_type_openclaw_regression_guard(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """Step 1 命中,openclaw 回归守卫。"""
    fake_bot_repo.get_by_binding_id.return_value = {
        "active_engine": "openclaw",
        "bot_type": "desktop",
    }
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "openclaw"


def test_step1_hit_claude_code_with_non_normalcc_template_normalizes_to_aicoding(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """claude_code + 非 normalCC template_type 应归一到 aicoding。"""
    fake_bot_repo.get_by_binding_id.return_value = {
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
        "bot_type": "desktop",
    }
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "aicoding"


def test_step1_hit_claude_code_with_normalcc_stays_claude_code(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """claude_code + normalCC 保持 claude_code。"""
    fake_bot_repo.get_by_binding_id.return_value = {
        "active_engine": "claude_code",
        "template_type": "normalCC",
        "bot_type": "desktop",
    }
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "claude_code"


# ── Step 2 反查:service bot via binding.device_props.bolt_id ──────
#
# 现场:trace 0be8ed2217816832272041880e9da7
# service bot 在 ac_bots 表里只有一条 owner 个人 ARCA binding 关联的 row,
# 经 baas publish_ext 拿到的 binding_id 不在 ac_bots.binding_id 列上;
# 通过 binding.device_props.bolt_id(=bot_id)反查 ac_bots。


def test_step2_fallback_resolves_service_bot_via_bolt_id(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """Step 1 miss → Step 2 通过 device_props.bolt_id 反查 ac_bots。"""
    # Step 1 查不到(service bot baas publish_ext binding 经典场景)
    fake_bot_repo.get_by_binding_id.return_value = None
    # Step 2:by (bolt_id, entity_id) 查到 bot
    fake_bot_repo.get_by_id_and_owner.return_value = {
        "active_engine": "openclaw",
        "bot_type": "service",
    }
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "openclaw"
    fake_bot_repo.get_by_binding_id.assert_called_once_with(42)
    # bolt_id="20260527_lqk6d4og"(fake_binding fixture), entity_id="100019"
    fake_bot_repo.get_by_id_and_owner.assert_called_once_with(
        "20260527_lqk6d4og", "100019"
    )


def test_step2_fallback_claude_code_service_bot(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """Step 2 反查,claude_code service bot 拿到正确 engine。修复目标 case。"""
    fake_bot_repo.get_by_binding_id.return_value = None
    fake_bot_repo.get_by_id_and_owner.return_value = {
        "active_engine": "claude_code",
        "bot_type": "service",
    }
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "claude_code"


# ── 完全反查不到的 fallback 路径(REL20260610 行为对齐) ─────────────


def test_fallback_to_default_engine_when_both_steps_miss(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """两步反查全部 miss → 返默认 'openclaw'(REL610 等价),不再 raise。"""
    fake_bot_repo.get_by_binding_id.return_value = None
    fake_bot_repo.get_by_id_and_owner.return_value = None
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "openclaw"
    assert conn_info["bot_type"] == ""


def test_fallback_when_device_props_has_no_bolt_id(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """binding.device_props 没 bolt_id → Step 2 跳过,直接走 fallback。"""
    fake_binding.device_props = {"some_other_field": "x"}
    fake_bot_repo.get_by_binding_id.return_value = None
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "openclaw"
    # 没 bolt_id 不应触发 Step 2
    fake_bot_repo.get_by_id_and_owner.assert_not_called()


def test_fallback_when_device_props_is_none(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """binding.device_props=None 也能安全降级。"""
    fake_binding.device_props = None
    fake_bot_repo.get_by_binding_id.return_value = None
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert conn_info["engine_type"] == "openclaw"


# ── device_uuid 透传(多实例 service bot) ───────────────────────────────


def test_build_passes_device_uuid_to_get_ws_info(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """build(device_uuid=...) 把 device_uuid 透传给 baas_service.get_ws_info(对称参数)."""
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    builder.build(fake_binding, user_id="user-1", device_uuid="DEV-xyz")

    call_kwargs = fake_baas_service.get_ws_info.call_args.kwargs
    assert call_kwargs.get("device_uuid") == "DEV-xyz"


def test_build_passes_device_uuid_into_conn_info(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """build(device_uuid=...) 把 device_uuid 写进 conn_info dict(下游 resolver/transport 用)."""
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1", device_uuid="DEV-xyz")

    assert conn_info["device_uuid"] == "DEV-xyz"


def test_build_default_device_uuid_none_omits_from_conn_info(
    fake_binding, fake_baas_service, fake_bot_repo, fake_device_repo
):
    """不传 device_uuid 时 conn_info 不含 device_uuid key(向后兼容单实例)."""
    builder = _make_builder(fake_baas_service, fake_bot_repo, fake_device_repo)

    conn_info = builder.build(fake_binding, user_id="user-1")

    assert "device_uuid" not in conn_info
    # get_ws_info 仍收到 device_uuid=None(默认值)
    assert fake_baas_service.get_ws_info.call_args.kwargs.get("device_uuid") is None
