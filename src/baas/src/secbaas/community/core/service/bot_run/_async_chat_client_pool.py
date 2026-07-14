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
from dataclasses import dataclass, field

from secbaas.community.api.config_manage import SystemConfigManageService
from secbaas.community.core.service.config._constants import SystemConfigKey
from secbaas.community.logger import get_logger

from ._async_chat_client import AsyncChatClient

logger = get_logger("core-bot-run")


@dataclass
class _ConnEntry:
    """单条连接的元数据"""

    client: AsyncChatClient
    last_used: float = field(default_factory=time.time)


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
    ) -> None:
        """初始化连接池

        Args:
            max_size: 池中最大总连接数，超出时 LRU 淘汰
            max_conns_per_sandbox: 每个 sandbox_id 最多维护的连接数
            max_concurrent_per_conn: 每条连接最大并发会话数，0 表示不限
            session_key_timeout: 同一 sessionKey 并发等待超时（秒）
            max_retries: WS 断连后自动重连次数，0 表示不重试
            retry_base_backoff: 重连退避基数（秒）
            system_config_service: 系统配置服务，传递给 AsyncChatClient 用于读取开关
        """
        self._max_size = max_size
        self._max_conns_per_sandbox = max_conns_per_sandbox
        self._max_concurrent_per_conn = max_concurrent_per_conn
        self._session_key_timeout = session_key_timeout
        self._max_retries = max_retries
        self._retry_base_backoff = retry_base_backoff
        self._ignore_case = self._read_ignore_case(system_config_service)
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
    ) -> AsyncChatClient:
        """获取该 sandbox_id 对应的连接（Least-Sessions 选择，On-demand 扩容）

        Args:
            sandbox_id: 沙箱标识，作为连接池的 key
            uri: WebSocket URI
            headers: WebSocket 请求头

        Returns:
            可用的 AsyncChatClient 实例
        """
        # 快速路径（无锁）：仅尝试复用空闲连接，不触发扩容决策。
        # 扩容决策必须在锁内做，否则多个协程会同时看到"需要扩容"
        # 并各自创建连接。
        client = self._pick_idle(sandbox_id)
        if client is not None:
            logger.info(
                "[ChatClientPool] Fast path reused idle connection: "
                "sandbox=%s, active_sessions=%d",
                sandbox_id,
                client.active_session_count,
            )
            return client

        logger.info(
            "[ChatClientPool] Fast path miss, entering slow path: sandbox=%s",
            sandbox_id,
        )

        # 获取 per-key lock
        key_lock = await self._get_key_lock(sandbox_id)

        # 慢路径：per-key lock 内完成所有决策（复用/扩容/创建）
        async with key_lock:
            # 优先复用空闲连接
            client = self._pick_idle(sandbox_id)
            if client is not None:
                logger.info(
                    "[ChatClientPool] Slow path reused idle connection "
                    "after lock: sandbox=%s, active_sessions=%d",
                    sandbox_id,
                    client.active_session_count,
                )
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
                best = self._pick_least_sessions(entries)
                if best is not None:
                    return best
                # 所有连接都不健康且已被清理，继续创建新的

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
                ignore_case=self._ignore_case,
            )
            await new_client.connect()

            entry = _ConnEntry(client=new_client)
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

    def _pick_idle(self, sandbox_id: str) -> AsyncChatClient | None:
        """从已有连接中复用空闲连接（无锁，快速路径）

        仅当存在空闲（active_session_count == 0）且健康的连接时返回。
        如果所有连接都在忙、有不健康连接、或无连接，返回 None 交给慢路径。
        不做扩容决策（扩容在锁内做），避免多协程同时触发创建。
        """
        entries = self._clients.get(sandbox_id)
        if not entries:
            return None

        for entry in entries:
            if not entry.client.is_connected:
                continue
            if entry.client.is_reconnecting:
                continue
            if entry.client.active_session_count == 0:
                entry.last_used = time.time()
                return entry.client

        return None

    def _pick_least_sessions(self, entries: list[_ConnEntry]) -> AsyncChatClient | None:
        """从给定连接列表中选择活跃会话数最少的健康连接

        重连中的连接会被跳过，不参与选择。
        """
        best_entry: _ConnEntry | None = None
        best_count = float("inf")

        for entry in entries:
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
