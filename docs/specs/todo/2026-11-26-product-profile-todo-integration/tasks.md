# Todo 产品层接入 ProductProfileService 实施索引

> Spec：[`spec.md`](spec.md)
> Design：[`design.md`](design.md)
> 日期：2026-11-26

## 1. 给执行 Agent 的总指令

### 1.1 开始前必读

按顺序读取：

1. 仓库根 `AGENTS.md`；
2. `CONTEXT-MAP.md`；
3. `docs/agents/development.md`；
4. `docs/arch/arch.rules.md`；
5. `docs/arch/ci.enforce.md`；
6. `docs/arch/context-boundary-format.md`；
7. `docs/arch/protocol-contract-tests.md`；
8. `apps/backend/README.md`；
9. `apps/backend/src/agentclaw/community/core/task/todos/README.md`；
10. Frontend 任务涉及目录内最近的 `AGENTS.md`/README；
11. 本目录的 [`spec.md`](spec.md) 和 [`design.md`](design.md)；
12. 当前任务文件。

如果准备 commit/PR/push，再额外读取：

```text
docs/agents/pull-requests.md
docs/agents/issue-tracker.md
docs/agents/validation.md
```

### 1.2 工作区保护

- 开始时运行 `git status --short --branch` 并保存输出；
- 不 `reset --hard`、不清理、不卡掉、覆盖或回滚他人修改；
- 不切换或修改 `dev`；
- 只修改当前任务明确范围；
- 发现工作区已有相同文件修改时先阅读、增量集成，不能覆盖；
- 未经用户明确要求，不 commit、不 push；
- 任务结束时分别报告：修改、测试、失败、未验证项。

### 1.3 协议层禁止修改

以下目录只读：

```text
apps/backend/src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/
apps/backend/src/agentclaw/community/core/task/context_collab/l5_methods/
```

协议实现缺失或不符合预期时停止并反馈。测试使用 Fake/Recording Double，不得通过修改协议实现完成 Todo 任务。

### 1.4 开发方式

每个任务按 TDD：

```text
读现有合同
→ 写失败测试
→ 最小实现
→ focused tests
→ 重构
→ architecture/protocol tests
→ 完整 CI
→ git diff --check
```

一次只执行一个纵向任务。不要提前实现后续任务，也不要顺手重构无关 Legacy Context 代码。

### 1.5 完成报告模板

```text
任务：Task N
结论：完成 / 部分完成 / 阻塞
修改文件：...
数据库变更：...
协议调用：...
兼容性：...
执行测试：命令 + 结果
未执行测试：原因
剩余风险：...
未提交/未推送（如适用）
```

## 2. 四个任务

### Task 1：BotTeam Revision 发布为 ProductProfile

文档：[`tasks/task-01-bot-team-product-profile.md`](tasks/task-01-bot-team-product-profile.md)

交付：

- Revision `product_profile_id`；
- Profile 转换器；
- `create_product_profile()` 调用；
- 确定 request ID；
- 原子发布和 CAS；
- BotTeam 创建/编辑端到端测试。

依赖：现有 BotTeam/Template/Revision。

### Task 2：Dima 导入、创建 Todo 并创建 Collet

文档：[`tasks/task-02-dima-todo-product-collet.md`](tasks/task-02-dima-todo-product-collet.md)

交付：

- Todo `product_collet_id`；
- Todo/Source/Participant/Stage 原子创建；
- `ensure_product_collet()`；
- `create_product_collet()` 调用；
- Scheduler Product/Legacy 分流；
- Dima 批量逐条结果和补偿。

依赖：Task 1。

### Task 3：Todo 详情和产品进度投影

文档：[`tasks/task-03-todo-product-progress.md`](tasks/task-03-todo-product-progress.md)

交付：

- Progress 产品 VO；
- Rollout/current revision 选择；
- Stage 状态映射；
- Session Messages 子步骤 Summary；
- Artifact 预览/下载衔接；
- Frontend 阶段、尝试和降级态。

依赖：Task 2。

### Task 4：Human 站内通知与产品互动

文档：[`tasks/task-04-human-notification-interaction.md`](tasks/task-04-human-notification-interaction.md)

交付：

- Todo 通知 Publisher/Adapter；
- 通知幂等和 deep link；
- Product Interaction 本地存储；
- `interactive()` 调用、single-flight 和补偿；
- 最后一个互动框、发送锁定、停止请求；
- Legacy/Product 分流。

依赖：Product Interaction 依赖 Task 2；通知基础设施可与 Task 3 并行。

## 3. 依赖和并行关系

```text
Task 1 ──→ Task 2 ──→ Task 3
                    └──→ Task 4 Product Interaction

Task 4 Notification Protocol/Adapter
                    └── 可与 Task 3 并行
```

规则：

- Task 2 不得在 Revision 无 Profile 时假装创建 Product Todo；
- Task 3 不得在无 Collet 时发明 Rollout；
- Task 4 互动不得把 Collet ID塞进旧 Context 字段；
- 后续任务可以读取前序能力，但不能重新实现或绕过前序 Repository 合同。

## 4. 候选修改范围

### Backend

```text
apps/backend/src/agentclaw/community/core/task/todos/
apps/backend/src/agentclaw/community/core/repository/protocols/task/todos.py
apps/backend/src/agentclaw/community/core/repository/protocols/task/todo_templates.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/todos/
apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/todos/
apps/backend/src/agentclaw/community/api/task/
apps/backend/src/agentclaw/community/di/modules/task/todo_bindings.py
apps/backend/src/agentclaw/community/core/task/todos/sql/
apps/backend/tests/community/core/task/todos/
apps/backend/tests/community/repository/task/todos/
apps/backend/tests/community/adapters/http/openapi_v1/task/todos/
apps/backend/tests/community/architecture/
```

具体文件必须由执行 Agent 基于当前树确认；禁止仅凭本文路径新建重复模块。

### Frontend

```text
apps/frontend-nextgen/src/domain/todos/
apps/frontend-nextgen/src/services/backendApi/todos/
apps/frontend-nextgen/src/hooks/useTodoDetail.ts
apps/frontend-nextgen/src/pages/Lab/Todos/
apps/frontend-nextgen/test/domain/todos/
apps/frontend-nextgen/test/services/backendApi/todos/
apps/frontend-nextgen/test/hooks/
apps/frontend-nextgen/test/pages/Lab/
```

## 5. 每个任务的最低验证

### Backend focused

根据实际文件选择最小集合，至少覆盖：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/todos \
  tests/community/repository/task/todos \
  tests/community/adapters/http/openapi_v1/task/todos \
  tests/community/architecture
```

### Backend 完整

```bash
cd apps/backend
bash scripts/ci_test.sh
```

### Frontend focused

```bash
cd apps/frontend-nextgen
npm run check:open-core
npm run typecheck
npm test -- --runInBand \
  test/domain/todos \
  test/services/backendApi/todos \
  test/hooks/useTodoDetail.test.tsx \
  test/pages/Lab/TodoDetail.test.tsx \
  test/pages/Lab/Todos.test.tsx
```

### Frontend 完整

```bash
cd apps/frontend-nextgen
npm run ci
```

### 文档和 diff 卫生

```bash
git diff --check
git diff --check --cached
```

执行 Agent 必须记录实际运行的命令和结果。未运行完整 CI 时不能写“全部通过”。

## 6. 全局验收清单

- [ ] 协议层目录没有修改；
- [ ] ProductProfileService 只经 DI Protocol 调用；
- [ ] Revision 与 Profile 使用 nullable 字段和 CAS；
- [ ] Todo 与 Collet 使用独立 nullable 字段和 CAS；
- [ ] Collet ID 未写入 `collab_context_id` 或 `ac_todo_execution`；
- [ ] Dima + BotTeam Todo 聚合本地原子创建；
- [ ] Product Todo 不进入旧 Context dispatch；
- [ ] Legacy Todo 行为保持；
- [ ] Progress 不以列表顺序选择当前 Rollout；
- [ ] Frontend 不解释协议 DTO；
- [ ] 互动 single-flight 和 `delivery_unknown` 已测试；
- [ ] 未确认 SHUTDOWN 不映射 cancelled；
- [ ] 通知幂等、已读不回退；
- [ ] tenant/env/space 权限已测试；
- [ ] 数据迁移对历史 NULL 数据安全；
- [ ] focused tests、架构测试和 diff check 有实际结果；
- [ ] 完整 CI 的执行状态被如实报告。


## 7. 验收点实现映射

| Slice | 覆盖 AC | 主要交付 | 快速验证 |
| --- | --- | --- | --- |
| Slice 1：Revision → ProductProfile | AC-001、AC-002、AC-013、AC-014 | Revision binding/CAS、稳定YAML、async发布、DI/HTTP | BotTeam service/repository/HTTP/architecture tests |
| Slice 2：Dima/Todo → Product Collet | AC-003、AC-004、AC-005、AC-013、AC-014 | 原子来源聚合、Todo binding/CAS、runtime resolver、Collet补偿、scheduler分流 | Todo source/product creation/repository/scheduler tests |
| Slice 3：Product Progress | AC-006、AC-007、AC-008、AC-012、AC-013、AC-014 | Progress VO、Rollout适配、Stage details防御、Frontend投影和降级 | Progress service/HTTP/frontend mapper/detail tests |
| Slice 4：Notification + Product Interaction | AC-009、AC-010、AC-011、AC-012、AC-013、AC-014 | Inbox adapter、interaction存储/single-flight、interactive/cancel、Frontend输入锁 | interaction/notification repository/service/HTTP/frontend tests |

所有 slice 禁止修改 ProductProfileService/L5 实现。若后台 Scheduler 缺少合法 ActorContext 合同，只记录缺口并跳过后台自动创建，显式 Human 创建和补偿仍需完成。
