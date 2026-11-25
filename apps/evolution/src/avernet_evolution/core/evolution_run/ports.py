"""本服务所依赖的端口（transit-free）。各自的负责文档定义真实 provider。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from avernet_genome.models import BotRef, GenomeRevision
from avernet_genome.patch_models import GenomePatch

from avernet_evolution.core.evolution_strategy.protocols import ExperienceQuery

from .budget import BudgetUsage

__all__ = [
    "ExperiencePort",
    "ExperienceQueryFactory",
    "GateCheck",
    "GateOutcome",
    "GenomePort",
    "KillSwitchesPort",
    "LedgerPort",
    "PromotionPort",
    "StrategyRegistryPort",
    "VerdictStatus",
    "VerifierPort",
]


class VerdictStatus(StrEnum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


class GateOutcome(StrEnum):
    AUTO_PROMOTE = "auto_promote"
    NEEDS_REVIEW = "needs_review"
    NOT_PROMOTABLE = "not_promotable"


@dataclass(frozen=True)
class GateCheck:
    name: str
    result: str  # "pass" | "fail" | "warn"
    detail: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GateDecision:
    outcome: GateOutcome
    checks: tuple[GateCheck, ...] = ()
    risk_tier: str | None = None


@dataclass(frozen=True)
class BotPolicy:
    mutable_genes: tuple[str, ...] = ()
    pins: tuple[str, ...] = ()


class GenomePort(Protocol):
    """01-genome 的子集：漂移、引用解析、内容读取、候选记录、工作区物化所需文件。"""

    async def drift(self, bot: BotRef):
        """None = 未漂移；否则返回漂移记录，运行不应启动。"""
        ...

    async def resolve_revision(self, bot: BotRef, ref: str) -> GenomeRevision:
        """``active`` 或序列号/摘要解析为修订版。"""

    async def read_content(self, bot: BotRef, digest: str) -> bytes: ...

    async def snapshot_files(
        self, revision: GenomeRevision, *, bot: BotRef | None = None
    ) -> dict[str, bytes]:
        """按路径的文件内容（用于沙箱物化）；生产适配器需要 ``bot`` 读内容存储。"""

    async def commit_patch(
        self,
        bot: BotRef,
        patch: GenomePatch,
        run_id: str,
        strategy_ref: str,
        allowed_genes: tuple[str, ...],
        created_by: str,
    ) -> str:
        """Record a candidate and its run ref; return its revision id.

        Recognized content collisions raise CandidateRejected(ALREADY_RECORDED).
        Retries of the same run/patch return the existing id; another run must
        not claim an old revision's provenance. Other provider failures propagate.
        """

    async def move_ref(self, bot: BotRef, ref: str, revision_id: str, actor: str) -> None: ...


class StrategyRegistryPort(Protocol):
    """03-strategy §9 的 StrategyRegistry 子集。"""

    async def get(self, strategy_id: str, version: str) -> Any:
        """返回注册记录（含 needs、definitions、conformance、enabled）。"""

    async def list(self, *, engine) -> list[Any]: ...

    async def check_engine(self, strategy_id: str, version: str, engine: str) -> list[str]: ...


class VerifierPort(Protocol):
    """07-verification：`verify(candidate, profile)` 立即返回 pending 判定（§4.2）。"""

    async def verify(self, candidate: Any, profile: str | None = None) -> Any:
        """按 (Run, candidate, profile) 幂等；返回当前判定。"""

    async def strategy_view(self, candidate_id: str, run_id: str) -> Any:
        """策略可见的视图：仅 validation 聚合。"""

    async def settle(self, candidate_id: str, profile: str | None = None, *, run_id: str) -> Any:
        """等待后台流水线结束（测试与运维用）。"""


class PromotionPort(Protocol):
    """08-promotion：静态底线与门禁。"""

    async def check_floor(self, bot: BotRef, candidate_id: str) -> GateCheck: ...


class LedgerPort(Protocol):
    """05-experiment-ledger：记录运行、提交、判定、成本。"""

    async def record_run(self, run: Any) -> None: ...
    async def record_submission(
        self,
        *,
        bot: BotRef,
        run_id: str,
        binding_id: str,
        iteration: int,
        candidate_id: str,
        parent_revision: str,
        candidate_revision: str,
        evidence: list[str],
        patch: dict[str, Any],
        cost: BudgetUsage,
        actor: str,
    ) -> str: ...


class ExperienceQueryFactory(Protocol):
    """Platform Plugin API: bind experience reads to the authenticated run's Bot.

    The SDK's ExperienceQuery intentionally has no Bot argument. Shared Job
    channels must resolve it here, not reuse a tenant-global SDK query. Providers
    must enforce the complete BotRef scope and propagate read failures.
    """

    def for_bot(self, bot: BotRef) -> ExperienceQuery: ...


class ExperiencePort(Protocol):
    """02-experience：会话导出（脱敏后）。"""

    async def sessions(self, *, days: int, limit: int, revision: str | None) -> list[Any]: ...


class KillSwitchesPort(Protocol):
    """§11.3：策略/ bot / 全局停止开关。"""

    async def strategy_disabled(self, strategy: str, version: str) -> bool: ...
    async def bot_frozen(self, bot: BotRef) -> bool: ...
    async def paused(self) -> bool: ...


class GovernancePort(Protocol):
    """§3.2 检查所需的只读事实：Bot 的引擎、基因组策略、各级预算上限、验证配置严格度。"""

    async def engine(self, bot: BotRef) -> str: ...

    async def policy(self, bot: BotRef) -> BotPolicy: ...

    async def bot_ceiling(self, bot: BotRef) -> Any:
        """Bot 级预算上限（``Budget``）。"""

    async def tenant_ceiling(self, bot: BotRef) -> Any:
        """租户级单次运行预算上限（``Budget``）。"""

    async def profile_problems(self, profile: str) -> list[str]:
        """不存在，或比平台默认更宽松时返回问题。"""
