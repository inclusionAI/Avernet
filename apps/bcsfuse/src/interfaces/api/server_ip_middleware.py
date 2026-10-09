"""
ServerIpMiddleware — 统一注入 server_ip 到所有 JSON 响应

拦截所有 HTTP 响应，对 JSON body 注入 "server_ip" 字段，
避免在每个接口或错误处理中手动添加。

行为：
- 仅处理 Content-Type 为 application/json 的响应
- 非 JSON 响应（WebSocket、文件下载、StreamingResponse）不受影响
- JSON 解析失败时保持原样返回
- server_ip 通过 src.utils.env_utils.get_server_ip() 获取（lru_cache 缓存）
"""

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.utils.env_utils import get_server_ip

logger = logging.getLogger(__name__)


class ServerIpMiddleware(BaseHTTPMiddleware):
    """拦截所有 JSON 响应，统一注入 server_ip 字段。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 仅处理 JSON 响应
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # 读取 response body
        body = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body += chunk.encode("utf-8")
            else:
                body += chunk

        # 尝试解析 JSON 并注入 server_ip
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                data["server_ip"] = get_server_ip()
        except (json.JSONDecodeError, ValueError):
            # JSON 解析失败，保持原样返回
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json",
            )

        new_body = json.dumps(data, ensure_ascii=False).encode("utf-8")

        # 构建新 headers，更新 content-length
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )


__all__ = ["ServerIpMiddleware"]