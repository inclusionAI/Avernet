"""Governance services — 核心业务层(domain logic + 编排,介于 web 与 repo 之间)。

**三类职责分层(2026-07-12 核心业务层化改造):**

| 类别 | 文件 | 职责 |
|---|---|---|
| **编排(Orchestration)** | scan_service | cron tick 入口:取领域模型→调内核→落库/发副作用,不含渲染/状态机守卫/ORM |
| **编排** | record_process_service | 离线批摄入入口:工单新建/快照刷新/批量质量校验编排 |
| **编排** | feedback_service | 用户反馈入口:resolve 状态流转编排 |
| **编排** | admin_service | 管理面入口:紧急制动/工单审批/批量白名单/应急删除/手动投递(本期未拆,文件豁免) |
| **内核(Kernel)** | lifecycle_service | 唯一推进工单状态机的驱动(open/scheduled/waiting_review/closed) |
| **内核** | notify_render_service | 唯一渲染出口:领域模型→可投递内容(通知 MD/TC 卡片/详情链接) |
| **内核(底层 builder)** | notify_builder_service | render service 内部依赖的纯 builder 函数,不被编排直接调 |
| **能力(Capability)** | whitelist_service | 单一资源(白名单)增删查封装,无跨实体编排 |
| **辅助** | delivery_runner | admin 投递链路 共用 helper(模块函数,非 service 类) |

**依赖边界(SDD spec A1-A3):**

- **上行(web)**:本层不直接被 router import;router 注入 `api/governance_service` 的 Protocol,
  DI 绑定 Protocol→具体服务(`di/modules/economy_governance_module`)。
- **下行(repo)**:经 `domain/protocols.py` 的 Repository Protocol 访问仓储(DI 绑定),
  service 不碰 ORM(`repositories/orm`)、不 import `repositories/` 直接。
- **横向(service↔service)**:经 `services/service_protocols.py` 的 Protocol 注入(非具体类 import);
  Protocol 定义在 core 自家(core 不得 import api),`api/governance_service` 仅 re-export 给 router。
  编排服务之间不互相 import,共享能力下沉内核/能力服务(render service / lifecycle)。
"""
