from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_public_risk.query import RiskQuery, RiskRecordNotFound


class Repository:
    def __init__(self):
        self.row = SimpleNamespace(bot_pk=12, env="dev", risk_check_id="r1")
        self.calls = []

    def get(self, *, env, risk_check_id):
        self.calls.append(("get", env, risk_check_id))
        return self.row if risk_check_id == "r1" else None

    def get_current(self, *, env, bot_pk, public_scope):
        self.calls.append(("current", env, bot_pk, public_scope))
        return self.row if (env, bot_pk, public_scope) == ("dev", 12, "user") else None


def test_local_record_read_checks_bot_identity():
    repository = Repository()
    query = RiskQuery(repository)
    assert query.get(env="dev", bot_pk=12, risk_check_id="r1") is repository.row
    with pytest.raises(RiskRecordNotFound):
        query.get(env="dev", bot_pk=13, risk_check_id="r1")
    with pytest.raises(RiskRecordNotFound):
        query.get(env="dev", bot_pk=12, risk_check_id="missing")
    assert repository.calls == [("get", "dev", "r1"), ("get", "dev", "r1"), ("get", "dev", "missing")]


def test_current_returns_null_without_external_query():
    repository = Repository()
    query = RiskQuery(repository)
    assert query.current(env="dev", bot_pk=12, public_scope="user") is repository.row
    assert query.current(env="dev", bot_pk=12, public_scope="agent") is None
    assert repository.calls == [("current", "dev", 12, "user"), ("current", "dev", 12, "agent")]
