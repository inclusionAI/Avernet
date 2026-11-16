"""BindingsService（06-evolution-run.zh-CN §3、§3.2、§13）。"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.evolution_strategy import ConformanceState, check_params

from .bindings import Binding, BotBindings, EventTrigger, ManualTrigger, ScheduleTrigger
from .errors import BindingCheckFailed, EvolutionFrozen
from .ports import GenomePort, GovernancePort, KillSwitchesPort, StrategyRegistryPort
from .store import EvolutionStore

__all__ = ["BindingsService"]

_GENES = ("persona", "skills", "memory", "resources", "tools")


class BindingsService:
    def __init__(
        self,
        store: EvolutionStore,
        *,
        registry: StrategyRegistryPort,
        genome: GenomePort,
        governance: GovernancePort,
        kills: KillSwitchesPort,
    ) -> None:
        self.store = store
        self.registry = registry
        self.genome = genome
        self.governance = governance
        self.kills = kills

    async def get_bindings(self, bot: BotRef) -> BotBindings:
        row = self.store.get_bindings(bot.owner_id, bot.bot_id)
        if row is None:
            return BotBindings(bot=bot, bindings=[], etag="")
        return BotBindings(
            bot=bot,
            bindings=_bindings_from_document(row["document"]),
            etag=row["etag"],
            updated_by=row["updated_by"],
        )

    async def put_bindings(
        self,
        bot: BotRef,
        bindings: list[Binding],
        *,
        expected_etag: str | None,
        actor: str,
    ) -> BotBindings:
        if await self.kills.bot_frozen(bot) or await self.kills.paused():
            raise EvolutionFrozen("bot is frozen or the platform is paused")
        problems = await self.check(bot, bindings)
        if problems:
            raise BindingCheckFailed(problems)
        ids = [binding.id for binding in bindings]
        if len(ids) != len(set(ids)):
            raise BindingCheckFailed(["binding ids must be unique within the bot"])
        document = {"bindings": [_binding_document(binding) for binding in bindings]}
        etag = self.store.put_bindings(bot, document, expected_etag, actor)
        return BotBindings(
            bot=bot, bindings=list(bindings), etag=etag, updated_by=actor
        )

    async def check(self, bot: BotRef, bindings: list[Binding]) -> list[str]:
        """§3.2 的八项检查；任何绑定失败都会列出其全部违规。"""

        engine = await self.governance.engine(bot)
        policy = await self.governance.policy(bot)
        bot_ceiling = await self.governance.bot_ceiling(bot)
        tenant_ceiling = await self.governance.tenant_ceiling(bot)
        problems: list[str] = []
        for binding in bindings:
            problems.extend(binding.shape_problems())
            problems.extend(
                await self._check_one(
                    bot, binding, engine, policy, bot_ceiling, tenant_ceiling
                )
            )
        return problems

    async def _check_one(
        self,
        bot: BotRef,
        binding: Binding,
        engine: str,
        policy: Any,
        bot_ceiling: Any,
        tenant_ceiling: Any,
    ) -> list[str]:
        prefix = f"binding {binding.id}"
        problems: list[str] = []

        # 1. 已注册、通过一致性、未被紧急停止开关禁用
        try:
            registered = await self.registry.get(
                binding.strategy_id, binding.strategy_version
            )
        except Exception as exc:  # noqa: BLE001 - registry raises its own NotFound
            return [f"{prefix}: strategy {binding.strategy} is not registered ({exc})"]
        if getattr(registered.conformance, "status", None) not in (
            ConformanceState.PASSED,
            ConformanceState.PENDING,
        ):
            problems.append(f"{prefix}: strategy version has not passed conformance")
        if not getattr(registered, "enabled", True):
            problems.append(f"{prefix}: strategy version is disabled")
        if await self.kills.strategy_disabled(
            binding.strategy_id, binding.strategy_version
        ):
            problems.append(f"{prefix}: strategy version is behind a kill switch")

        # 2/3. 每个 need 在该引擎有 provider；agents@1 的定义目标引擎集合
        try:
            engine_problems = await self.registry.check_engine(
                binding.strategy_id, binding.strategy_version, engine
            )
        except Exception as exc:  # noqa: BLE001
            engine_problems = [str(exc)]
        problems.extend(f"{prefix}: {problem}" for problem in engine_problems)

        # 4. allowed_genes 在 policy.mutable_genes 之内
        mutable = tuple(getattr(policy, "mutable_genes", ()) or ())
        for gene in binding.allowed_genes:
            if not _within(gene, mutable):
                problems.append(
                    f"{prefix}: allowed gene {gene!r} is outside the bot's mutable_genes"
                )

        # 5. 验证配置存在且不比默认更宽松
        problems.extend(
            f"{prefix}: {problem}"
            for problem in await self.governance.profile_problems(
                binding.verification_profile
            )
        )

        # 6. 预算落在 Bot 级与租户级上限之内
        if bot_ceiling is not None and not binding.budget.within(bot_ceiling):
            problems.append(f"{prefix}: budget exceeds the bot-level ceiling")
        if tenant_ceiling is not None and not binding.budget.within(tenant_ceiling):
            problems.append(f"{prefix}: budget exceeds the tenant-level ceiling")

        # 8. params 通过注册记录的 params_schema
        registration = getattr(registered, "registration", None)
        if registration is not None:
            problems.extend(
                f"{prefix}: params {problem}"
                for problem in check_params(registration, binding.params)
            )
        return problems


def _within(gene: str, mutable: tuple[str, ...]) -> bool:
    for allowed in mutable:
        root = allowed.rstrip("/")
        if gene == root or gene.startswith(root + "/") or allocated_root(gene) == root:
            return True
    return False


def allocated_root(gene: str) -> str:
    """把 ``persona/SOUL.md`` 归到 ``persona``；``skills/x/SKILL.md`` 归到 ``skills``。"""

    head = gene.split("/", 1)[0]
    return head if head in _GENES else gene


def _bindings_from_document(document: dict[str, Any]) -> list[Binding]:
    return [_binding_from_document(entry) for entry in document.get("bindings", [])]


def _binding_from_document(entry: dict[str, Any]) -> Binding:
    from .budget import Budget
    from .bindings import Rollout
    from .enums import RiskCeiling

    trigger_data = entry.get("trigger", {})
    if "schedule" in trigger_data:
        trigger: Any = ScheduleTrigger(trigger_data["schedule"])
    elif "event" in trigger_data:
        trigger = EventTrigger(trigger_data["event"])
    else:
        trigger = ManualTrigger()
    budget = entry["budget"]
    rollout_data = entry.get("rollout")
    return Binding(
        id=entry["id"],
        strategy=entry["strategy"],
        trigger=trigger,
        parent=entry.get("parent", "active"),
        allowed_genes=list(entry["allowed_genes"]),
        verification_profile=entry["verification_profile"],
        budget=Budget(
            max_usd=budget["max_usd"],
            max_wall_clock_s=budget["max_wall_clock_s"],
            max_rollouts=budget.get("max_rollouts"),
            max_tokens=budget.get("max_tokens"),
        ),
        params=dict(entry.get("params", {})),
        auto_promote_ceiling=RiskCeiling.parse(entry.get("auto_promote_ceiling", "T1")),
        rollout=Rollout(**rollout_data) if rollout_data else None,
    )


def _binding_document(binding: Binding) -> dict[str, Any]:
    if isinstance(binding.trigger, ScheduleTrigger):
        trigger = {"schedule": binding.trigger.schedule}
    elif isinstance(binding.trigger, EventTrigger):
        trigger = {"event": binding.trigger.event.value}
    else:
        trigger = {"manual": True}
    return {
        "id": binding.id,
        "strategy": binding.strategy,
        "trigger": trigger,
        "parent": binding.parent,
        "allowed_genes": list(binding.allowed_genes),
        "verification_profile": binding.verification_profile,
        "budget": {
            "max_usd": binding.budget.max_usd,
            "max_wall_clock_s": binding.budget.max_wall_clock_s,
            "max_rollouts": binding.budget.max_rollouts,
            "max_tokens": binding.budget.max_tokens,
        },
        "params": dict(binding.params),
        "auto_promote_ceiling": (
            binding.auto_promote_ceiling.name
            if binding.auto_promote_ceiling is not None
            else None
        ),
        "rollout": (
            {
                "canary_share": binding.rollout.canary_share,
                "auto_rollback": binding.rollout.auto_rollback,
            }
            if binding.rollout
            else None
        ),
    }
