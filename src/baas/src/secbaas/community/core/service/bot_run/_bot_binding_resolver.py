"""Bot Binding Resolver — 一次查询解析完整 Binding 信息。

提供 BotBindingResolver，
用于将 app_type != "baas" 的 bot_id:entity_id 解析为完整的 binding 数据，
包括 device_provider、sandbox_id 等，避免重复 DB 查询。
"""

from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import BotBindingInfo
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.ac_bot import AcBotRepository
    from secbaas.community.core.repository.ac_bot_publish import (
        AcBotPublishRepository,
    )
    from secbaas.community.core.repository.device_binding import (
        DeviceBindingRepository,
    )

logger = get_logger("core-bot-run")

# 支持的 engine_type 白名单：openclaw/teclaw（老引擎）+ 3 个新引擎。
_SUPPORTED_ENGINES = frozenset(
    {"openclaw", "teclaw", "aicoding", "hermes", "claude_code"}
)

# 这两种 template_type 的沙箱实为 aicoding 引擎进程;当 active_engine 被错写成
# claude_code 时,强制按 aicoding 处理(兼容生产脏数据)。
_AICODING_FAMILY_TEMPLATES = frozenset({"personalCoding", "applicationCoding"})


def _normalize_engine_type(
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


def _resolve_effective_engine_type(
    active_engine: str | None,
    template_type: str | None = None,
    template_runtime_engine_type: str | None = None,
) -> str:
    """Resolve the effective engine from an explicit supported value or legacy rules.

    A non-empty ``template_runtime_engine_type`` takes precedence only when it is
    supported by BaaS. Empty or unsupported values fall back to the existing
    ``active_engine`` and ``template_type`` normalization rules; unsupported
    explicit values are logged to make configuration drift observable.
    """
    runtime_engine_type = (
        template_runtime_engine_type.strip()
        if isinstance(template_runtime_engine_type, str)
        else ""
    )
    if runtime_engine_type in _SUPPORTED_ENGINES:
        return runtime_engine_type
    if runtime_engine_type:
        logger.warning(
            "engine_type.unsupported_template_runtime: "
            "template_runtime_engine_type=%r not in whitelist %s, "
            "fallback to legacy normalization",
            runtime_engine_type,
            sorted(_SUPPORTED_ENGINES),
        )
    return _normalize_engine_type(active_engine, template_type)


class BotBindingResolver:
    """Binding 解析器 — 单次查询完整链路。

    从 bot_id + entity_id 出发，依次查询：
    ac_bots → ac_bot_publish（如 service bot）→ ac_entity_device_binding
    """

    def __init__(
        self,
        ac_bot_repo: "AcBotRepository",
        publish_repo: "AcBotPublishRepository",
        binding_repo: "DeviceBindingRepository",
    ):
        self._ac_bot_repo = ac_bot_repo
        self._publish_repo = publish_repo
        self._binding_repo = binding_repo

    def resolve(
        self,
        bot_id: str,
        entity_id: str | None,
        env: str,
        lifecycle_stage: str = "online",
    ) -> BotBindingInfo | None:
        """解析 bot 的完整 binding 信息。

        查询链路：ac_bots → ac_bot_publish（service bot）→ ac_entity_device_binding。

        Step 1: 查询 ac_bots 表获取 bot 记录及 entity_id。
        Step 2: 校验 personal bot 的 binding_id 不能为 None。
        Step 3: 对 service bot，根据 lifecycle_stage 解析 binding_id：
          - draft：binding_id 不能为 None，否则返回 None
          - online/verify：从 publish 表按 status 查询，允许 ac_bots.binding_id 为 None
        Step 4: 用 binding_id 查询 ac_entity_device_binding 获取设备信息，
          组装 BotBindingInfo 返回。

        Args:
            bot_id: Bot ID（bare）
            entity_id: 实体 ID，当 bot_id != 'default' 时可为 None
            env: 环境
            lifecycle_stage: Bot 生命周期阶段（draft/verify/online 等），
                              仅对 service bot 生效

        Returns:
            BotBindingInfo 或 None（bot 不存在或 binding 不满足校验规则时）
        """
        # Step 1: Query ac_bots
        result = self._query_ac_bot(bot_id, entity_id, env)
        if result is None:
            return None
        ac_bot, entity_id = result

        if not self._ensure_personal_bot_has_binding(bot_id, ac_bot):
            return None

        binding_id = ac_bot.binding_id
        engine_type = _normalize_engine_type(ac_bot.active_engine, ac_bot.template_type)

        # Step 2: For service bots, resolve binding based on lifecycle_stage
        if ac_bot.bot_type == "service":
            stages = (
                ["online", "verify", "draft"]
                if lifecycle_stage == "all"
                else [lifecycle_stage]
            )
            resolved_binding_id: int | None = None
            for stage in stages:
                resolved_binding_id = self._resolve_service_bot_binding(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    lifecycle_stage=stage,
                    draft_binding_id=binding_id,
                )
                if resolved_binding_id is not None:
                    break

            if resolved_binding_id is None:
                logger.warning(
                    f"[resolve] No binding found for service bot: bot_id={bot_id}, "
                    f"lifecycle_stage={lifecycle_stage}"
                )
                return None
            binding_id = resolved_binding_id

        # Step 3: Query binding for device info
        binding = self._binding_repo.get_by_id(binding_id)
        if binding is None:
            logger.warning(f"[resolve] Binding not found: binding_id={binding_id}")
            return None

        sandbox_id = (
            binding.device_props.get("sandbox_id") if binding.device_props else None
        )

        info = BotBindingInfo(
            bot_id=bot_id,
            entity_id=entity_id,
            sandbox_id=sandbox_id,
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            binding_id=binding.id,
            device_props=binding.device_props or {},
            bot_type=ac_bot.bot_type,
            engine_type=engine_type,
        )

        logger.info(
            f"[resolve] Resolved: bot_id={bot_id}, binding_id={binding_id}, "
            f"device_provider={info.device_provider}, device_id={info.device_id}"
        )
        return info

    # ── 私有方法 ──────────────────────────────────────────────────────────

    def _query_ac_bot(
        self,
        bot_id: str,
        entity_id: str | None,
        env: str,
    ) -> tuple[Any, str | None] | None:
        """查询 ac_bots 并返回 (ac_bot, resolved_entity_id)，不存在时返回 None。

        当 bot_id 不为 "default" 时，entity_id 可以为 None。

        """
        if bot_id == "default":
            if entity_id is None:
                logger.warning(
                    "[query_ac_bot] entity_id is required when bot_id='default'"
                )
                return None
            ac_bot = self._ac_bot_repo.get_by_entity_id_bot_id_env(
                entity_id=entity_id,
                bot_id=bot_id,
                env=env,
            )
        else:
            ac_bot = self._ac_bot_repo.get_by_bot_id_env_exclude_default(
                bot_id=bot_id,
                env=env,
            )
            if ac_bot is not None:
                entity_id = ac_bot.entity_id

        if ac_bot is None:
            logger.warning(
                f"[query_ac_bot] Bot not found: bot_id={bot_id}, entity_id={entity_id}"
            )
            return None

        return ac_bot, entity_id

    def _ensure_personal_bot_has_binding(self, bot_id: str, ac_bot: Any) -> bool:
        """校验 personal bot 的 binding_id 是否存在。

        personal bot 的 binding_id 不能为 None。
        service bot 的校验由 resolve 的 service bot 分支处理。

        Returns:
            True 表示校验通过，False 表示校验失败应提前返回
        """
        if ac_bot.bot_type == "personal" and ac_bot.binding_id is None:
            logger.warning(
                f"[ensure_personal_bot_has_binding] Personal bot has no binding: bot_id={bot_id}"
            )
            return False

        return True

    def _resolve_service_bot_binding(
        self,
        bot_id: str,
        entity_id: str,
        lifecycle_stage: str,
        draft_binding_id: int,
    ) -> int | None:
        """解析 service bot 的 binding_id。

        根据 lifecycle_stage 查询 publish 表获取对应的 binding_id。
        如果 stage 为 draft，则直接返回 draft_binding_id。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID
            lifecycle_stage: 生命周期阶段
            draft_binding_id: 草稿状态的 binding_id

        Returns:
            解析后的 binding_id
        """
        # lifecycle_stage -> status 映射
        stage_to_status = {
            "online": "success",
            "verify": "validating",
        }
        stage_status = stage_to_status.get(lifecycle_stage, "success")

        # draft 阶段不需要查询 publish 表
        if lifecycle_stage == "draft":
            logger.info(
                f"[resolve_service_bot_binding] Using draft binding_id={draft_binding_id} "
                f"for bot_id={bot_id}"
            )
            return draft_binding_id

        binding_id_from_publish = self._publish_repo.get_binding_id(
            source_bot_id=bot_id,
            status=stage_status,
            owner_id=entity_id,
        )
        if binding_id_from_publish is not None:
            logger.info(
                f"[resolve_service_bot_binding] Using binding_id={binding_id_from_publish} "
                f"from lifecycle_stage={lifecycle_stage} (status={stage_status}), "
                f"was draft={draft_binding_id}"
            )
            return binding_id_from_publish
        else:
            logger.info(
                f"[resolve_service_bot_binding] No publish record for "
                f"lifecycle_stage={lifecycle_stage}"
            )
            return None
