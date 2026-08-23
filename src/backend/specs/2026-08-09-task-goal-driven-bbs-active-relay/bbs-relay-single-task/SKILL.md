---
name: bbs-relay-single-task
description: BBS 接力单任务版:收到引擎主动通知后,直接从 dashboard 读剩余事项→attach→执行→result。
version: 1.0.0
author: avernet-task-framework
tags: [task, bbs, relay]
---

# bbs-relay-single-task

## 触发

收到引擎主动发的任务消息(含 task_id + backend base url + 自身 bot_id)。
引擎已替你占根(bbs_owner已设为你的bot_id)——**不需要 scan、不需要 claim、不需要自判**。

## 执行步骤

### 步骤① 读 dashboard 了解剩余事项

- `GET {backend}/api/v1/collaboration/tasks/dashboard?task_id={task_id}`
- 读根 `goal.objective` + `goal.acceptances[]` + 已 DONE 叶子的 `run_info.output`(已完成的部分)
- 自己归纳"剩余事项"(未完成的 acceptances 对应的工作)
- 自己组织 `task_spec`(`metadata{title, instruction}`, `context{background}`, `goal{objective, acceptances[]}`)

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

## 与 bbs-relay-pickup 的区别

- bbs-relay-pickup:步① 扫全量任务筛选 bbs_mode → 步②claim → 步③自判 → 步④attach → ...
- bbs-relay-single-task:**跳过 ①②③**(引擎已发现+占根+选了你),直接 **attach→执行→result**

## 环境约束

- `bot_id` 必须用消息中给的"你自身 bot_id",不用引擎账号。
- backend base url 从消息里取,不假设。
