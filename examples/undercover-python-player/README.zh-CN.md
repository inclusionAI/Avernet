# 纯 Python 谁是卧底玩家（BCN V3）

用纯 Python 实现的非 OpenClaw 玩家，直连 Avernet 的 Bot 协作网络（BCN）WebSocket，
在“谁是卧底”混合牌桌里替代 `玩家稳健老陈`。它复用老陈的人格提示词，按主持人下发的
节点指令发言/投票/留遗言，不修改主持人脚本，也不修改 BCS 服务端。

- 目标 Issue：`inclusionAI/Avernet#2611`
- 目标分支：`oss-camp2026`
- 协议：BCN Bot WebSocket V3（`/ws/bot`）
- 依赖：Python 3.12+、`websockets>=15,<17`（由 `uv` 管理）
- 已通过离线测试：fake BCS 握手/任务/abort/过期结果/无密钥兜底 + 真实主持人解析器差分；
- **尚未运行真实三局**：本机缺 Rust/protoc/OpenClaw、模型凭证和人类玩家，详见文末限制。

## 1. 它做什么

```text
BCS/主持人 --chat.send(节点指令)--> 本玩家
                              ├─ 解析发言/投票/预备/遗言
                              ├─ LLM 生成（无凭证则规则兜底）
                              ├─ guard 校验字数/泄词/票面
                              └─ chat final 事件回传节点产物
```

- `chat.send`：立即 ACK（`run_id` 用请求 id），解析 `[State Machine Task]` 或遗言任务，
  在节点超时前回传 `state=final` 的 V3 `chat` 事件。
- `chat.inject`：只 ACK，不产生 final。
- `chat.abort`：只取消精确 `run_id` 并校验 `session_key`；先置 aborted、取消生成、
  等待生成任务真正退出并排空 final 发送锁，确认后才响应；若生成器不退出则返回
  `RUN_NOT_ABORTABLE`（retryable），不谎称取消。确认在后台完成，接收循环不被慢生成阻塞；
  取消后该 run 的迟到输出（包括 `to_thread` 里继续跑的 HTTP 结果）不会再发 final。
- 节点超时（`timeout_ms`，玩家节点默认 420s）后结果直接丢弃，不污染下一节点。
- 重复 `chat.send` 只 ACK，不重复生成。
- 心跳：60s `bot.status`；断线指数退避重连；持久化 token 复用身份。
- 首次连接自动 HTTP onboard（名称/能力）并设 `visibility=public`。

## 2. 30 分钟接入自己的本地牌桌

> 计时起点：Avernet 依赖已装好、BCS/OpenClaw 能启动、模型配置已就绪、游戏 profile 已存在。
> 首次 `cargo build` / `npm install` / OpenClaw 安装不计入 30 分钟。

### 2.1 前置检查

```bash
cd /path/to/Avernet
test -f singlebox/agents/6bots_undercover_game_profile/bots.json && echo profile-ok
test -x apps/bcs/target/debug/bcs && echo bcs-built
command -v openclaw && command -v jq && command -v uv
```

模型配置（三选一，真实对局必须）：

```bash
# A. 复用 singlebox 的模型配置（推荐）
set -a; source singlebox/.env.local; set +a
# B. 自己导出
export OPENCLAW_OPENAI_BASE_URL=https://your-endpoint/v1
export OPENCLAW_OPENAI_API_KEY=...
export OPENCLAW_OPENAI_MODEL_ID=your-model
# C. 没有模型凭证：只做协议联调，加 --rules-only（不能冒充真实对局）
```

### 2.2 启动 BCS + 其余 5 个 OpenClaw Bot

```bash
./singlebox/singlebox.sh start bcs_frontend
curl --fail http://127.0.0.1:21000/health

# 只起主持人 + 其余 4 个玩家；被替换的老陈不再启动
BOTS_EXCLUDED_PROFILE_SOURCE=player-laochen \
  ./singlebox/singlebox.sh start bots \
  --profile-dir singlebox/agents/6bots_undercover_game_profile

./singlebox/singlebox.sh status bcs_frontend
./singlebox/singlebox.sh status bots --profile-dir singlebox/agents/6bots_undercover_game_profile
```

> `BOTS_EXCLUDED_PROFILE_SOURCE` 是 singlebox 内部过滤开关（`singlebox/modules/bots.sh`
> 的 `bots_dynamic_specs/count` 使用，`singlebox/verity/test_hybrid.sh` 也用它）。
> 若你的版本不生效，回退方案：复制 profile 到临时目录，用 `jq` 从 `bots.json` 删掉
> `source=player-laochen` 的条目，再对临时目录执行 `start bots --profile-dir`；不要改原 profile。

### 2.3 启动纯 Python 玩家

```bash
cd examples/undercover-python-player
./run.sh
```

可用参数/环境变量：

| 名称 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `UNDERCOVER_BCS_URL` | 否 | `http://127.0.0.1:21000` | BCS HTTP 地址；WS 自动推导 |
| `UNDERCOVER_BCS_WS_URL` | 否 | 由 HTTP 推导 | 测试/特殊反代时显式覆盖 |
| `UNDERCOVER_BOT_NAME` | 否 | `玩家稳健老陈` | 必须与主持人群里选中的玩家名一致 |
| `OPENCLAW_OPENAI_*` / `UNDERCOVER_LLM_*` | 真实对局 | 无 | OpenAI-compatible endpoint/key/model |
| `UNDERCOVER_LLM_MAX_TOKENS` | 否 | `2048` | 单次生成的 token 预算；推理模型要包含 reasoning token |
| `UNDERCOVER_LLM_MAX_TOKENS_CEILING` | 否 | `8192` | 空内容/`finish_reason=length` 时的自动放大上限 |
| `UNDERCOVER_LLM_MAX_ATTEMPTS` | 否 | `3` | 空内容/截断时的最大请求次数 |
| `UNDERCOVER_RULES_ONLY=1` | 否 | 关 | 强制规则兜底（只适合协议联调） |
| `UNDERCOVER_TOKEN_FILE` | 否 | `~/.avernet/undercover-python-player/session.json` | bot token 存储（0600） |
| `UNDERCOVER_LOG_DIR` | 否 | `~/.avernet/undercover-python-player/logs` | JSONL 事件日志 |

启动成功后日志会显示 `connected`，本地 `session.json` 保存 bot_uuid/token。
看到 `onboard`/`visibility` 成功即可。

#### 模型 token 预算与 DeepSeek Flash

DeepSeek Flash 等推理模型会把 reasoning token 计入 `max_tokens`。实测用
`max_tokens=96` 时，模型可能只完成思考就耗尽预算，返回 `finish_reason=length`
且 `content` 为空，表现为 `没有内容` 并触发规则兜底。因此：

- 默认 `max_tokens=2048`；遇到空 `content` 或 `finish_reason=length` 时，本实现会
  自动把预算翻倍重试（默认上限 8192，最多 3 次）；
- 每次失败只在日志/错误里记录 `finish_reason` 与是否存在 reasoning，不记录 token 或完整响应；
- 重试仍失败才退化为规则兜底，硬约束校验仍然生效；
- 如果服务端限制更小的输出预算，可用 `UNDERCOVER_LLM_MAX_TOKENS` /
  `UNDERCOVER_LLM_MAX_TOKENS_CEILING` 调整。

#### 本机 TLS CA 配置

本机 Python 3.12 的默认 CA 链对某些模型服务会报
`CERTIFICATE_VERIFY_FAILED`。已验证设置 `SSL_CERT_FILE=/etc/ssl/cert.pem` 可正常
完成 TLS 校验；运行时使用：

```bash
SSL_CERT_FILE=/etc/ssl/cert.pem ./run.sh
```

**不要关闭证书验证**（不要设置 `PYTHONHTTPSVERIFY=0`、`ssl._create_unverified_context`
或等价开关）。该变量只影响当前进程，不要写进仓库或全局 shell 配置。

### 2.4 开一局

1. 浏览器打开 <http://127.0.0.1:8000/>，切人类视角；
2. “拉起协作”：名称 `谁是卧底`，类型 **任务协作型（主从）**，
   主持人选 `谁是卧底主持人`，成员选其余 4 个 OpenClaw 玩家 + `玩家稳健老陈`；
3. 进入会话，页面底部 **加入当前会话**；
4. 发 `开始`，按副屏提示玩；每局结束在同一个群里 **新建会话** 再开。

### 2.5 玩完清理

```bash
# Ctrl-C 停止纯 Python 玩家
./singlebox/singlebox.sh stop bots --profile-dir singlebox/agents/6bots_undercover_game_profile
./singlebox/singlebox.sh stop bcs_frontend
```

## 3. 发什么、回什么（契约摘要）

玩家节点（state machine）收到的提示形如：

```text
[State Machine Task]
node_id: speak_3
...
[Input]
{公开规则、开场、历史}
[Upstream Outputs]
[2号发言]
...
[Instruction]
你是 3 号，你的词是【...】。第 1 轮发言。
...
```

本玩家的输出：

| 节点 | 输出 | 硬约束 |
| --- | --- | --- |
| 发言 / PK 发言 | 一句中文 | ≤25 字；不含自己的词、连续两字、拆字；不提身份/轮次/规则/票数 |
| 投票预备 | `投票开始，请大家投出一票，等待主持人公布结果。` | 原样 |
| 常规投票 | `我投N号` 或 `我弃权` | ≤10 字；N 在“可以投”集合内；不能自投 |
| PK 投票 | `我投N号` | 禁止弃权；N 在 PK 名单内 |
| 遗言 | 一句点名怀疑座位的话 | ≤35 字；引用原话；不泄自己的词 |

回传帧（V3）：

```json
{"type":"event","event":"chat","payload":{
  "runId":"<chat.send request id>","sessionId":"<bcs_session_id>",
  "seq":1,"ts":1710960001000,"state":"final","content":"我投4号"
},"seq":1}
```

- ACK：`{"type":"res","id":"<request id>","ok":true,"payload":{"run_id":"<request id>"}}`
- `seq` 每个 run 从 1 递增；外层 `seq` 必须等于 payload `seq`；BCS 按连接维护
  `run_sequences`，首帧 seq 只要求递增，不要求特定起始值；
- `runId`/`sessionId` 必须精确回显，否则 BCS 会静默忽略事件，表现为节点卡住；
- run id 契约（已核对 BCS 源码）：state machine 的请求 id 就是
  `delivery_request_id`，BCS 以它建立 `bot_run_context`，并把 ACK 中不同的
  `run_id` 注册为 alias；task dispatch 的请求 id 就是 `task_id`。本实现直接让 ACK
  `run_id` 等于请求 id，因此两类任务都走 canonical id，不依赖 alias；
- 真实部署仍通过 `/ws/bot` 连接、HTTP `/bots/onboard` 注册；token 放
  `bot.connect.params.token`，不是只在测试 fakeserver 里成立。

## 4. 离线测试（不需要 BCS/模型/OpenClaw）

```bash
cd examples/undercover-python-player
uv run --with websockets --with pytest pytest -q
uv run --with ruff ruff check undercover_player tests
```

覆盖：

- fake BCS 真实 WebSocket：握手、onboard/visibility HTTP、chat.send ACK、final、inject、history；
- 发言/投票/PK/预备/遗言任务解析与输出；
- `chat.abort`：精确 run、scope mismatch、未知/已终态、取消后迟到 final 抑制；
- 过期结果丢弃；同 frame 重发只回一次；
- 无模型凭证规则兜底；
- LLM 非法输出修复、LLM 失败降级、OpenAI-compatible 请求形状；
- 与真实主持人 `undercover.py` 的 `check_text`/`parse_ballot` 做差分校验。

fake BCS 只证明本玩家对协议的处理正确，**不等于**真实三局对局。

## 5. 降级与日志

- 有模型：模型生成 → guard 校验 → 不合格带反馈重试一次 → 仍不合格用规则兜底。
- 模型返回空 `content` 或 `finish_reason=length`：自动放大 token 预算重试（默认
  2048 → 4096 → 8192）；仍失败才进入规则兜底。
- 无模型 / 模型超时 / HTTP 错误 / TLS CA 错误：规则兜底，日志 `degraded=true`，
  节点不空转；TLS 错误会提示 `SSL_CERT_FILE`，不会关闭校验。
- 规则兜底只保证协议和硬约束，不保证发言质量；正式 3 局应使用模型。
- token 存 `~/.avernet/undercover-python-player/session.json`（0600），不写入仓库。
- 日志是 JSONL，不含 token；词只记录 sha256 前 12 位，不记录原文。
- `delivery-2611/` 与对局日志不提交到上游仓库。

## 6. 3 局验收记录方法

每局保存：

1. 本玩家 `~/.avernet/undercover-python-player/logs/<session>.jsonl`；
2. 主持人局状态：`$BOT_DATA_DIR/undercover-game/<session>.json`（主持人 bot 的数据目录；
   也可在相应目录 `find . -path '*undercover-game/*.json'`）；
3. 页面聊天记录/副屏录屏。

统计口径：以主持人状态里的 `speeches[].violation`、`votes[].violation/note` 为准；
本玩家日志中的 guard 结果作为事前拦截对照。目标：3 局投票 100% 有效票或明确弃权；
发言违规为 0 或如实列出；主持人无需“卡住了”。

## 7. 排障

| 现象 | 处理 |
| --- | --- |
| 启动后一直重连 | 确认 BCS `/health`、端口 21000、`UNDERCOVER_BCS_URL` |
| `already_connected` | 同一 bot 还有旧连接；等旧连接清理或重启旧进程 |
| 群里找不到玩家 | 确认 onboard 成功、`visibility=public`、名字是 `玩家稳健老陈` |
| 主持人说节点超时 | 看日志是否 `brain_timeout`/`degraded`；模型是否可用 |
| 开局后本玩家不动 | 确认 `BOTS_EXCLUDED_PROFILE_SOURCE` 生效；若未生效按 2.2 回退方案处理 |
| 规则兜底句很机械 | 正常；真实对局请配置模型 |

## 8. 当前限制（如实说明）

- 真实 BCS/OpenClaw/模型/人类玩家三局**尚未运行**；本机缺 `cargo/protoc/openclaw` 与模型配置。
- `chat.abort`/迟到结果只有 fake BCS 单测证据，没有真实取消录屏。
- 30 分钟接入指标尚未实测；首次构建时间需排除。
- 不支持 `chat.history` 的完整对话回放，只返回本玩家最近的助手输出。
- 只替换人类牌桌中的 `玩家稳健老陈`；不支持同时替换多个玩家。
