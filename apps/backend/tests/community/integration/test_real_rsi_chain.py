"""端到端验证 RSI 核心链路（01-06）：用真实 bot 数据跑一次 binding→run→strategy→candidate。

前提（在你内网环境里设置环境变量）：
  BOT_GENOME_BASE_URL   例如 https://teamclawgw-pre.alipay.com/openapi/v1
  BOT_GENOME_USER_ID    479275
  BOT_GENOME_AUTH_TOKEN 平台调用凭证（IAM token，走 Cookie: IAM_TOKEN）

Bot（二选一）：
  BOT_ID=20261103_2g5uinv3   OWNER_ID=479275   （persona/CLAUDE.md）
  BOT_ID=20261103_0bpkqwlt   OWNER_ID=479275   （persona/SOUL.md）

运行：
  BOT_GENOME_BASE_URL=https://teamclawgw-pre.alipay.com/openapi/v1 \
  BOT_GENOME_USER_ID=479275 \
  BOT_GENOME_AUTH_TOKEN="$(cat /Users/hanzhou/project_code/iamToken)" \
  BOT_ID=20261103_0bpkqwlt OWNER_ID=479275 \
    uv run pytest tests/community/integration/test_real_rsi_chain.py -v

这条链路不涉及评测与晋升（后续 07/08 的工作），只验证：
  genome 读取（real capture/content） → 绑定检查 → 运行提交 → 策略执行 run(ctx)
  → 候选提交（本地内容寻址）→ 运行完成。
"""

from __future__ import annotations

import os
import secrets
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.adapters.bot_genome.real_bot_genome import RealBotGenome
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.evolution_run import (
    Binding,
    Budget,
    InProcessHost,
    ManualTrigger,
    RunStatus,
    TriggerKind,
    TriggerRecord,
    build_evolution_runtime,
    build_run_context,
)
from agentclaw.community.core.evolution_strategy import (
    InMemoryStrategyRegistry,
    Runtime,
    RuntimeKind,
    StrategyRegistration,
)
from agentclaw.community.core.evolution_strategy.strategies import IdentityHardening


class _PassThroughGovernance:
    def __init__(self) -> None:
        self._ceiling = Budget(max_usd=float("inf"), max_wall_clock_s=10 ** 9)

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
async def test_real_rsi_chain_end_to_end() -> None:
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

    runtime = build_evolution_runtime(
        genome=genome,
        registry=registry,
        governance=_PassThroughGovernance(),
        kills=_PermissiveKills(),
        strategies=strategies,
        models=None,
        experience=_EmptyExperience(),
        agents_provider=None,
        evaluation_provider=None,
        apply=None,
        sandbox_worker=None,
        strategy_images=None,
        database=":memory:",
    )

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
    await runtime.bindings.put_bindings(bot, [binding], expected_etag=None, actor="owner")

    run_id = await runtime.runs.submit(
        bot,
        "bind_real_01",
        idempotency_key="rsi_real_" + secrets.token_hex(4),
        trigger=TriggerRecord(kind=TriggerKind.MANUAL, requested_by=owner_id),
    )

    host = InProcessHost(
        strategies=strategies,
        store=runtime.store,
        jobs=runtime.jobs,
        operations=runtime.operations,
        budget=runtime.budget,
        genome=genome,
        ctx_factory=lambda run, **kwargs: build_run_context(
            run,
            store=runtime.store,
            genome=genome,
            budget=runtime.budget,
            operations=runtime.operations,
            **kwargs,
        ),
        models_provider=None,
        experience_provider=_EmptyExperience(),
        heartbeat_s=3600,
    )
    assert await host.run_once("worker-real-1") is True, host.last_error

    run = await runtime.runs.get(bot, run_id)
    assert run.status is RunStatus.COMPLETED, (run.status, host.last_error)
    assert run.candidate_count == 1
