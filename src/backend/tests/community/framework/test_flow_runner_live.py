# src/backend/tests/framework/test_flow_runner_live.py
"""run_flow_live self-tests: same chaining contract as run_flow + FsAssert.

httpx.MockTransport stands in for a live backend so we test the executor's
contract (status / expect / extract / per-step headers) without a process.
FsAssert checks run against a real tmp_path so the filesystem semantics are
exercised for real.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.community.framework.flow import FlowCase, FlowContext, FlowStep, FsAssert
from tests.community.framework.flow_runner_live import _check_fs_assert, run_flow_live


def _client_patch(monkeypatch, handler):
    """Make httpx.Client use a MockTransport with `handler`, ignoring base_url."""
    orig = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs.setdefault("base_url", "http://test")
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_run_flow_live_chains_extract(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/bots":
            return httpx.Response(200, json={"data": {"bot_id": "B7"}})
        if req.url.path == "/api/bots/B7":
            return httpx.Response(200, json={"data": {"bot_id": "B7", "name": "ok"}})
        return httpx.Response(404)

    _client_patch(monkeypatch, handler)
    case = FlowCase(name="c", covers=[], steps=[
        FlowStep(method="POST", path="/api/bots", extract={"bot_id": "data.bot_id"}),
        FlowStep(method="GET", path="/api/bots/{bot_id}", expect={"data": {"name": "ok"}}),
    ])
    ctx = run_flow_live(case, base_url="http://x", fs_root="/")
    assert isinstance(ctx, FlowContext)
    assert ctx["bot_id"] == "B7"


def test_run_flow_live_status_mismatch_reports_step(monkeypatch):
    _client_patch(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    case = FlowCase(name="c", covers=[], steps=[FlowStep(method="GET", path="/api/health")])
    with pytest.raises(AssertionError, match=r"step 1 \(GET /api/health\)"):
        run_flow_live(case, base_url="http://x")


def test_run_flow_live_per_step_header_override(monkeypatch):
    seen = {}

    def handler(req):
        seen["uid"] = req.headers.get("x-user-id")
        return httpx.Response(200, json={})

    _client_patch(monkeypatch, handler)
    case = FlowCase(name="c", covers=[], steps=[
        FlowStep(method="GET", path="/api/health", headers={"x-user-id": "override"}),
    ])
    run_flow_live(case, base_url="http://x")
    assert seen["uid"] == "override"


def test_run_flow_live_sends_query_and_form(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode("utf-8")
        return httpx.Response(200, json={"success": True})

    _client_patch(monkeypatch, handler)
    ctx = FlowContext()
    ctx["puid"] = "puid-1"
    case = FlowCase(name="form", covers=[], steps=[
        FlowStep(
            method="POST",
            path="/api/callback",
            query={"trace": "{puid}"},
            form={"globalUniqueId": "{puid}", "lastOperate": "AGREE"},
            expect={"success": True},
        ),
    ])

    returned = run_flow_live(case, base_url="http://x", initial_context=ctx)

    assert returned is ctx
    assert "trace=puid-1" in seen["url"]
    assert "globalUniqueId=puid-1" in seen["body"]
    assert "lastOperate=AGREE" in seen["body"]


def test_fs_assert_dir_symlink_file(tmp_path: Path):
    ctx = FlowContext()
    (tmp_path / "d").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "src")
    (tmp_path / "f.txt").write_text("hi")
    _check_fs_assert(FsAssert(kind="is_dir", path="d"), ctx, tmp_path)
    _check_fs_assert(FsAssert(kind="is_symlink", path="link"), ctx, tmp_path)
    _check_fs_assert(FsAssert(kind="is_file", path="f.txt"), ctx, tmp_path)
    _check_fs_assert(FsAssert(kind="exists", path="d"), ctx, tmp_path)
    _check_fs_assert(FsAssert(kind="not_exists", path="nope"), ctx, tmp_path)
    _check_fs_assert(
        FsAssert(kind="symlink_target", path="link", expected_target=str(tmp_path / "src")),
        ctx, tmp_path,
    )


def test_fs_assert_interpolates_path(tmp_path: Path):
    ctx = FlowContext()
    ctx["bot_id"] = "B9"
    (tmp_path / "B9").mkdir()
    _check_fs_assert(FsAssert(kind="is_dir", path="{bot_id}"), ctx, tmp_path)


def test_fs_assert_missing_fails(tmp_path: Path):
    with pytest.raises(AssertionError, match="exists"):
        _check_fs_assert(FsAssert(kind="exists", path="ghost"), FlowContext(), tmp_path)
