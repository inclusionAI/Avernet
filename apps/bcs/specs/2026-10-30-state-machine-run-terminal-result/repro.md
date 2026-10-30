# 复现记录：StateMachine 运行失败后主持人不会被唤醒

- 日期：2026-10-30
- 关联：Issue #2610
- 环境：singlebox 本地部署（standalone），`oss-camp2026` @ `86252c1e9`；Ubuntu 22.04；Rust 1.99.1；
  OpenClaw 2026.6.1；模型 `deepseek/deepseek-v4.1-flash`（OpenAI 兼容接口）
- 对局：`6bots_undercover_game_profile`，1 位人类（`human_001`）+ 5 个玩家 Bot，主持人为 manager
- 群消息队列：未开启（singlebox 默认 `DeliveryPolicy` 为 off，消息走 legacy 直投）

## 1. 步骤

人类一侧由脚本经 BCS HTTP 接口驱动，与前端副屏调用的是同一组接口：

1. `POST /groups`：主从群，主持人为 manager，5 个玩家为 worker，`create_initial_session=true`。
2. `PATCH /sessions/{sid}/members/human_001 {"mode":"present"}`：人类加入当前会话。
3. `POST /sessions/{sid}/chat {"message":"开始"}`：主持人发牌，提交第 1 轮发言 Run。
4. 故障注入：在 3 号玩家「稳健老陈」的节点被派发之前，`kill -9` 它的 OpenClaw 网关进程。
5. `POST /state-machine-runs/{run}/nodes/speak_2/respond`：人类提交 2 号发言，Run 推进到 3 号。
6. 观察 Run 状态、公开事件、主持人日志与会话消息。

## 2. 时间线

| 时间 | 事件 |
| --- | --- |
| 13:04:12 | 主持人提交发言 Run `sm-cb8caf8d-e534-4322-96e7-52451f5bf382`；`speak_1`（戏精阿浪）开始 |
| 13:04:16 | `speak_1` 完成，`speak_2`（人类）开始等待输入 |
| 13:04:39 | `kill -9` 稳健老陈的网关进程（端口 30811） |
| 13:05:10 | 人类提交 `speak_2`；`speak_3` 派发失败：`Bot '玩家稳健老陈' is not connected`；`NODE_MAX_ATTEMPTS = 1`，Run 立即 `failed` |
| 13:05:10 – 13:08:50 | 主持人无任何投递（无 `chat.send` / `chat.inject`），无激活，会话无新消息 |

Run 与节点终态（BCS SQLite 只读查询）：

```text
RUN sm-cb8caf8d-… failed by=谁是卧底主持人 13:04:12..13:05:10
    err=state-machine node delivery failed for bot '玩家稳健老陈': Bot '玩家稳健老陈' is not connected
    speak_1  completed  玩家戏精阿浪   13:04:12..13:04:16
    speak_2  completed  (human)        13:04:16..13:05:10
    speak_3  failed     玩家稳健老陈   13:05:10..13:05:10
    collect  pending    谁是卧底主持人
    speak_4..speak_6 pending
```

该 Run 的公开事件（`bcs_events`）只有 `state_machine.run.created`、`state_machine.run.started`，
以及 `speak_1`、`speak_2` 的 `node.started` / `node.completed` 和 `speak_3` 的 `node.started`。
**没有 `state_machine.run.failed`，也没有节点失败事件。** 失败只出现在 BCS 内部日志：
`WARN bcs_collaboration_runtime::runtime: state_machine: run failed …` 与 `event_type="run_failed"`。

## 3. 结论

与 Issue 描述一致：Run 失败后，`fail_run` 只更新状态、作废未完成派发、写日志；Chat 会话中没有任何
面向 manager 的投递，也没有公开事件。主持人的唤醒链在这里断开，只能等人类说「卡住了」。

## 4. 复现中顺带观察到的问题

1. **人类提交成功却收到 HTTP 400。** `respond` 接口同步触发下一节点派发；派发失败的错误被原样返回给
   人类（`invalid runtime request: state-machine node delivery failed …`），但 `speak_2` 实际已完成。
   副屏会把一次成功的发言显示为失败。
2. **manager 自身激活失败同样会让对局停住**（对应 Issue 加分项 5）。本次环境中观察到三种情形：
   - 模型请求网络中断：`Agent run failed before completing a reply.`，主持人刚执行完 `init` 就停止；
   - 模型服务返回 429：OpenClaw 的处理决策为 `surface_error`，不重试，本次激活直接失败；
   - 模型请求长时间无响应：OpenClaw 诊断为 `stalled session … active_work_without_progress`，
     后续消息在会话车道中排队。
   三种情形下都没有新的唤醒源，对局要等人类再发一条消息才能继续。详见设计文档第 13 节。
