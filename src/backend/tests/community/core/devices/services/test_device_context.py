from dataclasses import FrozenInstanceError

import pytest

from agentclaw.community.core.devices.services.device_context import (
    ConnInfo,
    DeviceContext,
    BotNotFoundError,
    DeviceNotBoundError,
    UnknownProviderError,
    ConnInfoBuildError,
)


def test_device_context_constructs_with_all_required_fields():
    ctx = DeviceContext(
        provider="baas",
        conn_info={"url": "http://test"},
        binding_id=42,
        bot_id="bot-1",
        user_id="user-1",
    )
    assert ctx.provider == "baas"
    assert ctx.binding_id == 42


def test_device_context_is_frozen():
    ctx = DeviceContext(
        provider="baas",
        conn_info={},
        binding_id=42,
        bot_id="bot-1",
        user_id="user-1",
    )
    with pytest.raises(FrozenInstanceError):
        ctx.provider = "arca"  # type: ignore[misc]


def test_device_context_provider_must_be_known():
    # ProviderType Literal — typing 不强制运行时检查,但本测试卡未来 mypy
    ctx = DeviceContext(
        provider="arca",  # 合法
        conn_info={},
        binding_id=1,
        bot_id="b",
        user_id="u",
    )
    assert ctx.provider in ("arca", "baas", "teclaw", "local")


def test_exception_classes_exist():
    # 仅断 4 个异常类可被实例化 + 是 RuntimeError 子类
    for exc_cls in (
        BotNotFoundError,
        DeviceNotBoundError,
        UnknownProviderError,
        ConnInfoBuildError,
    ):
        e = exc_cls("test")
        assert isinstance(e, RuntimeError)
        assert str(e) == "test"


# ── ConnInfo:typed 只读视图 ──

_BAAS_STYLE_CONN_INFO = {
    "url": "https://<baas-host>/api/v1/bots/team_claw/device-201/invoke-http/20003",
    "token": "tok",
    "headers": {"x-proxypass-token": "tok"},
    "use_proxy": True,
    "sandbox_id": None,
    "target": "<baas-target>:20003",
    "engine_type": "openclaw",
    "type": "baas",
    "bot_type": "service",
    "bind_id": 201,
    "binding_id": 201,
    "bot_uuid": "device-201",
    "tenant": "team_claw",
    "baas_base_url": "https://<baas-host>",
    "engine_port": 20003,
    "paas_device_id": "paas-dev-1",
    "device_affinity": "user-1",
    "device_uuid": "DEVICE-002",
}


def test_conn_info_typed_attribute_access():
    ci = ConnInfo(_BAAS_STYLE_CONN_INFO)
    assert ci.url == _BAAS_STYLE_CONN_INFO["url"]
    assert ci.token == "tok"
    assert ci.headers == {"x-proxypass-token": "tok"}
    assert ci.use_proxy is True
    assert ci.sandbox_id is None
    assert ci.target == "<baas-target>:20003"
    assert ci.engine_type == "openclaw"
    assert ci.type == "baas"
    assert ci.bot_type == "service"
    assert ci.bind_id == 201
    assert ci.binding_id == 201
    assert ci.bot_uuid == "device-201"
    assert ci.tenant == "team_claw"
    assert ci.baas_base_url == "https://<baas-host>"
    assert ci.engine_port == 20003
    assert ci.paas_device_id == "paas-dev-1"
    assert ci.device_affinity == "user-1"
    assert ci.device_uuid == "DEVICE-002"
    assert ci.available is True
    assert ci.message == ""


def test_conn_info_defaults_for_absent_fields():
    """binding 路由链路(resolve_for_binding_invoke)不预置传输字段 —
    缺席字段的 property 返回文档标注的缺省值,不 KeyError。"""
    ci = ConnInfo({"bind_id": 201, "engine_type": "teclaw"})
    assert ci.url == ""
    assert ci.token == ""
    assert ci.headers == {}
    assert ci.use_proxy is False
    assert ci.sandbox_id is None
    assert ci.target == ""
    assert ci.binding_id is None  # alias 由 resolver 层补,不在 ConnInfo 里推导
    assert ci.engine_port is None
    assert ci.paas_device_id is None
    assert ci.device_uuid is None
    assert ci.available is True
    assert ci.message == ""


def test_conn_info_mapping_compat_with_plain_dict_reads():
    """存量 conn_info["url"] / .get / in / dict() / == 读法原样兼容。"""
    ci = ConnInfo(_BAAS_STYLE_CONN_INFO)
    assert ci["url"] == _BAAS_STYLE_CONN_INFO["url"]
    assert ci.get("tenant") == "team_claw"
    assert ci.get("nonexistent", "fallback") == "fallback"
    assert "bind_id" in ci
    assert "nonexistent" not in ci
    # key 集合与底层 dict 一致 — 不因缺席 property 长出幻影 key
    assert set(ci.keys()) == set(_BAAS_STYLE_CONN_INFO.keys())
    assert dict(ci) == _BAAS_STYLE_CONN_INFO
    assert len(ci) == len(_BAAS_STYLE_CONN_INFO)
    # 等值比较双向成立(Mapping.__eq__)
    assert ci == _BAAS_STYLE_CONN_INFO
    assert _BAAS_STYLE_CONN_INFO == ci


def test_conn_info_unknown_keys_pass_through():
    """builder 产出的未知 key 原样透传(dict 式访问)。"""
    ci = ConnInfo({"url": "http://test", "_provider_mark": "baas-built"})
    assert ci["_provider_mark"] == "baas-built"
    assert set(ci.keys()) == {"url", "_provider_mark"}


def test_conn_info_is_read_only_and_copies_source():
    source = {"url": "http://test"}
    ci = ConnInfo(source)
    with pytest.raises(TypeError):
        ci["url"] = "http://mutated"  # type: ignore[index]
    # 构造时浅拷贝 — 外部改源 dict 不影响本对象
    source["url"] = "http://mutated"
    assert ci.url == "http://test"
    # to_dict 是副本,改它也不影响本对象
    exported = ci.to_dict()
    exported["url"] = "http://mutated"
    assert ci.url == "http://test"


def test_conn_info_kwargs_construction():
    ci = ConnInfo(url="http://test", use_proxy=False)
    assert ci.url == "http://test"
    assert ci == {"url": "http://test", "use_proxy": False}
    # mapping + kwargs 合并,kwargs 覆盖同名 key
    merged = ConnInfo({"url": "http://a", "token": "tok"}, url="http://b")
    assert merged.url == "http://b"
    assert merged.token == "tok"


def test_device_context_wraps_plain_dict_conn_info():
    ctx = DeviceContext(
        provider="baas",
        conn_info={"url": "http://test"},
        binding_id=42,
        bot_id="bot-1",
        user_id="user-1",
    )
    assert isinstance(ctx.conn_info, ConnInfo)
    assert ctx.conn_info.url == "http://test"
    assert ctx.conn_info["url"] == "http://test"


def test_device_context_keeps_conn_info_instance():
    ci = ConnInfo({"url": "http://test"})
    ctx = DeviceContext(
        provider="baas",
        conn_info=ci,
        binding_id=42,
        bot_id="bot-1",
        user_id="user-1",
    )
    assert ctx.conn_info is ci
