"""内测名额服务。

该服务不直接访问数据库，只组合使用 ``CommonConfigService`` 读写
``ac_common_config`` 中的内测名额配置，承载查询与按增量调整的语义。
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.log import get_logger


logger = get_logger()

BUSINESS_CODE = "teamclaw_access"
PARAM_CODE = "beta_invite_quota"
# 加白对象类型：与现有白名单 check 一致，使用工号（staffId）作为 entity_id
WHITELIST_ENTITY_TYPE = "staff"


class BetaQuotaService:
    """基于通用配置的内测名额管理服务。"""

    @inject
    def __init__(
        self,
        common_config_service: CommonConfigService,
        policy_service: PolicyServiceProtocol,
    ) -> None:
        self._common_config_service = common_config_service
        self._policy_service = policy_service

    def get_quota(self, env: str) -> dict[str, int]:
        """读取内测名额：返回 ``{"total", "remaining"}``。

        配置缺失时抛 ``ValueError("内测名额配置不存在")``，由调用方转为业务错误。
        """
        total, remaining, _ = self._read(env)
        return {"total": total, "remaining": remaining}

    def adjust_quota(self, env: str, delta: int, entity_id: str) -> dict[str, int]:
        """按增量调整剩余名额（负=占用，正=增配）。

        read-modify-write：读取 ``{total, remaining}``，计算
        ``new = remaining + delta``；``new < 0`` 抛 ``ValueError("名额不足")``
        （纯计算前置，名额不足时不触发加白）。

        加白是写入名额的前提：采用"先加白后扣名额"顺序——先确保当前调用方
        ``entity_id``（工号）在 ``ac_access_control_policy`` 加白为 policy=on，
        加白失败抛 ``ValueError("申请试用白名单失败")`` 且**名额不写入**；
        加白成功后再写回 ``{total, new}`` 并返回新值。
        """
        total, remaining, config_id = self._read(env)
        new_remaining = remaining + delta
        if new_remaining < 0:
            raise ValueError("名额不足")
        # 先加白：加白失败则整体失败，名额不写入，避免"名额已扣却报错"的不一致。
        self._ensure_whitelisted(entity_id)
        self._common_config_service.update_config(
            config_id=config_id,
            updates={"param_value": {"total": total, "remaining": new_remaining}},
        )
        logger.info(
            "[beta_quota.adjust] env=%s delta=%s remaining=%s->%s total=%s",
            env,
            delta,
            remaining,
            new_remaining,
            total,
        )
        return {"total": total, "remaining": new_remaining}

    def _ensure_whitelisted(self, entity_id: str) -> None:
        """无条件确保 ``entity_id`` 加白为 policy=on。

        直接调用 ``allow()``（merge 模式 upsert）：无记录则插入 policy=on，
        已有 policy=off 则翻成 on，已是 on 则幂等，并保留其他 key。

        加白是名额写入的前提：失败时记录原始异常细节（供排查），并对外抛出
        ``ValueError("申请试用白名单失败")``，使 ``adjust_quota`` 整体失败、
        名额不写入。对外 message 固定，不拼接内部异常字符串以免泄露内部细节。
        """
        try:
            self._policy_service.allow(
                entity_id=entity_id, entity_type=WHITELIST_ENTITY_TYPE
            )
            logger.info(
                "[beta_quota.adjust] whitelisted entity_id=%s type=%s",
                entity_id,
                WHITELIST_ENTITY_TYPE,
            )
        except Exception as exc:  # noqa: BLE001 — 记录原始异常后转为对外固定 message
            logger.error(
                "[beta_quota.adjust] whitelist entity_id=%s failed: %s",
                entity_id,
                exc,
            )
            raise ValueError("申请试用白名单失败") from exc

    def _read(self, env: str) -> tuple[int, int, int]:
        config = self._common_config_service.get_config(
            business_code=BUSINESS_CODE, param_code=PARAM_CODE, env=env
        )
        if config is None:
            raise ValueError("内测名额配置不存在")
        if config.get("enable") != "1":
            raise ValueError("内测未开放")
        value: Any = config.get("param_value")
        if not isinstance(value, dict):
            raise ValueError("内测名额配置不存在")
        return int(value["total"]), int(value["remaining"]), int(config["id"])
