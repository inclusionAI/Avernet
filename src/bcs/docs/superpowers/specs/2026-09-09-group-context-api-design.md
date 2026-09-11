# Group Context API — Core Design Spec

- **Date:** 2026-09-09
- **Doc:** [group context 的核心抽象](https://yuque.antfin.com/securitytec/otbct4/bdt73nogi6kcgk9z)

---

## 1. Problem

BCS 群聊中，多 Agent 协作需要共享记忆——游戏状态、帖子处理进度、投票结果、FAQ 等。当前 BCS 的 session 上下文由系统在 session 启动时一次性发送（`SessionContext` 系统消息，包含 group_id、session_id、session_input），bot 无法在运行时读写持久化的群组级信息。具体缺失能力：

1. **群组级别的持续记忆。** SessionContext 只在 session 启动时发送一次，session 结束后信息丢失。群聊游戏需要跨 session 保留规则和玩家状态，bot 需要有 API 读写这些信息。
2. **细粒度的可见范围控制。** 谁是卧底的底牌只有裁判和该玩家可见，投票结果只有裁判和狼人可见。需要支持按 users 列表控制可见范围。
3. **版本取代与冲突解决。** 同一信息的多次更新（如玩家状态从"存活"变为"出局"），需要建立取代链，不修改旧数据，保证可追溯。
4. **来源信任与审计。** 需要知道每条信息是谁写入的、从哪派生来的，事后可审计。

本设计引入 **Group Context API**，为 BCS 群组提供受治理的共享记忆基础设施。

---

## 2. ContextEntry

ContextEntry 是 Group Context 中的单条上下文条目，三层职责：数据面(是什么) / 策略面(能怎么用) / 治理面(经历过什么)。

### 2.1 数据面 · 客观档案

框架注入、不可变、可验签。PEP 硬判定(allow/deny)的唯一依据。

| 字段 | 含义 | 示例 |
|------|------|------|
| **content** | 内容本体 | `"你的词语是：香蕉"` |
| **origin** | 收集来源（出生地证明）。tenant_id / group_id / session_id / run_id / actor_id，框架注入，不可篡改 | `{group_id:"game-room-7", session_id:"sess-001", actor_id:"judge_bot"}` |
| **provenance** | 信任链。ref（指向 origin）+ chain（派生链：extract/consolidate/propagate 逐跳追加）+ signature（框架签名，三者任一被改即失效） | `{ref: ctx_origin_msg_001, chain: [{op: extract, from: raw_msg_001}], signature: hs256(...)}` |
| **time** | 双时间线。valid_from / valid_to（内容有效期，多数条目出生时为 ∞，被 supersede 时回填）+ tx_time（系统写入时间） | `valid_to` 被 supersede 时系统回填 |

**首期不做：**
- `provenance` — 设默认值（ref 指向 origin，chain 为空，不做签名）

### 2.2 数据面 · 推断注解

抽取器/后台可精化。只用于软判定与"加严"，永不放宽。

| 字段 | 含义 | 示例 |
|------|------|------|
| **type** | 认知类型：episodic / semantic / procedural / working。被系统分析并回填，萃取器赋值，巩固时可改。检索时可作为过滤条件 | `working` |
| **sensitivity** | 敏感程度。floor（继承地板，由会话渠道策略客观决定，不可降）+ assessed（内容评估，抽取器只许上调）+ effective = max(floor, assessed)，PEP 只读此项 | 首期设默认值 |
| **derived** | 推断注解。confidence（自评置信度，仅排序/标记用，禁作硬门）+ mentioned_entities（涉及实体）+ topics（话题域）+ pii_suspected（疑似敏感 → 触发复核/加严） | 首期设默认值 |

**首期不做：**
- `type` — 设默认值 `episodic`，不做智能推断
- `sensitivity` — 设默认值（effective=group）
- `derived` — 设默认值（confidence=1.0, entities=[], topics=[], pii_suspected=false）

### 2.3 策略面

条目级规则。默认值从 origin/全局 policy 推导。

**flow（读写控制：谁能写、谁能看、能传多远）：**

| 字段 | 含义 | 示例 |
|------|------|------|
| **visible_to** | 谁可以检索到这条 context。可选参数：tenant_id、group_id、session_id、run_id、user_ids。**user_ids 为数组，支持多角色可见**（如裁判+玩家两人可见底牌）。 | `{group_id: "game-room-7", user_ids: [judge_bot, player_1]}` |
| **collect_from** | 谁能写入。可选参数：tenant_id、group_id、session_id、run_id、user_id。**user_id 为单值，一条 context 只有一个写入者。** | `{user_id: "judge_bot_xxxx"}` |
| **propagate_to** | 能传播到多远（首期写死 `{groups: 0}`） | `{groups: 0}` |
| **allowed_purposes** | 允许出于什么目的使用 | 首期不做 |
| **redact_on_export** | 传播前是否脱敏 | 首期不做 |

> **设计说明：** `visible_to` / `collect_from` 是对原文 `flow` 块的拆分，将读写权限分为独立的两端。两者刻意不对称——`visible_to.user_ids` 为数组（多角色可读），`collect_from.user_id` 为单值（单一写入者）。`granularity` 为本设计新增字段，用于界定同 `domain` 下版本链的作用域边界，原文未显式定义此维度。

**consistency（版本管理：同一条信息多次更新时怎么处理）：**

| 字段 | 取值 | 含义 |
|------|------|------|
| **domain** | string（支持 `{param}` 占位符） | 版本标识。同 domain 条目互为版本 |
| **granularity** | `run` / `session` / `group` / `tenant` | 同 domain 条目在什么范围内互为版本 |
| **merge_strategy** | `supersede` / `append` / `llm_merge` | 版本合并策略 |
| **freshness_class** | `volatile` / `stable` / `audit`（默认 `stable`） | 过期策略 |
| **revalidate_due** | ISO 8601 / null | 过期时间兜底，仅 volatile 必填 |

**granularity：**

| 取值 | 示例 domain | 含义 |
|------|-----------|------|
| `run` | `intermediate_result` | 同 run 下互为版本，run 结束后不再更新 |
| `session` | `player_state` | 同 session 下互为版本，不同 session 独立 |
| `group` | `game_rule` | 同 group 下所有 session 共享一个版本 |
| `tenant` | `cross_board_insight` | 跨 group |

> **granularity 的设计动机：** 仅靠 domain 区分版本链不足以表达作用域边界——同一个 domain 名（如 `player_state`）在不同 session 下需要独立的版本链，否则跨 session 的状态会互相覆盖。granularity 显式声明版本链的作用域（run / session / group / tenant），让同一个 domain 在不同粒度下独立演进。例如 `player_state` 在 session A 记录玩家 1 的存活状态，在 session B 记录玩家 2 的状态，靠 `granularity=session` 自然隔离，互不干扰。retrieve 时按 granularity 分组逐组取活跃版本，再按从细到粗排序拼出继承链。

**merge_strategy：**

| 取值 | 含义 | 示例 |
|------|------|------|
| `supersede` | 新写入自动取代旧版本，用新 content 替换旧 content，系统回填 valid_to + 建立 lineage | `player_state`：新状态覆盖旧状态 |
| `append` | 新 content 拼接到旧 content 末尾（`\n` 分隔），系统回填 valid_to + 建立 lineage | `player_speech`：每次发言追加到对话记录 |
| `llm_merge` | **（首期不实现）** 调用 LLM 将新旧版本合并为一条 | 两个客服 agent 独立写入不同结论，LLM 融合 |

> 不论采用哪种 merge_strategy，每个 (domain, granularity) 在同一时刻均只有一条活跃版本（即 valid_to=null 的版本数 ≤ 1）。


**freshness_class：**

| 取值 | 含义 | 示例 |
|------|------|------|
| `volatile` | 很快过期，需定期重验。过期后不可检索 | 大促政策（促销结束后自动失效），`revalidate_due` 必填 |
| `stable`（默认） | 长期有效，被 supersede 时才失效 | `game_rule`、`faq` |
| `audit` | 长期有效，写权限严格管控 | 告警根因结论 |

**obligations（附带义务：允许你用，但必须做到这些）：**

| 取值 | 含义 |
|------|------|
| `"注入时附置信度标记"` | context 注入 LLM prompt 时必须标注置信度 |
| `"检索必须落审计"` | 每次检索必须写入审计日志 |

**首期不做（策略面）：**
- `allowed_purposes` / `redact_on_export` — 不做
- `collect_from: actor_tag=xxx` — 首期只支持 `actor_id=xxx`
- 跨 group 传播 — `propagate_to` 写死 `{groups: 0}`
- obligations — 首期仅实现『检索落审计』（每次 retrieve 写入审计日志），其他 obligation 类型不做

### 2.4 治理面

生命周期痕迹与指针。

| 字段 | 含义 |
|------|------|
| **lineage** | 取代链（append-only 的"修改"实现）。supersedes（向上：我取代了谁）+ superseded_by（向下：谁取代了我）+ superseded_at（何时被取代）。系统在 merge_strategy=supersede 时自动维护——**不修改旧条目的数据面**（content / origin / provenance 等业务字段保持不变），但会维护旧条目的**生命周期字段**：`time.valid_to`、`lineage.superseded_by`、`lineage.superseded_at`。新条目写入时设 `lineage.supersedes` 指向旧条目 |
| **verification** | 保鲜验证。last_verified_at（上次验证时间）+ verified_by（验证方） |
| **governance** | owner（责任人，跨边界传播/遗忘的审批人）+ policy_version（生命周期受哪版 policy 管辖）+ audit_ref（审计日志指针）+ forget_request（遗忘请求，墓碑化 + 全读路径屏蔽，留痕） |

**首期不做（治理面）：**
- `verification` — 首期靠 supersede + freshness 管理
- `forget_request` — 首期不支持遗忘操作
- `policy_version` — 首期不实现策略版本管理

---

## 3. Scenario Walkthrough: 谁是卧底

- admin 在 group 创建时为 game-room-7 绑定 6 个 domain 策略（game_rule、player_word、game_status、player_speech、vote_result、player_state）
- game_rule（domain=game_rule, granularity=group, freshness_class=stable）在 group 创建时自动生成一条 context 实例
- session 启动时，master bot 调用 POST /groupcontext/status 获取当前权限视图：已存在的 contexts（game_rule 等）+ 可创建的 context_templates（player_word 等）
- judge_bot 看到 `player_word` 的描述："需要为每位玩家单独调用一次"→ 调 5 次创建接口，每次传入不同 player_id
- 玩家检索自己的底牌：系统按 visible_to 验证，仅包含自己时返回
- 玩家冒充裁判写入：系统比对 actor_id ≠ 模板的 collect_from → deny
- 裁判写入 player_state → 同 domain + granularity=session 下已有旧版本 → merge_strategy=supersede → 系统自动回填旧条目 valid_to + 建立新条目的 lineage.supersedes

---

## 4. Open Questions

1. **actor_tag 如何写入？** group 级别由哪个接口管理？session 级别由 BCN 在 session 启动时注入还是由 bot 自行注册？
2. **domain 由谁定义？** domain 名在模板中预设——bot 能不能运行时新增 domain？
3. **propagate_to 与 granularity 的关系？** propagate_to={groups:1} 但 granularity=session 时，版本合并的范围是什么？
4. **`append` 的分隔符语义？** 当前用 `\n` 拼接新旧 content，是否需要支持自定义分隔符或结构化追加（如 JSON array append）？
5. **scope 存储格式与检索效率？** 按 users 列表过滤时是否需要倒排索引？policy 模板如何独立存储并关联版本号？