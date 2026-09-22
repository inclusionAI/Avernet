from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import (
    APITestHelper,
    approve_publish,
    create_test_bot,
    get_devices_from_progress,
)
from tests.e2e.hook_helpers import HOOK_DEPLOY_CONFIG

pytestmark = [pytest.mark.mock_paas_hook_failure]


async def _run_sweep(bootstrap_init) -> None:
    """Run the sweep deterministically with an expired attempt window.

    The sweep uses its own attempt_timeout_seconds, independent of the
    publish's callback_timeout_seconds, so the window is forced to zero to
    make the just-created record immediately eligible.
    """
    task = bootstrap_init.tasks.publish_retry_sweep_task()
    task._config.enabled = True
    task._config.dry_run = False
    task._config.attempt_timeout_seconds = 0
    await task.run()


class TestSweepDrivenRetry:
    @pytest.mark.asyncio
    async def test_sweep_settles_stale_attempt_without_budget(
        self,
        api: APITestHelper,
        monkeypatch: pytest.MonkeyPatch,
        unique_id: str,
        bootstrap_init,
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")

        bot = await create_test_bot(
            api,
            f"sweep-nobudget-{unique_id}",
            deploy_config=HOOK_DEPLOY_CONFIG,
            device_count=1,
            callback_timeout_seconds=1,
            publish_max_retry_times=0,
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        await _run_sweep(bootstrap_init)
        await _run_sweep(bootstrap_init)

        devices = await get_devices_from_progress(api, publish_id)
        assert devices
        for d in devices:
            assert d.get("result_status") in ("SUCCESS", "FAILED")

    @pytest.mark.asyncio
    async def test_sweep_records_attempt_when_budget_available(
        self,
        api: APITestHelper,
        monkeypatch: pytest.MonkeyPatch,
        unique_id: str,
        bootstrap_init,
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")

        bot = await create_test_bot(
            api,
            f"sweep-budget-{unique_id}",
            deploy_config=HOOK_DEPLOY_CONFIG,
            device_count=1,
            callback_timeout_seconds=1,
            publish_max_retry_times=2,
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        await _run_sweep(bootstrap_init)

        devices = await get_devices_from_progress(api, publish_id)
        assert devices

    @pytest.mark.asyncio
    async def test_sweep_is_idempotent_across_runs(
        self,
        api: APITestHelper,
        monkeypatch: pytest.MonkeyPatch,
        unique_id: str,
        bootstrap_init,
    ) -> None:
        monkeypatch.setenv("PAAS_MOCK_MODE", "true")

        bot = await create_test_bot(
            api,
            f"sweep-idem-{unique_id}",
            deploy_config=HOOK_DEPLOY_CONFIG,
            device_count=1,
            callback_timeout_seconds=1,
            publish_max_retry_times=1,
        )
        publish_id = bot["publish_id"]
        assert (await approve_publish(api, publish_id)) == 200

        await _run_sweep(bootstrap_init)
        first = await get_devices_from_progress(api, publish_id)
        await _run_sweep(bootstrap_init)
        second = await get_devices_from_progress(api, publish_id)

        assert len(first) == len(second)
        for a, b in zip(
            sorted(first, key=lambda d: d.get("device_id") or 0),
            sorted(second, key=lambda d: d.get("device_id") or 0),
        ):
            assert a.get("result_status") == b.get("result_status")
