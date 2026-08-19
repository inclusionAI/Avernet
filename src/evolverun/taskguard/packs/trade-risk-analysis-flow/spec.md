# trade-risk-analysis-flow Spec — v4 扁平化重构（移除 loop-group）

## 目标

将 workflow 从 loop-group 循环降级为**扁平化 revise 驱动循环**，解决 loop-group 内部 human 节点 revise 后流程卡死的问题。

## 设计文档

来源：[语雀文档 - workflow 流程更新](https://yuque.antfin.com/mohan.wy/riv4l4/xhlg9fs8n6bfxxau)

## v3 → v4 变更总览

| 维度 | v3（旧） | v4（新） |
|------|---------|---------|
| 意图补充循环 | loop-group x3 | **扁平化**：supplement-prompt (human) → revise → intent-recognition |
| DIMA 补全循环 | loop-group x3 | **扁平化**：dima-preparator → dima-check (human) → revise → dima-preparator |
| 卡片审核循环 | loop-group x3 | **扁平化**：card-generation → card-review (human) → revise → card-generation |
| 循环退出条件 | until + onMaxIterations | **人工驱动**：confirm 通过 / revise 重跑 / reject 结束 |
| 迭代计数 | loop-group.iterationVar | **不再自动计数**（`\| plus:` 模板过滤器不支持） |
| human 节点 revise | target body-node（**卡死**） | target 顶层节点 + **reset: target-and-descendants** |
| DIMA 结果审核 | is_ready 自动分流（complete/incomplete 分支） | **统一人工收口**：不管完不完整都过人工审核 |
| deep-analysis-review revise | 不支持 | **支持 revise** → 回流 deep-analysis |

## 核心原理：revise 驱动循环 vs loop-group

### 为什么 loop-group + revise 会卡死？

loop-group 有独立的运行时状态（`LoopGroupRuntimeState`、`LoopIterationRuntimeState`），每个 body 节点在每次迭代中都有独立的 **runtime node ID**。当 controller 处理 human 节点的 revise 时，它无法正确地将 body 节点 ID 映射回当前 iteration 的 runtime 节点，导致：
- target 节点 reset 失败，或
- loop-group 迭代状态机与节点状态不一致，流程永久停滞

### 扁平化 revise 循环如何工作？

```
agent-node (A) → human-node (B)
                    ├── confirm → succeed → 触发下游 C
                    ├── revise → feedback → reset A + descendants → rerun A
                    └── reject → fail-flow → 结束流程
```

关键配置：`reset: target-and-descendants`
- 不仅重置 target 节点本身
- 还重置 target 的所有下游节点（包括发起 revise 的 human 节点自身）
- 保证下游节点在下一轮可以被重新调度和触发

## v4 完整流程架构

### 路径1：DIMA需求直通

```
① 意图识别 (route_path=path1_dima)
    ↓
dima-after-intent (branchId=path1_dima)
    ↓
④.1 dima-preparator → ④.2 dima-check (人工)
    ├── confirm → ④.3 dima-creator
    ├── revise → reset dima-preparator + descendants → 回流 ④.1
    └── reject → 结束流程
    ↓
⑤ coverage-sql-exec → ⑥ coverage-analysis → ⑦ iteration-judgment
    ├── no_iteration → END
    └── need_iteration → ⑦ card-generation → ⑧ card-review (人工)
                               ├── confirm → ⑨ card-push → END
                               ├── revise → reset card-generation + descendants → 回流
                               └── reject → 结束流程
```

### 路径2：风险分析

```
① 意图识别 (route_path=path2_risk_analysis)
    ↓
② deep-analysis (alipaygame-daishua-risk-analysis)
    ↓
③ deep-analysis-review (人工)
    ├── confirm <decision> → 走 onResult 分支：
    │   ├── ok → 🏁 END
    │   └── risk_miss / false_positive → dima-after-review → ④.1 dima-preparator → ...
    ├── revise → reset deep-analysis + descendants → 回流 ②
    └── reject → 结束流程
```

### 路径3：信息补充

```
① 意图识别 (route_path=path3_supplement)
    ↓
② supplement-prompt (人工)
    ├── confirm → 跳过补充（当前分支继续）
    ├── revise → reset intent-recognition + descendants → 回流 ①
    └── reject → 结束流程
                                          ↓
                              重新识别 → path1/path2/path3 再次判断
```

## 节点清单（18个顶层节点）

| ID | 节点名称 | 类型 | 阶段 | Skill | BranchId | 备注 |
|----|---------|------|------|-------|----------|------|
| intent-recognition | 意图识别与路由 | embedded-agent | P1 | intent-classifier | — | 3 条分支路由 |
| supplement-prompt | 补充信息 | **human** | P1 | — | path3_supplement | confirm/revise/reject |
| deep-analysis | 深度分析 | embedded-agent | P2 | alipaygame-daishua-risk-analysis | path2_risk_analysis | |
| deep-analysis-review | 报告审核 | **human** | P2 | — | — | confirm+decision / revise / reject |
| dima-after-review | 审核后DIMA路由 | embedded-agent | P2 | — | review_need_dima | 内部根据 reviewDecision 决定 risk_miss / false_positive |
| dima-after-intent | DIMA直通路由 | embedded-agent | P2 | — | path1_dima | |
| dima-preparator | DIMA需求准备 | embedded-agent | P2 | dima-preparator | — | 生成 draft_data + is_ready |
| dima-check | DIMA结果审核 | **human** | P2 | — | — | confirm/revise/reject（统一收口） |
| dima-creator | DIMA工单创建 | embedded-agent | P2 | dima-creator | — | |
| coverage-sql-exec | SQL执行 | cli-script | P3 | — | — | 生产路径脚本 |
| coverage-analysis | 覆盖分析报告 | embedded-agent | P3 | bussiness-scene-cheat-cover-analyzer | — | |
| iteration-judgment | 能力迭代判断 | embedded-agent | P3 | iteration-decision-maker | — | |
| card-generation | 生成知识卡片 | embedded-agent | P4 | anticheat-generate-card | need_iteration | |
| card-review | 卡片审核 | **human** | P4 | — | — | confirm/revise/reject |
| card-push | 推送卡片 | embedded-agent | P4 | task-publisher-skill | — | |
| complete-done | 流程完成 | done | P4 | — | — | |
| supplement-complete | 跳过补充终点 | embedded-agent | P1 | — | — | confirm 后结束消息 |

> 注：`dima-after-review-fp` 节点已合并到 `dima-after-review`，通过 `workflowData.reviewDecision` 在模型内部区分 risk_miss / false_positive，避免引擎同时触发两个 `complete:false` 分支。 |

## 4 个人工节点操作总览

### ① supplement-prompt（意图信息补充）

| 操作 | 命令 | 行为 |
|------|------|------|
| ⏭️ confirm | `/... confirm` | 跳过补充 → succeed-current → 继续到 `supplement-complete` → 输出结束消息 → 流程结束 |
| 📝 revise | `/... revise <补充内容>` | feedbackPath: `workflowData.supplementInfo` → reset `intent-recognition` → rerun |
| ❌ 结束流程 | `/... reject` | fail-flow → 流程结束 |

### ② deep-analysis-review（深度分析报告审核）

| 操作 | 命令 | 字段 | 行为 |
|------|------|------|--|
| ✅ confirm | `/... confirm <decision>` | decision=ok/risk_miss/false_positive | saveAs → workflowData.reviewDecision → succeed-current |
| 📝 revise | `/... revise <修改/补充信息>` | feedback（可选） | feedbackPath: `workflowData.deepAnalysisFeedback` → reset `deep-analysis` → rerun |
| ❌ 结束流程 | `/... reject` | — | fail-flow → 流程结束 |

> confirm 时必须填写 `decision`，revise 时不需填。

### ③ dima-check（DIMA 结果审核 — 统一收口）

| 操作 | 命令 | 字段 | 行为 |
|------|------|------|--|
| ✅ confirm | `/... confirm` | feedback（可选） | saveAs: `workflowData.dimaChecked = "true"` → succeed-current → 触发 dima-creator |
| 📝 revise | `/... revise <补充内容>` | feedback（可选） | feedbackPath: `workflowData.dimaSupplementInfo` → reset `dima-preparator` → rerun |
| ❌ 结束流程 | `/... reject` | — | fail-flow → 流程结束 |

> **设计变更**：不通过 `is_ready` 自动分流，而是统一走人工审核。完整 → 确认直接过；不完整 → revise 重新生成。

### ④ card-review（知识卡片审核）

| 操作 | 命令 | 字段 | 行为 |
|------|------|------|--|
| ✅ confirm | `/... confirm` | — | saveAs: `workflowData.riskCardApproved = "true"` → succeed-current → 触发 card-push |
| 📝 revise | `/... revise <修改意见>` | feedback（可选） | feedbackPath: `workflowData.riskCardFeedback` → reset `card-generation` → rerun |
| ❌ 结束流程 | `/... reject` | — | fail-flow → 流程结束 |

## 4 个扁平化 revise 循环

### 循环1：意图补充（supplement-prompt → intent-recognition）

```yaml
revise:
  feedbackPath: "workflowData.supplementInfo"
  feedbackMode: append-line
  target: intent-recognition
  reset: target-and-descendants
  next: rerun-target
```

### 循环2：深度分析重做（deep-analysis-review → deep-analysis）

```yaml
- id: deep-analysis-review
  dependsOn: [deep-analysis]
  executor:
    type: human
    actions:
      confirm:
        saveAs:
          workflowData.reviewDecision: '{{input.decision | default: ""}}'
        next: succeed-current
      revise:
        feedbackPath: "workflowData.deepAnalysisFeedback"
        feedbackMode: append-line
        target: deep-analysis
        reset: target-and-descendants
        next: rerun-target
      reject:
        next: fail-flow
    commandHints:
      confirm: { label: "✅ confirm ok | risk_miss | false_positive" }
      revise: { label: "📝 revise <修改/补充信息>" }
      reject: { label: "❌ 结束流程" }
  onResult:
    branches:
      - branchId: review_ok
        match:
          decision: ok
        complete: true
      - branchId: review_need_dima
        match: {}
        complete: false   # catch-all：risk_miss / false_positive 统一路由
```

### 循环3：DIMA 信息补全（dima-check → dima-preparator）

```yaml
revise:
  feedbackPath: "workflowData.dimaSupplementInfo"
  feedbackMode: append-line
  target: dima-preparator
  reset: target-and-descendants
  next: rerun-target
```

### 循环4：卡片重做（card-review → card-generation）

```yaml
revise:
  feedbackPath: "workflowData.riskCardFeedback"
  feedbackMode: append-line
  target: card-generation
  reset: target-and-descendants
  next: rerun-target
```

## 路由网关设计

引擎不支持 `condition` 字段，因此用**路由网关节点 + branchId**实现多入口汇聚：

```
intent-recognition
  ├── path1_dima → dima-after-intent ──┐
  └── path2_risk_analysis              │
       └── deep-analysis-review        │
            ├── review_ok → END        │
            └── review_need_dima → dima-after-review ──┤
                       (reviewDecision 内部区分)        │
                              │                         │
                              dima-preparator ←─────────┘
                              (triggerRule: one_success)
```

> **重要**：`deep-analysis-review` 的 `onResult` 中只保留一个 `complete: false` 分支（`review_need_dima`），由下游 `dima-after-review` 节点内部根据 `workflowData.reviewDecision` 判断是 risk_miss 还是 false_positive。引擎在多个 `complete: false` 分支之间不一定严格互斥，因此**不能拆分两个 `complete: false` 分支**。 |

## 引擎兼容性确认

| 特性 | 引擎支持 | v4 使用方式 |
|------|---------|------------|
| `condition` 节点字段 | ❌ | branchId + 路由网关节点 |
| `\| plus:` 过滤器 | ❌ | 不使用计数器（人工驱动循环） |
| `{% if %}` Jinja2 块 | ❌ | `\| default: ""` 拼接 |
| `loop-group` | ✅ | **v4 不再使用**（revise 替代） |
| `branchId` + `onResult.branches` | ✅ | 所有路由均使用 |
| `revise` + `rerun-target` | ✅ | **核心循环机制** |
| `reset: target-and-descendants` | ✅ | **关键：保证下游节点可重触发** |
| `saveAs` + `workflowData` | ✅ | 跨节点状态传递 |
| `\| default:` 过滤器 | ✅ | 所有模板缺省值 |
| `fail-flow` (reject) | ✅ | 统一流程结束方式 |

## 已知限制

1. **无自动 maxIterations 限制**：由于引擎不支持 `\| plus: 1` 模板过滤器，无法在 workflowData 中维护循环计数器。循环次数完全由用户手动控制。
   - 缓解措施：在 agent prompt 中软性提示当前已收集的信息量，引导用户尽快完成。

2. **dima-supplement confirm 的风险**（已缓解）：用户点击 confirm 跳过补充时，dima-creator 可能因缺少必要字段而执行失败。
   - 缓解措施：dima-check 统一收口人工审核，用户可以看到 is_ready 状态自行判断。同时 dima-creator 的 skill 应内置容错逻辑。

3. **workflowData 覆盖问题**：`feedbackMode: append-line` 在多次补充后会累积大量历史记录。agent 节点在 prompt 中应明确指出只关注最新一次补充内容。
