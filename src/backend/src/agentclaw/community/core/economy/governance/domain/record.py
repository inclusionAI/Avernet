"""上层输入的 governance 治理记录领域模型 — GovernanceRecord。

注意:**这是上层(ODPS 离线管道→HTTP 接口)输入的一条治理判决数据的领域建模,
非 DB 层 record/ORM 行**。它由 adapter 层 ``GovernanceRecordInput``(Pydantic)校验后
经 ``to_record()`` 转入,作为 service ``process_record`` 的输入载体;本身不入库,
经由 process_record 落到 GovernanceTicket / GovernanceNotification 等持久化实体。

与 GovernanceTicket/GovernanceNotification 同级(domain 实体),只是它是"输入数据载体",
身份 + 数据字段平铺,不嵌套 Frozen/MutableSnapshot(二者分属通知/工单实体,语义不符)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GovernanceRecord:
    """上层输入的一条 governance 治理记录(非 DB record)。

    代表 ODPS 离线管道推来的一条治理判决数据:某 worker 在某 dt_version 下的
    命中维度/节省率/判决等。经 adapter ``GovernanceRecordInput.to_record()`` 构造,
    传入 service ``process_record`` 驱动工单创建/刷新;本对象不持久化。

    身份 + 数据字段平铺(不嵌套 Frozen/MutableSnapshot;二者分属通知/工单实体,语义不符,
    record 是独立输入载体)。

    不变量:
      - 身份/路由字段(owner_id/bot_id/governance_decision/dt_version)非空
      - 数据字段可选,缺则 None,由下游 refresh_snapshot/add_ticket 接受 None
    """

    # 身份/路由(必填,非空)
    owner_id: str
    bot_id: str
    governance_decision: str
    dt_version: str
    # 身份补充(可选)
    worker_id: str | None = None        # 缺则由 owner_id:bot_id 合成
    bot_name: str | None = None
    owner_name: str | None = None       # 负责人显示名(展示用,可空)
    # 数据字段(可选,缺则默认,传给 refresh_snapshot/add_ticket)
    hit_dimensions: str | None = None
    hit_dimensions_count: int | None = None
    governance_max_priority: str | None = None
    expected_token_saving: int | None = None
    saving_ratio: float | None = None
    token_baseline: int | None = None  # Token 消耗基线(展示用,refresh 走 guard)
    task_summary: str | None = None
    notification_structured: str | None = None
    analysis_status: str | None = None

    @property
    def effective_worker_key(self) -> str:
        """worker_key:worker_id 有且含 ':' 则用之,否则 owner_id:bot_id 合成。

        与 process_record 原 Step 1 合成逻辑等价(§5.4):生产者给的 worker_id 优先,
        避免重建错配;缺则从 owner_id:bot_id 重建。
        """
        if self.worker_id and ":" in self.worker_id:
            return self.worker_id
        return f"{self.owner_id}:{self.bot_id}"