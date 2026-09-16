"""Shared utilities for the bot_run module."""

from typing import TYPE_CHECKING, Any

import aiohttp

from secbaas.community.api.bot_runtime import BotBindingInfo
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import BotBindingData
from secbaas.community.spi.eval_env import EvalSessionLog

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import BotChatContext
    from secbaas.community.core.repository.bot_run import BotRunRecord
    from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

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
    """metadata 携带 ``cookie`` 即视作 caller 模式。

    ``cookie`` 同时是模式开关与拉容器的 IAM 凭据：值只允许存在于内存
    请求链路（runner 入口 → 后台 dispatch 闭包），落库前必须经
    :func:`strip_sensitive_metadata` 剥离。
    """
    return bool(metadata and metadata.get("cookie"))


def strip_sensitive_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """落库副本：剥离敏感凭据（cookie），其余键原样保留。

    run 记录 / queue meta 等持久化处一律使用本函数的返回值；原始
    ``metadata``（含 cookie）只在内存链路（resolver 拉容器、后台任务
    闭包）流转。
    """
    if "cookie" not in metadata:
        return metadata
    return {k: v for k, v in metadata.items() if k != "cookie"}


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


class _DefaultEngineAdapter:
    """core 内置兜底 adapter：adapter 未传入/未注册时的通用亲和键格式。

    core 不得 import plugins（registry 装配在 bootstrap 完成），plan 的
    兜底语义在 core 内以最小 duck-typed 实现提供，与 plugins 侧
    ``BaseEngineAdapter`` 的默认格式保持一致。
    """

    engine_type = ""

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str,
    ) -> str:
        return f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"


_DEFAULT_ENGINE_ADAPTER = _DefaultEngineAdapter()


def plan_session_id(
    *,
    tc_bot_id: str,
    user_id: str,
    run_id: str,
    eval_id: str | None = None,
    adapter: "BotEngineAdapter | None" = None,
) -> str:
    key = eval_id or run_id
    effective = adapter if adapter is not None else _DEFAULT_ENGINE_ADAPTER
    session_id = effective.session_consistency_key(
        tc_bot_id=tc_bot_id,
        user_id=user_id,
        run_id=key,
    )
    return session_id


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


def safe_client_msg(exc: Exception) -> str:
    """返回可安全外抛给客户端的异常消息(剥离 aiohttp 请求 url 等内部信息)。

    aiohttp.ClientResponseError 的 str(),
    其中 url 是内部代理地址,不应外泄。这里只取业务 message。
    其他 aiohttp.ClientError 子类(如 ClientConnectorError / InvalidURL)的 str()
    同样可能包含内部 hostname/URL,统一返回通用消息;完整异常由调用方记入日志。
    """
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.message or f"HTTP {exc.status}"
    if isinstance(exc, aiohttp.ClientError):
        return "Connection failed"
    return str(exc)
