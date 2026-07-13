"""Shared utilities for the bot_run module."""

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import BotBindingInfo
from secbaas.community.spi.bot_service import BotBindingData

from ._bot_binding_resolver import _normalize_engine_type

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import BotChatContext
    from secbaas.community.core.repository.bot_run import BotRunRecord


def resolve_user_id(
    metadata: dict[str, Any],
    binding_info: BotBindingInfo | None,
    context: "BotChatContext | None",
    bot_id: str,
) -> str:
    """从 metadata 中解析 user_id

    优先级：
    1. 个人 bot（bot_type == "personal"）直接使用 entity_id
    2. metadata.sender_options.from = owner 时，取 binding_info.entity_id
    3. context.app_type == 'bot' 时，从 context.app_id（格式 bot_id:entity_id）解析 entity_id
    4. 否则取 context.app_id
    5. 最后 fallback 到 bot_id

    Args:
        metadata: 会话元数据
        binding_info: 绑定信息
        context: 请求上下文
        bot_id: 机器人 ID（最低优先级 fallback）

    Returns:
        str: 解析出的 user_id
    """
    # 1. 个人 bot 直接使用 entity_id
    if binding_info and binding_info.bot_type == "personal":
        return binding_info.entity_id

    # 2. 检查 sender_options
    sender_options = metadata.get("sender_options")
    if sender_options:
        from_field = sender_options.get("from")
        if from_field == "owner" and binding_info:
            return binding_info.entity_id

    # 3. 当 app_type == 'bot' 时，app_id 格式为 bot_id:entity_id，取 entity_id 部分
    # 4. 否则取 context.app_id
    if context:
        if context.app_type == "bot" and context.app_id:
            parts = context.app_id.split(":", 1)
            if len(parts) == 2:
                return parts[1]
        if context.app_id:
            return context.app_id

    # 5. 最后 fallback 到 bot_id
    return bot_id


def parse_bot_id(bot_id: str) -> tuple[str, str]:
    """解析 bot_id 为 real_bot_id 和 entity_id"""
    parts = bot_id.split(":", 1)
    real_bot_id = parts[0] if parts else ""
    entity_id = parts[1] if len(parts) == 2 else ""
    return real_bot_id, entity_id


def resolve_bot_id(bot_id: str, binding_info: BotBindingInfo | None) -> str:
    """根据 binding_info 解析实际 bot_id"""
    if binding_info is not None:
        if binding_info.device_provider in ("baas", "teclaw"):
            return binding_info.device_id
        return binding_info.bot_id
    return bot_id


def extract_lifecycle_stage(metadata: dict[str, Any] | None) -> str:
    """从 metadata 中提取 lifecycle_stage"""
    if not metadata:
        return "online"
    bot_options = metadata.get("bot_options")
    if isinstance(bot_options, dict):
        return bot_options.get("lifecycle_stage") or "online"
    return "online"


def extract_session_id_from_record(
    record: "BotRunRecord",
) -> str | None:
    """从运行记录中提取 session_id

    优先从 result_extra JSON 中取，降级从 metadata 中取。
    """
    if record.result_extra and isinstance(record.result_extra, dict):
        if "session_id" in record.result_extra:
            return record.result_extra["session_id"]
    if record.metadata and isinstance(record.metadata, dict):
        return record.metadata.get("session_id")
    return None


def parse_wait_result(metadata: dict[str, Any]) -> bool:
    """从 metadata 解析 ignore_content / ignore_result 标志 → wait_result

    优先读取 ignore_content（新），fallback 到 ignore_result（旧，兼容）。

    Returns:
        True 表示等待结果，False 表示不等待
    """
    # 优先 ignore_content
    if "ignore_content" in metadata:
        raw = metadata["ignore_content"]
    elif "ignore_result" in metadata:
        raw = metadata["ignore_result"]
    else:
        return True
    if isinstance(raw, bool):
        ignore = raw
    elif isinstance(raw, str):
        ignore = raw.strip().lower() == "true"
    else:
        ignore = bool(raw)
    return not ignore


def binding_data_to_info(data: BotBindingData) -> BotBindingInfo:
    """Convert SPI-layer BotBindingData to API-layer BotBindingInfo.

    Field mapping:
    - owner_id → entity_id
    - sandbox_id: device_id when device_provider == "arca", else None
    - device_props: always {} (BotBindingData has no device_props)
    - engine_type: normalized via _normalize_engine_type(active_engine, template_type);
      empty active_engine → "openclaw"; claude_code + {personalCoding,applicationCoding}
      template → "aicoding"; unknown → "openclaw" (with WARN)
    - baas_session_id: always None (set at runtime by BaasBotService)
    - publish_id / publish_status: dropped (no counterpart in BotBindingInfo)
    """
    return BotBindingInfo(
        bot_id=data.bot_id,
        entity_id=data.owner_id,
        sandbox_id=data.device_id if data.device_provider == "arca" else None,
        device_id=data.device_id,
        device_provider=data.device_provider,
        binding_id=data.binding_id,
        device_props={},
        bot_type=data.bot_type,
        engine_type=_normalize_engine_type(data.engine_type, data.template_type),
        baas_session_id=None,
    )


def build_chat_metadata(
    metadata: dict[str, Any] | None,
    run_id: str,
) -> dict[str, str] | None:
    """从 metadata 中构造 chat_metadata，用于透传到 WS chat 请求。

    参考 _report_log_relation 的取值逻辑，提取 biz_task_id / biz_scene。
    """
    metadata = metadata or {}
    biz_task_id = (
        metadata.get("biz_task_id")
        if metadata.get("biz_task_id") is not None
        else run_id
    )
    biz_scene = (
        metadata.get("biz_scene")
        if metadata.get("biz_scene") is not None
        else "default"
    )
    chat_metadata: dict[str, str] = {
        "biz_task_id": str(biz_task_id),
        "biz_scene": str(biz_scene),
    }
    return chat_metadata
