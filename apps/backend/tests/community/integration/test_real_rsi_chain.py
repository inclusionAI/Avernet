"""Opt-in HTTP Genome → canonical RSI composition acceptance (no evaluation/Apply).

Set BOT_GENOME_BASE_URL, BOT_GENOME_USER_ID, BOT_GENOME_AUTH_TOKEN, BOT_ID and
OWNER_ID only for an explicitly authorized disposable Bot with an active baseline.
Credentials and deployment endpoints must stay outside Git. The HTTP adapter
records candidates locally; this is NOT proof of remote candidate persistence.
Run explicitly with pytest tests/community/integration/test_real_rsi_chain.py.
"""

from __future__ import annotations

import os
import secrets
from types import SimpleNamespace
from typing import Any

import pytest
from agentclaw.community.adapters.bot_genome.real_bot_genome import RealBotGenome
from agentclaw.community.core.bot_genome.models import BotRef
from avernet_evolution.composition import compose
from avernet_evolution.config import Settings
from avernet_evolution.core.evolution_run import (
    Binding,
    Budget,
    ManualTrigger,
    RunStatus,
    TriggerKind,
    TriggerRecord,
)
from avernet_evolution.core.evolution_strategy import (
    InMemoryStrategyRegistry,
    Runtime,
    RuntimeKind,
    StrategyRegistration,
)
from avernet_evolution.core.evolution_strategy.strategies import IdentityHardening
from avernet_evolution.plugin_api import Platform


class _PassThroughGovernance:
    def __init__(self) -> None:
        self._ceiling = Budget(max_usd=float("inf"), max_wall_clock_s=10**9)

    async def engine(self, bot: Any) -> str:
        return getattr(bot, "engine_type", "") or "openclaw"

    async def policy(self, bot: Any) -> Any:
        return SimpleNamespace(
            mutable_genes=["persona", "skills", "memory", "resources"], pins=[]
        )

    async def bot_ceiling(self, bot: Any) -> Budget:
        return self._ceiling

    async def tenant_ceiling(self, bot: Any) -> Budget:
        return self._ceiling

    async def profile_problems(self, profile: Any) -> list[str]:
        return []


class _PermissiveKills:
    async def strategy_disabled(self, strategy: str, version: str) -> bool:
        return False

    async def bot_frozen(self, bot: Any) -> bool:
        return False

    async def paused(self) -> bool:
        return False


class _EmptyExperience:
    """空会话即可让 IdentityHardening 走完（读取→判断→补丁→候选→判定）。"""

    def for_bot(self, bot):
        return self

    async def sessions(
        self, *, days: int, limit: int = 500, revision: str | None = None
    ) -> list[Any]:
        return []

    async def feedback(
        self, *, days: int, kinds: list[str] | None = None, limit: int = 500
    ) -> list[Any]:
        return []


def _env(name: str) -> str | None:
    return os.environ.get(name)


@pytest.mark.asyncio
async def test_real_rsi_chain_end_to_end(tmp_path) -> None:
    base_url = _env("BOT_GENOME_BASE_URL")
    user_id = _env("BOT_GENOME_USER_ID")
    auth_token = os.environ.get("BOT_GENOME_AUTH_TOKEN")
    bot_id = _env("BOT_ID")
    owner_id = _env("OWNER_ID")

    if not all([base_url, user_id, auth_token, bot_id, owner_id]):
        pytest.skip(
            "需要 BOT_GENOME_BASE_URL / BOT_GENOME_USER_ID / BOT_GENOME_AUTH_TOKEN / "
            "BOT_ID / OWNER_ID 才能跑真实 bot 链路"
        )

    genome = RealBotGenome(base_url=base_url, user_id=user_id, auth_token=auth_token)
    bot = BotRef(
        entity_id=bot_id,
        bot_id=bot_id,
        owner_id=owner_id,
        engine_type="openclaw",
        bot_type="personal",
    )

    # 真实 genome 读取
    active = await genome.resolve_revision(bot, "active")
    files = await genome.snapshot_files(active, bot=bot)
    assert files, "genome 快照为空"
    assert any(p.startswith("persona/") for p in files), "快照里没有 persona 文件"

    registry = InMemoryStrategyRegistry()
    registration = StrategyRegistration(
        id="acme/identity-hardening",
        version="1.0.0",
        runtime=Runtime(RuntimeKind.IN_PROCESS, package="acme.identity_hardening"),
        needs={"experience.sessions@1": {}},
    )
    await registry.register(registration, definitions={})
    strategies = {"acme/identity-hardening@1.0.0": IdentityHardening}

    key = tmp_path / "signing-key"
    key.write_text(secrets.token_hex(32))
    token = tmp_path / "local-token"
    token.write_text(secrets.token_hex(32))
    settings = Settings(
        database=tmp_path / "runs.db",
        signing_key_file=key,
        local_token_file=token,
        consumer=False,
    )
    runtime = await compose(
        settings,
        Platform(
            access=None,
            genome=genome,
            registry=registry,
            governance=_PassThroughGovernance(),
            kills=_PermissiveKills(),
            strategies=strategies,
            experience=_EmptyExperience(),
        ),
    )

    try:
        binding = Binding(
            id="bind_real_01",
            strategy="acme/identity-hardening@1.0.0",
            trigger=ManualTrigger(),
            parent="active",
            allowed_genes=["persona"],
            verification_profile="default@1",
            budget=Budget(max_usd=5.0, max_wall_clock_s=3600),
            params={},
        )
        await runtime.api.bindings.put_bindings(
            bot, [binding], expected_etag=None, actor="owner"
        )

        run_id = await runtime.api.runs.submit(
            bot,
            "bind_real_01",
            idempotency_key="rsi_real_" + secrets.token_hex(4),
            trigger=TriggerRecord(kind=TriggerKind.MANUAL, requested_by=owner_id),
        )

        host = runtime.runner.host
        assert await host.run_once("worker-real-1") is True, host.last_error

        run = await runtime.api.runs.get(bot, run_id)
        assert run.status is RunStatus.COMPLETED, (run.status, host.last_error)
        assert run.candidate_count == 1
        assert runtime.api.promotion is None and runtime.verifier is None
        assert (
            await genome.resolve_revision(bot, "active")
        ).revision.id == active.revision.id
    finally:
        await runtime.close()
