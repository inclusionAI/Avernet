# Tasks: TC 每库开关配置（含前后端）

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 2026-11-27 按最小配置需求修订，复用原开发分支与文档目录。所有实现任务均未开始；旧完整自动授权任务不属于本单。公共路径缩写见 plan，宿主前端路径相对其 `apps/frontend-nextgen/`。

## Task 1: 固定配置与元数据权限合同
- **Goal:** 确定配置结构、隔离键、真实创建者权限及最小 GET/PUT 合同。
- **Files:** `core/knowledge_management/{toggle_models.py,ports.py}`、同域 Service API/`api/knowledge_management_service.py`、`adapters/knowledge_platform/catalog.py`；对应 provider 合同测试。
- **Done when:**
  - [ ] AC01/06：隔离键覆盖可信 tenant/platform/env/space/opaque KB ID，64 字符键限制有明确处理；业务开关与记录 enable 区分。
  - [ ] 用现有 KB get 的 created_by 和 catalog-detail 的 can_manage/status 固定元数据组合合同；不使用 owner_staff_no/can_delete 冒充创建者。
  - [ ] AC03/04：真实身份映射、空间/KB 一致性、权限不足/不可操作/缺失字段明确拒绝，ECB 无新接口或代码任务。
  - [ ] 明确 0=缺行 revision、strict boolean、409 和存储/依赖错误，不增加授权执行字段或状态。
- **Depends on:** —

## Task 2: 实现配置持久化与通用写保护
- **Goal:** 复用 common-config 存储，保证并发和专用入口约束。
- **Files:** `core/repository/implementations/config/knowledge_toggle.py`、同域 repository Protocol、`core/common_config/service.py`、低层配置保留常量；`tests/community/repository/config/test_knowledge_toggle.py`（新增）、既有 common-config 测试。
- **Done when:**
  - [ ] AC01/02：缺失只读返回 OFF；保存 ON/OFF 正确回读，跨 scope/长 ID 隔离，错误不伪报缺失。
  - [ ] 真实数据库测试覆盖并发首次插入和已有值 CAS，冲突返回 409，其他写失败原样报告；不采用无条件 upsert。
  - [ ] AC03：通用 create/upsert/update/delete/enable/disable 的按 ID 和按键路径均拒绝保留 business_code，其他业务不受影响。
  - [ ] 不新增表、不改通用 enable 语义、不回填旧库；异常 disable/损坏 JSON/identity 不匹配明确报错。
- **Depends on:** Task 1

## Task 3: 接通 TC 专用配置服务与接口
- **Goal:** 提供只存配置的 GET/PUT，并验证每次写入权限。
- **Files:** `core/knowledge_management/services/toggle.py`、`api/knowledge_management_service.py`、`adapters/http/knowledge_management/{router.py,schemas.py,errors.py}`、`di/modules/knowledge_management_module.py`；`tests/community/core/knowledge_management/test_toggle.py`（新增）、接口/DI 测试。
- **Done when:**
  - [ ] AC02/03/04：有权创建者保存成功；非创建者管理者、失权创建者、无权用户和不可操作库直接调用均拒绝。
  - [ ] GET 对已有读取权限用户返回实际值和 can_update；PUT 不信任客户端的创建者/权限/环境信息。
  - [ ] AC06/07：只允许既有元数据读取及 TC 配置写，grant/撤权、队列、成员枚举、同步激活、enabled/MCP/发布调用均未发生。
  - [ ] 完整装配、严格 schema、错误映射及 CAS 冲突测试通过；缺配置与故障区分。
- **Depends on:** Tasks 1, 2

## Task 4: 实现工作台开关配置区
- **Goal:** 在“已授权 Bot”页签形成可操作、可回显的最小前端功能。
- **Files:** 宿主 `src/internal/knowledge/components/{AuthorizedBotsPanel.tsx,BotAutoAuthorizationToggle.tsx}`、`domain.ts`、`services/knowledgeService.ts`、`api/managementController.ts`；`test/internal/knowledge/botAutoAuthorizationToggle.test.tsx`（新增）。
- **Done when:**
  - [ ] AC05：采用现有 UI 组件/语义 token，显示“自动授权当前空间下所有 Bot”及“本期仅保存配置，暂不执行自动授权”。
  - [ ] 使用 can_update 控制开关，不将 canManage 当创建者；个人/团队空间均覆盖。
  - [ ] AC02：刷新回显，加载/读取失败不伪装 OFF，保存请求中防重复提交，失败保留最后确认值，409/结果未知重新读取。
  - [ ] 切换 scope 丢弃旧响应；配置错误不阻断人工列表，无授权计数刷新、Bot 循环、轮询或任务进度。
  - [ ] 在实际组装树完成组件和类型检查；宿主闭源代码不写入公共仓库。
- **Depends on:** Task 3

## Task 5: 补充集成与知识记录
- **Goal:** 固定宿主适配和上线边界，避免配置被误当成已执行授权。
- **Files:** 知识管理/common-config README、必要 Backend 导航、宿主 knowledge service/DI 集成及 gitlink；本目录 `verification.md`（实施后新增）。
- **Done when:**
  - [ ] 先 TC 接口后 UI 的发布依赖明确，无 ECB 发布、配置回填或生产 DDL。
  - [ ] 保留实际 grant、人工操作、启用与发布规则；回滚隐藏 UI 但保留配置和通用写保护。
  - [ ] 记录实际公共/宿主版本、元数据合同和验证入口；不把文档状态写成上线状态。
- **Depends on:** Task 4

## Task 6: Tests & Verification
- **Goal:** 对照 spec AC01–AC08 完成配置闭环及零授权副作用验证。
- **Files:** 新配置服务/repository/provider/HTTP/DI 测试、既有 common-config 和知识管理/Bot 知识回归、宿主 UI 测试、本目录 `verification.md`。
- **Done when:**
  - [ ] 聚焦测试及必需架构/HTTP fast gate 通过；覆盖长 opaque ID、损坏配置、存储异常和并发冲突。
  - [ ] 前后端验证默认 OFF、保存/刷新、权限和生命周期拒绝、页面错误与仅配置提示。
  - [ ] AC07：核对切换前后实际 grant、人工取消、人员 ACL、enabled、MCP、发布选择和新发起访问无变化；无任务/枚举/监听。
  - [ ] AC08：既有人工授权/取消可用，不产生 ECB 开关活动或数量接口。
  - [ ] 每项 AC 有可追溯结果；未运行的企业驱动/端到端项目标 UNVERIFIED，不用 mock 或 HTTP 200 代替组合验收。
- **Depends on:** Task 5

## Groups
- **Group A — 配置与权限基础:** Tasks 1, 2
  - Theme: 明确元数据合同，并实现隔离、CAS 和通用入口保护。
- **Group B — 前后端配置闭环:** Tasks 3, 4
  - Theme: 专用接口和页面开关仅保存配置，不执行授权。
- **Group C — 集成与验证:** Tasks 5, 6
  - Theme: 完成知识维护及 AC01–AC08 的配置/零副作用验收。
