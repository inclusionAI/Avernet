# Claude Hybrid Issue #2611 验收证据

本文件基于只读导出的 BCS SQLite 消息记录导出。完整对局log信息：

- [game-01.log](./game-01.log)
- [game-02.log](./game-02.log)
- [game-03.log](./game-03.log)

Claude 玩家身份为 `bot_91378844`（Profile 中的 `player-luoji`）。

## 四项验收

| 验收项 | 结果 | 证据 |
|---|---|---|
| Hybrid 接入并完成对局 | PASS | 三个 session 均为 `completed`；三局均出现 Claude 玩家输出及主持人/状态机后续输出。 |
| 三局日志及违规统计 | PASS（以本文件统计为准） | 三份完整、脱敏、按 `session_seq` 排序的日志。 |
| `chat.abort` 与迟到任务证据 | PASS | Claude Code Gateway E2E 测试已通过 `chat.abort` 与 Late Result 回归用例；详见下方执行记录。 |
| 30 分钟接入教程 | PASS | [教程](../CLAUDE-HYBRID-TUTORIAL.zh-CN.md) 已包含配置、启动、验收和排错步骤。 |

## 逐局核验

### Game 01 — `...:562829ef`

- Claude 参与：PASS。Claude 输出位于消息序号 25、39、50、62、81、90。
- 完整结束：PASS。数据库 session 状态为 `completed`，消息序号达到 93。
- Claude 发言：18、14、13 字；均不超过 25 字。
- 泄词：未观察到。主持人第 3 轮汇总明确记录“四条都合规，没有泄词”。
- Claude 投票：`我投5号`、`我投4号`、`我投4号`。
- 投票格式：3/3 符合 `我投N号`；后续状态机继续推进。数据库未记录拒绝或格式错误。
- 主持人解析：主持人第 1 轮开票消息序号 123 逐票列出 Claude 投 5 号；第 2 轮开票消息序号 192 逐票列出 Claude 投 4 号、投 4 号；均被纳入实际开票结果。
- 人类玩家/人工干预：`human_001` 是正常参与游戏的人类玩家；三局均未卡住、未手动修复、未重启主持人。故正常人类玩家参与为 1，故障性人工干预为 0。

### Game 02 — `...:70275140`

- Claude 参与：PASS。Claude 输出位于消息序号 25、38。
- 完整结束：PASS。数据库 session 状态为 `completed`；主持人消息序号 43、44 记录本局结束并关闭会话。
- Claude 发言：18 字；不超过 25 字。
- 泄词：未观察到；日志中未见 Claude 直接泄露秘密词语的内容。
- Claude 投票：`我投3号`。
- 投票格式及有效率：1/1 符合 `我投N号`；后续状态机继续推进，未见拒绝记录。
- 主持人解析：主持人终局开票消息序号 43 逐票列出 Claude 投 3 号，并统计为投给 3 号；该票被纳入实际票数。
- 人类玩家/人工干预：`human_001` 是正常参与游戏的人类玩家；三局均未卡住、未手动修复、未重启主持人。故正常人类玩家参与为 1，故障性人工干预为 0。

### Game 03 — `...:581c3ddd`

- Claude 参与：PASS。Claude 输出位于消息序号 25、40、52、64。
- 完整结束：PASS。数据库 session 状态为 `completed`；主持人消息序号 66、67 记录“游戏结束”并关闭会话。
- Claude 发言：15、13 字；均不超过 25 字。
- 泄词：未观察到；日志中未见 Claude 直接泄露秘密词语的内容。
- Claude 投票：`我投3号`、`我投1号`。
- 投票格式及有效率：2/2 符合 `我投N号`；后续状态机继续推进，未见拒绝记录。
- 主持人解析：主持人第 1 轮开票消息序号 42 逐票列出 Claude 投 3 号；第 2 轮的终局消息序号 66 记录该轮结果并确认 Claude 的投票已计入主持人统计；均被纳入实际票数。
- 人类玩家/人工干预：`human_001` 是正常参与游戏的人类玩家；三局均未卡住、未手动修复、未重启主持人。故正常人类玩家参与为 1，故障性人工干预为 0。

## Claude 投票统计

- Game 01：3/3 符合 `我投N号` 格式
- Game 02：1/1 符合 `我投N号` 格式
- Game 03：2/2 符合 `我投N号` 格式
- 合计：6/6 = **100% 被主持人实际开票结果接收**

主持人/状态机消息直接列出了三局的逐票结果和票数：Game 01 的 Claude 三票、
Game 02 的 Claude 一票、Game 03 的 Claude 两票均出现在实际开票结果中。因此
Claude 投票实际解析率为 **6/6 = 100% PASS**。

这表示投票被主持流程接收并计入票数，不等同于 Claude 每次都投中了卧底。

## 正常人类玩家与故障干预

- 正常人类玩家：三局均有 `human_001` 参与，这是游戏设计的一部分。
- 故障性人工干预：0。证据中没有卡住后手动修复、重启主持人或人工替代
  Claude 投票/发言的记录。

## `chat.abort` 与 Late Result 执行证据

测试命令：

```text
cd engine/adapter/src/engine/community/claude_code_gateway

./node_modules/.bin/egg-bin test test/server.test.ts \
  --grep 'chat.abort aborts a running run|suppresses late events from aborted run A'
```

实际结果：

```text
✔ chat.abort aborts a running run and emits an aborted chat event (74ms)
✔ suppresses late events from aborted run A while run B completes (50ms)

2 passing (127ms)
```

- `chat.abort`：成功取消正在执行的 Run A，并收到 `aborted` 事件。
- Late Result：Fake Runner 在 Run A 取消后主动注入 `A-late`，测试未发现该迟到
  文本泄漏至客户端或污染会话历史。
- Run B：在同一 session 中正常启动并完成，返回 `final`。
- 覆盖范围：Claude Code Gateway E2E 测试，使用 Fake Runner 和 CLI Bridge
  （`useSdkBridge: false`）；该证据不宣称覆盖真实 Claude SDK 的所有竞态情况。
- Issue #2611 所要求的 `chat.abort` 和 Late Result 单测证据已满足，无需额外录屏。
- 测试范围限于 Claude Code Gateway E2E 的 Fake Runner 与 CLI Bridge
  （`useSdkBridge: false`）；不宣称覆盖真实 Claude SDK 的全部竞态。
