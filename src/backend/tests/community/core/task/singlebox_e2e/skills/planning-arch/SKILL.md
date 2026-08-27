---
name: task-planning-arch
description: 计算任务 gap 并产出下一步可执行子任务 List[TaskSpec];gap 已闭返回空数组。对齐 arch 场景(架构师名册/技术栈概览/双视角分析)确定式分解——按根目标交付物集合 + done_children 查表(参照 task-planning storage 特例,非自由 LLM 分解)。
version: 1.0.0
author: avernet-task-framework
tags: [task, planning, decompose]
---

# task-planning-arch

任务目标驱动的**任务规划** skill,运行在 **owner bot**(owner_bot_id)。框架投递 planning prompt
(prompt 含 `{goal, context, target_node, graph_snapshot, gaps}` + 返回格式约定;详见框架
`GapBasedPlanningStrategy._compose_planning_prompt`),本 skill 读 prompt 中的目标节点 `node_id`,
按 **arch 场景确定式剧本**产出下一批子任务——参照 `task-planning` 的 storage 特例:**按根目标的验收交付物集合
+ `done_children` 查表**,不靠自由 LLM 分解(避免拆子数/拆法飘忽)。

## 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具;不得在 instruction 中要求子任务联网查资料。
> 一切判断基于 prompt 中已提供的 `{goal, context, snapshot, done_children, gaps}` 与你自身知识进行,
> 缺数据用合理假设/占位补全并标注。

## 触发条件

收到 prompt 头部 `[task-planning]` 标记的指令,且 prompt 含 `目标节点 node_id=...` 与 `任务态快照{...}`。

## 输入(框架组装,prompt 内嵌)

| 字段 | 含义 |
|---|---|
| `node_id` | 当前计算 gap 的目标节点(node_id=... 形式;根 task_id 由服务端生成,不作为场景判据) |
| `goal.objective` / `goal.acceptances[]` | 节点自身目标与验收标准 |
| `context.background` | 任务背景 |
| `gaps` | 上一轮验收 FAIL 的 gaps(补救规划时非空) |
| `graph_snapshot.loop_round` | 当前 BBS 上升轮次 |

## 输出(返回格式约定)

返回 JSON 字符串,结构为对象 `{"tasks": List[TaskSpec], "has_gap": bool, "gap_detail": str}`:

```json
{"tasks": [{"metadata": {"task_id": "<子节点node_id>", "title": "<标题>", "instruction": "<指令>"},
             "context": {"background": "<背景>", "extend_props": {}},
             "goal": {"objective": "<目标>", "acceptances": [{"id": "<ac_id>", "description": "<描述>"}]}}],
 "has_gap": true,
 "gap_detail": ""}
```

- `tasks` = 下一批可执行子任务;`metadata.task_id` 即子节点 `node_id`(须唯一,不与已存重复);
- gap 已闭(验收通过)→ `{"tasks": [], "has_gap": false, "gap_detail": "done"}`;
- 有 gap 但拆不出子 → `{"tasks": [], "has_gap": true, "gap_detail": "<原因>"}`;
- `done_children` 已列出已 DONE 子节点及产出,据此不重复产已 DONE 的。

## 确定式分解剧本(arch 场景:按根目标交付物集合 + `done_children`)

框架二轮起 target 恒为根(根从初始规划后一直 PLANNING)。按根 `goal.acceptances` 的**交付物集合** + 快照 `done_children`
(已 DONE 子节点 + 其 `output` 产出)联合返回下一批:

### 单一交付物:架构师名册(预期 MISS→升 BBS 中继)

| 目标 node_id | done_children | 返回 children |
|---|---|---|
| 根验收仅含架构师名册 | `[]`(初始) | `[N_architects]`  <!-- 无 bot → MISS@MAX→升 BBS,金庸中继收口 --> |
| 根验收仅含架构师名册 | done 产出**已含架构师名册**(可能来自 `run_mode=="bbs"` 中继 scoped 节点) | `[]`(`has_gap=false`,gap 闭) |
| 根验收仅含架构师名册 | 仍缺架构师名册 | `[]`(`has_gap=true`,`gap_detail="缺架构师名册"`,等 BBS 中继) |

### 两份交付物:技术栈概览 + 架构师名册

| 目标 node_id | done_children | 返回 children |
|---|---|---|
| 根验收含技术栈概览 + 架构师名册 | `[]`(初始) | `[N_tech_stack, N_architects]`  <!-- tech_stack 命中 bot(single_bot);architects MISS→BBS --> |
| 根验收含技术栈概览 + 架构师名册 | done 产出**已含技术栈概览 + 架构师名册** | `[]`(`has_gap=false`,gap 闭) |
| 根验收含技术栈概览 + 架构师名册 | 仍缺任一份 | `[]`(`has_gap=true`,`gap_detail="<缺哪份>"`) |

### 三份交付物:技术栈概览 + 业务/数据双视角分析 + 架构师名册

| 目标 node_id | done_children | 返回 children |
|---|---|---|
| 根验收含技术栈概览 + 双视角分析 + 架构师名册 | `[]`(初始) | `[N_tech_stack, N_dual_view, N_architects]`  <!-- tech_stack single_bot;dual_view coop_group;architects MISS→BBS --> |
| 根验收含技术栈概览 + 双视角分析 + 架构师名册 | done 产出**已含技术栈概览 + 双视角分析 + 架构师名册** | `[]`(`has_gap=false`,gap 闭) |
| 根验收含技术栈概览 + 双视角分析 + 架构师名册 | 仍缺任一份 | `[]`(`has_gap=true`,`gap_detail="<缺哪份>"`) |

### FAIL+gaps 叶补救 / 其它

| 情形 | 返回 |
|---|---|
| target=FAIL 叶节点且 `gaps` 非空(补救规划) | `[N_<叶>_remediate]`(按 gaps 描述产 1 个补救子) |
| 其它 / 无可规划 | `[]` |

### 子任务规格(固定 node_id + task_spec)

```json
[
  {"metadata": {"task_id": "N_tech_stack", "title": "基础架构方向技术栈概览",
                "instruction": "给出某某某公司基础架构方向的技术栈概览:列出计算/存储/网络等分层与每层核心组件,简要说明。基于自身知识即可,不联网。"},
   "context": {"background": "某某某公司基础架构方向技术栈梳理", "extend_props": {}},
   "goal": {"objective": "产出基础架构方向技术栈概览(分层+核心组件)",
            "acceptances": [{"id": "ac_tech", "description": "给出基础架构方向技术栈概览(计算/存储/网络等层与核心组件)"}]}},
  {"metadata": {"task_id": "N_dual_view", "title": "业务架构与数据架构双视角深度分析",
                "instruction": "从**业务架构与数据架构双视角**深度分析某某某公司基础架构的现状与演进(需业务架构、数据架构两个视角的专家协作完成)。基于自身知识即可,不联网。"},
   "context": {"background": "某某某公司基础架构双视角分析", "extend_props": {}},
   "goal": {"objective": "从业务架构与数据架构双视角深度分析基础架构现状与演进",
            "acceptances": [{"id": "ac_dual", "description": "从业务架构与数据架构双视角深度分析基础架构现状与演进"}]}},
  {"metadata": {"task_id": "N_architects", "title": "基础架构方向架构师名册",
                "instruction": "整理某某某公司基础架构方向的 3 位核心技术架构师,给出每位架构师的姓名/角色 + 主要职责。基于自身知识即可,不联网。"},
   "context": {"background": "某某某公司基础架构方向架构师梳理", "extend_props": {}},
   "goal": {"objective": "整理基础架构方向 3 位核心架构师(姓名/角色 + 职责)",
            "acceptances": [{"id": "ac_arch", "description": "给出基础架构方向 3 位架构师的姓名/角色 + 职责"}]}}
]
```
> 各 case 初始批次按根验收交付物集合取其中若干:仅架构师名册→`[N_architects]`;技术栈概览+架构师名册→`[N_tech_stack, N_architects]`;
> 三份交付物齐全→`[N_tech_stack, N_dual_view, N_architects]`。

> **gap 闭判据(二轮 owner 复核根 gap 用)**:读 `done_children[].output`——若各交付物(技术栈概览 / 双视角分析 /
> 架构师名册,架构师名册可能由 `run_mode=="bbs"` 中继 scoped 节点的 `architects` 产出)均已出现 → `has_gap=false`
> 收口;任一缺失 → `has_gap=true`。不重复产已 DONE 的;`N_architects` 被 BBS recover 清掉后由中继 scoped 节点补其产出。
> 节点名由本 skill 决定,**框架代码零 case 知识**(框架 grep 不得出现这些字面量,知识只在本 skill)。