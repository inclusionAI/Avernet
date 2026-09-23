"""Runtime endpoint registry — 从"烤死的 URL"到"按设备身份现场解析"。

Runtime 进程重启会重新分配端口（openclaw 的 ``gateway.port`` 写在
spawn 时的 ``openclaw.json`` 里，变更必然全进程重启）。任何把当时的
URL 长期持有的一方（存活 runtime 的 env、binding 记录、配置快照）都会
在重启后拿着失效地址——runtime 生命周期与绑定迁移的缺口即由此产生。

长期修法：消费方（transport / 连接解析）按设备身份（device_id）在
调用时从本注册表解析当前地址；生产方（``LocalProcessManager``）在
spawn 成功 / stop 时同步登记 / 摘除。重启换端口 = 同 device 重新
register，下一次解析立即拿到新值（热加载），不存在"迁移"环节。

约定：
* 注册表是进程内的活动视图，不是持久层；backend 重启后由各个
  runtime 的正常 spawn 流程重新填充。
* 未登记的设备 resolve 返回 ``None`` —— 绝不回落到任何全局默认
  地址（那是 #2435 终审确认的 binding-routed 跨 bot 串数据路径）。

Singlebox/local 本地 runtime 专用；生产远端 BaaS/Arca 的地址仍走
binding 携带的显式 ``url``/``target``，不经本注册表。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass(frozen=True)
class RuntimeEndpoints:
    """A device's current runtime addresses (normalized URLs)."""

    adapter_url: str


class RuntimeEndpointRegistry:
    """device_id → 当前 runtime 地址的进程内注册表（热更新）。"""

    _instance: "RuntimeEndpointRegistry | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._endpoints: dict[str, RuntimeEndpoints] = {}
        self._lock = threading.Lock()

    @classmethod
    def instance(cls) -> "RuntimeEndpointRegistry":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """测试隔离用：丢弃全局实例。"""
        with cls._instance_lock:
            cls._instance = None

    def register(self, device_id: str, *, adapter_url: str) -> RuntimeEndpoints:
        """登记/覆盖一个设备的当前 adapter 地址（spawn 成功后调用）。

        同 device 重复 register 即"重启换端口"的迁移语义：后写覆盖
        先写，不做任何旧值继承。
        """
        normalized = adapter_url.rstrip("/")
        endpoints = RuntimeEndpoints(adapter_url=normalized)
        with self._lock:
            previous = self._endpoints.get(device_id)
            self._endpoints[device_id] = endpoints
        if previous is not None and previous.adapter_url != normalized:
            logger.info(
                "[RuntimeEndpointRegistry] %s adapter moved %s -> %s "
                "(runtime respawn)",
                device_id,
                previous.adapter_url,
                normalized,
            )
        return endpoints

    def unregister(self, device_id: str) -> bool:
        """摘除一个设备的地址（stop 时调用）；未登记返回 False。"""
        with self._lock:
            removed = self._endpoints.pop(device_id, None)
        return removed is not None

    def resolve(self, device_id: str) -> RuntimeEndpoints | None:
        """按设备身份现场解析；未登记返回 None（绝不猜全局默认）。"""
        with self._lock:
            return self._endpoints.get(device_id)