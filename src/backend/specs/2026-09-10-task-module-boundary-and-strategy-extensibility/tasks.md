# 目标态验收清单：任务模块事件反应链

> 本清单定义目标态能力与验收。

## 1. 图谱事实与事件

- [ ] `TaskGraphService.report(TaskCallbackData)` 是唯一任务过程事实入口。
- [ ] 图谱服务校验报告身份、版本、幂等、状态机、字段白名单和授权范围。
- [ ] 图谱事实持久化与 Outbox 事件原子完成。
- [ ] 图谱服务从状态变化产生 `PLAN_REQUESTED`、`DISPATCH_REQUESTED`、`EXECUTION_REQUESTED` 和终态事件。

验收：重复、过期、越权或非法字段报告不会改变图谱；合法报告只产生一次有效状态推进。

## 2. 规划、派发与执行

- [ ] TaskPlanner 仅消费 `TaskContext`，输出 `PlanResult` 并以 `PLAN` 报告。
- [ ] TaskDispatcher 仅消费单个 `TaskNode`，输出受限 `TaskNodePatch` 并以 `DISPATCH` 报告。
- [ ] TaskRunner 仅消费单个 `TaskNode`，启动 executor 并报告启动及最终执行事实。
- [ ] 事件消费具备至少一次投递下的幂等语义。

验收：TaskPlanner、TaskDispatcher、TaskRunner 不读取/写入完整图谱、不互相调用；批量吞吐不改变其单节点领域接口。

## 3. 策略扩展

- [ ] 中心化动态规划策略通过 `TaskContext -> PlanResult` 驱动闭环。
- [ ] Master-Slave 通过依赖解锁汇总节点，并使用同一事件链。
- [ ] 接力规划通过 `PLAN` 报告产生下一步节点。
- [ ] 图谱拒绝接力规划中的过期版本、越权归属、非法关系和超深度请求。

验收：三种模式只替换策略实现，不增加跨模块直连或图谱写入旁路。

## 4. 统一任务入口

- [ ] TaskService 内部完成任务识别、四要素澄清和确认；TaskIntake 不作为公开模块。
- [ ] TaskCli 仅公开 `task [goal]`、`task get <task_id>`、`task list`。
- [ ] 确认后由 TaskService 的 `execute(TaskInfoRequest)` 创建正式任务；确认前没有 `task_id`。
- [ ] Workflow 和 YAML 仅属于 executor 实现，不成为 TaskCli 的任务类型或二级命令。

验收：人或 Agent 从一句话开始即可完成澄清、确认和执行；TaskCli 不暴露 `intake_id`、`answer`、`review` 或 `confirm`。

## 5. 架构约束与验证

- [ ] 依赖检查限制 TaskPlanner、TaskDispatcher、TaskRunner 访问 Graph 模块或彼此实现。
- [ ] 契约测试覆盖报告类型、事件投递、版本与幂等。
- [ ] 策略闭环测试覆盖中心化、Master-Slave 与接力模式。
- [ ] CLI 会话测试覆盖澄清四要素、确认、执行和查询。

验收：架构规则和核心协议均由自动化测试验证。
