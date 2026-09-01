# 分布式接力任务设计（2026-09-01）

> **状态：DRAFT，待评审。**
> 本稿在 2026-08-31 brainstorm（会话 `388f269e`）四节基础上，按 2026-09-01 三处拍板重订并补第 5 节测试矩阵。
> 三处拍板：① 兜底=BBS 子节点（非翻 root.bbs_mode）；② 收敛=去指针、每段 self-judge residual 空→上溯；③ 入口识别 skill 建 root、默认 `distributed_relay`。
>
> **调研锚点为代码时间戳 `3d8339cf2`（2026-09-01 dev_task_0830_rebase_dev）；具体行号实施前必复核**（活跃分支会 auto-pull，行号易漂）。

## 0. 目标与边界

- 对齐真实协作形态：谁会谁做、做不动找下家交棒，**无中心规划器**。
- 识别 skill 识别任务后建 root `TaskInfo`，**默认 `task_type=distributed_relay`**（不做 relay/非relay 分类门，distributed_relay 是默认形态）。
- **核心逻辑在 relayer 跑的 RELAY skill**；框架只提供「原语 + 端点」。
- 不复用 BBS 入口（BBS 入口是 HUNG 升级 / 公开认领；RELAY 入口是直接派给首个接力者开跑）。
- 复用面：`single_bot` / `coop_group` 派发、搜推（`SearchBasedDispatchStrategy`）、`bbs-relay-pickup`（认领对象从 root 扩到嵌套子节点）、graph 原语（add_task_nodes / CAS）、harness 旁路巡检、`task_settings` 配置。

## 1. 总体流程（三 Phase）

### Phase 0 入口 · 任务识别

入口处的单 bot 或协作群跑「**任务识别 skill**」→ 识别出任务 → 创建 root `TaskInfo`（`type=distributed_relay`、**非执行收敛头**），记 goal / 背景 / 验收标准 / 当前 gap（入口时 gap=整个 goal）→ 把 handoff payload `{taskId, 目标, 背景, 验收, 当前距离目标还差什么}` 转给**同一 bot/群**。

框架据此建 **`seg1`（root 的 child，`assignee=入口 owner`，`relay_seq=1`）** 并派发；入口 owner 的 **`driver_bot`** 开跑 RELAY skill 当第一棒。root 不执行，只作收敛头（同 BBS root）。

### Phase 1 单棒回路（RELAY skill，跑在 driver_bot）

1. `POST /relay/read` 读 `root.goal.acceptances` + 链上祖先段已 DONE 产出 → 算当前 gap。
2. 自裁「能完成多少」（PASS-size 子集）→ 执行 → 产出 `output_patch`。
3. `POST /relay/update` 回投 → 本段 DONE。
4. 重算 `residual = root.goal.acceptances − 链上所有 DONE 子孙产出`。
5. residual 空 → 收敛（Phase 2）。
6. 否则 `POST /relay/find-next`（residual gap_text）搜推：
   - **hit** → `POST /relay/append(mode=relay)` 在本段下挂 **relay 段 child**（下棒），框架派发给 target 并通知 → 下家接力，回 1。
   - **miss** → `POST /relay/append(mode=bbs)` 在本段下挂 **`bbs_mode/claimable` child**（无人 owner），交 **BbsScheduler** 周期扫到 → 调已有 `bbs_runner.notify`（传 RELAY 延续 skill）→ 广播 bid → 选 completion_rate 最高 → `claim_bbs_owner` CAS 认领该嵌套 child → 派发 → winner 在该 child 下接着跑 RELAY **延续段**（继承接力义务），回 1。

> found / 未中**同形**：都是「挂 child」，只是 child 的首棒 owner 获取方式不同（直派 vs 公开认领）。

### Phase 2 收敛

任一段在 `update` 后判 residual 空 → 框架沿 `predecessor_node_id` 链上溯，把沿途 DONE 段产出 fold 进 `root.goal.acceptances` → root DONE。安全性靠 §2 的 single-active 不变量；翻 root DONE 前框架复核 root acceptance 真满足，防 skill 误判提前闭合。

## 2. 图模型与状态机

- **嵌套单链**：`root → seg1 → seg2(seg1 的 child) → seg3(seg2 的 child)…`。每段最多挂**一个**接力 child（found/未中二选一）→ 任一时刻**至多一个段在 RUNNING**（认领等待期可为 0），即 **single-active 不变量**。它保证「某段 finish 时是唯一在跑者」：residual 空 = 真的没剩，可触发收敛而无需 stored head 指针。
- **段节点**：`run_mode = single_bot | coop_group`（搜推 `HIT_SINGLE/HIT_GROUP` 决定）；`extend_props` 带 `relay_seq`（嵌套深度 = 前驱 +1）、`predecessor_node_id`、`relay_reason` + `recommend_score`、`bbs_mode`（可选，claimable）；`task_spec` = 前驱按值传入的残留 gap 描述。
- **两种形态（结构同，首棒 owner 获取方式异）**：relay 段（直派） / bbs-open 段（append mode=bbs，等待公开认领）。
- **root**：`extend_props.relay_mode=true`（可选 hint），不执行。状态 `PENDING → RUNNING`（seg1 派发）→ `DONE`（residual 空）/ `HUNG`（深度 + 广场兜底仍无人接）。
- **seg**：`PENDING`（被 append）→ `RUNNING`（下家开工）→ `DONE`（做完且已交棒 or 全空）/ `DONE-skipped`（本棒一步都做不了，residual 转给 child）。
- **两条转广场路口**（本设计里都 = `append mode=bbs`）：① find-next miss；② `relay_seq+1 > RELAY_MAX_DEPTH`（超深，本棒 residual 转公开认领）。
- **双限深防环**：`RELAY_MAX_DEPTH`（嵌套深，默认 3，`task_settings` 可调）+ `relay_square_round`（每「claim→又无缘续接→再挂 bbs child」一圈 +1，默认上限 3）。任一超 → root `HUNG`（`hung_reason=relay_exhausted`）。

## 3. 端点、入口与 skill 编排

- **入口**：识别 skill 建 root → 框架建 `seg1` 派入口 owner → RELAY skill 起跑。
- **4 个端点**（挂 `/api/v1/collaboration/tasks/`；旧 `publish-square` 折进 `append mode=bbs`）：
  1. `POST /relay/read` — 入场读 `root.goal.acceptances` + 链上祖先段 DONE 产出，供算 gap。
  2. `POST /relay/find-next` — `{task_id, gap_text}` → 复用 `SearchBasedDispatchStrategy._prefetch_candidates`（jieba 分词 gap_text + `discover.search_by_keyword` 按推荐分排序 + owner search-skill 定 `HIT_SINGLE/HIT_GROUP`）→ `{hit_type: bot|group|miss, target, recommend_score, relay_reason}`。**只读不改图**。
  3. `POST /relay/append` — `{task_id, parent_node_id, next_task_spec, target, hit_type, mode: relay|bbs}` → 校验 `parent.relay_seq+1 ≤ RELAY_MAX_DEPTH`；超 → 返 `depth_exceeded`（skill 走 `mode=bbs` 或终止判 HUNG）；否则 `graph.add_task_nodes` 在 `parent` 下挂 child：
     - `mode=relay`：`assignee=target`，`run_mode=single_bot/coop_group`，派发 + 通知。
     - `mode=bbs`：`assignee=None`，`extend_props.bbs_mode=claimable`，不派发（等现有 pickup 认领）。
  4. `POST /relay/update` — `{task_id, node_id, output_patch, status, residual, residual_empty}` → CAS by `node_id` 幂等去重（已 DONE 段重复回投不再 fold）；fold 本段产出 + 本段 DONE；`residual_empty=true` → 沿 `predecessor_id` 链把沿途 DONE 段产出 fold 进 root acceptance → 复核满足 → root DONE。
- **桥接（兜底=BBS 子节点，相对旧「翻 root bbs_mode」的改动）**：`mode=bbs` child 标 `bbs_mode/claimable`、无人 owner，**并非被动等认领**——由 **BbsScheduler**（见下）扫到后调已有 `bbs_runner.notify` 主动招募。认领对象从旧 `root` 扩到**嵌套子节点**：`claim_bbs_owner` CAS 在 child 上、`attach_bbs_node` 放宽可挂 claimable child（小扩 `_DELEGATABLE_PARENT`）、`on_bbs_report`/`run_bbs` 键控「被认领节点的 bbs_mode」而非 `root.type`；winner 跑 **RELAY 延续段**（非通用 BBS 一锤子），继承找下家义务 → 命中 `append mode=relay` 嵌套续接 / 仍 miss 再 `append mode=bbs`。

### BbsScheduler 触发（兜底拾取）

- `mode=bbs` child 被 append 后，由统一 **BbsScheduler** 触发拾取（非 inline、非被动等抢），两 cadence 由该 child 的配置选：
  1. **即时触发（默认 `realtime`）**：分钟级扫「新产生的 bbs 任务」→ 命中即调已有 `bbs_runner.notify`（传 RELAY 延续 skill）→ 广播 bid → 选 completion_rate 最高 → `claim_bbs_owner` CAS → 派发 → winner 跑 RELAY 延续段。
  2. **Hourly 小时触发**：否则按小时级扫同流程。
- 触发模式配置在 **`task_node_run_info.extend_props`**：`bbs_trigger_mode ∈ {realtime, hourly}`，默认 `realtime`。Scheduler 依各 bbs 节点配置选 cadence 池。
- **范围（锁定 A）**：BbsScheduler **只管本流程新增的 `mode=bbs` child**；存量主动接力链路（`_maybe_propagate_hung` → 内联 `run_bbs`）保持不变，不统一。
- 机制来源：复用现有 APScheduler 模式（`task_discovery/scheduler.py` 的 `TaskDiscoveryScheduler`）；`BbsScheduler` 新建还是扩展 `TaskDiscoveryScheduler`、`bbs_runner.notify` 从「认领 root + BBS skill」扩到「认领嵌套 claimable child + 传 RELAY 延续 skill」，留实施计划。

- **收敛**：`relay_head` 指针**去除**；上溯由 `residual_empty` 在 `update` 内触发，沿 `predecessor_id` 走。Dashboard 若要「当前接力在第几段」，可惰性算一次最深活动段，**不作收敛触发**。

## 4. 异常、并发、幂等与失败断点

- **幂等**：`relay/update` 按 `node_id` CAS；`relay/append` 按 `(predecessor_node_id + relay_seq)` 唯一约束（`mode=bbs` 也是，同前驱同序号只挂一个 bbs child）。
- **并发**：`find-next` 只读可并发；`append`/`update` 写图/行锁串行；bbs 认领复用 CAS，抢输 409；single-active 保证不会有多段并发 trigger 收敛。
- **段执行异常**：`exec_error` → 本段不 fold、可重试；skill 崩 / 段卡 `RUNNING` → harness 旁路巡检超时：重试本段，仍败 → 在其父下 `append mode=bbs` 兜底，或 root `HUNG`。`residual_empty` 翻 root DONE 前框架复核 root acceptance 真满足。
- **派发降级**：find-next 返回 group 但 `form_coop_group` 失败 → 降级再 find-next 找单 bot，或直接 `append mode=bbs`。
- **BbsScheduler 幂等/单飞**：分钟级扫为只读；`bbs_runner.notify` 用 child 的 `claimable` 状态守门（已被 `claim_bbs_owner` 认领 → 非 `claimable` → 不再 bid），同 child 不会被多轮反复招募；`bbs_trigger_mode` 只选 cadence 池，不改认领结果。

## 5. 测试矩阵

| #  | 场景 | 期望 |
|----|------|------|
| T1 | 单棒：seg1 做完全部，residual 空 | root DONE，无子段 |
| T2 | 多棒链：seg1→seg2→seg3，seg3 residual 空 | 沿链 fold→root DONE，relay_seq 链 1→2→3 |
| T3 | 兜底：seg find-next miss → append mode=bbs child | bbs child `bbs_mode=claimable`；被 bot 认领→跑 RELAY 延续→解→root DONE |
| T4 | 深度限：append 时 `relay_seq+1 > RELAY_MAX_DEPTH`(3) | 返 `depth_exceeded`；走 `mode=bbs`（残转广场），不再嵌套 relay |
| T5 | 广场 churn：claim→miss→再 bbs 反复 > `relay_square_round`(3) | root `HUNG`（`hung_reason=relay_exhausted`） |
| T6 | 幂等-重复 update 同 `node_id` | 只 fold 一次，root 不重复翻 DONE |
| T7 | 幂等-重复 append 同 `(predecessor, relay_seq)` | 只挂一个 child |
| T8 | 并发-两个 find-next | 并发只读，OK |
| T9 | 并发-append / 写图竞态 | 行/图锁串行，不出现双 child |
| T10 | bbs 认领竞态 | CAS 单赢家，输家 409 |
| T11 | 跳棒：seg PASS-size=0（一步做不了） | DONE-skipped；append child（relay or bbs）转下 |
| T12 | 协作群派发：find-next HIT_GROUP | `run_mode=coop_group`，driver_bot 跑 RELAY skill |
| T13 | 误判收敛：skill 报 `residual_empty` 但 acceptance 未真满足 | 框架复核阻断，root 不翻 DONE |
| T14 | 派发降级：group 组建失败 | 降级单 bot 或 `append mode=bbs` |
| T15 | 段卡死：seg RUNNING 超时 | harness 超时→重试 / 转 bbs，最坏 HUNG |
| T16 | bbs 即时触发：`mode=bbs` child append 后 ≤1 分钟 | BbsScheduler 扫到 → `bbs_runner.notify` → 认领 → winner 跑 RELAY 延续 |
| T17 | bbs hourly 触发：child `extend_props.bbs_trigger_mode=hourly` | 不被分钟扫命中，按小时扫时才招募 |
| T18 | bbs 重复防止：同一 claimable child 已被认领 | 下次扫到 status 非 `claimable` → 不重复 bid/select |

## 6. 与现状的差异 / 复用面

- **新增**：`task_type=distributed_relay`（默认形态，非分类门）；RELAY skill；4 个 `/relay/*` 端点；`task_settings` 增 `RELAY_MAX_DEPTH` / `relay_square_round`。
- **复用**：`single_bot`/`coop_group` 派发、`SearchBasedDispatchStrategy` 搜推、`graph.add_task_nodes`/CAS、harness 旁路巡检。
- **改动（小）**：`bbs-relay-pickup` 认领对象 root→嵌套子节点（list/claim/`_DELEGATABLE_PARENT` 扩展）；`on_bbs_report`/`run_bbs` 键控 bbs_node 而非 `root.type`；新增 **BbsScheduler**（复用 APScheduler 模式，只管 `mode=bbs` child，分钟默认/小时可选）+ `task_node_run_info.extend_props.bbs_trigger_mode`；`bbs_runner.notify` 从「认领 root + BBS skill」扩到「认领嵌套 claimable child + RELAY 延续 skill」。
- **不动**：存量 BBS inline `run_bbs` 路径（锁定 A，范围不统一）。
- **去掉**：stored `relay_head` 指针（收敛改每节点上溯）。

## 7. 待办 / 未决

- 识别 skill **新建 vs 挂现有 task-discovery**：留到实施计划定。
- 超深时是「转广场继续」还是「直接 HUNG」：当前定超深→`mode=bbs`（残转广场），双限深再 HUNG。可回看。
- `_DELEGATABLE_PARENT` 与 `attach_bbs_node` 从「挂 root」扩到「挂 claimable child」的最小改法：实施时定。
- 入口"创建 root + 派 seg1"是单端点还是复用现有 task 建链：实施时定。
- **BbsScheduler** 新建 vs 扩展现有 `TaskDiscoveryScheduler`；分钟/小时双 cadence job 的注册、扫描谓词（`bbs_mode=claimable` 且未被认领）、单飞/幂等边界：实施时定。
- `bbs_runner.notify` 从「认领 root + `_BBS_SKILL_NAME`」扩到「认领嵌套 claimable child + RELAY 延续 skill」的传入与 skill 解析：实施时定。
