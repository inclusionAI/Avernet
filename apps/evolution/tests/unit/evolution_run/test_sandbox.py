"""容器执行沙箱（06 §10）：规格、白名单、生命周期与超时兜底。"""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_run import (
    Binding,
    Budget,
    ContainerHost,
    JobTokenIssuer,
    ManualTrigger,
    RunStatus,
    SandboxSpecError,
    TriggerRecord,
    job_worker_spec,
)
from avernet_evolution.core.evolution_run.sandbox import (
    ALLOWED_ENV_KEYS,
    ENV_JOB_ENDPOINT,
    ENV_JOB_TOKEN,
    ENV_WORKER_ID,
)

from .conftest import BOT

IMAGE = "registry.example.com/evolution/worker@sha256:" + "a" * 64
ENDPOINT = "http://platform.internal:8000/openapi/v1/evolution/v1/jobs"
REF = "acme/identity-hardening@1.0.0"


class FakeSandbox:
    def __init__(self) -> None:
        self.created: list = []
        self.executed: list[tuple[str, str, int]] = []
        self.destroyed: list[str] = []
        self.on_exec = None
        self.create_error: Exception | None = None

    async def create(self, spec):
        self.created.append(spec)
        if self.create_error is not None:
            raise self.create_error
        return f"sbx_{len(self.created)}"

    async def exec(self, sandbox_id: str, command: str, *, timeout_ms: int) -> int:
        self.executed.append((sandbox_id, command, timeout_ms))
        if self.on_exec is not None:
            await self.on_exec()
        return 0

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)


def test_spec_requires_a_digest_pinned_image() -> None:
    with pytest.raises(SandboxSpecError):
        job_worker_spec(worker_id="w", image="worker:latest", job_endpoint=ENDPOINT, job_token="t")


def test_spec_requires_an_http_endpoint() -> None:
    with pytest.raises(SandboxSpecError):
        job_worker_spec(
            worker_id="w",
            image=IMAGE,
            job_endpoint="unix:///run/jobs.sock",
            job_token="t",
        )


def test_spec_envs_are_the_allowlist_only() -> None:
    spec = job_worker_spec(worker_id="w1", image=IMAGE, job_endpoint=ENDPOINT, job_token="tok")
    assert set(spec.envs()) == ALLOWED_ENV_KEYS
    assert spec.envs()[ENV_JOB_ENDPOINT] == ENDPOINT
    assert spec.envs()[ENV_JOB_TOKEN] == "tok"
    assert spec.envs()[ENV_WORKER_ID] == "w1"
    assert spec.egress == (ENDPOINT,)
    assert spec.rootfs_readonly is True


BINDING = Binding(
    id="bind_01",
    strategy=REF,
    trigger=ManualTrigger(),
    parent="active",
    allowed_genes=["persona"],
    verification_profile="default@1",
    budget=Budget(max_usd=5.0, max_wall_clock_s=1800),
    params={},
)


async def _queued_run(services):
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    run_id = await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    return await services.runs.get(BOT, run_id)


def _host(services, sandbox: FakeSandbox, **overrides) -> ContainerHost:
    values = {
        "sandbox": sandbox,
        "store": services.store,
        "jobs": services.jobs,
        "runs": services.runs,
        "image_of": lambda _run: IMAGE,
        "job_endpoint": ENDPOINT,
        "strategy_refs": (REF,),
        "tokens": JobTokenIssuer(secret=b"s"),
        "poll_interval_s": 0.01,
        "settle_s": 1.0,
    }
    values.update(overrides)
    return ContainerHost(**values)


@pytest.mark.asyncio
async def test_container_host_runs_worker_then_destroys(services) -> None:
    run = await _queued_run(services)
    sandbox = FakeSandbox()

    async def worker() -> None:
        spec = sandbox.created[-1]
        claimed = await services.jobs.claim(spec.worker_id, [REF])
        assert claimed is not None
        await services.jobs.complete(claimed.job.id, claimed.job.fencing_token, {"ok": True})

    sandbox.on_exec = worker
    host = _host(services, sandbox)
    assert await host.run_once("host-worker") is True

    assert len(sandbox.created) == 1
    spec = sandbox.created[0]
    assert spec.image == IMAGE and set(spec.envs()) == ALLOWED_ENV_KEYS
    assert spec.job_token == JobTokenIssuer(secret=b"s").issue(spec.worker_id)
    (sandbox_id, command, timeout_ms) = sandbox.executed[0]
    assert sandbox_id == "sbx_1" and timeout_ms > 0
    assert command == "python -m avernet_evolution.plugins.entrypoint"
    assert spec.envs()[ENV_WORKER_ID] == spec.worker_id
    assert spec.job_token not in command
    assert sandbox.destroyed == ["sbx_1"]
    assert (await services.runs.get(BOT, run.id)).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_container_host_requeues_and_destroys_when_the_worker_dies(services) -> None:
    run = await _queued_run(services)
    sandbox = FakeSandbox()

    async def worker_dying_mid_job() -> None:
        spec = sandbox.created[-1]
        claimed = await services.jobs.claim(spec.worker_id, [REF])
        assert claimed is not None  # 认领后进程消失，作业保持开放

    sandbox.on_exec = worker_dying_mid_job
    host = _host(services, sandbox, settle_s=0.2)
    await host.run_once("host-worker")

    assert host.last_error == "worker_exited_with_open_job"
    assert sandbox.destroyed == ["sbx_1"]
    # §7.2：worker 死亡 = 租约丢失，运行作为下一次尝试重新排队，而不是被取消
    requeued = await services.runs.get(BOT, run.id)
    assert requeued.status is RunStatus.QUEUED
    assert requeued.attempt == 2
    claimed = await services.jobs.claim("worker-next", [REF])
    assert claimed is not None and claimed.job.attempt == 2


@pytest.mark.asyncio
async def test_container_host_leaves_no_sandbox_when_create_fails(services) -> None:
    await _queued_run(services)
    sandbox = FakeSandbox()
    sandbox.create_error = RuntimeError("no capacity")
    host = _host(services, sandbox)
    await host.run_once("host-worker")
    assert sandbox.destroyed == []
    assert "no capacity" in (host.last_error or "")


@pytest.mark.asyncio
async def test_container_host_does_nothing_without_queued_work(services) -> None:
    sandbox = FakeSandbox()
    host = _host(services, sandbox)
    assert await host.run_once("host-worker") is False
    assert sandbox.created == []
