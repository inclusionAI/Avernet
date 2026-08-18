---
name: task-search-arch
description: 通用派发决策。在框架预查的候选 bot 集里按子任务需求与 bot_name/desc 语义匹配决出执行者(who)+ 协作方式(how),返回 4 态(HIT_SINGLE/HIT_GROUP/HIT_MULTI_BOTS/MISS)。无案例剧本表,纯按候选 catalog 通用判定。
version: 1.0.0
author: avernet-task-framework
tags: [task, search, dispatch, generic]
---

# task-search-arch

**通用**任务派发搜推决策 skill,运行在 **owner bot**(source_channel_id)。框架语义预查候选 bot 集
(分字段 title/objective/background 调 singlebox keyword / BCSFuse recommend),把候选集喂入 prompt;
本 skill **在候选里**决出**谁执行 + 怎么执行(多 bot 拉哪种协作群)**,不自取候选源。

与 ``task-search`` 的区别:本 skill **不挂任何案例 node_id 剧本表**,纯按子任务需求与候选 catalog 的
语义匹配判定——有匹配 bot 即 HIT、无匹配即 MISS。供 LLM 自规划(非案例)任务用。

## 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具。
> 搜推判定仅基于 prompt 中的子任务需求与候选清单进行;候选清单已在 prompt 给出,直接挑选,无需联网。

## 触发条件

收到 prompt 头部 `[search]` 标记的指令,且 prompt 含 `子任务需求+候选集{demand, catalog}` + 返回格式约定
(详见框架 `SearchBasedDispatchStrategy._compose_search_prompt`)。

## 输入(prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `demand.node_id` | 待派发子任务节点 id |
| `demand.goal` / `demand.instruction` / `demand.acceptances[]` | 子任务需求 |
| `catalog[]` | 框架预查候选 bot 集:`{bot_id, bot_name, bot_desc, score, short_profile, reasons}`(按 score 降序) |

## 输出(返回格式约定)

返回 JSON 字符串,`outcome` 标 4 态之一(详见框架 `_parse_search_result`):

- **HIT_SINGLE**:`{"outcome":"HIT_SINGLE","bot_id":"<bot_id>"}`
- **HIT_GROUP**(已有协作群可复用):`{"outcome":"HIT_GROUP","group_id":"<group_id>"}`
- **HIT_MULTI_BOTS**(多 bot 协同,需动态拉协作群):
  ```json
  {"outcome":"HIT_MULTI_BOTS","bot_ids":[...],"collab_mode":"chat|manager_worker|state_machine",
   "group_name":"<协作群名>","members_info":[{"bot_id":"...","role":"<角色>","responsibility":"<职责>"}],
   "manager_bot_id":"<manager_bot_id>(collab_mode=manager_worker 时必填)",
   "definition_yaml":"<workflow yaml>(collab_mode=state_machine 时必填)"}
  ```
- **MISS**:`{"outcome":"MISS","miss_reason":"<原因>"}`

> **bot_id / bot_ids 必须填 `catalog` 里的真实 `bot_id`**(按 `bot_name` 匹配取该 bot 的 `bot_id`);
> catalog 中无匹配 bot → MISS,**不得用其它 bot 顶替、不得编造 bot_id**。`start_run` 用此 bot_id 真实投递,
> **bot_name 不是 bot_id**。

## 判定规则(通用,无案例表)

按子任务 `demand`(goal/instruction/acceptances)与候选 `catalog` 的语义匹配判:

1. **单 bot 足够**:demand 是单一视角/单一交付物,catalog 里有**一个** bot 的 `bot_name`(及 `bot_desc`,
   若有)语义上覆盖该需求 → `HIT_SINGLE`,取该 bot 的 `bot_id`。若有多个相关 bot,选**最贴合 demand 的那一个**
   (不要为"更全面"擅自升级成多 bot)。
2. **需多 bot 协作**:demand **明示**需多个视角/角色协作(如「双视角」「多视角」「联合」「A + B 两个专家协作」
   等),且 catalog 里有 ≥2 个 bot 各覆盖其中一个视角/角色 → `HIT_MULTI_BOTS`,`bot_ids` 取这几个 bot 的
   `bot_id`,`collab_mode` 默认 `manager_worker`(有明确主从)/`chat`(对等),`members_info` 逐 bot 填角色+职责,
   `manager_bot_id` 填主导 bot(`manager_worker` 必填)。
3. **无匹配**:catalog 为空,或 catalog 里**没有任何 bot** 的 `bot_name`/`bot_desc` 与 demand 语义相关 → `MISS`,
   `miss_reason` 写明"候选 bot 均无法覆盖子任务需求(<demand 摘要>)"。

### 强约束

- **只在 catalog 里选**;catalog 里与 demand 无关的 bot(如 owner bot、中继 bot、同名噪音 bot)是预查噪音,
  **必须忽略**,不得因"有 bot 就 HIT"误派无关 bot。
- **bot_id 必须来自 catalog**(按 `bot_name` 匹配取真实 `bot_id`),**不得编造**;`bot_ids` 同理,每个都须在 catalog。
- 单一交付物/demand 用 `HIT_SINGLE`;只有 demand **明示需多 bot** 才用 `HIT_MULTI_BOTS`。不得无谓拉群。
- catalog 空 → 一律 `MISS`(不得 fallback 全量 bot)。

## 示例

### A. 单 bot 足够(HIT_SINGLE)

demand「基础架构方向技术栈概览」,catalog 含 `技术栈概览Bot` 等:
```json
{"outcome":"HIT_SINGLE","bot_id":"<技术栈概览Bot 的 bot_id>"}
```

### B. 需多 bot 协作(HIT_MULTI_BOTS,demand 明示双视角)

demand「从业务架构与数据架构双视角深度分析」,catalog 含 `业务架构视角Bot`+`数据架构视角Bot`:
```json
{"outcome":"HIT_MULTI_BOTS","bot_ids":["<业务架构视角Bot 的 bot_id>","<数据架构视角Bot 的 bot_id>"],
 "collab_mode":"manager_worker","group_name":"业务与数据架构双视角分析群",
 "manager_bot_id":"<业务架构视角Bot 的 bot_id>",
 "members_info":[{"bot_id":"<业务架构视角Bot 的 bot_id>","role":"manager","responsibility":"业务架构视角分析"},
                 {"bot_id":"<数据架构视角Bot 的 bot_id>","role":"worker","responsibility":"数据架构视角分析"}]}
```

### C. 无匹配(MISS)

demand「3 位架构师名册」,catalog 空 / 无 bot 名含「架构师」:
```json
{"outcome":"MISS","miss_reason":"候选 bot 均无法覆盖子任务需求(架构师名册无对应现成 bot)"}
```