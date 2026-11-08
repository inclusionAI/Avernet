import asyncio
import json

import httpx
import pytest

from agentclaw.community.core.task.task_runner.client.session_files.bot_resource_file_cli import (
    ResourceFileError, fetch_preview,
)


class _Harness:
    def __init__(self, handler):
        self.seen = {}
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                        base_url="https://agentclaw-prod.alipay.com")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_preview_assembles_query_and_returns_content():
    seen = {}

    def h(req):
        assert req.method == "GET"
        assert req.url.path == "/api/resources/files/preview"
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"success": True, "data": {
            "content": "# AI News 2026-10-20", "size": 19}, "message": None})

    t = _Harness(h)
    text = _run(fetch_preview(
        t.client, "ai-news-2026-10-20.md", bot_id="20260920_hzzaxnzf",
        owner_id="35983", ctoken="w-fyUpHSLA_uEZ9D", user_id="35983"))
    assert text == "# AI News 2026-10-20"
    # 网关参数 + 业务参数都齐:ctoken/user_id 网关要,bot_id/path/owner_id 服务要
    assert seen["params"] == {
        "path": "ai-news-2026-10-20.md",
        "bot_id": "20260920_hzzaxnzf",
        "owner_id": "35983",
        "ctoken": "w-fyUpHSLA_uEZ9D",
        "user_id": "35983",
    }


def test_preview_omits_unset_optional_params():
    def h(req):
        params = dict(req.url.params)
        assert "engine_type" not in params
        assert "max_size" not in params
        assert "user_id" not in params      # 未传不下发
        assert "publish_id" not in params
        return httpx.Response(200, json={"success": True, "data": {"content": "ok", "size": 2}})

    t = _Harness(h)
    assert _run(fetch_preview(t.client, "a.md", bot_id="b", ctoken="w-x")) == "ok"


def test_preview_omits_ctoken_when_absent():
    def h(req):
        assert "ctoken" not in dict(req.url.params)
        return httpx.Response(200, json={"success": True, "data": {"content": "x", "size": 1}})

    t = _Harness(h)
    _run(fetch_preview(t.client, "a.md", bot_id="b"))


def test_preview_keeps_non_ascii_path():
    """工作区相对路径可含中文/斜杠(httpx 负责百分号编码,服务端 FastAPI 还原)。"""
    def h(req):
        assert dict(req.url.params)["path"] == "周报/2026-10-20.md"
        return httpx.Response(200, json={"success": True, "data": {"content": "ok", "size": 2}})

    t = _Harness(h)
    assert _run(fetch_preview(t.client, "周报/2026-10-20.md", bot_id="b")) == "ok"


def test_preview_empty_content_is_not_error():
    def h(req):
        return httpx.Response(200, json={"success": True, "data": {"content": "", "size": 0}})

    t = _Harness(h)
    assert _run(fetch_preview(t.client, "empty.md", bot_id="b")) == ""


def test_preview_forwards_headers_cookie_and_bearer():
    seen = {}

    def h(req):
        seen["cookie"] = req.headers.get("cookie")
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"success": True, "data": {"content": "z", "size": 1}})

    t = _Harness(h)
    _run(fetch_preview(t.client, "a.md", bot_id="b",
                       headers={"Cookie": "sid=1", "Authorization": "Bearer k"}))
    assert seen["cookie"] == "sid=1"
    assert seen["auth"] == "Bearer k"


def test_preview_error_carries_status_and_body():
    def h(req):
        return httpx.Response(404, json={"detail": "File not found"})

    t = _Harness(h)
    with pytest.raises(ResourceFileError, match="404"):
        _run(fetch_preview(t.client, "nope.md", bot_id="b"))


def test_preview_rejects_success_false_envelope():
    """success=false / 缺 data → 业务失败,不能拿 None 继续当正文。"""
    def h(req):
        return httpx.Response(200, json={"success": False, "data": None,
                                         "message": "bot not found"})

    t = _Harness(h)
    with pytest.raises(ResourceFileError, match="bot not found"):
        _run(fetch_preview(t.client, "a.md", bot_id="missing"))


def test_preview_401_maps_to_error():
    def h(req):
        return httpx.Response(401, json={"detail": "Not authenticated"})

    t = _Harness(h)
    with pytest.raises(ResourceFileError, match="401"):
        _run(fetch_preview(t.client, "a.md", bot_id="b"))
    _ = json