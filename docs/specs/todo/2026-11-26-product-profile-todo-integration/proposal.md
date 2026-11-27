# Todo 产品层接入 ProductProfileService Proposal

> 状态：Confirmed / implementation entry
> 日期：2026-11-26
> Dima：2026111900120790386、2026111900120789898、2026112400120935398

## 1. 背景

现有 Todo 已具备 Dima 来源、BotTeam facade、固定 Revision/Stage 快照和 Legacy Context 执行链路，但产品模型尚未与 Product Runtime 的 Profile、Collet、Rollout、互动闭环连接。用户需要在 Todo 内完成从外部工作项导入、按小队执行、查看真实进度、接收 Human 待处理通知并互动的完整流程。

## 2. 目标

1. BotTeam Revision 发布前创建并固定绑定 ProductProfile；
2. Dima 单向导入 Todo 后创建并固定绑定 Product Collet；
3. 以 Todo Stage 快照为骨架投影 Product Rollout/Session Messages；
4. 复用站内通知基础设施触达负责人，并在 Todo 内完成受控 Product Interaction。

## 3. 范围

- Backend：Revision/Profile 和 Todo/Collet nullable binding、CAS、原子来源落库、runtime 分流、Progress VO、Product Interaction、Todo Notification adapter、HTTP/DI/tests；
- Frontend：稳定 Progress/Interaction 产品 DTO、详情展示、发送锁、停止状态和降级态；
- Database：只新增 nullable binding 和必要 Product Interaction 存储，不回填历史数据。

## 4. 非范围

- 不实现或修改 ProductProfileService/L5 Methods；
- 不把单 Bot或历史 Context Todo 迁入 Product Runtime；
- 不实现 Dima 双向同步、BotTeam DAG、钉钉通知或 Runtime Worker；
- 不将协议 DTO 直接暴露给 Frontend。

## 5. 决策

- BotTeam/Todo 继续是产品所有者，Profile/Collet 仅保存引用；
- 新 Revision/Profile 与新 Product Todo/Collet 使用确定 request ID 和本地 CAS；
- Product 与 Legacy 运行模式集中判定，矛盾绑定 fail closed；
- Todo Stage 是稳定展示骨架，Rollout 只补充运行事实；
- Product Interaction 使用本地可追溯事实和 single-flight；
- 协议或后台 Actor 合同无法从代码确认时，记录缺口并跳过该自动化分支，不伪造身份或协议。

## 6. 风险与恢复

- 外部协议成功、本地写入失败：复用相同 request ID 补偿并 CAS；
- Progress/通知失败：保留产品事实并降级，不能伪装未开始或回滚 Runtime；
- interactive 超时：进入 delivery_unknown，复用同 interaction ID补偿；
- 历史数据：nullable、无回填、Legacy 行为保持。

## 7. 验收与验证

权威验收点为 `spec.md` 的 AC-001～AC-014；设计覆盖见 `design.md`，实现切片和命令见 `tasks.md`。最终分别报告通过、失败、未运行、跳过的合同缺口和剩余风险。
