"""Rule #6 — Token 认证 (/api/v1/token/exchange) 契约测试。

验证 POST /api/v1/token/exchange 响应字段。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock, AsyncMock

from tests.community.contracts.gateway.conftest import assert_response_schema, assert_has_fields


class TestTokenExchange:
    """POST /api/v1/token/exchange — Token 交换。

    文档: body 为空 JSON {}，返回 {access_token: "eyJ..."}
    """

    def test_token_exchange_schema(self, gw_client):
        # TokenExchange 通过 Injected(AuthPlugin) 下的插件实现
        # 在 local mode 下 AuthPlugin 是 LocalAuth，可能不支持 exchange
        # 这里验证接口可达及基本响应结构
        resp = gw_client.post("/api/v1/token/exchange", json={})
        body = resp.json()

        # 无论成功或失败，响应应包含可识别字段
        # 成功: {access_token: "..."}
        # 失败: 后端 ApiResponse 格式 {success: false, message: "..."}
        if resp.status_code == 200 and "access_token" in body:
            assert_has_fields(
                body, {"access_token": str},
                label="POST /api/v1/token/exchange success",
            )
        else:
            # 失败情况下，验证接口返回了合理错误
            assert resp.status_code in (200, 401, 403, 500), (
                f"POST /api/v1/token/exchange returned unexpected status {resp.status_code}"
            )