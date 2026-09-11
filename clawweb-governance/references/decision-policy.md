# Governance 决策规则

## 1. 两个分离的判断

不要把“是否应该创建”和“创建后有多急”混成一个分数。

### Eligibility Confidence：是否创建

主要证据：

- 独立 Session 与跨日/跨时段跨度；
- 主根因一致性；
- Judge 与真实工具返回是否一致；
- 当前 NAS 配置是否仍能复现缺陷；
- Owner 是否能通过 Bot 配置、Skill、MCP、工作流或业务规则推进；
- 失败后是否已经发布或成功恢复；
- 是否已有同根因改进项。

### Priority：治理收益排序

主要指标：

- 失败 Task 数；
- 独立失败 Session 数；
- 能力失败率及对总体成功率的影响；
- 最近窗口数量与趋势；
- 生产服务/高保 Bot 权重；
- 影响时长。

## 2. 跨度与数量

- **跨度用于识别持续性**：至少跨两个活跃日、跨 24 小时再次出现，或在失败后成功恢复前持续复现。
- **数量用于衡量收益**：数量越大、失败率越高，修复后对总体成功率提升越明显。
- 同一天大量 Debug 错误本身不能证明应治理；若之后已经成功且当前配置正常，进入 `WATCH/DROP`。
- 同一天大量错误但 NAS 证明当前缺陷仍存在，可创建候选。
- 生产服务/高保 Bot 的严重单点可降低跨度门槛，但必须有明确当前缺陷或高风险证据。

## 3. 根因粒度

候选键：

```text
user_id + bot_id + failure_class + canonical_root_cause
```

根因优先从确定性字段抽取：

```text
component_type + component_name + error_code + missing_resource/invalid_parameter
```

再用 3～5 个跨时间代表 Session 做语义确认。不要使用 `bot_id`、`error`、`failed` 等通用词作为根因锚点。

## 4. 平台共因

跨 Bot 同根因只增加 `crossBotHint`，不阻止创建具体 Owner 候选，也不自动转走平台队列。每个 `user_id + bot_id` 独立判断和指派，让明确的处理人推进。

在建议中注明：

```text
疑似同类问题覆盖多个 Bot，Owner 可先完成当前 Bot 修复，并将平台侧证据反馈给管理员。
```

## 5. 自动修复：CREATE_AUTO / DIRECT_EVOLUTION

**前置步骤**：先查 [fix-patterns.md](fix-patterns.md) 已知修复模式库。命中则直接用预定义的 actionType + 修复模板；未命中再按以下条件推理。

NAS 深查后同时满足以下条件即可选择自动修复：

- 当前缺陷仍存在；
- 修复目标明确到文件、Skill、MCP 或工作流配置；
- 方案不依赖新凭据、权限审批或业务判断；
- 修改范围只涉及目标测试 Bot/受控工作区；
- 可生成具体修改计划；
- 可回滚；
- 有明确验证方式；
- 不修改服务 Bot，除非后续流程获得用户明确授权。

典型自动修复：

- 修正 Skill 指令或路径；
- 补充已存在且允许加载的 Skill；
- 修正 `TOOLS.md`/`AGENTS.md` 的确定性规则；
- 修正工作流参数、工具参数模板、输出校验；
- 修复明确的非敏感配置键。

自动修复结论必须写出：目标、预期改动、风险、回滚方式和验证计划。

## 6. 手动修复：CREATE_MANUAL / ASSIGN_OWNER

以下情况使用手动修复：

- 需要凭据、Token、网络或权限授权；
- 涉及业务规则和产品取舍；
- 需要 Owner 选择数据源、审批人、流程或目标能力；
- NAS 只能确认问题，不能形成确定修复方案；
- 修改可能影响服务 Bot 或多人共用能力；
- 自动执行风险不可控。

## 7. WATCH / DROP

`WATCH`：样本不足、跨度不足、刚发布新版本、需要观察后续 Session。

`DROP`：Judge 误判、已恢复、用户预期失败、无用户可行动项、已有相同候选、Evidence 不一致。

## 8. 去重检查（防止重复打扰）

提交候选前，必须查询 ClawWeb 中同一 `ownerUserId + botId` 过去 15 天的改进项记录。

去重判断（按优先级）：

1. 同 `canonical_root_cause`（failure_class + component_type + component_name 一致）且 `adminReviewStatus = REJECTED` → **DROP**，不重复提交。在 assignmentReason 中记录驳回原因和日期
2. 同根因且 `status = PENDING_ADMIN` → **DROP**，等待现有审批结果
3. 同根因且 `status = IN_PROGRESS` → **DROP**，已有在处理中
4. 同根因且 `status = RESOLVED`，但 ODPS 近期仍有同类失败 → 可提交，但需在 assignmentReason 中注明"历史项已关闭但问题复现，建议复查"

相似度判断：优先比较 canonical_root_cause 中的结构化字段，而不是整段文本相似度。PATTERN 匹配为备选。

查询方式见 [api.md](api.md) 第 2 节。

## 9. 写入前硬校验

- Evidence 来自同一目标 Bot；
- 已通过去重检查（无同根因 REJECTED/PENDING_ADMIN/IN_PROGRESS 项）；
- `sessionId + taskIndex` 在 PRE/目标环境真实存在；
- 标题不是失败分类复述；
- 根因与建议由 Evidence 和当前配置支撑；
- `DIRECT_EVOLUTION` 有受控方案；
- `ASSIGN_OWNER` 有具体处理原因；
- 固定幂等键由候选稳定键生成；
- API 返回必须为 `PENDING_ADMIN/PENDING`。
