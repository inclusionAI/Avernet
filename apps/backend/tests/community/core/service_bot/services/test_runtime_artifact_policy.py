"""Runtime CLI snapshots are disabled unless an environment-scoped list enables them."""
from unittest.mock import MagicMock
import pytest

from agentclaw.community.core.service_bot.services.runtime_artifact_policy import resolve_runtime_artifacts


@pytest.mark.parametrize("entries,env,enabled,source", [
    (set(), "prod", False, "none"),
    ({("owner", "prod", "service_bot_runtime_artifact")}, "prod", True, "user"),
    ({("*", "prod", "service_bot_runtime_artifact")}, "prod", True, "global"),
    ({("owner", "pre", "service_bot_runtime_artifact")}, "prod", False, "none"),
    ({("owner", "prod", "caller_identity")}, "prod", False, "none"),
])
def test_scoped_decision(entries, env, enabled, source, caplog):
    caplog.set_level("INFO")
    repo = MagicMock()
    repo.exists.side_effect = lambda **kw: (kw["entity_id"], kw["env"], kw["user_list_type"]) in entries
    assert resolve_runtime_artifacts(repo, bot_id="bot", entity_id="owner", env=env) is enabled
    assert f"match={source}" in caplog.text
    assert repo.exists.call_count == (1 if source == "global" else 2)


def test_query_failure_closes_and_does_not_log_credentials(caplog):
    caplog.set_level("INFO")
    repo = MagicMock()
    repo.exists.side_effect = RuntimeError("private-token-must-not-log")
    assert resolve_runtime_artifacts(repo, bot_id="bot", entity_id="owner", env="prod") is False
    assert "match=error" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "private-token-must-not-log" not in caplog.text


def test_missing_owner_stays_disabled_even_with_global_entry(caplog):
    caplog.set_level("INFO")
    repo = MagicMock(exists=MagicMock(return_value=True))
    assert resolve_runtime_artifacts(repo, bot_id="bot", entity_id="", env="prod") is False
    repo.exists.assert_not_called()
    assert "match=missing_owner" in caplog.text


def test_user_query_failure_after_global_miss_closes(caplog):
    caplog.set_level("INFO")
    repo = MagicMock()
    repo.exists.side_effect = [False, RuntimeError("private-token-must-not-log")]
    assert resolve_runtime_artifacts(repo, bot_id="bot", entity_id="owner", env="prod") is False
    assert repo.exists.call_count == 2
    assert "match=error" in caplog.text
    assert "private-token-must-not-log" not in caplog.text
