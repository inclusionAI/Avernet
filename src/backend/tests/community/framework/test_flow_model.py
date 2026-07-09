# src/backend/tests/framework/test_flow_model.py
"""Tests for the declarative flow model (Plan B)."""
from __future__ import annotations

import pytest

from tests.community.framework.flow import FlowCase, FlowContext, FlowStep, FsAssert


def test_context_stores_and_reads():
    ctx = FlowContext()
    ctx["bot_id"] = "abc123"
    assert ctx["bot_id"] == "abc123"


def test_context_interpolate_string():
    ctx = FlowContext()
    ctx["bot_id"] = "abc123"
    assert ctx.interpolate("/bots/{bot_id}/info") == "/bots/abc123/info"


def test_context_interpolate_deep_mapping():
    ctx = FlowContext()
    ctx["owner"] = "u1"
    out = ctx.interpolate_deep({"owner_id": "{owner}", "nested": {"x": "{owner}"}})
    assert out == {"owner_id": "u1", "nested": {"x": "u1"}}


def test_context_interpolate_missing_key_raises():
    ctx = FlowContext()
    with pytest.raises(KeyError):
        ctx.interpolate("/bots/{missing}")


def test_flowstep_defaults():
    s = FlowStep(method="GET", path="/health")
    assert s.method == "GET"
    assert s.path == "/health"
    assert s.body is None
    assert s.query == {}
    assert s.form is None
    assert s.extract == {}
    assert s.expect is None
    assert s.expect_status == 200
    assert s.fs_asserts == []
    assert s.headers is None


def test_flowstep_fs_asserts_and_headers():
    s = FlowStep(
        method="POST",
        path="/api/skills",
        headers={"x-user-id": "lifecycle"},
        fs_asserts=[
            FsAssert(kind="is_dir", path="ws/{bot_id}/skills"),
            FsAssert(kind="symlink_target", path="ws/link", expected_target="ws/src"),
        ],
    )
    assert s.headers == {"x-user-id": "lifecycle"}
    assert len(s.fs_asserts) == 2
    assert s.fs_asserts[0].kind == "is_dir"
    assert s.fs_asserts[1].expected_target == "ws/src"


def test_flowstep_query_and_form_are_data_only():
    s = FlowStep(
        method="POST",
        path="/api/callbacks",
        query={"bot_id": "{bot_id}"},
        form={"decision": "{decision}"},
    )
    assert s.query == {"bot_id": "{bot_id}"}
    assert s.form == {"decision": "{decision}"}


def test_fs_assert_defaults_no_target():
    fa = FsAssert(kind="exists", path="ws/{bot_id}")
    assert fa.kind == "exists"
    assert fa.expected_target is None


def test_flowcase_holds_steps_and_covers():
    case = FlowCase(
        name="smoke",
        covers=["bot_management"],
        steps=[
            FlowStep(method="GET", path="/health"),
            FlowStep(method="POST", path="/bots", body={"x": 1}, extract={"bot_id": "data.id"}),
        ],
    )
    assert case.name == "smoke"
    assert case.covers == ["bot_management"]
    assert len(case.steps) == 2
    assert case.steps[1].extract == {"bot_id": "data.id"}
