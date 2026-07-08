"""LocalPolicyService — singlebox / 本机开发模式的 PolicyService 实现。

singlebox 是单用户本机环境,没有 "白名单 + 配额 + 抢名额" 的业务需求:
- 只有当前登录人一个 user,没有多 user 抢容器配额一说;
- backend SQLite 每次 ``./scripts/singlebox.sh start backend`` 都会被 ``rm`` 重建
  (见 backend.sh:63),不可能在里面长期维护 ``ac_access_control_policy`` /
  ``ac_common_config`` 的种子数据;
- 真 prod 上这些表由运维种,有真实数据,``PolicyService`` (走 DB 查询的实现)
  是对的。

为何不在 ``PolicyService.check`` 里 ``if env == 'singlebox': return True``
旁路: 那是 ``is_local_mode()`` 散落分支,违反 arch Rule 14 (config-driven
wiring 在 DI 层一次决定,下游不读 env)。RuntimeMode 体系本就是为此而设,
按 ``RuntimeMode.LOCAL`` 选 ``LocalPolicyService``、``RuntimeMode.PROD``
选 ``PolicyService`` 才是干净姿势——同 ``SecretResolver`` /
``PassportPlugin`` 等 Local/Prod 双实现样板。
"""
from __future__ import annotations

from agentclaw.community.log import get_logger

logger = get_logger()


class LocalPolicyService:
    """singlebox 全开放: ``check`` 始终 True; 写方法 no-op; ``get_quota`` 返空 dict。

    Structural 实现 ``api.policy_service.PolicyServiceProtocol`` —— **故意不显式继承**,
    跟 ``core.access.services.policy_service.PolicyService`` (真实现) 同款 duck-typing
    姿势。原因: arch test ``test_plugins_layer_does_not_import_api`` 禁止 plugins/
    import api/;显式继承会引入 ``from agentclaw.community.api.policy_service import
    PolicyServiceProtocol`` 触发 violation。Structural 实现满足 Protocol runtime check
    (``isinstance(x, PolicyServiceProtocol)`` 仍 True),DI 层在 ``TestingAccessModule``
    用 ``-> PolicyServiceProtocol`` 标注返回类型让 type-checker 验证契约,plugins/ 这层
    保持对 api/ 零依赖。

    不继承 ``MockSeam``: PolicyServiceProtocol 不是 ``Plugin``,无 impl_registry
    可识别。直接 ``binder.bind`` 即可 (见 ``TestingAccessModule``)。
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

    def get_bots_ceiling(self, *, entity_id: str, default: int = 5) -> int:
        """singlebox 不限 bot 数量上限。返回一个足够大的值。"""
        return 9999

    def set_bots_ceiling(self, *, entity_id: str, ceiling: int) -> None:
        logger.info(
            "[LocalPolicyService] set_bots_ceiling(entity_id=%s, ceiling=%d) -> no-op",
            entity_id, ceiling,
        )

    def get_quota(self) -> dict:
        """返回一个 "无限配额" 的占位 dict,前端如果显示配额时不会出错。"""
        return {
            "daily_container_quota": 9999,
            "total_container_limit": 9999,
            "daily_container_update_time": "00:00",
        }
