"""worker 镜像入口（06 §10）：`python -m agentclaw.community.plugins.evolution.entrypoint`。

镜像只做两件事：把策略注册表交给 worker、按环境变量连回平台。平台地址与令牌
来自沙箱注入的三个白名单变量，镜像里没有任何密钥。

策略注册表由镜像提供，经 ``EVOLUTION_WORKER_STRATEGIES`` 声明（逗号分隔的
``ref=module:attr``，例如
``acme/identity-hardening@1.0.0=agentclaw...strategies:IdentityHardening``）。
键必须是运行上冻结的 ``strategy@version``：worker 用它认领对应策略的作业，也是
``Worker.strategies`` 的查找键。
"""

from __future__ import annotations

import asyncio
import importlib
import os
from typing import Any, Callable

from agentclaw.community.core.evolution_run.sandbox import (
    ENV_JOB_ENDPOINT, ENV_JOB_TOKEN, ENV_WORKER_ID,
)
from agentclaw.community.core.evolution_run.worker import Worker

from .job_http import HttpWorkerTransport

__all__ = ["main", "main_from_env", "strategies_from_env"]

ENV_STRATEGIES = "EVOLUTION_WORKER_STRATEGIES"


def strategies_from_env(spec: str | None = None) -> dict[str, Callable[[], Any]]:
    """把 ``ref=module:attr`` 列表解析成 ``Worker.strategies`` 需要的映射。"""

    raw = (spec if spec is not None else os.environ.get(ENV_STRATEGIES, "")).strip()
    if not raw:
        raise RuntimeError(
            f"{ENV_STRATEGIES} is not set: the worker image must declare which "
            "strategies it serves, e.g. 'acme/identity-hardening@1.0.0=pkg.mod:IdentityHardening'"
        )
    strategies: dict[str, Callable[[], Any]] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        ref, _, target = item.partition("=")
        module_name, _, attr = target.partition(":")
        if not ref.strip() or not module_name.strip() or not attr.strip():
            raise RuntimeError(f"malformed strategy entry {item!r}: expected ref=module:attr")
        factory = getattr(importlib.import_module(module_name.strip()), attr.strip())
        strategies[ref.strip()] = factory
    if not strategies:
        raise RuntimeError(f"{ENV_STRATEGIES} declares no strategies")
    return strategies


def main(strategies: dict[str, Callable[[], Any]]) -> int:
    """跑一个 worker 直到队列排空；策略注册表由镜像提供。"""

    endpoint = os.environ[ENV_JOB_ENDPOINT]
    worker_id = os.environ[ENV_WORKER_ID]
    token = os.environ[ENV_JOB_TOKEN]

    async def _run() -> int:
        async with HttpWorkerTransport(endpoint) as transport:
            worker = Worker(transport=transport, strategies=strategies,
                            worker_id=worker_id)
            await worker.run_pending(token)
            return 0 if worker.last_error is None else 1

    return asyncio.run(_run())


def main_from_env() -> int:
    """镜像的 ``__main__`` 入口：策略表来自 ``EVOLUTION_WORKER_STRATEGIES``。"""

    return main(strategies_from_env())


if __name__ == "__main__":  # pragma: no cover - 容器内进程入口
    raise SystemExit(main_from_env())
