---
name: task-search
description: 在框架预查的候选 bot 集里决出执行者(who)与协作方式(how),返回 4 态 SearchResult(HIT_SINGLE/HIT_GROUP/HIT_MULTI_BOTS/MISS)。对齐案例剧本确定式映射。
version: 1.0.0
author: avernet-task-framework
tags: [task, search, dispatch]
---

# task-search

任务目标驱动的**任务派发搜推决策** skill,运行在 **owner bot**(owner_bot_id)。框架语义预查候选 bot 集(分字段 title/objective/background 调 BCSFuse recommend),把候选集喂入 prompt;本 skill 在候选里决出**谁执行 + 怎么执行(多 bot 拉哪种协作群)**,不自取 BCSFuse。

## 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具。
> 搜推判定仅基于 prompt 中的子任务需求与你自身的 bot 候选清单知识进行;候选清单已在 prompt 给出,直接挑选,无需联网。
## 触发条件

收到 prompt 头部 `[task-search]` 标记的指令,且 prompt 含 `子任务需求+候选集{demand, catalog}` + 返回格式约定(详见框架 `SearchBasedDispatchStrategy._compose_search_prompt`)。

## 输入(prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `demand.node_id` | 待派发子任务节点 id |
| `demand.goal` / `demand.instruction` / `demand.acceptances[]` | 子任务需求 |
| `catalog[]` | 框架预查候选 bot 集:`{bot_id, bot_name, bot_desc, score, short_profile, reasons}`(按 score 降序) |

## 输出(返回格式约定)

返回 JSON 字符串,`outcome` 标 4 态之一(详见框架 `_parse_search_result`):

- **HIT_SINGLE**:`{"outcome":"HIT_SINGLE","bot_id":"<bot_id>"}`
- **HIT_GROUP**(已有群复用):`{"outcome":"HIT_GROUP","group_id":"<group_id>"}`
- **HIT_MULTI_BOTS**(动态拉群):
  ```json
  {"outcome":"HIT_MULTI_BOTS","bot_ids":[...],"collab_mode":"chat|manager_worker|state_machine",
   "group_name":"<群名>","manager_bot_id":"<manager>(manager_worker 必填)",
   "members_info":[{"bot_id":"...","role":"...","responsibility":"..."}],
   "definition_yaml":"<workflow yaml>(state_machine 必填)"}
  ```
- **MISS**:`{"outcome":"MISS","miss_reason":"<原因>"}`

> **bot_id / bot_ids 必须填 `catalog` 里的真实 `bot_id`**(剧本角色名 → 在 catalog 里按 `bot_name` 匹配,取该 bot 的 `bot_id`);catalog 中无对应角色名的 bot → 该候选不可用,所有候选都不匹配时返回 MISS。`start_run` 用此 bot_id 真实投递,**角色名不是 bot_id**。

## 确定式决策剧本(案例 gwqie46v7hzr1w6h)

> **强约束(确定性优先)**:严格按 `demand.node_id` 查下表返回对应 outcome —— **禁止自由组合候选 bot**。
> catalog 里下表未提及的 bot(如 `task-owner-bot`、与该 node_id 剧本无关的角色)是预查噪音,**必须忽略**,
> 不得因"多 bot 协作更全面"擅自改 `HIT_SINGLE` 为 `HIT_MULTI_BOTS`。表中角色名须在 catalog 里按
> `bot_name` 匹配真实 `bot_id`;匹配不到 → 该节点返 `MISS`(候选不匹配),**不得用其它 bot 顶替**。

按 `demand.node_id` 返回(knowledge 只在本 skill):

| node_id | 输出 outcome | 执行者/拉群 |
|---|---|---|
| `N_overview` | HIT_SINGLE | `行业信息抓取Bot` |
| `N_market` | HIT_MULTI_BOTS(manager_worker) | `市场需求分析Bot`+`资本市场投资Bot`,群名"存储行业市场发展趋势研究群",manager=市场需求分析Bot |
| `N_tech` | HIT_MULTI_BOTS(manager_worker) | `数据中心存储架构师`+`企业级SSD专家`,群名"存储技术发展总结和预测",manager=数据中心存储架构师 |
| `N_compete` | HIT_SINGLE | `存储行业供应链专家` |
| `N_customer` | HIT_MULTI_BOTS(manager_worker) | `ToG方案专家`+`ToB方案专家`+`采购决策专家`,群名"存储行业客户分析群",manager=ToG方案专家 |
| `N_practice_bbs` | HIT_SINGLE | `实践bbs专家Bot` |
| `N_report` | HIT_SINGLE | `报告聚合Bot` |
| `N_field_interview` | MISS | `miss_reason="候选 bot 均无法覆盖子任务需求(现场访谈无对应专家 bot)"` |
| `_remediate` 节点 | HIT_SINGLE | 对应原维度 bot(同主体) |
| 候选都不匹配 / 未知 | MISS | `miss_reason="候选 bot 均无法覆盖子任务需求"` |

> 协作群名 / 成员角色分工知识只在本 skill;`members_info` 承载 `{bot_id, role, responsibility}` 透传 BCS `participants[].role`。

## 确定式决策剧本(arch 场景:planning-arch 产出的 node_id)

`planning-arch` 通用规划产出的 arch 子任务 node_id(非 storage 案例),同样**按 `demand.node_id` 查下表**返回(同 storage 方式:严格按表,禁止自由组合候选 bot)。供 2-mode / 3-mode natural e2e 用。

| node_id | 输出 outcome | 执行者/拉群 |
|---|---|---|
| `N_tech_stack` | HIT_SINGLE | `技术栈概览Bot` |
| `N_dual_view` | HIT_MULTI_BOTS(manager_worker) | `业务架构视角Bot`+`数据架构视角Bot`,群名"业务与数据架构双视角分析群",manager=业务架构视角Bot |
| `N_architects` | MISS | `miss_reason="候选 bot 均无法覆盖子任务需求(架构师名册无对应现成 bot,走 BBS 中继)"` |
| `N_tech_stack_remediate` / `N_dual_view_remediate`(补救子,若出现) | HIT_SINGLE | 对应原 bot(同主体) |

> 同强约束:严格按 `demand.node_id` 查表;`bot_id`/`bot_ids` 须在 `catalog` 里按 `bot_name` 匹配取真实 `bot_id`,匹配不到 → 该节点 MISS(不得用其它 bot 顶替);catalog 里表未提及的 bot(owner、中继 bot 等)是预查噪音,必须忽略。
