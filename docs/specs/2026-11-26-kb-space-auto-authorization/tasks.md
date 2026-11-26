# Tasks: 知识库按空间自动授权 Bot

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 当前为待实施清单，创建开发分支和编写文档不计作功能完成。公共路径缩写与 plan 的 Key Files 一致；ECB 与工作台修改在各自目标分支的隔离 checkout 中进行。每组完成后先复核契约与验收，再继续下一组。

## Task 1: 固定跨模块合同和源代码基线
- **Goal:** 将主体、入驻、策略屏障、有效权限和人工 CAS 的语义变为可测试合同。
- **Files:** `kernel/knowledge_bot_membership.py`、`plugin_api/knowledge_bot_membership.py`、`core/knowledge_management/ports.py`、`api/knowledge_management_service.py`；ECB 对应 domain/schema；`contract.md`（本目录新增）。
- **Done when:**
  - [ ] 更新并记录 ECB / 宿主精确基线、读取相应规则，定位真实文件，建立独立开发分支。
  - [ ] 固定 v2 字段、分页、认证、request_id、错误与旧客户端切换规则，列出所有调用方。
  - [ ] AC04/07/09/12 的反例进入合同测试，不使用 owner_id 作为稳定主体。
- **Depends on:** —

## Task 2: 实现 Bot 当前事实与持久 outbox
- **Goal:** 建立不可重用实例、当前入驻、单调版本及空间有序事件。
- **Files:** `core/knowledge_bot_sync/{README.md,models.py,repository.py,capture.py}`、`core/repository/protocols/bot/knowledge_effects.py`、`apps/backend/sql/2026_11_26_knowledge_bot_sync.sql`、`tests/community/core/knowledge_bot_sync/test_capture.py`。
- **Done when:**
  - [ ] 当前事实、不可变 payload/hash、序列、确认与 tombstone 有真实数据库测试。
  - [ ] 重复事实不重建入驻；同名重建不复用主体；数据错误不能被当成缺失。
  - [ ] AC06/07/10/12 的身份与版本断言通过。
- **Depends on:** Task 1

## Task 3: 在创建与普通移动中原子捕获
- **Goal:** 创建、个人空间解析和空间移动与事实事件同事务提交。
- **Files:** `core/repository/implementations/bot/{initial_layout.py,mutations.py}`、`core/repository/implementations/spaces/space.py`、`core/knowledge_bot_sync/capture.py`、`tests/community/core/knowledge_bot_sync/test_capture.py`。
- **Done when:**
  - [ ] 两个 Bot 创建入口、普通移动及 legacy NULL 有效空间变化均覆盖；只归一化到原有效空间不重置入驻。
  - [ ] 注入 outbox 写失败时源变更回滚，捕获关闭时兼容默认公开环境。
  - [ ] AC03/07/10 覆盖个人/团队空间。
- **Depends on:** Task 2

## Task 4: 接入删除、回收和 Owner 转交
- **Goal:** 所有身份失效与转交路径使用同一捕获规则。
- **Files:** `core/repository/implementations/bot/{mutations.py,recycle.py,owner_handoff.py}`、`tests/community/core/knowledge_bot_sync/test_capture.py`。
- **Done when:**
  - [ ] 软删除和回收最终路径产生持久失效；停止/休眠不产生删除事件。
  - [ ] 同空间转交保留 residency，跨空间转交结束旧入驻并创建新入驻。
  - [ ] AC06/07/12 及转交失败回滚均有测试。
- **Depends on:** Task 3

## Task 5: 实现投递、补偿和存量初始化
- **Goal:** 复用队列恢复追平，不因重启或丢队列指针丢事实。
- **Files:** `core/knowledge_bot_sync/{delivery.py,tasks.py,bootstrap.py}`、`di/modules/knowledge_bot_sync_module.py`、既有 `configs/` 配置装配、`tests/community/core/knowledge_bot_sync/test_delivery.py`、`tests/community/contracts/test_knowledge_bot_membership.py`。
- **Done when:**
  - [ ] 有界扫描、精确 hash 确认、失败重试和 at-least-once 幂等通过。
  - [ ] 初始化进度可恢复，锁内读事实，完成屏障前拒绝自动策略生效。
  - [ ] 断网、apply 后 ack 前退出、旧确认及迁移/删除并发初始化覆盖 AC10。
  - [ ] 完整 DI 与消费者合同测试证明实际调用 sink，默认关闭不依赖企业服务。
- **Depends on:** Task 4

## Task 6: 实现提供方可信归属接收
- **Goal:** ECB 验证认证与源版本，本地保存主体、Owner 和入驻历史。
- **Files:** ECB `knowledge_tenant` domain/store、TC space 接收 router/schema、迁移文件及对应 store/router 测试；具体路径在 Task 1 固定。
- **Done when:**
  - [ ] 未认证/越权更新拒绝；重复事件幂等，旧 revision 不能覆盖新当前归属。
  - [ ] 跨空间乱序的退出记录只结束对应入驻，不删除新空间当前身份。
  - [ ] AC06/07/10/12 覆盖；未知主体拒绝访问。
- **Depends on:** Tasks 1, 5

## Task 7: 实现每库策略、区间资格与人工选择
- **Goal:** 默认 OFF、关闭保留及入驻内例外由一个提供方策略计算。
- **Files:** ECB `knowledge_tenant` Bot authorization domain/service/store、策略命令与活动存储、迁移文件及对应测试。
- **Done when:**
  - [ ] 新旧库默认 OFF；创建者后端权限校验与策略 CAS 生效。
  - [ ] 策略命令在同空间序列屏障上应用，重复请求不产生重复动态；失败可查询。
  - [ ] 区间交集表达自动资格，人工 false 优先，Owner 变化不重置选择。
  - [ ] 取消自动资格无物理 grant 时仍有一条活动；ON/OFF 各只有汇总活动。
  - [ ] AC01–05/07/12 测试通过，不批量建立自动 grant/viewer。
- **Depends on:** Task 6

## Task 8: 统一提供方读取和实际访问
- **Goal:** 有效资格、原生 READ 与可消费性在所有读取入口一致。
- **Files:** ECB Bot authorization/catalog service/store、实际内容及 MCP 权限门禁、v2 router/schema、对应授权/批量/分页测试。
- **Done when:**
  - [ ] 点查、批量过滤、列表/数量、目录共享统一有效策略，无旧 grant OR 兜底。
  - [ ] 自动资格无人工 ID/时间；有效版本和 choice revision 分开；v2 游标不依赖 grant 行。
  - [ ] 可信执行用户仍通过原生 READ；非 TC/人员调用、WRITE 与管理权限不变。
  - [ ] AC08/09/12 和迁出后旧 KB ID 直调拒绝有业务断言。
- **Depends on:** Task 7

## Task 9: 适配公共管理接口和策略命令
- **Goal:** BFF 透传提供方有效结果，并持久记录策略屏障命令。
- **Files:** `adapters/knowledge_platform/{catalog.py,grant_state_reader.py,bot_policy.py}`、`adapters/http/knowledge_management/{router.py,schemas.py,errors.py}`、`core/knowledge_management/services/service.py`、`core/knowledge_bot_sync/repository.py`、`tests/community/endpoints/test_knowledge_management.py`。
- **Done when:**
  - [ ] 策略读写/状态、v2 授权页和人工选择可贯通；自动资格不再被旧 ID/revision 校验拒绝。
  - [ ] 显式版本能力协商，旧合同有兼容测试；新版本失败不回落到旧授权放行。
  - [ ] 只补当页 Bot 展示，无逐项远程查 TC 或前端全量授权。
  - [ ] AC01/02/04/05/09/10 的响应与错误语义通过。
- **Depends on:** Tasks 5, 8

## Task 10: 接入 Bot 配置、对话和服务阶段
- **Goal:** 所有消费阶段都使用提供方有效权限，保持启用与发布不变。
- **Files:** `core/bot_knowledge/{service.py,stage_selection.py}`、`core/knowledge_management/services/conversation.py`、`tests/community/core/bot_knowledge/`、`tests/community/endpoints/test_bot_knowledge_router.py`、`tests/community/contracts/test_knowledge_catalog.py`。
- **Done when:**
  - [ ] 可选目录、enable 前检查、draft/verify/online 都验证当前有效权限。
  - [ ] 未知历史阶段不回退草稿；自动授权不写 enabled 或发布快照。
  - [ ] AC04/08/09/12 覆盖；无需改动的消费者用回归证据说明。
- **Depends on:** Task 9

## Task 11: 完成企业装配与真实事务验证
- **Goal:** 企业运行路径具备同事务捕获、可信身份与可恢复投递。
- **Files:** 宿主 enterprise database adapter、knowledge identity/DI、任务注册及对应驱动/装配测试；Avernet `core/knowledge_bot_sync/` 合同测试。
- **Done when:**
  - [ ] 权威写单元使用已有显式事务，不能在 AUTOCOMMIT 上宣称原子性。
  - [ ] 真实目标数据库适配注入第二写失败，源行/outbox 同回滚；验证锁和提交行为。
  - [ ] 同空间 Owner 转交仍以稳定主体查资格；不从不可信 Header 补身份。
  - [ ] AC10/12 的企业证据齐全；不可运行时标 UNVERIFIED 并禁止声称全链路完成。
- **Depends on:** Tasks 5, 8, 9

## Task 12: 实现工作台开关与同步反馈
- **Goal:** 展示每库策略、人工调整和真实同步状态。
- **Files:** 宿主 `internal/knowledge/components/{AuthorizedBotsPanel.tsx,GrantBotDialog.tsx}`、`domain.ts`、管理 service/controller、相关 hooks 与组件测试。
- **Done when:**
  - [ ] 仅创建者可操作开关；其他管理者保留逐 Bot 授权/取消。
  - [ ] PENDING/APPLIED/FAILED、冲突刷新、列表/数量/活动刷新可观察。
  - [ ] 不按物理授权 ID 控制取消，不遍历全部 Bot 模拟自动授权。
  - [ ] AC01/02/04/05/09 覆盖个人与团队空间。
- **Depends on:** Task 9

## Task 13: 完成兼容迁移、回滚和知识维护
- **Goal:** 数据准备和代码切换不造成历史权限复活。
- **Files:** 公共及 ECB 迁移文件、`core/knowledge_bot_sync/bootstrap.py`、新模块与既有知识管理/Bot 知识 README、Backend README、`verification.md`（本目录新增）；宿主集成说明与 gitlink。
- **Done when:**
  - [ ] 旧 true/false 唯一映射、歧义拒绝、并发初始化和中断恢复验证通过。
  - [ ] 默认关闭、灰度顺序和保留捕获/有效判断的回滚流程可执行；未执行生产变更。
  - [ ] 更新模块边界注册、协议测试映射、知识导航；宿主 gitlink 对应已验证公共版本。
  - [ ] 文档区分实现、集成、部署，所有源文件大小符合仓库规则。
- **Depends on:** Tasks 10, 11, 12

## Task 14: Tests & Verification
- **Goal:** 逐项验证 spec AC01–AC12，分别报告实际结果和外部缺口。
- **Files:** `tests/community/core/knowledge_bot_sync/`、既有知识管理/Bot 知识/contracts/architecture 测试、ECB 权限测试、宿主 UI/企业驱动和组合验收、`verification.md`。
- **Done when:**
  - [ ] 聚焦单测、合同、DI、HTTP fast gate 和必要仓库 CI 通过，执行结果可追溯。
  - [ ] 受控环境完成 AC11 全链，分别核对页面、提供方与新发起实际检索访问。
  - [ ] AC12 四类承接状态及新 Owner 原生 READ 独立验证。
  - [ ] 所有 AC 有证据或明确 UNVERIFIED；仅全部满足后才能勾选 spec 完成，不伪造端到端或企业验收。
- **Depends on:** Task 13

## Groups
- **Group A — 合同与持久身份:** Tasks 1, 2
  - Theme: 固定公开边界，并验证主体/入驻/事件模型。
- **Group B — 权威捕获与恢复:** Tasks 3, 4, 5
  - Theme: 创建、移动、删除、转交和初始化可靠输出事实。
- **Group C — 提供方有效权限:** Tasks 6, 7, 8
  - Theme: 可信投影、策略、例外和实际访问形成一致闭环。
- **Group D — 公共消费者:** Tasks 9, 10
  - Theme: 管理、配置与对话/服务阶段使用相同有效结果。
- **Group E — 企业集成与工作台:** Tasks 11, 12, 13
  - Theme: 真实事务、UI、兼容迁移与宿主集成可核验。
- **Group F — Verification:** Task 14
  - Theme: Final spec acceptance check.
