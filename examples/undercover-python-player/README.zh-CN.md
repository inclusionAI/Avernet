# 纯 Python 谁是卧底玩家（BCN V3）

用纯 Python 实现的非 OpenClaw 玩家，直连 Avernet 的 Bot 协作网络（BCN）WebSocket，
在“谁是卧底”混合牌桌里替代 `玩家稳健老陈`。它复用老陈的人格提示词，按主持人下发的
节点指令发言/投票/留遗言，不修改主持人脚本，也不修改 BCS 服务端。

- 目标 Issue：`inclusionAI/Avernet#2611`
- 目标分支：`oss-camp2026`
- 协议：BCN Bot WebSocket V3（`/ws/bot`）
- 依赖：Python 3.12+、`websockets>=15,<17`（由 `uv` 管理）
- 已通过离线测试：fake BCS 握手/任务/abort/过期结果/无密钥兜底 + 真实主持人解析器差分；
- 已完成三局真实混合牌桌对局（同一协作群、三个独立会话）：老陈共 4 次发言、4 张有效票，
  8 个实际任务均为模型生成且未降级；主持人记录老陈违规 0。另有 1 次 `vote_start`
  规则任务不计票。公开脱敏证据见 [evidence/](evidence/README.md)。

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
- 重复 `chat.send` 只 ACK，不重复生成；`HistoryStore` 与 session 记忆有会话数/TTL 上限，
  活跃 run 的会话不会被清理，跨会话词不串。
- 游戏 state machine 节点无法识别时发送明确的 `state=error`，不伪造成功 final；
  已识别节点若无法生成合法答案，也回传错误终态，避免主持人静默等到超时；
  普通聊天无法识别时只回一句保守拒绝。
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

# 在临时 profile 中排除原 OpenClaw 老陈；不改仓库原文件
PROFILE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/avernet-undercover-XXXXXX")
cp -R singlebox/agents/6bots_undercover_game_profile/. "$PROFILE_DIR/"
jq '.bots |= map(select(.source != "player-laochen"))' "$PROFILE_DIR/bots.json" > "$PROFILE_DIR/bots.filtered.json"
mv "$PROFILE_DIR/bots.filtered.json" "$PROFILE_DIR/bots.json"
test "$(jq '.bots | length' "$PROFILE_DIR/bots.json")" = 5
# 确保 30801-30841 端口空闲；若已有同一牌桌在运行，先按 2.5 停止
./singlebox/singlebox.sh check bots --profile-dir "$PROFILE_DIR"
./singlebox/singlebox.sh start bots --profile-dir "$PROFILE_DIR"

./singlebox/singlebox.sh status bcs_frontend
./singlebox/singlebox.sh status bots --profile-dir "$PROFILE_DIR"
```

> **不要使用 `BOTS_EXCLUDED_PROFILE_SOURCE`**：当前 `singlebox.sh` 会在 `start/check bots`
> 路径把它当成 Claude hybrid 参数，报 `--exclusive-profile-dir and --claude-profile-dir
> must be provided together`。本机已验证临时 profile 方式：5 个 OpenClaw Bot 以 30801-30841
> 运行，`status bots` 能识别，原 `singlebox/agents/6bots_undercover_game_profile/` 未被修改。
> 保留 `PROFILE_DIR` 变量到本次终端会话结束；停机时必须使用同一个目录。

### 2.3 启动纯 Python 玩家

```bash
cd examples/undercover-python-player
# 本机 Python 3.12 默认 CA 对 DeepSeek 服务会 CERTIFICATE_VERIFY_FAILED：
SSL_CERT_FILE=/etc/ssl/cert.pem ./run.sh
# 若你的机器默认 CA 正常，可直接 ./run.sh；不要关闭证书校验。
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

先回到仓库根目录，再停止临时 profile 的 5 个 Bot（`PROFILE_DIR` 必须是本次启动用的同一路径）：

```bash
# 在 examples/undercover-python-player 里 Ctrl-C 停止纯 Python 玩家
cd /path/to/Avernet
./singlebox/singlebox.sh stop bots --profile-dir "$PROFILE_DIR"
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

当前 70 项离线测试，覆盖：

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

## 6. 三局验收结果与公开证据

2026-11-01 在本机同一协作群 `bcs_grp_93c843085d6d4c0ab2fae3765e112763` 完成三局，
三份主持人状态均为 `FINISHED` 且已 `reveal`：

| 局 | 会话 ID | 老陈座位/身份 | 轮次 | 结果 |
| --- | --- | --- | ---: | --- |
| 1 | `bcs_grp_93c843085d6d4c0ab2fae3765e112763:cf21c17e` | 1 号 / 平民 | 1 | 平民胜；3 号卧底 4:2 出局 |
| 2 | `bcs_grp_93c843085d6d4c0ab2fae3765e112763:11491aca` | 4 号 / 卧底 | 1 | 平民胜；老陈首轮最高票出局 |
| 3 | `bcs_grp_93c843085d6d4c0ab2fae3765e112763:eaf8480f` | 6 号 / 平民 | 2 | 平民胜；第 2 轮卧底出局 |

老陈合计 4 次发言、4 张有效票，8 个实际任务都是 `source=llm`、`degraded=false`；
主持人记录老陈违规 0。其他玩家有 4 条“超过 10 字”记录，来自人类副屏结构化投票 JSON
字符串长度，票本身仍有效并计入——不是老陈违规，也不应写成无效票。第一局主持人在
发言汇总节点有一次参数形状错误后自动重试成功；三局均没有人类发送“卡住了”解卡。

公开脱敏证据（可提交）：

- [evidence/README.md](evidence/README.md)：来源、脱敏范围、限制；
- [evidence/three-games.json](evidence/three-games.json)：完整会话 ID、逐轮老陈发言/投票、
  有效性、模型源、违规统计、派生的任务耗时；
- [evidence/timeline.json](evidence/timeline.json) / [timeline.md](evidence/timeline.md)：
  阶段顺序（非严格时序）时间线，含一致性校验；
- [evidence/reconnect.json](evidence/reconnect.json) / [reconnect.md](evidence/reconnect.md)：
  真实 BCS 连接层断线重连；
- [evidence/player-events.jsonl](evidence/player-events.jsonl)：三局相关玩家事件脱敏流。

完整原始主持人状态含全员词语/身份，不提交；公开证据只保留终局后可公开字段。

## 7. 排障

| 现象 | 处理 |
| --- | --- |
| 启动后一直重连 | 确认 BCS `/health`、端口 21000、`UNDERCOVER_BCS_URL` |
| `already_connected` | 同一 bot 还有旧连接；等旧连接清理或重启旧进程 |
| 群里找不到玩家 | 确认 onboard 成功、`visibility=public`、名字是 `玩家稳健老陈` |
| 主持人说节点超时 | 看日志是否 `brain_timeout`/`degraded`；模型是否可用 |
| 开局后本玩家不动 | 确认用 2.2 的临时 profile；不要用 `BOTS_EXCLUDED_PROFILE_SOURCE`（会触发 hybrid 校验） |
| 规则兜底句很机械 | 正常；真实对局请配置模型 |

## 8. 当前限制（如实说明）

- 三局真实对局已完成（证据见 [evidence/](evidence/README.md)）；老陈 4 次发言、4 张有效票
  全部为模型生成；老陈违规 0。其他玩家的 4 条“超过 10 字”是人类结构化票长度误报，
  票本身有效，已在 evidence 中单独记录。
- 第一局主持人在发言汇总节点有一次自动重试；三局均未要求人类发送“卡住了”。
- 一位未参与开发的同学反馈：按本说明在自己的本地牌桌独立接入，接入耗时在 30 分钟内；
  接入加后续游玩共 30 多分钟，过程没有遇到问题。这是使用者口述反馈，未提供精确计时或环境记录。
  首次 `cargo build`/`npm install`/OpenClaw 安装不计入接入计时。
- 真实 BCS 连接层断线重连已保存证据（`evidence/reconnect.*`）；游戏内断线恢复未验证。
- 真实 BCS 原生 state machine 的受控 PK 测试已保存证据（`evidence/pk-controlled.*`）：
  seat1 是实际 CPython `BcnClient`，真实收到 V3 `chat.send` 并发出 PK 发言/投票 final；
  其余 4 个 Bot 是脚本化测试 Bot。它不是已完成三局里的主持人 PK。
- 修复前的 BCS state-machine cancel 未向 Bot 下发 `chat.abort`（`evidence/abort-boundary.*`）。
  独立 BCS 分支 `codex/bcs-state-machine-cancel` 修复后，在隔离服务上用实际 CPython
  `BcnClient` 验证了精确 `chat.abort` 与迟到 final 抑制（`evidence/abort-confirmed.*`）。
  该服务端修复须单独合并/部署；三局真实牌桌本身没有触发取消。
- 不支持 `chat.history` 的完整对话回放，只返回本玩家最近的助手输出。
- 只替换人类牌桌中的 `玩家稳健老陈`；不支持同时替换多个玩家。
- 常规投票遇到模型失败时明确弃权；PK 不允许弃权，仍会在合法候选中确定性选择。
  日志的 `source=rules`/`degraded=true` 用于区分兜底和模型结果。
