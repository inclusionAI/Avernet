"""Unit tests for ManifestContentRepository.

Exercised against in-memory / file-backed SQLite — the same single ORM body
that runs on prod OceanBase, so the append-only shape and the tenant guard
are tested against a real database rather than a mock.

This file is the provenance contract reviewers read: the log has no
``update``/``delete`` surface at all, and each test below exists to keep it
that way — repetition is the audit fact, not a bug to dedupe away.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_config_manifest.content.models import (
    ManifestContentModel,  # noqa: F401  side-effect: registers on Base.metadata
    StoredContentRecord,
)
from agentclaw.community.core.repository.implementations.bot.manifest_content import (
    ManifestContentRepository,
)


class InMemorySqliteDB:
    """In-memory SQLite DB for unit testing."""

    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

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
def repo():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return ManifestContentRepository(InMemorySqliteDB(engine))


def _record(*, env="dev", entity_id="ent_a", bot_id="bot_1",
            digest="sha256:" + "ab" * 32, source_url="https://content.example/a.bin",
            fetched_url="https://cdn.example/a.bin", credential_name=None,
            size_bytes=1024, modifier="u1") -> StoredContentRecord:
    return StoredContentRecord(
        env=env,
        entity_id=entity_id,
        bot_id=bot_id,
        digest=digest,
        source_url=source_url,
        fetched_url=fetched_url,
        credential_name=credential_name,
        content_type="application/octet-stream",
        size_bytes=size_bytes,
        fetched_at=datetime(2026, 8, 31, 12, 0, 0),
        modifier=modifier,
    )


def test_add_returns_the_row_with_an_id(repo):
    stored = repo.add(_record())
    assert stored.id is not None
    assert stored.digest.startswith("sha256:")
    assert stored.credential_name is None
    assert stored.content_type == "application/octet-stream"


def test_the_log_is_append_only_the_same_digest_twice_is_two_rows(repo):
    # Two fetch events of the same bytes: two receipts. Dedup lives in the
    # blob layer by content address; here repetition IS the audit fact.
    first = repo.add(_record())
    second = repo.add(_record(modifier="u2"))
    assert first.id != second.id
    receipts = repo.records_for(env="dev", entity_id="ent_a", bot_id="bot_1")
    assert len(receipts) == 2


def test_records_for_returns_newest_first_and_only_that_bot(repo):
    for i in range(3):
        repo.add(_record(modifier=f"u{i}", digest=f"sha256:{i:064x}"))
    repo.add(_record(entity_id="ent_b", bot_id="bot_2", digest="sha256:" + "cd" * 32))
    receipts = repo.records_for(env="dev", entity_id="ent_a", bot_id="bot_1")
    assert [r.modifier for r in receipts] == ["u2", "u1", "u0"]


def test_records_limit_bounds_the_audit_read(repo):
    for i in range(5):
        repo.add(_record(modifier=f"u{i}", digest=f"sha256:{i:064x}"))
    assert len(repo.records_for(env="dev", entity_id="ent_a",
                                bot_id="bot_1")) == 5
    assert len(repo.records_for(env="dev", entity_id="ent_a",
                                bot_id="bot_1", limit=2)) == 2


def test_a_nonpositive_limit_is_refused_not_answered_empty(repo):
    """The review ruling on the negative clamp: an audit read that returns
    ``[]`` is read as "this bot has no receipts" — a claim, not a page. A
    caller whose page math went negative (or computed zero) must be told it
    asked something broken, not told a fact about the bot. And the raw SQL
    hazard remains real: a negative LIMIT is "unbounded" on SQLite and
    varies per dialect, so refusing it keeps the bound honest everywhere.
    (This deliberately replaces the earlier `limit=-1 → []` pin per that
    ruling.)"""
    with pytest.raises(ValueError):
        repo.records_for(env="dev", entity_id="ent_a", bot_id="bot_1", limit=-1)
    with pytest.raises(ValueError):
        repo.records_for(env="dev", entity_id="ent_a", bot_id="bot_1", limit=0)


def test_an_unknown_bot_reads_an_empty_receipt_list(repo):
    # Absent is not an error — the audit read over a bot that never fetched
    # is an empty list, the same "absent is not an error" stance as W1.
    assert repo.records_for(env="dev", entity_id="ent_a", bot_id="none") == []


def test_latest_for_returns_the_newest_receipt_for_one_source_url(repo):
    # The per-source lookup the fetch pipeline (W5) asks: "does this bot's
    # newest receipt for *this* URL hold these bytes?" — bounded by no
    # DEFAULT_RECORD_LIMIT, so a busy bot cannot evict from the answer the
    # one row a category wants.
    repo.add(_record(modifier="u0", digest=f"sha256:{0:064x}"))
    repo.add(_record(modifier="u1", digest=f"sha256:{1:064x}"))
    latest = repo.latest_for(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        source_url="https://content.example/a.bin",
    )
    assert latest is not None
    assert latest.modifier == "u1"


def test_latest_for_matches_the_source_exactly_not_by_prefix(repo):
    # Exact string equality: a sibling path that merely *starts* like the URL
    # is a different source, and a prefix match would answer about it.
    repo.add(_record(source_url="https://content.example/a.bin"))
    assert repo.latest_for(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        source_url="https://content.example/a.bin",
    ) is not None
    assert repo.latest_for(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        source_url="https://content.example/a.bin.bak",
    ) is None
    assert repo.latest_for(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        source_url="https://content.example/other.bin",
    ) is None


def test_latest_for_of_a_never_fetched_url_is_none(repo):
    # Absent is not an error here either: "never fetched from this URL" and
    # "the bot never fetched at all" both answer None.
    assert repo.latest_for(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        source_url="https://content.example/a.bin",
    ) is None


def test_latest_for_reads_only_another_bot_none_of_theirs(repo):
    # The same scoping the audit read applies: another bot's receipt for the
    # identical URL is not this bot's (a shared content mirror serves many
    # bots; the provenance question is per-bot).
    repo.add(_record(bot_id="bot_2", digest=f"sha256:{5:064x}"))
    assert repo.latest_for(
        env="dev", entity_id="ent_a", bot_id="bot_1",
        source_url="https://content.example/a.bin",
    ) is None

def test_latest_for_confines_to_the_request_tenant(tmp_path):
    """The lookup answers across the same isolation boundary as the audit
    read: two tenants sharing a legacy ``bot_id`` must never read each
    other's newest receipt — or a manifest apply would pin content fetched
    as another tenant."""
    from agentclaw.community.core.base import Base
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    engine = create_engine(
        f"sqlite:///{tmp_path / 'content-latest.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    repo = ManifestContentRepository(InMemorySqliteDB(engine))
    shared = dict(env="dev", entity_id="default", bot_id="shared-bot-id")
    url = "https://content.example/shared.bin"

    with avernet_tenant_scope("tenant-a"):
        repo.add(_record(digest="sha256:" + "11" * 32, modifier="tenant-a-op",
                         source_url=url, fetched_url=url, **shared))
    with avernet_tenant_scope("tenant-b"):
        # Nothing of tenant-b's under this URL, even though the other tenant
        # just stored one under the same bot axes.
        assert repo.latest_for(source_url=url, **shared) is None
    with avernet_tenant_scope("tenant-a"):
        assert repo.latest_for(source_url=url, **shared).modifier == "tenant-a-op"


# --- tenant isolation -------------------------------------------------------


def test_two_tenants_sharing_a_bot_id_read_only_their_own_receipts(tmp_path):
    """``ac_bots`` is tenant-scoped, so a ``bot_id`` is unique only *within* a
    tenant — legacy ``default`` bots carry documented cross-tenant collision
    on that identifier. Without the tenant guard this log would answer "what
    did this bot receive" across tenants, which is precisely the §2.8 audit
    question leaking across the isolation boundary.

    Uses a file-backed SQLite so the guard runs against a real connection,
    and drives the same tenant scope the request middleware binds.
    """
    from agentclaw.community.core.base import Base
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    engine = create_engine(
        f"sqlite:///{tmp_path / 'content.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    repo = ManifestContentRepository(InMemorySqliteDB(engine))
    shared = dict(env="dev", entity_id="default", bot_id="shared-bot-id")

    with avernet_tenant_scope("tenant-a"):
        repo.add(_record(digest="sha256:" + "11" * 32, modifier="tenant-a-op", **shared))
    with avernet_tenant_scope("tenant-b"):
        assert repo.records_for(**shared) == []
        repo.add(_record(digest="sha256:" + "22" * 32, modifier="tenant-b-op", **shared))

    with avernet_tenant_scope("tenant-a"):
        receipts = repo.records_for(**shared)
        assert [r.modifier for r in receipts] == ["tenant-a-op"]
    with avernet_tenant_scope("tenant-b"):
        assert [r.modifier for r in repo.records_for(**shared)] == ["tenant-b-op"]
