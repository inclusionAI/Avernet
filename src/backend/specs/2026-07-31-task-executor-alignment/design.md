# 任务执行者对齐与拆分/编排/执行流程修订 — 设计稿(design.md)

> 负责人:栖真(待确认)。隶属:`2026-07-28-goal-driven-task-execution/` 的后续收敛;与 `2026-07-30-task-status-state-machine-alignment/`(7 态状态机)并行,本文不重定义状态,仅引用态名。
> 落点域:ocb backend 任务内核(开源,代码在 Avernet `src/backend/src/agentclaw/community/core/task/`,同步到 ocb `ocb-public` submodule)。
> 日期:2026-07-31。
> 性质:**plan 修订稿**(只改设计口径与协议签名,不含产品功能新增)。本文档若被采纳,后续代码改动另行起 `tasks` / `implement`;本文不直接驱动写代码。

---

## 1. 概述

### 1.1 背景

`2026-07-28-goal-driven-task-execution/plan.md` §6 定了核心口径——"系统 TaskScheduler 编排 / owner-bot SKILL 验收 / 状态唯一写口在 TaskService",但在落地 review 时发现:

1. **执行者心智模型有混淆**:"搜推 bot"被反复提及,但设计里搜推是系统 Port,不是 bot。
2. **`DecomposerPort` 协议签名不自洽**:只暴露一个 `decompose(task_id)->Plan`,但拆分有两个用途(初始 plan / 运行期 sibling 拆解),语义不同。
3. **"owner bot" 是变身角色**未在领域模型里正式化:单 bot / 协作群 master / BBS task-owner 三种形态,验收发起人随之变化,但只是一张注释表,没进领域模型契约。
4. **BBS 的 task-owner 执行链路不清**:BBS 上升后验收由 task-owner 经 SKILL 回投,但 task-owner 是录入期 user bot,它在 BBS 上升时如何被对接,§5.4 没写清。
5. **运行期拆解的聚合验收 owner 缺失**:§5.2 父节点 SKIPPED 委托 sibling 聚合验收,但"谁在何时做聚合复核"没定。

### 1.2 这是什么

对 `2026-07-28` plan §5(执行 loop)/§6(操作契约 + owner bot 验收链路)的**执行者对齐修订**:把"每步谁干"从注释/口径提升为领域模型契约,消解 5 个二义点。**不改产品功能、不改状态机、不改三模态/三协作模式语义**。

### 1.3 与既有 spec 的关系

| 文档 | 关系 |
|---|---|
| `2026-07-28-goal-driven-task-execution/` | 母 spec/plan。本文修订其 §5/§6 的执行者口径;不重写其 WHAT/WHY(spec.md 不动) |
| `2026-07-30-task-status-state-machine-alignment/` | 7 态状态机权威定义。本文沿用其态名(DRAFTING/DEFINED/EXECUTING/REVIEWING/DONE/CANCELLED/FAILED + 6 NodeStatus),不重定义 |
| 本文 `design.md` | plan 修订稿;若采纳,母 plan.md 的 §5/§6 应同步更新(见 §10 开放问题) |

### 1.4 非目标

- 不新增执行模态/协作模式(仍是三模态 + 三协作模式)。
- 不改事件类型与回投通道(仍是统一 `POST /events` → `on_event`)。
- 不改副屏画布 / 可视化(归 `2026-07-28` FR-OBS)。
- 不在本期落代码;本文是设计收敛。

---

## 2. 核心口径(重申,作为后续一切的前提)

> **系统 `TaskScheduler` 做 deepresearch 动态编排**(路由/拆解/重规划/派发/上升/watchdog)= **inline 调 Port,系统执行,非 SKILL**。
> **只有"状态判断 + 验收"由 owner-bot 经 SKILL 自判后回投上报**。
> **Scheduler 不判验,owner-bot 不编排**;两者经回投 + `TaskService` 状态组耦合。
> **状态唯一改入口 = `TaskService` 状态组**(guard → fold → append → save)。

本修订**不改变**这条口径,只是把它从"注释级"提升到"领域模型契约级"。

---

## 3. 角色澄清(消解"搜推 bot"心智)

系统里只有**三类 bot + 一类系统**,不存在"搜推 bot":

| 角色 | 是谁 | 干什么 | 机制 |
|---|---|---|---|
| **系统** | TaskScheduler / TaskService / Ports | 编排 + 改态 + 搜推 | inline Port,非 SKILL |
| **user Bot** | 任务发起者的 bot | 录入需求 + 初始拆分 | task-recognition SKILL + 任务拆分 SKILL |
| **owner bot**(变身) | 验收发起人 | 子任务验收 + 全局终验 | 子任务验收 SKILL + 判断完成 SKILL |
| **worker / 广场 BBS bot** | 纯执行方 | 产 artifact,不判验 | 子任务执行 SKILL |

**关键修正**:**搜推 = 系统 `BotDiscoverPort.recommend`**(`protocols.py`),`TaskScheduler.tick` inline 调用,读 bot catalog 做能力 cover 匹配返回候选 + RouteClass(C1~C5)。**没有任何 bot 装备"搜推 SKILL"**。后续文档/讨论中"搜推 bot"一词应统一改为"系统搜推(Port)"。

---

## 4. 执行者映射(每步谁干)

### 4.1 任务拆分(两个执行者,按阶段分)

| 阶段 | 执行者 | 机制 | 能力来源 | 落口 |
|---|---|---|---|---|
| **初始拆解**(DRAFTING→DEFINED) | **user Bot**;若起手即拉群则为拉群 **master** | 任务拆分 SKILL | `DecomposerPort` | 回投 `PLAN_FINALIZED` → `TaskService.finalize_plan` |
| **运行期拆解**(EXECUTING,搜推 cover<100%) | **系统 TaskScheduler** | inline Port(非 SKILL) | `DecomposerPort` | `TaskService.add_sibling_node` + 父 SKIPPED |

> `DecomposerPort` 是**共享能力**:初始期由 user-bot/master 的 SKILL 调,运行期由 Scheduler inline 调。能力同一,执行者不同。详见 §5.1。

### 4.2 编排(全系统 TaskScheduler,非 SKILL)

| 编排动作 | 时机 | 依赖 Port(inline) | 落态(经 TaskService) |
|---|---|---|---|
| 搜推执行方 | 派发前 | `BotDiscoverPort.recommend` | — |
| 路由决策 C1~C5 | 搜推后 | `_route` 纯规则 | `set_node_status(RUNNING)` |
| 派发执行 | 路由后 | `ExecutionPort.dispatch`(single/coop/bbs) | `set_node_status(RUNNING)` |
| 运行期拆解 | cover<100% | `DecomposerPort.decompose_node`(见 §5.1) | `add_sibling_node` + 父 SKIPPED |
| LOOP 重规划 | 验收 fail 回投后 | `_compute_gap` + replan/split | `add_sibling_node` / `set_node_status` |
| BBS 上升编排 | 全局终验 fail + 人工确认 | `ExecutionPort.dispatch_bbs` | `mark_graph_status` + 终态推进 |
| 看门狗探活 | tick 超时(RUNNING 节点) | `ExecutionPort.probe` + `watchdog` | PROBE/REDRIVE/ESCALATE |
| **改态(唯一写口)** | 所有上述 | — | `TaskService` 状态组 |

> Scheduler **持零状态**,所有写经 TaskService;决策(`_route`/`_compute_gap`/`_select_collab`)是纯规则函数。

### 4.3 执行 + owner 变身(按 run_mode)

| run_mode | 执行方(产 artifact) | **owner bot**(验收人) | 协作模式 |
|---|---|---|---|
| `SINGLE_BOT`(C1/C2) | 该 bot 本身 | **该 bot 自身**(自验收) | — |
| `COOP_GROUP`(C3)+ chat | 群内 worker bots | **拉群 driver(master)** | 自由聊天 |
| `COOP_GROUP` + manager_worker | 群内 worker | **master(driver)** | 主从 |
| `COOP_GROUP` + state_machine | 群按注入 workflow 自闭环跑 | **master(driver,群汇总后验收)** | 自定义协作 |
| `BBS`(C5,task 内部) | 广场 BBS bots(自主认领) | **task-owner**(原录入任务的 user bot) | — |

> **owner bot 是变身角色**:单 bot=执行者自己;协作群=master;BBS=task-owner。详见 §5.2。

### 4.4 验收与回投(owner-bot SKILL,非系统)

| Skill | 装备者 | 时机 | 回投事件 |
|---|---|---|---|
| task-recognition(任务录入) | user Bot | DRAFTING | `SPEC_AMENDED` |
| 任务拆分(initial plan) | user Bot / master | DRAFTING→DEFINED | `PLAN_FINALIZED` |
| 子任务-执行 | worker / 单 bot | Node RUNNING | `NODE_DONE/FAILED`(状态上报) |
| **子任务-验收** | **owner bot**(见 §4.3) | artifact 后 | `NODE_ACCEPTED/REJECTED` |
| **判断完成**(全局终验) | **owner bot**(同一) | 全 DONE→REVIEWING 触发 | `GOAL_VERIFIED`(PASS/FAIL) |
| sibling 聚合验收(见 §5.4) | owner bot | sibling 全 DONE,对父 `targets_acceptance` 聚合复核 | `NODE_ACCEPTED/REJECTED`(父节点) |
| 通知用户(钉钉) | user Bot / master | 关键节点/交付 | USER_CONFIRM/REJECT |

回投通道统一:`POST /api/tasks/{id}/events` → `TaskService.on_event`(落态)+ `Scheduler.on_event`(编排反应)。**TaskService/Scheduler 不自调 `check_node/check_goal`**。

---

## 5. 领域模型修订点

### 5.1 `DecomposerPort` 两用途签名(消解协议二义)

**现状**(`core/task/protocols.py`):

```python
class DecomposerPort(Protocol):
    def decompose(self, task_id: str) -> Plan: ...
```

单签名服务于"初始 plan"(整图 Plan)。但 §5.2 运行期拆解要的是"把一个节点 spec 拆成 children 做 sibling",语义不是"给 task 出整张 Plan"。当前协议形态偏初始 plan,运行期 sibling 拆解的接线没在协议里显式表达。

**修订**:拆成两个语义清晰的签名(同一 Port,两用途):

```python
@runtime_checkable
class DecomposerPort(Protocol):
    def decompose(self, task_id: str) -> Plan:
        """初始拆解:DRAFTING 期由 user-bot/master 的任务拆分 SKILL 调用。
        产整图 Plan(sub_tasks + edges + confidence≥0.7,验收覆盖)。
        回投 PLAN_FINALIZED → TaskService.finalize_plan。"""

    def decompose_node(self, node_spec: str, constraints: list[str] | None = None) -> list["SubTaskSpec"]:
        """运行期拆解:EXECUTING 期由 TaskScheduler inline 调用(非 SKILL)。
        把单个 FAILED/cover<100% 节点的 spec 拆成 children(sibling)。
        Scheduler 经 TaskService.add_sibling_node 落态,父节点 SKIPPED。"""
```

> 两签名共享底层 SPARC/GOAP 能力,但**调用方不同**(初始=bot SKILL,运行期=系统)且**返回粒度不同**(整图 Plan vs 节点级 children)。分开后,"任务拆分流程"的两个执行者在协议层即区分,不再靠注释。

### 5.2 `owner bot` 变身表正式化(进领域模型契约)

**现状**:`plan.md §6.1` 一张注释表说明"owner bot 随 run_mode 变身",但领域模型(`Task`/`Node`/`protocols`)里没有这个概念,只是隐式约定。

**修订**:在 `core/task/protocols.py` 显式声明 owner 解析契约:

```python
@dataclass
class OwnerBotRef:
    """某节点/任务的验收发起人 bot 解析结果。owner 是变身角色,按 run_mode 推导。"""
    bot_id: str
    role: str  # "self" | "master" | "task-owner"


@runtime_checkable
class OwnerResolver(Protocol):
    """解析一个节点的 owner bot(验收发起人)。
    SINGLE_BOT  → 执行方自身(self)
    COOP_GROUP  → 拉群 master/driver
    BBS         → task-owner(原录入 user bot)
    实现可读 Node.run_mode / Task.spec.metadata 推导;纯解析,不写态。"""
    def resolve(self, task_id: str, node_id: str) -> OwnerBotRef: ...
```

> 这让"验收发起人是谁"从约定变成可注入、可测试的 Port。Scheduler/TaskService 在触发"判断完成 SKILL"和回投 verdict 时,经 `OwnerResolver` 拿到应回投给谁的 owner bot,而不是在各处 if-else。

### 5.3 BBS 的 task-owner 执行链路(消解 §5.4 断点)

**现状**:`plan.md §5.4` 说"BBS 节点验收 + 全局终验都由 task-owner 经 SKILL 回投(run_mode=BBS)",但**task-owner 是录入期 user bot,BBS 上升时它如何被对接、是否还活着、如何被重新唤起做验收**没写清。这是我前序 review 发现的"BBS escalation 半成品断层"之一。

**修订**:明确 BBS 上升时 task-owner 的对接契约:

```
on 用户确认上升 BBS(POST /escalate-bbs):
  1. TaskService.mark_graph_status(ON_PLAZA)
  2. Scheduler.escalate_to_bbs → ExecutionPort.dispatch_bbs(task_id, unfinished_subtasks, progress_snapshot)
     —— progress_snapshot 必含:未完成节点的 targets_acceptance + 已产出 artifact refs(给 BBS bot 续做上下文,消解"半成品丢失")
  3. 广场 BBS bot 经 BbsExecutor.claim 自主认领执行(已有)
  4. BBS 节点验收:由 task-owner 经子任务验收 SKILL 回投(run_mode=BBS)→ /events → on_event
     —— 关键:task-owner bot 在 BBS 上升时由 ExecutionPort.dispatch_bbs **显式重新唤起/绑定**到该任务(经 owner_bot_id 写在 Task 上,见 §5.2 OwnerResolver 读取)
  5. BBS 全局终验:task-owner 经判断完成 SKILL 回投 GOAL_VERIFIED(run_mode=BBS)
     PASS → DONE;FAIL → 任务 FAILED(终态,不回环)
```

新增字段(可选,落在 `Task` 聚合根):

```python
@dataclass
class Task:
    ...
    owner_bot_id: Optional[str] = None  # 录入期 user bot;BBS 上升时作为 task-owner 验收人被绑定
```

> `OwnerResolver` 在 BBS 链路读 `task.owner_bot_id` 返回 `task-owner`。这让"BBS 验收人是谁"从隐式变成持久化、可重新唤起。

### 5.4 运行期拆解的 sibling 聚合验收(补 §5.2 缺失)

**现状**:`plan.md §5.2` 运行期拆解父节点 SKIPPED 委托 sibling 聚合验收,但"谁在何时做聚合复核、父节点的 `targets_acceptance` 何时被判定"没写清。

**修订**:明确聚合验收触发点与执行者:

```
运行期拆解(§5.2)后:
  父节点 SKIPPED;sibling 节点各自 EXECUTING → 各自 owner bot 验收(NODE_ACCEPTED/REJECTED)
  当父节点所有 sibling 都 DONE 时:
    Scheduler.tick 检测到"父节点 SKIPPED 且 sibling 全 DONE" → 触发 owner bot 经子任务验收 SKILL
    对**父节点原 targets_acceptance** 做聚合复核(sibling 产出合并 vs 父验收标准)
    回投 NODE_ACCEPTED(父)/ NODE_REJECTED(父)
    父 ACCEPTED → 父视作 DONE(下游解锁)
    父 REJECTED → 走 §5.3 同节点重路由(对父节点,非 sibling)
```

> 聚合验收的执行者 = **父节点的 owner bot**(经 `OwnerResolver` 解析),与"判断完成"SKILL 同族,但作用域是父节点而非整任务。这补上了 §5.2 → 全局终验之间的一环:父节点不会永远停在 SKIPPED,sibling 全 DONE 后必有一次聚合验收闭合。

### 5.5 初始拆解 owner 二义消解( §6.0b "user Bot/master" 拆明)

**现状**:`plan.md §6.0b` 任务拆分 Skill 装备者写"user Bot/master",单 bot 场景是 user Bot,协作群场景是 master,二义。

**修订**:§4.1 已拆明:

- 任务起手为单 bot 链路 → 初始拆解 owner = **user Bot**(任务发起者 bot)。
- 任务起手即拉群 → 初始拆解 owner = **拉群 master**(driver)。

判定依据:任务创建时的 `ExecutionMeta.run_mode`(若已指定 COOP_GROUP,则 owner=master;否则 user Bot)。母 plan §6.0b 该行应同步改为两行。

---

## 6. 执行架构图(who-does-who,修订后)

```
┌─────────────────────── 系统(后端,inline Port,非 SKILL)────────────────────┐
│  TaskScheduler(编排,纯决策,不写态)                                          │
│    ├─ BotDiscoverPort.recommend     ← 搜推执行方(系统Port,非bot)            │
│    ├─ _route C1~C5 / _select_collab / _compute_gap(纯规则)                    │
│    ├─ DecomposerPort.decompose_node ← 运行期拆解(系统inline,§5.1)            │
│    ├─ ExecutionPort.dispatch / probe / redispatch / dispatch_bbs             │
│    ├─ watchdog                                                                │
│    └─ OwnerResolver.resolve         ← 解析验收发起人(§5.2,纯解析)            │
│  TaskService(唯一写口:guard→fold→append→save)                               │
│    └─ on_event / claim_node / set_node_status / advance_phase                │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ POST /api/tasks/{id}/events(回投)
┌──────────────────────────────┴───────────────────────────────────────────────┐
│  bot 侧(via SKILL,只产事件、不改态、不直调 task)                              │
│    user Bot ── task-recognition + 任务拆分 SKILL(初始 plan,调 DecomposerPort)│
│    owner bot(变身,经 OwnerResolver 解析)── 子任务验收 + 判断完成 + sibling 聚合验收│
│       ├─ SINGLE_BOT → 该 bot 自己                                           │
│       ├─ COOP_GROUP → master/driver                                         │
│       └─ BBS → task-owner(=task.owner_bot_id,§5.3)                         │
│    worker / BBS bot ── 子任务执行 SKILL(产 artifact,不判验)                   │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. 需求列表(修订项,FR-EXE-*)

> 以下为本次修订引入的需求;母 spec 的 FR-TASK/FR-DISPATCH/FR-ACCEPT/FR-LOOP/FR-EVENT/FR-COLLAB/FR-OBS 不变,本文不重复。

- **FR-EXE-01** 搜推在所有文档/代码注释中表述为"系统 `BotDiscoverPort`(Port)",不得出现"搜推 bot"/"搜推 SKILL"字样。
- **FR-EXE-02** `DecomposerPort` 暴露两个语义签名:`decompose(task_id)->Plan`(初始,bot SKILL 调)与 `decompose_node(node_spec, ...)->list[SubTaskSpec]`(运行期,Scheduler inline 调)。
- **FR-EXE-03** owner bot 变身经 `OwnerResolver` Port 解析(`resolve(task_id,node_id)->OwnerBotRef`),返回 self/master/task-owner;Scheduler/TaskService 不内联 if-else 判 owner。
- **FR-EXE-04** `Task` 聚合根新增 `owner_bot_id: Optional[str]`,录入期由 user bot 写入;BBS 上升时作为 task-owner 验收人绑定。
- **FR-EXE-05** BBS 上升时 `ExecutionPort.dispatch_bbs` 必须携带 `progress_snapshot`(未完成节点 targets_acceptance + 已产出 artifact refs),消解半成品断层;并显式重新唤起 `task.owner_bot_id` 作为 BBS 链路验收人。
- **FR-EXE-06** 运行期拆解后,父节点 SKIPPED 且 sibling 全 DONE 时,Scheduler 触发 owner bot 对父 `targets_acceptance` 做聚合验收;父不得永久停在 SKIPPED。
- **FR-EXE-07** 初始拆解 owner 二义消解:起手 COOP_GROUP → master;否则 → user Bot。母 plan §6.0b 同步拆行。

---

## 8. 验收标准(AC)

| ID | 验收标准 | 验证方式 |
|---|---|---|
| AC-01 | 全仓 `grep -r "搜推bot\|搜推 SKILL"` 为空;搜推统一称"系统 BotDiscoverPort" | 全文检索 |
| AC-02 | `DecomposerPort` 有 `decompose` 与 `decompose_node` 两签名;初始拆解仅调 `decompose`,运行期拆解仅调 `decompose_node` | 代码审查 + 契约测试 |
| AC-03 | `OwnerResolver` Port 存在;触发"判断完成 SKILL"/回投 verdict 处经 `OwnerResolver.resolve` 取 owner,无内联 if-else | 代码审查 |
| AC-04 | `Task` 有 `owner_bot_id` 字段;BBS 上升走 `dispatch_bbs` 时 `progress_snapshot` 非空且 `task.owner_bot_id` 被绑定 | 单测 + 场景 E2E |
| AC-05 | 父节点 SKIPPED + sibling 全 DONE → 必触发一次聚合验收事件(`NODE_ACCEPTED/REJECTED` 作用父节点);父不会永久 SKIPPED | 单测 |
| AC-EXE-06 | 母 `2026-07-28/plan.md` §5.2/§5.4/§6.0b/§6.1 已按本修订同步(或本文标注为权威,母 plan 标 superseded) | 文档对照 |

---

## 9. 场景(覆盖修订点)

### 9.1 场景 A:单 bot,初始拆解 owner=user Bot
用户 user Bot 录入需求 → task-recognition SKILL → 任务拆分 SKILL 调 `DecomposerPort.decompose` → `PLAN_FINALIZED` → `finalize_plan`(DEFINED)→ approve → EXECUTING → 该 bot 自执行 + 自验收(owner=self)→ 全 DONE → REVIEWING → 判断完成 SKILL → `GOAL_VERIFIED` → DONE。

### 9.2 场景 B:起手拉群,初始拆解 owner=master
用户拉群建任务 → master 装备任务拆分 SKILL 调 `DecomposerPort.decompose` → 群内 worker 各自执行 → master(owner)逐节点验收 → 全 DONE → master 判断完成 → DONE。验证 FR-EXE-07。

### 9.3 场景 C:运行期拆解 + sibling 聚合验收
某节点搜推 cover<100% → Scheduler inline 调 `DecomposerPort.decompose_node` → `add_sibling_node` + 父 SKIPPED → sibling 各自执行验收全 DONE → Scheduler 触发 owner bot 聚合验收父 `targets_acceptance` → 父 ACCEPTED → 下游解锁。验证 FR-EXE-02/06 + AC-05。

### 9.4 场景 D:BBS 上升,task-owner 验收
全局终验 fail → 人工确认 → `dispatch_bbs(task_id, unfinished, progress_snapshot)`(snapshot 含未完成节点验收标准 + 已产出 artifact refs)→ `task.owner_bot_id` 绑定为 BBS 验收人 → BBS bot 自主认领执行 → task-owner 经子任务验收 SKILL(run_mode=BBS)回投 → BBS 终验 `GOAL_VERIFIED` → DONE / FAILED。验证 FR-EXE-04/05 + AC-04。

### 9.5 场景 E:OwnerResolver 三形态同案
同一任务下混三模态节点(单 bot / 协作群 / BBS 上升后):`OwnerResolver.resolve` 对单 bot 节点返回 self、协作群节点返回 master、BBS 节点返回 task-owner;验收回投都打到正确 owner bot,无内联 if-else。验证 FR-EXE-03 + AC-03。

---

## 10. 开放问题(需评审拍板)

| # | 问题 | 取向建议 |
|---|---|---|
| O-1 | 本修订是否直接落进母 `2026-07-28/plan.md`(in-place 改 §5/§6),还是母 plan 标 superseded、本文独立留存? | 倾向 in-place 改母 plan §5.2/§5.4/§6.0b/§6.1,本文作为修订说明留存(带 "已合并至母 plan" 标记)。需评审。 |
| O-2 | `OwnerResolver` 是新建 Port,还是复用 `BotDiscoverPort` 扩 `resolve_owner`? | 倾向新建独立 Port(owner 解析与搜推是不同关注点,单一职责)。 |
| O-3 | `decompose_node` 返回 `list[SubTaskSpec]` 还是 `list[Node]`? | 倾向 `SubTaskSpec`(plan 期描述符),由 `add_sibling_node` 物化成 Node,与初始拆解一路同构。 |
| O-4 | `progress_snapshot` 是写进 BBS 的 `instruction` 上下文,还是单独字段? | 倾向进 BBS 认领节点的 `instruction`(对齐"重做带上下文"),不新增图字段。 |
| O-5 | 母 plan §6.1 owner 变身表与本文 §5.2 `OwnerResolver` 的关系:表当文档、Port 当执行? | 倾向表保留为文档说明,Port 为执行契约,两者不矛盾。 |

---

## 11. 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-07-31 | 待确认 | 初版:对 `2026-07-28-goal-driven-task-execution/plan.md` §5/§6 做执行者对齐修订——澄清"搜推非 bot"(§3)、`DecomposerPort` 拆两签名(§5.1)、`OwnerResolver` 正式化 owner 变身(§5.2)、BBS task-owner 执行链路(§5.3)、sibling 聚合验收(§5.4)、初始拆解 owner 二义消解(§5.5)。沿 7 态状态机(`2026-07-30-task-status-state-machine-alignment`)。不含代码改动;若采纳,后续另起 tasks/implement。 |