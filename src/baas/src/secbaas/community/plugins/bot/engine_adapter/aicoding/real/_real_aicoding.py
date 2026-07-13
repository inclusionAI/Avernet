"""AICoding 引擎 adapter。

AICoding 在 engine 侧监听 WS 路径 ``/api/ws``（不带引擎名段），故 ``ws_path`` 返回该值。
无引擎特有的 session 语义:``session_consistency_key`` 用基类默认（session_id 优先，
否则返回 None，即不做 device 亲和）；``create_adapter_session`` 走基类通用创建/复用逻辑，
不加会话前缀。
"""

from __future__ import annotations

from ..._base import BaseEngineAdapter


class AICodingAdapter(BaseEngineAdapter):
    """AICoding 引擎 adapter —— WS 路径 ``/api/ws``。"""

    engine_type = "aicoding"
    _WS_PATH = "/api/ws"
