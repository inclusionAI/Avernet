"""Integration tests for filter_candidates in core/bot_dormant/service.py.

Five-bot fixture:
  bot_wl      — active, personal, entity_id=111 (numeric), gmt_create=60 days ago, IN whitelist  → excluded
  default     — active, personal, entity_id=222 (numeric), gmt_create=60 days ago, not whitelisted → KEPT
                (uses the real _DEFAULT_BOT_ID="default" to guard against any future exclusion of that id)
  bot_nondigit — active, personal, entity_id='abc', gmt_create=60 days ago → excluded (entity_id non-digit)
  bot_fresh   — active, personal, entity_id=333 (numeric), gmt_create=yesterday → excluded (too recent)
  bot_survive — active, personal, entity_id=444 (numeric), gmt_create=60 days ago → KEPT
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_dormant.candidates import (
    Candidate,
    filter_candidates,
    partition_by_protected_owner,
)
from agentclaw.community.core.bot_dormant.sqlite_models import DormantWhitelist
from agentclaw.community.plugin_api.models import Base, BotModel


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.unit
def test_partition_by_protected_owner_splits_candidates_in_one_pass():
    created_at = _now() - timedelta(days=60)
    protected_candidate = Candidate(
        bot_id="protected",
        entity_id="111",
        owner_id="owner1",
        bot_name="Protected",
        gmt_create=created_at,
    )
    normal_candidate = Candidate(
        bot_id="normal",
        entity_id="222",
        owner_id="owner2",
        bot_name="Normal",
        gmt_create=created_at,
    )

    protected, unprotected = partition_by_protected_owner(
        [protected_candidate, normal_candidate],
        frozenset({"owner1"}),
    )

    assert protected == [protected_candidate]
    assert unprotected == [normal_candidate]


@pytest.mark.integration
def test_filter_candidates_returns_only_qualifying_bots():
    """Only the default bot and bot_survive should be returned; all others are excluded."""
    session = _make_session()
    N = 30  # 30 days threshold

    now = _now()
    old_create = now - timedelta(days=60)
    yesterday = now - timedelta(days=1)

    # bot_wl: numeric entity_id, old enough, but whitelisted → excluded
    bot_wl = BotModel(
        bot_id="bot_wl",
        bot_name="Whitelisted Bot",
        entity_id="111",
        entity_type="user",
        creator_id="u1",
        owner_id="owner1",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )

    # Real default bot (bot_id="default" == _DEFAULT_BOT_ID): numeric entity_id, old enough,
    # not whitelisted → MUST survive. Using the real id ensures that any future special-case
    # logic that mistakenly excludes bot_id=="default" would be caught by this test.
    bot_default = BotModel(
        bot_id="default",
        bot_name="Default Bot",
        entity_id="222",
        entity_type="user",
        creator_id="u1",
        owner_id="owner2",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )

    # bot_nondigit: non-numeric entity_id → excluded in-memory
    bot_nondigit = BotModel(
        bot_id="bot_nondigit",
        bot_name="NonDigit Bot",
        entity_id="abc",
        entity_type="user",
        creator_id="u1",
        owner_id="owner3",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )

    # bot_fresh: created yesterday → too recent, excluded
    bot_fresh = BotModel(
        bot_id="bot_fresh",
        bot_name="Fresh Bot",
        entity_id="333",
        entity_type="user",
        creator_id="u1",
        owner_id="owner4",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=yesterday,
        gmt_modified=now,
    )

    # bot_survive: numeric entity_id, old enough, not whitelisted → KEPT
    bot_survive = BotModel(
        bot_id="bot_survive",
        bot_name="Survivor Bot",
        entity_id="444",
        entity_type="user",
        creator_id="u1",
        owner_id="owner5",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )

    session.add_all([bot_wl, bot_default, bot_nondigit, bot_fresh, bot_survive])

    # Add bot_wl to whitelist
    wl_entry = DormantWhitelist(
        bot_id="bot_wl",
        owner_id="owner1",
        reason="manual whitelist",
    )
    session.add(wl_entry)
    session.commit()

    candidates = filter_candidates(session, N)

    candidate_ids = {c.bot_id for c in candidates}

    # The real default bot (bot_id="default") and bot_survive must both be in results
    assert "default" in candidate_ids, (
        'real default bot (bot_id="default") must survive the filter — '
        "numeric entity_id, old enough, not whitelisted"
    )
    assert "bot_survive" in candidate_ids, "survivor bot must be kept"

    # the excluded bots must NOT be in results
    assert "bot_wl" not in candidate_ids, "whitelisted bot must be excluded"
    assert "bot_nondigit" not in candidate_ids, "non-digit entity_id bot must be excluded"
    assert "bot_fresh" not in candidate_ids, "too-recent bot must be excluded"

    # Verify Candidate fields are populated
    for c in candidates:
        assert isinstance(c, Candidate)
        assert c.bot_id
        assert c.entity_id
        assert c.owner_id
        assert c.bot_name is not None  # may be empty string but should exist

    session.close()


@pytest.mark.integration
def test_filter_candidates_deduplicates_same_bot_owner_pair():
    """Duplicate ac_bots rows for one logical bot should produce one candidate."""
    session = _make_session()
    N = 30

    now = _now()
    old_create = now - timedelta(days=60)

    first = BotModel(
        bot_id="default",
        bot_name="Default Bot Old",
        entity_id="111",
        entity_type="user",
        creator_id="u1",
        owner_id="owner_dup",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now - timedelta(days=1),
    )
    latest = BotModel(
        bot_id="default",
        bot_name="Default Bot Latest",
        entity_id="222",
        entity_type="user",
        creator_id="u1",
        owner_id="owner_dup",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )
    other_owner = BotModel(
        bot_id="default",
        bot_name="Default Bot Other Owner",
        entity_id="333",
        entity_type="user",
        creator_id="u1",
        owner_id="owner_other",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )
    session.add_all([first, latest, other_owner])
    session.commit()

    candidates = filter_candidates(session, N)

    dup_candidates = [
        c for c in candidates
        if c.bot_id == "default" and c.owner_id == "owner_dup"
    ]
    assert len(dup_candidates) == 1
    assert dup_candidates[0].entity_id == "222"
    assert {
        (c.bot_id, c.owner_id) for c in candidates
    } == {("default", "owner_dup"), ("default", "owner_other")}
    session.close()


@pytest.mark.integration
def test_filter_candidates_empty_when_all_excluded():
    """When all bots are either whitelisted, non-digit, or too recent → empty result."""
    session = _make_session()
    N = 30

    now = _now()
    old_create = now - timedelta(days=60)

    bot_wl = BotModel(
        bot_id="only_wl",
        bot_name="Only Bot",
        entity_id="999",
        entity_type="user",
        creator_id="u1",
        owner_id="owner1",
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )
    session.add(bot_wl)

    wl = DormantWhitelist(bot_id="only_wl", owner_id="owner1")
    session.add(wl)
    session.commit()

    candidates = filter_candidates(session, N)
    assert candidates == []
    session.close()


@pytest.mark.integration
def test_filter_candidates_excludes_non_personal():
    """Bots with bot_type != 'personal' are excluded by SQL filter."""
    session = _make_session()
    N = 30

    now = _now()
    old_create = now - timedelta(days=60)

    team_bot = BotModel(
        bot_id="team_bot",
        bot_name="Team Bot",
        entity_id="555",
        entity_type="user",
        creator_id="u1",
        owner_id="owner1",
        status="ACTIVE",
        is_delete=0,
        bot_type="team",
        gmt_create=old_create,
        gmt_modified=now,
    )
    session.add(team_bot)
    session.commit()

    candidates = filter_candidates(session, N)
    assert not any(c.bot_id == "team_bot" for c in candidates)
    session.close()


@pytest.mark.integration
def test_filter_candidates_excludes_deleted():
    """Soft-deleted bots (is_delete=1) are excluded."""
    session = _make_session()
    N = 30

    now = _now()
    old_create = now - timedelta(days=60)

    deleted_bot = BotModel(
        bot_id="deleted_bot",
        bot_name="Deleted Bot",
        entity_id="666",
        entity_type="user",
        creator_id="u1",
        owner_id="owner1",
        status="ACTIVE",
        is_delete=1,
        bot_type="personal",
        gmt_create=old_create,
        gmt_modified=now,
    )
    session.add(deleted_bot)
    session.commit()

    candidates = filter_candidates(session, N)
    assert not any(c.bot_id == "deleted_bot" for c in candidates)
    session.close()
