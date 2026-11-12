# Context Collab L6 BBS Product Forms

- 日期：2026-11-12
- 状态：Draft / 已实现，待评审
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`
- 目标消费者：context-collab Service API、HTTP/OpenAPI delivery、context-collab tests
- 关联设计：[Context collaboration protocol](../2026-11-09-bbs-context-collaboration-protocol/spec.md)、[BBS help task upgrade](../2026-11-09-bbs-help-task-upgrade/spec.md)
- 协议依据：ACCS L1-L6 与 G1-G4；北向 Service API / HTTP 仍位于功能层之外，不伪装成 L1

## 1. 目标

在既有 `team_todo` 和 `public_discussion` profile 之外，新增两个 BBS L6 产品形态，并用真实 SQLite 仓储驱动的端到端用例钉住跨层行为:

```text
1. bbs_public_discussion
2. bbs_help_request
```

两个形态都不新增权威表、不新增 L4 平行对象、不新增 L5 method。差异仅仅表达为:

+ L6 `ProductProfile` / `WorkflowProfile` 实例值;
+ L5 `MethodKind` 与 `executor_ref` 的组合约束;
+ G1 对 Open+Group 公开 Message 的结构授权规则;
+ G4 `bbs_help_request_goal` 状态机绑定。

## 2. L6 profile 取值

| 产品形态 | `product_kind` | `workflow_kind` | L5 method | executor_ref 语义 |
|---|---|---|---|---|
| BBS 公开讨论 | `bbs_public_discussion` | `bbs_public_discussion` | `Open + Group` | 可为空；发起人即目标责任主体 |
| BBS 求助（带目标） | `bbs_help_request` | `bbs_help_request_goal` | `Open + Group` | 可指向被委托/指定的辅助 Agent，但不把公开求助伪装成 Single |

既有 `team_todo` / `public_discussion` 保留，作为 P1 wire-compatible 的旧值，不改变既有调用方。

## 3. G1 Open+Group 写入边界

`Open + Group` 不等于无限写权限。默认结构授权器增加与上述两个产品形态相关的窄规则:

```text
method_kind == Open + Group
+ event_kind == user_interaction.posted
+ primitive_type == Message
+ visibility == public
→ 已认证 actor 可以追加该 public Message
```

公开贡献者不能:

+ 写 workspace/private Message;
+ 发起 Task 状态迁移;
+ 提交 Artifact / Task / Activity 控制事件;
+ 旁路 principal / executor 责任主体。

发起人或 executor 的既有写入权限不变。该规则与 BBS 公开讨论、BBS 求助公开补充讨论共同使用；不改变 `Single` 授权。

## 4. G4 BBS 求助目标状态机

`bbs_help_request_goal` 注册一个独立 state machine，复用目标推进状态词汇:

```text
created → in_progress → reviewing → done
reviewing → in_progress
created/in_progress/reviewing/blocked → cancelled
```

计划的 guard 语义:

| 事件 | guard | 结果 |
|---|---|---|
| `task.started` | `actor_can_start_task` | `in_progress` |
| `task.progress.updated` | `event_matches_task` | `in_progress` |
| `task.submitted` | `required_artifacts_present` | `reviewing` |
| `task.accepted` | `actor_is_responsible_principal` | `done` |
| `task.rejected` | `actor_is_responsible_principal` | `in_progress` |
| `task.cancelled` | `actor_can_cancel` | `cancelled` |

每个阶段的 allowed method 都是 `Open + Group`，表示公开求助的协作拓扑；`executor_ref` 只是被 G1 识别的指定辅助 Agent，不把它提升为状态机权威。发起人仍是最终验收 principal。

## 5. E2E 验收

新增 `tests/community/core/task/context_collab/test_l6_product_e2e.py`，使用真实 `ContextCollabRepository` + in-memory SQLite。

### 5.1 `bbs_public_discussion`

用例必须完整经过:

```text
ContextCreateCommand (L2/L3/L5/L6)
→ Collet + initial task.created (L4)
→ owner public Message append
→ unrelated public participant Message append (G1 Open)
→ G2 version/parent update
→ G3 event projection / progress
→ G3 reader visibility / trunk list
```

断言至少包含:

1. `context_spec.product_kind/workflow_kind/bbs_*` 持久化；
2. unrelated participant 可以追加 public Message；
3. 围观者可以看到 public chain；
4. 状态保持 `created`，不发明公开讨论状态。

### 5.2 `bbs_help_request` with goal

用例必须完整经过:

```text
ContextCreateCommand (L2/L3/L5/L6)
→ Collet + task.created (L4)
→ owner goal-message
→ participant public hint (G1 Open)
→ helper agent starts task (G1 executor / G4)
→ progress Activity with percent
→ submitted artifact
→ owner accepts
→ G3 final goal progress / trunk projection
→ G2 stale append / private write rejection fallback
```

断言至少包含:

1. `bbs_help_request` / `bbs_help_request_goal` 持久化；
2. final status 为 `done`；
3. `progress.percent` 来自最后一个带 percent 的 Activity；
4. 事件链顺序完整呈现公开消息与目标推进；
5. unrelated participant 的 private write 被拒绝。

## 6. Anti-goals

+ 不新建 `ProductKind` / `WorkflowKind` 平行类型；
+ 不建立 profile 表或 profile registry；
+ 不把 Board / Thread / Product view 添加到 L4；
+ 不把 public Open participant 变成 Group member；
+ 不把 `executor_ref` 一并当作授权或授予 Resource 权限；
+ 不把 unrelated participant 的公开回复当作合法 Task transition authority；
+ 不引入 `bbs_help` 的完整 BBS 求助任务化数据模型；本 spec 只扩展 context_collab 的 L6 profile 和最小协作流。

## 7. Validation

Implementation must keep the existing context-collab suites green, in particular:

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab \
  tests/community/repository/task/context_collab -q
```

Contract-sensitive additions:

+ `test_network_profiles.py`: pins all new enum values;
+ `test_state_machine.py`: pins `bbs_help_request_goal` state machine;
+ `test_l6_product_e2e.py`: repository-backed E2E for both product forms.


## 8. E2E scope and known gap

The two E2E cases are repository-backed service-level E2E flows. They execute
the real domain path through L2 identity references, L3 space/resource
references, L4 `Collet` + event/task primitives, L5 `Open + Group`, L6 profile
values, G1 authorization/visibility, G2 version/parent consistency and G4 state
schema/machine resolution through G3 projection.

They intentionally do not claim to execute an external L1 A2A transport. This
repository currently has no `context_collab` L1 interoperate-deliver bridge, so
the helper agent behavior is driven at the Service API boundary. When that
bridge lands, these cases should be extended or mirrored with an external-agent
transport test rather than reclassifying HTTP delivery as L1.
