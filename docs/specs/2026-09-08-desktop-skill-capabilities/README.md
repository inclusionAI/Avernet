# Desktop Skill 文档基线

本目录固定Desktop Skill适配的设计与交付范围，不包含功能实现。后续实施Issue应引用合入后的固定提交及本目录，避免依赖个人工作树或历史聊天。

## 阅读顺序

| 需要做什么 | 阅读材料 |
| --- | --- |
| 理解目标、接口、兼容边界与验收 | [spec.md](spec.md)：唯一实现规范入口，含A01–A34验收与Q1–Q29追溯。 |
| 理解一个决定的取舍和明确保留的限制 | [decisions.md](decisions.md)：定稿摘要，不包含被替代的历史候选。 |
| 划分跨仓工作、依赖和共享文件owner | [plan.md](plan.md)：G1–G4交付计划。 |
| 建立实施Issue、记录执行结果 | [tasks.md](tasks.md)：四组工作清单，当前均未开始。 |
| 检查市场/工坊/Bot能力是否有遗漏 | [capability-coverage-matrix.md](capability-coverage-matrix.md)：能力到工程包的映射。 |
| 查正式HTTP方法、路径和调用前置条件 | [openapi-capability-coverage-audit.md](openapi-capability-coverage-audit.md)：固定源码入口审计。 |
| 查SC懒物化、Reference完成与Desktop恢复的衔接 | [market-coverage-audit.md](market-coverage-audit.md)：市场专线源码证据。 |

## 基线与使用限制

- 文档PR起点：Avernet `github/dev@678be7ef0980aa00a09c32c502a08c33ebf4444d`。
- 创建文档PR时核对的OCB：`dev@384146cff15758dae2817e7069321b94bfdcee51`，实际`ocb-public` gitlink为`ca308268927f10df887ad3543d664244dbe6ce52`。
- 两份审计仍引用各自声明的固定研究SHA；不是将所有旧研究重新标成最新dev，也不是部署证据。实施前须再次核对相关调用方、DI、配置和实际gitlink。
- `spec.md`规定目标行为；审计描述当时已实现行为，不能将占位Adapter或未实现Handler当作已经交付。新用户决定需要同步规范和受影响测试。
- 不发布个人研究目录、原始长对话、凭证或现场运行数据。本文档基线自包含，关联的正式ADR已在仓库中。

文档PR合并只完成“规范可追溯”，不完成开发、CI验收、客户端/Engine升级或真实Desktop验证。四组工作按各自结果分别记录这些阶段。
