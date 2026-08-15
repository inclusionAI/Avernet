---
name: task-acceptance
description: 叶子节点执行时的自验收判定(方案Y:worker bot 内部自闭环),把 success/data/gaps 折叠进 execute 回投 result。聚合/根节点验收走 planning gap 计算(本 skill 不参与)。
version: 1.0.0
author: avernet-task-framework
tags: [task, acceptance, verify]
---

# task-acceptance

任务目标驱动的**验收** skill,运行在 **worker bot**(**方案 Y**);叶子节点 execute 时 worker 内部自调判定,框架不感知、不主动 dispatch verify。判定结果折叠进 execute 回投 `result.success`/`result.data`/`result.gaps`,经 `TaskExecutorResultPoller → 翻译器 → TaskCallbackData → on_report` 翻态。

> 聚合节点 / 根节点的"验收"≠独立步骤,而是 **planning 的 gap 计算**(返回 `[]` = gap 闭 = 验收通过),由 owner bot 上的 `task-planning` skill 承担,本 skill 不参与聚合/根验收。

## 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 singlebox 本地 teamclaw bot,**无任何联网能力**:
> 不得调用任何 web_search / 联网检索 / 外部 HTTP 工具。
> 验收判定仅基于 worker 本次 execute 已产出的 `execute_output`、`goal.acceptances` 与上游 `sibling_outputs`,结合自身知识判定;不得因"无法联网核实"而判 FAIL。
## 触发条件

worker bot 收到 execute 指令(框架 `format_execute` 组装的 prompt:目标 `goal` + 指令 `instruction` + 上游产出 `sibling_outputs`),执行完子任务后**自调**本 skill 判定是否达到该叶节点的 `goal.acceptances`。

## 输入(执行完子任务后 worker 内部传入)

| 字段 | 含义 |
|---|---|
| `goal.objective` / `goal.acceptances[]` | 该叶子子任务自身的目标与验收标准 |
| `node_instruction` | 节点执行指令 |
| `sibling_outputs` | 上游兄弟产出(执行上下文) |
| `execute_output` | 本次 worker 执行产出的内容 |

## 输出(折叠进 execute 回投 result)

本 skill 不独立回投;判定结论折叠进 `TaskCallbackData.result`:

- 验收通过 → `result={"success": true, "data": {"result": "实际产出"}, "gaps": []}`
- 验收不通过 → `result={"success": false, "data": {"result": "已有产出"}, "gaps": ["gap 描述"]}`
  - `success` 必须是 JSON bool;FAIL 的 `gaps` 必须为非空字符串列表,驱动 `on_report` FAIL 链路。
  - 最终只输出 JSON 对象,不要输出 Markdown 代码块或额外解释;否则框架按 `terminal_result_invalid` 进入 Harness。

## 确定式判定剧本(案例 gwqie46v7hzr1w6h)

| 叶节点 | 默认判定 | FAIL 注入位(AC-5 治愈路径) |
|---|---|---|
| `N_overview` | PASS(产"行业全貌") | — |
| `N_market` / `N_tech` / `N_compete` / `N_customer` | PASS(产对应维度分析) | 可注入:`gaps=["tech 深度不足,缺 NAND 层数演进数据"]`(触发 N_tech 重试) |
| `N_practice_bbs` | PASS(产"一手实践") | — |
| `N_report` | PASS(产"尽调报告") | — |
| `N_<x>_remediate`(补救子) | PASS(产补救产出) | — |

> 当用例要验证 FAIL 治愈(AC-5),setup 时向对应叶节点的 worker bot 注入 FAIL 分支(本 skill 按 node_id 产非空 `gaps`);否则默认 PASS。
