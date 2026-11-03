# 接入笔记：非 OpenClaw 引擎接谁是卧底牌桌

> issue #2611 · 异构引擎接入谁是卧底牌桌
> 引擎：纯 Python BCN WebSocket 客户端（代码级，无 LLM）
> 范围：不修改主持人脚本（`undercover.py`）与 BCS 服务端

---

## 1. 一个玩家 Bot 一局里到底收到什么

BCS 用 **state_machine 主从协作**推整局。玩家是 worker，只接收两件东西：

### 1.1 收到：串行「发言」协作节点任务（`chat.send`）
以 `node_id: speak_<seat>`（如 `speak_3`）下发给每个存活玩家，串行推进。
消息正文（`chat.send.params.message.content[0].text`）是 BCS 在 `runtime.rs`
的 `build_node_prompt` 拼好的：

```
[State Machine Task]
node_id: speak_3
display_name: 3号发言

[Input]            <- run.input：全部历轮公开发言（历史轮次，绝不含词）
[Upstream Outputs] <- 只含本轮排在这个玩家前面的人的原话（直系父节点产物）
[Instruction]      <- 含：你是几号、你的词【X】、本轮钝度、字数、泄词红线
```

### 1.2 收到：并行「投票」协作节点任务（`chat.send`）
`node_id: vote_<seat>`，所有存活玩家并行投，互不可见。
`[Upstream Outputs]` 是唯一的 `vote_start` 预备节点的产物（一句话开场），
**不是**其他人的票——所以玩家投票的公开输入仍来自 `[Input]` 的历轮发言史。
载体仍是 `chat.send`。

### 1.3 偶尔收到：遗言任务
有 Bot 出局那轮，主持人派一个遗言任务（`chat.send`），玩家的回执是公开的，
不能被遮蔽——**这是唯一无法兜底泄词的面**（协议层无能为力，只能靠规则）。

### 1.4 其余时间
- `chat.inject`：静默观察（群广播/别人的话），只 ACK 不回复。
- `chat.history`：BCN 自己不存，会让引擎回本地历史；本引擎返回空。
- `chat.abort`：主持人/服务端取消某个正在处理的 run。

### 1.5 一个关键事实
**worker 收不到群广播，连 @ 也收不到。** 聊天时间线是「主持人 ↔ 人类」的
私密频道，玩家 Bot 只能通过协作节点的 `[Input]` + `[Upstream Outputs]`
获得信息。新引擎必须在这个前提下推理。

---

## 2. 玩家要回传什么（协议契约）

对 `chat.send`：
1. **先 ACK**：`{"type":"res","id":"<chat.send的id>","ok":true,"payload":{"run_id":"<自生成run_id>"}}`
   —— `run_id` 由引擎自生成，之后的 abort 靠它。
2. **再发 V3 canonical run event**（`state:"final"`，即节点产物）：

```json
{"type":"event","event":"chat","payload":{
  "runId": "<run_id>",
  "sessionId": "<bcs_session_id>",   // 回显 chat.send 里的
  "seq": 1, "ts": 1710960001000,
  "state": "final", "content": "这句话就是答案",
  "stopReason": "complete"
}, "seq": 1}
```

这条 `content` 会成为该节点的**产物/artifact**，主持人脚本
`undercover.py` 的 `votes-set` / `speeches-set` 逐字解析它。**格式必须对**。

若 ACK 之后决策回调失败，必须再发 canonical 终态 error，不能静默退出：
`{"state":"error","errorCode":"DECISION_ERROR","errorMessage":"..."}`。

对 `chat.inject`：只 ACK `{"ok":true,"payload":{}}`，绝不回 event。
对 `chat.abort`：见下文。
对 `chat.history`：返回 `{"session_key","session_id","messages":[]}`。

---

## 3. 哪些行为由【协议】保证，哪些必须【引擎自实现】

| 行为 | 谁负责 | 说明 |
| --- | --- | --- |
| 建连握手/心跳/重连 | 协议规定 + 引擎实现 | 协议只规定帧格式与未授权拒绝；具体退避要引擎写 |
| 节点任务推导（`[State Machine Task]` → 玩家该干嘛） | **引擎自实现** | 协议不解析 instruction，只负责投递 |
| worker 只能看到直系父节点产物 | 协议（`[Upstream Outputs]` 只含直接上游） | 引擎据此推理，不看群广播 |
| 发言 ≤25 字、按钝度控宽窄 | **引擎自实现**（协议不检查） | `undercover.py` `check_text` 事后判定+遮蔽，但不会阻止你说 |
| 投票只交「我投N号」 | **引擎自实现**（协议不保证格式） | 源码 `parse_ballot` 兜底解析，格式错 = 废票 |
| 泄词兜底 | `undercover.py check_text` 事后遮蔽 | 引擎端仍应预检（本实现 `check_own_word_leak`） |
| chat.abort 精确取消 | 协议规定：只取消精确匹配 `session_key+run_id` | 已终态幂等空列表；未知/跨会话报错 |
| 迟到事件屏蔽 | **引擎自实现**（协议只要求「不得提交迟到终态」） | run 被 abort 后，本地要注册 run 已终，丢弃后来的 final/error |
| 遗言泄露 | **协议保证**（回执是公开产物） | 引擎无遮蔽兜底，只能靠规则（V3 `final` 内容即产物） |
| 心跳保活 | 协议建议 60s，TTL 5 分钟 | 引擎实现 |
| V3 capabilities 校验 | 协议要求：`unified_run_events` + `canonical_session_id` 必须 true | 引擎必须check，否则拒绝连接 |

---

## 4. 踩过的坑

### 4.1 节点 prompt 的 section 切割会被正文里的 `[Input]` 带偏
`build_node_prompt` 的正文指令里**也会出现**「[Input] 含公开规则…」这类文字。
用 `content.split("[Input]")` 之类的简单切割会拿到错误的「instruction」。
正确做法：只按**段头独立成行**切（`"[Upstream Outputs]\n"` / `"[Instruction]\n"`），
忽略正文里行内的同名标记。见 `message_model.py::_section`。

### 4.2 `chat.abort` 的幂等语义
已终态 run 必须回 `{"aborted":true,"aborted_run_ids":[]}`（空列表），
不是报错；未知 run 或不属于该 session 的 run 才报协议错误。初版把「run还在
active 但因 on_task 已返回」当成「还能 abort」，导致幂等测试失败——需要
区分「已终态(完成)」和「进行中」：完成即 `_finished_runs`，abort 只作用于
真正 in-flight 的。见 `engine.py::_handle_chat_abort`。

### 4.3 迟到事件
`chat.abort` 到达后，on_task 里的 `asyncio.sleep` 会被 `cancel()`，
但**CanceledError 之后的残余代码**仍可能试图 `_emit_chat_final`。必须在
`_process_node` 里 abort 标记后再发事件，或让 `_emit` 检查 `_finished_runs`
后才能上报。本实现两处都做。

### 4.4 V3 强制要求的字段
- `chat.send`/`inject` 都必须带非空 `bcs_session_id`（与 `session_key` 同一
  canonical 值）。缺了要按 V3 拒绝，且**不要**用 `bcs_group_id` 重建 session。
- 引擎回 `chat` final 事件时**不能**再带 `bcsGroupId` / `bcs_group_id` 等
  替代字段，否则 V3 解析会拒绝。
- 握手只协商一次，重启要重新 `bot.connect`；旧连接未清理时新连接会被拒
  （`already_connected`），须保留 token 重试。

### 4.5 投票只能靠 `[Input]` 历史，`[Upstream Outputs]` 是预备确认
投票节点里直接上游是 `vote_start` 预备，不是别人的票。若像「常规推理」那样
想从 Upstream 读票面，会拿到一句话开场白，导致「没有候选→弃权」。正确：
公开线索全部从 `[Input]`（历轮发言）取。

真实 `[Input]` 不是 `[speak_N]` 文本，而是 BCS `build_node_prompt` 序列化后的
`run.input` JSON，其中 `history` 来自 `undercover.py::public_history()`：

```json
{"history": [{"round": 1, "speeches": [
  {"seat": 1, "player": "1号 阿和", "text": "要通电"}
]}]}
```

必须按对象读取 `speeches[].seat/text`；把 JSON 当字符串用正则会一条都读不到，
投票就退化成「我弃权」（PK 还会被主持人直接拒绝）。本实现
`agent.py::_history_speeches` 先解 JSON，再保留旧文本格式兼容。
投票候选还要按 `run.input.alive` 和 instruction 的 `可以投：…` 过滤，
避免把已出局玩家的历史发言当成可投目标。

### 4.6 不改造主持人脚本，那就得让格式恰好被 `parse_ballot` 认
`undercover.py` 的票号正则只认 `投[非数字]{0,4}(\d+|中文数字)`，且整串只有
唯一数字才算。**「我投4号」是安全的**；但「投四号」「请投4号」这类其实
`投四号` 会匹配到（正则允许非数字间隔），而「4号」不含「投」匹配不到——
所以最稳的就是写死「我投N号」或「我弃权」，不做任何自由表达。

### 4.7 本机没有 `python3` 只有 `python`
单测用 `python -m unittest` 而不是 `python3`，注意 Windows 环境差异。

### 4.8 补位绑定的是 session 里的 worker UUID，不是显示名
`undercover.py::session_roster()` 从当前 session 的
`participants[].bot_uuid` 取成员，`render_vote_yaml()` 也用这些 UUID 绑定
`vote_<seat>` / `speak_<seat>` 节点。`bots.json` 的 `name` 和 on-board
显示名都不参与路由。停掉旧玩家进程不会把它从 session 移除，所以：

- 复用原玩家身份：停掉旧进程后，用它的持久化 token 启动本引擎，
  `bot.connect` 返回同一个 `bot_uuid`，座位自然保留。
- 新增 Python 玩家：先握手拿到本引擎的 `bot_uuid`，再用它构造/加入 session。
- **不要**只把显示名改成一致就期望接管座位——任务仍会派给旧 UUID。

### 4.9 决策回调失败要回传 `state:"error"`
`chat.send` 已 ACK 之后，如果决策回调抛异常却不发终态，BCS 只能等节点超时。
`engine.py::_process_node` 现在捕获普通异常并发送 canonical
`{"state":"error","errorCode":"DECISION_ERROR","errorMessage":...}`；
abort 的 `CancelledError` 仍只做取消、不发错误。

---

## 5. 验收状态说明

- ✅ **新玩家投票 100% 可解析或明确弃权**：`format_rules` 只输出
  「我投N号」/「我弃权」，单测锁定与 `undercover.py parse_ballot` 语义一致。
- ✅ **chat.abort 与迟到任务有单测**：`tests/test_engine.py` 覆盖精确取消、
  迟到 final 屏蔽、幂等、未知 run、会话归属。
- ⚠️ **完整真实对局（默认配置一局、3 局投票统计）未在本环境执行**：
  需要起全栈 BCS + 前端 + 6 个 OpenClaw Bot + 真实模型鉴权，本机不具备。
  本实现提供「把 `make_speech/make_vote` 换成 LLM 决策器即可上真桌」的
  接缝；协议与格式层已单测验证。Or 可直接用本纯规则引擎+真实 BCS 跑
  （它不调模型，能连上就会回复），只是「像不像人」由钝化句库兜底。
- ✅ **30 分钟内可接入**：见 `README.md`「如何接入本地牌桌」，本质是
  `pip install -r requirements.txt && python -m undercover_player`。

## 6. 后续建议（社区同学可续做）

1. 把 `UndercoverPlayer.make_speech/make_vote` 换成 LLM（OpenAI-compatible）
   决策器：先 `parse_node_task` 得结构化任务，再让模型生成一句话，最后过
   `check_own_word_leak` + `validate_speech/vote` 兜底。协议层零改动。
2. 持久化 token / 历史，做一次完整对局的真机验收，把 3 局投票统计写进 PR。
3. 若想走 LangGraph / AutoGen：直接复用本 `engine.py` 作为 transport，
   决策交给 graph / agent 节点即可。