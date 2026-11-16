"""``copy_render_screens``: the overwrite converge a copy's render-screen set runs.

The service owns the destination read + create/update/delete; this proves the
classification each row lands in — a new name is ``created``, a same-name
same-url row is ``unchanged`` (the idempotent retry the apply-engine
convergence criterion rests on), a same-name changed-url row is ``updated``,
and a name present on the destination but no longer declared is ``removed``.
Re-applying an unchanged, already-converged set performs no writes at all,
which is the property the materialiser's ``is_noop`` reflects and the test
asserts at the repo boundary rather than trusting the outcome strings.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord
from agentclaw.community.core.bot_management.render_screen.services.render_screen_service import (
    RenderScreenService,
)
from agentclaw.community.core.bot_management.render_screen_service_protocol import (
    RenderScreenRow,
)


def _record(*, id: int, name: str, cdn_url: str, bot_id: str = "b_dst", owner_id: str = "o_dst") -> RenderScreenRecord:
    return RenderScreenRecord(
        id=id,
        bot_id=bot_id,
        owner_id=owner_id,
        name=name,
        cdn_url=cdn_url,
        env="dev",
        creator_id="actor",
        is_delete=0,
        gmt_create=datetime(2026, 1, 1),
        gmt_modified=datetime(2026, 1, 1),
    )


def _bot():
    return {"id": 101, "bot_id": "b_dst", "owner_id": "o_dst", "active_engine": "claude_code", "template_type": "normalCC"}


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_by_id.return_value = None
    return r


@pytest.fixture
def bot_repo():
    r = MagicMock()
    r.get_by_id.return_value = _bot()
    return r


@pytest.fixture
def service(repo, bot_repo):
    return RenderScreenService(
        repository=repo,
        bot_repository=bot_repo,
        collaborator_repository=MagicMock(),
    )


def _rows(*pairs: tuple[str, str]) -> tuple[RenderScreenRow, ...]:
    return tuple(RenderScreenRow(name=n, cdn_url=u) for n, u in pairs)


def test_a_new_row_is_created(service, repo):
    repo.list_by_bot_id.return_value = []
    result = service.copy_render_screens(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", rows=_rows(("lib", "https://cdn/x.js"))
    )
    assert {r.name: r.outcome for r in result.rows} == {"lib": "created"}
    assert [(c.kwargs["name"], c.kwargs["cdn_url"]) for c in repo.insert.call_args_list] == [("lib", "https://cdn/x.js")]
    repo.update_by_id.assert_not_called()
    repo.delete_by_id.assert_not_called()


def test_an_unchanged_row_writes_nothing(service, repo):
    repo.list_by_bot_id.return_value = [_record(id=7, name="lib", cdn_url="https://cdn/x.js")]
    result = service.copy_render_screens(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", rows=_rows(("lib", "https://cdn/x.js"))
    )
    names = {r.name: r.outcome for r in result.rows}
    assert names == {"lib": "unchanged"}
    repo.insert.assert_not_called()
    repo.update_by_id.assert_not_called()
    repo.delete_by_id.assert_not_called()


def test_a_same_name_changed_url_is_updated(service, repo):
    repo.list_by_bot_id.return_value = [_record(id=7, name="lib", cdn_url="https://cdn/old.js")]
    result = service.copy_render_screens(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", rows=_rows(("lib", "https://cdn/new.js"))
    )
    assert {r.name: r.outcome for r in result.rows} == {"lib": "updated"}
    assert repo.update_by_id.call_args.kwargs == {"record_id": 7, "name": "lib", "cdn_url": "https://cdn/new.js"}
    repo.insert.assert_not_called()
    repo.delete_by_id.assert_not_called()


def test_a_no_longer_declared_row_is_removed(service, repo):
    repo.list_by_bot_id.return_value = [
        _record(id=5, name="keep", cdn_url="https://cdn/k.js"),
        _record(id=9, name="gone", cdn_url="https://cdn/g.js"),
    ]
    result = service.copy_render_screens(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", rows=_rows(("keep", "https://cdn/k.js"))
    )
    names = {r.name: r.outcome for r in result.rows}
    assert names == {"keep": "unchanged", "gone": "removed"}
    assert repo.delete_by_id.call_args.kwargs == {"record_id": 9}


def test_re_applying_an_unchanged_set_writes_nothing(service, repo):
    """Already-converged: every row unchanged, nothing removed, zero repo writes."""
    repo.list_by_bot_id.return_value = [
        _record(id=1, name="a", cdn_url="https://cdn/a.js"),
        _record(id=2, name="b", cdn_url="https://cdn/b.js"),
    ]
    service.copy_render_screens(
        bot_id="b_dst", owner_id="o_dst", actor_id="a",
        rows=_rows(("a", "https://cdn/a.js"), ("b", "https://cdn/b.js")),
    )
    repo.insert.assert_not_called()
    repo.update_by_id.assert_not_called()
    repo.delete_by_id.assert_not_called()


def test_an_empty_overwrite_removes_everything(service, repo):
    repo.list_by_bot_id.return_value = [
        _record(id=1, name="a", cdn_url="https://cdn/a.js"),
        _record(id=2, name="b", cdn_url="https://cdn/b.js"),
    ]
    result = service.copy_render_screens(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", rows=()
    )
    assert {r.name: r.outcome for r in result.rows} == {"a": "removed", "b": "removed"}
    assert sorted(c.kwargs["record_id"] for c in repo.delete_by_id.call_args_list) == [1, 2]


def test_rows_land_under_the_destination_owner(service, repo, bot_repo):
    """The owner is taken from the destination bot's row, not the per-row field."""
    bot_repo.get_by_id.return_value = {"id": 101, "bot_id": "b_dst", "owner_id": "the_dst_owner",
                                        "active_engine": "claude_code", "template_type": "normalCC"}
    repo.list_by_bot_id.return_value = []
    service.copy_render_screens(
        bot_id="b_dst", owner_id="ignored", actor_id="a", rows=_rows(("lib", "https://cdn/x.js"))
    )
    assert repo.insert.call_args.kwargs["owner_id"] == "the_dst_owner"
