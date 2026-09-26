# task-acceptance

任务目标驱动的**验收 + push 上报** skill,运行在 **协作群的 driver/owner bot**;协作群跑完叶子子任务并产出交付物后,driver/owner 自调本段判定是否达到该叶 `goal.acceptances`,并**主动 push 上报**到任务后端(参考 bbs 接力上报方式,不写死 url)。**single_bot 叶子不走本段**:由框架 `format_execute` 内联指示 worker 直接输出 JSON `{success,data,gaps}`,经 `TaskExecutorResultPoller` poll 收口 → `on_report`,不 push。

> 聚合节点 / 根节点验收 = planning 的 gap 计算(返回 `[]` = gap 闭 = 验收通过),由 owner bot 的 task-planning 承担,本段不参与聚合/根验收。

## 环境约束(必须遵守)

> **禁止联网搜索**。本 skill 运行在 teamclaw bot,无联网能力:不得调用任何 web_search / 联网检索 / 外部 HTTP 工具。验收判定仅依据协作群本次叶子产出、`goal.acceptances` 与上游上下文,结合自身知识判定;不得因"无法联网核实"而判 FAIL。
> **不写死 url**:后端 base url 与 loop_task_id 从协作群上下文/指令取(派发期注入群 context),不假设固定地址。

## 触发条件

你是协作群的 driver/owner bot,协作群已跑完叶子子任务并产出交付物;需按该叶 `goal.acceptances` 自验收并 push 上报结果。single_bot 叶子不命中本段。

## 输入(从协作群上下文/指令取)

| 字段 | 含义 |
|---|---|
| `{backend}` | 任务后端 base url(派发期注入协作群 context,不写死) |
| `{loop_task_id}` | `{task_id}::{node_id}`(派发期注入协作群 context,定位要回投的执行节点) |
| `goal.objective` / `goal.acceptances[]` | 该叶子子任务的目标与验收标准 |
| 协作群产出 | 协作群本次叶子执行产出的交付物(从群会话/最终输出取) |

## 执行步骤

1. **判定**:比对协作群产出与 `goal.acceptances[]`,得出 success 与 gaps(逻辑同 single_bot 自验收)。

2. **push 上报**:发 HTTP 请求

   `POST {backend}/api/v1/collaboration/tasks/callback/report`

   请求体(JSON 对象,不要输出 Markdown 代码块或额外解释):
   ```
   {"loop_task_id": "{loop_task_id}", "result": {"success": true|false, "data": {"result": "实际产出"}, "gaps": []}}
   ```
   - 验收通过 → `{"success": true, "data": {"result": "..."}, "gaps": []}`
   - 验收不通过 → `{"success": false, "data": {"result": "已有产出"}, "gaps": ["gap 描述"]}`
   - `success` 必须是 JSON bool;FAIL 的 `gaps` 必须为非空字符串列表(驱动 FAIL→补救链路);`data.result` 为实际产出内容;否则框架按 `terminal_result_invalid` 进入 Harness。

3. **收口**:HTTP 200 → 上报完成,框架经 `on_report` 写执行节点并翻态(`success=true`→DONE / `success=false`+非空 gaps→补救 / `result.exec_error`→harness 重投),本段结束。非 200 按下面幂等重试。

## 幂等与重试

`on_report` 按 `event_id`/结果摘要幂等:重复 push 同一结果不会重复翻态。网络抖动返回非 200 时,重发**同一请求体**即可(幂等保证不重复翻态);不要改换 success/gaps 重发。

## 与 bbs 接力上报的关系

本段 push 契约(`/callback/report` + `{loop_task_id,result{success,data,gaps}}`)与 bbs 接力上报方式一致(从消息/上下文取 `{backend}`,不写死);区别:bbs 走 `bbs/attach`+`bbs/result` 专属端点(中继 scoped 节点);本段走统一 `/callback/report`(协作群叶子节点,框架已建好节点,直接用 loop_task_id 定位回投)。

## single_bot 叶子(不走本段)

single_bot 叶子由框架 `format_execute` 内联指示 worker 直接输出 JSON `{success,data,gaps}`(经 poll 收口 → `on_report`),不命中本段、不 push。本段仅协作群 driver/owner 命中。
