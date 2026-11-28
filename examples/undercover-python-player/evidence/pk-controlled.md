# 受控 BCS 原生 state machine PK 证据（含实际 CPython 玩家）

观测日期：2026-11-02；BCS：local standalone BCS 0.1.4-beta.3。

> 真实 BCS 原生 state machine 的**受控协议测试**。seat1 是实际 CPython `BcnClient`（PK 发言由 `RulesBrain` 生成，投票使用受控合法目标）；其余 4 个 Bot 是脚本化测试 Bot。不是已完成三局，也没有调用主持人本地函数或修改 BCS/主持人源码。

## 实际操作者

| 位置 | 参与者 |
| --- | --- |
| seat1 | actual CPython BcnClient + ControlledRulesBrain (RulesBrain speech, controlled legal vote) |
| seat2 | scripted test bot |
| seat3 | scripted test bot |
| seat4 | scripted test bot |
| referee | scripted test bot |

## API 流程

- `POST /groups (chat group with controlled test bots)`
- `POST /sessions/{session_id}/state-machine-runs with definition_yaml + participant_bindings`
- `GET /state-machine-runs/{run_id} until completed`

## 受控投票

| 阶段 | 1号 | 2号 | 3号 | 4号 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 常规投票 | 投2号 | 投1号 | 投1号 | 投2号 | 1号=2，2号=2，平票 |
| PK 投票 | 投2号 | 投1号 | 投2号 | 投2号 | 2号=3，1号=1，2号胜出 |

- 常规计数：`{'1': 2, '2': 2}`，平票：`True`
- PK 计数：`{'1': 1, '2': 3}`
- PK 节点：`['pk_speak_1', 'pk_speak_2', 'pk_speak_3', 'pk_vote_2', 'pk_vote_1', 'pk_vote_3', 'pk_speak_4', 'pk_vote_4', 'pk_tally']`
- 最终 output：`PK结束，2号胜出。`

## 实际 CPython 玩家的 V3 PK 证据

- bot：`bot_f1142772`（seat1）
- 收到 BCS `chat.send`：
  - `pk_speak_1` request `smnode-sm-5993b2ce-caec-4d64-a8d2-a2d64433d2b7-pk_speak_1-0`
  - `pk_vote_1` request `smnode-sm-5993b2ce-caec-4d64-a8d2-a2d64433d2b7-pk_vote_1-0`
- 发出的 V3 `chat` final：
  - `pk_speak_1` state=`final` seq=1 content=`用起来得两只手，急不得。`
  - `pk_vote_1` state=`final` seq=1 content=`我投2号`

## BCS 节点状态

| 节点 | 状态 | 产物 | 开始 | 完成 |
| --- | --- | --- | --- | --- |
| start | completed | 受控流程开始。 | 2026-11-02 01:37:45.192 | 2026-11-02 01:37:45.207 |
| vote_1 | completed | 我投2号 | 2026-11-02 01:37:45.208 | 2026-11-02 01:37:45.215 |
| vote_2 | completed | 我投1号 | 2026-11-02 01:37:45.212 | 2026-11-02 01:37:45.222 |
| vote_3 | completed | 我投1号 | 2026-11-02 01:37:45.216 | 2026-11-02 01:37:45.232 |
| vote_4 | completed | 我投2号 | 2026-11-02 01:37:45.220 | 2026-11-02 01:37:45.228 |
| tally | completed | 本轮平票，进入PK。 | 2026-11-02 01:37:45.234 | 2026-11-02 01:37:45.238 |
| pk_speak_1 | completed | 用起来得两只手，急不得。 | 2026-11-02 01:37:45.239 | 2026-11-02 01:37:45.250 |
| pk_speak_2 | completed | 继续按线索判断。 | 2026-11-02 01:37:45.243 | 2026-11-02 01:37:45.248 |
| pk_speak_3 | completed | 继续按线索判断。 | 2026-11-02 01:37:45.246 | 2026-11-02 01:37:45.255 |
| pk_vote_2 | completed | 我投1号 | 2026-11-02 01:37:45.249 | 2026-11-02 01:37:45.258 |
| pk_vote_1 | completed | 我投2号 | 2026-11-02 01:37:45.251 | 2026-11-02 01:37:45.266 |
| pk_vote_3 | completed | 我投2号 | 2026-11-02 01:37:45.256 | 2026-11-02 01:37:45.263 |
| pk_speak_4 | completed | 继续按线索判断。 | 2026-11-02 01:37:45.273 | 2026-11-02 01:37:45.277 |
| pk_vote_4 | completed | 我投2号 | 2026-11-02 01:37:45.278 | 2026-11-02 01:37:45.282 |
| pk_tally | completed | PK结束，2号胜出。 | 2026-11-02 01:37:45.283 | 2026-11-02 01:37:45.290 |

定义 SHA-256：`8afe6a86c86d1788b9924537b84b56d90610550f184f8fc7a5b19a96cb102571`；定义文件见 `pk-controlled-definition.yaml`。

## 限制

- 只有 seat1 是实际 CPython BcnClient；其余参与者是脚本化测试 Bot。
- 这是协议级受控测试，不是已完成三局里的真实主持人 PK；没有人类玩家或主持人 LLM。
- 测试输入使用合成词“测试物”；投票目标是受控合法目标。

脱敏：不含 token、本机路径、真实对局词语；测试 group/session/run ID 保留以便复现。
