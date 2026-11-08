# 工作流跨版本复现与进化指标 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 原地逐任务实施；不派发子任务、不另开会话。本文不授权真实分析、试跑、数据回填或部署。

**Goal:** 按问题和真实运行版本显示复现、样本覆盖、修复观察与整合质量的事实指标。

**Architecture:** 扩展已有 Workflow repositories、verification 和 issue视图，复用现有运行/分析/发布记录及核心计划的snapshotKey。不增加独立事件采集、时序数据库或模型归因服务。

**Tech Stack:** 现有 TypeScript SQL repositories、Vitest、React 查询与问题组件。

**Spec:** [spec.md §8](../specs/2026-10-19-workflow-versioned-evolution.md#8-配套指标r7扩展交付)。前置：[核心计划](../plans/2026-10-19-workflow-versioned-evolution.md)任务1–3；发布效果沿用任务4–6的精确目标绑定。

状态：2026-10-19 按投入范围复审；仅文档。路径前缀：`CW=apps/evolverun/clawweb`、`WF=$CW/public/modules/workflow`、`EV=$CW/public/modules/clawevolve`，均为所属仓库相对路径。

首批扩展优先回答“哪个版本出现多少次、分析了多少次、新版有没有再出现”。修复观察只读取已有发布/验证结论，不修改自动验证算法；处理周期、连续复现等M3指标是后续备选，不默认要求一次做完，也不阻塞核心交付。

## Global Constraints

- 时间以运行发生时间为准；同一问题同一flowId重复分析不重复计数。
- 正式运行、保存测试、未知来源分别统计，不用version=-1直接代替trial身份。
- completed分析与证据完整分开；命中率带分母；没有样本不判已解决。
- 复现按稳定问题signature，不把同类症状自动当作相同根因。
- 沿用既有验证阈值/规则；无规则只展示事实；不得以版本前后差值宣称因果。
- 新源码≤1000行；不复制统计业务到Host；无线上副作用。

## Review Focus

1. 同flow多诊断、重试/迟到分析重复累计：任务M1。
2. 相关节点未走到、分析缺失、样本为零：任务M1/M2。
3. 新部署、回滚、保存测试与版本身份缺失：任务M1/M2。
4. 相同签名但证据根因不同，或节点身份不可比：任务M2只报同类复现，不能推断同根因。
5. 发布完成即标修复、样本不足却绿灯、聚合缩得越少越好：任务M2/M3。

## Task M1：按版本的问题样本与覆盖（第一批）

**Files**

- EV/server/repositories/issue-aggregation-repository.ts：版本范围与问题运行集合。
- WF/server/repositories/evolution-metrics.ts、services/repair-verification-service.ts：复用已有按部署查询，避免新建重复聚合源。
- Create WF/server/contracts/issue-version-metrics.ts、repositories/issue-version-metrics-repository.ts；对应tests。
- 问题read API/视图：只扩展已存在的版本问题详情，不为每个数字加独立请求。

**Interfaces**

`IssueVersionMetrics` 包含 snapshotKey、窗口from/until、distinctRuns、eligibleRuns、analyzedRuns、completeEvidenceRuns、eligibleAnalyzedNodeRuns、affectedRuns、eligibleAffectedRuns、firstSeenAt/lastSeenAt；计数与时间区分真实0、未知/无样本。

分析覆盖分母为范围内符合分析条件的运行；命中率分母为已具备可用分析且实际执行相关节点的运行，分子eligibleAffectedRuns仅取该集合内命中，二者不能互换。全部已证实影响affectedRuns另列，不能拿它除以更小的合格观察集合。响应同时提供excludedUnknown与sourceKind，前端不自行猜口径。

同flow按来源快照与统计截止点内最后一次成功完成的兼容分析取一份，以completedAtMs、analysisId稳定排序。响应同时固定运行窗口与analysisCutoff；迟到分析只有截止点推进后才更新。最新结果为partial时仍单列其缺口，不用旧完整结果静默拼接成最新完整分析。整体证据完整数与该问题/节点的可判定数分别计算；不可判定的partial不进入该问题命中率分母。

- [ ] 写失败回归：一个flow五条诊断计一次；同flow重复/迟到分析不增运行数；固定截止点结果可重现；无node execution不进命中率分母；complete与partial分列，最新partial不拼旧完整；未知来源单列。
- [ ] 实现按workflow+snapshot+signature+时间窗的SQL查询，沿用现有数据库dialect；集合有界分页，不截取最近50条后假称全量。
- [ ] 展示数量、窗口、覆盖与命中率口径；无分母显示“样本不足/无可用分析”，不是0%。
- [ ] 运行新增repository/API/UI focused tests，以及相关现有evolution/verification tests，再提交一个指标行为变更。

## Task M2：跨版本复现与修复后观察（第一批）

**Files**

- M1 repository/contract；只读复用WF/server/services/repair-verification-service.ts、repair-auto-verification.ts及contracts/repair-verification.ts的既有结果，不改其算法/阈值。
- 问题详情中的版本记录组件及已有verification视图；新增 issue-version-recurrence.test.ts 与 IssueVersionRecurrence.test.tsx。

**Interfaces**

跨版本条目返回 snapshotKey、版本展示身份、M1样本、recurrence状态与关联的真实发布任务/验证结论。

状态为 `first_seen`（已读取可比范围无既往命中且当前有证据）、`recurred`（此前可比版本命中且当前有证据）、`not_observed`（当前有合格观察但未命中，仅表示暂未复现）、`insufficient_observation`（无样本/分析不足/节点未运行/身份未知）。另提供historyComplete及历史范围；historyComplete=false时first_seen只能展示“已读取范围首次发现”，不得宣称全历史首次。回滚重新激活原记录不算新版本，展示再次生效时间且不重复累计同flow；若既有发布路径形成新部署记录，则按其真实key独立统计，不能因配置相同合并。

- [ ] 失败回归：A有问题，B无运行→观察不足；B有完整相关样本无命中→暂未复现；B再次命中→复现；迟到A分析不改写B结果；回滚后身份仍对应原运行。
- [ ] 与已有发布receipt/item outcome关联，只对实际changed且成功部署的目标给修复观察；archive、not_needed（含已满足/不适用）、草稿成功不自动标verified。历史引用通过冻结source/target及任务结果追溯，不依赖改写旧item状态；原版本验证结论不被新引用覆盖。
- [ ] 延续既有规则的observing/verified/ineffective；缺验证规则只显示样本事实。不要把not_observed自动升级verified。
- [ ] UI按问题展示跨版本记录、样本与证据入口；同类问题不宣称同根因。运行focused与verification suite后提交。

## Task M3：聚合运行情况与后续扩展（备选，按实际需要选取）

**Files**

- WF/server/services/repair-proposal-integration.ts及现有队列read投影；repositories/evolution-metrics.ts。
- 问题/效果现有视图，必要时Create小型读repository，不能增加另一个worker。

**Deliverables**

- 原始成员数、方案数、来源保留数；queued/organizing/retryable/blocked/ready与最后更新时间、原因。精确fallback和语义完成分开，不把压缩率作为质量结论。
- 处理周期逐阶段：发现、可用方案、生成、实际生效、有效观察；未到达的阶段保留空值。
- 连续复现版本数按可比部署记录的时间顺序，不用版本号相减；保存测试不插入正式连续部署序列。
- 无效修复次数来自已关联的ineffective结论；修复后复发要求先有真实verified，再有后续新命中。
- 版本前后命中率、耗时、token比较使用相同窗口策略并同时给分母/覆盖；不输出因果或自动ROI结论。

- [ ] 为每项写明确计数fixture，包含worker无记录不等于排队、结果过期、重复结论、跳号版本和回滚。
- [ ] 实现只读查询与现有视图，保持按需加载/有界分页；不记录模型原文或凭据。
- [ ] 定向测试后运行受影响模块tests/check/build，记录未覆盖的真实数据库与模型质量边界。

## 验证与发布

从 `apps/evolverun/clawweb` 执行新增 `issue-version-metrics`、`issue-version-recurrence`、对应UI及现有verification suite，然后运行受影响的 @avernet/workflow / @avernet/clawevolve tests、ci:check、ci:build。新增文件名由任务固定，执行时记录精确命令与结果。

真实验收只读对照至少一个跨版本复现问题：逐个flowId回溯分子、分母与版本，覆盖当前、历史、remote和未知来源。不运行新业务样本来凑通过条件；需要新运行/模型/回填时另行申请授权。指标是独立扩展，不得延迟核心问题聚合/历史勾选的正确性交付。
