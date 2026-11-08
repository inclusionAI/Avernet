# RFC：多人多 Agent 版谁是卧底的上下文隔离

> 关联：issue #2609（队伍：壹贰）。威胁模型见同目录 `threat-model.md`。
> 基线：`oss-camp2026` @ `86252c1e9`。状态：草案，待维护者评审。

## 1. 问题

现有谁是卧底 profile 只支持 1 名人类 + 5 个 Bot。它的隔离建立在一个前提上：主从协作群里 worker 收不到群广播，所以聊天时间线就是"主持人 ↔ 人类"的私密频道，主持人可以直接在群里把词念给人类。

人类一多，这个前提就不成立了：

1. 群聊时间线对所有人类可见，"在群里念词"就是公开念词。
2. 主持人普通激活的 tool args、tool result 和 thinking 按 FullOnly 投递，默认 Full 视图的人类全都看得到。**实测**：第二个人类在会话历史里读到了主持人的 `tool_result` 原文。现行 `init` 的输出里带着 `human_word`。
3. HumanInput 节点不能绑定具体的人。任何在场人类都能看到、也能提交 pending 节点；前端 HumanInput 也不允许两个同时等待。
4. 副屏靠公开参数 `currentViewerActorId` 判断"我是谁"，这个参数谁都能读、也谁都一样。

目标是支持 2~4 名人类 + 若干 Bot（总人数 4~8，卧底数可配置），让每个人类正常流程中只看到自己的词；参与者副屏在计票完成前不请求、不展示他人票面（直接调接口和 Full 视图的既有读取路径保留，见已知限制）。不做观战、断线重连、超过 8 人。

## 2. 维护者已确认的边界

- 前端 HumanInput 复用 assignee 绑定人类，绑定不同人类的节点可以并行；pending 按认证身份过滤，提交时校验绑定；不新增请求参数。
- 本阶段不引入 `sealed_output`，节点详情等接口不加产出可见性校验。票面由前端在 `tally` 完成后才展示。直接调接口读票是已知限制。
- 保留默认 Full 视图。
- 发词可以用 Group Context。

## 3. 方案总览

```
            开局名单 ≥2 名 Present 人类 → 多人模式（冻结）
                              │
   init（脚本子进程）──────────┼──────────────────────────────┐
   抽词 / 排座 / 抽卧底         │                              │
   每个人类建 GC 条目 w_<随机>   │                              │
   先 owner-only 全建好，        │                              │
   再逐条授权给本人              ▼                              ▼
   命令只输出公开字段     发言图：人类节点 assignee=本人     副屏：认证 /me → actor
                          投票图：每人一个绑定节点，并行      按 wordRefs 取本人词条
                          vote_start 无存活 Bot 时用出局 Bot  tally 前不请求他人票
```

改动分三块，各自独立：

| 块 | 位置 | 是否需维护者另行确认 |
| --- | --- | --- |
| HumanInput 绑定人类 + 不同人类并行 | BCS `bcs-collaboration-runtime`，单独 PR | 方向已确认，实现单独评审 |
| GC 只读入口挂到 `/api/v1/collaboration` | BCS `bcs-api-http`，单独 commit | **需确认**（开放问题 Q2） |
| referee 多人模式、副屏身份/取词/计票门控 | profile + panel | 在 issue 允许范围内 |

## 4. 设计

### 4.1 BCS：HumanInput 绑定人类（单独 PR）

- `definition.rs`：前端 HumanInput 允许 `assignee: {type: runtime_actor, actor: <human actor id>}`。空 actor、`bot_binding` 拒绝；不写 assignee 时行为不变（单人类局继续用未绑定节点）。
- 并发校验：两个可能同时等待的 HumanInput，只有**都绑定了 runtime_actor 且 actor 不同**才放行；同一个人、或任一方未绑定，维持原来的拒绝。
- 运行时：绑定节点只出现在对应 actor 的 pending 列表里；提交者必须是 assignee；普通聊天只会被路由进发送者本人的节点或未绑定节点。
- 不新增请求参数，身份全部来自现有认证。

### 4.2 模式与名单

- 开局时会话里 ≥2 名 Present 人类即进入多人模式，写入状态并冻结，之后不随存活人数切换。
- 单人类局走原路径，`tests/fixtures/flow_baseline.json` 逐字不变。
- 校验：人类 1~4、总人数 4~8、Bot ≥1、卧底 `1 ≤ U ≤ ⌊(N−1)/2⌋`。
- 胜负：卧底全部出局平民胜；存活卧底数 ≥ 存活平民数卧底胜；到轮数上限卧底胜。
- 现行 referee 遇到 2 名人类会 fail-closed（`ROSTER_MISMATCH`），所以这一步是"放开"而不是"堵漏"。

### 4.3 发词：Group Context，先建后授权

1. `init` 在脚本子进程内完成全部发词，模型不经手。
2. 每个人类一条 session 级条目，名字 `w_` + 16 位随机 hex，彼此没有共同前缀。
3. 先以 `visible_to=[主持人]` 把所有条目建好并逐条回读校验；全部成功后再逐条改成 `visible_to=[本人]` 并再次回读校验（内容、`visible_to`、`owners`）。最终 `visible_to` 不含主持人：已实测条目 owner 不在 `visible_to` 中仍可读，回读校验因此可行。任何一步失败都进入 `DEAL_FAILED` 并中止本局，只输出稳定错误码。
   条目内容为 `{"kind":"undercover.word","version":1,"actorId":…,"seatNumber":…,"word":…}`，副屏据此再核对一次条目确实写给自己。
4. 授权完成后，才把"actor → 条目名"写进公开副屏参数 `wordRefs`。名字在创建前不可预测，公开时条目已存在，抢注不可能；内容受 `visible_to` 保护，名字公开不泄密。
5. 人类节点的 instruction 和私有 UI 上下文里不再出现词；`my-word` 在多人模式下返回 `MULTI_HUMAN_WORD_ON_PANEL`。

两个实测事实决定了这样写：

- `visible_to.userIds` 为空表示**所有人可见**，而 bcs-cli `--visible-to` 的默认值就是空。所以每一次写都显式给出 visible_to，测试逐条断言。
- 注入给 Bot 的 `<GroupContext>` 系统消息只有群信息、技能、任务三节，不含 GC 条目，所以条目不会经这条消息外泄。

### 4.4 主持人的秘密出口

原则：秘密不进入模型能看到的调用参数和输出。输出守卫只是检测手段，不是边界。

- 多人模式的命令输出只含公开字段（座位、人数、阶段、错误码）。
- 错误路径一律输出稳定错误码，外部命令（bcs-cli）的原始输出只落盘。
- 遗言：**恢复基线流程** `mask --text '<原话>'`，不算隔离修复。原话会作为 tool args 持久化进主持人的 Full 历史，比回执本身（已知限制 L3）多出一份可长期读取的副本；这条二次外发限制保留（T12）。第 3 局（`replays/game-3-4h3b.md`）实测了 Bot 的非终局遗言：链路走通，另一名人类在自己的 Full 历史中能读到 `mask` 调用参数里的遗言原文；人类出局按设计不进遗言。曾实现 `eulogy` 命令在子进程内取回执，但真实对局中取不到：普通会话历史接口排除 `run_reply`，CLI 也没有读取任务回执的命令。该命令保留为实验性、未接入，待开放问题 Q7。

### 4.5 编排

- 发言：仍是串行的传递闭包图，人类节点加 assignee。
- 投票：每个存活人类一个绑定节点，彼此并行；Bot 节点不变；`tally` 汇总。
- `vote_start` 入口：现行代码只从存活 Bot 里选，只剩人类存活时会抛 `StopIteration`（实测复现）。改为优先存活 Bot，没有就用已出局但仍在会话中的 Bot，并把它排除出计票、候选、进度统计和 `nodeActorMap`。开局要求至少 1 个 Bot。不使用主持人自派（会自锁）。
- 运行代际：**部分实现**。脚本登记每次提交的真实 run_id，调用方给出真实 run_id 时不匹配返回 `STALE_RUN`。但节点指令里的 `{{bcs.run_id}}` 不会被 BCS 展开、节点提示也不带 run_id，主持人拿不到真实值，因此 collect/tally 不带 run_id。多人局改为强制 round/stage/attempt（缺一返回 `SCOPE_REQUIRED`），且本阶段必须已登记 run（否则 `STALE_RUN`）。用 `--retry` 重开后，旧 attempt 的迟到提交会被拒；在 BCS 侧直接 rerun 同一快照得到的新 run 与已登记 run 范围相同，脚本无法区分。rerun 旧快照（威胁 T8）只得到部分防护，待开放问题 Q6。

### 4.6 副屏

- **身份**：多人模式下不再读公开参数。先调经 principal 校验的 `/openapi/v1/collaboration/me`，映射为 `human_<user_id>`；宿主没有这条路由（404 或非 JSON）时，回退到 BCS 根路由的 `/me`。401/403 不回退。结果再与公开座位表交叉校验，不在座位表里就只能旁观。UI 身份只决定显示什么，授权仍由服务端完成。
- **取词**：按 `wordRefs[本人]` 调 GC `get`。必须恰好一条、名字完全一致、内容是 `kind=undercover.word`、`version=1`、`actorId` 等于本人、词为非空单行，否则报错；不回退到任何公开值。参数变化或卸载时取消在途请求。
- **pending**：只处理映射到本人的节点。BCS 已经按身份过滤，副屏再过滤一次，防止服务端未升级时误把别人的节点当成自己的。
- **计票门控**：投票阶段在 `voteTallyNodeId` 完成前，不请求、不展示他人投票节点详情（详情响应里带原始票面）。这是展示层门控，不是访问控制。
- 打包体积：UMD 预算 256000 字节，本改动后 255980 字节，余量仅 20 字节。后续任何副屏改动都可能需要调整预算，请维护者知悉。

### 4.7 副屏访问 GC 的通路（D2）

**实测**：同一人类身份请求 GC `get`——

| 请求 | 状态 |
| --- | --- |
| BCS 根路由 `/groupcontext/get` | 200 |
| `/api/v1/collaboration/groupcontext/get`（BCS 直连或经网关） | 404 |
| 对照：网关 `/api/v1/collaboration/definitions/validate` | 400（说明命名空间确实转到了 BCS） |

旧前端把 `/bcnproxy` 改写到 BCS 根路由，所以能用；nextgen 宿主把 `/bcnproxy` 改写到 `/api/v1/collaboration`，副屏够不到 GC。可选方案：

- **(a)** 在 `bcs-api-http` 的 `/api/v1/collaboration` 下挂只读 `groupcontext/get`，复用现有 caller 解析与 application service，不新增权限语义。本队已在单独 commit 中实现。与根路由的一处差异：条目存在但不可读时返回 `200 + items:[]` 而不是 403，消除了新入口上的存在性探测（威胁 T13）；根路由行为不变。
- **(b)** 网关增加 `/groupcontext/*` 转发。

## 5. 兼容性

- 单人类：发言/投票图、副屏参数、`my-word`、群聊发词全部不变；`flow_baseline.json` 逐字不变。
- BCS：不写 assignee 的 HumanInput 行为不变；新增能力只在 YAML 显式绑定时生效。
- 副屏：没有 `wordRefs` 时走旧逻辑（`currentViewerActorId`）；没有 `voteTallyNodeId` 时不做门控。

## 6. 测试

按"主体 × 阶段 × 信道"定义允许集合：本人的词合法；同阵营同词不算泄露；终局公开后放行。词用金丝雀，票面用独立标记。

| 层 | 内容 |
| --- | --- |
| L0 referee 单测 | 多人 init 的边界；GC 调用顺序与 visible_to；按信道断言：命令 stdout/stderr、人类节点 instruction 与私有 UI 上下文、副屏参数、run input 不含任何人类的词（Bot 节点 instruction 按设计含该 Bot 自己的词，不在断言范围）；变异注入必须让泄露断言失败；`vote_start` 只剩人类时可渲染；给出的旧 run_id 被拒；多人局用实际下发的指令验证范围参数必填、旧 attempt 与未登记 run 被拒；偷看脚本与代打脚本自身的反例测试（pending 同 id、嵌入/转义 JSON、扫描预算、视图校验；代打的归属、409、无进展判定）；BCS 直接 rerun 同范围被接受这一已知限制由单测固定；单人类 baseline 不变 |
| L1 BCS 单测 | 绑定/并发校验矩阵；pending 过滤；越权提交；聊天路由 |
| L2 panel | 两个身份分别挂载，记录全部请求与响应；`tally` 前不请求他人投票详情；认证失败不取词；门控变异验证 |
| L3 singlebox | 验收计划：第二个人类身份拉 Full 历史搜金丝雀（`peek_history.py --expect-view full --require-role tool_result`），"2 人类 + 3 Bot""3 人类 + 3 Bot"各一局。已完成 3 局，复盘见 `replays/`：2H3B（第 1 局，过程中有热修复和人工恢复，第 1 轮即终局）、3H3B（第 2 局，第 1 轮即终局）、4H3B 2 卧底（第 3 局，4 轮打满，含两次 PK、人类 PK 发言、Bot 遗言、人类出局）。每局都做了 N×N GC 交叉读取，并用第二个人类身份读 Full 历史；代打脚本全程记录离线重扫，揭晓前 0 命中。WebSocket 帧未抓，单人类局的非终局遗言未实测 |

断言前先断言请求成功，避免网络错误造成假阴性；偷看脚本遇到响应结构不符直接失败，退出码区分：命中为 1，请求或结构错误为 2，扫描不完整为 3。T8 剩余行为（BCS 直接 rerun 同范围会被接受）已有行为测试固定；其余已知限制目前只在文档中列出，还没有写成 `xfail(strict=True)` 测试（待补）。

## 7. 已知限制

见威胁模型 §5（L1~L8）。其中 L1 票面、L2 原始发言、L3 遗言激活是现有平台行为，本 RFC 不声称已隔离。

## 8. 开放问题

- **Q1** 单人类局保持未绑定节点（当前选择，baseline 不变），还是统一绑定后重录 baseline？后者更安全：开局后才加入的人不能替唯一的人类提交。
- **Q2** GC 通路选 §4.7 的 (a) 还是 (b)？
- **Q3** task 事件对 Full 视图的放行（遗言激活，L3）是否列入后续 BCS 议题？
- **Q4** 任意已认证人类凭 session_id 即可通过 `PATCH /sessions/{sid}/members/<自己>` 以 Observer + Full 加入会话，这是有意设计吗？
- **Q5** 有人加入或离开会话时，系统消息会注入主持人和所有 Bot 的会话，打断进行中的激活：主持人激活 357 秒后失败；玩家 Bot 被打断时整轮发言运行失败。多人局开局阶段很容易撞上，也可被反复制造。是否有计划让系统消息注入不打断进行中的激活？
- **Q6** 能否让状态机节点激活拿到本次真实 run_id（展开 instruction 中的 `{{bcs.run_id}}`，或在节点提示中给出）？有了它才能强制运行代际校验（T8）。
- **Q7** 能否提供 Bot 可调用、受授权的任务回执读取（按 task ID 取规范回执与终态）？有了它遗言才能在子进程内遮蔽，避免原话进 tool args。
- **Q8** 新的 GC 只读入口对"存在但不可读"返回 `200 + items:[]`，根路由返回 403。两条路由语义不一致，是否接受？若希望完全一致，可改回 403。

## 附录 A：复现

```bash
# 单测：D4（只剩人类存活时由出局 Bot 开投）、运行代际、偷看者断言
cd singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee
python3 -m pytest -q tests

# 端到端：以第二个人类身份分页读会话历史，搜索金丝雀词（singlebox 本地，BCS mock 头）
python3 -I tests/e2e/peek_history.py '<session_id>' bob out.json '<金丝雀词>' --expect-view full --require-role tool_result
```

GC 通路与可见性的复现步骤与结果见同目录 `research.md`。
