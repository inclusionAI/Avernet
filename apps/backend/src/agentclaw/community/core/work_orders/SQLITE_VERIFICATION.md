# 工单 SQLite 流程验证（2026-10-10）

## 范围与结论

本轮只修改工单列表 Repository、工单标题转换、接口/协议说明及对应测试。
不修改前端、BCN、Skill 调用方、Bot 协作者专用业务流程和数据库结构。
Repository 原文件接近 1,000 行，因此将列表查询移入同目录 `listing.py`，
原入口保留并委托调用；不新增跨模块抽象。

- 文件 SQLite 流程：**38 passed**，17 条依赖弃用警告。
- 相关服务、Repository、接口、契约及架构回归：**646 passed**，18 条依赖弃用警告；包含上述 38 项，不应相加。
- 本地修改/新增 Python 文件 Ruff 检查通过，`git diff --check` 通过，均不超过 1,000 行。
- 通过测试不代表下列已知限制已经解决，也不代表生产数据库/BCN 联调已经通过。

## 测试方式

测试使用实际 WorkOrder Service、Repository 和每个用例独立的文件
`work-order-journey.sqlite`，不是内存字典或 mock Repository。
外部 HTTP、人员查询、Bot 下游同步等外部边界被替换；不连接真实 BCN。
新增列表测试还将服务返回值送入实际 HTTP 列表转换函数，断言返回标题、
条目类别、工单 ID、通知 ID、状态、已读状态及 `can_approve`。
这不是浏览器 E2E，也不等同于所有路由均通过真实 HTTP 访问 SQLite。

复现（从 `apps/backend` 执行）：

```bash
.venv/bin/python -m pytest \
  tests/community/core/repository/implementations/test_work_order_sqlite_journeys.py \
  tests/community/core/repository/implementations/test_work_order_sqlite_listing.py \
  -q -s
```

`STEP` 输出记录工单状态、审批人、通知、授权和协作者记录；`LIST` 输出记录
各角色/页签的数量、标题、已读状态和审批权限。SQLite 和日志不提交到仓库。

## 成功及拒绝路径

| 路径 | 执行顺序 | 关键断言 |
| --- | --- | --- |
| Skill AUTO | 申请校验 → PROCESSING → 授权/通过/通知事务 | 授权存在、SYSTEM、无人工审批人、申请人收到 NOTICE |
| Bot 好友 AUTO | PROCESSING → BCN 回调 mock → 通过/通知事务 | 回调时未提前 APPROVED、仅一次回调、接收人收到 NOTICE |
| Bot 协作者专用 AUTO（只验证） | 策略判断 → 协作者/通过/通知事务 → 下游同步 | 本地授权与结果同事务；正式模式字段问题见下文 |
| Skill/Bot 好友/Bot 协作者手工同意 | PENDING/APPROVAL → 人工同意 → 业务处理 → APPROVED/NOTICE | 审批前有权限、审批后无操作入口、业务写入符合结果 |
| 上述三类手工拒绝 | PENDING → 拒绝 → REJECTED/NOTICE | 不产生 Skill 授权或 Bot 协作者关系；好友回调携带拒绝决定 |
| 空间加入手工同意/拒绝 | PENDING → 人工决定 → 结果通知 | 终态退出待处理，未读结果也出现在已处理 |

对列表额外检查：

1. 申请人、结果接收人、无关用户的可见性；申请人可以按工单 ID 打开详情。
2. 已结束工单不因 NOTICE 未读进入待处理；已处理保留真实未读状态。
3. 未读转已读不改变审批状态，未读计数正确减少。
4. “我发起的 / ALL、APPROVAL”每张工单一条，不受多条通知或通知阅读时间影响。
5. “我发起的 / NOTICE”保留现有关联通知视图。
6. 独立通知继续按未读/已读进入待处理/已处理。
7. 分页总数、业务 ID 过滤、环境隔离。
8. AUTO 已结束后不允许再次审批；正式 AUTO 模式标题有自动审批标识。

## 本轮修复的问题

| 问题 | 原影响 | 处理 |
| --- | --- | --- |
| 终态结果通知未读仍进入待处理 | 已经结束的工单看起来仍需处理 | NOTICE 查询结合关联工单终态判断，并同步调整已处理查询 |
| 申请人收到 NOTICE 后，其申请被审批类筛选排除 | Skill AUTO 等申请在“我发起的 / 审批类”消失 | ALL/APPROVAL 直接按工单查询，不按通知类别判断 |
| 关联多条通知影响申请列表数量和排序 | 一张工单重复、分页数量错误、读通知后排序变化 | 发起视图一单一行，按工单时间与 ID 排序 |
| PROCESSING/FAILED 标题缺少状态映射 | 返回“新的系统通知”，缺少真实状态和自动审批标识 | 补齐“处理中/处理失败”标题，通知视图与工单视图共用转换 |

兼容性说明：发起视图 ALL/APPROVAL 返回 `WORK_ORDER_<id>`，没有通知 ID，
类别为 APPROVAL、`can_approve=false`；详情使用工单 ID。
终态通知可在已处理列表中保持未读。徽标仍表示“待审批 + 未读通知”，
不是待处理列表条数。无数据库迁移，没有把未读强制改成已读。

## 已验证仍存在的问题及未验证风险

### 1. 外部成功后本地保存失败：远端与本地不一致（高）

故障注入：HTTP 返回成功，再让结果通知 INSERT 失败。

- AUTO：本地保留 PROCESSING，无结果通知；远端回调已经执行一次。
- 手工：明确回滚时保留 PENDING，无结果通知；再次人工审批会再次发起回调，测试断言调用次数从 1 变成 2。
- 现有 `WorkOrderLocalFinalizeError`/500201 能显式报告不一致，但不会自动修复。
- 影响：工单悬挂，或重复触发远端决定；重复是否导致业务重复取决于 BCN 的幂等契约，SQLite 无法证明。
- 当前处置：保留诊断日志，需要核对远端状态；不要盲目重试。自动对账/持久重试不在本次范围。

### 2. 回调超时不能证明远端没有执行（高，远端结果未验证）

测试让 HTTP mock 抛出 TimeoutError，AUTO 本地写 FAILED 和失败通知；手工保持 PENDING。
真实网络里远端可能已执行但响应丢失，因此本地失败不能解释成好友关系一定未建立。
失败通知保留 external outcome may require reconciliation 提示；没有连接真实 BCN 验证最终结果。

### 3. Bot 提交后同步失败没有持久恢复（中，已注入验证）

数据库已提交授权、APPROVED 和 NOTICE 后，下游同步抛错。
工单正确保留成功，记录 `bot_post_commit_sync` 失败日志，但不会自动补偿。
影响：本地授权存在而下游未及时获知，可能暂时不生效；需要后续同步或人工排查。
本次不引入队列、Outbox 或恢复任务。

### 4. Bot 协作者专用 AUTO 的正式模式字段仍不一致（中，排除范围）

只做特征测试，不修改该业务路径：订单 APPROVED，业务数据/备注说明自动处理，
但正式模式为空并按 MANUAL 读取，因此 title 不显示“自动审批”。
影响：该专用路径的审批方式展示和审计不一致；不影响本次通用终态列表归类。
`test_excluded_bot_policy_auto_marker_gap_is_visible` 明确记录此现状，
不是把其“无自动标识”当成目标业务契约；将来修复该路径时应反转断言。

### 5. 进程中断、生产数据库并发与提交结果不确定（未覆盖）

本次未验证进程在 PROCESSING 后崩溃的恢复，未运行生产数据库并发测试，
也不能用 SQLite 明确回滚的测试推断真实连接中断时的 COMMIT 结果。
未新增请求级幂等、自动恢复或全局分布式事务；不能宣称无重复执行风险。

### 6. 通知落库不等于外部消息已送达（验证边界）

测试证明站内通知记录及列表/已读接口语义，不证明 IM/聊天消息送达。
没有运行浏览器 E2E、真实 BCN 联调或全仓完整 CI。


## 2026-10-13 补充验证：AUTO HTTP 与通知接收人

本次修改两条通用 events HTTP 入口，取消 AUTO 的一律拒绝；保留原有认证、
用户委托、申请人与 actor 一致性校验，以及通用入口对 Skill APPROVAL 的限制。
AUTO 是否适用由调用业务方决定，不新增模块白名单或额外身份验证。
AUTO 结果通知只使用规范化后的 `recipient_user_ids`，`approver_user_ids`
忽略并按空列表传入仓库；没有旧字段回退。MANUAL、NOTICE 契约保持不变。
Skill 内部 AUTO 调用、字段描述、Protocol、README 和网关发布描述同步更新。

验证结果：

- 相关单测、HTTP/领域/仓库契约、SQLite 场景及六项架构测试：**419 passed**，
  18 条已有依赖弃用警告；不是 Backend 全仓测试。
- 文件 SQLite HTTP、业务旅程与列表旅程：**48 passed**，包含在以上 419 项中。
  最终追加复跑上述 SQLite 48 项和 HTTP Router 29 项：**77 passed**。
- `git diff HEAD` 新增/修改的生产可执行行覆盖率 **100%（2/2）**。
  多行参数和描述属于已有语句行；按 AST 映射到受影响可执行语句，
  **8/8 覆盖**。删除的拒绝分支与非执行注释不算覆盖率分母。
- 修改过的生产 Python 文件整体行覆盖率：HTTP 两个 Router、DTO、Protocol
  100%；创建仓库 96.91%；Skill 申请服务 97.87%；WorkOrder Service 91.19%。
  以上均超过 90%，不代表 Backend 全仓达到该覆盖率。
- 本次修改/新增 Python 文件 Ruff 检查通过，`git diff --check` 通过；每个文件
  不超过 1,000 行。原超长 OpenAPI 契约测试按创建/查询职责拆分，没有删减用例。
- 通过 Backend `scripts/dump_openapi.py` 生成当前描述，只同步本次变更的请求
  component（全量发布描述还存在无关历史差异），通过 Gateway 发布兼容性门禁；
  新增用例校验三项字段描述与 DTO 一致。此门禁不检测接收人字段的业务语义迁移。

新增 `test_work_order_sqlite_http_auto.py` 使用真实 Router、WorkOrder Service、
Repository 和文件 SQLite，认证 dependency 在该集成测试中固定为已认证用户，
认证失败另由 HTTP 单测覆盖。外部 BCN HTTP 仍为 mock，不连接真实 BCN。
在两条 HTTP 路由上分别验证：

1. 回调时数据库仍为 PROCESSING，无人工审批记录、无提前结果通知。
2. 回调成功或异常后，数据库和 HTTP 返回终态一致，仅指定接收人得到结果通知。
3. 接收人去空格、去空白、去重复；非空 approvers 不成为接收人或人工审批人。
4. 空/全空白 recipients 即使有 approvers 也返回错误，并且零工单写入、零回调。
5. 伪造 applicant 返回拒绝，零工单写入、零回调。

独立复现 SQLite（从 `apps/backend` 执行）：

```bash
.venv/bin/python -m pytest \
  tests/community/core/repository/implementations/test_work_order_sqlite_http_auto.py \
  tests/community/core/repository/implementations/test_work_order_sqlite_journeys.py \
  tests/community/core/repository/implementations/test_work_order_sqlite_listing.py \
  -q -s
```

本轮没有发现这两个修改引入的新失败。上述历史风险仍然存在：远端成功后本地
持久化失败需对账；回调超时不能证明远端未执行；Bot 提交后下游同步失败无持久
恢复；Bot 专用 AUTO 的正式模式标记仍有历史差异。未新增幂等、Outbox、恢复队列，
未验证生产数据库并发、真实 BCN、浏览器或外部消息送达。测试通过不是绝对正确性证明。

兼容性：旧 AUTO 调用方必须把通知用户 ID 从 `approver_user_ids` 移到
`recipient_user_ids`，否则会在写入前失败。数据库结构无需迁移。
取消 AUTO 特有拒绝后，有效用户/委托用户可以直接选择 AUTO；业务方及部署入口
必须自行控制此能力的使用，不应把原有登录验证解释为自动审批业务资格校验。
