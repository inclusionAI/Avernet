"""Unified BotPublish repository — behavior + contract.

Round-3/session-4 criteria: single ORM body, 13-method
``BotPublishRepositoryProtocol`` parity. Covers the prod-parity
guarantees: plain INSERT (no upsert despite uk_oi_p_b_v), single
optimistic-lock UPDATEs (wrong source-status → None, no row touched),
and single hard DELETE.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ARTIFACT_KEY,
    ARTIFACT_OSS_MARKER,
    ARTIFACT_OSS_THRESHOLD_BYTES,
    ConfigArtifactOffloader,
)
from agentclaw.community.plugins.bot_publish_repository import (
    BotPublishRepository,
)

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bp.db'}",
        connect_args={"check_same_thread": False},
    )
    BotPublishModel.__table__.create(engine)
    # The offloader is DI-provided in prod; parity tests wrap the in-memory fake.
    return BotPublishRepository(
        _FileSqliteDB(engine), offload=ConfigArtifactOffloader(_FakeOSS())
    )


def _data(**overrides):
    base = dict(
        source_bot_pk=1,
        source_bot_id="src-bot",
        publish_bot_id="src-bot.pub.1",
        name="My Bot",
        description="desc",
        owner_id="emp001",
        owner_name="Alice",
        status="DRAFT",
        version=1,
        last_pub_id=0,
        env="dev",
        ext={"k": "v"},
        permission_owner="emp001",
    )
    base.update(overrides)
    return base


# ── insert (plain INSERT — no upsert) ───────────────────────────────

def test_insert_and_get_by_id(repo):
    rec = repo.insert(_data())
    assert rec.id > 0
    assert rec.name == "My Bot"
    assert rec.ext == {"k": "v"}
    got = repo.get_by_id(rec.id)
    assert got.id == rec.id


def test_insert_is_plain_not_upsert(repo):
    """Same (owner_id, publish_bot_id, version) inserts a 2nd row,
    not an upsert (prod parity — versioned/append-only)."""
    a = repo.insert(_data())
    b = repo.insert(_data())
    assert a.id != b.id
    rows = repo.list_by_owner("emp001", "dev")
    assert len(rows) == 2


def test_get_by_id_missing(repo):
    assert repo.get_by_id(999999) is None


# ── queries ─────────────────────────────────────────────────────────

def test_query_methods(repo):
    r = repo.insert(_data(status="RELEASE", version=2, last_pub_id=42))
    assert repo.get_by_publish_bot_id(
        "src-bot.pub.1", "emp001", "dev"
    ).id == r.id
    assert repo.get_by_publish_bot_id(
        "src-bot.pub.1", "emp001", "dev", publish_status="RELEASE"
    ).id == r.id
    assert repo.get_by_publish_bot_id_and_version(
        "src-bot.pub.1", "emp001", 2, "dev"
    ).id == r.id
    assert [x.id for x in repo.list_by_source_bot(1, "dev")] == [r.id]
    assert [x.id for x in repo.list_by_status("RELEASE", "dev")] == [
        r.id
    ]
    assert repo.get_by_last_pub_id(42).id == r.id


def test_query_env_isolation(repo):
    repo.insert(_data(env="dev"))
    repo.insert(_data(env="pre"))
    assert len(repo.list_by_owner("emp001", "dev")) == 1
    assert len(repo.list_by_owner("emp001", "pre")) == 1


# ── draft lookup (owner-agnostic, for teclaw artifact recording) ─────

_DRAFT = PublishStatus.DRAFT  # "draft" — the stored enum value


def test_get_draft_by_publish_bot_id_returns_latest_draft(repo):
    # Two DRAFT versions for the same publish_bot_id → highest version wins.
    repo.insert(_data(publish_bot_id="t-bot", status=_DRAFT, version=1))
    hi = repo.insert(_data(publish_bot_id="t-bot", status=_DRAFT, version=2))
    got = repo.get_draft_by_publish_bot_id("t-bot", "dev")
    assert got is not None and got.id == hi.id


def test_get_draft_by_publish_bot_id_is_owner_agnostic(repo):
    # No owner filter: a draft created under a different owner is still found.
    r = repo.insert(
        _data(publish_bot_id="t-bot", owner_id="creator", status=_DRAFT)
    )
    assert repo.get_draft_by_publish_bot_id("t-bot", "dev").id == r.id


def test_get_draft_by_publish_bot_id_skips_non_draft_and_other_env(repo):
    # Non-DRAFT status and other env are both excluded → None.
    repo.insert(
        _data(publish_bot_id="t-bot", status=PublishStatus.RELEASED, version=3)
    )
    repo.insert(_data(publish_bot_id="t-bot", status=_DRAFT, env="pre"))
    assert repo.get_draft_by_publish_bot_id("t-bot", "dev") is None
    # No row at all → None.
    assert repo.get_draft_by_publish_bot_id("missing", "dev") is None


# ── optimistic-lock updates ─────────────────────────────────────────

def test_update_status_optimistic_match(repo):
    r = repo.insert(_data(status="DRAFT"))
    out = repo.update_status(r.id, "RELEASE", source_status="DRAFT")
    assert out is not None
    assert out.status == "RELEASE"


def test_update_status_optimistic_mismatch_returns_none(repo):
    r = repo.insert(_data(status="DRAFT"))
    # source_status doesn't match current → 0 rows → None, no change.
    out = repo.update_status(r.id, "RELEASE", source_status="RELEASE")
    assert out is None
    assert repo.get_by_id(r.id).status == "DRAFT"


def test_update_status_no_guard(repo):
    r = repo.insert(_data(status="DRAFT"))
    out = repo.update_status(r.id, "FAIL")
    assert out.status == "FAIL"


def test_update_status_with_ext(repo):
    r = repo.insert(_data(status="DRAFT"))
    out = repo.update_status_with_ext(
        r.id, "RELEASE", {"trace": "abc"}, source_status="DRAFT"
    )
    assert out.status == "RELEASE"
    assert out.ext == {"trace": "abc"}
    # mismatch path
    assert repo.update_status_with_ext(
        r.id, "X", {"y": 1}, source_status="DRAFT"
    ) is None


def test_update_version_and_last_pub_id(repo):
    r = repo.insert(_data(version=1))
    out = repo.update_version(r.id, 5, status="RELEASE")
    assert out.version == 5 and out.status == "RELEASE"
    out2 = repo.update_last_pub_id(r.id, 777)
    assert out2.last_pub_id == 777


# ── delete (single hard DELETE) ─────────────────────────────────────

def test_delete_is_hard(repo):
    r = repo.insert(_data())
    assert repo.delete(r.id) is True
    assert repo.get_by_id(r.id) is None
    assert repo.delete(r.id) is False



def test_get_latest_by_source_bot_id_and_owner_and_status_returns_latest_id(repo):
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="dev"))
    latest = repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="dev", version=2))
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="FAILED", env="dev", version=3))
    repo.insert(_data(source_bot_id="src-1", owner_id="other", status="SUCCESS", env="dev", version=4))
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="pre", version=5))

    got = repo.get_latest_by_source_bot_id_and_owner_and_status(
        source_bot_id="src-1",
        owner_id="emp001",
        status="SUCCESS",
        env="dev",
    )

    assert got is not None
    assert got.id == latest.id


def test_get_latest_by_source_bot_id_and_owner_and_status_returns_none_when_missing(repo):
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="dev"))

    got = repo.get_latest_by_source_bot_id_and_owner_and_status(
        source_bot_id="src-missing",
        owner_id="emp001",
        status="SUCCESS",
        env="dev",
    )

    assert got is None


def test_get_latest_success_by_source_bot_id_owner_agnostic_returns_latest(repo):
    """Multi-instance bot_id → binding_id resolution: latest success row,
    owner-agnostic (org bot entity_id may differ from create owner_id)."""
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="success", env="dev"))
    latest = repo.insert(
        _data(source_bot_id="src-1", owner_id="other-owner", status="success", env="dev", version=2)
    )
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="failed", env="dev", version=3))
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="success", env="pre", version=4))

    got = repo.get_latest_success_by_source_bot_id("src-1", "dev")

    assert got is not None
    assert got.id == latest.id


def test_get_latest_success_by_source_bot_id_returns_none_when_no_success(repo):
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="failed", env="dev"))

    assert repo.get_latest_success_by_source_bot_id("src-1", "dev") is None
    assert repo.get_latest_success_by_source_bot_id("src-missing", "dev") is None


# ── config_artifact OSS offload ─────────────────────────────────────
#
# When ``ext['config_artifact']`` serializes past the inline TEXT-column
# threshold, the repository stashes its JSON in object storage and stores a
# self-describing marker instead, transparently re-inlining on read. The fake
# below is a minimal in-memory ObjectStoragePlugin covering the slice the repo
# uses (put/get/delete/list).


class _FakeOSS:
    """In-memory object store; records put_object calls for assertions."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.put_calls = 0

    def put_object(self, key: str, content) -> bool:
        self.put_calls += 1
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.store[key] = content
        return True

    def get_object(self, key: str):
        return self.store.get(key)

    def delete_object(self, key: str) -> bool:
        self.store.pop(key, None)
        return True

    def list_objects(self, prefix: str, max_keys: int = 1000):
        return [k for k in self.store if k.startswith(prefix)][:max_keys]


class _FakeOSSPutFails(_FakeOSS):
    def put_object(self, key: str, content) -> bool:
        self.put_calls += 1
        return False


def _repo_with(engine_tmp, oss):
    engine = create_engine(
        f"sqlite:///{engine_tmp / 'bp.db'}",
        connect_args={"check_same_thread": False},
    )
    BotPublishModel.__table__.create(engine)
    return BotPublishRepository(
        _FileSqliteDB(engine), offload=ConfigArtifactOffloader(oss)
    )


@pytest.fixture
def oss():
    return _FakeOSS()


@pytest.fixture
def repo_oss(tmp_path, oss):
    return _repo_with(tmp_path, oss)


def _big_artifact():
    """A serialized artifact comfortably over the inline threshold."""
    return {
        "schema_version": 4,
        "engine_type": "openclaw",
        "blob": "x" * (ARTIFACT_OSS_THRESHOLD_BYTES + 2048),
    }


def _small_artifact():
    return {"schema_version": 4, "engine_type": "openclaw", "blob": "tiny"}


def _raw_ext(repo, publish_id):
    """The ext JSON actually persisted in the column (unresolved), as a dict."""
    import json

    with repo._db.orm_session() as db:
        row = (
            db.query(BotPublishModel.ext)
            .filter(BotPublishModel.id == publish_id)
            .first()
        )
    return json.loads(row[0]) if row and row[0] else None


def test_small_artifact_stays_inline(repo_oss, oss):
    art = _small_artifact()
    rec = repo_oss.insert(_data(ext={"config_artifact": art}))
    # No offload: nothing written to object storage, no marker in the column.
    assert oss.put_calls == 0
    assert oss.store == {}
    raw = _raw_ext(repo_oss, rec.id)
    assert ARTIFACT_KEY in raw and ARTIFACT_OSS_MARKER not in raw
    # Read path returns the artifact unchanged.
    assert repo_oss.get_by_id(rec.id).ext["config_artifact"] == art


def test_large_artifact_offloaded_and_reinlined(repo_oss, oss):
    art = _big_artifact()
    rec = repo_oss.insert(_data(ext={"config_artifact": art, "keep": "me"}))
    # Offloaded exactly once, under this record's prefix.
    assert oss.put_calls == 1
    keys = list(oss.store)
    assert len(keys) == 1
    assert keys[0].startswith(f"teclaw/dev/bot_publish/{rec.id}/")
    # Column holds the marker (self-describing), NOT the inline artifact.
    raw = _raw_ext(repo_oss, rec.id)
    assert ARTIFACT_KEY not in raw
    marker = raw[ARTIFACT_OSS_MARKER]
    assert marker["offloaded"] is True
    assert marker["oss_key"] == keys[0]
    assert marker["size_bytes"] > ARTIFACT_OSS_THRESHOLD_BYTES
    assert "note" in marker
    assert raw["keep"] == "me"  # sibling ext fields untouched
    # Every read path re-inlines the full artifact and hides the marker.
    for got in (
        repo_oss.get_by_id(rec.id),
        repo_oss.get_by_publish_bot_id("src-bot.pub.1", "emp001", "dev"),
        repo_oss.list_by_owner("emp001", "dev")[0],
    ):
        assert got.ext["config_artifact"] == art
        assert got.ext["keep"] == "me"
        assert ARTIFACT_OSS_MARKER not in got.ext


def test_offload_via_update_status_with_ext(repo_oss, oss):
    rec = repo_oss.insert(_data(status="DRAFT", ext={"k": "v"}))
    assert oss.put_calls == 0
    out = repo_oss.update_status_with_ext(
        rec.id, "BUILT", {"config_artifact": _big_artifact()},
        source_status="DRAFT",
    )
    assert out.status == "BUILT"
    assert out.ext["config_artifact"] == _big_artifact()
    assert oss.put_calls == 1


def test_rejected_update_does_not_upload(repo_oss, oss):
    rec = repo_oss.insert(_data(status="DRAFT"))
    # source_status mismatch → 0 rows updated → artifact must NOT be uploaded.
    out = repo_oss.update_status_with_ext(
        rec.id, "BUILT", {"config_artifact": _big_artifact()},
        source_status="BUILT",
    )
    assert out is None
    assert oss.put_calls == 0
    assert oss.store == {}


def test_rewrite_new_content_and_delete_sweeps_all(repo_oss, oss):
    rec = repo_oss.insert(_data(status="DRAFT", ext={"config_artifact": _big_artifact()}))
    v2 = {**_big_artifact(), "blob": "y" * (ARTIFACT_OSS_THRESHOLD_BYTES + 4096)}
    repo_oss.update_status_with_ext(
        rec.id, "DRAFT", {"config_artifact": v2}, source_status="DRAFT",
    )
    # Content-addressed: two distinct versions coexist under the prefix.
    assert len(oss.store) == 2
    # Latest read returns the newest content.
    assert repo_oss.get_by_id(rec.id).ext["config_artifact"] == v2
    # Delete sweeps every version under the record's prefix.
    assert repo_oss.delete(rec.id) is True
    assert oss.store == {}


def test_delete_without_offload_leaves_store_untouched(repo_oss, oss):
    rec = repo_oss.insert(_data(ext={"config_artifact": _small_artifact()}))
    assert oss.store == {}
    assert repo_oss.delete(rec.id) is True
    assert oss.store == {}


def test_offload_put_failure_raises_and_rolls_back(tmp_path):
    fake = _FakeOSSPutFails()
    repo = _repo_with(tmp_path, fake)
    with pytest.raises(RuntimeError):
        repo.insert(_data(ext={"config_artifact": _big_artifact()}))
    # The insert transaction rolled back — no half-written row persisted.
    assert repo.list_by_owner("emp001", "dev") == []


def test_write_strips_stale_marker_end_to_end(repo_oss, oss):
    # A caller hands ext carrying BOTH a fresh inline artifact and a stale marker
    # (the degrade-path shape). The write must persist only the inline artifact,
    # and the read must never fetch the stale key. Exercised through the public
    # API, not the private helper.
    rec = repo_oss.insert(_data(status="DRAFT"))
    repo_oss.update_status_with_ext(
        rec.id, "DRAFT",
        {
            "config_artifact": _small_artifact(),
            "config_artifact_oss": {"oss_key": "stale/key", "offloaded": True},
        },
        source_status="DRAFT",
    )
    raw = _raw_ext(repo_oss, rec.id)
    assert ARTIFACT_OSS_MARKER not in raw
    assert raw["config_artifact"] == _small_artifact()
    got = repo_oss.get_by_id(rec.id)
    assert got.ext["config_artifact"] == _small_artifact()
    assert oss.store == {}  # stale key never fetched


def test_update_source_none_existing_row_uploads(repo_oss, oss):
    # source_status=None against an existing row still takes the write (affected
    # > 0) → the large artifact IS uploaded.
    rec = repo_oss.insert(_data(status="DRAFT"))
    out = repo_oss.update_status_with_ext(
        rec.id, "DRAFT", {"config_artifact": _big_artifact()}, source_status=None,
    )
    assert out is not None
    assert oss.put_calls == 1
    assert out.ext["config_artifact"] == _big_artifact()


def test_update_source_none_missing_row_does_not_upload(repo_oss, oss):
    # The exact round-2 orphan gap: source_status=None against a nonexistent
    # publish_id → affected == 0 → must NOT upload (no row to reference it).
    out = repo_oss.update_status_with_ext(
        999999, "DRAFT", {"config_artifact": _big_artifact()}, source_status=None,
    )
    assert out is None
    assert oss.put_calls == 0
    assert oss.store == {}


def test_delete_tolerates_sweep_failure(tmp_path):
    # delete()'s best-effort cleanup must never fail the DB delete, even if the
    # object-storage sweep raises (e.g. an impl lacking or breaking list_objects).
    class _FakeOSSListRaises(_FakeOSS):
        def list_objects(self, prefix: str, max_keys: int = 1000):
            raise RuntimeError("list_objects unavailable")

    fake = _FakeOSSListRaises()
    repo = _repo_with(tmp_path, fake)
    rec = repo.insert(_data(ext={"config_artifact": _big_artifact()}))
    assert fake.put_calls == 1  # it did offload
    # Sweep raises inside delete → swallowed → DB delete still reports success.
    assert repo.delete(rec.id) is True
    assert repo.get_by_id(rec.id) is None


def _artifact_of_json_size(nbytes):
    """A ``{"b": "x"*k}`` artifact whose JSON is exactly ``nbytes`` bytes."""
    import json

    base = len(json.dumps({"b": ""}, ensure_ascii=False).encode("utf-8"))
    return {"b": "x" * (nbytes - base)}


def test_threshold_boundary(tmp_path):
    fake = _FakeOSS()
    repo = _repo_with(tmp_path, fake)
    # Exactly at the threshold → inline (the check is ``> threshold``).
    at = _artifact_of_json_size(ARTIFACT_OSS_THRESHOLD_BYTES)
    repo.insert(_data(publish_bot_id="b-at", ext={"config_artifact": at}))
    assert fake.put_calls == 0
    # One byte over → offloaded.
    over = _artifact_of_json_size(ARTIFACT_OSS_THRESHOLD_BYTES + 1)
    repo.insert(_data(publish_bot_id="b-over", ext={"config_artifact": over}))
    assert fake.put_calls == 1
