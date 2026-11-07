"""A write-hook candidate that is neither ``None`` nor a dict must abort the
persistence at the service, before the repository is ever touched.

Reached through the REAL ``create_template`` / ``update_template`` entries
(the same choke every ``ac_templates.ext`` write passes) with the REAL
in-memory SQLite repository: the rejection escapes the service unwrapped as
:class:`TemplateValidationError`, no insert/update reaches the repository and
an existing row keeps its persisted content — the incoming snapshot and the
stored snapshot are both left untouched.

Run (community suite, host-only, in-memory SQLite):
DEPLOY_PROFILE=test pytest tests/community/core/bot_management/services/test_template_service_write_hook_guard.py -q
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
    TemplateValidationError,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.implementations.bot.template import (
    TemplateRepository,
)
from agentclaw.community.plugin_api.bot_template_write_extension import (
    TemplateWriteExtension,
)


class Database:
    def __init__(self, engine):
        self.sessions = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        with self.sessions.begin() as session:
            yield session


@pytest.fixture
def service():
    engine = create_engine("sqlite://")
    TemplateModel.__table__.create(engine)
    db = Database(engine)
    yield TemplateService(
        repository=TemplateRepository(db), vault=TokenVault(master_key="")
    )
    engine.dispose()


class _NonDictReturning(TemplateWriteExtension):
    """Returns a non-dict candidate — the shape the guard exists for."""

    def __init__(self, candidate: Any) -> None:
        self.candidate = candidate

    def validate_template_write(
        self, *, bot_id: str, template_config, source: str, existing_ext: Optional[Any]
    ):
        return self.candidate


BAD_CANDIDATES = ["template-string", ["not", "a", "dict"], 42]


@pytest.mark.parametrize("bad", BAD_CANDIDATES)
def test_create_persists_nothing_when_hook_returns_non_dict(service, bad):
    service._write_extension = _NonDictReturning(bad)
    with patch.object(
        service._repository, "insert"
    ) as insert, patch.object(service._repository, "update_by_bot_id") as update:
        with pytest.raises(TemplateValidationError, match="must be a dictionary"):
            service.create_template("b1", {"k": "v"})
        insert.assert_not_called()
        update.assert_not_called()
    assert service.get_template("b1") is None


@pytest.mark.parametrize("bad", BAD_CANDIDATES)
def test_update_persists_nothing_when_hook_returns_non_dict(service, bad):
    service._repository.insert({"bot_id": "b1", "ext": {"k": "old"}})
    service._write_extension = _NonDictReturning(bad)
    with patch.object(
        service._repository, "update_by_bot_id"
    ) as update, patch.object(service._repository, "insert") as insert:
        with pytest.raises(TemplateValidationError, match="must be a dictionary"):
            service.update_template("b1", {"k": "replacement"})
        update.assert_not_called()
        insert.assert_not_called()
    # fail-closed: the previous persisted snapshot is untouched
    assert service.get_template_config("b1") == {"k": "old"}


def test_dict_candidate_still_persists_verbatim(service):
    """Distinguish the guard from over-rejection: a dict candidate flows."""

    class _Echo(TemplateWriteExtension):
        def validate_template_write(
            self, *, bot_id: str, template_config, source: str, existing_ext: Optional[Any]
        ):
            return {"k": "adjusted"}

    service._write_extension = _Echo()
    service.create_template("c1", {"k": "incoming"})
    assert service.get_template_config("c1") == {"k": "adjusted"}
