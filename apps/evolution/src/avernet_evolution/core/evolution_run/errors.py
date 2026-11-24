"""进化运行的拒绝情形（06-evolution-run.zh-CN §3.2、§6、§7.1）。"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "BindingBusy",
    "BindingCheckFailed",
    "BudgetExhausted",
    "CandidateRejected",
    "CandidateRejectionCode",
    "CeilingExceeded",
    "EvolutionFrozen",
    "EvolutionRunError",
    "GenomeBaselineMissing",
    "IdempotencyKeyReused",
    "JobNotFound",
    "OperationNotFound",
    "PreconditionFailed",
    "RunAlreadyEnded",
    "RunNotFound",
    "StaleFencingToken",
]


class EvolutionRunError(Exception):
    """本服务所有拒绝的基类。"""


class BindingCheckFailed(EvolutionRunError):
    """一个或多个绑定未通过 §3.2 的检查；``problems`` 列出全部违规。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "binding check failed")


class PreconditionFailed(EvolutionRunError):
    """ETag 不匹配，或“创建”时已存在绑定文档。"""


class EvolutionFrozen(EvolutionRunError):
    """Bot 处于暂停/冻结状态（§11.3）。"""


class BudgetExhausted(EvolutionRunError):
    """某个预算维度耗尽；下一次计费调用失败，运行随后以 budget_exhausted 结束（§11.1）。"""


class CeilingExceeded(EvolutionRunError):
    """预算超出 Bot 级或租户级上限（§11.2）。"""


class BindingBusy(EvolutionRunError):
    """该绑定已有 queued/running 的运行（§4）。"""


class IdempotencyKeyReused(EvolutionRunError):
    """同一幂等键被用于不同的请求体（§6）。"""


class GenomeBaselineMissing(EvolutionRunError):
    """这个 Bot 还没有基因组基线（`active` 不存在），先创建第一个版本。

    它不是服务故障：新建 Bot 在首次 capture/promote 之前，任何需要解析
    `parent: "active"` 的配置写入或运行提交都会得到它。
    """

    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        super().__init__(
            f"bot {bot_id} has no genome baseline: create its first revision "
            "(capture and promote it) before configuring or running evolution"
        )


class RunNotFound(EvolutionRunError):
    pass


class RunAlreadyEnded(EvolutionRunError):
    pass


class JobNotFound(EvolutionRunError):
    pass


class OperationNotFound(EvolutionRunError):
    pass


class StaleFencingToken(EvolutionRunError):
    """调用携带的 fencing token 已被新持有者覆盖（§7.1）。"""

    def __init__(self, job_id: str, token: str | None) -> None:
        self.job_id = job_id
        self.token = token
        super().__init__(f"stale fencing token for job {job_id}")


class CandidateRejectionCode(StrEnum):
    """Safe, recognized Genome refusal codes; never arbitrary provider text."""

    ALREADY_RECORDED = "already_recorded"


class CandidateRejected(EvolutionRunError):
    """Genome refused to record a candidate; this is not a 07 verdict."""

    def __init__(self, code: CandidateRejectionCode) -> None:
        self.code = CandidateRejectionCode(code)
        super().__init__(f"candidate rejected: {self.code.value}")
