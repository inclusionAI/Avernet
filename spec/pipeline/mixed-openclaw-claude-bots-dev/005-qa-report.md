---
agent: tc-qa
status: completed
created: 2026-08-08T18:30:00+08:00
iteration: 3
environment: local-standalone
---

# QA 测试报告：Claude Code Bot BCN 下行

## 最终结论

**PASS**。初始失败记录保留在下文；已完成其三项整改并从真实浏览器页面验证当前 Claude Developer 的一次无副作用回复。验收只记录链路阶段和结果，不记录聊天正文、run ID 或凭据。

| 编号 | 复测项 | 结论 | 证据 |
| --- | --- | --- | --- |
| QA-R01 | 当前 Provider Bot 可见性与历史记录隔离 | PASS | 当前 planner/developer/reviewer 均以 `（当前）` 标识；重启时仅清理受 runtime 状态追踪的上一组三个 Provider Bot。 |
| QA-R02 | Claude CLI 选择 | PASS | 异常退出的 native CLI 被跳过，健康的既有 CLI 通过无副作用 `--version` 检查后注入 relay。 |
| QA-R03 | 用户页面消息 → BCS → bridge → BaaS → normalCC → relay → 页面 final | PASS | 新建当前 BCS 群，页面发送一次无副作用短消息；Developer final 成功渲染。BCS、bridge、relay 都记录了相同运行相关元数据。 |
| QA-R04 | 前端在 singlebox 返回后存活 | PASS | 从真实 macOS Terminal 执行 `restart frontend` 后，命令返回且 Node 继续监听 8000，HTTP 健康响应可用。 |
| QA-R05 | 新鲜群中的 Planner 与 Developer 双向最终回复 | PASS | 两个当前 Provider Bot 均实际收到 `chat.send` 并各自产生 BCS `final`；所有相应 bridge 流正常完成，无并发请求超时。 |

### QA-R05：最终独立本地复测

- 本轮只读检查的 fresh 群/会话中，Planner 收到 2 次 `chat.send` 并产生 2 个 `final`，Developer 收到 1 次 `chat.send` 并产生 1 个 `final`；对应终态错误计数均为 0。
- `bcs_baas_provider.log` 为这三次下行均记录 `bridge.stream_complete` 且 `cancelled=false`；当前日志目录内 `Concurrent request timeout`、Provider bot mismatch 和 webhook 非 2xx 的计数均为 0。
- `http://localhost:8000` 返回 HTTP 200。浏览器实际打开该群后，`/bcnproxy/bots/my` 和群/会话/消息读取均返回 200，页面无 JavaScript 错误。
- 可见 DOM 中三个 Claude 卡片只出现 `Claude Planner（当前）`、`Claude Developer（当前）` 和 `Claude Reviewer（当前）`；未出现不带“（当前）”标识的旧同角色卡片。

证据日志：

- `scripts/.dependencies/logs/bcs.log`（2026-08-08 19:16:29–19:17:48 的当前 Provider `chat.send`、`bot_deliver_result=delivered` 与三条 `chat_event_state=final`）
- `scripts/.dependencies/logs/bcs_baas_provider.log`（对应三条 `bridge.stream_complete`）
- 浏览器受管 tab 的网络记录（`/bcnproxy/bots/my`、group/session/messages 均 HTTP 200）与无 JS error 检查。

## 初始失败记录（已修复）

## 测试环境

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/mixed-openclaw-claude-bots-dev`
- 服务：本地 standalone；Frontend、BCS、BaaS、三个 Claude relay 和 bridge 均处于运行状态。
- 边界：未重启服务、未改动实现；对当前有效 Developer 仅发送一次固定、无副作用的短响应检查。报告不记录凭据、会话键、run ID 或聊天正文。

## 结果汇总

| 编号 | 用例 | 结论 | 说明 |
| --- | --- | --- | --- |
| QA-01 | 混合栈服务存活 | PASS | 5 个 OpenClaw、3 个 Claude relay、3 个 BaaS adapter、BCS Provider bridge 与 Frontend 均显示运行。 |
| QA-02 | 截图所示群的 Developer 投递 | FAIL | BCS 向历史 Provider Bot 下行时收到 HTTP 401；该 Bot 不属于当前 bridge 管理的三个 Provider Bot。 |
| QA-03 | 当前 Developer 的 Provider 下行至 relay | FAIL | 正确 Provider 凭据和当前 bot 引用的 h2c 流式探测到达 relay；CLI 在生成回复前被 SIGKILL，未产生 delta/final。 |
| QA-04 | Claude CLI 基础可执行性 | FAIL | `claude --version` 退出码为 137（SIGKILL），与 relay 失败一致。 |

## 端到端证据（脱敏）

### QA-02：前端群引用了历史 Provider Bot

- `2026-08-08 17:31:10` 与 `17:31:23`，BCS 对截图对应群执行 `chat.send`，目标为历史 Provider 记录，而不是当前 runtime 的 developer 引用。
- BCS 随后记录 webhook HTTP `401`；这正是前端“消息投递给 Bot Claude Developer 失败”的直接原因。
- 当前 bridge 运行时仅注册并白名单本次启动创建的三个 Provider Bot。历史 Provider 的 Provider 级凭据与当前 bridge 不匹配，因此 bridge 不会转发该请求到 BaaS。

证据日志：

- `scripts/.dependencies/logs/bcs.log`（17:31:10–17:31:23 的 `provider downlink`、`status=401`）
- `scripts/.dependencies/bcs_baas_provider.state.json`（当前三角色 Provider Bot 映射；仅用于本次脱敏比对）
- `scripts/.dependencies/logs/bcs_baas_provider.log`（相同时间窗口仅存在当前 bot 的 `chat.inject` 成功记录，没有该历史 `chat.send` 的 forward 记录）

### QA-03：当前 Provider 路径到 Claude relay

对当前 Developer 以 Provider 2.0 h2c/SSE 协议执行了固定短响应探测。bridge 的唯一上游为 BaaS `/bcn/downlink`；relay 记录证明 BaaS normalCC adapter 已把该请求送达 Developer relay，并已解析其独立工作区和模型配置。

- `2026-08-08 17:36:50`：Developer relay 进入 `chat.send`，已解析模型与工作区。
- 同一调用中，Claude Code SDK 在约 60ms 后报告子进程被 `SIGKILL`，没有可返回的 delta/final。
- `2026-08-08 17:36:56`：bridge 因上游流未形成正常结束而记录下行取消失败。

证据日志：

- `scripts/.dependencies/logs/bcs_baas_provider.log`（17:36:56 的 `bridge.downlink_failed`）
- `scripts/.dependencies/logs/claude_relays.log`（17:36:50 的 `chat.send`、binding 解析、`SIGKILL`；日志内容已保持不含聊天正文）

### QA-04：独立 CLI 检查

在相同本机执行边界内，`~/.local/bin/claude --version` 退出码为 `137`。可执行文件存在且签名有效；故当前阻塞点不是 Claude 模型登录或 relay URL 解析，而是 Claude CLI 进程被外部终止。

## 初始结论（已修复）

初始迭代为 **FAIL**，原因和证据如上。复测对应完成：

1. Provider runtime 状态保留 Provider 管理凭据和上一轮 bot 引用，启动/停止仅删除被追踪的三项历史记录；当前卡片显式标识，避免选择失效同名 bot。
2. relay 对显式路径 fail closed；自动发现会跳过 `--version` 异常退出的 native CLI，使用健康候选，不依赖额外登录动作。
3. 用户身份经 WebSocket request 的 `bot_id` 字段传递，未再误写入 `bot_uuid`；BCS 因此在无 `@` 提及时正确选择当前 Developer Driver，并返回页面 final。
