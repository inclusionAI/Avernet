"""A2A NodePort 发现的应用级共享状态（review P2：跨请求生命周期 + 回收）。

真实签发链路里插件实例是请求级的：``PaasServiceFactory.create`` 每次
WS 签发都经 ``local_k8s_plugin_factory`` 的 ``_build`` new 一个新的
``LocalK8sArcaSandboxPlugin``——credentials 逐请求注入，插件本身不该
也不能做成全局单例。「NodePort 记忆化 + 负缓存 + in-flight 去重 + 并发
额度」因此沉淀在本组件，由组合入口（bootstrap wiring）以 Singleton 注入
工厂、烘焙进每个实例，否则：

1. 后台发现结果活不过一个请求——「首次为空，后续自然带上 A2A 地址」
   在真实调用链中不成立；
2. 去重与并发上限随实例增殖失效——多个请求各自认为还有额度，仍可
   同时轰炸 kube API。

应用级持有后，历史必须受控（review P2 回收）：

- 任务带**代际 token**：acquire 发放，commit/finish 全程校验。旧任务的
  迟到回写拒收、旧任务的收尾只能归还自己的额度/清理自己的线程记录，
  绝不动同 key 新一代任务的任何东西；
- 本进程 ``destroy()`` 是**及时**失效锚点而非唯一保障：正缓存带有限
  有效期，过期后签发面不再交付未经验证的旧值并转后台再发现——跨
  worker/带外删除造成的陈旧至多有界，不会永久驻留；
- acquire 时顺带清扫全表过期条目，不再被访问的历史也不积累。

本组件只放共享状态与原子原语；查询、TTL 判定等决策逻辑仍在插件
（``_sandbox_plugin``）。状态不持有 credentials、不持有 K8s 客户端。
"""

from __future__ import annotations

import threading
import time

# (namespace, service_name)
DiscoveryKey = tuple[str, str]
# 任务代际标识：acquire 时按 key 递增发放，任务全程持有，
# commit/finish 凭它识别「自己那一届」
TaskToken = int

# 负缓存短 TTL：TTL 内同 key 一律空地址、零新查询；过期后可再探。
# 值本身没有取回意义（出错/容量耗尽是待恢复状态，不是可用地址），
# 只有 TTL 到点与否有意义。
_A2A_URL_DISCOVERY_NEGATIVE_TTL_S = 30.0

# 正缓存（成功/None=已验证事实）有限有效期：NodePort 对 Service 生命
# 周期内不变，但对本进程只是一份**不可自证的快照**——正常业务删除可能
# 落在别的 worker（DI Singleton 仅进程内共享），带外删除更拦不到。过期
# 后签发面不再直接交付旧值，转后台发现再验证；有界陈旧窗口取代永久陈旧
# （review P2 跨 worker）。
_A2A_URL_DISCOVERY_POSITIVE_TTL_S = 300.0

# 后台发现线程的进程级并发上限：容量耗尽立即降级（空地址 + 短负缓存），
# 绝不在 WS 签发链上等待/排队——可选面的线程资源永远让位主链。
_A2A_URL_DISCOVERY_MAX_CONCURRENCY = 8

# acquire() 的三种结局（去重与额度获取必须是一个原子区，否则多实例并发
# 签发仍可凭「各自看都还有额度」突破上限）：
_ACQUIRED = "acquired"  # 本调用获得调度权（已占 in-flight 额度 + token）
_ALREADY_IN_FLIGHT = "in-flight"  # 同 key 发现在途，坐等结果，不写负缓存
_AT_CAPACITY = "capacity"  # 并发额度耗尽：立即降级（空地址 + 短负缓存）


class A2aUrlDiscoveryState:
    """A2A NodePort 发现的共享缓存 / 负缓存 / in-flight / 并发额度。

    由组合入口以 Singleton 注入 local_k8s 工厂（``_plugin_core``），
    生命周期为整个应用进程。插件以属性引用共享同一批 dict/set 对象，
    插件侧逻辑（记忆化读写、create 响应预热）无需感知归属变化；调度、
    回写、收尾必须凭 :meth:`acquire` 发放的代际 token 走原子原语。
    """

    def __init__(
        self,
        *,
        negative_ttl_s: float = _A2A_URL_DISCOVERY_NEGATIVE_TTL_S,
        positive_ttl_s: float = _A2A_URL_DISCOVERY_POSITIVE_TTL_S,
        max_concurrency: int = _A2A_URL_DISCOVERY_MAX_CONCURRENCY,
    ) -> None:
        self._negative_ttl_s = negative_ttl_s
        self._positive_ttl_s = positive_ttl_s
        self._max_concurrency = max_concurrency
        # (namespace, service_name) -> node_port | None | ("error", monotonic_ts)
        self.node_port_cache: dict[DiscoveryKey, int | None | tuple[str, float]] = {}
        # key -> 持有额度的任务的代际 token（额度归属，非裸 key 集合）
        self.inflight: dict[DiscoveryKey, TaskToken] = {}
        # key -> 最新任务代际（invalidate 靠 bump 它撤销在途任务的回写资格）
        self._generations: dict[DiscoveryKey, TaskToken] = {}
        # key -> 正缓存(int/None)过期时刻；缺失记录按已过期处理
        # （未经验证的事实不得永久交付）
        self._expires_at: dict[DiscoveryKey, float] = {}
        self.threads: dict[DiscoveryKey, threading.Thread] = {}
        # 单锁同守去重、额度、回写与收尾：跨任务的检查 + 占位/删除必须
        # 一体完成
        self._lock = threading.Lock()

    def acquire(self, cache_key: DiscoveryKey) -> tuple[str, TaskToken | None]:
        """原子地完成「同 key 去重 + 并发额度获取」并发放任务代际 token。

        返回 ``(_ACQUIRED, token)`` / ``(_ALREADY_IN_FLIGHT, None)`` /
        ``(_AT_CAPACITY, None)``。获得 token 的调用方必须在任务真正结束
        或确认启动失败时以 :meth:`finish` 归还——中途任何事件（含
        :meth:`invalidate`）都不会替它释放额度。
        """
        with self._lock:
            self._purge_expired_locked()
            if cache_key in self.inflight:
                return _ALREADY_IN_FLIGHT, None
            if len(self.inflight) >= self._max_concurrency:
                return _AT_CAPACITY, None
            current = self._generations.get(cache_key, 0)
            token = current + 1
            self._generations[cache_key] = token
            self.inflight[cache_key] = token
            return _ACQUIRED, token

    def finish(
        self,
        cache_key: DiscoveryKey,
        token: TaskToken,
        thread: threading.Thread | None = None,
    ) -> None:
        """发现任务的**自然收尾**：只能归还**自己代际**的额度、清理**自己
        线程对象**的记录（review P2：额度只在任务真正结束或确认启动失败时
        释放；旧任务的收尾绝不能清掉同 key 新一代任务的在途标记或线程
        记录）。被 invalidate 撤销回写资格的任务照常经此归还额度。
        """
        with self._lock:
            if self.inflight.get(cache_key) == token:
                del self.inflight[cache_key]
            if thread is not None and self.threads.get(cache_key) is thread:
                del self.threads[cache_key]

    def commit_result(
        self, cache_key: DiscoveryKey, token: TaskToken, node_port: int | None
    ) -> bool:
        """发现结果落账：需同时满足「本任务持有该 key 的在途额度」与
        「本任务未被 invalidate 撤销」（代际仍最新）。

        旧任务的迟到回写不接纳已销毁设备的复活、也不得覆盖新一代任务。
        返回是否落账（False = 已被失效/换界，结果放弃）。"""
        with self._lock:
            if self.inflight.get(cache_key) != token:
                return False
            if self._generations.get(cache_key) != token:
                return False
            # 先记过期再落条目:无锁读者(positive_expired)最坏读到「新条目
            # +未见过期表」会当过期处理,反向「条目在前」才会在窗口内被误清
            self._expires_at[cache_key] = time.monotonic() + self._positive_ttl_s
            self.node_port_cache[cache_key] = node_port
            return True

    def commit_negative(self, cache_key: DiscoveryKey, token: TaskToken) -> bool:
        """发现失败的负缓存落账：与 :meth:`commit_result` 同门控。"""
        with self._lock:
            if self.inflight.get(cache_key) != token:
                return False
            if self._generations.get(cache_key) != token:
                return False
            self.node_port_cache[cache_key] = (
                "error",
                time.monotonic() + self._negative_ttl_s,
            )
            self._expires_at.pop(cache_key, None)
            return True

    def invalidate(self, cache_key: DiscoveryKey) -> None:
        """生命周期失效（Service 销毁）：清缓存/过期表/线程记录，并 **bump
        代际撤销在途任务的回写资格**。

        **不归还仍在执行任务的额度**（review P2：提前归还会让真实发现
        并发突破进程级上限）。被撤销的任务照常跑完 kube 查询并经自己的
        :meth:`finish` 归还额度；期间同 key 的新调度按 in-flight 去重
        落空，旧 key 的新鲜事实在下一轮签发自然补上。"""
        with self._lock:
            self.node_port_cache.pop(cache_key, None)
            self._expires_at.pop(cache_key, None)
            self.threads.pop(cache_key, None)
            if cache_key in self.inflight:
                # bump 代际 = 撤销在途任务的回写资格（额度保留给任务自己
                # 跑完再还）
                self._generations[cache_key] += 1

    def note_warm(self, cache_key: DiscoveryKey, node_port: int) -> None:
        """create 响应预热：刚创建的 Service 是最新事实，无条件落正缓存
        并记有效期（不走任务门控——create 刻没有在途任务，也无从被失效）。
        先记过期再落条目，理由同 :meth:`commit_result`。"""
        self._expires_at[cache_key] = time.monotonic() + self._positive_ttl_s
        self.node_port_cache[cache_key] = node_port

    def note_negative(self, cache_key: DiscoveryKey) -> None:
        """写短 TTL 负缓存（调度失败与发现失败同形状，出清路径无感差异）。

        签发链上的调度降级发生在入队前（本 key 无在途任务），不适用
        任务门控，无条件落账。"""
        self.node_port_cache[cache_key] = (
            "error",
            time.monotonic() + self._negative_ttl_s,
        )

    def positive_expired(self, cache_key: DiscoveryKey) -> bool:
        """正缓存（int/None）是否已过有效期。缺失过期记录按已过期处理；
        error 元组不走本判据（走插件里的负缓存 TTL 路径）。"""
        entry = self.node_port_cache.get(cache_key)
        if isinstance(entry, tuple):
            return False
        expires_at = self._expires_at.get(cache_key)
        return expires_at is None or time.monotonic() >= expires_at

    def drop(self, cache_key: DiscoveryKey) -> None:
        """签发面过期处理：清除过期条目（正缓存旧值不得再被交付，
        过期负缓存代以重新探查）。"""
        self.node_port_cache.pop(cache_key, None)
        self._expires_at.pop(cache_key, None)

    def _purge_expired_locked(self) -> None:
        """清扫全表过期条目（负缓存 TTL 到点 / 正缓存有效期到点）。

        挂在 acquire 内执行：不再被访问的历史 key 也随任何一次新调度
        出清——应用级缓存大小受「最近有效期窗口内的设备」约束，不随历史
        设备总量累积（review P2 回收）。"""
        now = time.monotonic()
        for key, entry in list(self.node_port_cache.items()):
            if isinstance(entry, tuple):
                if now >= entry[1]:
                    del self.node_port_cache[key]
            elif now >= self._expires_at.get(key, 0.0):
                del self.node_port_cache[key]
                self._expires_at.pop(key, None)
