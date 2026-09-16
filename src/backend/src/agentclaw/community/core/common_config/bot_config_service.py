"""Bot-scoped JSON configuration and persisted storage selection.

Generic JSON access and storage policy remain separate services/contracts;
co-located here to keep the Bot configuration implementation reviewable.
"""

import json
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from injector import inject

from agentclaw.community.core.repository.protocols.config import (
    BotCommonConfigRepositoryProtocol,
)
from agentclaw.community.core.common_config.bot_config_protocol import (
    BotCommonConfigServiceProtocol,
    BotStoragePolicyProtocol,
    StoragePolicy,
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
    ) -> Any:
        value = self._repo.get(
            bot_id=bot_id, entity_id=entity_id, env=env, config_key=config_key
        )
        return json.loads(value) if value is not None else None

    def set_config(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, value: Any
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
    *, config: Any, engine: str, user_id: str | None
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
    ) -> None:
        self._repo = repository
        self._bot_config = bot_config
        self._common_config = common_config

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
            if isinstance(value, dict) and value.get("storage_type") in {"nas", "upfs"}:
                return StoragePolicy(
                    value["storage_type"], str(value.get("source", ""))
                )
            if value is not None:
                logger.warning(
                    "[storage_policy] invalid policy bot_id=%s env=%s", bot_id, env
                )
        except Exception:
            logger.warning(
                "[storage_policy] read failed bot_id=%s env=%s",
                bot_id,
                env,
                exc_info=True,
            )
        return None

    def initialize(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        engine: str,
        user_id: str | None,
        upfs_ready: Callable[[], bool],
    ) -> StoragePolicy:
        """Return the saved choice or atomically create it; never create a device.

        This is the reusable storage_policy operation. Callers supply template
        readiness as a callback so persistence and rollout remain transport-free.
        """
        scope = dict(bot_id=bot_id, entity_id=entity_id, env=env)

        # Strict reads here: unlike compatibility reads during restart, a failed
        # read must not overwrite an already committed decision on a create retry.
        def read_strict() -> StoragePolicy | None:
            raw = self._repo.get(**scope, config_key=STORAGE_POLICY)
            if raw is None:
                return None
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("storage_type") not in {
                "upfs",
                "nas",
            }:
                raise ValueError("invalid persisted storage policy during creation")
            return StoragePolicy(value["storage_type"], str(value.get("source", "")))

        policy = read_strict()
        if policy is not None:
            return policy

        def decide() -> str:
            storage_type = "nas"
            try:
                config = self._common_config.get_config(
                    business_code="bot_storage",
                    param_code="upfs_rollout",
                    env=env,
                )
                decision = _should_rollout_upfs(
                    config=config, engine=engine, user_id=user_id
                )
                value = config.get("param_value") if isinstance(config, dict) else None
                version = value.get("version", "") if isinstance(value, dict) else ""
                logger.info(
                    "[upfs_rollout] bot_id=%s env=%s version=%s reason=%s",
                    bot_id,
                    env,
                    version,
                    decision.reason,
                )
                if decision.enabled and upfs_ready():
                    storage_type = "upfs"
            except Exception:
                # Capability/config READ failures are fail-open to NAS; the
                # transaction itself and all writes remain outside this catch.
                logger.warning(
                    "[upfs_rollout] precheck failed bot_id=%s env=%s",
                    bot_id,
                    env,
                    exc_info=True,
                )
            return json.dumps(
                {
                    "storage_type": storage_type,
                    "source": "rollout",
                }
            )

        self._repo.initialize_once(**scope, config_key=STORAGE_POLICY, factory=decide)
        policy = read_strict()
        if policy is None:
            raise ValueError("storage policy missing or deleted after initialization")
        return policy
