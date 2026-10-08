"""Bot 领域数据模型定义

定义 Bot 对话服务相关的领域模型，使用 dataclass 保持轻量。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class BotBindingInfo:
    """Binding 信息 — 一次查询解析的完整结果。

    Attributes:
        bot_id: Bot ID（bare，不含 entity_id）
        entity_id: 实体 ID（从 app_id 解析）
        sandbox_id: Arca 沙箱 ID（仅 device_provider="arca" 时有值）
        device_id: 设备 ID（来自 ac_entity_device_binding）
        device_provider: 设备提供商（arca/baas/daas/local）
        binding_id: 设备绑定 ID
        device_props: 设备属性字典
        bot_type: Bot 类型（personal/service）
        baas_session_id: BaaS 会话持久化 ID（由 BaasBotService.create_session 设置，
                         用于后续 send_message 中标记会话完成/失败状态）
    """

    bot_id: str
    entity_id: str
    sandbox_id: str | None = None
    device_id: str = ""
    device_provider: str = ""
    binding_id: int = 0
    device_props: dict[str, Any] = field(default_factory=dict)
    bot_type: str = "personal"
    baas_session_id: str | None = None
    engine_type: str = "openclaw"


@dataclass(slots=True)
class BotChatContext:
    """Bot 对话上下文

    用于传递身份标识、认证等上下文信息。
    在 Bot 对话服务中用于身份认证、权限校验等场景。
    """

    api_key_prefix: str  # 安全密钥前缀
    app_id: str  # 应用 ID
    app_type: str  # 应用类型
    iam_token: str | None = None  # IAM_TOKEN
    tenant: str = ""  # 租户
    extra: dict[str, Any] | None = None  # 扩展字段

    def build_auth_token(self) -> str:
        """构建认证令牌

        目前基于 api key 信息构建，格式为 OPEN_API:app:<api_key_prefix>。
        表示基于 app 授权，可以通过 api_key_prefix 反查身份。
        """
        return f"OPEN_API:app:{self.api_key_prefix}"

    @classmethod
    def from_api_key(
        cls,
        api_key_prefix: str,
        app_id: str,
        app_type: str = "UNKNOWN",
        iam_token: str | None = None,
        tenant: str = "",
    ) -> "BotChatContext":
        """Build BotChatContext from API key fields (no FastAPI dependency)."""
        return cls(
            api_key_prefix=api_key_prefix,
            app_id=app_id,
            app_type=app_type,
            iam_token=iam_token,
            tenant=tenant,
        )


@dataclass(slots=True)
class SessionInfo:
    """会话信息

    包含会话的元数据和管理信息。
    当前用于单轮对话，预留多轮对话扩展能力。
    """

    session_id: str
    bot_id: str
    status: str = "active"  # "active", "closed"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class BotResponse:
    """Bot 响应

    Bot 对话的返回结果。
    """

    content: str
    usage: dict[str, Any] | None = None  # token 消耗等
    metadata: dict[str, Any] | None = None  # 扩展字段


@dataclass(slots=True)
class MessageInfo:
    """消息信息

    包含消息的元数据和内容。
    """

    id: str
    session_id: str
    role: str
    content: str
    meta: dict[str, Any] | None = None
    created_at: str | None = None
    history_meta: dict[str, Any] | None = None


@dataclass(slots=True)
class MessageDeliverRequest:
    """消息投递请求

    用于 /openapi/v1/messages 端点，显式指定 bot_id 和 raw_session_id。
    """

    message: str
    bot_id: str
    raw_session_id: str


@dataclass(slots=True)
class MessageContent:
    """消息内容

    预留多模态支持（文本、图片、文件等）。
    当前只支持文本，未来可扩展。
    """

    text: str
    # 未来可扩展: attachments, images, files 等
    attachments: list[dict[str, Any]] | None = None
