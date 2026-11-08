# 实测记录

RFC 与威胁模型引用的实测事实。环境：singlebox 本地（`BCS_AUTH_MOCK=1`、`allow_mock_headers=true`），`openclaw@2026.6.1`。人类身份用 BCS 本地 mock 头 `X-Mock-User-Id` 构造：`001` → `human_001`，`bob` → `human_bob`。

## R1 主持人工具结果对其他人类可见

- 会话中 A=`human_001`（玩家），B=`human_bob`（Observer，默认 `message_view_scope=full`）。
- 以 B 身份分页读取 `GET /sessions/{sid}/messages`，结果里有主持人的 `role=tool_result` 消息，内容是主持人读取 SKILL.md、references 以及 `bcs-cli session get` 的原文。
- 来源：`bcs-message-flow/src/bot_event/persistence.rs` 的 `incoming_event_visibility`，非状态机节点的非 chat 事件为 `FullOnly`。
- 推论：任何进入命令输出的秘密，对 Full 视图的所有人类公开；历史持久化，后加入的人也能翻到。
- 现行 referee 遇到 2 名人类时 `init` 返回 `ROSTER_MISMATCH`，不会发牌。
- **词级确认**：bob 移出后 A 开局（`init` 成功），第 1 轮开始后 bob 重新加入并分页读历史，金丝雀词命中 2 条：`role=tool_result` 的 `init` 输出（`"human_word": "<词>"`，FullOnly）和 `role=assistant` 的群聊消息「你是 4 号，你的词是「<词>」」（Public）。晚到者无需任何特权即可读到。

## R2 GC 可见性

同一会话，A 建条目 `visible_to={"userIds":["human_001"]}`：

| 操作 | A | B |
| --- | --- | --- |
| `POST /groupcontext/get` | 200，返回内容 | 403 `permission_denied` |
| `POST /groupcontext/list` | 返回该条目 | `{"contexts":[]}` |

- 不存在的条目 `get` 返回 `200 {"items":[]}`。
- owner 不在 `visible_to` 中仍可读：以 A 身份建条目 `visible_to=[B]`，A `get` 返回内容与完整 ACL（`owners=[A]`），B 只拿到内容、不含 ACL 字段。这保证主持人授权后仍能回读校验。（本条与上表是当时的手工探测记录，原始请求响应没有留存；源码依据为 GC 服务对 owner 的放行逻辑，正式提交前需重跑留证。）
- `GroupContextPolicy`：`userIds` 为空表示所有人可见；bcs-cli `--visible-to` 默认 `{"userIds":[]}`。
- `bcs-system-message/src/producers/session_context.rs` 生成的 `<GroupContext>` 只含群信息、技能、任务三节。

## R3 GC 在协作命名空间下不可达

同一人类身份，body `{"name":"w_probe","scope_level":"session"}`：

| 请求 | 状态 |
| --- | --- |
| BCS `:21000/groupcontext/get` | 200 |
| BCS `:21000/api/v1/collaboration/groupcontext/get` | 404 |
| 网关 `:8889/api/v1/collaboration/groupcontext/get` | 404 |
| 网关 `:8889/groupcontext/get` | 404 `no route for path` |
| 网关 `:8889/api/v1/collaboration/definitions/validate`（对照） | 400 |

链路：panel 默认 `baseUrl='/bcnproxy'`；nextgen `config.local.ts` 把 `/bcnproxy` 改写为 `/api/v1/collaboration` 并转给网关；网关 `collaboration-internal` 原样转给 BCS；BCS 在该前缀下没有 GC 路由。旧前端把 `/bcnproxy` 改写到 BCS 根路由，所以旧前端可用。

## R4 `vote_start` 无存活 Bot

6 座，1/2/3 号人类存活、4/5/6 号 Bot 全部出局（1 卧底 vs 2 平民，对局未结束）。`render_vote_yaml` 在 `next(seat for seat in living if seat["kind"] == "bot")` 处抛 `StopIteration`。只有多人类局可能走到这里。

## R5 会话自助加入

`PATCH /sessions/{sid}/members/human_bob {"mode":"present"}`（以 bob 身份，bob 不是群成员，`POST /sessions/{sid}/members` 返回 404）→ 200，bob 以 `role=observer`、`mode=present`、`message_view_scope=full` 进入会话。时间线出现"用户 human_bob 已加入协作群"。代码：`bcs-http/src/routes/sessions.rs` 中 `participant_not_found && human.actor_id == bot_uuid` 的自插入分支。

## R6 系统消息打断主持人激活

时间线：22:39:47 会话 GroupContext 叫醒主持人开始 S0；22:40:48 bob 加入，系统向主持人 `chat.inject` 一条加入消息；22:45:45 主持人激活失败：`EmbeddedAttemptSessionTakeoverError: session file changed while embedded prompt lock was released`，耗时 357 秒。profile README 记录过同类现象（任务回执灌入）。

## R7 环境

`openclaw@2026.9.2` 下 BCN 插件报 `rt.config.loadConfig is not a function`，主持人每次回复都失败。改用 CI 钉的 `openclaw@2026.6.1`（`.github/workflows/singlebox-coverage.yml`）后正常。插件 `peerDependencies` 只声明 `>=2026.3.28`，没有上限。

另一个独立问题也会打断玩家 Bot：对局第 1 轮发言时人类重新加入会话，系统消息注入到所有 Bot 会话，正在生成 `speak_1` 的 Bot 激活以 `EmbeddedAttemptSessionTakeoverError` 失败（106 秒），节点失败导致整个发言运行失败。这与上面的插件兼容错误无关。

## R8 GC 只读入口（本 RFC 的 BCS 改动）实测

集成分支上重建 BCS 后，经网关 `POST /api/v1/collaboration/groupcontext/get`：

| 调用者 | 条目 | 结果 |
| --- | --- | --- |
| 条目 owner | 自己建的条目 | 200，返回内容与 ACL |
| `visible_to` 内的人类 | 授权给他的条目 | 200，只返回内容 |
| 不在 `visible_to` 内的人类 | 他人条目 | 200 `{"items":[]}`（根路由仍为 403） |
| 未认证 | — | 401 |

上表同样是手工探测记录，原始请求响应没有留存；mock 路由测试覆盖了这三种情况，但不能替代经 nextgen → 网关 → BCS 的认证链实测。

## R9 多人对局实测（2 人类 + 3 Bot，集成分支）

- 真实 `init`：`mode=multi`，两名人类各得一条随机名词条；主持人回读校验通过。
- 经网关新入口交叉读取：本人读到 `actorId` 为本人的条目，读他人条目得空列表。
- 以第二名人类身份分页读会话历史（含 8 条主持人 `tool_result`，其中有多人 `init` 输出）：平民词与卧底词均 0 命中。对照：旧代码下晚到者能读到 2 处。

## R10 节点指令中的 run_id 模板不会展开

多人局第 1 轮 collect：主持人把节点指令里的 `--run-id '{{bcs.run_id}}'` 原样交给 `speeches-set`，两次 `STALE_RUN`，collect 节点 600 秒超时，整轮运行失败。BCS 只在开场消息和副屏参数里展开 `{{bcs.*}}`，状态机节点 instruction 直接使用原文，节点提示里也没有 run_id。修复后 `--run-id` 改为可选（见 RFC §4.5、开放问题 Q6）。

## R11 Full 与 Participant 视图对比（多人局 1 结束后）

以第二名人类身份读取同一会话的完整历史：

| 视图 | 消息数 | 主持人 `tool_result` | 另一名人类的独立原始投票消息 | Bot 的独立原始投票消息（「我投 N 号」） |
| --- | --- | --- | --- | --- |
| Full（默认） | 46 | 14 | 1 | 3 |
| Participant | 18 | 0 | 0 | 0 |

两份快照不是同一时刻：Full 取于终局揭晓消息之前，Participant 取于之后，后者多出终局 `state-machine-result`。表中票面列只统计独立的原始投票消息；终局公开计票表不计入。

- 投票进行中（计票前），Full 视图的会话历史与宿主聊天时间线已能看到 Bot 票和另一名人类的票面。
- Participant 视图由服务端在历史接口上投影。本次在局后对 bob 做的 Participant 历史查询中，没有返回主持人工具输出，也没有他人的独立原始投票消息（只剩 bob 自己那条）；但终局公开计票表（`state-machine-result`）仍列出每个人的票面，这是设计上的公开结果。计票前的实时消息、视图切换时机和节点详情等接口须分别验证，目前不能据此断言"计票前保密在 Participant 下成立"。节点详情等直接接口仍见已知限制 L1。
- 终局揭晓前的 Full 历史中，平民词、卧底词 0 命中。终局揭晓消息（`state-machine-result`）会在群聊复述双方的词和身份，这是设计上的公开答案例外；局后 Participant 历史里两个词各命中 1 次，正是这条消息。
