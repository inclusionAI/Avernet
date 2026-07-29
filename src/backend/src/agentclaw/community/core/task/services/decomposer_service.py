"""DecomposerPort community impl — rule-based runtime decomposition (Phase 4.3).

A deterministic, side-effect-free stand-in for the LLM decomposer (which lives
in the owner-bot SKILL per the dual-track rule). Splits a task spec into sub-tasks
by clause boundaries, dedups near-identical clauses (similarity ≥ 0.92), and
assigns a confidence that drops below 0.7 when the spec is over-broad ( signalling
the Scheduler to打回 / reroute). Used by the C4 runtime-decompose path and the
deepresearch ① split loop (plan §5.2).

The real LLM decompose is the owner-bot SKILL's job; community never holds an LLM
prompt. This rule impl lets the loop close end-to-end in tests/local without one.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional

from agentclaw.community.core.task.protocols import DecomposerPort
from agentclaw.community.core.task.domain.models import (
    Plan,
    RunMode,
    SubTaskSpec,
    Task,
)
from agentclaw.community.core.task.domain.repository import TaskRepo

_DEDUP_THRESHOLD = 0.92
_BROAD_CLAUSE_COUNT = 6


def _split_clauses(spec_text: str) -> list[str]:
    """Split a spec into clauses on punctuation / conjunctions."""
    text = (spec_text or "").strip()
    if not text:
        return []
    for sep in ["; ", "；", "。", ". ", "\n"]:
        text = text.replace(sep, "|")
    parts = [p.strip() for p in text.split("|") if p.strip()]
    return parts or [text]


def _dedup(clauses: list[str]) -> list[str]:
    kept: list[str] = []
    for c in clauses:
        if any(SequenceMatcher(None, c, k).ratio() >= _DEDUP_THRESHOLD for k in kept):
            continue
        kept.append(c)
    return kept


def _confidence(clause_count: int) -> float:
    # over-broad spec → low confidence (打回 signal); tight spec → high.
    if clause_count >= _BROAD_CLAUSE_COUNT:
        return 0.55
    if clause_count <= 2:
        return 0.9
    return 0.75


class DecomposerService(DecomposerPort):
    """Rule-based DecomposerPort. Reads the task spec goal/objective text.

    ``decompose(task_id)`` loads the task via the injected :class:`TaskRepo`
    (read-only) and derives the spec text from the goal objective, the title,
    or the background — in that order. ``decompose_spec`` is the pure core,
    unit-testable without a repo.
    """

    def __init__(self, task_repo: Optional[TaskRepo] = None) -> None:
        self._task_repo = task_repo

    def decompose(self, task_id: str) -> Plan:  # type: ignore[override]
        if self._task_repo is None:
            return self.decompose_spec("")
        try:
            task = self._task_repo.get_by_id(task_id)
        except Exception:
            return self.decompose_spec("")
        return self.decompose_spec(self._spec_text_of(task))

    @staticmethod
    def _spec_text_of(task: Task) -> str:
        spec = task.spec
        if spec.goal is not None and spec.goal.objective:
            return spec.goal.objective
        if spec.metadata.title:
            return spec.metadata.title
        return spec.context.background or ""

    def decompose_spec(self, spec_text: str) -> Plan:
        clauses = _dedup(_split_clauses(spec_text))
        sub_tasks = [
            SubTaskSpec(node_id=f"n{i + 1}", spec=c, run_mode=RunMode.SINGLE_BOT)
            for i, c in enumerate(clauses)
        ]
        return Plan(sub_tasks=sub_tasks, confidence=_confidence(len(clauses)))


__all__ = ["DecomposerService"]