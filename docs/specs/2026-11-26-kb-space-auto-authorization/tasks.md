# Tasks: 知识库按空间自动授权 Bot

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> 本清单替代旧归属投影/继承授权方案，全部任务待实施。路径缩写与 plan 的 Key Files 一致；不预设新表或认证体系。

## Task 1: 核对并固定最小复用方案
- **Goal:** 查明现有存储、授权执行权限和可靠任务能力，固定最小补充。
- **Files:** `core/knowledge_management/ports.py`、现有 task queue repository、ECB KB ext/授权/活动/认证入口、宿主 database/DI；本目录 `plan.md`。
- **Done when:**
  - [ ] 记录公共、ECB 与宿主实际基线和文件；核对现有 ext 的局部更新、并发控制及保留能力。
  - [ ] 证明自动执行者具有增权与生命周期撤权的限定权限，Owner 离开后仍按真实授权执行；不足则记录现有能力最小补充，不冒充用户。
  - [ ] 核对同事务持久待办、任务清理后的恢复、false→false CAS 屏障及未知结果回执；明确每项实际承载位置与测试。
  - [ ] 任何必要持久化/接口扩展先更新方案并说明复用不足的证据，不预设新表、新认证或 Bot 归属接收 API。
- **Depends on:** —

## Task 2: 实现 KB ext 开关与汇总记录
- **Goal:** 将开关及控制版本存入既有 KB ext，复用 ECB 活动。
- **Files:** ECB KB metadata/ext 更新、授权校验、活动 service/store 及对应测试；公共 metadata adapter。
- **Done when:**
  - [ ] AC01：新旧库字段缺失为 OFF，只有创建者能改，局部更新不覆盖其他 ext。
  - [ ] AC02/05：一次开关动作一条汇总；保存成功与批次收敛分开，关闭不撤销已有 grant。
  - [ ] 记录批次和操作来源，重试不重复动态；按 plan 定义新增成功/已授权/跳过/失败/待确认，关闭新增数正常为 0。
- **Depends on:** Task 1

## Task 3: 补齐现有 grant mutation 的最小执行保证
- **Goal:** 自动授权仍落真实 grant，保持原鉴权和 CAS。
- **Files:** ECB Bot authorization service/store、原 mutation router/schema、活动入口及测试；`adapters/knowledge_platform/catalog.py`。
- **Done when:**
  - [ ] AC08/09：复用实际 grant 行、正 revision 与现有读路径，接收人及原生 READ 不被绕过。
  - [ ] 自动/人工/生命周期来源可信且可审计；自动单项写不产生虚假人工活动。
  - [ ] 按 Task 1 证据补齐同值拒绝屏障、操作回执/幂等必要缺口；CAS 冲突不能刷新版本后原样重试。
  - [ ] AC04/10：迟到授权、无原行的撤权屏障、未知结果归因与重复活动测试通过。
- **Depends on:** Task 1

## Task 4: 可靠记录 Bot 生命周期待办
- **Goal:** 所有权威写入口可靠保留增撤权工作及旧授权目标。
- **Files:** `core/repository/implementations/bot/{initial_layout.py,mutations.py,owner_handoff.py,recycle.py}`、Task 1 确认的既有持久载体、企业事务适配及测试。
- **Done when:**
  - [ ] AC03/06/10：新建、转入、移出、删除和回收均覆盖；停止/休眠不撤权。
  - [ ] 源变更与恢复标记可靠绑定，写后入队前崩溃仍可恢复；Bot 删除/队列清理不丢旧身份撤权。
  - [ ] 企业真实事务故障注入验证源行与待办同回滚；不能用普通 AUTOCOMMIT 或单次写后 HTTP 冒充。
  - [ ] 无 ECB Bot 归属投影、入驻历史同步或全量初始化任务。
- **Depends on:** Tasks 1, 3

## Task 5: 实现有界自动授权与失败恢复
- **Goal:** TC 通过既有队列执行真实 grant 写入并持续恢复未完成操作。
- **Files:** `core/knowledge_management/services/auto_authorization.py`（新增）、`ports.py`、现有任务 handler/生命周期/DI、相关测试。
- **Done when:**
  - [ ] AC02/03：开启后分页处理当前 Bot，后续新建/转入由生命周期接续；保存开关后排队前退出可补偿。
  - [ ] AC04/05/10：执行时复查权限、归属、开关、人工取消及代次，OFF 停新增但不阻断撤权清理。
  - [ ] 旧任务、旧确认和未知回执不会覆盖当前决定；接收端 CAS 与失效屏障有并发测试，active-only 去重不作为唯一保证。
  - [ ] 进度可恢复，成功数仅归因已确认新增并去重，失败和未知单列；批次完成后不计后续生命周期任务。
- **Depends on:** Tasks 2, 3, 4

## Task 6: 实现人工例外、迁回及 Owner 承接
- **Goal:** 保留业务规则，将变更落实到现有 owner-keyed grant。
- **Files:** `core/knowledge_management/services/{service.py,auto_authorization.py}`、`core/repository/implementations/bot/owner_handoff.py`、既有人工操作入口和测试。
- **Done when:**
  - [ ] AC04/07：人工取消跨重试/OFF→ON保留，真正迁出再迁回重置本次取消，来源不明 false 不批量恢复。
  - [ ] AC06/12：迁出/删除清所有旧空间实际授权；同空间转交迁移四类状态并撤销旧键，新 Owner 仍过原生 READ。
  - [ ] 迁回/OFF 不复活历史 true，旧撤权不能取消新授权；同名重建不复用旧任务。
  - [ ] 受控迁移部分成功可恢复，执行主体无权处理旧键时明确阻塞，不伪报完成。
- **Depends on:** Task 5

## Task 7: 接入开关和进度并回归原有读路径
- **Goal:** 提供最小管理扩展，保持实际 grant 查询与消费者兼容。
- **Files:** `adapters/http/knowledge_management/{router.py,schemas.py,errors.py}`、`adapters/knowledge_platform/{catalog.py,grant_state_reader.py}`、`api/knowledge_management_service.py`、知识管理/Bot 知识消费者与合同测试。
- **Done when:**
  - [ ] AC01/02/05/10：开关字段级读写、创建者权限及任务状态可观察，保存成功不伪装全部完成。
  - [ ] AC09：原授权列表/数量/批量状态/整数游标直接消费真实 grant，不引入 effective v2 或虚拟行。
  - [ ] AC08/09：Bot 可选目录、开启检查、配置、对话和服务阶段、实际内容访问复用原读路径；启用/发布行为不变。
- **Depends on:** Tasks 5, 6

## Task 8: 完成工作台和企业装配
- **Goal:** 界面展示实际授权与准确批次结果，企业执行身份接入既有认证。
- **Files:** 宿主 `internal/knowledge/` 的 AuthorizedBotsPanel、GrantBotDialog、domain、service/controller；enterprise knowledge identity/DI/database 和测试。
- **Done when:**
  - [ ] 创建者开关、逐 Bot 人工操作、部分完成/失败/重试反馈覆盖个人及团队空间。
  - [ ] 明确区分本次自动新增成功数与当前授权总数，关闭显示本次新增 0；不由浏览器遍历授权。
  - [ ] 现有服务认证和任务权限检查贯通，UI 无权隐藏不能替代后端拒绝；AC01/02/04/05/10/12 通过。
- **Depends on:** Task 7

## Task 9: 完善兼容、回滚及知识记录
- **Goal:** 保留真实授权和未收敛清理，移除旧投影迁移工作。
- **Files:** 知识管理 README、Backend 导航、宿主集成说明/gitlink、本目录 `verification.md`（实施时新增）。
- **Done when:**
  - [ ] 默认 OFF、旧 grant true/false 保留，无 Bot 投影初始化、区间迁移或全量改写。
  - [ ] 回滚停止新增权，但继续旧关系撤权和未知结果回查；不能清队列丢进度。
  - [ ] 实际公共/宿主版本与能力边界可追溯，未执行生产 DDL、数据变更或部署。
- **Depends on:** Task 8

## Task 10: Tests & Verification
- **Goal:** 逐项验证 spec AC01–AC12 和本轮执行权限、恢复、数量要求。
- **Files:** `tests/community/core/knowledge_management/test_auto_authorization.py`（新增）、现有 endpoints/contracts/bot_knowledge/architecture/DI 测试；ECB、企业驱动、UI 和组合验证；`verification.md`。
- **Done when:**
  - [ ] 创建者权限/任务执行权限、manual vs auto CAS、无 grant 迟到增权、Owner 旧键撤权、重启和任务清理故障注入通过。
  - [ ] AC11 完整链路核对页面、真实 grant 和新发起访问；AC12 四类状态承接独立覆盖。
  - [ ] 汇总不重复、不认领他人写入、不将未知计成功；关闭及部分失败数量口径正确。
  - [ ] 按实际变更跑必需合同/架构/HTTP及企业测试；所有 AC 有证据，未执行单列 UNVERIFIED，不以 HTTP200/入队成功替代验收。
- **Depends on:** Task 9

## Groups
- **Group A — 复用与实际授权合同:** Tasks 1, 2, 3
  - Theme: 开关存 KB ext，执行权限和真实 grant mutation 有明确保证。
- **Group B — TC 生命周期执行:** Tasks 4, 5, 6
  - Theme: 可靠执行、人工例外、迁移和转交可恢复且拒绝旧任务。
- **Group C — 接口与企业集成:** Tasks 7, 8, 9
  - Theme: 保持旧读路径，完成 UI、装配、兼容及回滚说明。
- **Group D — Verification:** Task 10
  - Theme: Final spec acceptance check.
