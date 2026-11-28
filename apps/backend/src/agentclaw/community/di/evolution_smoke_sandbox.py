"""Explicit acceptance-only real-sandbox composition; never globally enabled."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import socket

import uvicorn

from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.core.evolution_run import ContainerHost
from avernet_evolution.core.evolution_run.sandbox import JobWorkerSandbox


@dataclass(frozen=True)
class SmokeSandbox:
    sandbox: JobWorkerSandbox
    image: str
    bind_ip: str
    command: str


async def sandbox_worker(*, channel, issuer, config: SmokeSandbox, workdir: Path,
                         timeout_s: float) -> int:
    with socket.socket() as sock:
        sock.bind((config.bind_ip, 0))
        sock.listen(16)
        endpoint = f"http://{config.bind_ip}:{sock.getsockname()[1]}"
        host = ContainerHost(
            sandbox=config.sandbox, store=channel.store, jobs=channel.jobs, runs=None,
            image_of=lambda _: config.image, job_endpoint=endpoint, tokens=issuer,
            strategy_refs=("acme/identity-hardening@1.0.0",), command=lambda _: config.command,
        )
        server = uvicorn.Server(uvicorn.Config(build_job_app(channel),
                                               log_level="error", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(timeout_s):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("job server stopped before startup")
                    await asyncio.sleep(0.01)
                if not await host.run_once("acceptance-controller"):
                    raise RuntimeError("no queued acceptance job")
                return 0 if host.last_error is None else 1
        finally:
            server.should_exit = True
            await task
            # Optional diagnostic metadata, not part of the sandbox port contract.
            evidence = getattr(config.sandbox, "evidence", {})
            (workdir / "sandbox-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
