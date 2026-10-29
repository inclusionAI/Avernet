"""AsyncChatClient 连接池

按 sandbox_id 维护多条 AsyncChatClient 连接，避免每次 API 调用都新建 WebSocket。

设计要点：
- 每个 sandbox_id 最多维护 max_conns_per_sandbox 条长连接
- AsyncChatClient 本身支持同一连接上多 sessionKey 并行收发，连接不被请求独占
- On-demand 扩容：有空闲连接就复用（Least-Sessions），都在忙就开新连接（直到上限）
- Per-key Lock：同一 sandbox_id 的并发请求串行等锁，不同 sandbox_id 互不阻塞
- 池有总容量上限（max_size），超出时 LRU 淘汰最久未使用且无活跃会话的单条连接
- 空闲缩容：同一 sandbox_id 下多余的空闲连接可被回收
- 每条连接可配置并发信号量（max_concurrent_per_conn）和 sessionKey 排队超时（session_key_timeout）
- 重连中的连接会被跳过，不分配给新请求
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial

from secbaas.community.api.config_manage import SystemConfigManageService
from secbaas.community.core.service.config import SystemConfigKey
from secbaas.community.logger import get_logger

from ..bot_interaction import BotInteractionService
from ._async_chat_client import AsyncChatClient

logger = get_logger("core-bot-run")

# 敏感日志豁免片段的 TTL 缓存时长（秒）：过期后重新查 DB，
# 配置变更无需重启即可生效
_BYPASS_KEYS_TTL_SECONDS = 60.0


@dataclass
class _ConnEntry:
    """单条连接的元数据"""

    client: AsyncChatClient
    last_used: float = field(default_factory=time.time)
    # P1-3：建连时生效的灰度模式快照——复用路径用它校验连接模式与当前
    # 配置一致（AsyncChatClient 的模式随构造钉死，翻转只能靠池层失效）
    a2a_mode: str = "off"


class AsyncChatClientPool:
    """按 sandbox_id 维护多条 AsyncChatClient 连接

    使用方式:
        pool = AsyncChatClientPool(max_size=100, max_conns_per_sandbox=2)
        client = await pool.get(sandbox_id, uri, headers)
        # 直接使用 client，无需 release
        content, events = await client.send_message(...)

        # 程序退出时
        await pool.close_all()
    """

    def __init__(
        self,
        max_size: int = 100,
        max_conns_per_sandbox: int = 1,
        max_concurrent_per_conn: int = 0,
        session_key_timeout: float = 30.0,
        max_retries: int = 1,
        retry_base_backoff: float = 0.5,
        system_config_service: SystemConfigManageService | None = None,
        interaction_service: BotInteractionService | None = None,
    ) -> None:
        """初始化连接池

        Args:
            max_size: 池中最大总连接数，超出时 LRU 淘汰
            max_conns_per_sandbox: 每个 sandbox_id 最多维护的连接数
            max_concurrent_per_conn: 每条连接最大并发会话数，0 表示不限
            session_key_timeout: 同一 sessionKey 并发等待超时（秒）
            max_retries: WS 断连后自动重连次数，0 表示不重试
            retry_base_backoff: 重连退避基数（秒）
            system_config_service: 系统配置服务，用于创建连接时读取运行开关
            interaction_service: 交互持久化与应答服务；仅在交互开关启用时注入连接
        """
        self._max_size = max_size
        self._max_conns_per_sandbox = max_conns_per_sandbox
        self._max_concurrent_per_conn = max_concurrent_per_conn
        self._session_key_timeout = session_key_timeout
        self._max_retries = max_retries
        self._retry_base_backoff = retry_base_backoff
        self._system_config_service = system_config_service
        self._interaction_service = interaction_service
        # 敏感日志豁免片段缓存：(loaded_at, keys)；-inf 表示未加载
        self._bypass_keys_cache: tuple[float, tuple[str, ...]] = (
            float("-inf"),
            (),
        )
        # sandbox_id -> 连接列表
        self._clients: dict[str, list[_ConnEntry]] = {}
        # per-key lock，保护同一 sandbox_id 的并发创建
        self._key_locks: dict[str, asyncio.Lock] = {}
        # 全局锁，仅用于 _key_locks 的创建/删除
        self._lock = asyncio.Lock()

    # ── 公开 API ──────────────────────────────────────────────────────────

    @property
    def total_connections(self) -> int:
        """当前池中总连接数"""
        return sum(len(entries) for entries in self._clients.values())

    async def get(
        self,
        sandbox_id: str,
        uri: str,
        headers: dict[str, str] | None = None,
        session_key: str | None = None,
        token_minter: Callable[[], Awaitable[str]] | None = None,
    ) -> AsyncChatClient:
        """获取该 sandbox_id 对应的连接（Least-Sessions 选择，On-demand 扩容）

        Args:
            sandbox_id: 沙箱标识，作为连接池的 key
            uri: WebSocket URI
            headers: WebSocket 请求头
            session_key: R4-1 session 归属定向。携带时先按归属查找持有
                该 session 在途状态的连接（覆盖含 mode-stale 的全部连接）
                ——A2A 的 CancelTask 依赖建连实例内的 engine_task_id 记
                录，"普通负载分配"在默认 max_conns_per_sandbox=2 下即可
                把取消路由到无记录的新连接（RUN_NOT_TRACKED，永不发
                CancelTask）。无归属时回退正常分配（v3 chat.abort 按
                sessionKey 对任意连接成立；A2A 由内层诚实判
                RUN_NOT_TRACKED）。

        Returns:
            可用的 AsyncChatClient 实例
        """
        # R4-1：归属查找先于一切建连/模式决策（多出来的 owner 对象不应
        # 触发扩容；mode-stale 持有者的连接模式不参与校验——它正在服务
        # 该 session 的在途 run）。
        if session_key is not None:
            owner = self._find_session_owner(sandbox_id, session_key)
            if owner is not None:
                logger.info(
                    "[ChatClientPool] session-routed get: sandbox=%s owner_conns=%d",
                    sandbox_id,
                    len(self._clients.get(sandbox_id, [])),
                )
                self._refresh_outbound_auth(owner, headers)
                return owner
        # P1-3：每次 get() 决策点读一次当前灰度模式——复用连接前校验其
        # 建连模式快照与当前配置一致，不一致的停止承接新请求（空闲即时
        # 收口关闭，在途的排空后关闭）。读频与建连同一粒度（每 get 恰一
        # 次 A2A_CLIENT_MODE 读取）：off 且无 config 服务时是纯常量路径
        # （零 I/O，off 快路径语义不变）；开关翻转即时生效、无陈旧窗口。
        current_mode = self._read_a2a_mode(self._system_config_service)
        self._sweep_mode_stale(sandbox_id, current_mode)

        # 快速路径（无锁）：仅尝试复用空闲连接，不触发扩容决策。
        # 扩容决策必须在锁内做，否则多个协程会同时看到"需要扩容"
        # 并各自创建连接。
        client = self._pick_idle(sandbox_id, current_mode)
        if client is not None:
            logger.info(
                "[ChatClientPool] Fast path reused idle connection: "
                "sandbox=%s, active_sessions=%d",
                sandbox_id,
                client.active_session_count,
            )
            self._refresh_outbound_auth(client, headers)
            return client

        logger.info(
            "[ChatClientPool] Fast path miss, entering slow path: sandbox=%s",
            sandbox_id,
        )

        # 获取 per-key lock
        key_lock = await self._get_key_lock(sandbox_id)

        # 慢路径：per-key lock 内完成所有决策（复用/扩容/创建）
        async with key_lock:
            # 排空模式翻转过的重连窗口（sweep 幂等——锁内再校一次，
            # 覆盖 fast/slow 之间状态变化的窄窗口）
            self._sweep_mode_stale(sandbox_id, current_mode)
            # 优先复用空闲连接
            client = self._pick_idle(sandbox_id, current_mode)
            if client is not None:
                logger.info(
                    "[ChatClientPool] Slow path reused idle connection "
                    "after lock: sandbox=%s, active_sessions=%d",
                    sandbox_id,
                    client.active_session_count,
                )
                self._refresh_outbound_auth(client, headers)
                return client

            # 清理不健康/重连中的连接
            self._remove_unhealthy(sandbox_id)

            # 已有健康连接但都在忙：决定是否扩容
            entries = self._clients.get(sandbox_id, [])
            if entries and len(entries) >= self._max_conns_per_sandbox:
                # 已达上限，返回最不忙的连接（即使都在忙）
                logger.info(
                    "[ChatClientPool] Per-sandbox limit reached, "
                    "returning least-sessions: sandbox=%s, conns=%d/%d",
                    sandbox_id,
                    len(entries),
                    self._max_conns_per_sandbox,
                )
                best = self._pick_least_sessions(entries, current_mode)
                if best is not None:
                    self._refresh_outbound_auth(best, headers)
                    return best
                # 所有连接都不健康（或模式翻转不匹配）且已被清理，继续创建新的

            # 需要创建新连接（无连接、或未达上限且都在忙）
            logger.info(
                "[ChatClientPool] Creating connection for sandbox=%s "
                "(slot %d/%d, existing=%d)",
                sandbox_id,
                len(self._clients.get(sandbox_id, [])) + 1,
                self._max_conns_per_sandbox,
                len(self._clients.get(sandbox_id, [])),
            )
            new_client = AsyncChatClient(
                uri=uri,
                headers=headers,
                max_concurrent_sessions=self._max_concurrent_per_conn,
                session_key_timeout=self._session_key_timeout,
                max_retries=self._max_retries,
                retry_base_backoff=self._retry_base_backoff,
                a2a_mode=current_mode,
                ignore_case=self._read_ignore_case(self._system_config_service),
                interaction_service=self._interaction_service
                if self._enable_process_interaction(self._system_config_service)
                else None,
                sensitive_bypass_keys_loader=partial(
                    self._load_sensitive_bypass_keys,
                    self._system_config_service,
                ),
                proxypass_token_minter=token_minter,
            )
            await new_client.connect()

            entry = _ConnEntry(client=new_client, a2a_mode=current_mode)
            if sandbox_id not in self._clients:
                self._clients[sandbox_id] = []
            self._clients[sandbox_id].append(entry)

            logger.info(
                "[ChatClientPool] Connection created and pooled: "
                "sandbox=%s, total_conns=%d, pool_total=%d",
                sandbox_id,
                len(self._clients[sandbox_id]),
                self.total_connections,
            )

            # 超出总容量上限，LRU 淘汰
            self._evict_if_needed()

            return new_client

    async def close_all(self) -> None:
        """关闭池中所有连接"""
        async with self._lock:
            all_entries: list[_ConnEntry] = []
            for entries in self._clients.values():
                all_entries.extend(entries)
            self._clients.clear()
            self._key_locks.clear()

        if all_entries:
            await asyncio.gather(
                *(self._safe_close(e.client) for e in all_entries),
                return_exceptions=True,
            )
            logger.info(
                "[ChatClientPool] Closed all connections: count=%d",
                len(all_entries),
            )

    # ── 内部实现 ──────────────────────────────────────────────────────────

    @staticmethod
    def _read_a2a_mode(
        system_config_service: SystemConfigManageService | None,
    ) -> str:
        """从 system_config 读取 A2A 灰度模式（1b 迁移开关）。

        与 _read_ignore_case 同构：config 服务缺席/异常/未配置都回退
        "off"（生产零变化）；值合法域 off|grey|on，非法回退 off 并告警。
        """
        if system_config_service is None:
            return "off"
        try:
            resp = system_config_service.get_config(SystemConfigKey.A2A_CLIENT_MODE)
        except Exception:
            logger.warning(
                "failed to read a2a_client_mode config, defaulting to off",
                exc_info=True,
            )
            return "off"
        value = (resp.conf_value or "").strip().lower() if resp else ""
        if value not in ("off", "grey", "on"):
            if value:
                logger.warning("invalid a2a_client_mode %r, defaulting to off", value)
            return "off"
        return value

    @staticmethod
    def _read_ignore_case(
        system_config_service: SystemConfigManageService | None,
    ) -> bool:
        """从 system_config 读取 sessionKey 匹配是否忽略大小写。"""
        if system_config_service is None:
            return False
        try:
            resp = system_config_service.get_config(
                SystemConfigKey.SESSION_KEY_IGNORE_CASE
            )
        except Exception:
            logger.warning(
                "failed to read session_key_ignore_case config, defaulting to false",
                exc_info=True,
            )
            return False
        if resp is None:
            return False
        return (resp.conf_value or "").strip().lower() == "true"

    def _load_sensitive_bypass_keys(
        self,
        system_config_service: SystemConfigManageService | None,
    ) -> tuple[str, ...]:
        """读取敏感日志过滤豁免的 session_key 片段（TTL 缓存）。

        配置 ``bot_run.sensitive_bypass_session_keys``：逗号/换行
        分隔的片段列表；_log_event 的 sessionKey 包含任一片段时，
        兜底事件日志不做敏感字段过滤（全量打印 payload）。

        结果（含空列表与读取失败）缓存
        ``_BYPASS_KEYS_TTL_SECONDS`` 秒，过期后重新查询 DB
        （配置变更无需重启，分钟级生效）；
        未配置或读取失败期间返回空元组，默认仍做过滤。
        """
        now = time.monotonic()
        loaded_at, cached = self._bypass_keys_cache
        if now - loaded_at < _BYPASS_KEYS_TTL_SECONDS:
            return cached
        if system_config_service is None:
            return ()
        try:
            resp = system_config_service.get_config(
                SystemConfigKey.SENSITIVE_BYPASS_SESSION_KEYS
            )
            raw = (resp.conf_value or "") if resp is not None else ""
            keys = tuple(
                part.strip()
                for part in raw.replace("\n", ",").split(",")
                if part.strip()
            )
        except Exception:
            logger.warning(
                "failed to read sensitive_bypass_session_keys, "
                "sensitive log filtering stays enabled",
                exc_info=True,
            )
            keys = ()
        # 空结果/读取失败同样缓存，避免未配置时每条日志事件查 DB
        self._bypass_keys_cache = (now, keys)
        return keys

    @staticmethod
    def _enable_process_interaction(
        system_config_service: SystemConfigManageService | None,
    ) -> bool:
        """读取新连接是否启用 interaction 事件处理，缺省为关闭。"""
        if system_config_service is None:
            return False
        try:
            resp = system_config_service.get_config(SystemConfigKey.INTERACTION_PROCESS)
        except Exception:
            logger.warning(
                "failed to read interaction_process config, defaulting to false",
                exc_info=True,
            )
            return False
        if resp is None:
            return False
        return (resp.conf_value or "").strip().lower() == "true"

    def _sweep_mode_stale(self, sandbox_id: str, current_mode: str) -> None:
        """P1-3：收口与当前灰度模式不一致的存量连接。

        空闲的移出池并异步关闭（立即释放，不承接新请求）；在途的保留在池
        中（不误杀正在跑的请求），由 _pick_* 按模式跳过，排空后的下一次
        get() 会在本方法将其关闭。全同步段（无 await），无并发窗口。
        """
        entries = self._clients.get(sandbox_id)
        if not entries:
            return

        kept: list[_ConnEntry] = []
        stale_idle: list[_ConnEntry] = []
        for entry in entries:
            if entry.a2a_mode != current_mode and not entry.client.has_active_sessions:
                stale_idle.append(entry)
                continue
            kept.append(entry)

        if not stale_idle:
            return
        if kept:
            self._clients[sandbox_id] = kept
        else:
            del self._clients[sandbox_id]
        for entry in stale_idle:
            logger.info(
                "[ChatClientPool] Closing stale a2a_mode=%s connection "
                "(current=%s): sandbox=%s",
                entry.a2a_mode,
                current_mode,
                sandbox_id,
            )
            asyncio.ensure_future(self._safe_close(entry.client))

    @staticmethod
    def _refresh_outbound_auth(
        client: AsyncChatClient | None, headers: dict[str, str] | None
    ) -> None:
        """复用路径转发最新 proxypass token（P0：120s JWT 过期正修）。

        调用方每条消息现签 headers；连接构造时的 token 快照存活期远
        短于池连接寿命，而 A2A HTTP 腿逐请求鉴权——复用不刷即过期
        401（2026-10-29 预发 62 例风暴根因）。duck-typed：v3 WS 无此
        转发方法（握手后无请求级鉴权）；空/缺失 token（Local 平台）
        跳过；刷新失败只告警，不得殃及消息分派。
        """
        if client is None or not headers:
            return
        if not headers.get("x-proxypass-token"):
            return
        refresh = getattr(client, "refresh_outbound_auth", None)
        if refresh is None:
            return
        try:
            refresh(headers)
        except Exception:
            logger.exception("[ChatClientPool] outbound auth refresh failed")

    def _find_session_owner(
        self, sandbox_id: str, session_key: str
    ) -> AsyncChatClient | None:
        """按 session 找持有它的连接（R4-1）。

        扫描面 = 该 key 的全部连接：忙/闲不限（归属与负载无关）、模式
        不限（模式翻转后仍在途的旧连接继续服务它的 run，直到收口）。
        单条探测失败不传染（连接坏由既有清理路径处理）。
        """
        for entry in self._clients.get(sandbox_id, []):
            try:
                if entry.client.tracks_session(session_key):
                    return entry.client
            except Exception:
                logger.exception(
                    "[ChatClientPool] session owner probe failed: sandbox=%s",
                    sandbox_id,
                )
        return None

    def _pick_idle(
        self, sandbox_id: str, expected_mode: str | None = None
    ) -> AsyncChatClient | None:
        """从已有连接中复用空闲连接（无锁，快速路径）

        仅当存在空闲（active_session_count == 0）且健康的连接时返回。
        如果所有连接都在忙、有不健康连接、或无连接，返回 None 交给慢路径。
        不做扩容决策（扩容在锁内做），避免多协程同时触发创建。

        expected_mode 非 None 时，建连模式快照不一致的连接被跳过
        （P1-3 翻转校验）——直连该方法的旧调用方（无期望模式）不受影响。
        """
        entries = self._clients.get(sandbox_id)
        if not entries:
            return None

        for entry in entries:
            if expected_mode is not None and entry.a2a_mode != expected_mode:
                continue
            if not entry.client.is_connected:
                continue
            if entry.client.is_reconnecting:
                continue
            if entry.client.active_session_count == 0:
                entry.last_used = time.time()
                return entry.client

        return None

    def _pick_least_sessions(
        self, entries: list[_ConnEntry], expected_mode: str | None = None
    ) -> AsyncChatClient | None:
        """从给定连接列表中选择活跃会话数最少的健康连接

        重连中的连接会被跳过，不参与选择；expected_mode 非 None 时模式
        快照不一致的连接同样被跳过（P1-3 翻转校验）。
        """
        best_entry: _ConnEntry | None = None
        best_count = float("inf")

        for entry in entries:
            if expected_mode is not None and entry.a2a_mode != expected_mode:
                continue
            if not entry.client.is_connected:
                continue
            if entry.client.is_reconnecting:
                # 跳过重连中的连接
                continue
            count = entry.client.active_session_count
            if count < best_count:
                best_count = count
                best_entry = entry

        if best_entry is not None:
            best_entry.last_used = time.time()
            return best_entry.client
        return None

    def _remove_unhealthy(self, sandbox_id: str) -> None:
        """移除指定 sandbox_id 下所有不健康和重连中的连接"""
        entries = self._clients.get(sandbox_id)
        if not entries:
            return

        healthy: list[_ConnEntry] = []
        for entry in entries:
            if entry.client.is_connected and not entry.client.is_reconnecting:
                healthy.append(entry)
            else:
                asyncio.ensure_future(self._safe_close(entry.client))

        if healthy:
            self._clients[sandbox_id] = healthy
        else:
            del self._clients[sandbox_id]

    async def _get_key_lock(self, sandbox_id: str) -> asyncio.Lock:
        """获取或创建 per-key lock（全局锁保护，极快）"""
        lock = self._key_locks.get(sandbox_id)
        if lock is not None:
            return lock

        async with self._lock:
            # double-check
            if sandbox_id not in self._key_locks:
                self._key_locks[sandbox_id] = asyncio.Lock()
            return self._key_locks[sandbox_id]

    def _evict_if_needed(self) -> None:
        """淘汰最久未使用且无活跃会话的单条连接（细粒度）"""
        while self.total_connections > self._max_size:
            victim_key: str | None = None
            victim_idx: int = -1
            oldest_time = float("inf")

            for sid, entries in self._clients.items():
                for idx, entry in enumerate(entries):
                    if (
                        not entry.client.has_active_sessions
                        and entry.last_used < oldest_time
                    ):
                        oldest_time = entry.last_used
                        victim_key = sid
                        victim_idx = idx

            if victim_key is None:
                logger.warning(
                    "[ChatClientPool] Pool over capacity (%d/%d) "
                    "but all connections have active sessions",
                    self.total_connections,
                    self._max_size,
                )
                break

            # 移除该条连接
            entries = self._clients[victim_key]
            evicted = entries.pop(victim_idx)
            if not entries:
                del self._clients[victim_key]
            logger.debug(
                "[ChatClientPool] Evicting LRU connection: sandbox=%s",
                victim_key,
            )
            asyncio.ensure_future(self._safe_close(evicted.client))

    @staticmethod
    async def _safe_close(client: AsyncChatClient) -> None:
        """安全关闭连接，忽略异常"""
        try:
            await client.close()
        except Exception:
            pass
