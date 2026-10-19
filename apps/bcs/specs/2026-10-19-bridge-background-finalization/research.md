# Bridge 后台任务完成后缺少 final：问题与证据

> 日期：2026-10-19；现场时间统一为 UTC+8。
> 状态：故障链与原生 idle 的八组隔离实验已核对；Hook 收尾方案尚未实现或验收。
> 公共源码基线：`7385c8323439081a4022b0623e13788d7f816e35`（本次创建 worktree 前拉取的 GitHub `dev`）。
> [Spec](spec.md) · [执行计划](plan.md)

## 1. 问题描述

某 Claude stream-json 兼容 launcher 会话在正常输出最终回答后，界面仍等待结束，
约十分钟后收到：

```text
engine exited: stream idle for 600s (3 pending background task(s))
```

不是“模型没有产出 result”，也不能从 `3 pending` 推断 CLI 仍在执行三个任务。
该计数来自 bridge 自己的结果投递追踪，执行完成与投递确认被混在同一个集合中。

### 1.1 同一会话的两轮故障不能合并为因果链

为避免把运行标识和业务内容放入公共仓库，用 R1/R2 代指两个不同 run。

| 样本 | 开始 | 结束 | 事实 |
| --- | --- | --- | --- |
| R1 | 14:44:58 | 15:18:23 | 旧部署累计保留 8192 条事件，下一条变为 `buffer_overflow`，取消 run |
| R2 | 15:30:16 | 15:49:59 左右 | 15:39:59 已收到成功 result；bridge 留下 3 个消费等待项，600 秒后 runtime error |

R1 的终态 seq 为 8193；R2 的错误终态 seq 为 6758，之前有 6757 条非终态事件。
两者是不同 run，不是一次 run 内的 idle 错误被 retention 错误覆盖。

R2 观察到 7 个不同后台任务（3 Agent + 4 Bash），全部收到终态通知。
两轮的 `task_id` 和 `tool_use_id` 集合交集均为空；三个 Agent 的发起调用也存在于
R2 的执行记录中。因此不支持“R1 杀掉三个任务，R2 继承其 pending，注定超时”的解释。

R1 的 retention 问题已由基线提交中的前缀回收和 16384 条上限调整处理。
历史现场的 8192 是当时的部署值，不是当前源码值。参见
[retention spec](../../docs/superpowers/specs/2026-10-19-bridge-plugin-retention-reclamation-design.md)。
本次不重新修改 retention、发送游标或缓冲预算。

### 1.2 R2 的明确证据

- 原始 stdout 共 16028 条记录，有 `result.subtype=success`、`is_error=false`、
  `terminal_reason=completed`、`stop_reason=end_turn`，回答为 2446 字符。
- 同次 Stop hook 记录 `background_tasks: []`，最后回答长度一致。
- 三个 Agent 的 `system/task_notification(status=completed)` 都已到达 bridge。
- 4 个 Bash 的实际 `tool_result` 清除了等待；3 个 Agent 的等待没有清除。
- 主 agent 会话文件中，三个通知以 `attachment.type=queued_command`、
  `commandMode=task-notification` 接入消息父链。最终主 assistant 的 UUID 与 stdout 一致。
- 这三次消费没有在 stdout 中产生相应的 user 通知，也没有额外 `system/init`。
- 依现有 mapper 回放，最后是 `pending=3, enqueued=3`；十分钟后的真实错误也报告 3。

会话文件仅作为此次取证的交叉证据；本 spec 不将它作为生产实时判定依赖。
不提交原始回答、工具参数、思考内容、用户标识或真实会话 ID。

## 2. 源码链路

以下路径相对 `apps/bcs/crates/tools/bcs-bridge/`；优先使用函数名定位，行号会变化。

| 位置 | 已核实行为 |
| --- | --- |
| `core/src/engine/claude_code.rs::CcTurnMapper::new` | 每次 run 创建空集合，不随 resume 继承 bridge pending |
| `CcTurnMapper::map_line` / `consume_wake` | 完成通知入队；特定投递信号或一次 init 释放一个等待 |
| `map_result` | 成功 result 已经映射为 `CcMap::Final` |
| `ClaudeCode::run_turn` 的 `CcMap::Final` 分支 | pending 非零则 continue，丢弃作为终态的候选结果 |
| `ClaudeCode::run_turn` 的 idle watchdog | 600 秒无 stdout 后 kill CLI，返回 `TurnError::EngineExited` |
| `core/src/engine/mod.rs::TurnError` | `engine exited:` 是错误 Display 前缀，不是 CLI stderr 原文 |
| `core/src/run.rs` 的 engine outcome 分支 | 转成 `chat_error(runtime_error)`；成功 outcome 才走 `chat_final` |
| `run.rs::push_frame` 与主循环 | retention 失败保留 error、取消、返回 Terminate，主循环退出 |
| `run.rs` 收尾 | 等待引擎清理，保留原终态，完成 session/registry 生命周期 |

根因是“消费证据缺失导致成功 result 被扣住”，不是 result 解析失败。

## 3. 原生 idle：八组隔离实测

### 3.1 环境与方法

- launcher：cfuse 2.6.44；每次 `system/init` 实际报告 Claude Code 2.1.199。
- 独立临时 cwd、全新会话、stream-json stdin/stdout；保持 stdin 开放直至观察窗口结束。
- 限定工具与 sleep/printf 测试，不连接原故障 session，不修改运行中的 bridge 配置。
- 禁用测试会话的 MCP、skills，隔离 user/project settings；并未检验真实 Hook 合并。
- 每条 stdout 保存 seq、接收时间、active/cleanup 阶段；只分析 active 阶段。
- 实际读取子进程环境确认开关透传；所有用例成功 result，无损坏 JSON。
- 原始材料保留在调查机器的 `/tmp/cli-idle-probe-20261019/`，不是 CI 依赖。
  后续测试必须使用合成数据，不复制现场文件到公共仓库。

| 用例 | 配置 / 行为 | 观测结果 |
| --- | --- | --- |
| control | 不启用状态事件 | 1 result，无 session_state_changed |
| basic | 开启状态事件，连续两条普通输入 | 各自 result 后紧跟 idle |
| bash_default | 后台 Bash，主 agent 立即回复 | 首个 result 后 idle 时 Bash 尚未结束；之后自动唤醒 |
| agent_default | 后台 Agent，主 agent 立即回复 | 首个 result 后 idle 时 Agent 尚未结束；之后自动唤醒 |
| agent_running | 同上，`BG_TASKS_REPORT_RUNNING=1` | 仍在 Agent 存活时 idle |
| agent_running_true | 同上，`BG_TASKS_REPORT_RUNNING=true` | 同样不能阻止提前 idle |
| midturn | 后台在前台工具循环中完成 | 一个 init/result、无 user 通知投递帧，最终 result 后 idle |
| queued_during_answer | 后台在长回答生成中完成 | 中间 result 后直接续处理；最后一个 result 后才 idle |

状态事件通过 `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1` 启用。
所有观测到的 idle 都在 stdout 顺序中紧跟成功 result；约毫秒级的接收间隔不是契约，
不得用它配对消息。事件有 `session_id`、自身 `uuid`、`state`，没有 result/turn 关联 ID。

### 3.2 两个必须保留的反例

后台仍在执行：

```text
running → init → task_started(A) → result(R1) → idle
... A 继续工作 ...
task_notification(A, completed) → running → init → result(R2) → idle
```

通知已完成但需要下一轮处理（实际 seq）：

```text
79  task_notification(completed)
153 result(原先长回答)
154 system/init                         # 153 与 154 之间没有 idle
163 result(处理通知后的回答)
164 session_state_changed(idle)
```

执行中消费：没有额外 init 或 user 通知，仍存在最终 result + idle。
这支持研究原生收尾信号，但不能从八个实验推出所有版本/竞态都具有同一语义。

### 3.3 结论与限制

- 当前 CLI 有可用原生 idle，但 idle 不等于全局后台任务为空。
- `BG_TASKS_REPORT_RUNNING` 两个取值在本实验中没有改变该行为；二进制包含字符串
  不证明能力有效，也不能用新版 SDK 描述代替本机版本实测。
- 本地生命周期表只能说明“观察到的任务均终态”，不能保证恢复任务、嵌套任务及
  特殊类型的枚举完整性。生产 final 不应只依赖自建计数。
- Stop 快照为空在现场存在；其覆盖范围、落点时序、和具体 result 的强关联尚未证明。
- 尚未完成多并发任务、前台/后台 MCP、失败/中断、Stop 被其他 hook 阻止、消息
  注入竞态和 Hook 丢失的验证；不得将这些列为已通过。

## 4. Paseo 对照与不直接照搬的原因

参考本地 Paseo 提交 `9521ee995`，重点为
`packages/server/src/server/agent/providers/claude/agent.ts` 和 `subagents/live-source.ts`。

它长期持有 SDK query：原生 result 完成一个主 turn，不杀 CLI；后台任务跨 turn 存活，
后续 init 开启 autonomous turn。任务身份表是 session 级，状态跟随声明式 task 事件。
它不要求消费 pending 为零才结束主 turn，也不读 transcript 父链来放行 result；
会话文件主要用于历史恢复和 rewind。

相关提交 `8ddeb79c8` 修复后台帧误开启永不结束的主 turn，采用 init/result 作为边界。
其测试包含“父 turn idle，后台 sleep 完成后再自动唤醒”。本次只读过这些测试，未运行。

当前 BCS run 的一个终态会触发本轮清理。只复制 Paseo 的 result→completed，仍会
提前杀后台工作；全面采用其模型需要定义 autonomous turn 的上游归属，是另一项设计。

## 5. 待验证问题，不能以假设关闭

1. bridge 注入的 Stop hook 能否通过 launcher 合并并执行，且不覆盖原 hooks/settings？
2. Stop 快照包含什么：后台 Bash、Agent、嵌套任务、MCP、监控任务；缺字段如何表示？
3. Hook 是否有能与原生输出关联的 invocation ID？`prompt_id` 是否跨自动唤醒复用？
4. 同一个原生 turn 内重复 Stop / 其他 hook 阻止 Stop 时，哪个快照属于实际 result？
5. 快照后是否能启动新任务或入队新通知？原生 idle 是否约束了对应续处理？
6. Hook 与 stdout 两通道可否建立可验证的先后关系，而不是按接收时间配对？
7. policy 禁用 hooks、缺 idle、丢回调、并发输入、异常退出是否都能显式失败且不误发 final？

这些问题是 [Spec 的 G1–G4](spec.md#3-上线前硬门槛) 和执行计划 P1 的准入条件。
