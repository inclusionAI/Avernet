# 谁是卧底 · 非 OpenClaw 玩家 Bot（纯 Python BCN WebSocket 引擎）

Avernet issue **#2611**：让不同引擎的 agent 坐上同一张「谁是卧底」牌桌。

本目录提供一个**代码级引擎**玩家：不依赖 OpenClaw / Claude Code / Agent 框架，
直接用 Python 写 BCN（Bot Coordination Network）WebSocket 客户端接入 BCS，
复用 OpenClaw 人格「玩家逻辑控林工」，把 `6bots_undercover_game_profile` 里
写在 OpenClaw skill 的玩家规则移植到纯逻辑决策器，保证发言/投票格式能被主持人
脚本 `undercover.py` 精确解析。

## 为什么选代码级引擎（而非 Claude Code hybrid）

- `singlebox.sh` 的 `hybrid` 模式把 `--claude-profile-dir` **硬编码**给
  `platform-data / platform-data-analyst` 单个 Bot（见
  `singlebox/modules/claude_profile.sh` 的校验：要求 `source==platform-data`、
  恰好 1 个 Bot、relay_port 18900，CLAUDE.md 重名检测），并且必须走
  BAAS/Backend/relay 全栈，无法直接复用游戏 profile 的 6 个玩家。
- 本机 Claude Code 2.1.299 虽已安装但 hybrid 需要真实模型鉴权与完整服务栈；
  在受限环境里最稳的是先把协议层、规则层、abort/迟到处理写成**可单测的纯代码**，
  这正是 issue 验收要求的「chat.abort 和迟到任务有单元测试或证据」。
- 因此本实现是完全可复现、可单测的**代码级接入样例**，社区同学可在此基础上
  替换成 LangGraph / AutoGen / 任意 LLM 引擎。

## 目录结构

```
undercover-python-player/
├── requirements.txt                 # 仅 websockets
├── README.md                        # 本文件
├── INTEGRATION-NOTE.zh-CN.md        # ★ 接入笔记（协议保证 vs 引擎自实现）
├── undercover_player/
│   ├── __init__.py
│   ├── __main__.py                  # python -m undercover_player
│   ├── engine.py                    # ★ BCN 协议引擎（握手/心跳/chat.send/abort/history）
│   ├── message_model.py             # ★ 节点任务解析（[State Machine Task] 结构）
│   ├── format_rules.py              # ★ 玩家规则（发言≤25字、泄词检测、投票格式）
│   └── agent.py                     # ★ 人格「林工」+ 决策器（怎么发言、投谁）
└── tests/
    ├── fixtures.py                  # 与 undercover.py 一致的节点 prompt 构造
    ├── test_message_model.py
    ├── test_format_rules.py
    ├── test_engine.py               # chat.abort / 迟到屏蔽 / 幂等 / 握手
    └── test_agent.py                # 发言≤25/不泄词、PK禁弃权、预备播报
```

## 启动

```bash
# 依赖
pip install -r requirements.txt

# 连接到本机 BCS（默认 ws://127.0.0.1:21000/ws/bot，端口可用 BCS_PORT 覆盖）
python -m undercover_player --name "玩家逻辑控林工"

# 首次连接会拿到 token；掉线重连传入 token 复现身份
python -m undercover_player --token <PERSISTED-TOKEN>
```

连接协议（BCN V3）：
1. `bot.connect`（`protocol_version=3`, `client_kind=undercover-python-player`）
2. 读 `payload.token / bot_uuid / capabilities`，确认
   `unified_run_events & canonical_session_id` 均为 true，否则拒绝连接
3. 后台 60s 一次 `bot.status` 心跳
4. 断线指数退避重连（1s→30s）；旧连接仍活跃时服务端会拒绝新 `bot.connect`，
   按协议保留 token 等旧连接清理后再重试

## 测试

```bash
# 无网络的内存级单测（起假 BCS，不连真实服务）
cd singlebox/agents/undercover-python-player
python -m unittest discover -s tests -v
```

覆盖：
- `message_model`：把 BCS 下发的 `chat.send` content 解析成结构化节点任务
- `format_rules`：投票 `我投N号`/`我弃权`、发言泄词（整词/连续两字/拆字）判定
- `engine`：握手直接读取 connect 响应（不在启动读循环前等 ACK）、
  `chat.send` ACK+final 回传、决策回调异常回传 terminal error、
  `chat.inject` 静默、**`chat.abort` 精确取消并屏蔽迟到 final、已终态幂等空列表、
  未知 run 报错**、`chat.history`
- `agent`：发言≤25字不泄词、PK 禁止弃权、投票预备原样播报、
  解码 referee JSON `history[].speeches` 并按存活/可投名单过滤投票目标

## 如何接入本地牌桌

1. 正常起 BCS + 前端 + OpenClaw 的 6 Bot（主持人 + 5 玩家）。
2. 玩家座位由**当前 session 的 `participants[].bot_uuid`** 决定，不是 on-board
   `display_name`，也不读 `bots.json`：`undercover.py::session_roster()` 从
   session 成员里取 UUID，`render_vote_yaml()` 用这些 UUID 绑定投票/发言节点。
   因此只把新 Bot 显示名改成和原玩家一样**不会**转移座位；如果旧玩家还留在
   session 里、只是进程被停掉，任务仍会派给那个已停止的 worker。
3. 补位请二选一：
   - **复用原玩家身份（推荐）**：先停掉原 OpenClaw 玩家，再用它持久化的
     `--token` 启动本引擎；`bot.connect` 会复用同一个 `bot_uuid`，session
     成员无需改动。
     ```bash
     python -m undercover_player --token <原玩家的持久化 token>
     ```
   - **新增 Python 玩家为 session 成员**：先启动本引擎拿到 `bot_uuid`
     （握手日志 `bot connected: uuid=...`），再**先把这个身份入网**，最后用
     这个 UUID 构造/加入牌局 session，然后开一局。
     ```bash
     # bot.connect 只分配 uuid/token；未入网的身份 capability.name 为空，
     # 不在 list_bots 作用域里，前端/牌桌选不到它。
     curl -X POST http://127.0.0.1:21000/admin/bots/onboard \
       -H 'Content-Type: application/json' \
       -d '{"bot_id":"<bot connected: uuid=...>","name":"玩家逻辑控林工"}'
     ```
     入网响应为 `{"bot_uuid":"...","onboarded":true,"name":"玩家逻辑控林工"}`；
     之后用同一个 UUID 加入 session。引擎在 `is_new` 握手时会直接打出这条
     `curl` 提示（`bot ... is not onboarded yet`），照抄即可。

4. 完整的一局手动验证按 `docs/undercover-game-tutorial.zh-CN.md` 走。

## 与 OpenClaw 玩家的人格对齐

本引擎把 `player-luoji/`（逻辑控林工）的规则从 skill 里搬成了可执行逻辑：

| OpenClaw skill/文件 | 本引擎实现 |
| --- | --- |
| `skills/undercover-game-player/SKILL.md` | `agent.py::decide` + `format_rules.py` |
| `RULES.md`（泄密红线、格式、不空回复） | `format_rules.check_own_word_leak`、`validate_speech` |
| `SOUL.md` / `IDENTITY.md`（人格） | `agent.py::PERSONA`（属性推理、平实、不下定义） |
| `OUTPUT.md`（发言≤25、投票只交票号） | `format_rules` 常量与 `validate_vote` |

关键点：**规则只写一处**。发言 ≤25 字、钝度阶梯、投票只交「我投N号」、
泄词兜底全部在 `format_rules.py` 里集中定义，并由同一套单测锁定，
避免 OpenClaw 那版「四份副本各写一遍」的坑。

## 已知限制 / 踩坑

详见 [INTEGRATION-NOTE.zh-CN.md](INTEGRATION-NOTE.zh-CN.md)。
核心：本引擎是**纯规则决策器**，不调真实模型，所以「说得像不像人」由预设
钝化句库兜底；真实对局体验需要接一个 LLM 决策器（在 `make_speech` /
`make_vote` 里换成 model 调用即可，协议与格式层无需改动）。