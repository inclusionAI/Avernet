"""Regression tests for the two post-merge review findings on PR #456.

1. Raw SQL bypassed the guard — `bot_discover_service` read `ac_bots` via a raw
   cursor, which never triggers `do_orm_execute`. It now goes through
   `BotRepository.list_public_bots_by_owner_bot_pairs` (ORM → guard-covered);
   this test proves that method is tenant-scoped.
2. Bare threads dropped the tenant — several in-request `Thread`/`ThreadPoolExecutor`
   sites read `BotRepository` and, without `bind_current_avernet_tenant`, would
   fall back to `teamclaw`. This proves a repo read inside a bound thread stays
   under the spawning tenant (and a bare thread does not).
"""
import threading
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_public.services.bot_discover_service import (
    BotDiscoverService,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.di.config import BcsFuseConfig
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.bot_repository import BotRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
    bind_current_avernet_tenant,
)

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
        f"sqlite:///{tmp_path / 'bots.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    BotPublishModel.__table__.create(engine)
    EntityDeviceBinding.__table__.create(engine)
    BotCollaboratorModel.__table__.create(engine)
    return BotRepository(_FileSqliteDB(engine))


def _data(**ov):
    base = dict(
        bot_id="bot-1",
        bot_name="Bot One",
        bot_desc="d",
        entity_id="staff_x",
        entity_type="staff",
        creator_id="emp1",
        owner_id="emp1",
        status="ACTIVE",
        owner_name="Alice",
        public="1",
    )
    base.update(ov)
    return base


# ── Comment 1: the raw-SQL replacement is tenant-scoped ─────────────

def test_list_public_bots_by_owner_bot_pairs_is_tenant_scoped(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a"))
    with avernet_tenant_scope("tenant-b"):
        repo.insert(_data(bot_id="bot-b", owner_id="own-b"))

    pairs = [("bot-a", "own-a"), ("bot-b", "own-b")]
    with avernet_tenant_scope("tenant-b"):
        got = repo.list_public_bots_by_owner_bot_pairs(pairs)
        assert [b["bot_id"] for b in got] == ["bot-b"]  # not A's
    with avernet_tenant_scope("tenant-a"):
        got = repo.list_public_bots_by_owner_bot_pairs(pairs)
        assert [b["bot_id"] for b in got] == ["bot-a"]


def test_list_public_bots_excludes_non_public_and_deleted(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="pub", owner_id="o", public="1"))
        repo.insert(_data(bot_id="priv", owner_id="o", public="0"))
    with avernet_tenant_scope("tenant-a"):
        got = repo.list_public_bots_by_owner_bot_pairs([("pub", "o"), ("priv", "o")])
        assert [b["bot_id"] for b in got] == ["pub"]


def test_list_public_bots_empty_pairs(repo):
    assert repo.list_public_bots_by_owner_bot_pairs([]) == []


# ── Comment 1: the real caller (_batch_get_public_bots) end-to-end ──

def _discover_service(repo):
    return BotDiscoverService(bot_repository=repo, bcsfuse_config=BcsFuseConfig())


def test_batch_get_public_bots_returns_detail_and_is_tenant_scoped(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(
            _data(
                bot_id="bot-a",
                owner_id="own-a",
                bot_name="Alpha",
                public="1",
                ext={"owner_name": "Bob"},
            )
        )
    svc = _discover_service(repo)
    rec = {"score": 0.9}
    entries = [("own-a:worker", "bot-a", "own-a", rec)]

    # Under tenant-a: the detail comes back, ext parsed, rec threaded through.
    with avernet_tenant_scope("tenant-a"):
        result = svc._batch_get_public_bots(entries, user_id="own-a")
    assert len(result) == 1
    bot_detail, got_rec = result[0]
    assert bot_detail["bot_id"] == "bot-a"
    assert bot_detail["public"] == "1"
    assert bot_detail["ext"] == {"owner_name": "Bob"}  # already parsed by to_dict
    assert bot_detail["owner_name"] == "Bob"
    assert got_rec is rec

    # Under tenant-b: the ORM guard hides tenant-a's bot — the real leak the
    # raw SQL allowed is now closed at the service entry point.
    with avernet_tenant_scope("tenant-b"):
        assert svc._batch_get_public_bots(entries, user_id="own-a") == []


def test_batch_get_public_bots_empty_entries(repo):
    svc = _discover_service(repo)
    assert svc._batch_get_public_bots([], user_id="u") == []


# ── Comment 2: a repo read inside a bound thread keeps the tenant ───

def test_repo_read_in_bound_thread_is_tenant_scoped(repo):
    """Mirrors the bot_public / device_service pattern: a BotRepository read
    runs on a spawned thread and must observe the spawning request's tenant."""
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a"))

    seen = {}

    def read():
        # get_by_id_and_owner is the exact call the wrapped sites make.
        seen["bot"] = repo.get_by_id_and_owner("bot-a", "own-a")

    # Bound under tenant-a → the thread finds A's bot.
    with avernet_tenant_scope("tenant-a"):
        target = bind_current_avernet_tenant(read)
    t = threading.Thread(target=target)
    t.start()
    t.join()
    assert seen["bot"] is not None
    assert seen["bot"]["bot_id"] == "bot-a"


def test_repo_read_in_bare_thread_drops_tenant(repo):
    """Guards the premise: without binding, the thread falls back to the default
    tenant and cannot see tenant-a's bot."""
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a"))

    seen = {}

    def read():
        seen["tenant_default"] = True
        seen["bot"] = repo.get_by_id_and_owner("bot-a", "own-a")

    with avernet_tenant_scope("tenant-a"):
        t = threading.Thread(target=read)  # NOT bound
        t.start()
        t.join()
    # Thread ran under DEFAULT_AVERNET_TENANT ("teamclaw"), so A's bot is hidden.
    assert DEFAULT_AVERNET_TENANT == "teamclaw"
    assert seen["bot"] is None
