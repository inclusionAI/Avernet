"""DI wiring for the temporary duty-domain search fallback.

This module is deliberately self-contained so the temporary duty fallback policy can be
removed after search quality improves: delete this file and its container
registration; ``TaskModule`` then falls back to the null augmentor. The public
``/task/search`` contract and Relay report contract do not change.
"""
from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.core.task.domain.models import BotCandidate
from agentclaw.community.core.task.task_dispatch.candidate_augmentation import (
    NullSearchCandidateAugmentor,
    SearchCandidateAugmentor,
    TemporaryDutySearchCandidateAugmentor,
)
from agentclaw.community.di.modules import config_module


def _candidates(raw: Any) -> list[BotCandidate]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            "task_dispatch.temporary_duty_bot_candidates must be a list"
        )
    parsed: list[BotCandidate] = []
    bot_ids: set[str] = set()
    for index, record in enumerate(raw):
        if not isinstance(record, dict):
            raise ValueError(
                "task_dispatch.temporary_duty_bot_candidates"
                f"[{index}] must be a mapping, got {record!r}"
            )
        unknown = sorted(set(record) - {"bot_id", "name", "description"})
        if unknown:
            raise ValueError(
                "task_dispatch.temporary_duty_bot_candidates"
                f"[{index}] contains unknown keys: {', '.join(unknown)}"
            )
        bot_id = str(record.get("bot_id") or "").strip()
        name = str(record.get("name") or "").strip()
        if not bot_id or not name:
            raise ValueError(
                "task_dispatch.temporary_duty_bot_candidates"
                f"[{index}] requires non-blank bot_id and name"
            )
        if bot_id in bot_ids:
            raise ValueError(
                "task_dispatch.temporary_duty_bot_candidates"
                f" contains duplicate bot_id: {bot_id}"
            )
        bot_ids.add(bot_id)
        parsed.append(
            BotCandidate(
                bot_id=bot_id,
                name=name,
                description=str(record.get("description") or "").strip(),
                score=None,
            )
        )
    return parsed


class TemporaryDutyModule(Module):
    """Provide the deployment-owned duty fallback augmentor when configuration exists."""

    @singleton
    @provider
    def search_candidate_augmentor(self) -> SearchCandidateAugmentor:
        dispatch = config_module.read_user_config().get("task_dispatch") or {}
        if not isinstance(dispatch, dict):
            raise ValueError("task_dispatch must be a mapping")
        candidates = _candidates(
            dispatch.get("temporary_duty_bot_candidates")
        )
        if not candidates:
            return NullSearchCandidateAugmentor()
        return TemporaryDutySearchCandidateAugmentor(candidates)


__all__ = ["TemporaryDutyModule"]
