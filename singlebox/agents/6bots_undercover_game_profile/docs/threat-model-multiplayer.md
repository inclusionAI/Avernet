# 谁是卧底 · 多人上下文隔离威胁模型与信息流

> 状态：草案（`wt-2609-multiplayer` 并行贡献）。范围限定在多人类（2～4 位 Human）+ N 个玩家 Bot + 主持人，
> 总人数 4～8 的可配置对局。本文只列**信道事实**与**现状泄露点**，不给出解决方案——设计方案见同目录
> [RFC（draft）](rfc-multiplayer-isolation.md)。
>
> 判断泄露的依据是「信道是否能让一个诚实但好奇的参与者拿到不属于他的信息」，与「系统性防窥/防代交」
> 是两个档次。本文逐格标出哪些是**信道保证**（硬隔离，任何参与者遵守平台规则即可生效），哪些只是
> **荣誉制 / UI 隐藏**（前端不展示，但调用服务端接口仍能读出来）。

## 1. 参与者模型

| 代号 | 身份 | 来源 | 能力 |
| --- | --- | --- | --- |
| `H1..Hn` | n 位 Present 人类玩家（2≤n≤4） | BCS 会话 `present` 成员 | 副屏操作、群聊收发、直接调 BCS HTTP/WS 接口 |
| `B1..Bm` | m 个玩家 Bot worker（总人数 4～8，含假想卧底） | 群成员 `worker` | 只收到分配到自己的协作节点、任务、群里的公开事件 |
| `R` | 主持人 manager Bot | 群成员 `manager` | 全是公开事件 + 自己节点 + 状态文件作者 |

> 现状（v1.7.1，单人版）：`n=1`，`m=5`，共 6 人 + 主持人。本文把 n 展开到 2～4，
> 并在「现状 vs 目标」列分别标注每一格在多人类下是否安全。

## 2. 信道清单

一个「信息」从一个参与者流向另一个参与者，只存在下述 8 条信道。每条给出：谁可以订阅/读取、
内容是什么、是否能被「不想要它的人」看到并如何绕过。

### C1 群聊广播（公开消息）

- **谁发**：任何 Bot 的激活输出、任何玩家的发言，都会以群消息形式广播给所有群成员。
- **谁能读**：所有 Ai —— **包括所有玩家 Bot**。这是本设计最重要的信道事实，也是单人版整套模型的支柱。
- **单人版现状**：worker Bot 收不到 @ 定向、但群广播文字 Bot 能看到。单人版故意让主持人**只在群里给唯一人类的词**
  ——因为「没有第二个人类」，这条广播的唯一人类收件人无罪。
- **多人类泄露点（🔴 致命）**：主持人若仍在群聊里发词，**所有其他 Bot 和其他人类都能看到**。
  发词必须离开本信道。

### C2 协作节点 instruction（HumanInput 私有指令）

- **谁能看到**：指令里的词通过节点 `instruction` 下发。
  - **待处理节点**：经 `GET /state-machine-runs/{run}/pending-human-nodes` 读取。现状（access.rs）对无 assignee 的
    前端节点**返回给任一 Present 人类**（`pending_human_node_view` 里直接放 `node.instruction`）。多人类时，A 的
    待处理词会被 B 的 `/pending-human-nodes` 一起读到。
  - **已完成节点**：经 `GET /state-machine-runs/{run}/nodes/{node_id}`（node-detail）读取，返回工件的 `artifact_text`
    与历史 `instruction`。access.rs `authorize_run_access` 只校验「调用者是 Present Human in session」，**不区分 node 归属**。
- **谁不能看**：Bot 收不到其他节点的 instruction（只拿到直接委托给自己的）。
- **多人类泄露点（🔴）**：`pending-human-nodes` 无 assignee 就向所有 Present Human 展开；`node-detail` 无按 actor 过滤。
  这正是 issue 里 `currentViewerActorId 来自公开参数不可作认证证据` 的根因。

### C3 upstream artifacts（运行输入与父节点产物）

- 运行 `--input` 与每个节点的直接父节点 artifact 会进 `[Upstream Outputs]`。单人版把运行输入限定为**公开发言史**，不含词。
- **多人类泄露点（🟡 视编排）**：投票阶段如果 B 的投票指令把 A 的前序「发言/投票」原样带上（传递闭包/上游传递），
  B 会看到 A 的私有表述。必须用「发言后转公共通告、投票只在计票节点聚合」的方式切断，而不能把 A 的原始票串给 B。

### C4 公开面板参数（panel-params JSON）

- 面板渲染所需，注入 `--panel-params`，作为运行元数据对会话内所有 Present Human 可见。
- 单人版已坚持**公开参数只含座位、存活的行动顺序、脱敏发言史、候选人**，词与身份只在 HumanInput instruction 的
  `UNDERCOVER_UI_CONTEXT_V1` 私有块（即不在此信道）。
- **多人类泄露点（🔴 若加错字段）**：一旦有任何一格的私有词/身份被放进公共参数（例如为了「省一条请求」把
  `currentViewerWord` 塞进来），任一其他玩家调用参数接口立刻读到。**这是隔离测试最容易抓住的类**（issue 验收 2）。

### C5 任务回执与遗言

- 遗言通过 `bcs_assign_task` 派给已出局 Bot，回执以 `[任务状态]` 写入时间线，**公开**。
- **多人泄露点（🟡）**：遗言正文是出局者自己的自由文本，可能带出词/身份线索；单人版已用 `mask` 遮蔽后念，但
  **原始回执仍落在群聊天记录里**（服务端不回滚）。多人类会放大——遗言成为对两名及以上 Present Human 的公开广播。
  属「现状已知唯一拦不干净的面」（README 已知限制），多人类下严格说是**公开面**，只能规则约束 + `mask` 转述，无法真隔离。

### C6 工具调用事件转发（命令输出/状态旁白）

- Bot 每次工具调用及其 stdout 被转发成群事件。单人版脚本已把含词命令（render-* / reveal / my-word）控制在「只打印路径」，
  命令输出不含词。
- **多人类泄露点（🟢 已控制）**：脚本保证不把词写进普通命令输出；改动时勿破坏该约束，并新增测试扫描所有子命令输出不含词。

### C7 状态文件（BOT_DATA_DIR 落盘）

- 裁判脚本把完整词、身份、逐座 word 存成本机 JSON（`undercover-game/*.json`），只有裁判运行进程可读。
- **多人类泄露点（🟢 现状安全）**：本文件不在 BCS 可见域，Human 只能经 C2/C4/C5 间接拿到；隔离测试不应把它当成可读面。
  但**任何把状态摘要输出到公共信道的命令**都会泄露，需回归覆盖。

### C8 终局公开结果（file upload + reveal）

- 终局 `reveal` 公布词对、全员身份（C1 广播 + 面板 GameOverDialog）。这是**唯一允许公开全员信息的面**。
- 多人类下保持不变（结束即公开），不是泄露。

## 3. 可见性矩阵

行 = 信息项，列 = 参与者。格子值：`✅` 信道保证该参与者**应看到**；`🚫` 信道保证看不到；
`⚠️` 现状该参与者**本不该看到却可能拿到**（漏洞/荣誉制）。

| 信息 / 参与者 | 主持人 R | 玩家 Bot Bx | 词的主人 Hx | 其他人类 H(y≠x) | 备注 |
| --- | --- | --- | --- | --- | --- |
| Hx 的词语 | ✅（事实层裁决者） | 🚫 | ✅ | ⚠️ **现状可经 C2/C4 拿到** | 目标：只走 C2 的 Hx 私有 instruction |
| Hx 的身份(平民/卧底) | ✅ | 🚫 | ⚠️（不告知任何人） | ⚠️ 同词 | 局中不公布，终局 C8 全员公开 |
| Hx 的发言原文 | ✅（汇总前先 check_text+mask） | ✅（仅在轮到后的公开转述） | ✅ | ✅（公开发言本来就是公共面） | 发言是共享信息，不算隐私 |
| Hx 的投票目标 | ✅（fact 层） | 🚫（只交票号，不跨节点传原始票） | 🚫（不告知自己也是隐私？——不，本人投的当然知道） | ⚠️ 若 C3 把 A 原始票转发给 B | 目标：投票期间 B 不看到 A 的票 |
| 词对 | ❌（不应提前知道对方词？——主持人 fact 层本就知道） | 🚫 | 🚫 | 🚫 | 终局 C8 公布 |
| 座位表 / 存活顺序 | ✅ | ✅（作为公开 board） | ✅ | ✅ | 公共面 |
| 已公布票面(voteHistory) | ✅ | ✅ | ✅ | ✅ | 终局/每轮揭晓后公开 |

> 🔴 红=现状多人类必爆；🟡 黄=需编排约束；🟢 绿=已控制。关键结论：
> **只有 3 格是硬问题**——C2 pending/detail 无 assignee 隔离、C3 上游传递可能带原始票、
> C4 若把私有字段塞公共参数（一票否决）。

## 4. 现状泄露点汇总（多人类下逐个触发）

1. **`list_pending_human_nodes` 对前端无 assignee 节点返回给任一 Present Human**（access.rs 172-221）。
   多人类同时行动时，A 的词通过 pending 接口泄露给 B。**Rust 服务端格**。
2. **`get_state_machine_node_run`（node-detail）不按 actor 过滤 `instruction`/`artifact_text`**（service.rs 131-142，
   views.rs 456-461）。任何 Present Human 可枚举节点、取任意已存在节点详情拿词。**Rust 服务端格**。
3. **`respond_human_node` 对前端无 assignee 节点放行任一 Present Human**（definition.rs 305-346 拒绝有 assignee 的前端节点 → 因此
   单人版只能 accept Node）。
4. **投票并行**：现行唯一可执行的路径是「多个人类节点同时进入 running」，但 human_input.rs 在
   `handle_session_human_input`（WS 会话输入）里显式拒绝多 pending（`multiple pending human_input nodes are not supported`）。
   这条拒绝是**普通 frontend HumanInput 天生「一次性收一个人」**的体现，故「投票并行」无法沿用现有契约全并行。
5. **面板 `currentViewerActorId` 来自公开参数**：前端把「我是谁」建立在不可信的公共参数上，无法做认证证据——
   服务端过滤（1/2）落地前，前端即使拿不到别人词也无法自证身份。**既是 design 也是 server 格**。

## 5. 修复优先级建议（供 RFC 引用）

- **P0（必须，他人可先合）**：隔离测试把 C2 `pending`/`node-detail` 的越权读抓出来，方法见 RFC 测试节。
- **P1（服务端）**：给前端 HumanInput 增加 `assignee.type: runtime_actor` 支持（definition.rs 305 放开当前拒绝），
  pending/detail/respond 都按 assignee 过滤。这是「身份来自认证而非公开参数」的服务端实现面。
- **P2（编排）**：多人类投票「同一阶段内 bot 并行、Human 按序 + requirement 闭环」，保证无跨人 artifact 串原始票。
- **P3（前端）**：多人类场景把 `currentViewerActorId` 降级为「高亮用」，所有权限判定交给服务端 / 以认证为准。