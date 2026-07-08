"""Tests for build_baas_conn_info translation helper."""
from __future__ import annotations

from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info


class TestBuildBaasConnInfo:
    """build_baas_conn_info 把 BAAS ws_info 翻译成统一 conn_info schema."""

    def _ws_info(self, **overrides) -> BotWsConnectionInfoResponse:
        # 占位 ws_url：BAAS 实际形态由 BAAS 侧同事实现（覆盖 ARCA + agentbox），
        # 这里用 ARCA proxypass 形态做翻译契约的代表。BAAS 真正落地后改 build_baas_conn_info
        # 内部映射即可，本测试只断言"7-key schema 不变"。
        defaults = {
            "ws_url": "wss://<baas-host>/proxypass/<baas-target>/api/openclaw/ws",
            "token": "jwt-token-abc",
            "target": "<baas-target>",
            "expires_at": "2026-12-31T00:00:00Z",
        }
        defaults.update(overrides)
        return BotWsConnectionInfoResponse(**defaults)

    def test_url_strips_ws_path_and_scheme(self):
        info = self._ws_info()
        result = build_baas_conn_info(info, engine_type="openclaw")

        # 不断言完整 URL（BAAS 形态后续可变），只断言两条不变量：
        # 1. scheme 已从 wss 改为 https
        # 2. /api/openclaw/ws 后缀已去掉（下游会拼自己的 path）
        assert result["url"].startswith("https://")
        assert not result["url"].endswith("/api/openclaw/ws")
        assert "/api/openclaw/ws" not in result["url"]

    def test_headers_contains_proxypass_token(self):
        info = self._ws_info(token="abc-123")
        result = build_baas_conn_info(info, engine_type="openclaw")

        assert result["token"] == "abc-123"
        assert result["headers"] == {"x-proxypass-token": "abc-123"}
        assert result["use_proxy"] is True

    def test_empty_token_yields_empty_headers(self):
        info = self._ws_info(token="")
        result = build_baas_conn_info(info, engine_type="openclaw")

        assert result["headers"] == {}
        assert result["use_proxy"] is True  # BAAS 始终走 proxypass

    def test_full_schema_keys_present(self):
        info = self._ws_info()
        result = build_baas_conn_info(info, engine_type="openclaw")

        # Subset assertion: all legacy keys must be present (new fields may be added).
        # Note: ``device_provider`` 不在 dict 里(单源由 ctx.provider 承载,
        # resolver._normalize_schema 会 pop)。
        legacy_keys = {"url", "token", "headers", "use_proxy", "sandbox_id", "target", "engine_type"}
        assert legacy_keys.issubset(result.keys()), f"missing legacy keys: {legacy_keys - result.keys()}"
        assert "device_provider" not in result, (
            "device_provider 由 ctx.provider 单源承载,build_baas_conn_info 不再塞此字段"
        )
        assert result["sandbox_id"] == info.target
        assert result["target"] == info.target

    def test_device_provider_not_in_dict(self):
        """device_provider 由 DeviceContextResolver.ctx.provider 单源承载,
        build_baas_conn_info 不再向 dict 写入此字段(resolver 也会 pop)。"""
        info = self._ws_info()
        result = build_baas_conn_info(info, engine_type="openclaw")
        assert "device_provider" not in result

    def test_device_provider_formal_arg_accepted_but_not_in_dict(self):
        """形参保留(向下兼容老 callsite),但传 "teclaw" 也不会写入 dict —
        ctx.provider 在 resolver 层从 binding.device_provider 取真值。"""
        info = self._ws_info()
        result = build_baas_conn_info(info, engine_type="openclaw", device_provider="teclaw")
        assert "device_provider" not in result

    def test_teclaw_and_baas_dict_byte_equivalent(self):
        """teclaw 与 baas 共用 transport,build_baas_conn_info 输出字段一致
        (device_provider 都不在 dict 里,下游 resolver 通过 binding.device_provider
        区分 ctx.provider)。"""
        info = self._ws_info()
        baas = build_baas_conn_info(info, engine_type="openclaw")
        teclaw = build_baas_conn_info(info, engine_type="openclaw", device_provider="teclaw")
        assert teclaw == baas

    def test_engine_type_passthrough(self):
        info = self._ws_info()
        result = build_baas_conn_info(info, engine_type="aicoding")

        assert result["engine_type"] == "aicoding"


def test_build_baas_conn_info_includes_invoke_http_fields():
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok-xyz",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="container_abc--machine_M1--user_U7@tpl_42",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
        bot_uuid="bot-uuid-001",
    )

    conn_info = build_baas_conn_info(ws_info, engine_type="openclaw")

    # device_provider 由 ctx.provider 单源承载(resolver._normalize_schema 会 pop),
    # build_baas_conn_info 输出 dict 不再含此字段。
    assert "device_provider" not in conn_info
    assert conn_info["paas_device_id"] == "container_abc--machine_M1--user_U7@tpl_42"
    assert conn_info["baas_base_url"] == "https://secbaas-prod.teamclaw.com"
    assert conn_info["engine_port"] == 20003
    assert conn_info["engine_type"] == "openclaw"
    assert conn_info["tenant"] == "team_claw"
    assert conn_info["bot_uuid"] == "bot-uuid-001"
    # 头部仍然带 token
    assert conn_info["headers"] == {"x-proxypass-token": "tok-xyz"}


def test_build_baas_conn_info_handles_missing_optional_fields():
    """Even if upstream BAAS returns no token, conn_info shouldn't crash."""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="x--y--z@1",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
    )

    conn_info = build_baas_conn_info(ws_info, engine_type="openclaw")
    # No token → headers empty dict (don't crash, don't emit None header value)
    assert conn_info["headers"] == {}
    assert conn_info["paas_device_id"] == "x--y--z@1"


def test_build_baas_conn_info_includes_tenant():
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok-xyz",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="container_abc--machine_M1--user_U7@tpl_42",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
    )

    conn_info = build_baas_conn_info(ws_info, engine_type="openclaw")

    assert conn_info["tenant"] == "team_claw"


def test_build_baas_conn_info_uses_provided_bot_type():
    """build_baas_conn_info 应透传 bot_type 参数，不再写死为空字符串。"""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="dev-1",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
    )

    conn_info = build_baas_conn_info(ws_info, engine_type="openclaw", bot_type="desktop")
    assert conn_info["bot_type"] == "desktop"


def test_build_baas_conn_info_bot_type_defaults_to_empty():
    """bot_type 默认值为空字符串（向后兼容）。"""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="dev-1",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
    )

    conn_info = build_baas_conn_info(ws_info, engine_type="openclaw")
    assert conn_info["bot_type"] == ""


def test_build_baas_conn_info_does_not_emit_bind_id():
    """老函数不再吐 bind_id —— 走 invoke_http 的调用方必须改用
    build_baas_conn_info_for_http（涔涔 review A.3）。"""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="dev-1",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
    )

    conn_info = build_baas_conn_info(ws_info, engine_type="openclaw")
    assert "bind_id" not in conn_info


def test_build_baas_conn_info_for_http_returns_dict_with_bind_id():
    """build_baas_conn_info_for_http 在老函数基础上塞入 bind_id 并对齐 v2 desktop 字段。

    与 :func:`build_baas_conn_info` 的差异由方案 X 引入(v2 desktop byte-equivalent):
    - bind_id: 新增 ac_entity_device_binding.id
    - url: 覆盖为 invoke-http 完整代理 URL(v2 desktop L1448-1451)
    - type: 写死 "desktop"(v2 desktop L1464)
    - sandbox_id: 覆盖为 None(v2 desktop L1460,build_baas_conn_info 写 target)
    - device_affinity: 仅在 user_id 非空时写(v2 desktop L1475)
    其余字段(headers/token/baas_base_url/engine_port/tenant/bot_uuid/paas_device_id/
    bot_type/engine_type/use_proxy/target/device_provider)与老函数一致。
    """
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import (
        build_baas_conn_info,
        build_baas_conn_info_for_http,
    )

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok-xyz",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="container_abc--machine_M1--user_U7@tpl_42",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
        bot_uuid="bot-uuid-001",
    )

    http_info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        bot_type="desktop",
        user_id="staff-001",
    )
    base_info = build_baas_conn_info(
        ws_info, engine_type="openclaw", bot_type="desktop"
    )

    # 方案 X 新增 / 覆盖字段
    assert http_info["bind_id"] == 42
    assert http_info["url"] == (
        "https://secbaas-prod.teamclaw.com/api/v1/bots/team_claw/bot-uuid-001"
        "/invoke-http/20003"
    )
    assert http_info["type"] == "desktop"
    assert http_info["sandbox_id"] is None
    assert http_info["device_affinity"] == "staff-001"

    # 方案 X 之外的字段仍与老函数一致
    untouched_keys = set(http_info.keys()) - {
        "bind_id", "url", "type", "sandbox_id", "device_affinity",
    }
    for k in untouched_keys:
        assert http_info[k] == base_info[k], f"untouched field diverged: {k}"


def test_build_baas_conn_info_for_http_without_user_id_omits_device_affinity():
    """user_id 默认空字符串(向后兼容旧 callsite),不写 device_affinity。"""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="dev-1",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
        bot_uuid="bot-uuid-001",
    )

    http_info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
    )
    assert "device_affinity" not in http_info


def test_build_baas_conn_info_for_http_missing_bind_id_raises_typeerror():
    """漏传 bind_id 立即 TypeError（不是等到下游 invoke_http 才 KeyError）—— 这是
    本次拆函数的核心收益（涔涔 review A.3）。"""
    import pytest

    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-prod.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok",
        target="foo",
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="dev-1",
        baas_base_url="https://secbaas-prod.teamclaw.com",
        engine_port=20003,
    )

    with pytest.raises(TypeError):
        # bind_id 必填 keyword-only，无 default —— Python 自带的入参校验
        build_baas_conn_info_for_http(  # type: ignore[call-arg]
            ws_info=ws_info,
            engine_type="openclaw",
        )


# ── 2026-06-17 ARCA target fallback(P0 hotfix) ─────────────────────────
# service+baas bot 的 BaaS device 实际后端是 ARCA sandbox;BaaS 端
# ArcaPaasService.invoke_http 未实现,走 invoke-http 网关必 500。
# url 临时回退 REL20260610 的 agentclawproxy/proxypass 写法。
# 现场证据:trace 0b446a4d17816613864388343e3e63 / 0be8ed6217816218973027461e3161
# 跟进文档:docs/superpowers/baas-refactor-dirty-work.md §1


def _ws_info(target: str):
    """Build a BotWsConnectionInfoResponse with the given target field."""
    from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
    return BotWsConnectionInfoResponse(
        ws_url="wss://secbaas-pre.teamclaw.com/proxypass/foo/api/openclaw/ws",
        token="tok-xyz",
        target=target,
        expires_at="2026-01-01T00:00:00Z",
        paas_device_id="BOT-some-device-id",
        baas_base_url="https://secbaas-pre.teamclaw.com",
        engine_port=20003,
        tenant="team_claw",
        bot_uuid="BOT-some-device-id",
    )


def test_build_for_http_arca_target_falls_back_to_proxypass():
    """ws_info.target 是 ARCA sandbox 时,url 走 agentclawproxy/proxypass(REL610 写法)。

    覆盖 service+baas bot 链路:BaaS binding 后端是 ARCA sandbox,BaaS 端
    ArcaPaasService.invoke_http 未实现 → invoke-http 网关返 500 →
    backend 临时直透 ARCA proxy。
    """
    from unittest.mock import MagicMock
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="ARCA_ARCA-SANDBOX-e3d5fd63-d40b-4e3e-8caf-203e8cfa4bf3@0:20003")

    sandbox_client = MagicMock()
    sandbox_client.proxy_base_url.return_value = "https://agentclawproxy-pre.teamclaw.com"
    info = build_baas_conn_info_for_http(
        bind_id=1348341,
        ws_info=ws_info,
        engine_type="openclaw",
        bot_type="service",
        user_id="100014",
        sandbox_client=sandbox_client,
    )

    assert info["url"] == (
        "https://agentclawproxy-pre.teamclaw.com/proxypass/"
        "ARCA_ARCA-SANDBOX-e3d5fd63-d40b-4e3e-8caf-203e8cfa4bf3@0:20003"
    )
    # sandbox_id 对齐 REL610 v2 输出(target 字符串本身),而非 invoke-http 链路的 None
    assert info["sandbox_id"] == ws_info.target
    # type 字段从 bot_type 透传(REL610 行为):service bot → 'baas',前端
    # 自动走 wss://agentclawproxy/proxypass/... 而非裸 ws://target(后者会 502)
    assert info["type"] == "baas"
    # bind_id / device_affinity 字段不受 fallback 影响
    assert info["bind_id"] == 1348341
    assert info["device_affinity"] == "100014"


def test_build_for_http_arca_target_without_sandbox_client_raises():
    """ARCA target 但没注入 sandbox_client → 立即 RuntimeError(不静默产坏 URL)。

    生产链路(baas builder/plugin)总会注入 sandbox_client;此守卫保证误用
    (能产出 ARCA target 的 caller 漏传 client)早炸而非一路炸到 transport。
    """
    import pytest as _pytest
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="ARCA_ARCA-SANDBOX-xxx@0:20003")
    with _pytest.raises(RuntimeError, match="sandbox_client"):
        build_baas_conn_info_for_http(
            bind_id=1,
            ws_info=ws_info,
            engine_type="openclaw",
            bot_type="service",
        )


def test_build_for_http_non_arca_target_uses_invoke_http():
    """非 ARCA target(desktop bot)继续走 BaaS invoke-http 网关,行为不变。

    回归守卫:确保 ARCA fallback 不误伤 desktop bot 链路(PR #2662)。
    """
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    # desktop bot 的 target 是 BaaS internal device 标识,不是 ARCA_ 前缀
    ws_info = _ws_info(target="BOT-f268792926fa45c2895ebb75afb16fd7")

    info = build_baas_conn_info_for_http(
        bind_id=1352506,
        ws_info=ws_info,
        engine_type="openclaw",
        bot_type="desktop",
        user_id="100020",
    )

    assert info["url"] == (
        "https://secbaas-pre.teamclaw.com/api/v1/bots/team_claw/"
        "BOT-some-device-id/invoke-http/20003"
    )
    # 真 BaaS 后端:sandbox_id 沿用 invoke-http 链路的 None
    assert info["sandbox_id"] is None


def test_build_for_http_empty_target_uses_invoke_http():
    """ws_info.target 为空字符串时不进 fallback,走 invoke-http。"""
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="")

    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
    )

    assert "/invoke-http/20003" in info["url"]
    assert info["sandbox_id"] is None


# ── 2026-06-17 type 字段从 bot.bot_type 透传(对齐 REL20260610 行为) ────────
# 现场:trace 0b446a1f17816776901423178e41a8 (GY 服务助手 service bot WS 502)
# 根因:build_baas_conn_info_for_http 死写 type="desktop",前端 selectWebsocket
#       Url 把 service+baas bot 错路由到裸 ws://ARCA_xxx@0:20003/... → 浏览器 502
# 修复:type 字段从 bot_type 透传 — desktop bot → 'desktop',其它 → 'baas'
#       (前端按 type=='local'|'desktop' 走裸 ws,其它走 agentclawproxy/proxypass)


def test_build_for_http_type_desktop_when_bot_type_desktop():
    """desktop bot:type='desktop',前端走裸 ws://target(REL610 行为)。"""
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="BOT-desktop-device-id")
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        bot_type="desktop",
    )
    assert info["type"] == "desktop"


def test_build_for_http_type_baas_when_bot_type_service():
    """service bot:type='baas',前端走 wss://agentclawproxy/proxypass/...。

    现场回归守卫:GY 服务助手 (bot_type=service, target=ARCA_xxx) 必须返
    type='baas',否则前端裸 ws://ARCA_xxx@0:20003/... 必 502。
    """
    from unittest.mock import MagicMock
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="ARCA_ARCA-SANDBOX-xxx@0:20003")
    sandbox_client = MagicMock()
    sandbox_client.proxy_base_url.return_value = "https://agentclawproxy-pre.teamclaw.com"
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        bot_type="service",
        sandbox_client=sandbox_client,
    )
    assert info["type"] == "baas"


def test_build_for_http_type_baas_when_bot_type_personal():
    """personal bot(灰度切流后的 personal+baas):同 service,type='baas'。"""
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="BOT-personal-device-id")
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        bot_type="personal",
    )
    assert info["type"] == "baas"


def test_build_for_http_type_baas_when_bot_type_empty():
    """bot_type 缺失(老数据 / 漏传)默认 type='baas' 走代理,不退化成 desktop。

    安全 default:`'baas'` 在前端 selectWebsocketUrl 里走 proxypass 分支,
    比 `'desktop'` 安全(后者会被前端拼成裸 ws://target 直连)。
    """
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="BOT-some-id")
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        # bot_type 不传,使用默认空字符串
    )
    assert info["type"] == "baas"


# ── device_uuid 透传(多实例 service bot) ───────────────────────────────


def test_build_baas_conn_info_for_http_includes_device_uuid():
    """传 device_uuid 时,conn_info dict 含 device_uuid"""
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="BOT-some-id")
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        device_uuid="DEV-xyz",
    )
    assert info["device_uuid"] == "DEV-xyz"


def test_build_baas_conn_info_for_http_omits_device_uuid_when_none():
    """不传 / 传 None 时,conn_info 不含 device_uuid key(向后兼容单实例)."""
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="BOT-some-id")
    # 不传 device_uuid
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
    )
    assert "device_uuid" not in info

    # 显式传 None
    info_none = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        device_uuid=None,
    )
    assert "device_uuid" not in info_none


def test_build_baas_conn_info_for_http_empty_string_device_uuid_omitted():
    """空字符串 device_uuid 也不塞(falsy 守卫,与 device_affinity 一致)."""
    from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

    ws_info = _ws_info(target="BOT-some-id")
    info = build_baas_conn_info_for_http(
        bind_id=42,
        ws_info=ws_info,
        engine_type="openclaw",
        device_uuid="",
    )
    assert "device_uuid" not in info
