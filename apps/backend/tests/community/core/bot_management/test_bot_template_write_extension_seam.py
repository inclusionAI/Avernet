"""The template-write extension seam: call sites, adjustment protocol, and
rejection passthrough (businessless here — every rule lives in the overlay).

Run (community suite, host-only, in-memory SQLite):
DEPLOY_PROFILE=test pytest tests/community/core/bot_management/test_bot_template_write_extension_seam.py -q
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
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.implementations.bot.template import (
    TemplateRepository,
)
from agentclaw.community.plugin_api.bot_template_write_extension import (
    NoopTemplateWriteExtension,
    TemplateWriteInvalidInput,
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
    TemplateService_instance = TemplateService(
        repository=TemplateRepository(db), vault=TokenVault(master_key="")
    )
    yield TemplateService_instance
    engine.dispose()


class _NoopCounting(NoopTemplateWriteExtension):
    """Counts every seam call without changing anything."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def validate_template_write(
        self,
        *,
        bot_id: str,
        template_config,
        source: str,
        existing_ext: Optional[Any],
    ) -> None:
        self.calls.append(
            {
                "bot_id": bot_id,
                "incoming": template_config,
                "source": source,
                "existing": existing_ext,
            }
        )
        return super().validate_template_write(
            bot_id=bot_id,
            template_config=template_config,
            source=source,
            existing_ext=existing_ext,
        )


class _Passthrough:
    """Returns an adjusted candidate and records every seam call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def validate_template_write(
        self,
        *,
        bot_id: str,
        template_config,
        source: str,
        existing_ext: Optional[Any],
    ):
        self.calls.append(
            {
                "bot_id": bot_id,
                "incoming": template_config,
                "source": source,
                "existing": existing_ext,
            }
        )
        adjusted = dict(template_config)
        adjusted["hook_seen"] = source
        return adjusted


class _Rejector:
    def validate_template_write(self, **_kw):
        raise TemplateWriteInvalidInput("hook rejects this write")


def test_noop_default_keeps_incoming_candidate(service: TemplateService) -> None:
    service._write_extension = NoopTemplateWriteExtension()
    service.create_template("b1", {"k": "v"})
    assert service.get_template_config("b1") == {"k": "v"}
    service.update_template("b1", {"k": "v2"})
    assert service.get_template_config("b1") == {"k": "v2"}


def test_hook_receives_source_and_decoded_existing(service: TemplateService) -> None:
    hook = _NoopCounting()
    service._write_extension = hook
    service.create_template("b1", {"k": 1})
    assert len(hook.calls) == 1
    assert hook.calls[0]["source"] == "create"
    assert hook.calls[0]["existing"] is None
    service.update_template("b1", {"k": 2})
    updated = hook.calls[1]
    assert updated["source"] == "update"
    # The CURRENT persisted row arrives decoded so an overlay can preserve
    # server-owned keys without another read.
    assert updated["existing"] == {"k": 1}


def test_hook_candidate_persisted_verbatim(service: TemplateService) -> None:
    hook = _Passthrough()
    service._write_extension = hook
    service.create_template("b1", {"k": 1})
    assert service.get_template_config("b1")["hook_seen"] == "create"
    assert service.get_template_config("b1")["k"] == 1
    service.update_template("b1", {"k": 2})
    assert service.get_template_config("b1")["hook_seen"] == "update"
    assert service.get_template_config("b1")["k"] == 2


def test_hook_rejection_propagates_unwrapped(service: TemplateService) -> None:
    service._write_extension = _Rejector()
    with pytest.raises(TemplateWriteInvalidInput):
        service.create_template("b1", {"k": 1})
    assert service.get_template("b1") is None
    with pytest.raises(TemplateWriteInvalidInput):
        service.update_template("b1", {"k": 2})


def test_wrapper_reaches_the_choke_exactly_once(service: TemplateService) -> None:
    hook = _Passthrough()
    service._write_extension = hook
    service.create_or_update_template(
        "w1", {"k": 1}, template_type=None, active_engine=None
    )
    service.create_or_update_template(
        "w1", {"k": 2}, template_type=None, active_engine=None
    )
    assert [c["source"] for c in hook.calls] == ["create", "update"]


def test_noop_default_with_no_explicit_binding(service: TemplateService) -> None:
    """The DI default (or no binding at all) keeps everything as-is —
    an unextended runtime persists exactly as before."""
    assert isinstance(service._write_extension, NoopTemplateWriteExtension)
    service.create_template("b2", {"k": 9})
    assert service.get_template_config("b2") == {"k": 9}


@pytest.mark.parametrize("method", ["create_template", "update_template"])
def test_existing_snapshot_read_failure_never_reaches_write(service, method):
    service.create_template("b1", {"k": "kept"})
    with patch.object(
        service._repository, "get_by_bot_id", side_effect=RuntimeError("read unavailable")
    ), patch.object(service._repository, "insert") as insert, patch.object(
        service._repository, "update_by_bot_id"
    ) as update:
        with pytest.raises(RuntimeError, match="read unavailable"):
            getattr(service, method)("b1", {"k": "replacement"})
        insert.assert_not_called()
        update.assert_not_called()
    assert service.get_template_config("b1") == {"k": "kept"}
