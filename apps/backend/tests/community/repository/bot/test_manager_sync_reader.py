"""SQLite fixtures for the manager-sync read repository.

``SqlManagerSyncReadRepository`` is the by-PK facts source behind
``ManagerSyncReadRepository``: one short session per read, stable
identity plus explicit collaborator rows plus Space eligibility, and
bounded key pages for the space fan-out / repair sweep. The contract
this file pins down:

- a Bot is addressed by PK *and* tenant *and* env — a key from another
  ownership never yields its facts;
- a missing/broken Space is a known-unavailable snapshot, never an
  empty roster;
- the query budget is 1 Bot + 1 roster + 1 Space + ceil(N/100)
  membership lookups — batching by 100, never one query per user;
- the reader is by-PK: same-``bot_id`` default bots and ref forms
  (numeric, ``team:<id>``, code, ``personal:``) resolve per persisted
  rules, with no release-stage filtering anywhere.

Fixtures mirror ``test_collaborator_repository_batch.py``: in-memory
SQLite over the existing ORM models only, seeded through the tenant
guard so ``BotModel`` rows carry their stamping tenant.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.repository.implementations.bot.manager_sync import (
    SqlManagerSyncReadRepository,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SnapshotTooLarge,
    SnapshotUnavailable,
    SpaceChange,
    SyncKey,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)


class _InMemorySqliteDB:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return _InMemorySqliteDB(engine)


@pytest.fixture
def reader(db):
    return SqlManagerSyncReadRepository(db)


# ── seeding helpers (existing ORM models only, no DDL) ──────────────
# The seeding factories (one Bot + explicit rows, one Space, its
# members, and a bulk Space-bot sweep) are extracted to
# ``tests/community/core/bot_collaborator/manager_sync/factories.py``
# so the unit and contract suites seed rows through this file's exact
# helpers; the ``seed_sync_facts`` signature is the Task 4 fixture,
# kept verbatim there.
from tests.community.core.bot_collaborator.manager_sync.factories import (  # noqa: E402
    seed_members,
    seed_space,
    seed_space_bots,
    seed_sync_facts,
)


@contextmanager
def _record_selects(db):
    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)


def _selects(statements):
    return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


# ── read_facts: isolation and identity ──────────────────────────────


def test_reader_does_not_find_another_tenants_bot(db):
    key = seed_sync_facts(db, tenant="a", env="dev", bot_pk=7,
        entity_id="stable", owner_id="owner", rows=(), space_id=None)
    reader = SqlManagerSyncReadRepository(db)
    with pytest.raises(SnapshotUnavailable):
        reader.read_facts(SyncKey("b", key.env, key.bot_pk))


def test_read_facts_returns_owner_entity_and_eligible_collaborators(db, reader):
    seed_space(db, space_id=31, env="dev")
    seed_members(db, space_id=31, env="dev", user_ids=["user-1"])
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=5, entity_id="entity-5",
        owner_id="owner-5",
        rows=(("user-1", "admin"), ("user-2", "member")), space_id=31,
    )

    facts = reader.read_facts(key)

    assert facts.key == key
    assert facts.bot_id == "bot-entity-5"
    assert facts.entity_id == "entity-5"
    assert facts.owner_id == "owner-5"
    assert [
        (fact.user_id, fact.role, fact.space_eligible)
        for fact in facts.collaborators
    ] == [
        ("user-1", "admin", True),
        ("user-2", "member", False),
    ]


def test_same_bot_pk_isolated_by_tenant_and_env(db, reader):
    key_a = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=7, entity_id="entity-a",
        owner_id="owner-a", rows=(("member-a", "admin"),), space_id=None,
    )
    key_b = seed_sync_facts(
        db, tenant="b", env="pre", bot_pk=9, entity_id="entity-b",
        owner_id="owner-b", rows=(("member-b", "admin"),), space_id=None,
    )

    facts_a = reader.read_facts(key_a)
    assert facts_a.entity_id == "entity-a"
    assert [fact.user_id for fact in facts_a.collaborators] == ["member-a"]
    facts_b = reader.read_facts(key_b)
    assert facts_b.entity_id == "entity-b"
    assert [fact.user_id for fact in facts_b.collaborators] == ["member-b"]

    # The same bot_pk under another tenant or env answers with a stable
    # not-found — never with the other ownership's facts.
    for foreign in (
        SyncKey("b", "dev", 7),
        SyncKey("a", "pre", 7),
        SyncKey("b", "pre", 7),
    ):
        with pytest.raises(SnapshotUnavailable) as excinfo:
            reader.read_facts(foreign)
        assert excinfo.value.code == "bot_not_found"
    with pytest.raises(SnapshotUnavailable) as excinfo:
        reader.read_facts(SyncKey("a", "dev", 9))
    assert excinfo.value.code == "bot_not_found"

    # Rejections leaked nothing: each ownership still reads its own facts.
    assert reader.read_facts(key_a).entity_id == "entity-a"
    assert reader.read_facts(key_b).entity_id == "entity-b"


def test_read_facts_keeps_entity_id_across_owner_change(db, reader):
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=3, entity_id="stable-entity",
        owner_id="old-owner", rows=(("collab-1", "admin"),), space_id=None,
    )
    with avernet_tenant_scope("a"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 3).update(
            {"owner_id": "new-owner"}
        )

    facts = reader.read_facts(key)

    assert facts.entity_id == "stable-entity"
    assert facts.bot_id == "bot-stable-entity"
    assert facts.owner_id == "new-owner"
    assert [fact.user_id for fact in facts.collaborators] == ["collab-1"]


def test_read_facts_addresses_same_bot_id_bots_by_pk(db, reader):
    for pk, entity, owner, user in (
        (21, "entity-one", "owner-one", "collab-one"),
        (22, "entity-two", "owner-two", "collab-two"),
    ):
        seed_sync_facts(
            db, tenant="a", env="dev", bot_pk=pk, entity_id=entity,
            owner_id=owner, rows=((user, "admin"),), space_id=None,
            bot_id="default",
        )

    one = reader.read_facts(SyncKey("a", "dev", 21))
    two = reader.read_facts(SyncKey("a", "dev", 22))

    assert one.entity_id == "entity-one"
    assert one.owner_id == "owner-one"
    assert [fact.user_id for fact in one.collaborators] == ["collab-one"]
    assert two.entity_id == "entity-two"
    assert two.owner_id == "owner-two"
    assert [fact.user_id for fact in two.collaborators] == ["collab-two"]


# ── read_facts: Space eligibility and failure modes ──────────────────


def test_read_facts_treats_missing_space_as_known_unavailable(db, reader):
    seed_space(db, space_id=41, env="dev")
    seed_members(db, space_id=41, env="dev", user_ids=["user-1"])
    deleted_space_key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=4, entity_id="entity-4",
        owner_id="owner", rows=(("user-1", "admin"),), space_id=41,
    )
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter(
            SpaceMemberModel.space_id == 41
        ).delete(synchronize_session=False)
        session.query(SpaceModel).filter(SpaceModel.id == 41).delete(
            synchronize_session=False
        )
    # A deleted Space is a known-unavailable snapshot, not an empty roster.
    with pytest.raises(SnapshotUnavailable) as excinfo:
        reader.read_facts(deleted_space_key)
    assert excinfo.value.code == "space_unavailable"

    # So is a reference that never resolved to any Space at all.
    dangling_key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=6, entity_id="entity-6",
        owner_id="owner", rows=(("user-1", "admin"),), space_id=999999,
    )
    with pytest.raises(SnapshotUnavailable) as excinfo:
        reader.read_facts(dangling_key)
    assert excinfo.value.code == "space_unavailable"


def test_read_facts_tracks_space_membership_leave_and_rejoin(db, reader):
    seed_space(db, space_id=51, env="dev")
    seed_members(db, space_id=51, env="dev", user_ids=["stays", "leaves"])
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=8, entity_id="entity-8",
        owner_id="owner",
        rows=(("stays", "member"), ("leaves", "admin")), space_id=51,
    )

    eligible = {
        fact.user_id: fact.space_eligible
        for fact in reader.read_facts(key).collaborators
    }
    assert eligible == {"stays": True, "leaves": True}

    # Leave: the membership row is removed entirely (delete_member semantics).
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter(
            SpaceMemberModel.user_id == "leaves", SpaceMemberModel.space_id == 51
        ).delete(synchronize_session=False)
    eligible = {
        fact.user_id: fact.space_eligible
        for fact in reader.read_facts(key).collaborators
    }
    assert eligible == {"stays": True, "leaves": False}

    # Rejoin may arrive as a reactivated INACTIVE row — only ACTIVE counts.
    with db.orm_session() as session:
        session.add(
            SpaceMemberModel(
                space_id=51, user_id="leaves", user_name="leaves",
                role="MEMBER", status="INACTIVE", env="dev", created_by="creator",
            )
        )
    eligible = {
        fact.user_id: fact.space_eligible
        for fact in reader.read_facts(key).collaborators
    }
    assert eligible == {"stays": True, "leaves": False}
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter(
            SpaceMemberModel.user_id == "leaves", SpaceMemberModel.space_id == 51
        ).update({"status": "ACTIVE"})
    eligible = {
        fact.user_id: fact.space_eligible
        for fact in reader.read_facts(key).collaborators
    }
    assert eligible == {"stays": True, "leaves": True}


def test_read_facts_propagates_membership_read_error(db, reader):
    seed_space(db, space_id=61, env="dev")
    seed_members(db, space_id=61, env="dev", user_ids=["user-1"])
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=9, entity_id="entity-9",
        owner_id="owner", rows=(("user-1", "admin"),), space_id=61,
    )
    # A real database failure at the membership read — never an empty roster.
    SpaceMemberModel.__table__.drop(db.engine)

    with pytest.raises(SnapshotUnavailable) as excinfo:
        reader.read_facts(key)

    assert excinfo.value.code == "read_error"
    # The tenant scope is restored even after the failure.
    assert get_current_avernet_tenant() == "teamclaw"


def test_read_facts_resolves_all_persisted_space_id_forms(db, reader):
    seed_space(db, space_id=71, env="dev")
    seed_members(db, space_id=71, env="dev", user_ids=["user-1"])

    # Numeric id
    key_numeric = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=11, entity_id="entity-11",
        owner_id="owner", rows=(("user-1", "admin"),), space_id=71,
    )
    facts = reader.read_facts(key_numeric)
    assert facts.collaborators[0].space_eligible is True

    # "team:<id>" and Space code stored as legacy text references, plus the
    # synthetic "personal:" form which follows the existing no-team rules.
    key_team = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=12, entity_id="entity-12",
        owner_id="owner", rows=(("user-1", "admin"),), space_id="team:71",
    )
    facts = reader.read_facts(key_team)
    assert facts.collaborators[0].space_eligible is True

    key_code = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=13, entity_id="entity-13",
        owner_id="owner", rows=(("user-1", "admin"),), space_id="spc-71",
    )
    facts = reader.read_facts(key_code)
    assert facts.collaborators[0].space_eligible is True

    key_personal = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=14, entity_id="entity-14",
        owner_id="owner", rows=(("user-1", "admin"),), space_id="personal:owner",
    )
    facts = reader.read_facts(key_personal)
    assert facts.collaborators[0].space_eligible is True


def test_read_facts_allows_personal_space_without_membership_check(db, reader):
    seed_space(
        db, space_id=91, env="dev", space_type="PERSONAL", personal_owner_id="owner"
    )
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=15, entity_id="entity-15",
        owner_id="owner", rows=(("user-1", "admin"),), space_id=91,
    )
    with _record_selects(db) as statements:
        facts = reader.read_facts(key)
    assert facts.collaborators[0].space_eligible is True
    # EditorPolicy treats a non-TEAM Space as allowed — no membership query.
    assert len(_selects(statements)) == 3


# ── read_facts: query budget and roster caps ────────────────────────


def test_read_facts_uses_batched_membership_queries_within_budget(db, reader):
    space_id = 101
    seed_space(db, space_id=space_id, env="dev")
    roster = tuple((f"user-{i:03d}", "admin") for i in range(150))
    members = [f"user-{i:03d}" for i in range(0, 150, 2)]
    seed_members(db, space_id=space_id, env="dev", user_ids=members)
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=16, entity_id="entity-16",
        owner_id="owner", rows=roster, space_id=space_id,
    )

    with _record_selects(db) as statements:
        facts = reader.read_facts(key)

    selects = _selects(statements)
    # 1 Bot + 1 roster + 1 Space + ceil(150/100) membership batches — the
    # roster is never walked with one query per user.
    assert len(selects) == 5, statements
    assert len(facts.collaborators) == 150
    assert {f.user_id for f in facts.collaborators if f.space_eligible} == set(
        members
    )


def test_read_facts_reads_a_thousand_row_roster_within_budget(db, reader):
    space_id = 111
    seed_space(db, space_id=space_id, env="dev")
    roster = tuple((f"user-{i:04d}", "admin") for i in range(1_000))
    seed_members(
        db, space_id=space_id, env="dev",
        user_ids=[f"user-{i:04d}" for i in range(1_000)],
    )
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=17, entity_id="entity-17",
        owner_id="owner", rows=roster, space_id=space_id,
    )

    with _record_selects(db) as statements:
        facts = reader.read_facts(key)

    selects = _selects(statements)
    # 1 Bot + 1 roster + 1 Space + ceil(1000/100) membership batches.
    assert len(selects) == 13, statements
    assert len(facts.collaborators) == 1_000
    assert all(fact.space_eligible for fact in facts.collaborators)


@pytest.mark.parametrize("with_team", [False, True], ids=["no-team", "team"])
def test_read_facts_accepts_owner_plus_thousand_nonowners(
    db, reader, with_team
):
    owner_id = "owner-at-boundary"
    nonowners = [f"user-{i:04d}" for i in range(1_000)]
    roster = ((owner_id, "admin"), *[(user_id, "admin") for user_id in nonowners])
    space_id = 121 if with_team else None
    if with_team:
        seed_space(db, space_id=space_id, env="dev")
        seed_members(
            db, space_id=space_id, env="dev", user_ids=[owner_id, *nonowners]
        )
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=118, entity_id="entity-118",
        owner_id=owner_id, rows=roster, space_id=space_id,
    )

    facts = reader.read_facts(key)

    assert len(facts.collaborators) == 1_001
    assert sum(
        fact.space_eligible and fact.user_id != owner_id
        for fact in facts.collaborators
    ) == 1_000


def test_read_facts_raises_snapshot_too_large_without_truncating_eligibility(
    db, reader
):
    space_id = 121
    seed_space(db, space_id=space_id, env="dev")
    # The first 1,001 raw rows are NOT members; eligibility only appears in
    # the rows after them. A reader that judged eligibility from the first
    # 1,001 raw rows would return an all-ineligible roster instead of the
    # over-cap verdict.
    roster = tuple((f"user-{i:04d}", "admin") for i in range(2_002))
    seed_members(
        db, space_id=space_id, env="dev",
        user_ids=[f"user-{i:04d}" for i in range(1_001, 2_002)],
    )
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=18, entity_id="entity-18",
        owner_id="owner", rows=roster, space_id=space_id,
    )

    with pytest.raises(SnapshotTooLarge):
        reader.read_facts(key)


@pytest.mark.parametrize("with_team", [False, True], ids=["no-team", "team"])
def test_read_facts_rejects_owner_plus_thousand_and_one_nonowners(
    db, reader, with_team
):
    owner_id = "owner-over-boundary"
    nonowners = [f"user-{i:04d}" for i in range(1_001)]
    roster = ((owner_id, "admin"), *[(user_id, "admin") for user_id in nonowners])
    space_id = 131 if with_team else None
    if with_team:
        seed_space(db, space_id=space_id, env="dev")
        seed_members(
            db, space_id=space_id, env="dev", user_ids=[owner_id, *nonowners]
        )
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=119, entity_id="entity-119",
        owner_id=owner_id, rows=roster, space_id=space_id,
    )

    with pytest.raises(SnapshotTooLarge):
        reader.read_facts(key)


def test_read_facts_reads_bot_without_space_or_roster_in_two_queries(db, reader):
    key = seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=20, entity_id="entity-20",
        owner_id="owner", rows=(), space_id=None,
    )

    with _record_selects(db) as statements:
        facts = reader.read_facts(key)

    assert facts.collaborators == ()
    # Just the Bot and the roster — no Space, no membership batches.
    assert len(_selects(statements)) == 2


# ── page_space_keys ─────────────────────────────────────────────────


def _walk(db, reader, page):
    """Walk a paging method to exhaustion; assert order, no duplicates."""
    seen: list[int] = []
    after = 0
    while True:
        chunk = page(after_pk=after, limit=3)
        if not chunk:
            break
        pks = [key.bot_pk for key in chunk]
        assert pks == sorted(pks)
        assert pks[0] > after
        seen.extend(pks)
        after = pks[-1]
    assert len(seen) == len(set(seen))
    return seen


def test_page_space_keys_pages_distinct_bot_pks_without_duplicates(db, reader):
    space_id = 131
    seed_space(db, space_id=space_id, env="dev")
    seed_space_bots(
        db, tenant="a", env="dev", space_id=space_id,
        bot_pks=[31, 32, 33, 34, 35, 36, 37],
    )
    change = SpaceChange("a", "dev", (str(space_id),), ())

    seen = _walk(
        db,
        reader,
        lambda **kwargs: reader.page_space_keys(change, **kwargs),
    )

    assert seen == [31, 32, 33, 34, 35, 36, 37]


def test_page_space_keys_scopes_tenant_env_and_live_bots(db, reader):
    space_id = 141
    seed_space(db, space_id=space_id, env="dev")
    seed_space_bots(
        db, tenant="a", env="dev", space_id=space_id, bot_pks=[41, 42]
    )
    # Same Space value, wrong env — excluded by the explicit env on both sides.
    seed_space_bots(db, tenant="a", env="pre", space_id=space_id, bot_pks=[43])
    # Wrong tenant, wrong Space, soft-deleted — all excluded.
    seed_space_bots(db, tenant="b", env="dev", space_id=space_id, bot_pks=[44])
    seed_space_bots(db, tenant="a", env="dev", space_id=space_id + 1, bot_pks=[45])
    seed_space_bots(db, tenant="a", env="dev", space_id=space_id, bot_pks=[46])
    with avernet_tenant_scope("a"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 46).update(
            {"is_delete": 1}
        )
    # Live bot in the Space without an explicit collaborator row — excluded,
    # the fan-out pages bots that have explicit rows.
    with avernet_tenant_scope("a"), db.orm_session() as session:
        session.add(
            BotModel(
                id=47, bot_id="bot-47", entity_id="entity-47", entity_type="staff",
                creator_id="owner", owner_id="owner", env="dev",
                space_id=space_id, status="ACTIVE", is_delete=0,
            )
        )

    change = SpaceChange("a", "dev", (str(space_id),), ())
    page = reader.page_space_keys(change, after_pk=0, limit=100)

    assert [key.bot_pk for key in page] == [41, 42]
    assert all(
        key.avernet_tenant == "a" and key.env == "dev" for key in page
    )


def test_page_space_keys_filters_by_user_ids_when_given(db, reader):
    space_id = 151
    seed_space(db, space_id=space_id, env="dev")
    with avernet_tenant_scope("a"), db.orm_session() as session:
        for pk, user in ((51, "user-1"), (52, "user-2")):
            session.add(
                BotModel(
                    id=pk, bot_id=f"bot-{pk}", entity_id=f"entity-{pk}",
                    entity_type="staff", creator_id="owner", owner_id="owner",
                    env="dev", space_id=space_id, status="ACTIVE", is_delete=0,
                )
            )
            session.add(
                BotCollaboratorModel(
                    bot_pk=pk, bot_id=f"bot-{pk}", owner_id="owner",
                    user_id=user, user_name=user, role="admin",
                    operator_id="owner", env="dev",
                )
            )

    every = SpaceChange("a", "dev", (str(space_id),), ())
    filtered = SpaceChange("a", "dev", (str(space_id),), ("user-1",))

    assert [k.bot_pk for k in reader.page_space_keys(
        every, after_pk=0, limit=100)] == [51, 52]
    assert [k.bot_pk for k in reader.page_space_keys(
        filtered, after_pk=0, limit=100)] == [51]


def test_page_space_keys_resolves_numeric_team_and_code_refs(db, reader):
    space_id = 161
    seed_space(db, space_id=space_id, env="dev", space_code="spc-team")
    seed_space_bots(
        db, tenant="a", env="dev", space_id=space_id, bot_pks=[61, 62]
    )
    base = (SpaceChange("a", "dev", refs, ())
            for refs in (("spc-team",), (f"team:{space_id}",), (str(space_id),)))

    for change in base:
        assert [k.bot_pk for k in reader.page_space_keys(
            change, after_pk=0, limit=100)] == [61, 62]

    # All forms together resolve the same Space exactly once.
    combined = SpaceChange(
        "a", "dev", (str(space_id), f"team:{space_id}", "spc-team"), ()
    )
    assert [k.bot_pk for k in reader.page_space_keys(
        combined, after_pk=0, limit=100)] == [61, 62]


def test_page_space_keys_reaches_bots_of_deleted_space_by_numeric_ref(
    db, reader
):
    space_id = 171
    seed_space(db, space_id=space_id, env="dev", space_code="spc-gone")
    seed_space_bots(db, tenant="a", env="dev", space_id=space_id, bot_pks=[71])
    with db.orm_session() as session:
        session.query(SpaceModel).filter(SpaceModel.id == space_id).delete(
            synchronize_session=False
        )

    # A deleted Space needs no post-delete Space query; the numeric ref still
    # reaches the Bots that referenced it. Its code no longer resolves.
    numeric = SpaceChange("a", "dev", (str(space_id),), ())
    by_code = SpaceChange("a", "dev", ("spc-gone",), ())

    assert [k.bot_pk for k in reader.page_space_keys(
        numeric, after_pk=0, limit=100)] == [71]
    assert reader.page_space_keys(by_code, after_pk=0, limit=100) == ()


# ── page_keys ───────────────────────────────────────────────────────


def test_page_keys_pages_every_live_bot_in_tenant_scope(db, reader):
    seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=1, entity_id="entity-1",
        owner_id="owner", rows=(("collab-1", "admin"),), space_id=None,
    )
    seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=2, entity_id="entity-2",
        owner_id="owner", rows=(), space_id=None,
    )
    seed_sync_facts(
        db, tenant="b", env="dev", bot_pk=3, entity_id="entity-3",
        owner_id="owner", rows=(("collab-3", "admin"),), space_id=None,
    )
    seed_sync_facts(
        db, tenant="a", env="pre", bot_pk=4, entity_id="entity-4",
        owner_id="owner", rows=(("collab-4", "admin"),), space_id=None,
    )
    seed_sync_facts(
        db, tenant="a", env="dev", bot_pk=5, entity_id="entity-5",
        owner_id="owner", rows=(("collab-5", "admin"),), space_id=None,
    )
    with avernet_tenant_scope("a"), db.orm_session() as session:
        session.query(BotModel).filter(BotModel.id == 5).update(
            {"is_delete": 1}
        )

    seen = _walk(
        db,
        reader,
        lambda **kwargs: reader.page_keys(
            avernet_tenant="a", env="dev", **kwargs
        ),
    )

    assert seen == [1, 2]


def test_page_space_keys_and_page_keys_clamp_limit_to_100(db, reader):
    space_id = 181
    seed_space(db, space_id=space_id, env="dev")
    space_pks = list(range(300, 450))
    seed_space_bots(
        db, tenant="a", env="dev", space_id=space_id, bot_pks=space_pks
    )
    loose_pks = list(range(200, 260))
    for pk in loose_pks:
        seed_sync_facts(
            db, tenant="a", env="dev", bot_pk=pk, entity_id=f"entity-{pk}",
            owner_id="owner", rows=(), space_id=None,
        )

    space_change = SpaceChange("a", "dev", (str(space_id),), ())
    space_first = reader.page_space_keys(space_change, after_pk=0, limit=500)
    assert len(space_first) == 100
    assert [key.bot_pk for key in space_first] == space_pks[:100]

    keys_first = reader.page_keys(
        avernet_tenant="a", env="dev", after_pk=0, limit=500
    )
    assert len(keys_first) == 100
    assert [key.bot_pk for key in keys_first] == sorted(
        set(loose_pks) | set(space_pks)
    )[:100]