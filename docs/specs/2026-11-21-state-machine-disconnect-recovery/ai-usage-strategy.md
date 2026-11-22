# AI 使用策略报告

## 任务范围

本报告记录 Issue #2610（StateMachine 协作失败时可靠唤醒 manager）的 AI 辅助使用方式、人工判断边界和验证证据。AI 只辅助分析、实现和测试，不替代维护者对协议、数据安全和合入范围的判断。

## AI 辅助内容

1. **问题分析**：阅读 Issue、现有 StateMachine runtime、Send 队列、事件契约和主持人阶段机，梳理 Bot 断连、Run 终态、消息投递和自动重试之间的边界。
2. **方案设计**：提出使用一次性 Run 终态 checkpoint、CAS 终态转换、结构化原因类别和 metadata-only public event 的方案，并对照现有 TaskResult 语义限制 StateMachine 特有行为。
3. **代码实现**：辅助编写 WebSocket 断连入口、runtime/store 终态处理、事件契约、主持人恢复逻辑和测试；所有改动均限制在 Issue 规定的 BCS runtime、message flow、事件契约、主持人 Skill、文档和测试范围内。
4. **测试生成与诊断**：辅助补充并行节点、重复断连、终态结果持久化、checkpoint 重放、事件 metadata-only 和真实 MySQL 路径测试。

## 人工控制与安全边界

- 不向仓库写入 API Key、Token、Cookie、私钥或生产环境地址；运行所需凭据只通过本地环境注入。
- 不让 AI 直接决定数据库迁移、公开事件字段或消息投递身份；这些内容以仓库架构规则、Issue 验收标准和现有协议为准。
- 不把原始错误、Node artifact、玩家词语或模型内部内容放入 manager 消息和公开事件。
- 不根据相似命名自动匹配其它队伍实现；本提交独立完成 Issue 要求，不依赖其它队伍。
- 不通过放宽断言、跳过测试或增加无限重试来制造通过结果。

## 验证策略

- 使用 Rust 单元、Runtime progression、Store、Eventing、Service API 和 CLI E2E 测试验证行为。
- 使用 Event Catalog Python contract test 验证公开事件 schema、fixture 和 metadata-only 限制。
- 使用真实 MySQL Text/Prepared 路径验证终态 checkpoint contract。
- 使用 Singlebox 故障注入重复验证 Bot 被杀后的自动恢复，目标为 10/10 恢复、0 次卡死。
- 最终 Runtime progression suite 达到 270 passed、0 failed；已知迁移换行断言通过跨平台规范化修复。

## 人工复核清单

- [x] 变更范围与 Issue 边界一致。
- [x] 公开消息和事件不暴露内部错误全文或玩家内容。
- [x] 失败结果只在首次终态 CAS 成功时创建，并保持 checkpoint immutable。
- [x] 自动重试由主持人 Skill 按当前阶段决定，平台不替主持人推断阶段。
- [x] 已运行完整 Runtime progression、事件契约和目标模块检查。
- [ ] PR 创建后由维护者完成最终代码审查和合入决策。
