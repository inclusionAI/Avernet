# MEMORY.md

长期记忆重点：
- 店主明确确认的稳定经营目标、品质定义、授权边界和升级偏好。
- 已由各方确认的契约版本、变更原因和验收结果。
- 已验证有效的券结构、核销上限、补货触发线和异常处置规则。
- 被否决方案及原因，避免下一次重复提出。
- 每次活动的指标口径和数据截止时间。

当前任务私有记忆：
- `task_ref`、备用 `owner_private_session_id`、当前 manager-worker session ID、当前阶段、私有约束账本、`private_fields/private_literals` 和授权矩阵。
- 对外 `shared_brief`、各 worker 任务摘要、脱敏 decision_id 和契约版本之间的映射。
- 每个 required Worker 的真实 `dispatch_receipts`、`worker_output_retries`、`manual_dispatch_closed`、当前唯一未决 `decision_id`、`owner_confirmation`、授权包络、失效条件、`change_set`、carry-forward 证据、manager 生成的 `contract_version`、四项验收状态、one-shot `run_id` 与 terminal 状态。另记录 `schema_read_receipt={skill_path,reference_path,schema_path,read_at,last_heading}`、对最终 YAML/input 每次生成版本有效的 `one_shot_privacy_preflight`、`present_human_preflight`、`session_completion_lock=LOCKED|UNLOCKED`、`COMPLETION_PREFLIGHT`、同一 run 的服务端 `completion_evidence`，以及 `human_input_preflight={kind:human_input,assignee:null,binding_absent:true,assignee_bot_id:null}`。`completion_evidence` 必须包含 HumanInput judge outcome、accepted/blocked/changes marker 状态和 final output marker；没有工具回执的 task_id、无效业务卡、普通聊天同意、未绑定唯一 decision_id 的模糊回复、Manager 本地写入的完成时间或伪装成 Bot 的 HumanInput 不进入完成证据账本。
- 私有任务 JSON 只能镜像服务端事实，不能成为事实制造器；不得留下重复键，不得让 `one_shot_run.status` 与 terminal 证据冲突，也不得通过本地编辑把 skipped HumanInput、blocked judge 或失败 marker 改成 completed/accepted。
- 私有任务记忆只能由店长本地读取；新 session 需要私有值时按 `task_ref` 取回，不能把原值塞进群 context 作为传递手段。
- manager-worker 中店主直接提供的补充与审批仍归入私有账本；只把允许共享的操作事实或脱敏 decision_id 派发给对应 Worker。
- 当前任务状态以 `.merchant-private/tasks/<task_ref>.json` 为确定来源；语义记忆只作历史参考，不用于找回本轮私有约束或判断当前阶段。

记忆隔离：
- 成本、毛利底线、现金上限和店主私聊内容只用于商家内部判断，不写入 BCS 建群 context、session input、worker 任务、自定义协作定义或输入、公开契约和共享复盘。
- 复盘对外只保留可复用的方法、触发条件和经脱敏的结果。

不应记忆：
- 密钥、token、cookie、支付信息、顾客或员工个人数据。
- 未经确认的异常归因、平台承诺和供应商状态。
- 已失效的临时价格与库存，除非标明时间并作为复盘证据。
