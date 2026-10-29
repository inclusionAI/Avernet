# 多人类谁是卧底：上下文隔离

Issue: [#2609](https://github.com/inclusionAI/Avernet/issues/2609)。Target: `oss-camp2026`。
Status: 评审中（v0.4，2026-10-27，已按维护者 10/27 的评审意见修订）。
配套文档：同目录 [`threat-model.md`](threat-model.md)（信道 C1～C15、泄露点 L1～L8、偷看者测试 T1～T12、证据 E1～E25）。

**开发基线**：按 `oss-camp2026` 会同步 `dev` 来设计。本文依赖 `dev` 上已有的 Group Context 能力（#2538、#2900，后者为 `d163eb905`：add 成员校验、owners、collect_from 可选），实测核对到 `dev` @ `d48b2885c`（2026-10-27）。`oss-camp2026` 当前缺少 #2900，本文不为此维护长期兼容方案。

---

## 1. 问题

现状只支持 **1 名人类 + 5 个 Bot**，隔离依赖两个前提：玩家 Bot 收不到群广播，且只有一名人类。于是主持人在群聊里给人类发词，副屏和接口对"所有在场人类"返回数据——单人类时这等于私密。多人类时这两个前提失效（详见威胁模型 §4、§5）：

| 泄露 | 出口 | 现状 |
|---|---|---|
| L1 | 主持人在群聊里发词 | 任何人类打开群聊即见他人的词（E1） |
| L2 | `pending-human-nodes` 对所有在场人类返回含词的 `instruction` | 双人实测：第二个人类拿到 4 号的词（E15） |
| L3 | 节点详情 / run 视图返回已完成投票节点的 `artifact_text`；副屏自动拉取 | 计票前读到其他人的票面（E16、E17） |
| L5 | 前端 HumanInput 无绑定，任何在场人类都能替别人提交 | 源码（E9） |
| L8 | 会话历史在 `Full` 视图下包含全部节点产出 | 源码（E23） |

根因不是身份可被伪造——人类身份来自 HttpOnly `bcs_session` cookie，前端改不了——而是**授权粒度**：确认了"你是谁"之后，没有按"这条数据是不是给你的"过滤。

## 2. 目标与非目标

目标（对齐 issue）：

1. 发词走每人私有通道，每位玩家只能拿到自己的词。
2. 访问者身份来自认证，不来自公开参数（现状已满足，本文保持）。
3. 多人类"发言串行、投票并行"的 HumanInput 编排。
4. 支持 2～4 名人类 + 若干 Bot，总人数 4～8，卧底数可配。
5. 单人类原流程不回归（以 `tests/fixtures/flow_baseline.json` 为准）。

非目标：观战模式、断线重连、超过 8 人。

## 3. 本阶段的隔离边界

这一节是本阶段验收口径。不同信息的保证强度不同，**不能把前端过滤描述为服务端隔离**。

| 信息 | 保证方式 | 强度 |
|---|---|---|
| 某玩家的词（S1） | Group Context 私有条目，`visible_to` 由服务端校验（§4） | 服务端隔离 |
| 待处理节点、代为提交 | 人类节点绑定 `runtime_actor`；pending 只返回绑定到自己的节点，提交校验绑定关系（§5） | 服务端隔离 |
| HumanInput 提示词 | 会话历史中提示词的受众随绑定收窄为本人 | 服务端（参与者视角） |
| 计票前的票面（S3） | 副屏在本轮计票节点完成前不拉取、不展示他人投票产出（§6）；参与者视角的会话历史本就不含他人产出 | **仅界面层** |

**已知限制（本阶段接受）**：在场人类直接调用以下接口，仍可在计票前读到他人票面——

- `GET /state-machine-runs/{run}/nodes/{node}`、`GET /state-machine-runs/{run}`（节点详情 / run 视图沿用现有 run 级访问判断）；
- 选择 `Full` 视图时的会话历史：源码上该视图不做受众检查，会话历史接口会返回他人的投票产出（E23）；群聊界面是否直接渲染这些消息待本地实测。`Full` 是产品默认值，因此游戏要求人类玩家改用参与者视角（§6.3）。

这些路径在偷看者测试里以"记录现状"的方式覆盖（§8），后续若补服务端可见性校验，对应断言翻转即可。

## 4. 发词：Group Context 私有条目

### 4.1 条目设计

开局发牌时，主持人为每位人类玩家 `add` 一条 session 级条目：

| 字段 | 值 |
|---|---|
| `name` | `word_<rand>_seat{N}`，`<rand>` 为每局随机串 |
| `scope_level` | `session` |
| `content` | `你的词语是：{词}` |
| `visible_to` | `{ userIds: [主持人, 该座位人类的 actor_id] }`，**必须非空** |
| `collect_from` | 不设置（只有 owner 可写） |
| `owners` | 缺省即创建者（主持人） |

两条约束由主持人脚本保证，T10 覆盖：

1. **`visible_to.userIds` 非空**（`add` 前断言）：空名单在 Group Context 中表示"所有人可见"（E21）。
2. **遇 `conflict` 中止本局**，不跳过、不重试改名。

玩家 Bot 保持现有发词方式（它们收不到群广播，无需改动）。

### 4.2 取词流程

1. 主持人生成 `<rand>`，为每个人类建好条目。
2. **全部建好之后**，主持人才把 `<rand>` 写进公开的副屏参数。座位号本就公开，人类副屏由此拼出自己的条目名。
3. 副屏以自身认证身份调用 `get name=word_<rand>_seat{自己的座位} scope_levels=[session]`，只接受恰好 1 条结果，否则提示"取词失败"。
4. 人类节点的 `instruction` 与 UI 上下文中**不再写词**。

为什么条目名可以在建好后公开：词受 `visible_to` 保护；随机串防的是抢注——会话参与者（人类也是参与者）在 `<rand>` 公开前无法预知名字，公开时条目已存在，同名 `add` 返回 conflict。

为什么不用 `list` 找条目：`list` 返回调用者可读的全部条目且不含创建者（E20），会话内任何人都能给受害者塞一条"像发词"的条目。精确名字 + 锁定 session 级 + 只接受 1 条，可排除这种干扰。

为什么不用示例 `group-context-undercover-test` 的 `player_word_1/2` 按组分发：对人类只能在群里说"谁查哪条"，A 听到"B 查 2"即知道分组。

### 4.3 能力缺口核对（基于 `dev`）

| 需要的能力 | `dev` 现状 | 结论 |
|---|---|---|
| 人类以认证身份读取 | `/groupcontext/*` 路由从请求头解析调用者，人类解析为 `human_<staff_no>`；10/19 在 `oss-camp2026` 实测人类可 add/get、他人 get 返回 403（E13），`dev` 上该路由的调用者解析相同 | 满足 |
| 外部抢注防护 | `add` 校验调用者是 session 当前参与者（`group_context_access.rs`） | 满足 |
| 防篡改可见名单 | 非 owner 不能改 `visible_to`/`collect_from`/`owners` | 满足 |
| 锁定作用域读取 | `get` 支持 `scope_levels` | 满足 |

**结论：BCS 侧没有发现缺口。** 需要补的是副屏的 Group Context 调用（profile/panel 范围）。一个可选增强（非阻塞）：`get` 目前只对 owner 返回 `owners`，若对可读者也返回 `owners`，副屏可以直接校验"条目确由主持人创建"，不必依赖名字不可预测。

## 5. HumanInput 绑定与并行（BCS 改动）

### 5.1 改动

只改 `bcs-collaboration-runtime` 的定义校验（`definition.rs`）：

1. 前端 HumanInput（无 `notification`）允许指定 `runtime_actor` assignee；`bot_binding` 或空 actor 仍拒绝。
2. 绑定到**不同** `runtime_actor` 的 HumanInput 允许并行等待；同一 actor、或其中任一未绑定的，仍按原规则要求显式先后依赖。

运行时不需要新代码，复用已有逻辑：

- `pending-human-nodes` 按服务端认证得到的当前人类过滤：已绑定的节点只返回给绑定者，未绑定的沿用原规则。不新增请求参数，副屏继续调用原接口。
- 提交时已有绑定校验，非绑定者返回 Forbidden。
- 会话历史中 HumanInput 提示词的受众，有绑定时只含绑定者。

### 5.2 为什么可以放开并行

BCS 设计文档中"多个 HumanInput 必须显式串行"是第一版约束，前提是 IM 回复要"按唯一 pending 节点猜测"归属；2026-07-27 引入 RuntimeActor assignee 与精确回复路由后，文档已注明替代了这条旧约束（`apps/bcs/docs/superpowers/specs/2026-01-08-human-node-design.md` 开头的更新说明）。前端提交本就在 URL 中带 `node_id`，再加上绑定过滤，两个不同的人同时等待不存在歧义。

### 5.3 兼容

无绑定的单人类定义行为不变；新增能力只在作者显式绑定时生效。作者文档 `custom-collaboration-schema.md` 同步说明绑定方式、并行条件，以及"绑定不隐藏已完成节点的产出"。

## 6. 投票展示（profile / panel 改动）

### 6.1 明确的汇总节点

每轮投票是一个独立 run：`vote_start`（存活 Bot）→ 各 `vote_{座位}` 并行 → `tally`（主持人计票）。主持人在**投票阶段**的副屏参数中写明：

```json
"voteTallyNodeId": "tally"
```

副屏校验该节点在 `nodeActorMap` 中且属于主持人，否则拒绝渲染。发言阶段（汇总节点为 `collect`）不设置此字段，发言展示不受影响。

### 6.2 副屏规则

投票阶段、设置了 `voteTallyNodeId` 时，对非主持人的节点：

- `tally` 完成之前，**不调用**节点详情接口，仅凭 graph 状态显示"已投票"；点开玩家详情时也只展示 graph 数据；
- `tally` 完成之后恢复原逻辑。

未设置 `voteTallyNodeId` 的旧参数保持原行为。

### 6.3 视图

保留 `Full` 为默认视图（非对抗协作场景的产品设计）。游戏场景由人类玩家手动选择**参与者视角**：该视角下会话历史已有的投影只给本人自己的产出与提示词（原型测试已覆盖），群聊里看不到他人的票。§6.2 的副屏规则不依赖视角设置，任何视角下都生效。拟在主持人开场提示中提醒人类玩家切换视角；`Full` 视角下会话历史可含他人票面，属于 §3 的已知限制。

## 7. 编排图

```
发牌：主持人为每位人类 add 私有词条目（session 级）→ 公开 <rand>
        │
第 N 轮发言（串行，按座位顺序）：
  speak_{座位} → … → collect（主持人汇总，全员可见）
  人类节点绑定 runtime_actor；instruction 不含词
        │
第 N 轮投票（并行）：
  vote_start（存活 Bot）
     ├── vote_{人类A}（绑定 A） ┐
     ├── vote_{人类B}（绑定 B） ├─ 并行
     └── vote_{Bot}             ┘
        │
  tally（主持人计票，= voteTallyNodeId）→ 公布 → 下一轮 / 终局
```

`vote_start` 必须是存活的 Bot（BCS 要求唯一入口），因此每局至少 1 个 Bot，且投票发起时该 Bot 存活；本期通过限制 Bot 数下限保证，不实现替代入口。

## 8. 测试与验收

偷看者测试以人类 B 的身份断言拿不到人类 A 的信息（本地用 `X-Mock-User-Id` 造出第二个真实人类，配方见威胁模型 §7）。下表是**本阶段实现完成后的预期**；标注"原型已验证"的项已有自动化测试。

| 测试 | 断言 | 本阶段预期 |
|---|---|---|
| T1 | 群聊不含任何玩家的词 | 通过 |
| T2 | B 的 pending 不含 A 的节点、不含 A 的词 | 通过（服务端；原型已验证节点过滤） |
| T3 | 计票前 B 读不到 A 的票 | **界面层通过**；直接调接口可读——记录现状（原型已有该测试） |
| T4 | 副屏卡片参数不含词语、不含按人定制字段 | 通过 |
| T5 | 终局前 `run.input` 与会话文件区不含词语/身份 | 通过 |
| T6 | B 提交 A 的节点返回 Forbidden | 通过（服务端；原型已验证） |
| T7 | Bot 的 profile/workspace 不含他人的词 | 通过 |
| T8 | 人类节点的 `upstream_artifacts` 不含他人投票 | 通过（并行投票，无人类上游） |
| T9 | 以 B 身份渲染副屏，计票前网络响应不含 A 的词和票 | 通过（副屏不再请求；原型已验证投票部分） |
| T10 | B `get` A 的词条目返回 403；主持人拒绝空 `visible_to`；遇 conflict 中止；干扰条目不影响取词 | 通过（服务端） |
| T11 | 人为注入一处泄露，测试能抓住 | 通过 |
| T12 | 计票前 B 在参与者视角的会话历史中读不到 A 的词和票 | 通过（服务端；原型已验证）；`Full` 视图记录现状 |

回归：referee `tests/*.py`、panel `npm run verify`、`cargo test -p bcs-collaboration-runtime`、单人类 `flow_baseline.json`（仅投票阶段副屏参数新增 `voteTallyNodeId`）。

验收局：「2 人类 + 3 Bot」「3 人类 + 3 Bot」各打完一局。

## 9. 实现与 PR 拆分

| PR | 内容 | 前提 |
|---|---|---|
| 文档 | 本目录：RFC + 威胁模型 | — |
| BCS | §5：`definition.rs` 两处放开 + 测试 + 作者文档 | RFC 对齐 |
| profile / panel | 多人类发牌与 Group Context 发词、人类节点绑定、`voteTallyNodeId`、副屏取词与 4～8 座布局、偷看者测试 | BCS PR 合入 |

profile 侧已知改动点：放开 `undercover.py` 的单人类限制（`len(humans) > 1`），单数字段 `human_actor_id` / `human_seat` / `human_word` 改为按座位的多人类结构；副屏参数中按人定制的字段（`currentViewerActorId`、`voteCandidates`）改由副屏依据认证身份与 pending 结果推导（L4）。

原型（供评审参考，未提 PR）：BCS 绑定与并行、`voteTallyNodeId`、副屏按计票节点隐藏票面已实现，`cargo test -p bcs-collaboration-runtime`、referee `tests/*.py`、panel 单元与集成测试通过；在 `oss-camp2026` 与 `dev`（`d48b2885c`）上均可无冲突应用且上述测试通过。

## 10. 评审记录

2026-10-27 维护者意见（已落实到本版）：

- BCS 补齐前端 HumanInput 的人类绑定，复用 assignee 机制，允许绑定不同人类的节点并行等待；pending 按认证身份过滤，提交校验绑定；不新增请求参数。→ §5
- 本阶段不引入服务端的节点产出可见性控制；由前端在明确的汇总节点完成后再展示并行投票产出。→ §6
- 节点详情等接口保留现有访问判断与主消息流的参与者投影；在 RFC 与验收说明中明确边界与已知限制。→ §3、§8
- 文档直接向 `oss-camp2026` 提 PR 至 `docs/specs/`；BCS 实现在 RFC 对齐后单独提 PR；原型可先推到 fork。→ §9
- 按 `oss-camp2026` 会同步 `dev` 设计，标明依赖的 `dev` 版本。→ 文首
- 保留默认 `Full` 视图，游戏展示过滤在参与者视角下生效。→ §6.3
- Group Context 可用于私有发词；如有缺口在 issue 中提出。→ §4.3（未发现 BCS 缺口）

## 11. 待讨论

1. 结果文件下载链接的 token 不绑定身份（E18）。文件只在终局后上传（E24），不影响对局公平；是否另开 issue？
2. §4.3 的可选增强（`get` 对可读者返回 `owners`）是否值得做？
