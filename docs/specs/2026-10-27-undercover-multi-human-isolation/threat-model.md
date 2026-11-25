# 多人多 Agent 谁是卧底：威胁模型与信息流

Issue: [#2609](https://github.com/inclusionAI/Avernet/issues/2609)。Target: `oss-camp2026`。
Status: 评审中（v0.7，2026-10-27）。配套 RFC：同目录 [`spec.md`](spec.md)。

> 源码基准：实测与运行时分析基于 `oss-camp2026` @ `d2a964de9`；Group Context 分析按维护者意见以 `dev` 为准（#2900 `d163eb905`，核对到 `dev` @ `d48b2885c`）。实测环境：singlebox 本地部署，OpenClaw 2026.6.1。
> 实测覆盖：单人类 + 5 Bot 共 3 局（10/16、10/17）；10/19 Group Context 接口与双人类偷看实测；10/24 投票票面与 run 视图实测；10/26～27 源码审查与原型测试。
> 文中 [E1]～[E25] 指附录中的证据编号；标"源码"的条目尚未实测。
> 本阶段的隔离边界（哪些由服务端保证、哪些只在界面层保证）以 RFC §3 为准；§5 的"本阶段处理"列与之对应。

## 1. 背景与范围

当前谁是卧底 profile 只支持 1 名人类 + 5 个 Bot（`undercover.py:1217-1218` 硬限制 `len(humans) > 1` 即 `die("ROSTER_MISMATCH")`；脚本内部用单数 `human_actor_id`/`human_seat`/`human_word`，667/691/1330/1953/1976 行）。现有隔离依赖两个前提（profile README「信道」一节）：

1. **worker（玩家 Bot）收不到群广播**，所以主持人可以在群聊里直接给人类发词；
2. **只有一名人类**，所以"群聊 / 副屏 / pending 接口对所有人类可见"等于"只对这一个人可见"。

多人类版本打破了前提 2。本文档列出对局中所有承载信息的信道，逐一分析在 N 名人类（2～4）+ M 个 Bot + 1 个主持人（总玩家 4～8）的配置下谁能看到什么，标出泄露点，作为 RFC 的输入和"偷看者"测试的覆盖清单。

官方草稿 `player-count-spec.md` 把范围限定为"不改 BCS"，因此只能做到副屏层面分开展示，并明确承认"不能提供服务端防窥或防代交保证"。本文档的目的之一，就是把这些缺口逐条定位到接口和代码，供 RFC 决定哪些需要服务端修复。

**不在范围内**：BCS 管理员、能登录宿主机或读 BCS 数据目录 / 主持人 workspace 的人；模型供应商侧的数据处理；本地开发模式的身份伪造（见 §2 注）。

## 2. 参与者与受保护信息

**参与者**

| 记号 | 参与者 | 说明 |
|---|---|---|
| H_A | 人类 A | 视角主体 |
| H_B | 其他人类 | 同一会话中的其他人类玩家，与 A 同权限 |
| W | 玩家 Bot（worker） | 每个 Bot 有独立 OpenClaw 会话，按「群 ID + 会话 ID」隔离 [E3] |
| R | 主持人 Bot（referee，manager） | 必然掌握全部信息 |
| X | 会话外的人 | 同群但未加入本会话的成员，或未登录者 |

**受保护信息（资产）**

| 记号 | 资产 | 应该对谁可见 |
|---|---|---|
| S1 | 某玩家的词 | 仅本人 + R；终局公布后对所有人 |
| S2 | 某玩家的身份（平民 / 卧底） | 仅 R；终局公布后对所有人 |
| S3 | 本轮投票内容（计票前） | 仅投票者本人 + R；计票后公开 |
| S4 | 全员词表 / 对局状态文件 | 仅 R；终局后结果可公开 |

**攻击者模型**：诚实但好奇的人类玩家 H_B。他用正常账号登录，能打开浏览器开发者工具，能以自己的身份调用前端可达的任何 BCS 接口、读取群聊和副屏收到的全部数据，但不能伪造别人的登录态。

> 注 1（身份层可信）：本地人类身份由服务端从 **HttpOnly 的 `bcs_session` cookie（JWT）**解析（`caller.rs` → `user_identity.extract` → `human_<staff_no>`）。该 cookie 前端 JavaScript 读不到也改不了，`currentViewerActorId` 等公开参数不参与鉴权 → **身份层本身是可信的，符合 issue「身份来自认证而非公开参数」的要求** [E13][E14]。本模型中的泄露点都不是"伪造身份"，而是"合法身份拿到了不该看的数据"（授权粒度问题）。
> 注 2（本地测试设施）：singlebox 本地模式的认证插件还允许通过 `X-Mock-User-Id` 请求头指定身份（`bcs-auth-local`，`bcs-config-local.toml:209` `allow_mock_headers = true`）[E12]。这是本地开发设施，生产认证链不适用，不计入泄露点；但偷看者测试和本地多人验收可以用它造出 A / B 两个**真实**人类身份（先调一次 `/bots/my` 触发 `ensure_human_actor` 自动建号，即可加入会话）[E14][E15]。

## 3. 信道清单

副屏契约（`UNDERCOVER_GAME.md`「Existing contracts」）声明副屏只使用 4 个状态机接口 + 会话文件接口，下表 C3～C7 即副屏的全部服务端面。

| # | 信道 | 写入方 | 载体 / 接口 | 副屏是否使用 | 证据 |
|---|---|---|---|---|---|
| C1 | 群聊消息 | R、人类、Bot | 协作群聊天流 | — | E1 |
| C2 | 副屏卡片参数 | `bcs_state_machine` | 群聊中的 `AixUI` 卡片 `params` | 是（输入） | E2、E4 |
| C3 | 待处理人类节点 | BCS runtime | `GET /state-machine-runs/{run}/pending-human-nodes`（含 `instruction`、`upstream_artifacts`） | 是，高频轮询 | E7、**E15** |
| C4 | 单个节点运行结果 | BCS runtime | `GET /state-machine-runs/{run}/nodes/{node}`（含 `artifact_text`） | **是，自动拉取已完成的映射节点** | E8、E11、**E16** |
| C4b | 整个 run 视图 | BCS runtime | `GET /state-machine-runs/{run}`（**一次返回全部节点 `artifact_text` + 发言 history**） | 否（接口存在） | **E17**、源码 |
| C5 | run 图视图与 run.input | BCS runtime / R | `GET /state-machine-runs/{run}/graph` | 是 | E8b |
| C6 | 人类提交 | 人类 | `POST /state-machine-runs/{run}/nodes/{node}/respond` | 是 | E9 |
| C7 | 会话共享文件 | R | `/sessions/{sid}/files`（`undercover-result-*.json`）+ 群聊带 `token` 下载链接 | 是（终局） | E5、**E18** |
| C8 | Bot 节点 instruction / Bot 会话上下文 | R → W | BotTask 派发；W 的 OpenClaw 会话 | — | E3、E6 |
| C9 | 主持人私有状态 | R | R 的 workspace 状态文件 | — | E6 |
| C10 | 群内任务状态消息 | BCS | 群聊系统消息「[任务状态] 待回复 / 已回复」 | — | 待查 |
| C11 | Agent 事件转发 | OpenClaw → BCS | `Forwarded agent event stream=assistant/lifecycle` | — | 待查 |
| C12 | 上游产物 | BCS runtime | 节点提示 / pending 中的 `upstream_artifacts`（直接父节点的产物） | 是 | E7、源码 |
| C13 | Group Context | R / Bot / 人类 | `POST /groupcontext/{add,update,list,get}`（条目带 `visible_to`/`collect_from`/`owners`，持久化 KV） | 否（独立于副屏契约；人类副屏可直接调用） | E13、E15 |
| C14 | 系统 `<GroupContext>` 群消息 | BCS（systemset） | 新建会话时自动发入群聊的「当前你在 bcn 群聊中…群聊信息如下」系统消息 | — | **E19** ✅ |
| C15 | 会话历史 | BCS runtime | `GET /sessions/{sid}/messages`：由 run 快照生成 HumanInput 提示词消息（含 instruction）与节点产出消息（含 `artifact_text`），按调用者 `message_view_scope` 过滤 | 否（群聊/工作台使用） | **E23**、源码 |

## 4. 可见性矩阵（多人类场景，现状）

图例：✅ 无泄露　🟠 有条件泄露 / 需约束　🔴 确定泄露　❓ 待验证　— 不适用

| 信道 | 携带什么 | H_B 能否拿到 A 的 S1/S2/S3 | W | X | 依据 |
|---|---|---|---|---|---|
| C1 群聊 | 主持人发词：「你是 N 号，你的词是【…】」 | 🔴 S1：打开群聊即见 | ✅ 收不到群广播 | ❓ | E1 |
| C2 副屏卡片 | 按某个观看者定制的参数（`voteCandidates` 排除本人、`currentViewerActorId`） | 🟠 无词语；但所有人收到同一张卡，B 看到为 A 定制的参数，前端可能按 A 渲染 | — | ❓ | E2、E4 |
| C3 pending 接口 | 人类节点 `instruction`：开头【你的词语】+ UI_CONTEXT 中 `"word"` | 🔴 S1：**已双人实测**。节点无 assignee，对所有在场人类返回；以 human_002 身份调该接口，拿到 human_001（4 号）节点，`seatNumber:4`、`word:"保温杯"` | — | ✅ 非在场者 403 | E7、**E15**；`runtime.rs:3747-3760` |
| C4 节点运行结果 | `artifact_text`、`responded_by` | 🔴 S3：**已实测**。计票前以自身身份 `GET nodes/vote_1..5` 均 200，`artifact_text` 含票面；副屏对已完成的映射节点自动拉详情，界面只显示"已投票"，但网络响应里有票面 | — | ❓ | E8、E11、**E16**；README「荣誉制约定」 |
| C4b run 视图 | 全部节点运行记录（含 `artifact_text`）、`session_id`/`group_id` 等元信息 | 🔴 S3：**已实测**。单请求返回全部发言 history 与投票 `artifact_text`，比 C4 逐个拉更省事 | — | ❓ | E15、**E17** |
| C5 graph / run.input | 节点状态；`run.input`：存活名单、开场公告、规则 | ✅ 不含 instruction 与词语；🟠 `run.input` 由 R 写入，需约束不得含词 | — | ❓ | E8b |
| C6 提交接口 | 调用者身份 | ✅ 身份来自认证；🟠 前端节点无 assignee，**任何在场人类都能替别人的节点提交** | — | — | E9；`runtime.rs:3502-3514` |
| C7 共享文件 | 结果文件：词对、全员身份、投票明细 | ✅ 只在 `phase == FINISHED` 后上传（源码 [E24]），对局中无窗口；早于公告约 6 秒属终局后 | ❓ | 🟠 下载 token 不绑身份，有链接即可下 [E18] | E5、E18、**E24** |
| C8 Bot 上下文 | 各 Bot 只拿到自己的词 | — | ✅ 5 个平民 Bot 目录中均无卧底词 | — | E3、E6 |
| C9 主持人状态 | 全员词表 | ✅ 仅 R 私有 | ✅ | ✅ | E6 |
| C10 任务状态消息 | 谁已回复 / 待回复 | ❓ 可能暴露投票进度（不含内容） | ❓ | ❓ | 待查 |
| C11 事件转发 | 主持人推理过程 / 工具调用？ | ❓ | ❓ | ❓ | 待查 |
| C12 上游产物 | 发言节点：本轮此前发言；投票节点：`vote_start` 的中性确认 | ✅ 现状只含公开信息；🔴 **若改为人类串行投票**，下一位人类会收到上一位的票（方案 B 的坑） | — | — | E7；`player-count-spec.md` |
| C13 Group Context | 条目内容 + `visible_to`/`collect_from`/`owners` 名单 | ✅（`dev`）正确设置时隔离有效：他人 get 返回 403 [E13]；`list` 只返回可读/可写条目 [E20]；非 owner 改不了可见名单。🟠 注意：`visible_to` 空名单 = 所有人可见 [E21]；`list` 不带创建者，会话内的人可塞干扰条目 → 副屏须按精确名字 + session 级 `get` | ❓（Bot 经 bcs-cli 调用） | ✅ 非会话参与者 add 被拒 | E13、E20、E21 |
| C14 系统群消息 | 「群聊信息」正文 | ✅ 只含群/会话 ID、群名、角色、成员表，无词无身份 | — | ❓ | **E19** |
| C15 会话历史 | 提示词消息（含词）；节点产出消息（含票） | 参与者视角：✅ 只含本人的提示词与产出（原型测试 [E25]）；提示词受众在无绑定时为全部在场人类（`human_input_prompt_actor_ids`），绑定后只含本人。`Full` 视图：🔴（源码）不做受众检查，会话历史接口返回计票前全部投票产出，群聊界面是否渲染待实测；`Full` 为产品默认，保留，游戏引导人类使用参与者视角 | — | ❓ | E23、E25 |

## 5. 泄露点汇总（按优先级）

| 编号 | 泄露点 | 级别（现状） | 本阶段处理（RFC） |
|---|---|---|---|
| L1 | 主持人在群聊里发词（C1） | 🔴 | 改走 Group Context 私有条目（RFC §4）→ 服务端隔离 |
| L2 | pending 接口向所有在场人类返回含词的 instruction（C3）。双人实测 [E15]：授权只校验 `is_human && Present`，不校验节点归属 | 🔴 | 人类节点绑定 `runtime_actor`，pending 按认证身份过滤（RFC §5）；instruction 不再含词 → 服务端隔离 |
| L3 | 投票内容在计票前被他人获取（C4 / C4b）：副屏自动拉取已完成投票节点详情 [E16][E17] | 🔴 | 副屏在 `voteTallyNodeId` 完成前不拉取、不展示（RFC §6）→ **仅界面层**；直接调接口仍可读 = 已知限制 |
| L4 | 副屏卡片发在群聊、参数按某人定制（C2） | 🟠 | 卡片参数只放公共信息；按人定制部分由副屏依据认证身份与 pending 推导（profile 实现） |
| L5 | 前端节点可被任何在场人类代为提交（C6） | 🟠 | 绑定后提交校验已现成（RFC §5）→ 服务端 |
| L6 | `run.input` / 共享文件是 R 写给所有人的通道（C5、C7）；结果文件下载 token 不绑身份 [E18] | 🟠 | 约束 + 测试 T5：对局中写入内容不含词语或身份；结果文件只在终局上传 [E24]；token 绑定身份待讨论（RFC §11） |
| L7 | Group Context 抢注 / 干扰条目（C13） | 🟢（`dev`） | `dev` 已有 add 成员校验与 owner；profile 层另做：`visible_to` 非空、条目名随机串建好后公开、精确名字 + session 级 `get`、遇 conflict 中止 |
| L8 | 会话历史泄露词与计票前票面（C15） | 🔴（`Full` 视图） | 词：不进 instruction；提示词受众随绑定收窄。票：参与者视角已有投影 [E25]；`Full` 视图保留为产品默认 → 已知限制，游戏引导使用参与者视角 |

### 关于 Group Context 作为"私有发词通道"（基于 `dev`）

#2900 之后的 Group Context（C13）是 issue 要求的"发词改走每人私有通道"的现成候选，且**不需要改 BCS**。#2900 带来的新语义正好加固了它作为发词通道的安全性：

- **成员准入（spec-02）**：`add` 一条 session 级条目，要求 actor 是该会话的**当前参与者**。主持人本就是会话成员，满足；会话外的人无法 add 条目 → 堵住外部抢注。
- **owner 管理权（spec-03）**：创建者（主持人）默认是 owner；**非 owner 的写权限只能改 `content`，改不了 `visible_to`/`collect_from`/`owners`**。
- **collect_from 可选（spec-04）**：发词条目不设 `collect_from`，只有 owner（主持人）可写。

**发词方案（按座位一人一条）**：
- 主持人为每位人类玩家 `add` 一条 **session 级** 条目，条目名 `word_<rand>_seat{N}`（随机串在全部条目建好后才公开），`visible_to` = `[主持人, 该座位玩家的 actor_id]`（必须非空），不设 `collect_from`，`owners` 默认归主持人。
- 人类副屏以自身认证身份按精确名字、锁定 session 级 `get` 取词，只接受恰好 1 条；他人 `get` 返回 403。
- **不用** 维护者示例 `group-context-undercover-test` 的 `player_word_1/2` 两条按组分发方式——那对人类会从"告诉你查哪条"泄露分组（主持人只能在群里说"A 查 1、B 查 2"）。
- 投票并行与计票前票面（L3）不在 Group Context 范围内，见下方编排约束与 RFC §5、§6。

> 基线：按 `oss-camp2026` 会同步 `dev` 设计（维护者 10/27 意见），不为缺少 #2900 的分支维护长期兼容方案。具体条目设计与取词流程见 RFC §4。

### 编排约束（非泄露，但决定方案形态）

- **人类节点不能并行**：`definition.rs:686-703` 要求任意两个 HumanInput 有先后依赖。现有投票图 `vote_start → 各 vote_N → tally` 在单人类时恰好满足；多人类时被定义校验拒绝。
  - 本阶段（维护者 10/27 确认）：BCS 放开前端 HumanInput 的 `runtime_actor` 绑定，绑定到不同人类的节点允许并行（RFC §5）。
  - 曾评估的替代方案（不改 BCS，`player-count-spec.md` 的思路）：人类投票串成链。代价是体验变成"依次投"，C12 会把上一位人类的票带给下一位，L2 / L5 仍然存在，故未采用。
- **投票入口 `vote_start` 必须由一个存活的玩家 Bot 担任**。若 Bot 为 0 或后期 Bot 全部出局，投票图无法构造 → 需限制"至少 1 个 Bot 且存活"或另设入口。
- **主持人发牌脚本的单人类硬限制**：`undercover.py:1217-1218`（`len(humans) > 1` → `die`）；单数字段 `human_actor_id`/`human_seat`/`human_word`（667/691/1330/1953/1976 行）。实现阶段需改为按座位存多人类。

### 会被多人类放大的现有限制

- 人类中途退出 Present 会让下一轮协作直接失败：人类越多，触发概率越高。
- `[任务状态]` 灌入会打断主持人正在进行的激活；10/17 对局 3 中主持人在遗言回执前后挂死（证据记录·环境问题 4，是否同因待验证）。
- 副屏依赖节点详情接口显示已完成的发言，所以 L3 的界面层处理只针对投票阶段、且以明确的计票节点为界（RFC §6），不影响发言展示。

## 6. 已确认成立的隔离

- 玩家 Bot 只拿到自己的词，群广播和终局公告都没有进入它们的上下文 [E6]
- 服务端提交 / pending 接口的身份来自认证（`bcs_session` cookie），不来自副屏公开参数；前端 JS 读不到该 cookie，无法伪造身份 [E9][E13]。BCS 已实现"有 assignee 时只返回给本人、只允许本人提交"[E9]
- pending 接口有 run 级人类成员门槛：非本会话在场人类调用返回 403 [E14][E15]；但对**在场的**第二个人类无效（L2）
- Group Context（#2900 后）：`add` 的 session 级成员校验堵住外部抢注；owner 机制使非 owner 改不了可见名单；`visible_to` 隔离对正确设置的条目有效 [E13]
- graph 视图不含 instruction 与词语 [E8b]
- 收票期间副屏**界面**不显示本轮投票目标 [E4]（但网络层见 L3）；结果文件只在终局上传 [E5]

- 参与者视角的会话历史只含本人的 HumanInput 提示词与产出，看不到其他人类的提示词与票 [E25]

## 7. 对"偷看者"测试的映射

以 H_B 身份逐项断言拿不到 H_A 的 S1～S3。本地可用 `X-Mock-User-Id` 造出第二个真实人类（配方已跑通 [E15]：① `GET /bots/my` 触发建号 → ② `POST /sessions/{sid}/members` 加入 → ③ `PATCH .../members/{id}` 设 `mode:present` → ④ 以该身份调目标接口）。

| 测试 | 覆盖信道 | 断言 |
|---|---|---|
| T1 | C1 | 对局中群聊消息不含任何玩家的词 |
| T2 | C3 | B 的 pending 列表不含 A 的节点；返回内容不含 A 的词（**现状此断言失败** [E15]，改 BCS 后应通过） |
| T3 | C4 / C4b | 计票前，B 的副屏不请求、不展示 A 的投票产出；直接读 A 的投票节点 / run 视图记录现状（已知限制） |
| T4 | C2 | 群聊中副屏卡片参数不含词语、不含按人定制字段 |
| T5 | C5 / C7 | 对局结束前，`run.input` 与共享文件不含词语 / 身份 |
| T6 | C6 | B 提交 A 的节点返回 Forbidden |
| T7 | C8 | 每个 Bot 的 profile / workspace 中不出现他人的词（复用 E6 的检查方法） |
| T8 | C12 | 任一人类节点的 `upstream_artifacts` 不含他人投票 |
| T9 | 副屏 | 以 B 身份渲染副屏的整个过程中，网络响应里不出现 A 的词和计票前的票面 |
| T10 | C13 | B 对 A 的发词条目 `get` 返回 403；主持人脚本拒绝空 `visible_to`、`collect_from` 只含主持人；B 抢先建同名条目不可能（随机串未公开）；B 在公开后建同名 group 级条目或近似名条目，A 的副屏仍只取到主持人那条 |
| T11 | 注入 | 人为注入一处泄露（如把词写进公开参数 / 无 assignee 节点），测试能抓住（issue 验收标准第 2 条） |
| T12 | C15 | 计票前，B 以 `Participant` 视图读会话历史拿不到 A 的词和票；`Full` 视图记录现状（已知限制） |

各测试在本阶段的预期结果（服务端保证 / 仅界面层 / 记录现状）见 RFC §8。

## 8. 待验证

- [ ] C11：Agent 事件转发是否携带敏感内容、谁能看到
- [ ] C10：任务状态消息（本局未触发，待后续对局）
- [x] C7（公布前窗口）：源码确认只在 `phase == FINISHED` 后上传 [E24]
- [x] C15（参与者视角）：原型的运行时测试确认只含本人提示词与产出 [E25]
- [ ] C15（本地实测）：以 human_002 分别设 `Full` / `Participant` 视图，在投票未计票时读 `/sessions/{sid}/messages`，并观察群聊界面是否渲染他人投票产出
- [ ] C15：`PATCH /sessions/{sid}/members/{id}` 改 `message_view_scope` 是否只允许改自己
- [ ] X 列：同群但未加入会话的成员能否访问 run 接口（pending 已知非在场者 403 [E15]）
- [ ] C2：副屏前端如何使用 `currentViewerActorId`
- [x] E11/L3：投票计票前 `nodes/vote_N` 响应带票面 [E16]
- [x] C4b：`GET /state-machine-runs/{run}` 一次返回全部节点内容 [E17]
- [x] C7 token：下载链接 token 不绑身份，局外人可下 [E18]
- [x] C14：系统 `<GroupContext>` 群消息不含词/身份 [E19]
- [x] 两人类实测：用 `X-Mock-User-Id` 造出第二个真实人类 human_002，复现 L2 [E15]
- [x] L1：主持人群聊发词，对局截图确认 [E1]
- [x] C13：人类可调用 Group Context，`visible_to` 隔离有效 [E13]

## 参考

- profile README：`singlebox/agents/6bots_undercover_game_profile/README.md`
- 副屏契约：`apps/bcs/assets/panel/UNDERCOVER_GAME.md`
- 官方 1～2 人类草稿：`singlebox/agents/6bots_undercover_game_profile/docs/player-count-spec.md`
- Group Context 规格：`apps/bcs/specs/2026-09-09-group-context-api/`（spec-01 基础；spec-02 add 成员校验；spec-03 owners；spec-04 collect_from 放开）、示例 `singlebox/agents/group-context-undercover-test/`（#2538、#2900）

## 附录：证据索引

| 编号 | 内容 | 来源 |
|---|---|---|
| E1 | 主持人在群聊里给人类发词 | 对局 1 截图 |
| E2 | 发言阶段副屏参数：无词语；`currentViewerActorId` 为公开参数 | 对局 1 卡片源码 |
| E3 | Bot 会话 lane 键 = 群 ID + 会话 ID | 玩家日志 |
| E4 | 投票阶段副屏参数：`voteCandidates` 按观看者裁剪；本轮投票不提前暴露 | 对局 2 卡片源码 |
| E5 | 结果文件只在终局上传，含全员词语与身份 | 对局 2 会话文件区 |
| E6 | 玩家 Bot 目录中无他人词语；主持人有状态文件 | 对局 2 结束后检索 |
| E7 | pending 接口 instruction 含人类的词（2 处），副屏高频轮询 | 对局 3 Network 抓包 + 源码 |
| E8 | 单个节点运行结果对已授权人类可读，含 `artifact_text` | 对局 3 控制台实测 + 源码 |
| E8b | graph 视图不含 instruction / 词；`run.input` 只有公开信息 | 对局 3 控制台实测 |
| E9 | 提交 / pending 接口用认证身份；有 assignee 时校验提交人 | 源码 |
| E10 | `undercover.py` 硬限制 1 名人类，全局单一 `human_actor_id` | 源码 |
| E11 | 副屏自动拉取已完成映射节点详情；投票阶段 `nodeActorMap` 含 `vote_N` | 源码；待实测 |
| E12 | 本地认证允许 `X-Mock-User-Id` 指定身份 | 源码：`bcs-auth-local` |
| E13 | 人类可经副屏调用 Group Context；`visible_to` 隔离有效（他人 get 403） | 10/19 控制台实测 |
| E14 | 身份来自 HttpOnly `bcs_session` cookie（前端不可伪造）；`/bots/my` 触发 `ensure_human_actor` 建号；pending 对非在场者 403 | 源码 + 实测 |
| E15 | **双人偷看实测**：human_002 加入会话设 present 后，调 pending 拿到 human_001（4 号）节点，`seatNumber:4`、`word:"保温杯"` | 10/19 控制台实测（run `sm-3fe914e7-…`、session `…:fc432319`） |
| E16 | **L3 投票票面计票前可读（实测）**：6 号（本人）未投、计票未开始时，以自身身份逐个 `GET nodes/vote_1..5` → 均 200 且 `artifact_text` 含票面（"我投3号"等 5 个 Bot 的票全部拿到） | 10/24 控制台实测（run `sm-5ab34a7a-…`、session `…:ce9bf9e5`） |
| E17 | **C4b 一次拉全（实测）**：`GET /state-machine-runs/{run}` 单请求返回 4628 字，含全部发言 history（player/seat/text）与投票 `artifact_text` | 10/24 控制台实测 |
| E18 | **C7 token 不绑身份（实测）**：结果文件下载链接 `.../shared-file/content?token=...` 用局外 mock 身份 `peeker999` 调用 → 200，下到含词对「保温杯/热水壶」与全员身份的 json。注：下载于终局后进行，"公布前窗口"未验 | 10/24 控制台实测 |
| E19 | **C14 系统消息安全（实测）**：开局自动发入群的 `<GroupContext>` 消息只含群/会话 ID、群名、角色（manager/worker）、成员表（name/bot_id/role），无词、无身份 | 10/24 截图确认 |
| E20 | **`list` 按权限过滤（源码）**：`list()` 对每条调用 `permission_for`，不可读也不可写返回 `None` 即不列出；列表项只有 name/scope/permission/时间，无创建者 | `bcs-app-group-context/src/service.rs` `list()`、`permissions.rs`、`bcs-domain/src/group_context.rs:72-79`（两分支相同） |
| E21 | **空 `visible_to` = 全员可见（源码）**：`is_visible_to` 在 `user_ids` 为空时返回 true；`validate_policy` 只校验大小，不拒绝空名单 | `bcs-domain/src/group_context.rs:51-54`；`service.rs` `validate_policy`（两分支相同） |
| E22 | **`oss-camp2026` 上的写权限（源码）**：`add` 只要求调用者在自己提交的 `collect_from` 里，不校验会话成员；`update` 只要求 `actor_can_write`（= 在 `collect_from` 里），即可改 `visible_to`/`collect_from`。`dev` 上 `group_context_access.rs` 的成员校验与 owner 检查在 `oss-camp2026` 不存在 | `service.rs` `add()`/`update()` @ `d2a964de9` vs `6cd36d885` |
| E23 | **会话历史信道（源码）**：`state_machine_messages_from_snapshot` 把 HumanInput 提示词（`content = instruction`）与节点 `artifact_text` 转成消息；提示词受众由 `human_input_prompt_actor_ids` 给出，无 assignee 时为全部在场人类；`HumanMessageView::allows` 在 `Full` 视图直接返回 true；人类加入时 `message_view_scope` 缺省 `Full`；`update_session_participant_mode` 接受人类修改视图 | `runtime.rs:1431-1606`、`5599-5628`；`bcs-domain/src/message.rs:88-120`；`bcs-http/src/routes/sessions.rs:812-822`、`1092-1112` |
| E24 | **结果文件只在终局上传（源码）**：调用 `publish_public_result` 的 `reveal`/`publish-result`/`finish` 三处都先检查 `phase == "FINISHED"` | `undercover.py:2127-2180` |
| E25 | **参与者视角历史投影（原型测试）**：两名在场人类绑定各自的投票节点并行投票；A 投票后，B 的参与者视角会话历史不含 A 的票与 A 的提示词，A 能看到自己的票；同一时刻 B 直接调节点详情 / run 视图可读到 A 的票（已知限制） | 原型 `tests/runtime_progression/multi_human_votes.rs` |
