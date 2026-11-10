"""The bot ceiling counts identities, not the Bots that borrow one.

Real SQLite through the real ``SqliteDB`` plugin and the real repository queries.
The exclusion lives in SQL, so a mocked repository would prove nothing: the
numbers below are the actual behaviour every owner experiences.

The scenario that matters: an owner is at their ceiling of 5 ordinary Bots, and
must still be able to add an inheriting one — while an ordinary one is refused.
"""

from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local import database


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    # The plugin caches its engine module-wide; without this every test in the
    # file shares one database and the rows (and the counts) leak across cases.
    database.reset_for_tests()
    monkeypatch.setattr(database, "_DATABASE_URL", f"sqlite:///{tmp_path/'quota.sqlite'}")
    monkeypatch.setenv("ENV", "dev")
    db = database.SqliteDB()
    asyncio.run(db.bootstrap())
    yield BotRepository(db)
    database.reset_for_tests()


def _add(db_session, *, bot_id: str, inheriting_from=None, owner="owner",
         deleted=0, engine="openclaw", bot_type="personal"):
    db_session.add(
        BotModel(
            bot_id=bot_id,
            entity_id=owner,
            entity_type="staff",
            creator_id=owner,
            owner_id=owner,
            is_delete=deleted,
            active_engine=engine,
            bot_type=bot_type,
            identity_bot_id=inheriting_from,
        )
    )
    db_session.commit()


def _seed(repo, *bots):
    with repo._db.orm_session() as session:
        for kwargs in bots:
            _add(session, **kwargs)


class TestQuota:
    def test_inheriting_bots_do_not_consume_quota(self, repo):
        _seed(
            repo,
            {"bot_id": "template"},
            {"bot_id": "w1", "inheriting_from": "template"},
            {"bot_id": "w2", "inheriting_from": "template"},
            {"bot_id": "w3", "inheriting_from": "template"},
        )
        # Four rows exist, but only the identity Bot holds an identity.
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 1

    def test_ordinary_bots_still_consume_quota(self, repo):
        _seed(repo, *[{"bot_id": f"bot{i}"} for i in range(5)])
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 5

    def test_owner_at_ceiling_of_five_sees_five(self, repo):
        """The default ceiling: five ordinary Bots fill it exactly."""
        _seed(repo, *[{"bot_id": f"bot{i}"} for i in range(5)])
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 5

    def test_adding_inheriting_bots_never_moves_the_count(self, repo):
        """Five ordinary Bots plus ten inheriting ones is still five."""
        _seed(
            repo,
            *[{"bot_id": f"bot{i}"} for i in range(5)],
            *[{"bot_id": f"w{i}", "inheriting_from": "bot0"} for i in range(10)],
        )
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 5

    def test_soft_deleted_rows_are_not_counted(self, repo):
        _seed(repo, {"bot_id": "live"}, {"bot_id": "gone", "deleted": 1})
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 1

    def test_other_owners_are_not_counted(self, repo):
        _seed(repo, {"bot_id": "mine"}, {"bot_id": "theirs", "owner": "someone-else"})
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 1

    def test_desktop_and_teclaw_stay_excluded(self, repo):
        _seed(
            repo,
            {"bot_id": "cloud"},
            {"bot_id": "desk", "bot_type": "desktop"},
            {"bot_id": "tc", "engine": "teclaw"},
        )
        assert repo.count_by_owner(
            "owner", exclude_bot_type="desktop", exclude_active_engine="teclaw",
            exclude_identity_inheriting=True,
        ) == 1


class TestSpaceQuota:
    """The Space quota is the path create-with-manifest actually takes.

    A swarm provisions its Bot agents against a Space; if inheriting Bots
    counted there, a space at its ceiling would refuse every agent even
    though none of them holds an identity of its own.
    """

    def test_inheriting_bots_fit_a_personal_space_at_its_ceiling(self, repo):
        _seed(
            repo,
            *[{"bot_id": f"b{i}"} for i in range(5)],
            *[{"bot_id": f"w{i}", "inheriting_from": "b0"} for i in range(3)],
        )
        # Five ordinary Bots fill the personal-space slot; the three that
        # borrow b0's identity add nothing to it.
        assert repo.count_cloud_bots_in_personal_space(
            owner_id="owner", personal_space_id=None,
        ) == 5

    def test_a_team_space_counts_only_identity_holders(self, repo):
        with repo._db.orm_session() as session:
            for i in range(4):
                _add(session, bot_id=f"t{i}")
                session.query(BotModel).filter(
                    BotModel.bot_id == f"t{i}"
                ).update({"space_id": 7})
            for i in range(2):
                _add(session, bot_id=f"tw{i}", inheriting_from="t0")
                session.query(BotModel).filter(
                    BotModel.bot_id == f"tw{i}"
                ).update({"space_id": 7})
            session.commit()
        assert repo.count_cloud_bots_by_space(space_id=7) == 4
