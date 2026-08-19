# 闭环调试 Workflow Spec

## 目标

将 clawmind-workflow 的 loop module 转为引擎确定性编排的 workflow。引擎管循环，LLM 只做单轮任务。

## 输入参数

| 参数 | 必填 | 说明 |
|------|------|------|
| workflowId | ✅ | 目标 workflow ID 或 facade 名 |
| examples | ❌ | 业务示例文件路径或内联 JSON |
| 其他 `--key value` | 透传 | 传递给目标 workflow 的业务参数 |

## 流程设计

```
P0: version-check → 远端/本地版本检查
    ├─ error → version-error (workflow 不存在等)
    ├─ auto_sync → version-auto-sync → validate
    ├─ confirm_diff → version-confirm (human) → version-apply → validate
    └─ none → validate
    validate → dry-run 静态校验
    ├─ fail → validate-fail (结构性错误，需人工修)
    └─ ok ↓

P2: debug-loop (loop-group, until: passed=true)
    ├── run-and-judge: 启动 → 轮询 → 分析 → 示例比对 → 判定
    └── fix-yaml: fix-only 修复 (仅 fix_needed 时)

P3: report → 生成运行报告 → 结束
```

## 判定规则

| 问题类型 | 处理 |
|---------|------|
| 全 succeeded + 示例全 PASS | passed=true，循环退出 |
| 代码/配置问题（prompt错、模板变量错、dependsOn错、示例MISMATCH） | fix_needed=true，fix-yaml 自动修复 |
| 权限/工具/MCP 缺失 | needs_human=true + passed=true，循环立即退出，report 输出 status=needs_human |
| 429限流/网络抖动 | 不修 YAML，下一轮重试 |
| 静态校验失败 | 需人工修，不进入循环 |

## report 输出状态

| status | 含义 | 后续 |
|--------|------|------|
| passed | 调试通过 | skill 层引导用户确认是否 deploy |
| needs_human | 工具/权限/MCP缺失 | skill 层输出错误信息和修复指引 |
| exhausted | N轮未通过 | skill 层输出每轮摘要，用户决定下一步 |

## 红线

- ❌ 任何节点调用 deploy 命令
- ❌ fix-yaml 重新生成 spec.md
- ❌ 工具/MCP/权限问题自行绕过（必须 needs_human 退出）