"""Bot catalog seam for the generalized搜推 (Phase 4.1, plan §2.4).

``BotDiscoverService`` needs a typed view of the available bots and their
capabilities to compute cover. The real bot-management service
(``BotServiceProtocol``) is a loose ``*args / **kwargs → Any`` surface not
suited to cover matching, so the task module owns this minimal typed seam.

This is a **task-internal** Port (not one of the 9 api Protocols) — like
``TaskRepo`` is a task-internal repository Port. Community default =
``LocalBotCatalog`` (the singlebox 5-bot set, the open-source local-dev协同
collaborators); a prod adapter may later wrap ``BotServiceProtocol`` / a BCS
bot-listing query into ``BotProfile``s (override ``BotCatalogPort`` in the prod
DI module — the 5-bot default is a *local-dev* fixture, NOT a prod source).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class BotProfile:
    """Typed capability view of a bot for cover calculation."""

    bot_id: str
    summary: str = ""
    skills: list[str] = field(default_factory=list)


@runtime_checkable
class BotCatalogPort(Protocol):
    """Read-only catalog of bots available for搜推 (cover matching)."""

    def list_bots(self) -> list[BotProfile]:
        ...


# --- singlebox 5-bot协同 set (local-dev default) -------------------------
# Mirrors ``ocb-public/src/bcs/scripts/start_bcs_bots.sh`` (the open-source
# singlebox 5-bot协同 fleet). Skills are English tokens (hyphenated multi-word,
# tokenized on cover match in ``bot_discover_service``); summaries are the
# canonical Chinese role descriptions. This is the local-dev catalog source so
# the loop's搜推 finds a real executor out of the box (community singlebox +
# corp ``singlebox --dev`` reuse it). Prod overrides ``BotCatalogPort``.
_SINGLEBOX_BOTS: list[BotProfile] = [
    BotProfile(
        bot_id="CEO",
        summary=(
            "CEO，团队总控 Bot，负责把 Chairman 的模糊目标压缩成清晰任务，用第一性原理识别"
            "关键约束，调度产品、研发、验证、客服协作，并在冲突中做最终取舍和推进闭环"
        ),
        skills=[
            "goal-framing", "first-principles-analysis", "task-decomposition",
            "priority-ranking", "agent-routing", "resource-allocation",
            "risk-framing", "tradeoff-analysis", "decision-escalation",
            "execution-followup",
        ],
    ),
    BotProfile(
        bot_id="产品经理",
        summary=(
            "产品经理，产品判断 Bot，负责从用户场景和体验闭环出发定义需求，识别真正痛点，"
            "裁剪功能范围，明确不做什么，并把产品判断转成研发和验证可执行的验收标准"
        ),
        skills=[
            "product-judgment", "user-scenario-framing", "requirement-clarification",
            "scope-pruning", "mvp-definition", "ux-review", "copy-review",
            "acceptance-criteria-design", "non-goal-definition", "product-risk-review",
        ],
    ),
    BotProfile(
        bot_id="研发",
        summary=(
            "研发，工程实现 Bot，负责把产品目标落到真实代码和系统边界上，评估架构影响、"
            "实现复杂度、技术风险和维护成本，优先选择简单、可测试、可演进的工程路径"
        ),
        skills=[
            "implementation-planning", "architecture-review", "code-review",
            "technical-risk-analysis", "complexity-control", "dependency-analysis",
            "contract-impact-review", "testability-review", "debugging-plan",
            "maintainability-assessment",
        ],
    ),
    BotProfile(
        bot_id="验证",
        summary=(
            "验证，质量验证 Bot，负责把团队结论转成可证伪假设，设计测试、寻找反例、检查"
            "边界条件，区分已验证和未验证内容，并在发布或承诺前给出证据化质量门禁"
        ),
        skills=[
            "test-design", "edge-case-analysis", "counterexample-search",
            "evidence-review", "quality-gate", "regression-risk-review",
            "acceptance-validation", "log-evidence-analysis",
            "coverage-gap-analysis", "release-readiness-check",
        ],
    ),
    BotProfile(
        bot_id="客服",
        summary=(
            "客服，客户服务 Bot，负责接住用户现场问题和情绪，整理诉求、影响范围、复现线索"
            "和承诺风险，把用户反馈转成产品、研发、验证可处理的输入，并推动服务补救和回访闭环"
        ),
        skills=[
            "customer-intake", "issue-triage", "service-recovery",
            "feedback-synthesis", "user-voice-summarization", "impact-assessment",
            "commitment-tracking", "escalation", "followup-planning",
            "external-response-drafting",
        ],
    ),
]


def singlebox_bots() -> list[BotProfile]:
    """The open-source singlebox 5-bot协同 fleet (local-dev catalog source)."""
    return [BotProfile(b.bot_id, b.summary, list(b.skills)) for b in _SINGLEBOX_BOTS]


class LocalBotCatalog:
    """Community default: a static list of bot profiles (non-bcsfuse, local).

    Defaults to the singlebox 5-bot协同 set so local-dev搜推 finds a real
    executor out of the box; pass an explicit ``bots`` (incl. ``[]``) to wire a
    different/empty source. Keeping the cover/route logic independent of the
    bot source is the point of the seam.
    """

    def __init__(self, bots: Optional[list[BotProfile]] = None) -> None:
        self._bots: list[BotProfile] = (
            list(bots) if bots is not None else singlebox_bots()
        )

    def list_bots(self) -> list[BotProfile]:
        return list(self._bots)


__all__ = ["BotCatalogPort", "BotProfile", "LocalBotCatalog", "singlebox_bots"]