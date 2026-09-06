"""SingleboxEngineAdapter: singlebox 本地链路直连 bot 引擎(WebSocket),实现 OpenApiBotPort。

替代 OpenApiBotAdapter(BaaS OpenApi)在 singlebox 的角色。BaaS OpenApi 在 singlebox 走不通:
``bot_service: local`` → ``get_binding`` raise PLATFORM_UNAVAILABLE(本地无远程 bot 元数据服务),
且 BaaS ``baas_service`` 用固定 adapter_port 跟 singlebox per-bot 动态端口对不上。本类复刻产品界面对话链路:

  backend(LocalAuth ``x-user-id``):``GET /api/bots/{bot_id}``→binding_id →
  ``GET /api/v1/devices/{binding_id}/connection``→ per-bot 引擎 ``localhost:port``(无鉴权)→
  ``POST /api/sessions`` 建 session → ``ws://localhost:port/api/openclaw/ws``:
  ``connect``(proto 3)→ ``chat.send{sessionKey,message}`` → 收 ``agent`` 流 + ``chat`` final(content[0].text)。

run dict 形状对齐 BaaS ``get_run`` data:``{status: COMPLETED|FAILED, result{content}, error}``,供
``SingleBotRunTranslator`` / ``_parse_children`` / ``_parse_search_result`` 复用(契约一致,零额外适配)。

- ``send_and_wait_async``(plan/dispatch,owner bot):await WS chat round-trip,同步返终态 run。
- ``send_message``+``get_run``(executor single_bot worker bot):fire ``chat.send``,后台 loop 收帧存 ``_runs``,
  poller 轮询 ``get_run``(同 BaaS 火-轮询模型;流式经后台桥接为轮询)。

env 选实现落在组合根(``TaskModule._resolve_ports``),本类不读 env、不含 case 知识。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any

import httpx
import websockets

from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotPort,
)
from agentclaw.community.core.task.task_runner.client.protocols import (
    BotPublicServiceProtocol,
)
from agentclaw.community.core.bot_public.catalog_metadata import BotCatalogCaller

_WS_PATH = "/api/openclaw/ws"
_CONNECT_PARAMS = {
    "minProtocol": 3,
    "maxProtocol": 3,
    "client": {
        "id": "singlebox-task-engine",
        "version": "1.0.0",
        "platform": "linux",
        "mode": "operator",
    },
    "role": "operator",
}


class SingleboxEngineAdapter(
    OpenApiBotPort
):  # pragma: no cover — live singlebox per-bot engine (WebSocket round-trip); needs a real singlebox backend, exercised by singlebox acceptance / 联调, not CI LOCAL line coverage
    """singlebox 直连 per-bot 引擎的 OpenApiBotPort 实现(WebSocket)。

    构造期收 backend(LocalAuth)地址 + user_id;per-bot 引擎 target 经 backend 解析并缓存。
    WS chat round-trip 可同步(``send_and_wait_async``)或火-后台收(``send_message``+poller ``get_run``)。
    """

    def __init__(
        self,
        *,
        backend_base_url: str,
        user_id: str,
        http_client: httpx.AsyncClient | None = None,
        collect_timeout: float | None = None,
    ) -> None:
        self._backend = backend_base_url.rstrip("/")
        self._user_id = user_id
        self._http = http_client or httpx.AsyncClient()
        self._collect_timeout = collect_timeout
        self._lock = threading.Lock()
        self._targets: dict[str, str] = {}  # bot_id → "localhost:20014"
        self._runs: dict[
            str, dict[str, Any]
        ] = {}  # run_id → {status, result{content}, error}
        self._collectors: dict[
            str, Any
        ] = {}  # run_id → run_coroutine_threadsafe Future
        self._cancelled_runs: set[str] = set()
        # 后台 loop:send_message 的 WS 收集器(poller 跨 loop 轮询 get_run,桥接流式→轮询)
        self._bg_loop = asyncio.new_event_loop()
        self._bg_thread = threading.Thread(
            target=self._bg_loop.run_forever, daemon=True, name="singlebox-ws-collector"
        )
        self._bg_thread.start()

    @property
    def api_key_prefix(self) -> str:
        # singlebox 不走 secbaas grant,派发也不做 claim_on 名单 JOIN(本地直连引擎),返回空串占位满足 OpenApiBotPort 契约。
        # claim_on JOIN 发生在 corp/prod(OpenApiBotAdapter + BcnService 名单),singlebox 无 secbaas api-key,不参与。
        return ""

    async def grant(self, *, bcs_bot_id: str, cookie: str, referer: str) -> None:  # noqa: ARG002
        raise NotImplementedError(
            "singlebox 不支持 secbaas grant(本地直连引擎,无 api-key 授权闭环)"
        )

    async def revoke(self, *, bcs_bot_id: str, cookie: str, referer: str) -> None:  # noqa: ARG002
        raise NotImplementedError("singlebox 不支持 secbaas revoke")

    async def _aclose(self) -> None:
        with self._lock:
            collectors = list(self._collectors.values())
            self._collectors.clear()
        for future in collectors:
            future.cancel()
        self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        self._bg_thread.join(timeout=2.0)
        if not self._bg_loop.is_closed():
            self._bg_loop.close()
        await self._http.aclose()

    # ===== OpenApiBotPort =====

    async def ensure_grant(self, bot_id: str) -> None:
        """singlebox 无 api-key grant:仅预解析并缓存 bot → 引擎 target(等同"确保可达")。"""
        await self._resolve_target(bot_id)

    async def send_message(
        self, *, bot_id: str, message: str, metadata: dict[str, Any]
    ) -> BotSendResult:
        """fire ``chat.send``:解析 target+建 session → 后台 WS 收帧存 ``_runs`` → 立即返 BotSendResult。

        解析/建会话失败不抛(避免打断 executor gather):落 FAILED 进 ``_runs``,poller 收口。
        run_id 为 poller 关联句柄,session_id 透传 WS session_key(workflow task_type 路径用)。
        """
        run_id = f"ws_{uuid.uuid4().hex[:8]}"
        resolved = await self._resolve_roundtrip_inputs(bot_id)
        if isinstance(resolved, str):  # 错误信息
            with self._lock:
                self._runs[run_id] = {"status": "FAILED", "error": resolved}
            return BotSendResult(run_id=run_id, session_id=None)
        target, session_key = resolved
        with self._lock:
            self._runs[run_id] = {"status": "RUNNING"}
        future = asyncio.run_coroutine_threadsafe(
            self._collect(run_id, target, session_key, message), self._bg_loop
        )
        with self._lock:
            self._collectors[run_id] = future
        future.add_done_callback(lambda done: self._collector_done(run_id, done))
        return BotSendResult(run_id=run_id, session_id=session_key)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """轮询 run 状态:未终态返 RUNNING;终态返 {status, result{content}, error}(对齐 BaaS)。"""
        with self._lock:
            return self._runs.get(run_id, {"status": "RUNNING"})

    async def cancel_run(self, run_id: str) -> None:
        """取消本地 WebSocket collector；用于 Poller 统一业务 SLA 到期后的清理。"""
        with self._lock:
            self._cancelled_runs.add(run_id)
            future = self._collectors.pop(run_id, None)
            self._runs[run_id] = {"status": "FAILED", "error": "cancelled"}
        if future is not None:
            future.cancel()

    async def send_and_wait_async(
        self,
        *,
        bot_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """sync WS chat round-trip(plan/dispatch owner bot):await 到 chat final,直返终态 run。"""
        resolved = await self._resolve_roundtrip_inputs(bot_id)
        if isinstance(resolved, str):
            return {"status": "FAILED", "error": resolved}
        target, session_key = resolved
        return await self._ws_chat_roundtrip(target, session_key, message, timeout)

    # ===== internals =====

    async def _collect(
        self, run_id: str, target: str, session_key: str, message: str
    ) -> None:
        try:
            run = await self._ws_chat_roundtrip(
                target, session_key, message, self._collect_timeout
            )
        except asyncio.CancelledError:
            return
        finally:
            with self._lock:
                self._collectors.pop(run_id, None)
        with self._lock:
            if run_id not in self._cancelled_runs:
                self._runs[run_id] = run

    def _collector_done(self, run_id: str, future: Any) -> None:
        """清理 collector 索引；覆盖 collector 在登记前极快结束的竞态。"""
        with self._lock:
            if self._collectors.get(run_id) is future:
                self._collectors.pop(run_id, None)
            self._cancelled_runs.discard(run_id)

    async def _resolve_roundtrip_inputs(self, bot_id: str) -> tuple[str, str] | str:
        """解析 target + 建 session,返 (target, session_key);失败返错误串。"""
        try:
            target = await self._resolve_target(bot_id)
            session_key = await self._create_session(target)
            if not session_key:
                return f"no_session_key: target={target}"
            return target, session_key
        except Exception as e:  # noqa: BLE001 本地链路异常收口成 FAILED
            return f"{type(e).__name__}: {e}"

    async def _resolve_target(self, bot_id: str) -> str:
        with self._lock:
            cached = self._targets.get(bot_id)
        if cached:
            return cached
        headers = {"x-user-id": self._user_id}
        r = await self._http.get(f"{self._backend}/api/bots/{bot_id}", headers=headers)
        r.raise_for_status()
        binding_id = (r.json().get("data") or {}).get("binding_id")
        if binding_id is None:
            raise RuntimeError(f"bot not found / no binding_id: {bot_id}")
        c = await self._http.get(
            f"{self._backend}/api/v1/devices/{binding_id}/connection", headers=headers
        )
        c.raise_for_status()
        target = (c.json().get("data") or {}).get("target")
        if not target:
            raise RuntimeError(
                f"no connection target: bot={bot_id} binding={binding_id}"
            )
        with self._lock:
            self._targets[bot_id] = target
        return target

    async def _create_session(self, target: str) -> str:
        r = await self._http.post(
            f"http://{target}/api/sessions",
            json={"title": "task", "user_id": self._user_id},
            headers={"x-user-id": self._user_id},
        )
        r.raise_for_status()
        return (r.json().get("data") or {}).get("id") or ""

    async def _ws_chat_roundtrip(
        self, target: str, session_key: str, message: str, timeout: float | None
    ) -> dict[str, Any]:
        """开 WS:connect(proto3)→ chat.send → 收到 chat final/error/超时 → 返终态 run dict(不抛)。"""
        uri = f"ws://{target}{_WS_PATH}"
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            async with websockets.connect(uri, open_timeout=10) as ws:
                # 1) 握手
                await ws.send(
                    json.dumps(
                        {
                            "type": "req",
                            "id": "1",
                            "method": "connect",
                            "params": _CONNECT_PARAMS,
                        }
                    )
                )
                hs = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if not hs.get("ok"):
                    return {
                        "status": "FAILED",
                        "error": f"handshake_failed: {json.dumps(hs)[:200]}",
                    }
                # 2) 发消息
                await ws.send(
                    json.dumps(
                        {
                            "type": "req",
                            "id": "2",
                            "method": "chat.send",
                            "params": {"sessionKey": session_key, "message": message},
                        }
                    )
                )
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if not ack.get("ok"):
                    return {
                        "status": "FAILED",
                        "error": f"chat_send_rejected: {json.dumps(ack)[:200]}",
                    }
                # 3) 读事件到 final
                while deadline is None or time.monotonic() < deadline:
                    try:
                        if deadline is None:
                            raw = await ws.recv()
                        else:
                            remaining = max(0.1, deadline - time.monotonic())
                            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return {"status": "FAILED", "error": "timeout"}
                    data = json.loads(raw)
                    if data.get("type") != "event":
                        continue
                    if data.get("event") != "chat":
                        continue
                    payload = data.get("payload") or {}
                    state = payload.get("state")
                    if state == "final":
                        return {
                            "status": "COMPLETED",
                            "result": {"content": _extract_final_text(payload)},
                        }
                    if state == "error":
                        return {
                            "status": "FAILED",
                            "error": payload.get("errorMessage") or "chat_error",
                        }
                return {"status": "FAILED", "error": "timeout"}
        except Exception as e:  # noqa: BLE001 本地链路异常收口成 FAILED(不抛)
            return {"status": "FAILED", "error": f"{type(e).__name__}: {e}"}


class SingleboxBotProvisioner:  # pragma: no cover — singlebox local e2e provisioning helper (HTTP create bot / install skill); exercises a real singlebox backend, not CI LOCAL line coverage
    """singlebox 本地集成测试 provisioning 助手(建 bot + 装 skill),非框架运行时。

    framework 运行时(``SingleboxEngineAdapter``)不感知 bot/skill 存在;本类复刻产品界面操作,
    供 e2e 跑前 reproducible 拉起环境(singlebox 重启清空内存后一键重建 bot+skill)。仅本地集成用:
    ``SingleboxEngineAdapter`` 收 bot_id 即开跑,本类负责把那个 bot_id 连同其 skill 准备出来。

    - ``create_bot`` → ``POST /api/bots`` + 轮询等 ACTIVE + set public=1 → 返 ``bot_id``
    - ``install_skills`` → 逐 skill 目录 upload → 建 skill set → 加技能(自动激活)→ 落 activate → 返 ``skill_set_id``
    """

    def __init__(
        self,
        *,
        backend_base_url: str,
        user_id: str,
        http_client: httpx.AsyncClient | None = None,
        default_engine_type: str = "openclaw",
        wait_active_timeout: float = 120.0,
        wait_active_interval: float = 1.5,
    ) -> None:
        self._backend = backend_base_url.rstrip("/")
        self._user_id = user_id
        self._default_engine = default_engine_type
        self._wait_active_timeout = wait_active_timeout
        self._wait_active_interval = wait_active_interval
        self._http = http_client or httpx.AsyncClient(timeout=60.0)

    async def _aclose(self) -> None:
        await self._http.aclose()

    def _hdrs(self) -> dict[str, str]:
        return {"x-user-id": self._user_id, "accept": "application/json"}

    # ===== create bot =====
    async def _list_my_bots(self) -> list[dict[str, Any]]:
        """``GET /api/bots/by-owner-or-collaborator`` 取本 user 的 bot 列表(LocalAuth ``x-user-id``)。"""
        r = await self._http.get(
            f"{self._backend}/api/bots/by-owner-or-collaborator",
            params={"user_id": self._user_id},
            headers=self._hdrs(),
        )
        if r.status_code != 200:
            return []
        return (r.json().get("data") or {}).get("items") or []

    async def _find_existing_bot(self, bot_name: str | None) -> dict[str, Any] | None:
        """按 bot_name 在本 user bot 列表里查已存在的 ACTIVE bot(幂等:已建则复用,不重复建)。

        反复跑集成用例时复用上次 provisioned 的 bot(同进程 session 内 bot 仍在内存);singlebox 重启清空后判定为空→正常新建。
        """
        if not bot_name:
            return None
        return next(
            (it for it in await self._list_my_bots() if it.get("bot_name") == bot_name),
            None,
        )

    async def create_bot(
        self,
        *,
        bot_name: str | None = None,
        bot_desc: str | None = None,
        engine_type: str | None = None,
        bot_type: str = "personal",
        set_public: bool = True,
        wait_active: bool = True,
    ) -> str:
        """``POST /api/bots`` 建个人 bot(singlebox mock passport 即时签发)→ 轮询等 ACTIVE → set public=1。返 ``bot_id``。

        singlebox 本地走 LocalAuth(``x-user-id`` 无 ctoken);engine 默认 openclaw(对齐 singlebox 主链路)。
        public=1 仅为 BCSFuse discover 可见,设置失败不阻断(本地直连 WS 不经 discover)。

        幂等:按 ``bot_name`` 先查已存在的 ACTIVE bot,复用其 id(确保 public),不重复建。
        """
        if bot_name:
            existing = await self._find_existing_bot(bot_name)
            if existing:
                bot_id = existing.get("bot_id")
                if (
                    str(existing.get("status") or "").upper() != "ACTIVE"
                    and wait_active
                ):
                    try:
                        await self.wait_active(bot_id)  # 残留/并发 PENDING bot 等就绪
                    except Exception:  # noqa: BLE001  等不到 ACTIVE 仍返回(下游 WS 自愈)
                        pass
                if set_public:
                    try:
                        await self.set_public(bot_id, True)
                    except Exception:  # noqa: BLE001  public 设置失败不阻断
                        pass
                return bot_id  # 复用已建 bot(任意状态)
        body: dict[str, Any] = {
            "bot_name": bot_name,
            "bot_desc": bot_desc,
            "engine_type": engine_type or self._default_engine,
            "bot_type": bot_type,
            "entity_id": self._user_id,
            "entity_type": "staff",
        }
        r = await self._http.post(
            f"{self._backend}/api/bots", headers=self._hdrs(), json=body
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            msg = str(data.get("message") or data)
            if "already exists" in msg.lower() and bot_name:
                # 竞态/残留同名 bot(PENDING):按名取回,等 ACTIVE 复用,不报错
                existing = await self._find_existing_bot(bot_name)
                if existing:
                    bot_id = existing.get("bot_id")
                    if wait_active:
                        try:
                            await self.wait_active(bot_id)
                        except Exception:  # noqa: BLE001
                            pass
                    if set_public:
                        try:
                            await self.set_public(bot_id, True)
                        except Exception:  # noqa: BLE001
                            pass
                    return bot_id
            raise RuntimeError(f"create_bot failed: {msg}")
        bot = (data.get("data") or {}).get("bot") or {}
        bot_id = bot.get("bot_id")
        if not bot_id:
            raise RuntimeError(f"create_bot: no bot_id in response: {data}")
        if wait_active:
            await self.wait_active(bot_id)
        if set_public:
            try:
                await self.set_public(bot_id, True)
            except Exception:  # noqa: BLE001  public 设置失败不阻断主流程
                pass
        return bot_id

    async def wait_active(self, bot_id: str) -> dict[str, Any]:
        """轮询 ``GET /api/bots/by-owner-or-collaborator`` 等 ``bot_id`` 状态 ACTIVE,返该 bot dict。"""
        deadline = time.monotonic() + self._wait_active_timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            r = await self._http.get(
                f"{self._backend}/api/bots/by-owner-or-collaborator",
                params={"user_id": self._user_id},
                headers=self._hdrs(),
            )
            if r.status_code == 200:
                items = (r.json().get("data") or {}).get("items") or []
                for it in items:
                    if it.get("bot_id") == bot_id:
                        last = it
                        if str(it.get("status") or "").upper() == "ACTIVE":
                            return it
            await asyncio.sleep(self._wait_active_interval)
        raise RuntimeError(
            f"bot {bot_id} not ACTIVE within {self._wait_active_timeout}s (last={last})"
        )

    async def set_public(self, bot_id: str, public: bool = True) -> dict[str, Any]:
        """``POST /api/bots/{bot_id}/public`` 设公开(供 BCSFuse discover 可见)。"""
        r = await self._http.post(
            f"{self._backend}/api/bots/{bot_id}/public",
            headers=self._hdrs(),
            json={"public": "1" if public else "0", "user_id": self._user_id},
        )
        r.raise_for_status()
        return r.json()

    async def onboard_to_bcn(
        self, bot_id: str, bot_desc: str | None = None
    ) -> dict[str, Any]:
        """``PUT /api/bots/{bot_id}`` 改 ``bot_desc`` → 触发 ``bot_service._sync_bot_to_bcn`` → ``BcnService.onboard_bot``
        → BCN ``POST /admin/bots/onboard`` 把 ``{bot_id}:{owner_id}`` 注册进协作网。

        **为何需要**:`create_bot` 对 ``openclaw+personal`` bot 主动 skip BCN provider 注册
        (``_should_register_bcn_provider`` 返回 False + DRM 默认关),provisioned bot 默认不在 BCN;
        ``form_coop_group`` 建群校验成员时 BCS 按 ``{bot_id}:{owner_id}`` 查会 404 ``bot_not_found``。
        本方法走 **update(上行 onboard)路**——不经 ``_should_register_bcn_provider``/DRM gate——把 bot 入网 BCN。
        **coop_group 成员 bot 建群前必须 onboard**(single_bot/BBS 用不到,调了也无害)。
        """
        r = await self._http.put(
            f"{self._backend}/api/bots/{bot_id}",
            headers=self._hdrs(),
            json={"bot_desc": bot_desc or "e2e fixture bot"},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(
                f"onboard_to_bcn failed (PUT /api/bots/{bot_id}): {data.get('message') or data}"
            )
        return data

    async def set_bcs_visibility(
        self, bot_id: str, visibility: str = "public"
    ) -> dict[str, Any]:
        """``PUT /bots/{bot_uuid}/visibility`` 到 BCS(:21000) 设**单个 bot** 的 BCS visibility。

        bot_uuid = ``{bot_id}:{owner_id}``(BCN onboard 时的 bcn_bot_id 格式,owner=本 provisioner 的
        user_id)。singlebox ``BCS_AUTH_MOCK=1``,无需真 token。

        为何需要:BCS 建群 ``ensure_reachable`` 对 ``visibility=="protected"`` 的成员 bot 做好友校验
        (403 "not friends");``visibility=="public"`` 直接放行(不查好友)。singlebox bcs-config
        ``default_visibility="protected"`` 是公共配置不能动,故对**要进协作群的成员 bot** 单独 PUT 成
        ``public``,绕开好友校验、让真 ``form_coop_group`` 建群过(UI 可见真群)。只调你要的 bot,不改全局。
        """
        bcs_url = os.environ.get("BCS_API_BASE_URL", "http://127.0.0.1:21000").rstrip(
            "/"
        )
        bot_uuid = f"{bot_id}:{self._user_id}"
        r = await self._http.put(
            f"{bcs_url}/bots/{bot_uuid}/visibility",
            headers={"accept": "application/json"},
            json={"visibility": visibility},
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"set_bcs_visibility failed (PUT {bcs_url}/bots/{bot_uuid}/visibility "
                f"visibility={visibility}): {r.status_code} {r.text[:200]}"
            )
        return r.json() if r.text else {}

    async def set_bbs_task_dream_mode(
        self, bot_id: str, enabled: bool = True
    ) -> dict[str, Any]:
        """开启单个 bot 的 BCS ``task_dream_mode``(BBS 主动 bid roster 入选开关)。

        唯一 setter 是 principal-gated 的 BCS openapi ``PATCH /openapi/v1/collaboration/bots/{bot_id}``
        (``bcs-api-http`` openapi v1,经 ``GatewayPrincipalTokenVerifier`` 校验)。``task_dream_mode`` 的
        读写与 ``set_bcs_visibility`` 不同:visibility 走 bcs-http mock-auth 面,无 token;dream-mode 只在
        openapi v1 面,必须带 gateway principal token。

        singlebox launcher 已设 ``AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE``(默认
        ``avernet-dev-signing-key-NOT-FOR-PROD``,见 ``scripts/modules/bcs.sh:884`` /
        ``scripts/modules/backend.sh:86``)。本方法自铸一个 gateway principal token:
        HS256 / iss=gateway / aud=bcs / kid=bare / principals=[user(subject.id=user_id)]。
        token claim shape 对齐 BCS ``wire.rs:GatewayUserPrincipal``(与 Avernet gateway_principal 共享
        gateway 签发契约)。user_id 即 bot 的 owner staff_no(创建时 ``entity_id=user_id``),经 BCS
        ``authorize_bot_management`` 的 owner 匹配(``caller_actor_id == created_by``)放行。

        bot_uuid=``{bot_id}:{user_id}``(同 ``set_bcs_visibility``)。PATCH 只传 task_dream_mode
        (BCS ``UpdateBotRequest`` 各字段 Option,仅更新传入项)。非 2xx 抛错带 status/body,
        便于定位 principal 形状 / owner 匹配问题(若 403 可调 principal subject.id)。
        """
        import time as _t

        import jwt  # PyJWT(Avernet gateway_principal verifier 同库;HS256)

        bcs_url = os.environ.get("BCS_API_BASE_URL", "http://127.0.0.1:21000").rstrip(
            "/"
        )
        key = os.environ.get(
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "avernet-dev-signing-key-NOT-FOR-PROD",
        )
        bot_uuid = f"{bot_id}:{self._user_id}"
        now = int(_t.time())
        claims: dict[str, Any] = {
            "iss": "gateway",
            "aud": "bcs",
            "iat": now,
            "exp": now + 300,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": self._user_id,
                        "username": self._user_id,
                        "display_name": "singlebox-e2e",
                        "full_name": None,
                        "tenant_id": "default",
                    },
                }
            ],
        }
        token = jwt.encode(claims, key, algorithm="HS256", headers={"kid": "bare"})
        r = await self._http.patch(
            f"{bcs_url}/openapi/v1/collaboration/bots/{bot_uuid}",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={"task_dream_mode": enabled},
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"set_bbs_task_dream_mode failed (PATCH "
                f"{bcs_url}/openapi/v1/collaboration/bots/{bot_uuid} "
                f"task_dream_mode={enabled}): {r.status_code} {r.text[:300]}"
            )
        return r.json() if r.text else {}

    # ===== install skills =====
    async def install_skills(
        self,
        bot_id: str,
        skill_dirs: list[str],
        *,
        skill_set_name: str = "task-framework-skills",
        skill_set_desc: str | None = None,
        upload_mode: str = "create",
        entity_type: str = "staff",
    ) -> str:
        """装 skill:逐目录(含 ``SKILL.md``)upload → 建 skill set → 加技能(自动激活)→ 落 activate。返 ``skill_set_id``。

        **幂等**:先查该 bot 已装 skill 的 name 集合(``GET /api/skills?bot_id=...``),frontmatter.name 命中已装 →
        跳过 upload(不重复建同名 skill);skill set 同名复用(``_ensure_skill_set``);add/activate 均幂等。
        每个 ``skill_dir`` 形如 ``…/skills/planning/(SKILL.md)``;``SKILL.md`` frontmatter.name 即 skill 名。
        """
        import os as _os

        # 0) readiness guard:装 skill 前确保 bot 已创建完成(status=ACTIVE);PENDING→ACTIVE 约 15s
        await self.wait_active(bot_id)

        # 1) upload each skill dir (幂等:跳过已装同名 skill)
        installed = await self._installed_skill_names(bot_id)
        skill_ids: list[str] = []
        for sd in skill_dirs:
            skill_md_path = _os.path.join(sd, "SKILL.md")
            sname = self._read_skill_name(skill_md_path)
            if sname and sname in installed:
                continue  # 已装同名 skill,跳过 upload
            with open(skill_md_path, "rb") as fh:
                content = fh.read()
            files = {"files": ("SKILL.md", content, "text/markdown")}
            form = {"file_paths": json.dumps(["SKILL.md"])}
            r = await self._http.post(
                f"{self._backend}/api/skills/upload",
                params={
                    "user_id": self._user_id,
                    "bot_id": bot_id,
                    "upload_mode": upload_mode,
                },
                headers=self._hdrs(),
                files=files,
                data=form,
            )
            r.raise_for_status()
            body = r.json()
            sid = (body.get("data") or {}).get("id") if body.get("success") else None
            if not sid:
                raise RuntimeError(
                    f"upload skill {sd} failed: {body.get('message') or body}"
                )
            skill_ids.append(str(sid))

        # 2) ensure skill set (同名已存则复用其 id,避免重复建)
        skill_set_id = await self._ensure_skill_set(
            bot_id, skill_set_name, skill_set_desc
        )

        # 3) add skills to set (自动激活;重复添加返回 already-in-set,不视为失败)
        if skill_ids:
            r = await self._http.post(
                f"{self._backend}/api/skillsets/{skill_set_id}/skills",
                params={"user_id": self._user_id, "bot_id": bot_id},
                headers=self._hdrs(),
                json={
                    "skill_ids": skill_ids,
                    "user_id": self._user_id,
                    "bot_id": bot_id,
                },
            )
            r.raise_for_status()

        # 4) 落 activate (幂等:已激活返 already active)
        await self._activate_skill_set(skill_set_id, bot_id, entity_type)
        return skill_set_id

    async def get_active_skill_bots(self, bot_id: str) -> list[dict[str, Any]]:
        """``GET /api/skills/active/list`` 取 bot 当前激活 skill 列表(注:singlebox community 模式下可能为空,
        因 active/list 扫 symlink 而 device_sync 为 no-op;以 ``skill_sets.json`` / 实际触达判定为准)。"""
        r = await self._http.get(
            f"{self._backend}/api/skills/active/list",
            params={"entity_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
        )
        r.raise_for_status()
        return r.json().get("data") or []

    async def list_skill_sets(self, bot_id: str) -> list[dict[str, Any]]:
        """``GET /api/skillsets`` 取 bot 的能力集列表(is_active 标志即引擎加载态)。"""
        r = await self._http.get(
            f"{self._backend}/api/skillsets",
            params={"user_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
        )
        r.raise_for_status()
        return r.json().get("data") or []

    async def _installed_skill_names(self, bot_id: str) -> set[str]:
        """``GET /api/skills?bot_id=...&user_id=...`` 取该 bot 已装 skill 的 name 集合(幂等跳过判定依据)。

        singlebox community 模式 active/list 扫 symlink 可能为空;DB 查询 ``/api/skills`` 更可靠(查 skill 表该 bot 名下记录)。
        """
        names: set[str] = set()
        r = await self._http.get(
            f"{self._backend}/api/skills",
            params={
                "user_id": self._user_id,
                "bot_id": bot_id,
                "page": 1,
                "page_size": 200,
            },
            headers=self._hdrs(),
        )
        if r.status_code == 200:
            for sk in r.json().get("data") or []:
                n = sk.get("name")
                if n:
                    names.add(str(n))
        return names

    @staticmethod
    def _read_skill_name(skill_md_path: str) -> str | None:
        """从 ``SKILL.md`` frontmatter 解析 ``name:`` 字段(幂等跳过判定依据);无 frontmatter 返 None。"""
        import os as _os

        if not _os.path.exists(skill_md_path):
            return None
        with open(skill_md_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if not text.startswith("---"):
            return None
        end = text.find("---", 3)
        if end < 0:
            return None
        for line in text[3:end].splitlines():
            ls = line.strip()
            if ls.startswith("name:"):
                return ls[len("name:") :].strip().strip('"').strip("'")
        return None

    async def _ensure_skill_set(self, bot_id: str, name: str, desc: str | None) -> str:
        """建 skill set;同名已存则复用其 id(幂等)。"""
        r = await self._http.get(
            f"{self._backend}/api/skillsets",
            params={"user_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
        )
        if r.status_code == 200:
            for s in r.json().get("data") or []:
                if s.get("name") == name:
                    return str(s.get("id"))
        r = await self._http.post(
            f"{self._backend}/api/skillsets",
            params={"user_id": self._user_id, "bot_id": bot_id},
            headers=self._hdrs(),
            json={
                "name": name,
                "description": desc or "",
                "user_id": self._user_id,
                "bot_id": bot_id,
            },
        )
        r.raise_for_status()
        body = r.json()
        sid = (body.get("data") or {}).get("id") if body.get("success") else None
        if not sid:
            raise RuntimeError(f"create skillset failed: {body}")
        return str(sid)

    async def _activate_skill_set(
        self, skill_set_id: str, bot_id: str, entity_type: str
    ) -> dict[str, Any]:
        r = await self._http.post(
            f"{self._backend}/api/skills/skillset/activate",
            headers=self._hdrs(),
            json={
                "skill_set_id": skill_set_id,
                "entity_id": self._user_id,
                "entity_type": entity_type,
                "bot_id": bot_id,
            },
        )
        r.raise_for_status()
        return r.json()


class CatalogKeywordBotDiscover:
    """Catalog 关键字搜推适配:实现 ``BotDiscoverServiceProtocol.search_by_keyword``,
    底层走 ``BotPublicService.search_catalog_public_bots_by_keyword``(BCS catalog 关键字搜索,
    适配 OSS ``public='0'`` 部署 bot 也能命中)。

    非单 ``singlebox`` 专用:corp / prod / community / singlebox 各 profile 的 task 派发候选预查
    均复用此实现(``TaskModule._resolve_discover``)。底层经 BCS ``/bots/search`` 关键字检索后回 join
    后端 bot 元数据,每个 item 自带完整 ``bot_uuid``(``{bot_id}:{entity_id}``,对齐 BCN onboard 的
    ``bot_id:owner_id`` 形态),供下游 BCS 派发身份解析直接消费,无需搜推层再做 product→复合兜底。

    - 关键词为空 → 返空(对齐 catalog 空关键词行为);
    - 无语义 score → 合成 ``recommend.score``(命中次序降权),保 stable 排序供策略排序;
    - ``filters``(runtime_state 等) 本类忽略(catalog 无 runtime 在线态维度),统一传 ``filters=None``
      仅取 BCS ``visibility=public`` bot(公开可认领语义),不含 protected;
    - 身份参数:catalog ``caller`` 在 ``search_public_bot_metadata`` 顶部即 ``del caller``(废弃),
      占位 ``BotCatalogCaller(tenant_id="", user_id=None, app_id=None)`` 不影响检索结果;
      ``request_id`` 仅日志,生成 ``task-prefetch-<uuid12>``;
    - 端口/catalog 不可用(``BotCatalogSearchUnavailableError`` 等) → 返空,不阻断字段预查。
    """

    def __init__(self, bot_public_service: "BotPublicServiceProtocol") -> None:
        self._bps = bot_public_service

    def search_by_keyword(
        self,
        *,
        keyword: str,
        user_id: str,
        top_k: int = 10,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
        fallback_to_all: bool = False,
    ) -> dict[str, Any]:
        """catalog 候选预查:**决策非查找**——关键字命中返命中;命中 0 默认返空(收窄,不盲目塞全量
        噪音 bot,避免 search skill 在无关候选里自由组合)。``fallback_to_all=True`` 时回落全量公开 bot
        (显式场景:产品搜索等需要"有结果"兜底)。谁执行仍由 search skill 在候选里决,本层只供候选。"""
        # 1) catalog 关键字命中(bot 按能力命名时能命中)
        hits = self._query(user_id=user_id, search=keyword or None, top_k=top_k)
        used_fallback = False
        if not hits and fallback_to_all:
            # 2) 显式回落:全部公开 bot(仅 fallback_to_all=True 时)
            hits = self._query(user_id=user_id, search=None, top_k=top_k)
            used_fallback = bool(hits)
        # 合成 recommend.score(命中次序降权),对齐 BCSFuse items 形态供策略排序
        for i, it in enumerate(hits):
            rec = it.get("recommend")
            if not isinstance(rec, dict):
                rec = {}
            if "score" not in rec:
                rec["score"] = max(min_score, 1.0 - i * 0.05)
            it["recommend"] = rec
        return {
            "total": len(hits),
            "items": hits,
            "context": {"mode": "catalog_keyword", "fallback_to_all": used_fallback},
        }

    def _query(
        self, *, user_id: str, search: str | None, top_k: int
    ) -> list[dict[str, Any]]:
        """调 ``search_catalog_public_bots_by_keyword``(BCS catalog)并收口异常→空列表(不阻断预查)。

        ``caller`` 占位(catalog service 顶部 ``del caller``)、``request_id`` 仅日志;``filters=None``
        仅取 BCS ``visibility=public`` bot。返回 item 自带 ``bot_uuid``(``{bot_id}:{entity_id}``)。"""
        try:
            res = self._bps.search_catalog_public_bots_by_keyword(
                search=search,
                page=1,
                page_size=top_k,
                caller=BotCatalogCaller(tenant_id="", user_id=None, app_id=None),
                request_id=f"task-prefetch-{uuid.uuid4().hex[:12]}",
                filters=None,
            )
        except Exception:  # noqa: BLE001  端口/catalog 不可用→空候选
            return []
        return res.get("items") or []


def _extract_final_text(payload: dict[str, Any]) -> str:
    """从 chat final 事件聚合全部文本 content block，避免首块为 tool 时丢终态 JSON。"""
    msg = payload.get("message") or {}
    contents = msg.get("content") or []
    if not isinstance(contents, list):
        return ""
    texts = []
    for block in contents:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    return "\n".join(texts)
