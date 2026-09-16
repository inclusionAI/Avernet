"""Bot-scoped JSON configuration and storage business decisions.

Generic JSON access and storage policy remain separate services/contracts.
Storage policy owns creation eligibility, template readiness and deploy decisions;
HTTP queries and device allocation stay outside this component.
"""

import json
from dataclasses import dataclass, replace
from typing import Any
from collections.abc import Callable

from injector import inject
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_baas_engine_bucket,
)
from agentclaw.community.core.devices.services.baas_template_resolver import (
    BaasTemplateResolveError,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
    Storage,
    StorageType,
)
from agentclaw.community.core.service_bot.services.deploy.deploy_config_composer import (
    BotDeployContext,
)

from agentclaw.community.core.repository.protocols.config import (
    BotCommonConfigRepositoryProtocol,
)
from agentclaw.community.core.common_config.bot_config_protocol import (
    BotCommonConfigServiceProtocol,
    BotStoragePolicyProtocol,
    StoragePolicy,
    PreparedBotStoragePolicy,
    JsonValue,
)
from agentclaw.community.core.common_config.common_config_service_protocol import (
    CommonConfigServiceProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()
STORAGE_POLICY = "storage_policy"


class BotCommonConfigService(BotCommonConfigServiceProtocol):
    @inject
    def __init__(self, repository: BotCommonConfigRepositoryProtocol) -> None:
        self._repo = repository

    def get_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str
    ) -> JsonValue:
        value = self._repo.get(
            bot_id=bot_id, entity_id=entity_id, env=env, config_key=config_key
        )
        return json.loads(value) if value is not None else None

    def set_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, value: JsonValue
    ) -> None:
        self._repo.put(
            bot_id=bot_id,
            entity_id=entity_id,
            env=env,
            config_key=config_key,
            config_value=json.dumps(value, ensure_ascii=False),
        )


@dataclass(frozen=True)
class _UpfsRolloutDecision:
    enabled: bool
    reason: str


def _should_rollout_upfs(
    *, config: Any, engine: str, user_id: str
) -> _UpfsRolloutDecision:
    if not isinstance(config, dict) or str(config.get("enable")) != "1":
        return _UpfsRolloutDecision(False, "disabled")
    value = config.get("param_value")
    if not isinstance(value, dict):
        return _UpfsRolloutDecision(False, "invalid_config")
    allow_all = value.get("allow_all", False)
    if type(allow_all) is not bool:
        return _UpfsRolloutDecision(False, "invalid_allow_all")
    if allow_all:
        return _UpfsRolloutDecision(True, "allow_all")
    if not isinstance(user_id, str) or not user_id.strip():
        return _UpfsRolloutDecision(False, "missing_user_id")
    rules = value.get("rules", [])
    if not isinstance(rules, list):
        return _UpfsRolloutDecision(False, "invalid_rules")
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("engine_bucket") != engine:
            continue
        all_users = rule.get("allow_all_users", False)
        users = rule.get("allow_user_groups", [])
        tail = rule.get("user_tail_digit_max", -1)
        if (
            type(all_users) is not bool
            or not isinstance(users, list)
            or any(not isinstance(user, str) for user in users)
            or type(tail) is not int
            or tail > 9
        ):
            continue
        if all_users:
            return _UpfsRolloutDecision(True, "allow_all_users")
        if 0 <= tail <= 9 and user_id[-1] in "0123456789" and int(user_id[-1]) <= tail:
            return _UpfsRolloutDecision(True, "user_tail_digit")
        if user_id in users:
            return _UpfsRolloutDecision(True, "allow_user")
    return _UpfsRolloutDecision(False, "rule_not_matched")


class BotStoragePolicyService(BotStoragePolicyProtocol):
    @inject
    def __init__(
        self,
        repository: BotCommonConfigRepositoryProtocol,
        bot_config: BotCommonConfigServiceProtocol,
        common_config: CommonConfigServiceProtocol,
        *,
        env: str,
        select_provider: Callable[..., str],
        resolve_template: Callable[..., Any],
        get_template: Callable[[str], dict[str, Any]],
    ) -> None:
        self._repo = repository
        self._bot_config = bot_config
        self._common_config = common_config
        self._env = env
        self._select_provider = select_provider
        self._resolve_template = resolve_template
        self._get_template = get_template

    def prepare_bot_storage_policy(self, **kwargs: Any) -> dict[str, Any]:
        """Creation-only business orchestration; allocation still uses apply_device.

        Scope is the original creation entry; Provider/Template rollout decides
        eligibility. Publish/upgrade/caller instances never enter this entry.
        """
        bot_type = (kwargs.get("bot_type") or "").strip()
        engine = kwargs.get("engine") or DEFAULT_ENGINE_TYPE
        provider = self._select_provider(
            user_id=kwargs["operator"].staff_id,
            bot_type=bot_type,
            engine_type=engine,
            template_type=kwargs.get("template_type") or "",
        )
        if provider != "baas":
            # Do not pin non-BaaS providers: some boots (singlebox/test) inject a
            # different rollout policy than the one inside the device router.
            # Returning no override preserves the router's original rollout path.
            return {}
        overrides = {"device_provider": provider}
        bot_id = kwargs.get("bot_id") or "default"
        entity_id = kwargs.get("entity_id") or kwargs["operator"].staff_id
        owner_id = kwargs.get("owner_id") or entity_id
        config = dict(kwargs.get("template_config") or {})
        config.pop("_prepared_bot_creation", None)
        template_uid = config.get("template_uid")
        if not isinstance(template_uid, str) or not template_uid.strip():
            raise BaasTemplateResolveError(
                "provider=baas allocation requires non-empty template_config.template_uid"
            )
        template = self._resolve_template(
            bot_id=bot_id,
            user_id=owner_id,
            env=self._env,
            bot_type=bot_type,
            engine_type=engine,
            template_type=kwargs.get("template_type"),
            template_config=config,
        )
        choice = self.initialize_for_template(
            bot_id=bot_id,
            entity_id=entity_id,
            env=self._env,
            engine=resolve_baas_engine_bucket(
                engine_type=engine,
                template_type=kwargs.get("template_type"),
                template_config=config,
            ),
            user_id=owner_id,
            template_uuid=template.template_uuid,
        )
        config["_prepared_bot_creation"] = PreparedBotStoragePolicy(
            template_uid.strip(),
            template.template_uuid,
            choice,
        )
        return {**overrides, "template_config": config}

    def initialize_for_template(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        engine: str,
        user_id: str,
        template_uuid: str,
    ) -> StorageType:
        """Evaluate template readiness only after the creation rollout matches."""

        def ready() -> bool:
            template = self._get_template(template_uuid)
            config = template.get("config") if isinstance(template, dict) else None
            volume = None
            if isinstance(config, dict):
                volume_env = (env or "").lower()
                volume = (
                    config.get(f"upfs_volume_id_{volume_env}")
                    if volume_env in {"pre", "prod"}
                    else None
                )
                volume = volume or config.get("upfs_volume_id")
            result = (
                isinstance(template, dict)
                and template.get("template_uuid") == template_uuid
                and template.get("type") == "ARCA"
                and template.get("status") == "ONLINE"
                and isinstance(config, dict)
                and config.get("type") == "ARCA"
                and isinstance(volume, str)
                and bool(volume.strip())
                and volume == volume.strip()
            )
            log = logger.info if result else logger.warning
            log(
                "[upfs_rollout] event=template_precheck bot_id=%s entity_id=%s env=%s template_uuid=%s ready=%s",
                bot_id,
                entity_id,
                env,
                template_uuid,
                result,
            )
            return result

        policy = self.initialize(
            bot_id=bot_id,
            entity_id=entity_id,
            env=env,
            engine=engine,
            user_id=user_id,
            upfs_ready=ready,
        )
        return policy.storage_type

    def resolve_deploy_context(self, ctx: BotDeployContext) -> BotDeployContext:
        """Resolve once so start command, mounts and storage use the same layout.

        No type/stage/migration restriction: reuse the pinned creation choice or
        the saved policy, falling back to NAS. Publish, upgrade and Caller
        payloads therefore read the same policy as creation and restart.
        """
        choice = ctx.storage_type
        if choice is None:
            policy = self._resolve_storage_policy(
                ctx.bot_id, ctx.entity_id, ctx.env or self._env
            )
            choice = policy.storage_type if policy else StorageType.NAS
        return replace(
            ctx,
            storage_type=choice,
            mount_home_dir_storage=True
            if choice == StorageType.UPFS
            else ctx.mount_home_dir_storage,
        )

    def apply_to_storage(self, storage: Storage, ctx: BotDeployContext) -> Storage:
        """Apply the resolved choice and shared quota, without changing identity."""
        env = ctx.env or self._env
        if ctx.storage_type is not None:
            storage.type = ctx.storage_type
        storage.quota = self.get_storage_quota(env)
        logger.info(
            "[storage_policy] event=storage_composed bot_id=%s entity_id=%s env=%s storage_type=%s quota=%s",
            ctx.bot_id,
            ctx.entity_id,
            env,
            storage.type,
            storage.quota,
        )
        return storage

    def get_storage_quota(self, env: str) -> str:
        """Shared NAS/UPFS quota travels through the original Storage.quota string field."""
        default = "1G"
        try:
            config = self._common_config.get_config(
                business_code="bot_storage", param_code="storage", env=env
            )
            params = config.get("param_value") if isinstance(config, dict) else None
            value = params.get("quota") if isinstance(params, dict) else None
            if isinstance(value, str) and value.strip():
                return value
            logger.warning(
                "[storage_policy] event=quota_fallback reason=invalid_or_missing env=%s quota=%s",
                env,
                default,
            )
        except Exception as exc:
            logger.warning(
                "[storage_policy] event=quota_fallback reason=read_error env=%s quota=%s error_type=%s",
                env,
                default,
                type(exc).__name__,
            )
        return default

    def _resolve_storage_policy(
        self, bot_id: str, entity_id: str, env: str
    ) -> StoragePolicy | None:
        try:
            value = self._bot_config.get_config(
                bot_id=bot_id,
                entity_id=entity_id,
                env=env,
                config_key=STORAGE_POLICY,
            )
            if value is None:
                logger.info(
                    "[storage_policy] event=fallback reason=missing_policy bot_id=%s entity_id=%s env=%s storage_type=nas",
                    bot_id,
                    entity_id,
                    env,
                )
                return None
            if not isinstance(value, dict) or value.get("storage_type") not in {
                "nas",
                "upfs",
            }:
                raise ValueError("invalid persisted storage policy")
            logger.info(
                "[storage_policy] event=reused bot_id=%s entity_id=%s env=%s storage_type=%s",
                bot_id,
                entity_id,
                env,
                value["storage_type"],
            )
            return StoragePolicy(
                StorageType(value["storage_type"]), str(value.get("source", ""))
            )
        except Exception as exc:
            logger.error(
                "[storage_policy] event=read_failed bot_id=%s entity_id=%s env=%s error_type=%s",
                bot_id,
                entity_id,
                env,
                type(exc).__name__,
            )
            raise

    def initialize(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        engine: str,
        user_id: str,
        upfs_ready: Callable[[], bool],
    ) -> StoragePolicy:
        """Reuse a saved policy or persist UPFS only; never create a device.

        This is the reusable storage_policy operation. Callers supply template
        readiness as a callback so persistence and rollout remain transport-free.
        """
        scope = dict(bot_id=bot_id, entity_id=entity_id, env=env)

        # Reuse a committed UPFS decision even after rollout is disabled.
        policy = self._resolve_storage_policy(**scope)
        if policy is not None:
            logger.info(
                "[storage_policy] event=initialization_reused bot_id=%s entity_id=%s env=%s storage_type=%s rollout_skipped=true",
                bot_id,
                entity_id,
                env,
                policy.storage_type,
            )
            return policy

        try:
            config = self._common_config.get_config(
                business_code="bot_storage",
                param_code="upfs_rollout",
                env=env,
            )
            decision = _should_rollout_upfs(
                config=config, engine=engine, user_id=user_id
            )
            logger.info(
                "[upfs_rollout] event=evaluated bot_id=%s entity_id=%s env=%s engine=%s enabled=%s reason=%s",
                bot_id,
                entity_id,
                env,
                engine,
                decision.enabled,
                decision.reason,
            )
            ready = decision.enabled and upfs_ready()
        except Exception as exc:
            logger.warning(
                "[upfs_rollout] event=fallback reason=precheck_error bot_id=%s entity_id=%s env=%s storage_type=nas error_type=%s",
                bot_id,
                entity_id,
                env,
                type(exc).__name__,
            )
            ready = False
        if not ready:
            # NAS follows the old flow. Never insert a NAS row or placeholder.
            logger.info(
                "[storage_policy] event=initialization_skipped reason=not_selected bot_id=%s entity_id=%s env=%s storage_type=nas",
                bot_id,
                entity_id,
                env,
            )
            return StoragePolicy(StorageType.NAS)

        try:
            self._repo.initialize_once(
                **scope,
                config_key=STORAGE_POLICY,
                config_value=json.dumps({"storage_type": "upfs", "source": "rollout"}),
            )
            policy = self._resolve_storage_policy(**scope)
            if policy is None:
                raise ValueError(
                    "storage policy missing or deleted after initialization"
                )
        except Exception as exc:
            logger.error(
                "[storage_policy] event=initialization_failed phase=persist_or_verify bot_id=%s entity_id=%s env=%s error_type=%s",
                bot_id,
                entity_id,
                env,
                type(exc).__name__,
            )
            raise
        logger.info(
            "[storage_policy] event=initialization_complete bot_id=%s entity_id=%s env=%s storage_type=%s",
            bot_id,
            entity_id,
            env,
            policy.storage_type,
        )
        return policy
