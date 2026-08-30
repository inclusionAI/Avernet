# Specification

## Input
`template_id` 与 `input.okr`。Skill 不传 bot 列表或 group_id。

## Static plan
```text
risk_assessment ─┐
                 ├─> strategy_approval ─(approved=true)─> implementation
marketing_strategy┘
```

## Acceptance Ledger
| ID | 验收标准 | 验证 |
|---|---|---|
| AC-FIXED-001 | 模板 API 接收 template_id + input 并创建 Task | API 单测 |
| AC-FIXED-002 | 风险评估群与策略 Bot 并行派发 | Runtime 单测 |
| AC-FIXED-003 | 审核仅在两个前置节点 DONE 后启动，且收到两者输出 | Runtime 单测 |
| AC-FIXED-004 | 审核 approved=true 后启动实施；否则不启动 | Runtime 单测 |
| AC-FIXED-005 | 群运行时创建，bot_id 来自 YAML；空绑定明确拒绝 | Loader/API 单测 |
| AC-FIXED-006 | dynamic/workflow/yaml 旧路径行为不变 | 既有 Task 测试 |
| AC-FIXED-007 | 冲突群、共识群、BBS/自动研发不在本次实现 | 代码范围审查 |

## Failure semantics
模板不存在、输入不符合 schema、依赖循环、bot 绑定为空均失败且不创建可执行任务。执行回报继续走现有 callback → on_report 链路；重复回报不得重复推进同一节点。
