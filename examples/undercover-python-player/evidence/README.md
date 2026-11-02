# #2611 混合牌桌公开脱敏证据

本目录包含 2026-11-01 三局真实混合牌桌的**可公开提交**导出，以及 2026-11-02
五局补充复盘；不是单元测试、fake BCS 或模拟输入。原有三局来自同一协作群的不同会话，
主持人均已 `reveal` 并 `FINISHED`。补充五局中四局无人工解卡完成，一局经人工恢复后完成。

## 文件

| 文件 | 内容 |
| --- | --- |
| [three-games.json](three-games.json) | 机读证据：完整会话 ID、逐轮老陈发言/投票、有效性、模型源、违规统计、任务耗时 |
| [three-games.md](three-games.md) | 同一数据的人类可读速览 |
| [five-more-games.md](five-more-games.md) | 后续五局脱敏复盘：四局无人工解卡，一局离线后人工恢复；逐局区分验收用途 |
| [timeline.json](timeline.json) | 三局按阶段排序的公开时间线（开局、逐轮发言、汇总、投票、计票、终局、首局主持人自动重试）及一致性校验；明确是阶段顺序而非严格时序 |
| [timeline.md](timeline.md) | 时间线的人类可读版 |
| [player-events.jsonl](player-events.jsonl) | 三局相关玩家事件脱敏流（task_received / final_sent / task_completed） |
| [reconnect.json](reconnect.json) / [reconnect.md](reconnect.md) | 真实本机 BCS 上的主动断线重连证据（连接层，不是游戏内恢复） |
| [pk-controlled.json](pk-controlled.json) / [pk-controlled.md](pk-controlled.md) | 受控 BCS 原生 state machine 平票→PK 测试证据；seat1 为实际 CPython BcnClient，其余测试 Bot 脚本化（协议级，不是已完成三局） |
| [pk-controlled-definition.yaml](pk-controlled-definition.yaml) | 上述受控 PK 的可复现测试定义 |
| [abort-boundary.json](abort-boundary.json) / [abort-boundary.md](abort-boundary.md) | 真实 BCS 取消路径边界：cancel 不交付 chat.abort、迟到 final 实测；非成功 abort |
| [abort-confirmed.json](abort-confirmed.json) / [abort-confirmed.md](abort-confirmed.md) | 独立 BCS 修复分支 + 实际 CPython 玩家：精确 chat.abort、取消后无迟到 final |

## 来源

- 协作群：`bcs_grp_93c843085d6d4c0ab2fae3765e112763`
- 三个会话：
  - `bcs_grp_93c843085d6d4c0ab2fae3765e112763:cf21c17e`
  - `bcs_grp_93c843085d6d4c0ab2fae3765e112763:11491aca`
  - `bcs_grp_93c843085d6d4c0ab2fae3765e112763:eaf8480f`
- 本机玩家事件：`~/.avernet/undercover-python-player/logs/` 下的 JSONL（未提交原始文件）；
- 主持人终局状态：`Avernet/.standalone-openclaw/profiles/undercover-referee/undercover-game/`
  下按完整会话 ID 命名的 JSON（未提交原始文件）；
- 两个来源交叉核对：老陈每条发言/投票的文本长度、`violation`、投票目标与玩家日志中的
  任务 kind/round、`source=llm`、`degraded=false` 一致。

## 结论（可核对）

- 3/3 局完成并 reveal；
- 老陈 4 次发言、4 张投票，全部 `source=llm`、`degraded=false`；
- 老陈发言/投票违规 0；4 张票均解析为有效目标（无弃权）；
- 其他玩家记录 4 条“超过 10 字”，全部来自人类副屏结构化投票 JSON 字符串长度，
  票本身仍被计票；不是老陈违规，也不是无效票；
- 另有 1 次 `vote_start` 规则任务（chars=23），不属于实际投票，不计入模型票统计。

## 脱敏范围

**不包含**：

- API 密钥、Bearer token、Bot 会话 token；
- 全部玩家词语、其他玩家身份/词语、原始结构化投票 JSON；
- 原始主持人状态文件、完整聊天记录；
- 未完成对局的任何秘密（本证据只包含 3 个 `FINISHED` 对局）。

**保留**：

- 完整 group/session ID；
- 轮次、老陈发言原文与投票原文；
- 每次发言/投票的有效性与 `violation`；
- `model_source` / `degraded` / 派生的任务耗时；
- 其他玩家违规的座位、类型、计数（不含原话）。

老陈的 `role` 只在对局终局 reveal 后公开；本目录不包含词对或其他玩家身份。

## 限制（如实说明）

- 第一局主持人在发言汇总节点有一次参数形状错误后自动重试；三局均没有人类发送
  “卡住了”解卡。该异常与纯 Python 玩家无关。
- 三局真实对局未触发 PK、断线、`chat.abort`；另有受控真实 BCS 协议级 PK、连接层重连
  和修复后精确 abort 证据，不能据此声称三局内发生过这些路径。
- 一位未参与开发的同学反馈：按中文接入说明在自己的本地牌桌独立接入，耗时在 30 分钟内；
  接入加后续游玩共 30 多分钟，过程没有遇到问题。这是使用者口述反馈，未提供精确计时或环境记录。
- 旧玩家日志未记录 `duration_ms` 字段，证据中的耗时由同一 run 的 `task_received` 与
  `task_completed` 时间戳推导，JSON 中标记为 `duration_source: derived_from_jsonl_timestamps`。
- 原始日志/状态文件留在本机，不随仓库提交；本目录是人工筛选后的公开导出。

## 真实 BCS 断线重连

`reconnect.json` 记录在真实本机 BCS 上创建临时测试 Bot、连接成功后主动关闭 WebSocket、
再用持久化 token 以同一 bot 身份重连的事件；不含 token/路径，bot_uuid 仅保留哈希。
它只证明连接层重连，不等同于游戏节点中途断线的完整恢复。

## 受控 BCS 原生 PK

`pk-controlled.json` 是在真实本机 BCS 上用 5 个受控测试 Bot 提交原生 state machine
定义的协议级测试：4 票形成 2:2 平票，真实 BCS 执行 `pk_speak_*`、`pk_vote_*`、`pk_tally`
节点并 completed。seat1 是实际 CPython `BcnClient`（`RulesBrain` 生成 PK 发言，受控合法目标
投票），并记录了它收到的 V3 `chat.send` 与发出的 `chat` final；其余 4 个 Bot 仍是脚本化测试 Bot。
它不是已完成三局里的主持人 PK，也没有调用主持人本地函数。

## 真实取消路径边界

`abort-boundary.*` 记录实际 CPython BcnClient 的真实 state-machine cancel 证据：run 变为 aborted，但 WS Bot
没有收到 `chat.abort`，gate 节点仍显示 running，释放后客户端发送 final（BCS 侧 run 仍 aborted）；
`POST /channels/bindings` 返回 `channel bridge is disabled`。这是架构边界证明，不是成功的 abort。

## 修复后真实取消

`abort-confirmed.*` 记录独立 BCS 修复分支上的实际 `BcnClient`：取消接口 200，Bot 收到
与原任务相同 `run_id`/`session_key` 的 `chat.abort`，记录取消，放开 gate 后无迟到 final。
该证据使用隔离临时服务；服务端修复仍需单独合并/部署。

## 时间线一致性

`timeline.json` 的 `consistency.checks` 由真实终局状态逐项重算，全部为 `true`：

- 每轮发言数、投票数、票数字段求和与状态一致；
- 最高票出局者与状态 `eliminated` 一致；
- 终局 winner/round 与状态 `result` 一致；
- 老陈逐轮发言/投票与 `three-games.json` 一致；
- 阶段顺序满足：开局 → 轮开始 → 全部发言 → 汇总 → 投票开始 → 全部投票 → 计票 → 终局；
- 第一局恰好有 1 条主持人自动重试记录；并行投票的时间允许交错，按阶段而非逐票时间排序。

## 校验

```bash
cd examples/undercover-python-player
python3 -m json.tool evidence/three-games.json > /dev/null && echo evidence-json-ok
python3 -m json.tool evidence/timeline.json > /dev/null && echo timeline-json-ok
python3 -m json.tool evidence/reconnect.json > /dev/null && echo reconnect-json-ok
python3 -m json.tool evidence/pk-controlled.json > /dev/null && echo pk-controlled-json-ok
python3 -m json.tool evidence/abort-boundary.json > /dev/null && echo abort-boundary-json-ok
python3 -m json.tool evidence/abort-confirmed.json > /dev/null && echo abort-confirmed-json-ok
wc -l evidence/player-events.jsonl
```

`three-games.json` 的 `summary` 可直接核对：

```json
{
  "games": 3,
  "finished_games": 3,
  "player_speeches": 4,
  "player_votes": 4,
  "player_violations": 0,
  "other_player_violations": 4,
  "model_source_counts": {"llm": 8}
}
```
