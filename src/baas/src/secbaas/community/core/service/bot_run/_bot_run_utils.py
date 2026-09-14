"""Shared utilities for the bot_run module."""

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import BotBindingInfo
from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import BotBindingData
from secbaas.community.spi.eval_env import EvalSessionLog

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import BotChatContext
    from secbaas.community.core.repository.bot_run import BotRunRecord
    from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter
    from secbaas.community.spi.bot_service import BotServicePlugin

logger = get_logger("core-bot-run")

# openclaw 给持久化 session id 加的固定前缀; 路由亲和键必须对有无该前缀不敏感,
# 否则同一会话经 DingTalk(裸 id)与 Open API(带前缀 id)两次投递会哈希到不同实例。
_AGENT_MAIN_PREFIX = "agent:main:"

_SUPPORTED_ENGINES = frozenset(
    {"openclaw", "teclaw", "aicoding", "hermes", "claude_code", "deepseek_harness"}
)

_AICODING_FAMILY_TEMPLATES = frozenset({"personalCoding", "applicationCoding"})

BAAS_DEVICE_PROVIDERS = frozenset({"baas", "teclaw"})

#: caller 模式的 device_provider 标记（BotServiceSelector 据此路由到 CallerBotService）。
CALLER_DEVICE_PROVIDER = "caller"

#: 队列 meta 中承载 caller 容器 sandbox_id 的 key（入队时写入，worker 复用）。
CALLER_SANDBOX_META_KEY = "caller_sandbox_id"


def normalize_engine_type(
    active_engine: str | None, template_type: str | None = None
) -> str:
    """解析 engine_type,先按 template_type+active_engine 的特定组合归一化,再校验白名单。

    生产数据中存在 active_engine 与沙箱实际引擎不一致的脏数据:personalCoding/
    applicationCoding 模板的沙箱实为 aicoding,但 active_engine 被写成 claude_code。
    为兼容这类历史数据,仅当 ``template_type ∈ {personalCoding, applicationCoding}``
    且 ``active_engine == "claude_code"`` 时,强制按 aicoding 处理。其余情况(含
    template_type 为空)一律以 active_engine 为准,走白名单校验,空/未知回落 openclaw。
    """
    if template_type in _AICODING_FAMILY_TEMPLATES and active_engine == "claude_code":
        return "aicoding"
    if not active_engine:
        return "openclaw"
    if active_engine in _SUPPORTED_ENGINES:
        return active_engine
    logger.warning(
        "engine_type.unknown: active_engine=%r not in whitelist %s, fallback to openclaw",
        active_engine,
        sorted(_SUPPORTED_ENGINES),
    )
    return "openclaw"


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
    """从 metadata 中提取 lifecycle_stage

    支持 "eval" 返回值：当 metadata["bot_options"]["lifecycle_stage"] == "eval"
    时返回 "eval"，供 binding 解析走 eval binding 路由。
    """
    if not metadata:
        return "online"
    bot_options: dict[str, Any] | None = metadata.get("bot_options", {})
    if bot_options:
        stage = bot_options.get("lifecycle_stage")
        if stage:
            return stage
    return "online"


def extract_session_id_from_record(
    record: "BotRunRecord",
) -> str | None:
    """从运行记录中提取 session_id

    优先从 result_extra JSON 中取，降级从 metadata 中取。
    """
    if record.result_extra:
        if "session_id" in record.result_extra:
            return record.result_extra["session_id"]
    if record.metadata:
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
    - engine_type: normalized via normalize_engine_type(active_engine, template_type);
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
        engine_type=normalize_engine_type(data.engine_type, data.template_type),
        baas_session_id=None,
    )


def is_caller_mode(metadata: dict[str, Any] | None) -> bool:
    """metadata 携带 ``token`` 即视作 caller 模式。"""
    return bool(metadata and metadata.get("token"))


def build_caller_binding(bot_id: str, sandbox_id: str) -> BotBindingInfo:
    """用指定的 caller 容器 sandbox_id 构造 binding（``device_provider="caller"``）。"""
    real_bot_id, entity_id = parse_bot_id(bot_id)
    return BotBindingInfo(
        bot_id=real_bot_id,
        entity_id=entity_id,
        sandbox_id=sandbox_id,
        device_id=sandbox_id,
        device_provider=CALLER_DEVICE_PROVIDER,
    )


async def resolve_caller_binding(
    plugin: "BotServicePlugin",
    *,
    bot_id: str,
    metadata: dict[str, Any],
) -> BotBindingInfo:
    """caller 模式 binding：依赖 caller-connection 按 (bot_id, owner_id, user_id) 拉起新容器。

    与 ``binding_data_to_info``（按 (bot, owner) 解析既有发布设备）不同，caller 模式
    每次现拉容器，用 ``device_provider="caller"`` 标记，使 ``BotServiceSelector`` 路由到
    ``CallerBotService``，并把返回的 sandbox_id 作为连接目标（不参与 device affinity 选设备）。

    caller 模式由请求 metadata 携带 ``token`` 触发，凭 token 调 caller-connection。
    """
    real_bot_id, entity_id = parse_bot_id(bot_id)
    token = str(metadata.get("token") or "")
    user_id = str(metadata.get("user_id") or "") or resolve_user_id(
        metadata, None, None, real_bot_id
    )
    sandbox_id = await plugin.get_caller_connection(
        bot_id=real_bot_id,
        owner_id=entity_id,
        user_id=user_id,
        token=token,
    )
    logger.info(
        "[resolve_caller_binding] bot_id=%s owner_id=%s user_id=%s sandbox_id=%s",
        real_bot_id,
        entity_id,
        user_id,
        sandbox_id,
    )
    return build_caller_binding(bot_id, sandbox_id)


async def resolve_binding(
    plugin: "BotServicePlugin",
    *,
    bot_id: str,
    metadata: dict[str, Any],
    caller_sandbox_id: str | None = None,
) -> BotBindingInfo | None:
    """解析 bot_id 的 binding（runner 入队时与 worker 执行时共用，保证两条路径一致）。

    - caller 模式（metadata 携带 ``token``）：
      - 传入 ``caller_sandbox_id``（队列路径从 queue meta 读到入队时已建的容器）→ 直接复用；
      - 否则经 caller-connection 现拉新容器。
    - 否则按 (bot, owner) + lifecycle_stage 解析既有发布设备；NOT_FOUND 返回 None。
    """
    if is_caller_mode(metadata):
        if caller_sandbox_id:
            return build_caller_binding(bot_id, caller_sandbox_id)
        return await resolve_caller_binding(plugin, bot_id=bot_id, metadata=metadata)

    lifecycle_stage = extract_lifecycle_stage(metadata)
    real_bot_id, entity_id = parse_bot_id(bot_id)
    if not real_bot_id:
        return None
    try:
        data = await plugin.get_binding(
            bot_id=real_bot_id,
            owner_id=entity_id or "",
            stage=lifecycle_stage,
        )
    except PaasError as e:
        if e.code == ErrorCode.NOT_FOUND:
            logger.warning(
                "[resolve_binding] Bot binding unavailable: bot_id=%s, "
                "lifecycle_stage=%s, error=%s",
                bot_id,
                lifecycle_stage,
                e,
            )
            return None
        raise
    return binding_data_to_info(data)


def build_chat_metadata(
    metadata: dict[str, Any] | None,
    run_id: str,
    eval_session_log: EvalSessionLog,
) -> dict[str, str]:
    """从 metadata 中构造 chat_metadata，用于透传到 WS chat 请求。

    参考 _report_log_relation 的取值逻辑，提取 biz_task_id / biz_scene。
    当 metadata 中包含 eval_id / default_tag 时，通过
    EvalSessionLogProtocol Plugin 增加观测字段。
    title/model 复制到 chat_metadata，供 send 时
    _materialize_session 恢复会话属性（引擎忽略未知字段）。
    """
    metadata = metadata or {}
    biz_task_id = (
        metadata.get("biz_task_id")
        if metadata.get("biz_task_id") is not None
        else run_id
    )
    # eval 场景：default_tag 非空时 biz_scene 设为 "eval:{default_tag}"
    default_tag = metadata.get("default_tag")
    if default_tag:
        biz_scene = f"eval:{default_tag}"
    else:
        biz_scene = (
            metadata.get("biz_scene")
            if metadata.get("biz_scene") is not None
            else "default"
        )
    chat_metadata: dict[str, str] = {
        "biz_task_id": str(biz_task_id),
        "biz_scene": str(biz_scene),
    }
    # 评测标识透传：eval_id / default_tag 需传递给引擎 chat.send
    if metadata.get("eval_id"):
        chat_metadata["eval_id"] = str(metadata["eval_id"])
    if metadata.get("default_tag"):
        chat_metadata["default_tag"] = str(metadata["default_tag"])
    # eval 观测字段注入 — 委托 Plugin
    chat_metadata = eval_session_log.enrich_chat_metadata(
        metadata=chat_metadata,
        run_id=run_id,
    )
    # 在 enrich 之后写入，避免 Plugin 返回新 dict 时丢失
    if metadata.get("title"):
        chat_metadata["title"] = str(metadata["title"])
    if metadata.get("model"):
        chat_metadata["model"] = str(metadata["model"])
    return chat_metadata


def plan_session_id(
    *,
    engine_type: str | None,
    tc_bot_id: str,
    user_id: str,
    run_id: str,
    eval_id: str | None = None,
    adapter: "BotEngineAdapter | None" = None,
) -> str:
    """构造计划 session_id（与 consistency_key 计算规则完全一致），恒非 None。

    显式 session_id 的复用/透传由调用方前置处理，本函数仅在
    无显式 session_id 时被调用。
    """
    key = eval_id or run_id
    if adapter is not None:
        adapter_key = adapter.session_consistency_key(
            tc_bot_id=tc_bot_id,
            user_id=user_id,
            run_id=key,
        )
        if adapter_key is not None:
            return adapter_key

    if engine_type == "openclaw":
        return f"agent:main:session:{key}:user:{user_id}"
    return f"agent:{tc_bot_id}:session:{key}:user:{user_id}"


def extract_session_key_from_planned_id(planned_id: str) -> str:
    """从 planned session_id 中提取裸 session key（即 adapter 侧的 uuid）。

    planned id 格式：
    - openclaw → agent:main:session:{key}:user:{user_id}
    - claude_code / teclaw → agent:{tc_bot_id}:session:{key}:user:{user_id}

    adapter 的 create_session(uuid=...) 期望接收裸 key（如 c03ad14d-...），
    而非完整 planned id。本函数提取 ``session:`` 与 ``:user:`` 之间的部分。

    如果格式不匹配（非 plan_session_id 构造的 id），原样返回。
    """
    # 尝试匹配 "...session:{key}:user:{user_id}" 格式
    if ":session:" not in planned_id:
        return planned_id
    _, _, rest = planned_id.partition(":session:")
    key, sep, _ = rest.partition(":user:")
    if not sep:
        return planned_id
    return key


def strip_agent_main_prefix(session_id: str) -> str:
    """剥离前导 ``agent:main:`` 前缀,使设备路由亲和键对前缀有无不敏感。

    openclaw 会在持久化/返回 session id 时加上 ``agent:main:`` 前缀,而 DingTalk 入站
    携带的是裸 id;若直接把调用方原样传入的 session_id 作为 ``device_affinity`` 哈希,
    同一会话两次调用(裸 id vs 带前缀 id)会命中不同实例。在构造亲和键处统一剥离前缀,
    使两种形式哈希到同一设备。循环剥离以对重复前缀幂等。
    """
    while session_id.startswith(_AGENT_MAIN_PREFIX):
        session_id = session_id[len(_AGENT_MAIN_PREFIX) :]
    return session_id
