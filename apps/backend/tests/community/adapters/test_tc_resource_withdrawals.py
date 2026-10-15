"""Operator interface is scoped, compare-and-set, audited, and opt-in."""

from unittest.mock import Mock

import pytest

from agentclaw.community.adapters.tc_resource_withdrawals import execute, parser


def test_replay_requires_confirmation_actor_reason_and_expected_attempts():
    with pytest.raises(SystemExit):
        parser().parse_args(["replay", "--tenant", "t", "--event-id", "e"])


def test_stats_is_read_only():
    repo = Mock()
    repo.stats.return_value = {"blocked": 1}
    assert execute(parser().parse_args(["stats", "--tenant", "t"]), repo) == {
        "blocked": 1
    }
    repo.replay.assert_not_called()


def test_replay_forwards_tenant_and_optimistic_fence():
    repo = Mock()
    repo.replay.return_value = True
    args = parser().parse_args(
        [
            "replay",
            "--tenant",
            "t",
            "--event-id",
            "e",
            "--expected-attempts",
            "4",
            "--actor",
            "operator",
            "--reason",
            "credential fixed",
            "--confirm",
        ]
    )
    result = execute(args, repo)
    assert result["replayed"] is True
    repo.replay.assert_called_once_with(
        event_id="e",
        tenant="t",
        expected_attempts=4,
        actor="operator",
        reason="credential fixed",
    )


def test_inspect_refuses_cross_tenant_records():
    repo = Mock()
    repo.get.return_value.tenant = "other"
    with pytest.raises(ValueError, match="not_found"):
        execute(
            parser().parse_args(["inspect", "--tenant", "t", "--event-id", "e"]), repo
        )


@pytest.mark.parametrize("replayed,exit_code", [(True, 0), (False, 1)])
def test_main_uses_application_repository_and_reports_replay_result(
    monkeypatch, capsys, replayed, exit_code
):
    import sys
    from agentclaw.community.adapters import tc_resource_withdrawals as cli
    from agentclaw.community.adapters.http import app as app_module

    repo = Mock()
    repo.replay.return_value = replayed
    application = Mock()
    application.state.injector.get.return_value = repo
    monkeypatch.setattr(app_module, "app", application)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "withdrawals",
            "replay",
            "--tenant",
            "t",
            "--event-id",
            "e",
            "--expected-attempts",
            "1",
            "--actor",
            "ops",
            "--reason",
            "fixed",
            "--confirm",
        ],
    )
    assert cli.main() == exit_code
    assert '"replayed":' in capsys.readouterr().out
