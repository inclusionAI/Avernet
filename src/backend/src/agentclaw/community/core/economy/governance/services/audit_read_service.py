"""[能力] Governance audit read service — worker-scoped audit history query.

只读薄 service:把"按 worker 查全部治理审计"翻译为对 ``ac_governance_audit``
的按 ``owner_id``/``bot_id`` 定位查询,委托给
:meth:`GovernanceAuditRepository.list_by_subject`,再经 ``AuditLogOrm.to_dict()``
序列化为前端可消费的 dict 列表。

为什么单独成 service(不挂在 whitelist/admin service):
  audit_repo 是通用审计存储,读能力不应绑死在某个业务 service;独立 service
  单一职责(SRP),且经 Protocol 注入 router(见 ``GovernanceAuditReadServiceProtocol``)。

治理领域 "worker" = ``owner_id:bot_id`` 复合标识(同 ``ac_governance_notify_log.
worker_id`` 定义);审计表本身无 ``worker_id`` 列,故运行时解析。

只读、无副作用、不写审计。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from injector import inject


if TYPE_CHECKING:
    from agentclaw.community.core.repository.implementations.governance.audit import GovernanceAuditRepository


class GovernanceAuditReadService:
    """Read-only audit history query scoped by worker (owner:bot)."""

    @inject
    def __init__(self, audit_repo: "GovernanceAuditRepository") -> None:
        self._audit_repo = audit_repo

    def list_audit_by_worker(
        self,
        *,
        worker_id: str | None = None,
        owner_id: str | None = None,
        bot_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """按 worker 维度查治理审计,返回 ``(审计条目 dict 列表, total)``。

        ``worker_id``(冒号分隔 ``owner:bot``)优先解析并覆盖独立的
        ``owner_id``/``bot_id``;``action`` 透传给 repo 做 ``action_taken`` 过滤。
        解析后 owner/bot/action 三者皆空抛 :class:`ValueError`(路由侧 400,
        防全表扫)。条目按 ``gmt_create`` 倒序(由 repo 保证)。

        Args:
            worker_id: 复合标识 ``"owner_id:bot_id"``,优先解析。
            owner_id: 独立按 owner 查(被 worker_id 覆盖)。
            bot_id: 独立按 bot 查(被 worker_id 覆盖)。
            action: 可选 ``action_taken`` 过滤(AuditAction 枚举值)。
            limit: 分页大小。
            offset: 分页偏移。

        Returns:
            ``(审计条目 dict 列表, 筛选条件下的总数)``。

        Raises:
            ValueError: ``worker_id`` 非 ``owner:bot`` 形态(缺冒号/段为空),
                或解析后 owner/bot/action 三者皆空。
        """
        if worker_id is not None:
            owner_id, bot_id = self._parse_worker_id(worker_id)
        rows, total = self._audit_repo.list_by_subject(
            owner_id=owner_id, bot_id=bot_id, action=action,
            limit=limit, offset=offset,
        )
        return [r.to_dict() for r in rows], total

    @staticmethod
    def _parse_worker_id(worker_id: str) -> tuple[str, str]:
        """解析 ``"owner_id:bot_id"`` 为 ``(owner_id, bot_id)``。

        严格要求单一冒号分隔两个非空、无内嵌空白/冒号的 token(owner_id /
        bot_id 在治理领域均不含冒号或空白)。

        Raises:
            ValueError: 缺冒号 / 多于一个冒号 / 任一段为空或含空白。
        """
        parts = worker_id.split(":")
        if len(parts) != 2:
            raise ValueError(
                f"invalid worker_id {worker_id!r}: expected 'owner_id:bot_id'"
            )
        owner, bot = parts[0].strip(), parts[1].strip()
        if not owner or not bot:
            raise ValueError(
                f"invalid worker_id {worker_id!r}: owner and bot must be non-empty"
            )
        if any(ch.isspace() for ch in owner + bot):
            raise ValueError(
                f"invalid worker_id {worker_id!r}: owner and bot must not contain whitespace"
            )
        return owner, bot
