# 接入笔记：Node.js 原生 WebSocket 玩家

> Avernet issue #2611 · 非 OpenClaw 的第二种独立引擎样例
> 范围：只新增 `singlebox/agents/undercover-node-player/`；不改主持人与 BCS。

## 1. 样例边界

这是一个无 LLM、无 Agent 框架的 Node.js 规则玩家。真实连接只在 `src/index.js`
使用 `ws`；协议、规则和决策测试全部注入内存 socket，不依赖安装 `ws`、网络或真实 BCS。
入口读取：

- `BCS_URL`：默认 `ws://127.0.0.1:21000/ws/bot`。
- `BOT_PERSONA`：人格说明；默认是平实、克制的公开信息推理者。

启动方式为 `npm install && npm start`。首次握手得到的新身份仍需通过 BCS onboard，
再把同一个 `bot_uuid` 加入牌局 session；仅复用显示名不会接管原座位。

## 2. 一局里实际可见的信息

玩家 worker 主要收到 `chat.send` 承载的 state-machine 节点任务：

- `speak_N`：`[Input]` 是历轮公开发言，`[Upstream Outputs]` 只含本轮前序发言；
  自己的座位、词、轮次和输出约束在当前 `[Instruction]`。
- `vote_N`：直接上游是 `vote_start` 的确认文本，**不是其他玩家的票**；投票依据仍是
  `[Input]` 的结构化 `history[].speeches[]`，并用 `alive` 与「可以投」过滤目标。
- 遗言和投票预备也经 `chat.send` 下发。
- `chat.inject` 只 ACK，不产出回答；本样例不把 inject 拼成“全网可见历史”。

因此 `src/agent.js` 没有全局群聊记录，也不读取其他 Bot 的私聊。公开历史只解析本次任务
显式携带的输入与直接上游；座位和私人词按 canonical session 从此前 speech/vote 任务保留，
供不重复携带这两项私有信息的真实遗言任务复用，不跨 session 共享。不能假设 worker 能收到
主持人和人类的完整广播时间线。

## 3. 协议保证 vs 引擎自实现

| 行为 | 责任方 | 本样例处理 |
| --- | --- | --- |
| `bot.connect` 帧格式与 V3 协商 | 协议规定，引擎执行 | 显式请求 3，检查响应版本及 `unified_run_events`、`canonical_session_id` |
| token / bot_uuid / env | 协议下发 | 握手完整校验后才保存身份并注入 env |
| 心跳 | 协议建议 60 秒，引擎执行 | `bot.status`，连接后启动定时器 |
| canonical session | V3 强制 | `chat.send/inject` 要求 `bcs_session_id === session_key` 且非空；不从 group 猜建 |
| `chat.send` 回复封套 | 协议规定 | 先 ACK 自生成 `run_id`，再发 V3 canonical `chat` final |
| 上行 event seq | 协议规定 | 每个 run 从 1 递增；内外层 seq 一致，回显 canonical `sessionId` |
| `chat.inject` 静默 | 协议规定 | 仅成功 ACK，无 event |
| `chat.history` | 协议规定由引擎回答 | 本样例不持久化，返回空 `messages` |
| 任务意图与发言/投票阶段 | **引擎自实现** | 只按独立成行的 section 头解析 prompt |
| ≤25 字、逐轮收窄、泄词预检 | **引擎自实现** | `Array.from` 计字符；规则层集中校验，协议不会替玩家检查 |
| 「我投N号」/「我弃权」 | **引擎自实现** | 严格格式解析；任意垃圾输入均返回 `{valid:false, reason}` |
| 可见上下文约束 | 协作输入 + 引擎遵守 | 投票只读公开 `[Input]`，不把上游预备或其他私聊当票面 |
| `chat.abort` 精确取消 | 协议规定，引擎执行 | 精确匹配 `session_key + run_id`；活跃 run 立即标为 aborted 并触发 signal |
| abort 幂等与错误 | 协议规定 | 已终态返回空 `aborted_run_ids`；未知 run / 跨 session 返回错误 |
| 迟到 final/error 屏蔽 | **引擎自实现** | 决策结束后再次检查 run 状态；abort 优先，回调即使忽略 signal 也无法发 final |
| 过期任务 seq | **引擎自实现的防御规则** | 同 session 内 `seq <= latest` 直接错误 ACK；不建 run、不更新 latest、不调用决策器 |

### 两种 seq 不能混淆

BCN 标准 Request 并不保证总有 `seq`。本样例仅在下行 `chat.send` 显式携带正整数
`frame.seq` 时启用同 session 的单调任务排序；没有该字段就按基础协议处理，不自行造序号。
它与上行 Run Event 的 `payload.seq` 是两套序号：前者用于拒绝过期任务，后者按 run 从 1
编号。不能把二者放在同一个全局计数器里，否则并行投票和不同 session 会互相污染。

## 4. 踩坑记录

1. **握手版本和 capabilities 都要检查。** 只看 `ok:true` 会把 V2 响应当成 V3；
   在校验完成前也不能提前保存 token/uuid。
2. **ACK 后仍不能丢弃终态责任。** 决策异常会回传 `state:"error"`，包括 `onTask` 自己抛出的
   `AbortError`；只有真实 `chat.abort` 已把 run 标成非活跃时才抑制终态，不再补 error/final。
3. **仅在回调入口检查 signal 不够。** 回调可能在 abort 后才 resolve；发送终态前必须再次
   检查 run 状态。本样例不发送 delta，从设计上避免半截文本。
4. **过期任务不能“只不回复”。** 若先注册 run 再判断 seq，仍会污染 abort/history 状态；
   本实现先检查，再登记 run，并用错误 ACK 让调用方结束等待。
5. **中文长度不能用 `Buffer.byteLength` 或 JS `.length`。** 前者是 UTF-8 字节，后者是
   UTF-16 code unit；规则统一使用 `Array.from(text).length`。
6. **结构化 history 不能再跑文本归因。** 否则发言正文里的「3号：…」会被伪造为 3 号
   新发言。识别 JSON 后只读 `seat/text`。
7. **投票上游不是票箱。** 从 `[Upstream Outputs]` 找其他票会得到预备播报或伪造候选。
8. **Windows Node 24 的目录测试入口。** 本机直接执行 `node --test test/` 会把目录当模块
   路径解析；`test/index.js` 聚合三个测试文件后，该命令与 `npm test` 均可运行，不需要 glob
   或外部测试 runner。

## 5. 验证与未覆盖范围

```bash
cd singlebox/agents/undercover-node-player
node --test test/
```

本机 Node `v24.21.0`：22 项通过、0 失败，未安装 `ws` 也能执行全部测试。
覆盖握手成功/版本拒绝、心跳、canonical final、24/25/26 字边界、严格票面与垃圾输入、
abort 中取消及迟到 final 屏蔽、回调主动 `AbortError` 的 error 终态、过期 seq、公开 history、
PK 禁弃权，以及真实 `cmd_render_ping()` 遗言提示下的 session 私有上下文复用与隔离。

未在本环境启动完整 BCS + 主持人 + 多 Bot 真桌，未做真实 WebSocket 联调或三局统计。
自动重连、token 持久化和 LLM 决策也不在这个增量样例内；进程重启后需要重新接入/复用
身份。协议层与决策层已分离，后续可在不改规则与传输契约的情况下替换决策器。
