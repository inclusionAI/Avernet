# 使用 OpenAI Agents SDK 接入「话痨小满」

本示例使用 Bot WebSocket V3 接入原版《谁是卧底》。启动阵容包括原版主持人、四个 OpenClaw 玩家、一个 Python 玩家和一个真人席位。Python 玩家使用既有的「话痨小满」Bot 身份，在原协作群中可直接接收分配给这个身份的节点。

## 启动

接入从一张已经可以使用的本地牌桌开始。准备 Python 3.10 或更新版本、可用的 Responses 模型，并按 [原版对局教程](../../../../docs/undercover-game-tutorial.zh-CN.md) 启动本地 BCS 和工作台、注册并启动六个原版 Bot。保留小满的 .bcs 会话文件。从仓库根目录、沿用原牌桌的 Singlebox 模型设置执行下列命令。两组 profile 保留五个原版 Bot 的身份和端口。

```bash
profile=singlebox/agents/6bots_undercover_game_profile
./singlebox/singlebox.sh stop bots --profile-dir "$profile"
runtime=${STANDALONE_OPENCLAW_ROOT:-.standalone-openclaw}
mixed="$runtime/undercover-openai-agents"
python3 "$profile/openai-agents-player/prepare_profile.py" --output "$mixed"
./singlebox/singlebox.sh start bots --profile-dir "$mixed/before-xiaoman"
./singlebox/singlebox.sh start bots --profile-dir "$mixed/after-xiaoman"

python3 -m venv "$mixed/.venv"
. "$mixed/.venv/bin/activate"
python -m pip install -r "$profile/openai-agents-player/requirements.txt" \
  -c "$profile/openai-agents-player/constraints.txt"
```

如果牌桌启动时自定义了运行目录，请在同一终端沿用 `STANDALONE_OPENCLAW_ROOT`。依赖约束记录了 WSL 2 中用于验收的版本。如果 PyPI 下载读取超时，可在 pip 安装命令中加上 `--timeout 60 --retries 4` 重试。

给 Python 进程设置 `OPENAI_API_KEY`，并按需设置 OpenAI 兼容 Responses 服务的 `OPENAI_BASE_URL`。默认模型为 `gpt-6-luna`，可以用 `--model` 指定自己的模型 ID。

```bash
python "$profile/openai-agents-player/main.py" \
  --session-file "$runtime/profiles/undercover-player-xiaoman/.bcs/session.json"
```

会话文件记录 Bot 的身份和登录凭据，实际位置以本机 OpenClaw 配置为准。启动日志出现 `connected` 和 `protocol_version: 3` 后，在工作台按原版教程创建或进入协作群。把主持人设为 manager，五个玩家设为 worker，真人加入当前会话后按教程开局。

## 接入笔记

- `bot.connect` 协商 V3，再定时发送 `bot.status`。启动时校验恢复的 Bot 身份。出现 `already_connected` 时先确认该玩家的 OpenClaw 进程已停止。详见 [Bot 接入指南](../../../../docs/bot-integration.zh-CN.md)。
- `chat.send` 有效时立即确认 run，随后异步生成产物。优先读取 `session_context.message` 中的原始任务，缺失时读取消息正文。发言素材来自该任务的 `[Input]` 和 `[Upstream Outputs]`，`chat.inject` 只确认。模型调用采用独立上下文。
- 引擎负责校验发言长度、句数、自己的完整牌词、连续两字和拆散说全的情况。拼音、英文、谐音和轮次钝度由玩家指令约束，裁判继续做全局判定。票面按 `我投N号` 回传，校验合法候选，普通投票支持明确弃权，PK 禁止弃权。第一稿不合规时可以再生成一次。日志区分被本地拦下的草稿和实际回传结果。
- `chat.abort` 按完整会话 ID 和 run ID 取消，先锁定终态再取消模型任务。随后即使模型返回旧结果，也只记 `late_result_discarded`。同一次运行最多发出一个 final、error 或 aborted。此进程在 WebSocket 重连后保留最近 1024 个已结束 run 的会话关联。收到已记录的同会话任务时只确认原 run ID。
- 日志记录运行 ID、节点、耗时与终态，供对照裁判的逐轮记录。牌词、模型原始输出和凭据留在运行时，不写进诊断日志。

## 验证

```bash
cd singlebox/agents/6bots_undercover_game_profile/openai-agents-player
python -B -m unittest -v
```

自动测试覆盖端口保留、发言与投票规则、错误会话取消、超时和观察消息。回环 WebSocket 测试走同一个玩家启动入口，验证原始任务选择、精确取消、迟到结果抑制和重连去重。另用 Responses 测试端点跑真实 Agents SDK，验证第一稿投自己时只重试一次并提交合规票面。

升级版在本地 BCS V3 完成了发言和投票两个真实节点，产物均通过了原版裁判检查。[验证记录](VALIDATION.zh-CN.md#更新版真实-bcn-复验)附有合成任务定义和回执摘要。

三局混合实战均已完整结束。[统计和复验说明](VALIDATION.zh-CN.md)汇总了公开动作、裁判判定和接入计时。小满共发言 5 次，违规 0 次。6 张票均被原版裁判计入，其中 1 张为 PK 票。第一局和第三局发牌后只需真人席正常发言、投票。第二局遇到模型网关过载，按原版流程恢复后完成，日志保留了 3 条恢复消息。

本地测试曾遇到模型网关并发限制。完成的实战沿用同一模型和上游网关，本地测试转发器在把回复交给 Bot 前，会对明确的限流或过载响应自动重试一次。开局前还通过 BCS 会话成员表核对了本地主持人的正式 Bot ID，因为它与显示名恰好相同。

启动时要区分群组 ID 和完整会话 ID。玩家直接从 BCN 信封读取会话 ID，原版主持人的命令也需要带上冒号后的会话后缀。

### 30 分钟接入复现

2026-11-27 在 WSL 2 中按上面的 Singlebox 命令，从已运行的本地牌桌开始计时。采用新的运行目录、两份新生成的 profile 和新建的 Python 虚拟环境，沿用 BCS 中原已注册的身份。从停原版 Bot 到原版裁判函数接受新玩家的两份产物，累计 15 分 57 秒，包含首次 PyPI 下载超时和重试。独立联调使用模拟游戏输入，发言和投票通过真实 BCN 节点交付。裁判确认发言 17 字且无违规，票面「我投2号」有效。
