from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, IntakeTargetType, TaskExecutionMode, TaskIntakeResult,
    TaskIntakeStatus,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, TaskSourceType, TaskSpec,
)
from agentclaw.community.core.task.repository.task_intake_repository import TaskIntakeRepository
import agentclaw.community.core.task.repository.models  # noqa: F401


class _DB:
    def __init__(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    transactional_orm_session = orm_session


def _result() -> TaskIntakeResult:
    return TaskIntakeResult(
        session_id="tis-1", owner_user_id="owner-1", source_type=TaskSourceType.API,
        status=TaskIntakeStatus.CLARIFYING,
        target=IntakeTarget(IntakeTargetType.BOT, "bot-1"),
        execution_mode=TaskExecutionMode.CENTRALIZED,
        a2a_proxy_bot_id="proxy:owner-1",
        task_spec=TaskSpec(
            context=Context(title="上线", extend_props={"deliverables": ["记录"], "constraints": []}),
            goal=Goal(objective="上线模型", acceptances=[
                AcceptanceCriteria(id="ac1", description="指标正常"),
            ]),
        ),
        create_time_ms=1, update_time_ms=1,
    )


def test_round_trip_and_compare_and_swap() -> None:
    repo = TaskIntakeRepository(_DB())
    original = repo.create(_result())
    loaded = repo.get("tis-1")
    assert loaded == original
    confirmed = replace(loaded, status=TaskIntakeStatus.CONFIRMED, update_time_ms=2)
    repo.save(loaded, confirmed)
    assert repo.get("tis-1") == confirmed
    with pytest.raises(TaskStateError, match="concurrently"):
        repo.save(loaded, replace(loaded, status=TaskIntakeStatus.CANCELLED))
