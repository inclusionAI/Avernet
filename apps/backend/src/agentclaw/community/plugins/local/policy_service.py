"""LocalPolicyService — singlebox / 本机开发模式的 PolicyService 实现。

singlebox 是单用户本机环境,没有 "白名单 + 配额 + 抢名额" 的业务需求:
- 只有当前登录人一个 user,没有多 user 抢容器配额一说;
- backend SQLite 每次 ``./scripts/singlebox.sh start backend`` 都会被 ``rm`` 重建
  (见 backend.sh:63),不可能在里面长期维护 ``ac_access_control_policy`` /
  ``ac_common_config`` 的种子数据;
- 真 prod 上这些表由运维种,有真实数据,``PolicyService`` (走 DB 查询的实现)
  是对的。

为何不在 ``PolicyService.check`` 里按 Env 增加 singlebox 旁路: 那会把
实现选择散落到业务代码。composition root 改由 ``DeployProfile.SINGLEBOX``
安装 ``LocalPolicyService``，其它 Profile 使用各自的 Policy 绑定；下游不再
读取 Env 来决定实现。
"""
from __future__ import annotations

from agentclaw.community.log import get_logger

logger = get_logger()


class LocalPolicyService:
    """singlebox 全开放: ``check`` 始终 True，写方法 no-op，配额近似无限。

    Structural 实现 ``api.policy_service.PolicyServiceProtocol`` —— **故意不显式继承**,
    跟 ``core.access.services.policy_service.PolicyService`` (真实现) 同款 duck-typing
    姿势。原因: arch test ``test_plugins_layer_does_not_import_api`` 禁止 plugins/
    import api/;显式继承会引入 ``from agentclaw.community.api.policy_service import
    PolicyServiceProtocol`` 触发 violation。Structural 实现满足 Protocol runtime check
    (``isinstance(x, PolicyServiceProtocol)`` 仍 True),DI 层在 ``SingleboxAccessModule``
    的 provider 上用 ``-> PolicyServiceProtocol`` 标注返回类型,plugins/ 这层
    保持对 api/ 零依赖。

    不继承 ``MockSeam``: PolicyServiceProtocol 不是 ``Plugin``,无 impl_registry
    可识别。由 ``SingleboxAccessModule`` 的 typed provider 直接提供实例。
    """

    def check(self, *, entity_id: str, entity_type: str) -> bool:
        logger.info(
            "[LocalPolicyService] check(entity_id=%s, entity_type=%s) -> True "
            "(singlebox 全开放)",
            entity_id, entity_type,
        )
        return True

    def allow(self, *, entity_id: str, entity_type: str) -> None:
        logger.info(
            "[LocalPolicyService] allow(entity_id=%s) -> no-op (singlebox 无需白名单)",
            entity_id,
        )

    def disallow(self, *, entity_id: str, entity_type: str) -> None:
        logger.info(
            "[LocalPolicyService] disallow(entity_id=%s) -> no-op (singlebox 无需黑名单)",
            entity_id,
        )

    def get_bots_ceiling(
        self,
        *,
        entity_id: str,
        default: int = 5,
        entity_type: str = "staff",
    ) -> int:
        """singlebox 不限 bot 数量上限。返回一个足够大的值。"""
        return 9999

    def set_bots_ceiling(
        self,
        *,
        entity_id: str,
        ceiling: int,
        entity_type: str = "staff",
    ) -> None:
        logger.info(
            "[LocalPolicyService] set_bots_ceiling(entity_type=%s, "
            "entity_id=%s, ceiling=%d) -> no-op",
            entity_type,
            entity_id,
            ceiling,
        )

    def clear_bots_ceiling(self, *, entity_id: str, entity_type: str = "staff") -> bool:
        logger.info(
            "[LocalPolicyService] clear_bots_ceiling(entity_type=%s, "
            "entity_id=%s) -> no-op",
            entity_type,
            entity_id,
        )
        return False

    def get_quota(self) -> dict:
        """Return the API-facing unlimited quota shape used by the router."""
        return {
            "quota": 9999,
            "totalLimit": 9999,
            "activeCount": 0,
            "effectiveQuota": 9999,
            "updateTime": "00:00",
        }
