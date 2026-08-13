# MEMORY.md

## 长期记忆

- 店主明确确认的稳定经营目标、品质定义、授权边界和升级偏好。
- 已验收契约、被否决方案及原因、有效的触发线和复盘口径。
- 只保留可复用方法与脱敏结果；临时价格、库存和平台承诺必须带有效期。

## 当前任务私有账本

以 `.merchant-private/tasks/<task_ref>.json` 为唯一确定来源，记录：

- 原店主私聊 session ID、当前 manager-worker session ID 和阶段；
- `private_fields/private_literals`、授权矩阵、店主决定及 `OWNER_DECISIONS_FROZEN`；
- 知识快照、来源、有效期和各角色公开事实包；
- `dispatch_receipts`、格式重试次数、三张有效业务卡、owner 包络和失效条件；
- `manual_dispatch_closed`、当前版本/digest、issue ledger 与四项检查；
- 每版 `closed_issues/remaining_issues/execution_preconditions/monitoring_items`；
- privacy/schema receipts、one-shot run ID、terminal 与 completion evidence。

账本只能镜像真实工具和服务端事实，不得补造 task ID、judge outcome、HumanInput 回答或完成时间。普通聊天中的“继续/执行”只记录为启动授权，不能作为 run 内最终验收。

## 隔离与版本

- 私有账本只供店长本地读取，不经 group context、Worker 任务、YAML/input 或共享 artifact 传递。
- 状态机节点只接收公开候选和 `PRIVATE_FINANCIAL_CHECK=PASS|FAIL`。
- 每轮必须保存 contract version、revision digest、上游业务卡版本和 issue 状态；相同 version/digest 不得计作进展。
- `EXECUTION_PRECONDITION` 与 `MONITORING_ITEM` 进入 pending external actions，不得在记忆中提前标记为已执行。

## 不应记忆

- 密钥、token、cookie、支付信息、顾客或员工个人数据。
- 未确认的异常归因、平台承诺和供应状态。
- 已过期而未标明时间的价格、库存或排班。
