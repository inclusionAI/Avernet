from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)


@pytest.fixture
def fake_binding_repo():
    repo = MagicMock()
    return repo


@pytest.fixture
def fake_bot_repo():
    repo = MagicMock()
    # 默认返 personal,既有 case 改成显式 mock 才需要别的值
    repo.get_by_id_and_owner.return_value = {"bot_type": "personal"}
    repo.get_by_binding_id.return_value = {"bot_type": "personal"}
    return repo


@pytest.fixture
def builders():
    """4 个 builder mock。每个返回一个有标记的 dict 便于断 routing。"""
    return {
        "arca": MagicMock(
            build=MagicMock(return_value={"_provider_mark": "arca-built"})
        ),
        "baas": MagicMock(
            build=MagicMock(return_value={"_provider_mark": "baas-built", "bind_id": 42})
        ),
        "teclaw": MagicMock(
            build=MagicMock(return_value={"_provider_mark": "teclaw-built"})
        ),
        "local": MagicMock(
            build=MagicMock(return_value={"_provider_mark": "local-built"})
        ),
    }


@pytest.fixture
def resolver(fake_binding_repo, fake_bot_repo, builders):
    return DeviceContextResolver(
        binding_repository=fake_binding_repo,
        bot_repository=fake_bot_repo,
        arca_builder=builders["arca"],
        baas_builder=builders["baas"],
        teclaw_builder=builders["teclaw"],
        local_builder=builders["local"],
    )


def _mock_binding(bid: int, provider: str):
    b = MagicMock()
    b.id = bid
    b.device_provider = provider
    b.device_id = f"device-{bid}"
    return b


# ── 4 个 provider 路由 ──

def test_arca_bot_returns_provider_arca(resolver, fake_binding_repo, builders):
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(1, "arca")
    ctx = resolver.resolve_for_bot("bot-1", "user-1")
    assert ctx.provider == "arca"
    assert ctx.binding_id == 1
    assert ctx.bot_id == "bot-1"
    assert ctx.user_id == "user-1"
    assert ctx.bot_type == "personal"  # fake_bot_repo 默认值
    builders["arca"].build.assert_called_once()


def test_baas_bot_returns_provider_baas(resolver, fake_binding_repo, builders):
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(2, "baas")
    ctx = resolver.resolve_for_bot("bot-2", "user-1")
    assert ctx.provider == "baas"
    builders["baas"].build.assert_called_once()


def test_teclaw_bot_returns_provider_teclaw_not_translated_to_baas(
    resolver, fake_binding_repo, builders
):
    """teclaw provider 不被翻译成 baas。"""
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(3, "teclaw")
    ctx = resolver.resolve_for_bot("bot-3", "user-1")
    assert ctx.provider == "teclaw"
    builders["teclaw"].build.assert_called_once()
    builders["baas"].build.assert_not_called()  # 不被误路由


def test_local_bot_returns_provider_local(resolver, fake_binding_repo, builders):
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(4, "local")
    ctx = resolver.resolve_for_bot("bot-4", "user-1")
    assert ctx.provider == "local"


# ── 异常路径 ──

def test_bot_no_active_binding_raises_device_not_bound(resolver, fake_binding_repo):
    fake_binding_repo.get_active_by_bot_and_owner.return_value = None
    with pytest.raises(DeviceNotBoundError):
        resolver.resolve_for_bot("non-existent", "user-1")


def test_unknown_provider_raises_unknown_provider_error(resolver, fake_binding_repo):
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(99, "daas")  # 历史值
    with pytest.raises(UnknownProviderError):
        resolver.resolve_for_bot("bot-bad", "user-1")


# ── 不变性 ──

def test_returns_immutable_device_context(resolver, fake_binding_repo):
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(1, "baas")
    ctx = resolver.resolve_for_bot("bot-1", "user-1")
    with pytest.raises(FrozenInstanceError):
        ctx.provider = "arca"  # type: ignore[misc]


# ── 单源事实源 ──

def test_provider_field_comes_from_binding_not_dict(
    resolver, fake_binding_repo, builders
):
    """resolver 从 binding.device_provider 拿 provider,即使 builder 返的 dict 里
    带 'device_provider': 'arca'(误标),最终 ctx.provider 仍以 binding 为准。"""
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(1, "baas")
    # builder 返一个误标 device_provider 的 dict
    builders["baas"].build.return_value = {
        "_provider_mark": "baas-built",
        "device_provider": "arca",  # 故意误导
    }

    ctx = resolver.resolve_for_bot("bot-1", "user-1")

    assert ctx.provider == "baas"  # 仍以 binding 为准


# ── resolve_for_binding(by-binding 入口) ──

def test_resolve_for_binding_routes_to_arca(resolver, fake_binding_repo, builders):
    """已知 binding_id,provider=arca → 路由到 ArcaConnInfoBuilder。"""
    fake_binding_repo.get_by_id.return_value = _mock_binding(101, "arca")

    ctx = resolver.resolve_for_binding(101, "user-1", bot_id="bot-101")

    assert ctx.provider == "arca"
    assert ctx.binding_id == 101
    assert ctx.user_id == "user-1"
    builders["arca"].build.assert_called_once()
    # by-binding 不走 by-bot 入口
    fake_binding_repo.get_active_by_bot_and_owner.assert_not_called()


def test_resolve_for_binding_passes_through_bot_id(
    resolver, fake_binding_repo
):
    """by-binding 入口 caller 显式传 bot_id,透传到 ctx.bot_id —
    下游(如 skill_center_module._build_binding_ctx)依赖这个字段做 owner 校验。"""
    fake_binding_repo.get_by_id.return_value = _mock_binding(102, "baas")

    ctx = resolver.resolve_for_binding(102, "user-1", bot_id="bot-102")

    assert ctx.bot_id == "bot-102"


def test_resolve_for_binding_raises_when_binding_missing(
    resolver, fake_binding_repo
):
    """binding_id 不存在 → DeviceNotBoundError。"""
    fake_binding_repo.get_by_id.return_value = None

    with pytest.raises(DeviceNotBoundError):
        resolver.resolve_for_binding(999, "user-1", bot_id="bot-999")


def test_resolve_for_binding_raises_on_unknown_provider(
    resolver, fake_binding_repo
):
    """binding.device_provider 是未知值 → UnknownProviderError。"""
    fake_binding_repo.get_by_id.return_value = _mock_binding(103, "daas")  # 历史值

    with pytest.raises(UnknownProviderError):
        resolver.resolve_for_binding(103, "user-1", bot_id="bot-103")


def test_resolve_for_bot_omitted_connection_identity_keeps_operator_for_legacy_v2(
    resolver, fake_binding_repo, builders
):
    """The legacy by-bot path also keeps its original operator fallback."""
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(
        112, "local"
    )
    resolver.resolve_for_bot("bot-112", "operator")

    call = builders["local"].build.call_args
    assert call.args[0].id == 112
    assert call.args[1] == "operator"
    assert call.kwargs == {"device_uuid": None}


def test_resolve_for_binding_connection_identity_routes_only_to_legacy_v2_builders(
    resolver, fake_binding_repo, builders
):
    """connection_identity is isolated to providers backed by legacy v2.

    ARCA/local use the owner to pass the unresolved device gate, while
    BaaS/teclaw connection affinity continues using the operator id.
    """
    fake_binding_repo.get_by_id.return_value = _mock_binding(104, "arca")
    resolver.resolve_for_binding(
        104, "friend", bot_id="b-1", connection_identity="owner"
    )
    call = builders["arca"].build.call_args
    assert call.args[0].id == 104
    assert call.args[1] == "owner"
    assert call.kwargs == {"device_uuid": None}

    fake_binding_repo.get_by_id.return_value = _mock_binding(105, "baas")
    resolver.resolve_for_binding(
        105, "friend", bot_id="b-2", connection_identity="owner"
    )
    call = builders["baas"].build.call_args
    assert call.args[0].id == 105
    assert call.args[1] == "friend"
    assert call.kwargs == {"device_uuid": None}


def test_resolve_for_binding_omitted_connection_identity_keeps_operator_for_legacy_v2(
    resolver, fake_binding_repo, builders
):
    """Existing resolver callers must not send None to ARCA/local legacy v2."""
    fake_binding_repo.get_by_id.return_value = _mock_binding(107, "arca")
    resolver.resolve_for_binding(107, "operator", bot_id="b-3")

    call = builders["arca"].build.call_args
    assert call.args[0].id == 107
    assert call.args[1] == "operator"
    assert call.kwargs == {"device_uuid": None}

    fake_binding_repo.get_by_id.return_value = _mock_binding(108, "local")
    resolver.resolve_for_binding(108, "operator", bot_id="b-4")

    call = builders["local"].build.call_args
    assert call.args[0].id == 108
    assert call.args[1] == "operator"
    assert call.kwargs == {"device_uuid": None}


def test_resolve_for_binding_local_supports_explicit_owner_identity(
    resolver, fake_binding_repo, builders
):
    """Local binding resolution keeps the same guarded owner override."""
    fake_binding_repo.get_by_id.return_value = _mock_binding(110, "local")
    resolver.resolve_for_binding(
        110, "friend", bot_id="b-6", connection_identity="owner"
    )

    call = builders["local"].build.call_args
    assert call.args[0].id == 110
    assert call.args[1] == "owner"
    assert call.kwargs == {"device_uuid": None}


def test_resolve_for_binding_empty_connection_identity_falls_back_to_operator(
    resolver, fake_binding_repo, builders
):
    """Empty overrides cannot erase the legacy operator identity."""
    fake_binding_repo.get_by_id.return_value = _mock_binding(111, "local")
    resolver.resolve_for_binding(
        111, "operator", bot_id="b-7", connection_identity=""
    )

    call = builders["local"].build.call_args
    assert call.args[0].id == 111
    assert call.args[1] == "operator"
    assert call.kwargs == {"device_uuid": None}


def test_resolve_for_binding_teclaw_ignores_connection_identity(
    resolver, fake_binding_repo, builders
):
    """Only legacy v2 builders may change the device-connection subject."""
    fake_binding_repo.get_by_id.return_value = _mock_binding(109, "teclaw")
    resolver.resolve_for_binding(
        109, "operator", bot_id="b-5", connection_identity="owner"
    )

    call = builders["teclaw"].build.call_args
    assert call.args[0].id == 109
    assert call.args[1] == "operator"
    assert call.kwargs == {"device_uuid": None}


def test_resolve_for_bot_connection_identity_routes_only_to_legacy_v2_builders(
    resolver, fake_binding_repo, builders
):
    """By-bot resolution carries the same provider isolation."""
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(
        106, "local"
    )
    ctx = resolver.resolve_for_bot(
        "bot-106", "friend", connection_identity="owner"
    )
    call = builders["local"].build.call_args
    assert call.args[0].id == 106
    assert call.args[1] == "owner"
    assert call.kwargs == {"device_uuid": None}
    assert ctx.user_id == "friend"


# ── resolve_for_binding_invoke(binding 路由入口) ──

def test_resolve_for_binding_invoke_builds_baas_routing_context_without_builder(
    resolver, fake_binding_repo, fake_bot_repo, builders
):
    fake_binding_repo.get_by_id.return_value = _mock_binding(201, "baas")
    fake_bot_repo.get_by_id_and_owner.return_value = {
        "bot_type": "service",
        "active_engine": "openclaw",
    }

    ctx = resolver.resolve_for_binding_invoke(
        201,
        "owner-1",
        bot_id="service-bot",
    )

    assert ctx.provider == "baas"
    assert ctx.conn_info == {
        "bind_id": 201,
        "binding_id": 201,
        "bot_uuid": "device-201",
        "engine_port": 20003,
        "engine_type": "openclaw",
        "bot_type": "service",
        "type": "baas",
        "headers": {},
        "device_affinity": "owner-1",
    }
    builders["baas"].build.assert_not_called()


def test_resolve_for_binding_invoke_preserves_teclaw_device_uuid(
    resolver, fake_binding_repo, fake_bot_repo, builders
):
    fake_binding_repo.get_by_id.return_value = _mock_binding(202, "teclaw")
    fake_bot_repo.get_by_id_and_owner.return_value = {"bot_type": "service"}

    ctx = resolver.resolve_for_binding_invoke(
        202,
        "owner-1",
        bot_id="teclaw-service-bot",
        device_uuid="DEVICE-002",
    )

    assert ctx.provider == "teclaw"
    assert ctx.conn_info["engine_type"] == "teclaw"
    assert ctx.conn_info["device_uuid"] == "DEVICE-002"
    builders["teclaw"].build.assert_not_called()


def test_resolve_for_binding_invoke_keeps_desktop_full_resolution(
    resolver, fake_binding_repo, fake_bot_repo, builders
):
    fake_binding_repo.get_by_id.return_value = _mock_binding(203, "baas")
    fake_bot_repo.get_by_id_and_owner.return_value = {"bot_type": "desktop"}

    ctx = resolver.resolve_for_binding_invoke(
        203,
        "owner-1",
        bot_id="desktop-bot",
    )

    assert ctx.conn_info["_provider_mark"] == "baas-built"
    builders["baas"].build.assert_called_once()


# ── bot_type 注入(本 PR 新增) ──

def test_resolve_for_bot_injects_bot_type_from_bot_repo(
    resolver, fake_binding_repo, fake_bot_repo
):
    """resolve_for_bot 内部用 bot_repo.get_by_id_and_owner 拿 bot_type,注入 ctx.bot_type。"""
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(1, "baas")
    fake_bot_repo.get_by_id_and_owner.return_value = {"bot_type": "desktop"}

    ctx = resolver.resolve_for_bot("bot-1", "owner-1")

    assert ctx.bot_type == "desktop"
    fake_bot_repo.get_by_id_and_owner.assert_called_once_with("bot-1", "owner-1")


def test_resolve_for_binding_injects_bot_type_from_bot_repo(
    resolver, fake_binding_repo, fake_bot_repo
):
    """resolve_for_binding 内部用 bot_repo.get_by_binding_id 拿 bot_type。"""
    fake_binding_repo.get_by_id.return_value = _mock_binding(2, "baas")
    fake_bot_repo.get_by_binding_id.return_value = {"bot_type": "personal"}

    ctx = resolver.resolve_for_binding(2, "user-1", bot_id="bot-2")

    assert ctx.bot_type == "personal"
    fake_bot_repo.get_by_binding_id.assert_called_once_with(2)


def test_resolve_for_bot_handles_missing_bot_gracefully(
    resolver, fake_binding_repo, fake_bot_repo
):
    """bot_repo 返 None(bot 已删/不存在)时 ctx.bot_type 兜底为 ''。"""
    fake_binding_repo.get_active_by_bot_and_owner.return_value = _mock_binding(1, "arca")
    fake_bot_repo.get_by_id_and_entity.return_value = None
    fake_bot_repo.get_by_id_and_owner.return_value = None

    ctx = resolver.resolve_for_bot("bot-orphan", "user-1")

    assert ctx.bot_type == ""


def test_resolve_for_binding_handles_missing_bot_gracefully(
    resolver, fake_binding_repo, fake_bot_repo
):
    """bot_repo.get_by_binding_id 返 None 时 ctx.bot_type 兜底为 ''。"""
    fake_binding_repo.get_by_id.return_value = _mock_binding(3, "arca")
    fake_bot_repo.get_by_binding_id.return_value = None

    ctx = resolver.resolve_for_binding(3, "user-1", bot_id="bot-orphan")

    assert ctx.bot_type == ""
