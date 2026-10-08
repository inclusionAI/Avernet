"""#722 repository seam: Bot Skill reads use the real Track A tenant guard."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import BotSkillInstallation
from agentclaw.community.core.repository.implementations.skill_center.skill import SkillRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env


class _Database:
    def __init__(self, engine) -> None:
        self._session = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        session = self._session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _repo(tmp_path) -> tuple[SkillRepository, _Database]:
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    Base.metadata.create_all(engine)
    db = _Database(engine)
    return SkillRepository(db), db


def _page(repo, *, members=(), **overrides):
    return repo.list_bot_skills(
        **{
            "bot_id": "bot",
            "user_id": "owner",
            "skill_set_member_ids": members,
            "page": 1,
            "page_size": 20,
            "active": None,
            "keyword": None,
            "source": None,
            **overrides,
        }
    )


def test_exact_bot_skill_query_is_tenant_scoped_and_reports_desired_state(tmp_path):
    repo, _db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-a"):
        created = repo.create(
            {
                "name": "forecast",
                "description": "Daily Forecast",
                "git_path": "local://forecast",
                "user_id": "owner",
                "bolt_id": "bot",
                "tags": ["weather"],
            }
        )
        repo.create(
            {
                "name": "market",
                "git_path": "git://market",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        total, rows = _page(repo, keyword="FORE")
        # "market" is filtered by the keyword, not by its source prefix — the
        # next test is what holds the prefix rule.
        assert total == 1
        assert rows[0]["id"] == created["id"]
        assert rows[0]["active"] is False
        assert _page(repo)[0] == 2

    with avernet_tenant_scope("tenant-b"):
        total, rows = _page(repo)
        assert total == 0 and rows == []
        assert (
            repo.get_bot_local_skill(
                skill_id=created["id"], bot_id="bot", user_id="owner"
            )
            is None
        )


def test_bot_owned_rows_are_listed_whatever_their_source_prefix(tmp_path):
    """The listing is the Bot's Skills, not the Bot's uploads."""
    repo, _db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-a"):
        for name, git_path in (
            ("forecast", "local://forecast"),
            ("market", "git://market"),
            ("space", "center://space-uuid"),
        ):
            repo.create(
                {
                    "name": name,
                    "git_path": git_path,
                    "user_id": "owner",
                    "bolt_id": "bot",
                }
            )
        total, rows = _page(repo)

        assert total == 3
        assert {row["name"] for row in rows} == {"forecast", "market", "space"}

        local_total, local_rows = _page(repo, source="LOCAL")

        assert local_total == 1
        assert [row["name"] for row in local_rows] == ["forecast"]


def test_a_bridged_row_is_listed_even_though_the_bot_does_not_own_it(tmp_path):
    """A SkillSet member names another owner; that is the whole point."""
    repo, _db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-a"):
        mine = repo.create(
            {
                "name": "forecast",
                "git_path": "local://forecast",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        shared = repo.create(
            {
                "name": "shared",
                "git_path": "git://shared",
                "user_id": "someone-else",
                "bolt_id": "another-bot",
            }
        )
        repo.create(
            {
                "name": "unreachable",
                "git_path": "git://unreachable",
                "user_id": "someone-else",
                "bolt_id": "another-bot",
            }
        )
        total, rows = _page(repo, members=(int(shared["id"]),))
        local_total, local_rows = _page(
            repo, members=(int(shared["id"]),), source="LOCAL"
        )

    assert total == 2
    assert {row["id"] for row in rows} == {mine["id"], shared["id"]}
    assert local_total == 1
    assert [row["id"] for row in local_rows] == [mine["id"]]


def test_a_skill_both_owned_and_bridged_is_listed_once(tmp_path):
    """`total` counts Skills, so the two reach-routes must not double up."""
    repo, _db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-a"):
        mine = repo.create(
            {
                "name": "forecast",
                "git_path": "local://forecast",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        other = repo.create(
            {
                "name": "other",
                "git_path": "git://other",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        # The Bot owns both rows *and* a SkillSet bridges them back.
        total, rows = _page(
            repo, members=(int(mine["id"]), int(other["id"]))
        )

    assert total == 2
    assert [row["id"] for row in rows] == sorted(
        [mine["id"], other["id"]], key=int, reverse=True
    )


def test_a_bridged_id_cannot_reach_across_the_tenant_guard(tmp_path):
    """Passing an id in is not a way around the read guard."""
    repo, _db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-b"):
        theirs = repo.create(
            {
                "name": "theirs",
                "git_path": "git://theirs",
                "user_id": "someone-else",
                "bolt_id": "another-bot",
            }
        )
        # Positive control: inside its own tenant the very same id is bridged
        # in, so the empty answer below is the guard and not a dead branch.
        assert _page(repo, members=(int(theirs["id"]),))[0] == 1

    with avernet_tenant_scope("tenant-a"):
        total, rows = _page(repo, members=(int(theirs["id"]),))

    assert total == 0 and rows == []


def test_active_filter_and_paging_apply_to_the_whole_merged_list(tmp_path):
    """`active` filters before the page is cut, so `total` counts matches."""
    repo, db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-a"):
        mine = repo.create(
            {
                "name": "mine",
                "git_path": "local://mine",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        bridged = repo.create(
            {
                "name": "bridged",
                "git_path": "git://bridged",
                "user_id": "someone-else",
                "bolt_id": "another-bot",
            }
        )
        with db.orm_session() as session:
            session.add(
                BotSkillInstallation(
                    bot_id="bot",
                    owner_id="owner",
                    skill_id=int(bridged["id"]),
                    env=get_current_env(),
                    avernet_tenant="tenant-a",
                )
            )

        members = (int(bridged["id"]),)
        active_total, active_rows = _page(repo, members=members, active=True)
        inactive_total, inactive_rows = _page(repo, members=members, active=False)
        first_total, first_rows = _page(repo, members=members, page=1, page_size=1)
        second_total, second_rows = _page(repo, members=members, page=2, page_size=1)

    assert active_total == 1 and [row["id"] for row in active_rows] == [bridged["id"]]
    assert active_rows[0]["active"] is True
    assert inactive_total == 1 and [row["id"] for row in inactive_rows] == [mine["id"]]
    # `total` is the size of the merged, filtered set — not of one page. The
    # pages must also partition it in a stable order (newest id first here,
    # both rows sharing a gmt_modified second), or clients repeat and skip rows.
    assert first_total == second_total == 2
    assert [row["id"] for row in first_rows] == [bridged["id"]]
    assert [row["id"] for row in second_rows] == [mine["id"]]


def test_a_directly_installed_shared_skill_is_listed(tmp_path):
    """The third way a Bot reaches a Skill, and the one easiest to miss.

    Activating a shared Repo Skill that belongs to no SkillSet writes an
    Installation row and nothing else — the Skill row still names another owner
    and another Bot. ``list_bot_installed_assets`` puts it in the runtime
    projection, so a listing that admitted only Bot-owned and SkillSet-bridged
    rows would hide a Skill the Bot is actually running.
    """
    repo, db = _repo(tmp_path)

    with avernet_tenant_scope("tenant-a"):
        mine = repo.create(
            {
                "name": "mine",
                "git_path": "local://mine",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        direct = repo.create(
            {
                "name": "activated-directly",
                "git_path": "git://market/activated-directly",
                "user_id": "someone-else",
                "bolt_id": "another-bot",
            }
        )
        repo.create(
            {
                "name": "never-activated",
                "git_path": "git://market/never-activated",
                "user_id": "someone-else",
                "bolt_id": "another-bot",
            }
        )
        with db.orm_session() as session:
            session.add(
                BotSkillInstallation(
                    bot_id="bot",
                    owner_id="owner",
                    skill_id=int(direct["id"]),
                    env=get_current_env(),
                    avernet_tenant="tenant-a",
                )
            )
        # No SkillSet bridges anything: the Installation row is the only tie.
        total, rows = _page(repo)
        active_total, active_rows = _page(repo, active=True)

    assert total == 2
    assert {row["id"] for row in rows} == {mine["id"], direct["id"]}
    assert active_total == 1
    assert [row["id"] for row in active_rows] == [direct["id"]]
