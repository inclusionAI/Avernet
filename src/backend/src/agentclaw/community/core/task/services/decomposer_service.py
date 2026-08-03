"""DecomposerPort community impl — rule-based runtime decomposition (Phase 4.3).

A deterministic, side-effect-free stand-in for the LLM decomposer (which lives
in the owner-bot SKILL per the dual-track rule). Splits a task spec into sub-tasks
by clause boundaries, dedups near-identical clauses (similarity ≥ 0.92). Used by
the runtime-decompose path (plan §5.2 / scheduler_ops._decomposition).

The real LLM decompose is the owner-bot SKILL's job; community never holds an LLM
prompt. This rule impl lets the loop close end-to-end in tests/local without one.

单签名 ``decompose_subtasks(spec, state) -> list[SubTaskSpec]``(plan §4.1/spec
FR-GRAPH-05);旧 ``decompose(task_id)->Plan`` / ``decompose_spec`` 随 ``Plan``
退场已删(2026-08-03-execution-stage-bot-skills)。
"""
from __future__ import annotations

from difflib import SequenceMatcher

from agentclaw.community.core.task.protocols import DecomposerPort
from agentclaw.community.core.task.domain.models import (
    RunMode,
    SubTaskSpec,
    TaskState,
)

_DEDUP_THRESHOLD = 0.92


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


class DecomposerService(DecomposerPort):
    """Rule-based DecomposerPort,单签名 ``decompose_subtasks``。"""

    def decompose_subtasks(self, spec: str, state: TaskState) -> list[SubTaskSpec]:
        """v2 单签名(plan §4.1/spec FR-GRAPH-05)。children ``depth = 父 depth +1``;
        父深度由调用方置入 ``state.public['__decompose_parent_depth__']``(未置 = 顶层
        → children depth=0,即根 subtask)。规则分句同旧 ``decompose_spec``。"""
        parent_depth = int(state.public.get("__decompose_parent_depth__", -1))
        child_depth = parent_depth + 1 if parent_depth >= 0 else 0
        clauses = _dedup(_split_clauses(spec))
        return [
            SubTaskSpec(
                node_id=f"n{i + 1}", spec=c, run_mode=RunMode.SINGLE_BOT, depth=child_depth
            )
            for i, c in enumerate(clauses)
        ]


__all__ = ["DecomposerService"]