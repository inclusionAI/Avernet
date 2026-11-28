"""`JobWorkerSandbox` 在平台容器运行时上的实现（06 §10）。

把进化模块的中性规格翻译成 `SandboxRuntimeClient` 的调用：镜像按 digest 传入
模板覆盖，资源来自规格，环境变量只有白名单里的那几个，不挂载任何密钥卷。

运行时的 `create_sandbox` / `exec_command` / `destroy_sandbox` 是同步阻塞调用
（`exec_command` 可能等待数分钟），因此统一用 `asyncio.to_thread` 挪出事件循环，
避免拖住整个 backend 进程。

OutBoundOperationRule 并不是已验证的网络 ACL；平台端口也没有逐沙箱只读根开关。
这两项隔离必须由模板/网络策略另行保证，不能仅凭 JobWorkerSpec 宣称生效。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from avernet_evolution.core.evolution_run.sandbox import JobWorkerSpec
from agentclaw.community.kernel.device_dto import (
    CommandResult,
    OutBoundOperationRule,
    ResourceSpecification,
)
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

__all__ = ["SandboxRuntimeJobWorker"]


@dataclass
class SandboxRuntimeJobWorker:
    """把一个容器运行时客户端适配成进化模块的沙箱端口。"""

    runtime: SandboxRuntimeClient
    template_id: str
    tenant_idx: int = 1

    async def create(self, spec: JobWorkerSpec) -> str:
        # JobWorkerSpec uses MiB; platform ResourceSpecification uses GiB.
        overrides = {
            "image": spec.image,
            "resource_spec": ResourceSpecification(cpu=spec.cpu, memory=(spec.memory + 1023) // 1024),
        }
        info = await asyncio.to_thread(
            self.runtime.create_sandbox,
            template_id=self.template_id,
            ttl_minutes=spec.ttl_minutes,
            envs=spec.envs(),
            outbound_rule=OutBoundOperationRule(),
            tenant_idx=self.tenant_idx,
            overrides=overrides,
        )
        return info.sandbox_id

    async def exec(self, sandbox_id: str, command: str, *, timeout_ms: int) -> int:
        result: CommandResult = await asyncio.to_thread(
            self.runtime.exec_command,
            sandbox_id=sandbox_id,
            cmd=command,
            tenant_idx=self.tenant_idx,
            timeout_ms=timeout_ms,
        )
        return result.exit_code

    async def destroy(self, sandbox_id: str) -> None:
        destroyed = await asyncio.to_thread(
            self.runtime.destroy_sandbox,
            sandbox_id=sandbox_id,
            tenant_idx=self.tenant_idx,
        )
        if not destroyed:
            raise RuntimeError("sandbox runtime did not confirm destruction")
