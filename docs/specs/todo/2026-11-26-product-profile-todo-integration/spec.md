# Todo 产品层接入 ProductProfileService Spec

> 状态：Ready for implementation
> 日期：2026-11-26
> Owner：Todo 产品层
> 前置 Spec：[`../2026-11-26-lab-todo-prototype-alignment/spec.md`](../2026-11-26-lab-todo-prototype-alignment/spec.md)
> 技术设计：[`design.md`](design.md)
> 实施索引：[`tasks.md`](tasks.md)

## 1. 目标

本 Spec 将已经完成第一批产品建模的 Todo，接入现有 `ProductProfileServiceProtocol`，形成以下四条完整产品链路：

1. Bot 小队 Revision 发布时创建并绑定不可变 ProductProfile；
2. Dima 工作项导入为 Todo 后创建并绑定 Product Collet；
3. Todo 详情将 Product Runtime 的 Rollout 和 Session Messages 投影为用户可理解的阶段进度；
4. Human 待处理事项通过站内通知触达，并在 Todo 内完成受控互动和结果回显。

最终用户不需要理解 Profile、Collet、Rollout、StageRollout 等协议概念，只需要在 Todo 产品中看到：任务来自哪里、由哪个小队负责、当前在哪个阶段、Bot 做了什么、卡在哪里、需要谁处理、Human 可以做什么、处理后是否继续执行。

## 2. 强制前提

### 2.1 协议层只使用，不实施

本次把以下已有协议视为外部依赖：

```text
ProductProfileServiceProtocol
├── create_product_profile()
├── create_product_collet()
├── query_collet_rollout()
├── interactive()
└── query_stage_rollout_details()
```

本次明确禁止：

- 修改 ProductProfileService 的五方法合同；
- 实现或补齐 ProductProfileService、L5 Methods、Rollout Worker、Progress ingress 的内部逻辑；
- 为 Todo 需求向协议 DTO 增加字段；
- 通过 Todo 内部 HTTP 自调用替代进程内 Protocol 注入；
- 为了让测试通过修改协议实现。

允许的做法是：Todo 产品层通过 DI 注入 `ProductProfileServiceProtocol`，并在测试中使用 Fake 或 Recording Double 验证调用、参数、幂等和错误处理。

禁止修改目录：

```text
apps/backend/src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/
apps/backend/src/agentclaw/community/core/task/context_collab/l5_methods/
```

### 2.2 保留 Todo 产品所有权

- BotTeam 是 Todo 面向用户的产品模型，ProductProfile 是执行协议模型；
- Todo 是用户可见工作实例，Collet 是 Product Runtime 实例；
- Todo Stage 是产品稳定快照，StageRollout 是某次运行尝试；
- Todo 状态、负责人、通知、互动记录和产品进度 VO 仍由 Todo 产品层拥有；
- Frontend 不直接消费或解释 ProductProfileService DTO。

## 3. 背景与当前问题

第一批 Todo 已经具备：

- Dima 单向导入模型；
- BotTeam 产品 facade；
- 不可变 Revision；
- Todo 固定 Stage 快照；
- 六状态协议；
- 负责人 participant binding；
- 历史 Context 运行链路。

但 BotTeam Revision 目前没有实际绑定 ProductProfile；BotTeam Todo 创建后仍会落到旧 Context dispatch；详情进度无法根据 Product Runtime 的真实运行信息展示；Human 互动仍依赖旧 `collab_context_id` 和 `user_interaction.posted`。因此，产品界面已有概念与新的运行协议之间还没有闭环。

本 Spec 不推翻第一批设计，而是在其上增加运行绑定和产品投影。

## 4. 术语

| 术语 | 产品含义 | 所有者 |
| --- | --- | --- |
| BotTeam | 用户配置的小队，V1 为线性 Bot 节点集合 | Todo 产品层 |
| Revision | BotTeam 一次已发布、不可变的版本 | Todo 产品层 |
| ProductProfile | 由 Revision 转换出的不可变运行定义 | 协议层，Todo 保存引用 |
| Todo | 一次具体工作实例 | Todo 产品层 |
| SourceBinding | Todo 对 Dima 等外部来源的单向快照绑定 | Todo 产品层 |
| Collet | 某个 ProductProfile 的一次运行实例；协议拼写固定为 Collet | 协议层，Todo 保存引用 |
| Todo Stage | Todo 创建时固定的用户可见阶段快照 | Todo 产品层 |
| Rollout | Collet 的一次整体运行尝试 | 协议层 |
| StageRollout | 某个 Stage 在某次 Rollout 中的运行尝试 | 协议层 |
| 子步骤 | Todo Backend 根据 Stage Session Messages 生成的用户可读摘要 | Todo 产品层 |
| Product Interaction | Human 对 Product Todo 的一次受控输入和平台同步响应 | Todo 产品层记录，协议层执行 |
| Legacy Todo | 没有 ProductProfile/Collet，继续使用旧 Context 链路的 Todo | Todo 产品层 |

## 5. 范围

### 5.1 Task 1：BotTeam Revision 发布为 ProductProfile

BotTeam 创建或编辑时，在发布新 Revision 前生成协议层需要的 composition 和 stages YAML，调用 `create_product_profile()`，并将返回的 `product_profile_id` 与不可变 Revision 绑定。

### 5.2 Task 2：Dima 导入、Todo 创建与 Collet 创建

Dima 工作项按单条独立结果导入 Todo。Todo、来源绑定、负责人绑定和 Stage 快照必须原子落库；随后调用 `create_product_collet()` 并以 CAS 方式绑定 `product_collet_id`。创建失败允许补偿，不允许重复导入或重新创建 Todo。

### 5.3 Task 3：Todo 产品进度投影

Todo 详情以固定 Stage 快照为骨架，查询当前 Collet Rollout 和必要的 Stage Messages，集中映射状态并生成子步骤 Summary。Frontend 只渲染稳定的 Todo Progress VO。

### 5.4 Task 4：站内通知与产品互动

当 Todo 需要 Human 处理时，通过 Todo 专属通知发布器写入现有站内通知基础设施。Human 在 Todo 最后一个互动框发送消息后，Backend 持久化 interaction，调用 `interactive()`，保存同步响应并刷新进度。发送期间禁止重复输入，允许提出停止请求。

## 6. 用户流程

### 6.1 配置和发布小队

```text
空间成员创建/编辑 BotTeam
→ 产品层校验名称、节点、Bot、顺序和验收条件
→ 生成不可变 Revision
→ 转换 ProductProfile 输入
→ 创建 ProductProfile
→ 原子发布 Revision 并绑定 product_profile_id
→ 后续创建 Todo 固定使用该 Revision 和 Profile
```

用户看到的是 BotTeam Revision 发布成功，不需要看到协议 YAML 或 Profile 内部结构。

### 6.2 导入 Dima 并启动 Product Todo

```text
用户选择最多 20 条 Dima 工作项
→ 为每条选择 BotTeam Revision 和负责人
→ 映射标题、描述、来源和参与者
→ 原子创建 Todo 聚合
→ 创建 Collet
→ CAS 绑定 product_collet_id
→ Todo 进入可执行状态
```

每条返回 `imported | duplicate | failed`。一条失败不得回滚其他成功条目。

### 6.3 查看进度

```text
用户打开 Todo 详情
→ 页面先展示固定阶段和静态信息
→ Backend 查询当前 Rollout
→ 只为当前或展开阶段读取详细 Messages
→ Backend 生成子步骤和阶段摘要
→ 页面展示状态、尝试、产物、错误和待处理动作
```

协议服务不可用时，页面仍展示 Todo 基本信息和 Stage 骨架，并明确显示“运行进度暂不可用”，不能伪装成“未开始”。

### 6.4 Human 处理和互动

```text
Bot/流程产生 Human 待处理事项
→ Todo 写入待处理事实
→ 发布站内通知
→ Human 点击通知进入 Todo 最后一个互动框
→ 发送消息并锁定输入框
→ Backend 保存 sending interaction
→ 调用 interactive()
→ 保存平台回复和动作类型
→ 刷新进度并解除锁定
```

同一时刻只能有一个未确认的 Product Interaction。发送后，只有收到明确结果或完成补偿处理，用户才可再次输入。

## 7. 验收标准

### AC-001：Revision 与 ProductProfile 一一固定绑定

每个新发布 BotTeam Revision 必须绑定且只绑定一个 `product_profile_id`。相同发布请求重试返回相同结果；已绑定不同 ID 时报告冲突，不覆盖。

历史 Revision 不回填 Profile，仍可查看，但不能误进入 Product Runtime。

### AC-002：BotTeam 产品模型与协议模型解耦

BotTeam V1 继续只支持线性顺序。ProductProfile stages 根据节点顺序生成 `depends_on`。现有 `approval_required` 合成 Human Stage 不得不经转换直接进入 ProductProfile；Human 负责人、通知和验收仍由 Todo 产品层管理。

### AC-003：Todo 聚合原子创建

一次 Product Todo 创建必须在一个本地数据库事务中落下：

```text
Todo
SourceBinding（如有）
participant bindings
Stage snapshots
```

任一部分失败时本地聚合均不得部分存在。

### AC-004：Dima 单向导入保持幂等

- 每批最多 20 条；
- 每条独立返回结果；
- 同一 tenant/env/space/source identity 只创建一个 Todo；
- 重复导入返回既有 Todo；
- 不回写 Dima；
- Todo 已创建但 Collet 暂时失败时，保留 Todo 并允许补偿，不删除重建。

### AC-005：Collet 绑定独立于旧 Context

Product Todo 使用新增的 `product_collet_id`，不得把整数 Collet ID 写入 `collab_context_id` 或 `ac_todo_execution`。运行模式依据 Revision/Profile/Collet 明确判断，不能依赖 ID 格式、创建时间或空字符串约定。

### AC-006：Progress 以 Todo Stage 为骨架

页面始终按 Todo 创建时的 Stage 快照展示名称、顺序、Bot 和验收条件；Runtime 信息只能补充状态、尝试、消息、产物、错误和时间，不得反向改变历史 Stage 定义。

### AC-007：当前 Rollout 选择正确

`query_collet_rollout()` 的结果可能携带 `revision`、`current_rollout_id`、`stopped` 元数据。产品层必须使用 `current_rollout_id` 选择当前 Rollout，不能依赖返回列表顺序。

### AC-008：子步骤由 Backend 生成

Frontend 不解析 provider 原始 Messages。Backend 对当前或用户展开 Stage 的 Messages 做排序防御、边界处理和摘要，返回稳定 VO。协议声明 `order="provider-defined"` 或 `complete=None/False` 时不得宣称消息完整有序。

### AC-009：站内通知可达且幂等

需要 Human 处理、执行中断/失败、Verify、完成等产品事件可生成站内通知。通知只发给当前负责人或明确 recipient。相同 delivery key 重试不重复增加未读通知；后续新事件可创建新通知。

### AC-010：产品互动本地可追溯

每次 Human 请求、发送状态、平台响应、动作类型、失败和停止请求均在 Todo 产品库中可查询。Product Todo 的互动不依赖旧 `collab_context_id` 事件历史。

### AC-011：互动并发受控

同一 Todo 不允许同时存在两个 `sending` 或 `delivery_unknown` interaction。互动调用使用 interaction ID 作为协议 `request_id`，并携带当前 Rollout `expected_revision`。相同 interaction 重试必须复用 request ID。

### AC-012：协议故障不制造产品事实

- HTTP/进程超时不等于明确失败，互动进入 `delivery_unknown`；
- 未收到明确 `SHUTDOWN` 不得把 Todo 标记为已取消；
- Progress 查询失败不得投影成未开始；
- 通知失败不得回滚已经成功的 Runtime 操作；
- Summary 失败不得阻塞基础进度，也不得编造子步骤。

### AC-013：Legacy 兼容

- 没有 ProductProfile 的历史 Revision 继续旧 Context 路径；
- 单 Bot Todo 本次继续旧 Context 路径；
- 已有 `collab_context_id` 的 Todo 保持现有详情和互动行为；
- 不回填历史数据，不批量创建 Profile 或 Collet。

### AC-014：权限和隔离

所有读写按 `avernet_tenant + env + space_id` 隔离。ActorContext 只从已认证 Human 构造，不信任请求体内 actor、tenant 或 env。所有空间成员可查看和互动，但通知 recipient 和 Human 处理权限仍按 Todo 负责人/明确绑定判断。

## 8. 产品运行模式

Todo Backend 必须将运行模式集中判定为以下之一：

| 模式 | 判定 | 行为 |
| --- | --- | --- |
| `product_collet` | Revision 有 `product_profile_id`，Todo 有或应创建 `product_collet_id` | 使用 ProductProfileService |
| `legacy_context` | 历史 Revision 无 Profile，或单 Bot/已有 Context Todo | 保持现有 Context 链路 |
| `product_runtime_pending` | Revision 有 Profile，但 Todo 暂无 Collet 且创建可补偿 | 保持 Todo，不回退旧 Context，等待补偿 |
| `invalid_binding` | 数据互相矛盾，例如无 Profile 却存在 Collet | fail closed，记录诊断，不猜测修复 |

## 9. 数据变更概览

### 9.1 Revision 绑定

```text
ac_todo_template_version.product_profile_id BIGINT NULL
```

只对新 Revision 写入，不回填历史数据。增加 tenant/env 下非空查询索引。

### 9.2 Todo 绑定

```text
ac_todo.product_collet_id BIGINT NULL
```

只对 Product Todo 写入。增加 tenant/env 下非空查询索引。

### 9.3 Product Interaction

若合并分支尚无等价存储，则新增：

```text
ac_todo_product_interaction
```

至少保存 interaction identity、Todo/Collet/Rollout、expected revision、Human 请求、同步响应、delivery state、停止请求、tenant/env 和审计时间。

实施 Agent 必须先检索是否已有等价表或模型；若存在则扩展复用，禁止创建同义表。

## 10. 权限与安全

- HTTP 请求中的 user ID 只能作为路由输入，服务层必须再次基于已认证身份鉴权；
- ProductProfileService 的 `ActorContext` 从认证上下文构造；
- 不允许由请求体覆盖 tenant、env、space 或 actor；
- Dima 导入只读取当前用户有权访问的工作项；
- Todo 的所有空间成员可查看和发表评论/互动；
- 负责人或明确 Human recipient 承担待处理动作；
- Session Messages 返回前必须经过产品层可见性和敏感字段过滤；
- 日志不得记录凭据、完整私密消息或运行 token。

## 11. 非目标

- 实现 ProductProfileService 或 L5 Methods；
- 修改协议 DTO 或新增协议 API；
- 实现 Product Runtime Worker、调度、Rollout 推进或 Progress ingress；
- BotTeam DAG、并行、分支、循环；
- Dima 双向同步；
- 单 Bot Todo 迁移到 Product Runtime；
- 历史 Revision/Profile/Collet 回填；
- Todo 创建后编辑；
- 钉钉通知；本任务先完成站内通知；
- 部署和 Verify 的底层执行实现；它们只作为可展示阶段或 Human 触发点；
- Frontend 直接渲染协议层 DTO。

## 12. 与旧 Spec 的关系

[`实验室待办第一批对齐 Spec`](../2026-11-26-lab-todo-prototype-alignment/spec.md) 仍然拥有以下合同：

- Dima 单向批量导入；
- BotTeam facade；
- Revision 不可变；
- Todo Stage 快照；
- 六状态；
- 负责人；
- 页面基础信息架构。

本 Spec 新增：

- ProductProfile 绑定；
- Collet 创建和绑定；
- Product Progress VO；
- Product Interaction 本地记录；
- Todo 站内通知。

本 Spec 替代旧设计中“`approval_required=true` 直接等同于协议层 Human Stage”的解释。现有 Template 兼容模型可以继续展开合成 Human Stage，但 ProductProfile 生成器必须过滤或明确映射，不允许把兼容存储结构未经解释地当作协议运行图。

旧文档保留为历史决策，不在本任务中修改。

## 13. 交付拆分

| Task | 交付 | 依赖 |
| --- | --- | --- |
| [`Task 1`](tasks/task-01-bot-team-product-profile.md) | Revision 创建并绑定 ProductProfile | 现有 BotTeam/Revision |
| [`Task 2`](tasks/task-02-dima-todo-product-collet.md) | Dima/Todo 原子创建并绑定 Collet | Task 1 |
| [`Task 3`](tasks/task-03-todo-product-progress.md) | Progress Backend VO 与 Frontend 展示 | Task 2 |
| [`Task 4`](tasks/task-04-human-notification-interaction.md) | 站内通知和 Product Interaction | Task 2；部分通知基础设施可并行 |

依赖图：

```text
Task 1 → Task 2 → Task 3
                    └→ Task 4

Task 4 的通知基础设施可与 Task 3 并行；
Task 4 的 Product Interaction 必须等 Task 2 提供 product_collet_id。
```

## 14. 当前验证状态

本目录当前仅定义需求和实施边界：

- 尚未修改业务代码；
- 尚未执行数据库迁移；
- 尚未运行 Backend/Frontend 测试；
- 协议层能力只依据现有合同设计，实施时如合同与运行表现不一致，Agent 必须停止并反馈，不得自行修改协议层。
