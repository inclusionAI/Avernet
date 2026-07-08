"""Translate BAAS ws_info into the unified conn_info dict schema.

Schema (Phase 1 contract — see plan §conn_info Schema 契约):
    {url, token, headers, use_proxy, sandbox_id, target, engine_type}

NOTE: BAAS 侧最终给出的 ws_url 形态由 BAAS 同事实现（覆盖 ARCA + agentbox）。
本模块只做字段映射 + 两条不变量保证：
  - url 必须是 HTTP(S) base，不带末尾 ws path
  - 七字段 schema 完整

BAAS 真正落地后只需调整 _to_http_base 内部逻辑，下游消费者无感知。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.service_bot.services.baas_service import BotWsConnectionInfoResponse
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient


logger = get_logger()

# 当前已知的 ws path 后缀；若 BAAS 之后改路径，把这里改成可配置或多个 marker。
_WS_PATH_MARKERS = ("/api/openclaw/ws",)


def build_baas_conn_info(
    ws_info: BotWsConnectionInfoResponse,
    *,
    engine_type: str,
    bot_type: str = "",
    device_provider: str = "baas",
) -> dict[str, Any]:
    """Translate a BAAS WebSocket connection response into a conn_info dict.

    Schema (see docs/superpowers/backend-agentbox-arca-split-guide.md §3):
      Common: device_provider, engine_type, headers, bot_type (元数据)
      BAAS-specific: paas_device_id, baas_base_url, engine_port, tenant, bot_uuid
      Legacy (过渡保留, 后续 phase 删): url, token, target, sandbox_id, use_proxy

    Trust BAAS-given fields verbatim except for normalizing ws_url into a
    base HTTP URL for legacy `url` field.

    NOTE: 本函数 **不** 写 ``bind_id`` 字段。需要走 invoke_http 链路
    （filesystem / sync 等下游会用 ``conn_info["bind_id"]`` 反向定位
    ``ac_entity_device_binding``）请改用
    :func:`build_baas_conn_info_for_http` —— 它把 ``bind_id`` 设为必填
    keyword-only 参数，避免调用方漏传导致下游 ``KeyError`` 才暴露。

    Args:
        device_provider: The ``device_provider`` tag to stamp on the conn_info.
            Defaults to ``"baas"``. ``TeclawDeviceAccessor`` passes ``"teclaw"``:
            a teclaw container shares the BaaS invoke-http transport (so the
            transport fields are identical), but downstream routing keys off
            this tag to select whole-artifact delivery instead of the per-domain
            BaaS push.
    """
    url = _to_http_base(ws_info.ws_url)
    token = ws_info.token
    # NOTE: ``device_provider`` 形参保留但不写入返回 dict。
    # 单源由 DeviceContextResolver.ctx.provider 承载;resolver._normalize_schema
    # 已在 wrap 层 ``conn_info.pop("device_provider")``,放进 dict 也会被清掉。
    # 形参不删:老 callsite(``ProdDeviceSyncPluginSupplier.for_bot`` 内分流)
    # 仍读 raw conn_info["device_provider"],该 caller 路径待迁
    # ``DeviceSyncDispatcher.dispatch(ctx)`` 后再行清理(handoff:
    # docs/superpowers/handoffs/2026-06-15-device-sync-supplier-for-bot-cleanup-handoff.md)。
    return {
        # ── 通用字段 ────────────────────────────────────────────
        "engine_type": engine_type,
        "headers": {"x-proxypass-token": token} if token else {},
        "bot_type": bot_type,
        # ── agentbox 专属 ──────────────────────────────────────
        "paas_device_id": ws_info.paas_device_id,
        "baas_base_url": ws_info.baas_base_url,
        "engine_port": ws_info.engine_port,
        "tenant": ws_info.tenant,
        "bot_uuid": ws_info.bot_uuid,  # ac_entity_device_binding.device_id
        # ── Legacy（过渡，下期 phase 删） ────────────────────────
        "url": url,
        "token": token,
        "target": ws_info.target,
        "sandbox_id": ws_info.target,
        "use_proxy": True,
    }


def build_baas_conn_info_for_http(
    *,
    bind_id: int,
    ws_info: BotWsConnectionInfoResponse,
    engine_type: str,
    bot_type: str = "",
    device_provider: str = "baas",
    user_id: str = "",
    device_uuid: str | None = None,
    sandbox_client: SandboxRuntimeClient | None = None,
) -> dict[str, Any]:
    """Build a BaaS conn_info **for the invoke_http 下游链路** (必带 bind_id).

    与 :func:`build_baas_conn_info` 字段对齐，外加 v2 desktop 分支
    (:meth:`DeviceService.get_device_connection_v2`，L1446-1476) 的硬合同：

    - ``bind_id``：``ac_entity_device_binding.id``，下游
      ``BaasDeviceFileSystem`` / ``BaasDeviceSync`` 通过 invoke_http 反向定位绑定，
      漏传立即 ``TypeError``（kw-only 无默认值），避免一路炸到 transport 才暴露。
    - ``url``：覆盖为 **invoke-http 完整代理 URL**（而非 ``_to_http_base`` 出的
      proxypass 半 URL）。caller（``data_init`` / ``yuque`` / ``expert_chat``）拿
      ``conn_info["url"]`` 拼 path 直接 POST 才能到 BaaS 网关 invoke-http 端点；
      若给 proxypass 半 URL，POST 会 404。这与 v2 desktop 输出**一字不差**。
    - ``type``：写死 ``"desktop"``，对齐 v2 desktop 分支的 ``connection_type``。
      ``build_baas_conn_info_for_http`` 仅用于走 invoke_http 的 baas 链路，
      这条路就是 v2 desktop 等价路径；teclaw（也走 baas transport 但不走
      invoke_http）继续用 :func:`build_baas_conn_info`。
    - ``sandbox_id``：覆盖为 ``None``。v2 desktop 分支永远写 ``None``
      （L1460），而 :func:`build_baas_conn_info` 因服务 bot 复用其字段映射
      把 ``ws_info.target`` 塞进了 ``sandbox_id`` —— 在 invoke_http 链路这不对。
    - ``device_affinity``：=``user_id``，v2 desktop L1475 同源；空字符串时不塞，
      保持向后兼容（旧 callsite 没传 user_id 也不退化）。
    - ``device_uuid``：多实例 service bot 场景，caller 通过 ``device_uuid`` 锁定
      具体实例。下游 ``BaasInvokeTransport`` 把它透传给 ``invoke_http`` →
      ``get_http_info`` → BaaS ``/http-info?device_uuid=``，与 ``get_ws_info`` 的
      ``device_uuid`` 参数对称。``None``（单实例 / 未指定）时不塞，走 BaaS
      自动选活跃实例的老行为，向后兼容。

    沿用老函数的 callsite（teclaw 等不走 invoke_http 的链路）继续用
    :func:`build_baas_conn_info`，不需要 bind_id 也不要 desktop 特化字段。
    """
    info = build_baas_conn_info(
        ws_info,
        engine_type=engine_type,
        bot_type=bot_type,
        device_provider=device_provider,
    )
    info["bind_id"] = bind_id

    # ── url 出口分流（service+baas bot 临时回退 REL20260610 写法） ──────
    #
    # 现状：BaaS 服务端 ``ArcaPaasService.invoke_http`` 尚未实现。当 BaaS
    # binding 后端真实落地是 ARCA sandbox（``ws_info.target`` 形如
    # ``ARCA_ARCA-SANDBOX-xxx@0:20003``）时，走 BaaS invoke-http 网关会
    # 必报 ``[PLATFORM_ERROR] ArcaPaasService does not support HTTP
    # invocation`` 500。
    #
    # 现场证据：
    #   - trace 0b446a4d17816613864388343e3e63（service bot GY服务助手）
    #   - trace 0be8ed6217816218973027461e3161
    #
    # 临时绕行：当 target 是 ARCA sandbox 时，``url`` 改回 REL20260610 写法
    # （``agentclawproxy/proxypass/{target}``）—— BaaS 网关只当查表用，
    # 实际流量直透 ARCA proxy。``sandbox_id`` 同步对齐 REL610 v2 输出
    # （写 ``ws_info.target``，而非 invoke-http 链路的 ``None``）。
    #
    # 删除条件：BaaS 端 ``ArcaPaasService.invoke_http`` 落地、ARCA 后端
    # 经 invoke-http 实测 200 后，删本分支，``url`` 永久走 invoke-http。
    # 跟进：docs/superpowers/baas-refactor-dirty-work.md §1
    target = ws_info.target or ""
    if target.startswith("ARCA_"):
        # The sandbox-runtime client owns the proxy base URL (prod=arca proxy).
        # An ``ARCA_`` target is only ever produced by a prod/corp deployment, so
        # ``sandbox_client`` is always wired for the callers that can reach this
        # branch (baas builder/plugin). The community client never yields an
        # ``ARCA_`` target, so its ``proxy_base_url()`` (which raises) is never hit.
        if sandbox_client is None:
            raise RuntimeError(
                "build_baas_conn_info_for_http: ARCA target requires a "
                f"sandbox_client to resolve the proxy base URL (target={target!r})"
            )
        proxy_base = sandbox_client.proxy_base_url()
        info["url"] = f"{proxy_base}/proxypass/{target}"
        info["sandbox_id"] = target
    else:
        # 真 BaaS 后端（desktop bot / 未来 personal+baas）走 invoke-http 网关。
        # caller 直接 url + path POST 命中 BaaS 网关 invoke-http 端点。
        info["url"] = (
            f"{ws_info.baas_base_url}/api/v1/bots/{ws_info.tenant}/{ws_info.bot_uuid}"
            f"/invoke-http/{ws_info.engine_port}"
        )
        info["sandbox_id"] = None  # v2 desktop 永远 None，覆盖 build_baas_conn_info 的 target 值

    # ── type 字段从 bot.bot_type 透传（对齐 REL20260610 行为） ───────────
    #
    # 前端 ``selectWebsocketUrl`` / ``buildPrivateWebsocketUrl`` 按 type 分流:
    # - ``'local' | 'desktop'``  → 裸 ``ws://{target}/api/openclaw/ws``
    # - 其它(``'remote' | 'baas'`` / 任何非 local/desktop) → 走
    #   ``wss://agentclawproxy/proxypass/{target}/api/openclaw/ws``
    #
    # REL20260610 ``baas_device_service._compose_device_conn_info`` 写法:
    # 查 ``ac_bot.bot_type``,desktop → ``'desktop'``,其它 → ``'baas'``。
    # PR #2532 BaaS 容器六边形化重构丢了这次查询,写死 ``'desktop'``,导致
    # service+baas bot(GY 服务助手,``bot_type=service``)被错路由到裸
    # ``ws://ARCA_xxx@0:20003/...`` → 浏览器 WS 502。
    #
    # 现场证据:trace ``0b446a1f17816776901423178e41a8`` (06-17 14:28 GY服务助手)。
    # 复用现有 ``bot_type`` 参数（BaasConnInfoBuilder.build 已透传
    # ``binding.bot_type``）,无需新查 DB。
    if bot_type == "desktop":
        info["type"] = "desktop"
    else:
        info["type"] = "baas"
    if user_id:
        info["device_affinity"] = user_id
    if device_uuid:
        info["device_uuid"] = device_uuid
    return info


def _to_http_base(ws_url: str) -> str:
    """Normalize a BAAS ws_url into a base HTTP URL (no trailing ws path).

    Examples (BAAS 同事确定最终形态前的占位逻辑):
        wss://<host>/proxypass/<target>/api/openclaw/ws → https://<host>/proxypass/<target>
        ws://127.0.0.1:20003/api/openclaw/ws            → http://127.0.0.1:20003
    """
    base = ws_url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    if base == ws_url:
        logger.warning(
            "_to_http_base: ws_url has no recognized ws:// or wss:// scheme, "
            "returning as-is; downstream may receive a bad base URL. url=%r",
            ws_url,
        )
    for marker in _WS_PATH_MARKERS:
        if marker in base:
            return base.split(marker, 1)[0]
    return base.rstrip("/")
