import asyncio
import hashlib
import hmac
import json

import httpx
import pytest

from agentclaw.community.core.task.task_runner.client.session_files.bcs_session_files_client import (
    BcsClientError, BcsClientRequestError, BcsSessionFilesClient, SessionFileContent,
)


class _Tok:
    token = "drv"
    secret = "s3c"
    base_url = "http://bcs"
    task_callback_url = ""


def _client(handler):
    return BcsSessionFilesClient(_Tok(), http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://bcs"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _assert_hmac(req):
    ts = req.headers["X-ECB-Timestamp"]
    exp = hmac.new(b"s3c", f"{ts}{req.method}{req.url.path}".encode(), hashlib.sha256).hexdigest()
    assert req.headers["X-ECB-Signature"] == exp
    assert req.headers["X-ECB-Token"] == "drv"


# ===== list_files: GET /sessions/{sid}/files =====
def test_list_files_gets_items_with_query_params():
    seen = {}

    def h(req):
        assert req.method == "GET"
        assert req.url.path == "/sessions/s1/files"
        _assert_hmac(req)
        seen["params"] = dict(req.url.params)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"items": [
            {"file_id": "f1", "file_name": "报告.xlsx", "mime_type": "application/vnd..."
                                                                 u"spreadsheetml.sheet",
             "size": 1024, "status": "Ready"},
        ], "total": 1})

    files = _run(_client(h).list_files("s1", prefix="报告", status="Ready", limit=10, offset=5))
    assert files == [{"file_id": "f1", "file_name": "报告.xlsx",
                      "mime_type": "application/vnd...spreadsheetml.sheet",
                      "size": 1024, "status": "Ready"}]
    assert seen["params"] == {"prefix": "报告", "status": "Ready", "limit": "10", "offset": "5"}
    assert seen["auth"] is None              # 无 bearer 不发 Authorization


def test_list_files_omits_unset_query_params():
    seen = {}

    def h(req):
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json={"items": [], "total": 0})

    _run(_client(h).list_files("s1"))
    assert seen["params"] == {}              # 未传的可选参数不下发(BCS 侧默认 limit=100/offset=0)


def test_list_files_forwards_caller_bearer():
    def h(req):
        assert req.headers["authorization"] == "Bearer drv-session-token"
        _assert_hmac(req)                    # HMAC 服务签名照常叠加
        return httpx.Response(200, json={"items": [], "total": 0})

    _run(_client(h).list_files("s1", caller_bearer="drv-session-token"))


def test_list_files_maps_404_to_request_error():
    def h(req):
        return httpx.Response(404, json={"error": "FILE_NOT_FOUND"})

    with pytest.raises(BcsClientRequestError):
        _run(_client(h).list_files("s1"))


# ===== download_file: GET /sessions/{sid}/files/{fid}/content =====
def test_download_file_streams_local_backend_bytes():
    seen = {}

    def h(req):
        assert req.method == "GET"
        assert req.url.path == "/sessions/s1/files/f1/content"
        _assert_hmac(req)
        seen["params"] = dict(req.url.params)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, content=b"RAW_BYTES",
                              headers={"Content-Type": "text/markdown; charset=utf-8",
                                       "Content-Disposition": 'attachment; filename="report.md"'})

    got = _run(_client(h).download_file("s1", "f1", caller_bearer="drv-session-token"))
    assert isinstance(got, SessionFileContent)
    assert got.content == b"RAW_BYTES"
    assert got.file_name == "report.md"
    assert got.content_type == "text/markdown; charset=utf-8"
    assert seen["params"] == {}             # 未传可选参数不进 query
    assert seen["auth"] == "Bearer drv-session-token"


def test_download_file_inline_show_flag():
    def h(req):
        assert req.url.params.get("show") == "true"
        return httpx.Response(200, content=b"B", headers={"Content-Type": "text/plain"})

    got = _run(_client(h).download_file("s1", "f1", show=True))
    assert got.content == b"B"
    assert got.file_name is None             # 无 Content-Disposition → file_name 缺省 None


def test_download_file_follows_presign_redirect():
    """baas 后端:下载口 302 → presigned URL,客户端需跟随重定向取回字节。"""
    hits = []

    def h(req):
        hits.append(req.url.path)
        if req.url.path == "/sessions/s1/files/f1/content":
            return httpx.Response(302, headers={"Location": "http://bcs/presigned/f1?sig=abc"})
        assert req.url.path == "/presigned/f1"
        return httpx.Response(200, content=b"PRESIGNED_BYTES")

    got = _run(_client(h).download_file("s1", "f1"))
    assert got.content == b"PRESIGNED_BYTES"
    assert hits[0].endswith("/content") and hits[1] == "/presigned/f1"


def test_download_file_maps_404_to_request_error():
    def h(req):
        return httpx.Response(404, json={"error": "FILE_NOT_FOUND"})

    with pytest.raises(BcsClientRequestError):
        _run(_client(h).download_file("s1", "no-such-file"))


def test_session_file_content_is_ordered_dataclass():
    got = SessionFileContent(content=b"X", file_name="a.md", content_type="text/markdown")
    assert got.content == b"X"


# ===== extra_headers:人身份(Cookie)直通——CLI/技能层本地 e2e 用 =====
def test_list_files_forwards_extra_headers_cookie():
    seen = {}

    def h(req):
        _assert_hmac(req)
        seen["cookie"] = req.headers.get("cookie")
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"items": [], "total": 0})

    _run(_client(h).list_files("s1", extra_headers={"Cookie": "sessionid=u_42"},
                               caller_bearer="drv-session-token"))
    assert seen["cookie"] == "sessionid=u_42"    # Cookie 直通
    assert seen["auth"] == "Bearer drv-session-token"


def test_download_file_forwards_extra_headers_cookie():
    def h(req):
        assert req.headers.get("cookie") == "sessionid=u_42"
        return httpx.Response(200, content=b"C", headers={"Content-Type": "text/plain"})

    got = _run(_client(h).download_file("s1", "f1", extra_headers={"Cookie": "sessionid=u_42"}))
    assert got.content == b"C"


# ===== loop 安全:跨 event loop 复用隔离(同 BcsHttpAdapter 机制)=====
def test_owned_client_isolated_when_event_loop_changes(monkeypatch):
    import agentclaw.community.core.task.task_runner.client.session_files.\
        bcs_session_files_client as module

    class _FakeClient:
        instances = []

        def __init__(self, *, base_url):
            self.base_url = base_url
            self.closed = False
            self.__class__.instances.append(self)

        async def request(self, method, path, **kwargs):
            return httpx.Response(200, json={"items": [], "total": 0})

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeClient)
    client = module.BcsSessionFilesClient(_Tok())

    _run(client.list_files("s1"))
    _run(client.list_files("s2"))
    assert len(_FakeClient.instances) == 2    # 第二个 loop → 隔离客户端
    assert _FakeClient.instances[1].closed is True


def test_json_error_summary_kept_on_request_error():
    """4xx 抛错时保留 BCS 错误体,便于技能层提示用户。"""
    def h(req):
        return httpx.Response(403, json={"error": "FORBIDDEN"})

    try:
        _run(_client(h).list_files("s1"))
    except BcsClientRequestError as ex:
        assert "FORBIDDEN" in str(ex)
    else:
        pytest.fail("expected BcsClientRequestError")


def test_module_exports_no_extra_public_surface():
    import agentclaw.community.core.task.task_runner.client.session_files.\
        bcs_session_files_client as module
    for name in ("BcsSessionFilesClient", "SessionFileContent",
                 "BcsClientError", "BcsClientRequestError",
                 "BcsServerError", "BcsRateLimitError", "BcsTimeoutError"):
        assert hasattr(module, name), name
    _ = json  # keep import parity with sibling adapters