"""Composition-level model routing, charging and historical profile isolation."""

from types import SimpleNamespace

import pytest

from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_strategy import RunSummary, registration_from_json
from avernet_evolution.local import BOT, build_local_platform


class Models:
    def __init__(self):
        self.calls = []
        self.scopes = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        from avernet_evolution.core.evolution_run.data_scope import current_data_scope

        self.scopes.append(current_data_scope.get())
        return SimpleNamespace(text="response", model="fixture", cost_usd=0.01, tokens=12)


class Strategy:
    async def run(self, ctx):
        result = await ctx.models.complete(messages=[{"role": "user", "content": "probe"}])
        assert result.text == "response"
        return RunSummary(summary="model called")


@pytest.mark.parametrize("isolated", [False, True])
async def test_compose_routes_and_charges_platform_models(settings, isolated):
    platform = await build_local_platform(settings)
    platform.models = Models()
    ref = "local/model-probe@1.0.0"
    await platform.registry.register(
        registration_from_json(
            {
                "id": "local/model-probe",
                "version": "1.0.0",
                "runtime": {"kind": "in_process", "package": "fixture"},
                "needs": {"models@1": {}},
            }
        ),
        {},
    )
    platform.strategies[ref] = Strategy
    runtime = await compose(settings, platform)
    try:
        await runtime.api.put_bindings(
            BOT,
            [
                {
                    "id": "probe",
                    "strategy": ref,
                    "trigger": {"manual": True},
                    "parent": "active",
                    "allowed_genes": ["persona"],
                    "verification_profile": "rules-file@1",
                    "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
                }
            ],
            expected_etag=None,
            actor=BOT.owner_id,
        )
        run_id = (
            await runtime.api.submit_run(
                BOT,
                {"binding_id": "probe", "idempotency_key": "probe"},
                actor=BOT.owner_id,
            )
        )["run"]
        if isolated:
            token = runtime.channel.tokens.issue("probe-worker")
            job = await runtime.channel.claim("probe-worker", token, [ref])
            reply = await runtime.channel.models_complete(
                job["job_id"],
                job["fencing_token"],
                messages=[{"role": "user", "content": "probe"}],
            )
            assert reply == {"text": "response", "model": "fixture", "cost_usd": 0.01}
            from avernet_evolution.core.evolution_run.job_protocol import JobUnauthorized

            with pytest.raises(JobUnauthorized):
                await runtime.channel.models_complete(job["job_id"], "wrong", messages=[])
        else:
            assert await runtime.runner.tick() == 1
            assert runtime.store.get_run(run_id).status.value == "completed"
        assert len(platform.models.calls) == 1
        from avernet_evolution.core.evolution_run.data_scope import current_data_scope, run_data_scope

        assert platform.models.scopes == [run_data_scope(runtime.store.get_run(run_id))]
        assert current_data_scope.get() is None
        usage = runtime.store.get_run(run_id).budget_used
        assert float(usage.usd) == 0.01 and usage.tokens == 12
    finally:
        await runtime.close()


async def test_missing_model_provider_is_explicit(settings):
    platform = await build_local_platform(settings)
    runtime = await compose(settings, platform)
    try:
        assert runtime.channel.models is None
    finally:
        await runtime.close()
