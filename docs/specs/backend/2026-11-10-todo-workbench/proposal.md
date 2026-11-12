# 待办工作台自动执行闭环

## 问题

当前 Todo 把 `draft`、手动 Run 和 Context 读取时状态投影混在主流程里：创建时立即创建 Context，普通用户还需要手动启动。这与“创建待办后由系统自动调度，Context 只反映真实运行状态，完成后进入人工验收”的产品语义不一致，也容易出现 Todo 与运行态不一致。

## 目标

- Todo 使用 `pending -> in_progress -> pending_acceptance -> completed` 四个持久产品状态。
- 创建 Todo 只校验空间/Bot 并持久化 `pending`，不创建 Context、不启动运行。
- 后台 Scheduler 扫描 Todo：派发 `pending`，刷新 `in_progress`。
- Context 只保留 `created -> in_progress -> done` 的真实运行状态。
- Bot runtime 的 `get_run(run_id)` 是运行完成的事实来源；不依赖 Skill 主动上报。
- 运行成功完成后，Context 进入 `done`，Todo 进入 `pending_acceptance`；人工通过后 Todo 进入 `completed`。
- 保留 `/run` 作为补偿/运维入口，普通前端不调用、不展示。

## 非范围

- 不修改旧 Tasks。
- 不做 Todo/Context 跨库事务、持久 claim/lease 字段和 Todo 失败状态。
- 不做 Todo 编辑；验收拒绝后第一版保持 `pending_acceptance`。
- 不完善 Context 权限模型。
- 不做团队知识库。

## 已确认决策

- Scheduler 扫描 Todo，不扫描 Context。
- 创建 Todo 后即为 `pending`；真正创建 Context 并成功启动运行后才转 `in_progress`。
- Context 不承载“待验收”；`pending_acceptance` 仅属于 Todo。
- `COMPLETED` 才推进到待验收；`FAILED`、`TIME_OUT`、`ABORTED` 第一版保持 `in_progress` 并记录日志。
- Human 互动继续通过 B 的事件接口保存；A 不创建互动表。
- 人工验收通过使 Todo 完成，拒绝保持待验收；Context 保持 `done`。
