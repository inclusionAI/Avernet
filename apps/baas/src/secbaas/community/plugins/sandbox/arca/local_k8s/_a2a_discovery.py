"""A2A NodePort 发现的应用级共享状态（review P2：跨请求生命周期）。

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

本组件只放共享状态与原子原语；查询、TTL 出清等决策逻辑仍在插件
（``_sandbox_plugin``）。状态不持有 credentials、不持有 K8s 客户端。
"""

from __future__ import annotations

import threading
import time

# (namespace, service_name)
DiscoveryKey = tuple[str, str]

# 负缓存短 TTL：TTL 内同 key 一律空地址、零新查询；过期后可再探。
# 值本身没有取回意义（出错/容量耗尽是待恢复状态，不是可用地址），
# 只有 TTL 到点与否有意义。
_A2A_URL_DISCOVERY_NEGATIVE_TTL_S = 30.0

# 后台发现线程的进程级并发上限：容量耗尽立即降级（空地址 + 短负缓存），
# 绝不在 WS 签发链上等待/排队——可选面的线程资源永远让位主链。
_A2A_URL_DISCOVERY_MAX_CONCURRENCY = 8

# acquire() 的三种结局（去重与额度获取必须是一个原子区，否则多实例并发
# 签发仍可凭「各自看都还有额度」突破上限）：
_ACQUIRED = "acquired"  # 本调用获得调度权（已占 in-flight 额度）
_ALREADY_IN_FLIGHT = "in-flight"  # 同 key 发现在途，坐等结果，不写负缓存
_AT_CAPACITY = "capacity"  # 并发额度耗尽：立即降级（空地址 + 短负缓存）


class A2aUrlDiscoveryState:
    """A2A NodePort 发现的共享缓存 / 负缓存 / in-flight / 并发额度。

    由组合入口以 Singleton 注入 local_k8s 工厂（``_plugin_core``），
    生命周期为整个应用进程。插件以属性引用共享同一批 dict/set 对象，
    插件侧逻辑（记忆化读写、create 响应预热、发现线程收尾）无需感知
    归属变化；只有调度入口必须走 :meth:`acquire`/:meth:`release`
    原子原语。
    """

    def __init__(
        self,
        *,
        negative_ttl_s: float = _A2A_URL_DISCOVERY_NEGATIVE_TTL_S,
        max_concurrency: int = _A2A_URL_DISCOVERY_MAX_CONCURRENCY,
    ) -> None:
        self._negative_ttl_s = negative_ttl_s
        self._max_concurrency = max_concurrency
        # (namespace, service_name) -> node_port | None | ("error", monotonic_ts)
        self.node_port_cache: dict[DiscoveryKey, int | None | tuple[str, float]] = {}
        self.inflight: set[DiscoveryKey] = set()
        self.threads: dict[DiscoveryKey, threading.Thread] = {}
        # 单锁同守去重与额度：检查 + 占位必须一体完成
        self._lock = threading.Lock()

    def acquire(self, cache_key: DiscoveryKey) -> str:
        """原子地完成「同 key 去重 + 并发额度获取」。

        返回 :data:`_ACQUIRED` / :data:`_ALREADY_IN_FLIGHT` /
        :data:`_AT_CAPACITY`；只有 ``_ACQUIRED`` 占用了 in-flight
        额度，调用方获得后必须以 :meth:`release` 归还（无论成败）。
        """
        with self._lock:
            if cache_key in self.inflight:
                return _ALREADY_IN_FLIGHT
            if len(self.inflight) >= self._max_concurrency:
                return _AT_CAPACITY
            self.inflight.add(cache_key)
            return _ACQUIRED

    def release(self, cache_key: DiscoveryKey) -> None:
        with self._lock:
            self.inflight.discard(cache_key)

    def finish(self, cache_key: DiscoveryKey, thread: threading.Thread) -> None:
        """发现任务的**自然收尾**：额度释放与线程记录清理同锁原子完成。

        线程记录不能依赖诊断 seam（a2a_discovery_join 只在没有它时被调
        用的测试/诊断面）清出——任务结束后应用级状态必须自行收敛。按
        线程对象身份删：本 key 已被换上新任务或被 invalidate 先行移除
        时都不误删他人记录。"""
        with self._lock:
            self.inflight.discard(cache_key)
            if self.threads.get(cache_key) is thread:
                del self.threads[cache_key]

    def commit_result(self, cache_key: DiscoveryKey, node_port: int | None) -> bool:
        """发现结果落账：仅当本 key 仍在 in-flight 时写入。

        invalidate（Service 销毁）会同步摘除 in-flight——销毁窗口内迟到
        的发现回写在此被丢弃，已销毁设备的历史缓存条目不复活。返回是否
        落账（False = 设备已被销毁，结果放弃）。"""
        with self._lock:
            if cache_key not in self.inflight:
                return False
            self.node_port_cache[cache_key] = node_port
            return True

    def commit_negative(self, cache_key: DiscoveryKey) -> None:
        """发现失败的负缓存落账：与 :meth:`commit_result` 同门控（仅当
        本 key 仍在 in-flight），迟到负结果同样不得复活历史。"""
        self._commit_entry(
            cache_key, ("error", time.monotonic() + self._negative_ttl_s)
        )

    def _commit_entry(
        self, cache_key: DiscoveryKey, entry: int | None | tuple[str, float]
    ) -> bool:
        with self._lock:
            if cache_key not in self.inflight:
                return False
            self.node_port_cache[cache_key] = entry
            return True

    def invalidate(self, cache_key: DiscoveryKey) -> None:
        """生命周期失效（Service 销毁）：缓存与线程记录出清，同锁摘除
        in-flight 作回写门控判据。

        摘除 in-flight 会短暂放宽同 key 去重——只发生在设备销毁瞬间，
        重复发现是只读幂等查询；不摘则销毁窗口内的迟到回写无法被
        :meth:`commit_result` 拒收。在途任务自己的 :meth:`finish` 收尾
        均为幂等（discard + 身份判删），互不干扰。"""
        with self._lock:
            self.node_port_cache.pop(cache_key, None)
            self.threads.pop(cache_key, None)
            self.inflight.discard(cache_key)

    def note_negative(self, cache_key: DiscoveryKey) -> None:
        """写短 TTL 负缓存（调度失败与发现失败同形状，出清路径无感差异）。

        签发链上的调度降级发生在入队前（本 key 无在途任务），不适用
        in-flight 门控，无条件落账。"""
        self.node_port_cache[cache_key] = (
            "error",
            time.monotonic() + self._negative_ttl_s,
        )
