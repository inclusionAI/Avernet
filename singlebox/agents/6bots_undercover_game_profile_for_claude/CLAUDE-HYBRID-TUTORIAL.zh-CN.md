# Claude hybrid 谁是卧底接入教程

本文说明如何在 Avernet 的谁是卧底牌桌中，1 个 OpenClaw 主持人 + 4 个 OpenClaw 玩家 + 1 个 Claude 玩家 + 1 名人类玩家，完成三局真实模型参与的“谁是卧底”游戏。Claude 玩家通过 Claude Code Relay 接入 Avernet Hybrid 模式。

## 1. 前置条件

在仓库根目录执行。需要准备 Node.js、npm、Rust/Cargo、protoc、Python、
`jq`、OpenClaw 和 Claude Code。首次使用可以先运行：

```bash
./singlebox/singlebox.sh install-tools
./singlebox/singlebox.sh check bcs_frontend
```

Claude hybrid 使用两份清单：

| 清单 | 作用 |
| --- | --- |
| `singlebox/agents/6bots_undercover_game_profile` | 原始的 6 个 OpenClaw Bot |
| `singlebox/agents/6bots_undercover_game_profile_for_claude` | 要替换的 Claude Bot |

`--exclusive-profile-dir player-luoji` 是替换键。两份清单必须都使用
`player-luoji`，否则 hybrid 启动会拒绝替换。

## 2. 准备本地模型配置

复制本地配置模板：

```bash
test -f singlebox/.env.local || cp singlebox/.env.example singlebox/.env.local
```

编辑 `singlebox/.env.local`，填写真实模型配置。下面的值只是格式示例，
不要把真实密钥提交到 Git 或发送到聊天中：

```dotenv
SINGLEBOX_MODEL_CONFIG_MODE=manual
OPENCLAW_ENABLE_THINKING=false

OPENCLAW_OPENAI_PROVIDER_ID=openai-compatible
OPENCLAW_OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
通过安全的运行时环境注入 OPENCLAW_OPENAI_API_KEY。
OPENCLAW_OPENAI_MODEL_ID=your-openclaw-model
OPENCLAW_OPENAI_MODEL_NAME=your-openclaw-model
OPENCLAW_OPENAI_MODEL_API=openai-completions

ANTHROPIC_BASE_URL=https://your-anthropic-compatible-endpoint
通过安全的运行时环境注入 ANTHROPIC_AUTH_TOKEN。
ANTHROPIC_MODEL=your-claude-model
```

OpenClaw 主持人和四个 OpenClaw 玩家使用 `OPENCLAW_OPENAI_*` 配置；
Claude 玩家使用 `ANTHROPIC_*` 配置。`ANTHROPIC_BASE_URL` 必须支持
Anthropic Messages API，不能仅因为某个地址支持 OpenAI API 就直接复用它。

BCN 负责 Agent、会话、任务和消息的协议传输与关联，不负责把 Claude
引擎输出转换成游戏规则。Claude Relay 需要自己实现 Anthropic-compatible
Messages API、会话继续、取消信号和最终结果回传；玩家 Prompt/上游任务负责
约束发言和投票格式。worker 只能使用直接父节点任务产物，不能依赖群广播
补齐当前任务上下文。

### 关于 Mock 模式

```dotenv
SINGLEBOX_MODEL_CONFIG_MODE=mock
SINGLEBOX_MOCK_MODEL_PORT=18080
```

Mock 会启动本地固定回复服务，适合检查 OpenClaw Bot、BCS 和连接链路。
它不提供 Claude Code 的 Anthropic 模型回复，因此不能验收完整的 Claude
hybrid 对局。要验证 Claude 玩家，必须切回 `manual` 并提供可用的
Anthropic-compatible endpoint。

## 3. 编译并启动 BCS 和前端

```bash
./singlebox/singlebox.sh setup bcs_frontend
./singlebox/singlebox.sh start bcs_frontend
```

确认服务正常：

```bash
./singlebox/singlebox.sh status bcs_frontend
curl --fail http://127.0.0.1:21000/health
```

## 4. 启动 Claude hybrid 牌桌


```bash
./singlebox/singlebox.sh start hybrid \
  --profile-dir singlebox/agents/6bots_undercover_game_profile \
  --exclusive-profile-dir player-luoji \
  --claude-profile-dir singlebox/agents/6bots_undercover_game_profile_for_claude
```

检查 Bot 和 relay：

```bash
./singlebox/singlebox.sh status hybrid
curl --fail http://127.0.0.1:18900/health
```

启动日志应同时出现：

- 主持人和其余 5 个 Bot 已连接 BCS；
- Claude relay 监听 `ws://127.0.0.1:18900`；
- Claude relay 加载 `player-luoji` 的 Claude prompt；
- 日志中没有 `ANTHROPIC_MODEL`、`ANTHROPIC_BASE_URL` 或认证错误。

停止服务：

```bash
./singlebox/singlebox.sh stop hybrid
```

## 5. 创建并开始一局

1. 打开 <http://127.0.0.1:8000/>，切换到人类视角。
2. 创建任务协作群，群主选择“谁是卧底主持人”。
3. 成员选择主持人和五名玩家。
4. 协作目标填写：

   ```text
   谁是卧底 · 6人局（5 Bot + 我）· 卧底1名 · 词语难度中等
   ```

5. 在当前会话底部点击“用户协作”→“加入当前会话”。
6. 等待主持人开场后发送 `开始`。
7. 轮到 Claude 玩家时，不要依赖群广播；它只能依据直接父节点任务产物
   作答。发言不超过 25 字，投票只输出 `我投N号` 或允许时的 `我弃权`。

如果副屏没有更新，可以点击“告诉主持人卡住了”，然后查看日志；不要
直接重复创建协作运行。

## 6. 排错

查看主持人和 Claude relay 日志：

```bash
grep -nE "model-fetch|ETIMEDOUT|chat.send|chat.inject|failed|error" \
  singlebox/.dependencies/logs/undercover-referee.log

grep -nE "system-prompt|chat.inject|frame:req|error|failed" \
  singlebox/.dependencies/claude-relays/player-luoji/logs/relay-*.log
```

常见原因：

| 现象 | 处理 |
| --- | --- |
| `ANTHROPIC_BASE_URL` 缺失 | 在 `singlebox/.env.local` 设置 Anthropic-compatible 地址 |
| `ANTHROPIC_MODEL` 缺失 | 设置 Claude 模型 ID |
| 主持人 `ETIMEDOUT` | 先确认 OpenClaw endpoint、模型 ID 和网络 |
| relay 已启动但玩家无回复 | 检查 Anthropic endpoint、密钥权限和 relay 日志 |
| bridge missing | 确认 `singlebox/apps/bcs/bcs_baas_provider_bridge.mjs` 存在 |
| 页面显示旧局卡住 | 停止服务并新建一个会话，不要复用失败运行 |

## 7. 测试与验收（Testing & Validation）

本节用于验证 Claude Code Relay 能否作为独立 Agent 接入 Avernet Hybrid 模式，并与 OpenClaw Agent 共同完成“谁是卧底”游戏。

本节只记录 Issue 要求的四项验收：Hybrid 接入并完成对局、三局日志及统计、
`chat.abort` 与迟到任务证据，以及可复现的接入教程。

### 7.1 环境与配置验证

使用 `env-local` 模式启动 Claude Hybrid：

```bash
SINGLEBOX_MODEL_CONFIG_MODE=manual \
HYBRID_CLAUDE_CONFIG_MODE=env-local \
./singlebox/singlebox.sh start hybrid \
  --profile-dir singlebox/agents/6bots_undercover_game_profile \
  --exclusive-profile-dir player-luoji \
  --claude-profile-dir singlebox/agents/6bots_undercover_game_profile_for_claude
```

验证以下配置要求：

- [x] Claude Relay 能读取 `singlebox/.env.local` 中的模型配置。
- [x] Claude Relay 最终使用的模型与本地配置一致。
- [x] 无需修改项目根目录 `.env.local` 即可完成部署。

上述配置隔离已通过本地自动化测试；三局真实对局证据见 `evidence/`。

### 7.2 自动化测试

在仓库根目录执行：

```bash
bash singlebox/verity/test_claude_profile_generic.sh
bash singlebox/verity/test_hybrid.sh
```

测试内容包括：

- Claude Profile 的通用配置解析与校验。
- Hybrid 模式下的 Profile 替换机制。
- `--exclusive-profile-dir player-luoji` 对应的替换约束。
- Hybrid 启动配置的基本有效性。

验收标准：测试脚本正常退出，且不存在未预期的测试失败。

### 7.3 端到端游戏测试

使用真实模型 API 启动 Claude Hybrid，验证 Claude Agent 能否参与完整的“谁是卧底”游戏。

| 验收项目 | 验收标准 |
| --- | --- |
| Agent 接入 | `player-luoji` 成功通过 Claude Relay 加入 Hybrid |
| 角色协作 | Claude 玩家与 OpenClaw 主持人及其他玩家正常交互 |
| 游戏发言 | Claude 玩家能够在对应回合提交发言 |
| 投票行为 | Claude 玩家能够提交主持人可解析的投票或明确弃权 |
| 游戏完成 | 游戏能够正常推进并结束 |
| 模型调用 | 无模型配置错误、认证失败或导致游戏中断的 API 错误 |

#### 实际对局记录

| 局次 | 完整结束 | 发言与投票 | 超过 25 字 | 泄露词语 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 第 1 局 | 通过 | 通过 | 否 | 未观察到 | [evidence/game-01.log](./evidence/game-01.log) |
| 第 2 局 | 通过 | 通过 | 否 | 未观察到 | [evidence/game-02.log](./evidence/game-02.log) |
| 第 3 局 | 通过 | 通过 | 否 | 未观察到 | [evidence/game-03.log](./evidence/game-03.log) |

三局完整消息、投票统计和证据边界见 [evidence/ACCEPTANCE.md](./evidence/ACCEPTANCE.md)。
三局均包含正常人类玩家参与；故障性人工干预为 0（未卡住、未手动修复、
未重启主持人）。

### 7.4 四项 Issue 验收

`chat.abort` 与 Late Result 已通过 Claude Code Gateway E2E 回归测试（2 passing，127ms）。

测试覆盖取消运行任务、取消后迟到文本的隔离，以及同一 Session 中后续 Run 的正常完成。

测试使用 Fake Runner 和 CLI Bridge（`useSdkBridge: false`），不代表已经覆盖真实 Claude SDK 的全部取消竞态。

执行命令：

```bash
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

具体证据边界见 [evidence/ACCEPTANCE.md](./evidence/ACCEPTANCE.md)。

### 7.5 验收结论

当前本地验证结果：

- Claude Hybrid 已成功使用本地配置的 DeepSeek 模型。
- Claude Relay 能够作为 `player-luoji` 接入 Hybrid。
- 已完成三局真实模型参与的“谁是卧底”游戏。
- 三局消息已脱敏保存，Claude 投票格式和主持人实际开票接收均为 6/6（100% PASS）。
- 正常人类玩家参与属于游戏流程，不计为故障性人工干预；三局故障性人工干预为 0。
- 发言均未超过 25 字，未观察到泄词。
- `chat.abort` 与 Late Result 回归测试已执行并通过（2 passing，127ms）。

**当前结论：Hybrid 接入、三局真实对局、日志统计、Gateway 取消/迟到结果证据和教程
四项验收已完成。**

提交 PR 时应同时纳入 `evidence/` 下的脱敏日志和验收说明。

注意：日志中不得包含 API Key、Token 或其他敏感信息。
