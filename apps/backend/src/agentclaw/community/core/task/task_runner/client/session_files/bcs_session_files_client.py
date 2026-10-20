"""BcsSessionFilesClient:bot 技能层用的 BCS 会话共享文件工具 client。

集成两个 BCS 读口(BCS session file workspace,FileWorkspace ACL = 会话成员级):

* ``GET /sessions/{sid}/files`` — 列文件(分页/前缀/状态过滤)
* ``GET /sessions/{sid}/files/{file_id}/content`` — 下载字节;local 后端直接流式回字节,
  baas 后端 302 → presigned URL(客户端跟随重定向取回)

HMAC 服务签名(X-ECB-*)与 ``BcsHttpAdapter`` 同规约(签串 f"{ts}{method}{path}",token
provider 同源注入);但这两个口走参与者级 ACL——``resolve_group_chat_caller`` 只认
``X-BCS-Bot-Token`` / ``Authorization: Bearer``(bot token)或 human 身份,服务 HMAC 单独
不构成 caller(会 401)。所以调用方须传持有者 bot 的 ``caller_bearer`` 作 Bearer(token
不打日志,同 ``get_session_messages`` 规约)。

错误语义与 ``BcsHttpAdapter`` 共享同一异常族(``BcsClientRequestError`` 等,4xx 不重试)。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

import httpx

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError,
    BcsClientRequestError,
    BcsRateLimitError,
    BcsServerError,
    BcsTimeoutError,
    _map_status,
    _response_summary,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import BcsTokenProvider


logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r'filename="((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class SessionFileContent:
    """一次成功下载的字节结果;``file_name``/``content_type`` 取自响应头
    (``Content-Disposition`` 的 ``filename=`` / ``Content-Type``),缺省 ``None``。"""

    content: bytes
    file_name: str | None = None
    content_type: str | None = None


class BcsSessionFilesClient:  # pragma: no cover — live BCS HTTP client (与 BcsHttpAdapter 同款,联调覆盖)
    def __init__(self, token: BcsTokenProvider, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._t = token
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(base_url=token.base_url)
        # Shared across asyncio event loops is unsafe. Keep the owned client pinned
        # to the first loop and spin a short-lived client for other loops (Web-Worker
        # 线程与 FastAPI loop 并存会换 loop,见 BcsHttpAdapter 同机制注释)。
        self._client_loop: asyncio.AbstractEventLoop | None = None

    @asynccontextmanager
    async def _client_for_current_loop(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an AsyncClient that belongs to the current event loop."""
        current_loop = asyncio.get_running_loop()
        if not self._owns_client:
            yield self._client
            return
        if self._client_loop is None:
            self._client_loop = current_loop
            yield self._client
            return
        if self._client_loop is current_loop:
            yield self._client
            return
        logger.warning(
            "[bcs_session_files] event loop changed; using isolated client previous_loop=%s current_loop=%s",
            id(self._client_loop),
            id(current_loop),
        )
        client = httpx.AsyncClient(base_url=self._t.base_url)
        try:
            yield client
        finally:
            await client.aclose()

    def _sign(self, method: str, path: str, ts: str) -> dict[str, str]:
        sig = hmac.new(self._t.secret.encode(), f"{ts}{method}{path}".encode(), hashlib.sha256).hexdigest()
        return {"X-ECB-Token": self._t.token, "X-ECB-Timestamp": ts, "X-ECB-Signature": sig}

    async def list_files(
        self,
        session_id: str,
        *,
        prefix: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        caller_bearer: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """列会话共享文件:``GET /sessions/{sid}/files`` → ``items`` 列表原样返回。

        每条 dict 带 file_id/file_name/mime_type/size/status(Pending/Ready/Deleting/
        Failed)/created_at 等;``prefix`` 按文件名前缀过滤,``status`` 取值仅限上述四种。
        可选参数未传不下发(BCS 侧默认 limit=100/offset=0)。``extra_headers`` 直接并入
        请求头(如 ``{"Cookie": ...}`` 表达人身份,叠加在 HMAC/Bearer 之上)。
        """
        path = f"/sessions/{session_id}/files"
        ts = str(int(time.time()))
        headers = self._sign("GET", path, ts)
        if caller_bearer:
            # 参与者级 ACL:BCS resolve_group_chat_caller 只认 bot token/human 身份,
            # 服务 HMAC 单独不足。token 不打日志。
            headers["Authorization"] = f"Bearer {caller_bearer}"
        if extra_headers:
            headers.update(extra_headers)
        params: dict[str, Any] = {}
        if prefix is not None:
            params["prefix"] = prefix
        if status is not None:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        logger.info("[bcs_session_files] >>> list_files GET path=%s base_url=%s params_keys=%s caller_identity=%s",
                    path, self._t.base_url, sorted(params), "bearer" if caller_bearer else "hmac-only")
        _t0 = time.monotonic()
        async with self._client_for_current_loop() as client:
            r = await client.request("GET", path, params=params, headers=headers)
        logger.info("[bcs_session_files] <<< list_files GET path=%s status=%s elapsed_ms=%s body=%s",
                    path, r.status_code, int((time.monotonic() - _t0) * 1000), _response_summary(r))
        _map_status(r)
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise BcsClientError(f"list_files 响应缺 items 列表: {_response_summary(r)}")
        return items

    async def download_file(
        self,
        session_id: str,
        file_id: str,
        *,
        show: bool = False,
        caller_bearer: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> SessionFileContent:
        """下载文件字节:``GET /sessions/{sid}/files/{fid}/content``。

        local 后端直接流式回字节(带 Content-Type/Content-Disposition);baas 后端
        302 重定向到 presigned URL——``follow_redirects=True`` 跟随取回。两种形态
        统一收敛为 :class:`SessionFileContent`。字节体不打日志(只记 size)。
        ``show=True`` → BCS 以 inline 而非 attachment 输出(语义透传)。
        ``extra_headers`` 直接并入请求头(如 ``{"Cookie": ...}`` 表达人身份)。
        """
        path = f"/sessions/{session_id}/files/{file_id}/content"
        ts = str(int(time.time()))
        headers = self._sign("GET", path, ts)
        if caller_bearer:
            headers["Authorization"] = f"Bearer {caller_bearer}"
        if extra_headers:
            headers.update(extra_headers)
        params: dict[str, Any] = {}
        if show:
            params["show"] = "true"
        logger.info("[bcs_session_files] >>> download_file GET path=%s base_url=%s show=%s caller_identity=%s",
                    path, self._t.base_url, show, "bearer" if caller_bearer else "hmac-only")
        _t0 = time.monotonic()
        async with self._client_for_current_loop() as client:
            r = await client.request("GET", path, params=params, headers=headers, follow_redirects=True)
        logger.info("[bcs_session_files] <<< download_file GET path=%s status=%s elapsed_ms=%s bytes=%s",
                    path, r.status_code, int((time.monotonic() - _t0) * 1000), len(r.content))
        _map_status(r)
        disposition = r.headers.get("content-disposition", "")
        m = _FILENAME_RE.search(disposition)
        file_name = m.group(1).replace('\\"', '"') if m else None
        return SessionFileContent(
            content=r.content,
            file_name=file_name,
            content_type=r.headers.get("content-type"),
        )