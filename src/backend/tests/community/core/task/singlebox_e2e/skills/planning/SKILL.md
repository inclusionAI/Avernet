---
name: task-planning
description: 计算任务 gap 并产出下一步可执行子任务 List[TaskSpec];gap 已闭返回空数组。对齐案例剧本 gwqie46v7hzr1w6h(存储行业尽调)确定式分解。
version: 1.0.0
author: avernet-task-framework
tags: [task, planning, decompose]
---

# task-planning

任务目标驱动的**任务规划** skill,运行在 **owner bot**(source_channel_id)。框架投递 planning prompt(prompt 含 `{goal, context, target_node, graph_snapshot, gaps}` + 返回格式约定;详见框架 `GapBasedPlanningStrategy._compose_planning_prompt`),本 skill 读 prompt 中的目标节点 `node_id`,按案例剧本确定式产出下一批子任务。

## 触发条件

收到 prompt 头部 `[planning]` 标记的指令,且 prompt 含 `目标节点 node_id=...` 与 `任务态快照{...}`。

## 输入(框架组装,prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `node_id` | 当前计算 gap 的目标节点(node_id=... 形式) |
| `goal.objective` / `goal.acceptances[]` | 节点自身目标与验收标准 |
| `context.background` | 任务背景 |
| `gaps` | 上一轮验收 FAIL 的 gaps(补救规划时非空) |
| `graph_snapshot.loop_round` | 当前 BBS 上升轮次 |

## 输出(返回格式约定)

返回 **`List[TaskSpec]` 的 JSON 字符串**(对齐领域模型 `Metadata/Context/Goal/AcceptanceCriteria`):

```json
[{"metadata": {"task_id": "<子节点node_id>", "title": "<标题>", "instruction": "<指令>"},
  "context": {"background": "<背景>", "extend_props": {}},
  "goal": {"objective": "<目标>", "acceptances": [{"id": "<ac_id>", "description": "<描述>"}]}}]
```

- `metadata.task_id` 即子节点 `node_id`(须唯一,不与已存重复);
- gap 已闭(验收通过)→ 返回 `[]`;
- 子任务 `goal.acceptances` 为该子任务自身的验收标准;无独立标准可继承父 goal。

## 确定式分解剧本(案例 gwqie46v7hzr1w6h)

按目标 `node_id` 返回下一批子节点(knowledge 只在本 skill,框架不含):

| 目标 node_id(或来源) | 返回 children |
|---|---|
| `t_case`(根,初始规划) | `[N_overview]` |
| `N_overview`(PASS 后二轮) | `[N_market, N_tech, N_compete, N_customer]` |
| `N_market` / `N_tech` / `N_compete` / `N_customer`(全 DONE 后) | `[N_practice_bbs]` |
| `N_practice_bbs`(PASS 后) | `[N_report]` |
| `N_report`(全 DONE 后) | `[]`(根级终验 gap 闭) |
| FAIL+gaps 叶节点 | `[N_<叶>_remediate]`(按 gaps 描述产 1 个补救子) |
| 其它/状态非可规划 | `[]` |

> 节点名由本 skill 决定,**框架代码零 case 知识**(框架 grep 不得出现这些字面量)。
