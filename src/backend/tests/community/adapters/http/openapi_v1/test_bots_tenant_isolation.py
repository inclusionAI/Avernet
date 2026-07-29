"""Cross-tenant isolation for the public bots surface (Track B, Task 9).

The public handlers delegate to the bot repository/services, which are already
tenant-scoped by the Track A guard (a ``do_orm_execute`` listener on ``Session``
plus a ``before_insert`` stamp). This test exercises that guard through a real
``BotRepository`` + SQLite DB: a bot created under tenant A is invisible and
immutable from tenant B via every operation the public read/update/delete routes
rely on. That is the mechanism behind the endpoints' "a bot in another tenant is
never reachable" guarantee (cross-tenant lookups return nothing → the handler's
``get_bot`` raises ``BotNotFoundError`` → a masked 404).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.bot_repository import BotRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class _DB:
    def __init__(self, engine):
        self._Session = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bots.db'}",
        connect_args={"check_same_thread": False},
    )
    for m in (BotModel, BotPublishModel, EntityDeviceBinding, BotCollaboratorModel):
        m.__table__.create(engine)
    return BotRepository(_DB(engine))


def _bot(**ov):
    base = dict(
        bot_id="bot-A", bot_name="Alpha", bot_desc="d", entity_id="e",
        entity_type="staff", creator_id="u1", owner_id="u1", status="ACTIVE",
        owner_name="Al", active_engine="teclaw",
    )
    base.update(ov)
    return base


@pytest.fixture
def seeded(repo):
    """A bot owned by u1, stamped into tenant A by the insert guard."""
    with avernet_tenant_scope(TENANT_A):
        repo.insert(_bot())
    return repo


def test_same_tenant_can_read(seeded):
    with avernet_tenant_scope(TENANT_A):
        assert seeded.get_by_id_and_owner("bot-A", "u1") is not None
        assert seeded.list_by_conditions(owner_id="u1")[0] == 1


def test_cross_tenant_get_is_not_found(seeded):
    with avernet_tenant_scope(TENANT_B):
        assert seeded.get_by_id_and_owner("bot-A", "u1") is None


def test_cross_tenant_list_is_empty(seeded):
    with avernet_tenant_scope(TENANT_B):
        total, items = seeded.list_by_conditions(owner_id="u1")
        assert total == 0 and items == []


def test_cross_tenant_update_is_noop_and_leaves_original(seeded):
    with avernet_tenant_scope(TENANT_B):
        seeded.update_by_owner("bot-A", "u1", {"bot_name": "HACKED"})
    with avernet_tenant_scope(TENANT_A):
        bot = seeded.get_by_id_and_owner("bot-A", "u1")
        assert bot is not None and bot["bot_name"] == "Alpha"  # untouched


def test_cross_tenant_delete_is_noop_and_leaves_original(seeded):
    with avernet_tenant_scope(TENANT_B):
        seeded.soft_delete_by_owner("bot-A", "u1")
    with avernet_tenant_scope(TENANT_A):
        assert seeded.get_by_id_and_owner("bot-A", "u1") is not None  # still live
