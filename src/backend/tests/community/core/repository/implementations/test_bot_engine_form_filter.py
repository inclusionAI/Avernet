"""Parity between the SQL engine filter and ``uses_aicoding_runtime``.

``engine_criterion`` (
``repository/implementations/bot/engine_filter.py``) mirrors the runtime
predicate arm-by-arm on the ``ac_bots`` column set. This module pins that
parity with in-memory SQLite: the rows the SQL criterion matches for
``engine=aicoding`` are exactly the rows ``uses_aicoding_runtime`` accepts,
across both vocabulary spells — and every other engine value keeps its
exact-match semantics.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from agentclaw.community.core.repository.implementations.bot.engine_filter import (
    engine_criterion,
)
from agentclaw.community.core.workspace.runtime_identity import (
    uses_aicoding_runtime,
)
from agentclaw.community.plugin_api.models import Base, BotModel

pytestmark = pytest.mark.unit

#: (suffix, active_engine, template_type) representative rows across the
#: vocabulary split: legacy literal aicoding, post-split claude_code coding
#: template forms, plain claude_code, and an unrelated engine.
_ROWS = [
    ("legacy_app", "aicoding", "applicationCoding"),
    ("legacy_personal", "aicoding", "personalCoding"),
    ("legacy_no_template", "aicoding", None),
    ("new_app", "claude_code", "applicationCoding"),
    ("new_personal", "claude_code", "personalCoding"),
    ("new_architect", "claude_code", "architect"),
    ("plain_cc", "claude_code", "normalCC"),
    ("plain_cc_uppercase", "claude_code", "NORMALCC"),
    ("plain_cc_no_template", "claude_code", None),
    ("plain_cc_blank_template", "claude_code", ""),
    ("openclaw_app", "openclaw", "applicationCoding"),
    ("openclaw_no_template", "openclaw", None),
    ("teclaw", "teclaw", None),
]


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BotModel.__table__])
    with Session(engine) as db:
        for suffix, active_engine, template_type in _ROWS:
            db.add(
                BotModel(
                    bot_id=f"bot_{suffix}",
                    entity_id="u1",
                    entity_type="staff",
                    creator_id="u1",
                    owner_id="u1",
                    active_engine=active_engine,
                    template_type=template_type,
                )
            )
        db.commit()
        yield db
    engine.dispose()


def _sql_matched(session: Session, engine_filter: str) -> set[str]:
    rows = session.scalars(
        select(BotModel).where(
            engine_criterion(BotModel, engine_filter)
        )
    ).all()
    return {row.bot_id for row in rows}


def _predicate_matches() -> set[str]:
    return {
        f"bot_{suffix}"
        for suffix, active_engine, template_type in _ROWS
        if uses_aicoding_runtime(
            active_engine=active_engine,
            template_type=template_type,
        )
    }


def test_aicoding_filter_matches_the_runtime_predicate_exactly(session) -> None:
    # Contract: SQL criterion == uses_aicoding_runtime over both vocabulary
    # spells. If someone edits one side without the other, this fails. The
    # form-marker arm cannot be expressed in SQL but is subsumed by the
    # template arm (see the helper's docstring), so the sets stay equal.
    assert _sql_matched(session, "aicoding") == _predicate_matches()
    # Legacy literal rows survive and post-split forms are no longer dropped.
    assert "bot_legacy_app" in _sql_matched(session, "aicoding")
    assert "bot_new_app" in _sql_matched(session, "aicoding")
    # Plain/neutral spellings stay excluded.
    assert "bot_plain_cc" not in _sql_matched(session, "aicoding")
    assert "bot_plain_cc_uppercase" not in _sql_matched(session, "aicoding")
    assert "bot_plain_cc_no_template" not in _sql_matched(session, "aicoding")


def test_non_aicoding_filters_keep_exact_match_semantics(session) -> None:
    # Historical semantics are untouched for every other value:
    # claude_code stays the full weight (aicoding form included), openclaw /
    # teclaw stay literal. No expansion outside the aicoding value.
    claude_code_rows = _sql_matched(session, "claude_code")
    assert "bot_new_app" in claude_code_rows  # form included, unchanged
    assert "bot_plain_cc" in claude_code_rows
    assert "bot_legacy_app" not in claude_code_rows
    assert not _sql_matched(session, "aicoding") & _sql_matched(session, "openclaw")
    assert _sql_matched(session, "teclaw") == {"bot_teclaw"}


def test_aicoding_spelling_normalization(session) -> None:
    # The expansion keys off the same normalization as the registry: a
    # differently-cased aicoding value still expands. (Hyphen variants are
    # not part of the engine vocabulary — "ai-coding" normalizes to
    # "ai_coding", a different value — and keep exact-match semantics.)
    assert _sql_matched(session, "AICODING") == _sql_matched(session, "aicoding")
    assert _sql_matched(session, " Aicoding ") == _sql_matched(session, "aicoding")
    assert _sql_matched(session, "ai-coding") == set()
