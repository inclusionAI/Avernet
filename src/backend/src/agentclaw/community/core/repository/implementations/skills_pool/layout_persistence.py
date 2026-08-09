"""Pure persistence helpers for the Skills Pool layout repository."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from sqlalchemy import func, text
from sqlalchemy.sql.elements import ColumnElement

from agentclaw.community.core.skills_pool.repository.models import (
    BotSkillLayoutStateModel,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope


def scope_filter(scope: BotSkillLayoutScope) -> tuple[ColumnElement, ...]:
    return (
        BotSkillLayoutStateModel.env == scope.env,
        BotSkillLayoutStateModel.entity_id == scope.entity_id,
        BotSkillLayoutStateModel.bot_id == scope.bot_id,
    )


def lease_expiry(session, seconds: int) -> ColumnElement:
    if seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if session.bind.dialect.name == "sqlite":
        return func.datetime(func.now(), text(f"'+{seconds} seconds'"))
    return func.date_add(func.now(), text(f"INTERVAL {seconds} SECOND"))


def is_runtime_local_locator(value: object) -> bool:
    """Accept an opaque absolute ``local://`` locator without engine tables."""

    if not isinstance(value, str) or not value.startswith("local:///"):
        return False
    raw_path = value[len("local://") :]
    path = PurePosixPath(raw_path)
    return (
        path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == raw_path
    )


def encode_stage_evidence(
    encoded_evidence: str | None,
    *,
    stage: str,
    evidence: dict[str, object],
) -> str:
    """Merge one transition result into the persisted probe evidence."""

    stored = json.loads(encoded_evidence) if encoded_evidence else {}
    if not isinstance(stored, dict):
        stored = {}
    stored[stage] = evidence
    return json.dumps(stored, ensure_ascii=False)
