"""BotService SPI — shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class Result[T](BaseModel):
    """外部接口统一响应信封 ``{"success", "message", "error_code", "data"}``。

    ``data`` 的具体类型由泛型参数给出（如 ``Result[BotBindingData]``）；
    失败响应不带 data（为 ``None``）。
    """

    success: bool = False
    message: str = ""
    error_code: int | None = None
    data: T | None = None


@dataclass(slots=True)
class BotBindingData:
    """GET /api/service-bot/publish/{bot_id}/binding response data."""

    bot_id: str
    owner_id: str
    bot_type: str
    engine_type: str
    publish_id: int | None = None
    publish_status: str | None = None
    binding_id: int = 0
    device_provider: str = ""
    device_id: str = ""
    template_type: str | None = None
    # 仅在 HTTP 消费边界用于运行时引擎选择（get_binding 的 munging），出参不回填
    active_runtime_engine_type: str | None = None


@dataclass
class LogRelationPayload:
    """POST /api/bot-chat/log-relations 请求体"""

    biz_scene: str
    biz_task_id: str
    engine: str
    collector: str
    refs: list[dict[str, Any]] = field(default_factory=list)
    user_id: str = ""
    bot_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "biz_scene": self.biz_scene,
            "biz_task_id": self.biz_task_id,
            "engine": self.engine,
            "collector": self.collector,
            "refs": self.refs,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
        }


class CallerConnection(BaseModel):
    """app-caller-connection 就绪时返回的连接信息（契约 §4；token 不入日志）。"""

    ws_url: str | None = None
    token: str | None = None
    target: str | None = None
    paas_device_id: str | None = None
    baas_base_url: str | None = None
    engine_port: int | None = None
    tenant: str | None = None
    bot_uuid: str | None = None


class CallerInstance(BaseModel):
    """app-caller-connection 的实例信息（仅声明消费到的字段，其余忽略）。"""

    user_id: str | None = None
    bot_id: str | None = None
    owner_id: str | None = None
    status: str | None = None
    ext: dict[str, Any] | None = None


class CallerConnectionData(BaseModel):
    """app-caller-connection 响应的 ``data`` 载荷（就绪 / 处理中两种形态）。"""

    instance: CallerInstance | None = None
    connection: CallerConnection | None = None
    need_poll: bool = False


class IamTokenData(BaseModel):
    """GET /api/v1/token/iam 响应（非统一信封：只有 success + iam_token / error）。

    契约：``iam_token`` 只是请求 Cookie 的
    回显，不是新换取的 Caller 凭据——刷新结果体现为服务端运行时更新副作用。
    """

    success: bool = False
    iam_token: str | None = None
    error: str | None = None
