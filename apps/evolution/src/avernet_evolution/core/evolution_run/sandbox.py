"""策略执行沙箱（06-evolution-run.zh-CN §10）。

策略是处理不可信数据的不可信代码，因此它只在一次性容器里运行：镜像按 digest
固定、环境变量走白名单、出网只到作业协议端点、根文件系统只读（临时目录除外）、
资源受规格限制。这些是规格要求；各适配器须声明实际执行机制与未验证项。
宿主结束时尝试销毁；销毁失败必须报错，不能承诺外部 API 失效时仍绝无残留。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "ALLOWED_ENV_KEYS",
    "DEFAULT_TTL_MINUTES",
    "ENV_JOB_ENDPOINT",
    "ENV_JOB_TOKEN",
    "ENV_WORKER_ID",
    "JobWorkerSandbox",
    "JobWorkerSpec",
    "SandboxSpecError",
    "job_worker_spec",
]

ENV_JOB_ENDPOINT = "EVOLUTION_JOB_ENDPOINT"
# Public constant names the env key (not a credential); keep the literal in a
# private constant so the value is a reference, not an inline secret-shaped string.
_ENV_JOB_TOKEN_KEY = "EVOLUTION_JOB_TOKEN"
ENV_JOB_TOKEN = _ENV_JOB_TOKEN_KEY
ENV_WORKER_ID = "EVOLUTION_WORKER_ID"

#: 唯一允许进入 worker 的环境变量；模型密钥、云凭据、宿主 env 一律不注入。
ALLOWED_ENV_KEYS: frozenset[str] = frozenset(
    {
        ENV_JOB_ENDPOINT,
        ENV_JOB_TOKEN,
        ENV_WORKER_ID,
    }
)

DEFAULT_CPU = 2
DEFAULT_MEMORY_MB = 4096
DEFAULT_TTL_MINUTES = 90


class SandboxSpecError(ValueError):
    """沙箱规格不合法（镜像未钉 digest、端点不是 http(s)、上限非正数）。"""


@dataclass(frozen=True)
class JobWorkerSpec:
    """一个作业 worker 沙箱的完整规格；创建它就是隔离策略的全部输入。"""

    worker_id: str
    image: str
    job_endpoint: str
    job_token: str
    ttl_minutes: int = DEFAULT_TTL_MINUTES
    cpu: int = DEFAULT_CPU
    memory: int = DEFAULT_MEMORY_MB
    rootfs_readonly: bool = True
    egress: tuple[str, ...] = ()

    def envs(self) -> dict[str, str]:
        """worker 的环境：白名单内的三个变量，其余什么都不给。"""

        return {
            ENV_JOB_ENDPOINT: self.job_endpoint,
            ENV_JOB_TOKEN: self.job_token,
            ENV_WORKER_ID: self.worker_id,
        }


def job_worker_spec(
    *,
    worker_id: str,
    image: str,
    job_endpoint: str,
    job_token: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    cpu: int = DEFAULT_CPU,
    memory: int = DEFAULT_MEMORY_MB,
) -> JobWorkerSpec:
    """构造沙箱规格；镜像必须按 digest 固定，端点必须是 http(s)。"""

    if "@sha256:" not in image:
        raise SandboxSpecError(f"worker image {image!r} must be pinned by digest (…@sha256:…)")
    if not job_endpoint.startswith(("http://", "https://")):
        raise SandboxSpecError(f"job endpoint {job_endpoint!r} must be an http(s) URL")
    if cpu <= 0 or memory <= 0 or ttl_minutes <= 0:
        raise SandboxSpecError("cpu, memory and ttl_minutes must be positive")
    return JobWorkerSpec(
        worker_id=worker_id,
        image=image,
        job_endpoint=job_endpoint,
        job_token=job_token,
        ttl_minutes=ttl_minutes,
        cpu=cpu,
        memory=memory,
        rootfs_readonly=True,
        egress=(job_endpoint,),
    )


class JobWorkerSandbox(Protocol):
    """容器运行时端口：只做创建、执行、销毁三件事。

    执行命令是与容器运行时的 I/O（可能持续数分钟），不允许阻塞
    事件循环；三个方法都是协程。
    """

    async def create(self, spec: JobWorkerSpec) -> str:
        """创建沙箱，返回运行时的 sandbox id。

        一次性固定入口适配器可在创建时启动 Worker；exec 必须校验同一入口并
        等待其退出。调用者应先准备好 Job 端点，不能假定 exec 前 Worker 未运行。
        """

    async def exec(self, sandbox_id: str, command: str, *, timeout_ms: int) -> int:
        """执行命令或等待创建时启动的固定入口，异步等待并返回该命令的退出码。"""

    async def destroy(self, sandbox_id: str) -> None:
        """幂等销毁并确认结束；未确认销毁必须抛错，宿主不能视为清理成功。"""
