"""Dispatch display projections must use real delivery/planning facts only."""
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from agentclaw.community.adapters.http.task.schemas_relay_dispatch import (
    build_dispatch_handoff_display,
)


def _node(*, mode="single_bot", assignee="bot:old-owner", title="Task",
          task_id="task-1", **props):
    return SimpleNamespace(
        task_id=task_id,
        task_spec=SimpleNamespace(context=SimpleNamespace(title=title)),
        run_info=SimpleNamespace(run_mode=mode, assignee=assignee, extend_props=props),
    )


def _query(display):
    assert display.entry_url is not None
    url = urlsplit(display.entry_url)
    assert url.scheme == "https"
    assert url.netloc == "workbench.test"
    assert url.path == "/workspace/chat"
    return parse_qs(url.query)


@pytest.mark.parametrize("formation,identity", [
    ({"group_name": "  Support group  ",
      "extend_props": {"manager_bot_id": "manager:owner"},
      "bot_ids": ["member:owner"]}, "manager:owner"),
    ({"group_name": "  Support group  ",
      "extend_props": {}, "bot_ids": ["member:owner", "other:owner"]}, "member:owner"),
])
def test_group_display_uses_group_name_and_manager_or_first_member(formation, identity):
    delivered = _node(mode="coop_group", session_id="group/session",
                      pending_group_formation=formation)
    display = build_dispatch_handoff_display(delivered, None, "https://workbench.test/")
    assert display.collaborator_name == "Support group"
    assert _query(display) == {
        "section": ["team"], "bot": [identity], "session": ["group/session"],
    }


def test_single_bot_uses_authoritative_owner_not_assignee_suffix():
    delivered = _node(session_id="s", assignee_owner_id="new-owner",
                      assignee_name="  Delivered bot  ")
    planned = _node(assignee_name="Planned bot", assignee_owner_id="old-owner")
    display = build_dispatch_handoff_display(delivered, planned, "https://workbench.test")
    assert display.collaborator_name == "Delivered bot"
    assert _query(display) == {
        "section": ["managed"], "bot": ["bot:new-owner"], "session": ["s"],
    }


@pytest.mark.parametrize("delivered", [
    SimpleNamespace(),
    _node(mode="coop_group", pending_group_formation="invalid"),
    _node(mode="coop_group", pending_group_formation={"bot_ids": []}),
    _node(mode="coop_group", pending_group_formation={"bot_ids": "invalid"}),
    _node(assignee_owner_id=""),
    _node(assignee="", assignee_owner_id="owner"),
    _node(mode="unknown"),
])
def test_missing_delivery_identity_falls_back_to_planned_facts(delivered):
    planned = _node(session_id="planned-session", assignee_owner_id="owner",
                    assignee_name="Planned bot")
    display = build_dispatch_handoff_display(delivered, planned, "https://workbench.test")
    assert display.collaborator_name == "Planned bot"
    query = _query(display)
    assert query["bot"] == ["bot:owner"]
    assert query["session"] == ["planned-session"]


def test_no_delivery_record_uses_planned_group_section():
    planned = _node(mode="coop_group", session_id="s", pending_group_formation={
        "group_name": "Group", "extend_props": {"manager_bot_id": "manager:owner"}})
    display = build_dispatch_handoff_display(None, planned, "https://workbench.test")
    assert _query(display)["section"] == ["team"]
    assert display.collaborator_name == "Group"


@pytest.mark.parametrize("props,frontend", [
    ({"assignee_owner_id": "owner"}, "https://workbench.test"),
    ({"session_id": "s"}, "https://workbench.test"),
    ({"session_id": "s", "assignee_owner_id": "owner"}, "  "),
])
def test_incomplete_entry_facts_do_not_fabricate_url(props, frontend):
    display = build_dispatch_handoff_display(_node(**props), None, frontend)
    assert display.entry_url is None
    assert display.collaborator_name is None


@pytest.mark.parametrize("delivered,planned,subject", [
    (_node(title=" ", task_id=" delivered-id "), None, "delivered-id"),
    (SimpleNamespace(), _node(title="", task_id="planned-id"), "planned-id"),
    (SimpleNamespace(), SimpleNamespace(), "Relay handoff"),
    (_node(title="", task_id="delivered-id"), _node(title="Planned title"), "Planned title"),
    (_node(title="Delivered title"), _node(title="Planned title"), "Delivered title"),
])
def test_subject_title_and_task_id_fallbacks(delivered, planned, subject):
    display = build_dispatch_handoff_display(delivered, planned, "https://workbench.test")
    assert display.subject == f"{datetime.now().strftime('%m%d')}:{subject}"


@pytest.mark.parametrize("field", ["task_id", "target_node_id", "origin_node_id", "relay_token"])
def test_dispatch_request_rejects_whitespace_identity(field):
    from pydantic import ValidationError
    from agentclaw.community.adapters.http.task.schemas_relay_dispatch import TaskDispatchRequestDTO

    payload = dict(task_id="task", target_node_id="target", origin_node_id="origin",
                   relay_token="test-generation")
    payload[field] = "  "
    with pytest.raises(ValidationError, match="must be a non-empty string"):
        TaskDispatchRequestDTO(**payload)
