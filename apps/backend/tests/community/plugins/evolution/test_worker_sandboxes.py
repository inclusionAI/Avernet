"""Consumer/adapter contracts: actual calls, restricted spec and failure cleanup."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from avernet_evolution.core.evolution_run.sandbox import job_worker_spec, ALLOWED_ENV_KEYS
from agentclaw.community.plugins.evolution.sandbox_worker import SandboxRuntimeJobWorker

IMAGE = "registry.example/worker@sha256:" + "a" * 64


def spec(**kwargs):
    return job_worker_spec(worker_id="w1", image=IMAGE, job_endpoint="http://10.42.0.1:12345",
                           job_token="worker-scoped-only", **kwargs)


@pytest.mark.asyncio
async def test_platform_units_and_destroy_failure():
    runtime = Mock()
    runtime.create_sandbox.return_value = SimpleNamespace(sandbox_id="s1")
    runtime.exec_command.return_value = SimpleNamespace(exit_code=0)
    runtime.destroy_sandbox.return_value = False
    adapter = SandboxRuntimeJobWorker(runtime, "worker-template")
    assert await adapter.create(spec()) == "s1"
    kwargs = runtime.create_sandbox.call_args.kwargs
    assert kwargs["overrides"]["resource_spec"].memory == 4  # 4096MiB, NOT 4096GiB
    assert set(kwargs["envs"]) == ALLOWED_ENV_KEYS
    assert await adapter.exec("s1", "worker", timeout_ms=20) == 0
    runtime.exec_command.assert_called_once()
    with pytest.raises(RuntimeError, match="confirm destruction"):
        await adapter.destroy("s1")
    runtime.destroy_sandbox.assert_called_once()


