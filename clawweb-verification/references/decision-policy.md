# Verification 判断规则

## 1. 验收对象

验收对象分为两类：

```text
标准验收：status = IN_PROGRESS 且 handledAt 非空
主动验收：status = ACTIVE，或 status = IN_PROGRESS 且 handledAt 为空
```

修复方式可以是自动或手动，验收标准一致：修复后的真实运行是否解决原规范化根因。主动验收对象必须来自 `verification-candidates/open`，标准验收对象必须来自 `verification-candidates`。

## 2. 证据窗口

- 起点：`handledAt`，不是候选创建时间或 Admin 批准时间。
- 标准验收的最短观察窗口为 **2 天**；未满 2 天即使已有新 Session，也不能提交 `DISAPPEARED`。
- 只统计该时间之后的新 Session。
- Session 必须属于目标 `ownerUserId + botId`，并覆盖所有 `_DEVICE-*` 与 Agent 变体。
- 离线 ODPS 水位可能落后；NAS mtime 和 Session 内时间戳用于当前验收。
- 文件 mtime 只用于召回，最终要检查 Session 事件或消息时间，避免复制/迁移文件造成误判。

## 3. 相关 Session

相关性至少满足一项：

- 执行同类任务；
- 使用同一 Skill/MCP/工具/工作流节点；
- 命中同一配置路径；
- 可能触发原根因的等价场景。

无关 Session 不计入 `newSessionCount`，也不能证明问题消失。

## 4. DISAPPEARED

同时满足：

- 至少一个真实相关新 Session；
- 修复目标已应用；
- 新 Session 没有出现同一规范化根因；
- 任务完成或已经越过原失败节点；
- 没有用静态配置、Evolve 状态或 Agent 自述代替运行证据。
- 标准验收还必须满足：当前时间距离 `handledAt` 至少 2 天。

对于高风险或生产服务 Bot，可按 Governance Candidate 的 `verificationPlan` 要求更多 Session 或更长观察窗口。

## 5. STILL_PRESENT

出现以下任一情况：

- 同一组件和错误再次出现；
- 表面错误文案变化，但根因路径未变化；
- 修复未真正应用到运行实例；
- 新 Session 仍停在原失败节点；
- 自动修复造成等价的新错误，核心任务仍无法完成。

### 5a. STILL_PRESENT 对 IN_PROGRESS 项

保持 `IN_PROGRESS`，UI 表示”验收未通过”，等待继续修复。

如果原 `actionType=DIRECT_EVOLUTION`（自动修复失败），应在提交时携带 `overrideActionType=ASSIGN_OWNER`，将修复方式改为手动。这样用户看到的是”自动修复未生效，请手动处理”，而非继续等待自动修复。

### 5b. STILL_PRESENT 对 RESOLVED 项（假修复检测）

> ✅ 已实现（2026-08-19）

主动验收时发现 RESOLVED 项的问题实际未消失（假修复），提交 STILL_PRESENT 后 ClawWeb 将该项**重新打开为 ACTIVE**。用户重新看到待修复项，可以再次进入手动或自动修复流程。

## 6. INSUFFICIENT_DATA

以下情况不能关闭：

- 没有修复后新 Session；
- 只有无关 Session；
- 新 Session 还在运行或结果截断；
- NAS 与 ODPS 时间不一致，无法确认发生时间；
- Evidence 不完整；
- 原根因不能可靠映射到新 Session。

## 7. 同类根因比较

优先比较：

```text
failure_class
component_type
component_name
error_code
missing_resource / invalid_parameter
failure_stage
user-visible impact
```

不要仅用整段文本相似度，也不要把任意新失败视为原问题复现。

## 8. 写回前检查

- 再次 list，取得最新 `version`；
- 核对目标仍在候选列表；
- `newSessionCount` 等于实际检查并确认相关的 Session 数；
- `lastRecurrenceAt` 只在确实复现时填写；
- `DISAPPEARED` 必须 `newSessionCount >= 1`。
- 标准验收确认消失前，`handledAt` 至少经过 2 天；主动验收确认消失前，修复观察边界至少经过 7 天。服务端会再次校验这两个时间窗口。

## 9. 主动验收：检查已标记完成的改进项

针对已 RESOLVED/VERIFIED/AUTO_VERIFIED 的项，定期通过 NAS + ODPS 交叉验证是否真的修复。

## 10. 主动验收：用户未回传状态的开放项

对于 `ACTIVE`，或 `IN_PROGRESS` 但没有 `handledAt` 的开放项，修复观察边界优先取项目的 `gmtModified`，必要时取 NAS/Evolve 能确认的更晚修复时间。只有连续观察满 **7 天**且没有同一规范化根因复现，才允许通过主动结果接口提交 `DISAPPEARED`。未满 7 天时保持原状态，不能以“最近没有看到失败”直接关单。

开放项的接口只允许操作仍处于 `ACTIVE` 或未回传 `handledAt` 的 `IN_PROGRESS` 项；服务端会再次校验 7 天窗口，提前提交返回 `409 OPEN_VERIFICATION_TOO_EARLY`。

### 证据来源

| 来源 | 用途 |
|------|------|
| NAS Session JSONL | 读取 resolvedAt 之后的新对话，检查同类任务中是否仍出现原根因错误 |
| ODPS Judge 表 | 批量筛查同一 Bot 在 resolvedAt 之后的能力类 failure，确认无同类失败 |
| ODPS session_full_di | 查 full_message 确认 Bot 后续运行是否正常 |
| NAS 当前配置 | 确认修复中承诺的配置变更已实际落盘（Skill/MCP/AGENTS.md） |

### 判定规则

1. **确实已修复**：resolvedAt 之后 ≥1 个相关新 Session + 无同类根因 + 配置已更新 → 保持 RESOLVED，不操作
2. **假修复/问题复现**：新 Session 中出现同一 `failure_class + component_type + component_name` 错误 → 判定 STILL_PRESENT，标注 `lastRecurrenceAt`。改进项回退到需要重新处理
3. **无法确认**：无新 Session 或 Session 不相关 + 配置未变化 → INSUFFICIENT_DATA，保持当前状态，等待更多证据
4. **配置回滚**：修复承诺的配置变更不存在或被回滚 → STILL_PRESENT

### 执行频率

建议每天对过去 30 天内标记为完成的改进项执行主动验收。优先验收高风险 Bot 和 DIRECT_EVOLUTION 项（自动修复更可能出错）。
