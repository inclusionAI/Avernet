"""Governance services — 核心业务层(domain logic + 编排,介于 web 与 repo 之间)。

**三类职责分层:**

| 类别 | 文件 | 职责 |
|---|---|---|
| **编排(Orchestration)** | scan_service | cron tick 入口:取领域模型→调内核→落库/发副作用,不含渲染/状态机守卫/ORM |
| **编排** | record_process_service | 离线批摄入入口:工单新建/快照刷新/批量质量校验编排 |
| **编排** | feedback_service | 用户反馈入口:resolve 状态流转编排 |
| **编排** | admin_service | 管理面入口:紧急制动/批量白名单/应急关闭/应急删除/手动投递(delivery 链路为内部 _run_delivery) |
| **编排** | workflow_service | 审批面入口:工单审批列表/详情/动作(从 admin 按路由边界拆出,对应 workflow_router) |
| **内核(Kernel)** | lifecycle_service | 唯一推进工单状态机的驱动(open/scheduled/waiting_review/closed) |
| **内核** | notify_lifecycle_service | 唯一推进通知发送状态机的驱动(pending→sending→sent/failed,正常投递路径) |
| **内核** | notify_render_service | 唯一渲染出口:领域模型→可投递内容(通知 MD/TC 卡片/详情链接);底层 builder 纯函数内聚于本文件 |
| **能力(Capability)** | whitelist_service | 单一资源(白名单)增删查封装,无跨实体编排 |

**说明:**
- `notify_builder_service` 已并入 `notify_render_service`(渲染出口唯一,无旁路)。
- `delivery_runner` 已并回 `admin_service._run_delivery`(投递链路是 admin 私有实现,不独立成文件)。
- admin/workflow 按对外路由边界分(admin_router→admin_service、workflow_router→workflow_service)。

**依赖边界:**

- **上行(web)**:本层不直接被 router import;router 注入 `api/governance_service` 的 Protocol,
  DI 绑定 Protocol→具体服务(`di/modules/economy_governance_module`)。
- **下行(repo)**:经 `domain/protocols.py` 的 Repository Protocol 访问仓储(DI 绑定),
  service 不碰 ORM(`repositories/orm`)、不 import `repositories/` 直接。
- **横向(service↔service)**:经 `services/service_protocols.py` 的 Protocol 注入(非具体类 import);
  Protocol 定义在 core 自家(core 不得 import api),`api/governance_service` 仅 re-export 给 router。
  编排服务之间不互相 import,共享能力下沉内核/能力服务(render service / lifecycle / notify_lifecycle)。
"""