from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_public_risk.application import RiskApplication
from agentclaw.community.core.bot_public_risk.repository import RequestConflict


class Repository:
    def __init__(self):
        self.row = None

    def get_by_request(self, *, env, client_scope, client_request_id):
        if self.row and (self.row.env, self.row.client_scope, self.row.client_request_id) == (env, client_scope, client_request_id):
            return self.row
        return None

    def get(self, *, env, risk_check_id):
        return self.row if self.row and (env, risk_check_id) == (self.row.env, self.row.risk_check_id) else None


class Tasks:
    def __init__(self, repo):
        self.repo = repo
        self.polls = []

    def start(self, record):
        self.repo.row = replace(record, current_target_key="current")
        return self.repo.row, True

    def poll(self, *, env, risk_check_id, manual):
        self.polls.append((env, risk_check_id, manual))
        return "UPDATED"


class Builder:
    def __init__(self):
        self.calls = 0

    def build(self, *, bot, target):
        self.calls += 1
        return {"agent_info": {"agentCode": "existing"}}, "a" * 64


def fixture():
    repo = Repository()
    tasks = Tasks(repo)
    builder = Builder()
    app = RiskApplication(repo, tasks, builder)
    bot = SimpleNamespace(bot_pk=12, current_owner_id="owner", bot={"id": 12, "env": "dev", "owner_id": "owner"})
    return app, repo, tasks, builder, bot


def test_submit_replays_same_request_without_rebuilding_or_creating():
    app, repo, tasks, builder, bot = fixture()
    body = {"client_request_id": "req", "public_scope": "user", "visibility": "public", "view_depts": []}
    first, created = app.submit(bot=bot, actor_id="owner", surface="openapi", body=body)
    replay, replay_created = app.submit(bot=bot, actor_id="owner", surface="openapi", body=body)
    assert created is True and replay_created is False and first.risk_check_id == replay.risk_check_id
    assert builder.calls == 1
    assert first.client_scope == "openapi:owner"
    with pytest.raises(RequestConflict):
        app.submit(bot=bot, actor_id="owner", surface="openapi", body={**body, "public_scope": "agent"})


def test_refresh_reads_only_the_original_current_record():
    app, repo, tasks, builder, bot = fixture()
    record, _ = app.submit(bot=bot, actor_id="owner", surface="legacy", body={"client_request_id": "r2", "public": "1", "friend_approval": "0", "permission_owner": "caller"})
    outcome, current = app.refresh(bot=bot, actor_id="owner", record=record)
    assert outcome == "UPDATED" and current.risk_check_id == record.risk_check_id
    assert tasks.polls == [("dev", record.risk_check_id, True)]
    with pytest.raises(LookupError):
        app.refresh(bot=SimpleNamespace(bot_pk=99, current_owner_id="owner", bot={"env": "dev"}), actor_id="owner", record=record)


def test_department_order_and_name_do_not_change_request_identity():
    app, repo, tasks, builder, bot = fixture()
    body = {"client_request_id": "r3", "public_scope": "agent", "visibility": "protected", "view_depts": [{"deptNo": "b", "deptName": "B"}, {"deptNo": "a", "deptName": "A"}]}
    record, _ = app.submit(bot=bot, actor_id="owner", surface="openapi", body=body)
    reordered = {**body, "view_depts": [{"deptNo": "a", "deptName": "Renamed"}, {"deptNo": "b", "deptName": "B"}]}
    replay, created = app.submit(bot=bot, actor_id="owner", surface="openapi", body=reordered)
    assert not created and replay.risk_check_id == record.risk_check_id


def test_refresh_stops_after_owner_or_current_changes():
    app, repo, tasks, builder, bot = fixture()
    body = {"client_request_id": "r4", "public_scope": "user", "visibility": "public", "view_depts": []}
    record, _ = app.submit(bot=bot, actor_id="owner", surface="openapi", body=body)
    changed_owner = SimpleNamespace(bot_pk=12, current_owner_id="new-owner", bot={"env": "dev"})
    assert app.refresh(bot=changed_owner, actor_id="new-owner", record=record)[0] == "NOT_QUERIED"
    repo.row = replace(record, current_target_key=None)
    assert app.refresh(bot=bot, actor_id="owner", record=record)[0] == "NOT_QUERIED"
    assert not tasks.polls


def test_input_failure_does_not_create_record():
    app, repo, tasks, builder, bot = fixture()
    def broken(**kwargs):
        raise RuntimeError("resource read failed")
    builder.build = broken
    with pytest.raises(RuntimeError, match="resource read failed"):
        app.submit(bot=bot, actor_id="owner", surface="openapi", body={"client_request_id": "r5", "public_scope": "user", "visibility": "public", "view_depts": []})
    assert repo.row is None
