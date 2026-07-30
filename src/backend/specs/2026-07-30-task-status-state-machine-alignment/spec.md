# 任务状态机对齐产品线设计 — 系统设计规格(spec.md)

> 负责人:栖真。隶属:`2026-07-28-goal-driven-task-execution/` 的后续收敛。
> 落点域:ocb backend 任务内核(开源);具体代码落点(枚举/状态机/service/router/迁移)属 HOW,见 `plan.md`。
> 日期:2026-07-30。

---

## 1. 概述

### 1.1 背景

当前任务内核的生命周期状态机有 8 个任务级状态:`INTAKE / DISCUSSING / PLANNED / EXECUTING / VALIDATING / DELIVERED / CANCELLED / HUNG`(终态:DELIVERED / CANCELLED / HUNG)。产品设计(`TeamClaw 任务 loop 产品方案》)定义的任务状态机是:

```
DRAFTING ──(四要素齐)──► DEFINED ──(用户确认执行)──► EXECUTING ──(执行完)──► REVIEWING ──(用户验收)──► DONE
   │                        │                          │  │                       │
   │                     (用户否决)                 (失败)│                  (不通过→返工)
   └──► CANCELLED          └──► CANCELLED              │  └──► FAILED            └──► EXECUTING
                                                       │
                                                  (派生子任务)──► 子任务走自己的状态机
                                                  EXECUTING(挂起/等待子任务汇聚)
```

两套术语不一致:skill / 副屏卡片 / API 返回的 `status` 与产品 PRD 状态名对不上,沟通与理解成本高;且当前**缺任务级 `FAILED`**(只有节点级 FAILED + 任务级 HUNG),任务执行彻底失败(重规划上限耗尽)无清晰终态;HUNG 在产品线无对应概念。

### 1.2 这是什么

把**任务级状态机的枚举值与合法迁移**对齐到产品设计的状态机:

- `DRAFTING` —— 要素补全中(合并现 `INTAKE` + `DISCUSSING`);创建即进入,amend 期间留在此态。
- `DEFINED` —— 四要素齐 + 计划冻结,等用户确认执行(= 现 `PLANNED`)。
- `EXECUTING` —— 执行中(不变)。
- `REVIEWING` —— 验收中(= 现 `VALIDATING`)。
- `DONE` —— 交付完成(= 现 `DELIVERED`)。
- `CANCELLED` —— 取消(不变)。
- `FAILED` —— 任务级失败终态(新增)。

迁移(对齐后):

- `DRAFTING ──(四要素齐 + finalize_plan)──► DEFINED`
- `DEFINED ──(approve / 用户确认执行)──► EXECUTING`
- `EXECUTING ──(全节点 settled)──► REVIEWING`
- `REVIEWING ──(用户验收通过)──► DONE`
- `REVIEWING ──(验收不通过,返工)──► EXECUTING`
- `EXECUTING ──(重规划上限耗尽 / 原子终止)──► FAILED`
- 任意非终态 `──(用户否决)──► CANCELLED`
- 子任务派生:`EXECUTING` 内挂起等待子任务汇聚(**不新增任务级状态**,由执行图谱 DAG 承载)。
- "被 hung 住 / 上升等人工"不设任务级状态:由节点级 `HUMAN_REQUIRED` 承载(任务留 `EXECUTING`);不可恢复的受阻走 `FAILED`。现 `HUNG` 移除。

### 1.3 为什么是现在

- **术语一致**:task-recognition skill / 副屏卡片 / HTTP API 返回的 `status` 需与产品 PRD 状态名一致,否则用户在卡片看到的态名与产品文档/设计稿对不上,排查与沟通成本高(本次验证 skill 调用链时就暴露:intake/discussing/planned/validating/delivered 这套名对产品同学很别扭)。
- **补齐 FAILED**:产品线明确任务有失败终态;当前仅节点级 FAILED + 任务级 HUNG,任务执行彻底失败(重规划上限耗尽、原子终止)无清晰终态,流程"挂"在 HUNG 语义模糊。
- **收敛 DRAFTING**:产品把"创建到要素齐"视为单一草稿态;现 `INTAKE`/`DISCUSSING` 二态对用户无业务意义,合并降低认知负担,也让"首次 amend 触发状态跳变"这个内部信号不再外泄给消费方。
- **返工闭环**:`REVIEWING → EXECUTING`(验收不通过回炉)已在现状态机(`VALIDATING → EXECUTING`)隐含,对齐命名后语义更显式。

### 1.4 非目标

- **节点级状态机(`NodeStatus`)** 不在本次范围,除非对齐任务态时必须联动(如节点 FAILED 升级到任务 FAILED 的判定)。
- **执行引擎 / 调度器核心逻辑不变** —— 仅状态命名与合法迁移表调整;dispatch / watchdog / 自驱等行为不动。
- **副屏卡片 / skill 文案适配**属下游,跟随状态名变更即可,不在本 spec 的 HOW 范围。
- **历史任务数据迁移策略**属 HOW(`plan.md` 定)。
- 不改任务领域模型的其他维度(TaskSpec / Plan / ExecutionGraph 结构不动)。

---

## 2. 现状与差距

### 2.1 现状态机(任务级)

8 态:`INTAKE / DISCUSSING / PLANNED / EXECUTING / VALIDATING / DELIVERED / CANCELLED / HUNG`。终态:`DELIVERED / CANCELLED / HUNG`。关键迁移:`INTAKE→DISCUSSING`(首次 amend)、`DISCUSSING→PLANNED`(finalize_plan)、`PLANNED→EXECUTING`(approve/start)、`EXECUTING→VALIDATING`(全 settled)、`VALIDATING→DELIVERED`(goal.verified)、`VALIDATING→EXECUTING`(返工)。

### 2.2 产品设计状态机

7 态:`DRAFTING / DEFINED / EXECUTING / REVIEWING / DONE / CANCELLED / FAILED`。迁移见 §1.2。

### 2.3 差距表

| 现状枚举 | 产品设计 | 处置 |
|---|---|---|
| `INTAKE` | `DRAFTING` | 合并进 `DRAFTING` |
| `DISCUSSING` | `DRAFTING` | 合并进 `DRAFTING`(删除) |
| `PLANNED` | `DEFINED` | 改名 |
| `EXECUTING` | `EXECUTING` | 不变 |
| `VALIDATING` | `REVIEWING` | 改名 |
| `DELIVERED` | `DONE` | 改名 |
| `CANCELLED` | `CANCELLED` | 不变 |
| `HUNG` | (产品无) | **移除**("被 hung 住"语义下沉到节点级 `HUMAN_REQUIRED`;不可恢复走 `FAILED`) |
| (无) | `FAILED` | **新增**任务级失败终态 |

---

## 3. 需求

### 3.1 状态枚举对齐

- **R1** 任务级状态机枚举对齐为 `DRAFTING / DEFINED / EXECUTING / REVIEWING / DONE / CANCELLED / FAILED`(7 态,与产品线完全一致)。
- **R2** 合并 `INTAKE + DISCUSSING → DRAFTING`;删除 `DISCUSSING`;要素补全期间的 amend **不再触发状态切换**(任务留在 DRAFTING)。
- **R3** `PLANNED → DEFINED`(冻结)、`VALIDATING → REVIEWING`(验收)、`DELIVERED → DONE`(交付)改名。
- **R4** 新增任务级 `FAILED` 终态;明确触发条件(重规划上限耗尽 / 原子终止;节点 FAILED 是否升级见开放问题 3)。
- **R4b** 移除 `HUNG`;原 HUNG 语义("上升挂起等人工")下沉到节点级 `HUMAN_REQUIRED`(任务留 `EXECUTING`),不可恢复的受阻走 `FAILED`。

### 3.2 合法迁移

- **R5** 定义对齐后的迁移图(见 §1.2),状态机守卫按此校验,非法迁移拒绝并报错。
- **R6** 返工 `REVIEWING → EXECUTING` 保留(验收不通过回炉重规划)。
- **R7** 子任务派生在 `EXECUTING` 内挂起等待汇聚(不新增任务级状态,由执行图谱 DAG 承载)。
- **R8** 退出态可达性:`CANCELLED` 从任意非终态可达;`FAILED` 从 `EXECUTING`(及 arguably `REVIEWING`?)可达 —— 待开放问题确认。

### 3.3 HUNG 移除

- **R9** 移除 `HUNG`;不新增 `BLOCKING`。"被 hung 住 / 上升等人工"语义下沉到节点级 `HUMAN_REQUIRED`(任务整体留 `EXECUTING`);不可恢复的受阻走 `FAILED`。

### 3.4 兼容性(无需)

任务功能尚未上线,无存量数据 / 调用方需要兼容。**直接 breaking 改名,不做历史兼容**:

- **R10** 无需数据迁移;旧状态值(`intake/discussing/planned/validating/delivered/hung`)在代码、测试、文档中全部直接改为产品设计的 新值(`drafting/defined/executing/reviewing/done/failed`,+`cancelled`)。
- **R11** HTTP API `status` 字段直接返回新值,不做旧值别名 / 过渡期;同步更新所有调用方(task-recognition skill / 前端 / 副屏卡片)。

---

## 4. 验收标准

- 任务状态机枚举与产品线完全一致(`DRAFTING/DEFINED/EXECUTING/REVIEWING/DONE/CANCELLED/FAILED`,7 态);`DISCUSSING`/`HUNG` 已移除。
- 全回路可走通并断言态名:
  - `create → DRAFTING`
  - `amend → 仍 DRAFTING`(不再跳 DISCUSSING)
  - `finalize_plan → DEFINED`
  - `approve → EXECUTING`
  - 全节点 settled → `REVIEWING`
  - 验收通过 → `DONE`
  - 验收不通过 → 返工 `EXECUTING`
  - 否决 → `CANCELLED`
  - 重规划耗尽 → `FAILED`
- 状态机守卫覆盖所有合法迁移,非法迁移(如 `EXECUTING → DONE` 直跳、终态再迁移)拒绝。
- 节点级 FAILED 升级到任务级 FAILED 的规则(若采纳)有明确实现与测试。
- 原 `HUNG` 语义已下沉(节点级 `HUMAN_REQUIRED` + 任务级 `FAILED`),无引用残留。
- 现有内核测试、task-recognition skill、副屏卡片状态引用全部同步更新,全绿。

---

## 5. 开放问题(需 plan 前确认)

1. ~~**HUNG 去留?**~~ ✅ 已决:`HUNG` 移除,不新增 `BLOCKING`;语义下沉到节点级 `HUMAN_REQUIRED`(任务留 `EXECUTING`),不可恢复走 `FAILED`。
2. ~~**合并 INTAKE+DISCUSSING?**~~ ✅ 已决:合并 `INTAKE + DISCUSSING → DRAFTING`,`DISCUSSING` 删除;amend 不再触发状态跳变。
3. **任务级 FAILED 触发条件**(:)仅"重规划上限耗尽(原子终止)"?还是节点级 FAILED 也升级到任务级 FAILED?升级阈值?
4. **节点级 `NodeStatus` 是否联动调整?** 产品线"执行完→REVIEWING"在节点层是否需要新态,还是仍用 `DONE`?产品设计的状态码是否覆盖节点级?
5. ~~**历史任务数据迁移?**~~ ✅ 已决:任务未上线,不做历史兼容,无迁移。
6. ~~**API `status` 改名兼容?**~~ ✅ 已决:直接 breaking 改名,不别名、不过渡;同步更新所有调用方。