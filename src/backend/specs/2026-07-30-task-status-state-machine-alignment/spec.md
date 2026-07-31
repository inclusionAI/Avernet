# 任务状态机(对齐产品设计)— 系统设计规格(spec.md)

> 负责人:栖真。隶属:`2026-07-28-goal-driven-task-execution/` 的后续收敛。
> 落点域:ocb backend 任务内核(开源);代码落点(枚举 / 状态机 / service / router / 测试 / skill / 副屏)属 HOW,见 `plan.md`。
> 日期:2026-07-30。

---

## 1. 概述

### 1.1 背景

ocb 任务内核需要一套与产品设计一致、清晰、稳定的任务生命周期状态机,作为 task-recognition skill、副屏卡片、HTTP API、内核调度器共用的事实术语。状态名对用户可见,且必须与产品 PRD 一致。

### 1.2 这是什么

**任务级**生命周期状态机,7 态:

- `DRAFTING` —— 要素补全中;创建即进入,要素补全期间留在此态。
- `DEFINED` —— 四要素齐 + 计划冻结,等用户确认执行。
- `EXECUTING` —— 执行中。
- `REVIEWING` —— 验收中。
- `DONE` —— 交付完成(终态)。
- `CANCELLED` —— 取消(终态)。
- `FAILED` —— 任务级失败(终态)。

迁移:

- `DRAFTING ──(四要素齐 + finalize_plan)──► DEFINED`
- `DEFINED ──(approve / 用户确认执行)──► EXECUTING`
- `EXECUTING ──(全节点 settled)──► REVIEWING`
- `REVIEWING ──(用户验收通过)──► DONE`
- `REVIEWING ──(验收不通过,返工)──► EXECUTING`
- `EXECUTING ──(失败,见 §3 R4)──► FAILED`
- 任意非终态 `──(用户否决)──► CANCELLED`
- 子任务派生:`EXECUTING` 内挂起等待子任务汇聚(**不新增任务级状态**,由执行图谱 DAG 承载)。
- "被 hung 住 / 上升等人工"不设任务级状态,由节点级 `HUMAN_REQUIRED` 承载(任务整体留 `EXECUTING`);不可恢复的受阻走 `FAILED`。

**节点级** `NodeStatus`,6 态:

- `PENDING` / `RUNNING` / `DONE` / `FAILED` / `SKIPPED` / `HUMAN_REQUIRED`
- 验收不通过与执行失败统一为 `FAILED`;二者的区分由节点属性 `acceptance_result` / failure kind 承载,不由状态枚举表达。

### 1.3 为什么

- **术语一致**:skill / 副屏 / API / 内核共用一套与 PRD 一致的状态名,消除沟通与理解成本。
- **清晰的失败终态**:任务有明确的 `FAILED` 终态,执行彻底失败有归宿。
- **草稿态收敛**:要素补全期单一 `DRAFTING` 态,降低认知负担。
- **返工闭环**:`REVIEWING → EXECUTING` 显式回炉重规划。
- **节点失败态收敛**:验收失败与执行失败合一,减少冗余态,区分下沉到节点属性。

### 1.4 非目标

- **执行引擎 / 调度器核心逻辑不变** —— 仅状态命名与合法迁移表调整;dispatch / watchdog / 自驱等行为不动。
- **副屏卡片 / skill 文案适配**属下游,跟随状态名变更即可,不在本 spec 的 HOW 范围。
- **不改任务领域模型的其他维度**(`TaskSpec` / `Plan` / `ExecutionGraph` 结构不动)。

---

## 2. 任务状态机

### 2.1 状态枚举

`DRAFTING / DEFINED / EXECUTING / REVIEWING / DONE / CANCELLED / FAILED`(7 态;终态:`DONE / CANCELLED / FAILED`)。

### 2.2 合法迁移

```
DRAFTING ──(四要素齐 + finalize_plan)──► DEFINED
DEFINED   ──(approve)──────────────────► EXECUTING
EXECUTING ──(全节点 settled)────────────► REVIEWING
REVIEWING ──(验收通过)─────────────────► DONE
REVIEWING ──(验收不通过,返工)──────────► EXECUTING
EXECUTING ──(失败,§3 R4)───────────────► FAILED
任意非终态 ──(用户否决)─────────────────► CANCELLED
```

- `EXECUTING → EXECUTING` 自环(tick 推进 / 子任务汇聚)合法。
- 终态(`DONE / CANCELLED / FAILED`)无出迁移。
- 非法迁移(如 `EXECUTING → DONE` 直跳、终态再迁移)一律拒绝。

---

## 3. 需求

### 3.1 状态枚举

- **R1** 任务级状态机枚举为 `DRAFTING / DEFINED / EXECUTING / REVIEWING / DONE / CANCELLED / FAILED`。
- **R2** `DRAFTING` 覆盖要素补全期;要素补全期间的 amend **不触发状态切换**(任务留 `DRAFTING`)。
- **R3** `DEFINED`(冻结)/ `REVIEWING`(验收)/ `DONE`(交付)命名如 §2.1。
- **R4** 任务级 `FAILED` 终态,**两种触发都要**:
  - (a) **原子终止** —— 重规划上限耗尽(`compute_gap` 命中原子终止)且存在不可再拆的 FAILED 节点 → 任务 `FAILED`。
  - (b) **节点升级** —— 节点 `MAX_ATTEMPTS` 耗尽且无重规划余地(不可 reroute/split)时,该节点 FAILED 升级为任务 `FAILED`。
  - 两种触发的精确先后与 reroute/split 恢复的关系(先尝试恢复,恢复无望才 `FAILED`)由 `plan.md` 定。

### 3.2 合法迁移

- **R5** 状态机守卫按 §2.2 校验所有合法迁移,非法迁移拒绝并报错。
- **R6** 返工 `REVIEWING → EXECUTING` 保留(验收不通过回炉重规划)。
- **R7** 子任务派生在 `EXECUTING` 内挂起等待汇聚(不新增任务级状态,由执行图谱 DAG 承载)。

### 3.3 节点级状态

- **R8** 节点级 `NodeStatus` = `PENDING / RUNNING / DONE / FAILED / SKIPPED / HUMAN_REQUIRED`(6 态)。
- **R9** 验收不通过(`NODE_REJECTED`)与执行失败(`NODE_FAILED`)统一置节点 `FAILED`;二者的区分由节点属性 `acceptance_result`(pass/fail)/ failure kind 承载。
- **R10** `compute_gap` 读节点属性(`acceptance_result` / `attempted_executors` 是否耗尽)判定 reroute vs split,**不依赖节点状态枚举的区分**。具体映射在 `plan.md` 定。
- **R11** 节点 `FAILED → RUNNING`(重试)与 `FAILED → DONE`(验收通过)等迁移边保留合法。

---

## 4. 验收标准

- 任务状态机枚举为 §2.1 的 7 态;节点状态机枚举为 §3.3 的 6 态。
- 全回路可走通并断言态名:
  - `create → DRAFTING`
  - `amend → 仍 DRAFTING`
  - `finalize_plan → DEFINED`
  - `approve → EXECUTING`
  - 全节点 settled → `REVIEWING`
  - 验收通过 → `DONE`
  - 验收不通过 → 返工 `EXECUTING`
  - 否决 → `CANCELLED`
  - 失败 → `FAILED`
- 状态机守卫覆盖所有合法迁移,非法迁移(`EXECUTING → DONE` 直跳、终态再迁移等)拒绝。
- 任务级 `FAILED` 的两种触发(R4 a/b)均有实现与测试。
- 节点级验收失败与执行失败均为 `FAILED`,由 `acceptance_result` 属性区分;`compute_gap` 不依赖节点状态枚举区分 reroute/split。
- 内核测试、task-recognition skill、副屏卡片状态引用全部同步更新,全绿。

---

## 5. 开放问题(plan 前确认)

1. **`FAILED` 两种触发的先后** —— 节点 FAILED 时,先走 reroute/split 恢复,只有恢复无望(原子终止 / 节点无重规划余地)才升 `FAILED`?还是某条触发命中即立即 `FAILED`?需在 plan 定精确 precedence。