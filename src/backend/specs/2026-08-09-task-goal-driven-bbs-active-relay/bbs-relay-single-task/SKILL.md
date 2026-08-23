---
name: bbs-relay-single-task
description: BBS 接力单任务版:收到引擎主动通知(含内联任务态快照)后,据快照归纳剩余事项→attach→执行→result(dashboard 仅兜底)。
version: 1.0.0
author: avernet-task-framework
tags: [task, bbs, relay]
---

# bbs-relay-single-task

## 触发

收到引擎主动发的任务消息(含 task_id + backend base url + 自身 bot_id + **内联任务态快照** JSON)。
引擎已替你占根(bbs_owner已设为你的bot_id)——**不需要 scan、不需要 claim、不需要自判**。

## 执行步骤

### 步骤① 据消息内联快照归纳剩余事项(dashboard 仅兜底)

- 引擎发的任务消息已**内联任务态快照**(JSON):`goal`(objective+acceptances)、`instruction`、`background`、`done_children`(已 DONE 子节点+`output`)、`gaps`、`loop_round`、`task_id`/`node_id`。
- **直接据快照归纳"剩余事项"**:`剩余 = goal.acceptances 全集 − {done_children 的 output 并集}`,再按 `gaps` 细化;**正常路径不读 dashboard**。
- 仅当快照缺字段 / 字段不全时,才 `GET {backend}/api/v1/collaboration/tasks/dashboard?task_id={task_id}` 兜底补全。
- 自己组织 `task_spec`(`metadata{title, instruction}`, `context{background}`, `goal{objective, acceptances[]}`)覆盖你将要做的剩余子集。

### 步骤② attach(挂 scoped 节点)

- `POST {backend}/api/v1/collaboration/tasks/bbs/attach`
- body: `{"task_id": "{task_id}", "parent_node_id": "{root_node_id}", "task_spec": {你组织的}, "bot_id": "{你自身bot_id}"}`
- 200 → 读 `data.node_id`(你的 scoped 节点 id)
- 409 → 结束(不应发生,引擎已占根;若发生说明被释放,结束不重试)

### 步骤③ 执行

用自身能力执行 `task_spec.instruction`(产出对应 deliverable + acceptance 内容)。

### 步骤④ result(回投终态)

- `POST {backend}/api/v1/collaboration/tasks/bbs/result`
- body: `{"task_id": "{task_id}", "node_id": "{步骤②的node_id}", "bot_id": "{你自身bot_id}",
  "acceptance_result": {"verdict": "PASS", "acceptances_metric": [...]},
  "output_patch": {"{deliverable_key}": {产出}}}`
- 200 → 接力完成(框架经 on_bbs_report 收口)

## 环境约束

- `bot_id` 必须用消息中给的"你自身 bot_id",不用引擎账号。
- backend base url 从消息里取,不假设。
