# BBS 自主接单 skill — 系统设计规格(spec.md)

> 负责人:蒋建。背景对比见语雀《接力执行方案对比》(https://yuque.antfin.com/mad/enxdbg/ncmbmw3z37kh8sm6)。
> 落点域:ocb backend(task 域)+ bot/agent 自有的内容 skill;具体代码落点(新路由挂载、清扫器、skill 目录)属 HOW,见 `plan.md`。
> 上游基线:`src/backend/specs/2026-07-28-goal-driven-task-execution/spec.md`(目标驱动任务 loop)、`2026-07-30-task-status-state-machine-alignment/`(9 态状态机权威)。
> 日期:2026-08-04。

---

## 1. 概述

### 1.1 背景

语雀《接力执行方案对比》给"任务协作广场"提了两套设计:方案 A(插件模式,引擎内自驱)与方案 B(外部调度器模式,BCS 会话驱动)。两份详细设计不在本仓库;本仓库现有的是 `goal-driven-task-execution` 任务系统——9 态状态机(GraphStatus 单一权威,SSOT=`execution_graph.status`)、动态生长 DAG、owner-bot 经 SKILL 验收、回投通道统一写态,且 **BBS 模态在 core 已实现**(`BbsExecutorService.claim` 是现成 CAS 认领原语,`run_mode=BBS`,`BBS_ACTIVE` 状态机合法),但 `claim`/`post_progress`/`retrieve_state` **均未挂 HTTP**,BBS bot 接入只能进程内。

本设计是**第三形态**:把"自主接单接力执行"做成 **bot/agent 自己拥有的一个内容 skill**,经 REST 复用现有 task API,bot 自驱拉单-判断-抢占-执行-写回。它既不是方案 A(不依赖 `openclaw/plugin-sdk`,故不重蹈"每引擎一份"硬伤),也不是方案 B(不引入单点全局调度器)——编排下沉进可移植的 skill 内容,抢占/收口语义收口在现有 TaskService 的状态机。

### 1.2 这是什么

一个**内容 skill `bbs-relay-pickup` + 现有 task API 的最小 REST 扩展**,让任意引擎的 bot 在被唤醒后自主完成 6 步:①从 task API 取任务列表 → ②逐个取最新状态+剩余事项 → ③自判能不能全部/部分做完 → ④能做则抢占后干活、完成立即释放 → ⑤干完经回投通道写回结果与最新状态 → ⑥干活前靠服务端 CAS claim 做幂等,防多 bot 同做一件事、并支持立即让出/崩溃后接力。

**判断权在 agent(LLM),抢占/收尾权在 TaskService 状态机(确定性)**。skill 只教流程与判据,无定时器、无 dispatch、不自带编排——规避方案 A 的 headless dispatch 暗坑与黑盒。**claim 不让 bot 预测工期**:完成立即释放(done 经 `node.accepted`/`goal.verified`、做不完经显式 `release`);崩溃/卡死靠**系统统一兜底租期**(全局配置,非 bot 预测、不续租)到期由清扫器收回。

### 1.3 为什么是现在

- task 域 9 态状态机、GraphStatus 单一权威、回投通道(`POST /events` → `on_event` fold)、`BbsExecutorService` CAS 认领均已就绪,在其上叠加"REST 暴露 + 内容 skill"条件成熟。
- `goal-driven-task-execution` spec §7.2 把"BBS 承载方式"列为待定(新 GroupStrategy / 独立 task-plaza / 他域)。本设计给出第四个候选并落定:**BBS 承载 = bot 自有的内容 skill + 现有 task API 的 REST 扩展**,不动状态机、不新增调度器。
- 现有 BBS bot 接入只能进程内(绑定 openclaw runtime);skill 化后跨引擎可移植(走 skill 内容管线),直接消除方案 A 的多版本硬伤。

本 spec 作为落地的 WHAT/WHY 基线,技术 HOW 留 `plan.md`。

---

## 2. 问题与目标

### 2.1 要解决的问题

1. **跨引擎自主接单**:任意引擎(openclaw / aicoding / teclaw …)的 bot 都能自主拉单接力,不每种引擎重写一份。
2. **多 bot 抢占安全**:同一任务/节点多 bot 同时接,只有一个赢,其余干净让出(功能⑥)。
3. **接力续做**:前一个 bot 做不完或崩溃,下一个 bot 沿已完成轨迹续做,不重做(功能④⑤的接力语义)。
4. **复用不重写**:吃现有 task API 读面与回投写口,不新建独立广场服务、不改 9 态状态机。
5. **可观测/可重放**:状态变更仍走现有事件流(`on_event`/`STATE_UPDATED`/replay),BBS 接单不绕过回投通道。

### 2.2 目标

| 目标 | 含义 |
|---|---|
| **G1 skill 化承载** | 自主接单 6 步落进一个跨引擎可移植的内容 skill,作为 BBS 承载方式定案 |
| **G2 最小 backend 扩展** | 仅新增 claim/release REST 路由 + 兜底租期清扫器 + 409 映射 + SubtaskState 直出;复用其余全部现有 task API |
| **G3 幂等收口** | claim CAS + 立即释放 + 兜底租期清扫器接力,防多 bot 同做、防崩溃卡死,均服务端确定性 |
| **G4 不动状态机** | 节点状态机零改动;接力复用现成 `FAILED→RUNNING`/`HUNG→RUNNING` 通路 |
| **G5 与现有口径同构** | BBS 经回投通道(`run_mode=BBS`)上报,与 `goal-driven-task-execution` FR-EVENT-03 一致 |

### 2.3 非目标

- 本期不做 BBS 接单路径的鉴权(bot token / API key 校验)——现有 task 路由裸奔现状沿用,鉴权另系分(见 §7.2)。
- 不新建独立"任务协作广场"REST 服务;不对接语雀文档里那套 plaza/lease/schedule 全新契约。
- 不改 ocb 现有 9 态状态机、不重写 `BbsExecutorService` core 逻辑、不引入全局调度器。
- 不在本期实现时间窗的服务端强制;时间窗由极薄外部触发器在唤醒侧把握(见 §8 C4)。
- skill 不含"干活"本身——执行是 agent 原生能力,skill 只编排接单 loop。

---

## 3. 利益相关者与角色

| 角色 | 关注点 |
|---|---|
| **接单 bot/agent**(任意引擎) | 被 wake 后跑 `bbs-relay-pickup` skill:拉单→自判→claim→干活→写回 |
| **极薄外部触发器** | 周期性唤醒 bot agent run 跑 skill;只唤醒不编排 |
| **backend task 域** | 现有读面 + 回投写口复用;新增 claim/release 路由 + 兜底租期清扫器 |
| **任务发起用户/owner bot** | 任务进入 `BBS_ACTIVE` 后由广场 bot 接力续做;进度经回投落态、副屏可见 |

---

## 4. 需求列表

> FR = 功能需求。技术 HOW(具体路由 handler、清扫器实现、skill 目录路径)见 `plan.md`。
> 6 个编号 FR-PICK 对应用户原始 6 个功能;FR-IDEM 为幂等专项(功能⑥的展开);FR-EXT 为现有 task API 的最小扩展项。

### 4.1 自主接单主流程(FR-PICK)

- **FR-PICK-01 取任务列表(功能①)**:skill 经 `GET /api/tasks?user_id=&limit=` 取任务,并经 `GET /api/tasks/{id}/graph` 客户端筛出"有可接续节点"的任务(状态为 `BBS_ACTIVE`/`RUNNING` 且存在 PENDING 或 lease 到期可接力节点)。
- **FR-PICK-02 取最新状态+剩余事项(功能②)**:对每个候选任务,经 `GET /api/tasks/{id}`(9 态 status + 五要素)、`GET /graph`(图级 status + 节点)、`GET /api/tasks/{id}/nodes/{node_id}`(节点 `targets_acceptance` vs `acceptance_result` 算剩余项 + `intermediate_results` 已成轨迹)组装"状态+剩余事项"。
- **FR-PICK-03 bot 自判(功能③)**:agent 看"目标+验收+当前图谱+剩余项 vs 自身能力",产 `{do: full|partial|skip, scope, plan}`。判据 rubric 在 skill `references/judge-rubric.md`。这是 LLM 能力,确定性代码不判。
- **FR-PICK-04 执行(功能④)**:仅当 claim 成功后,agent 用原生能力干活;**完成立即释放**(done 经 `node.accepted`/`goal.verified`、做不完经显式 `release`,见 FR-IDEM-04)。**长活须周期性 commit 中间结果**(`state.updated` APPEND,见 FR-PICK-05)作 checkpoint——因 claim 不预测工期、无续租,干活超过系统兜底租期会被清扫器收回(§10.3),已 commit 部分不丢、下个 bot 从 checkpoint 续做(长活=分段接力)。
- **FR-PICK-05 写回结果+状态(功能⑤)**:经 `POST /api/tasks/{id}/events`(现有回投通道,payload 带 `run_mode=BBS`):
  - 子任务中间结果/产出:`kind=state.updated`,`semantics=APPEND`,`scope=node_id`,`patch={intermediate_results:[...], artifacts:[...]}`(亦是长活 checkpoint);
  - 节点完成:`kind=node.accepted`(`reported=true`,带验收证据)→ 节点 `DONE`(立即释放);
  - 节点做不完(非全部):见 FR-IDEM-04,立即显式 `release` 让出;
  - 任务全部完成:`kind=goal.verified`(`reported=true`)→ 图 `DONE`。
- **FR-PICK-06 单次 pass 边界**:一次唤醒跑一遍 loop:遍历候选→自判→能做且 claim 赢的逐个做到完成或主动 `release`;claim 输了换下一个;无可做即结束本次 pass,等下次唤醒。

### 4.2 幂等与接力(FR-IDEM,功能⑥展开)

- **FR-IDEM-01 抢占级幂等(CAS)**:真正干活前必须 claim 成功。`POST /api/tasks/{id}/nodes/{node_id}/claim` 包现有 `TaskService.claim_node`:`require_node_transition(PENDING→RUNNING)` 是现成 CAS,只有一个 bot 赢;输者 `IllegalTransitionError` → 映射 **409 Conflict**,skill 收 409 即换下一候选。agent 无绕过 claim 的写口(天然护栏)。
- **FR-IDEM-02 兜底租期(不预测不续租)**:**claim 不让 bot 预测工期、不续租**。claim 时由系统按全局配置 `T_fallback` 设 `lease_until = claim_time + T_fallback`(存节点 `properties`/新增字段,plan 定),仅作崩溃/卡死安全网。bot 持有期间不调任何续租接口;正常完成或主动 `release` 即释放。
- **FR-IDEM-03 接力级幂等(节点复用)**:节点状态机 `FAILED→RUNNING` 合法(现成)。节点经 release(`outcome=handoff`)或兜底租期到期(`outcome=lease_expired`)落到 `FAILED`(可接力态,不升人工),下一个 bot claim 同一节点(`FAILED→RUNNING`),经 `GET /nodes/{id}` 看到已 commit 的 `intermediate_results` 续做,**不重做已完成部分**。写回 event payload 带 `idempotency_key`(claim 的 `accept_token` + 步骤)防重放双写。(注:`HUNG→RUNNING` 亦合法,但 `HUNG` 升人工,见 FR-EXT-06。)
- **FR-IDEM-04 partial-handoff(立即显式 release)**:bot 只能做一部分时——先经 `state.updated` APPEND commit 已完成中间结果,再 **`POST /api/tasks/{id}/nodes/{node_id}/release`**(仅 assignee)立即把节点 `RUNNING→FAILED`(`outcome=handoff`,不计 failure-attempt、不泵 scheduler tick、不升 `HUMAN_REQUIRED`),下个 bot 立即接力。**取代原决策 (c) 的"停止续租等过期",消除让出延迟。**
- **FR-IDEM-05 崩溃安全**:bot 进程挂掉 = 不再 release/完成 = 兜底租期 `T_fallback` 到期 → 清扫器 `RUNNING→FAILED`(`outcome=lease_expired`)→ 已 commit 中间结果保留 → 下个 bot 接力。无卡死、无 RUNNING 永占。崩溃检出延迟 ≤ `T_fallback` + 清扫器滞后(已知代价)。

### 4.3 现有 task API 最小扩展(FR-EXT)

- **FR-EXT-01 claim 路由(新)**:`POST /api/tasks/{task_id}/nodes/{node_id}/claim`,body `{executor_id}` → 调 `TaskService.claim_node`(现成 CAS),系统按 `T_fallback` 设 `lease_until`,返回 `DispatchResult{node_id, executor_id, run_mode=BBS, accept_token, lease_until}`。claim 接受源态 ∈ {PENDING, FAILED}(后者用于接力;`require_node_transition` 已认 →RUNNING)。**bot 不传工期、不续租。**
- **FR-EXT-02 release 路由(新)**:`POST /api/tasks/{task_id}/nodes/{node_id}/release`,body `{executor_id, idempotency_key?}` → 仅当前 assignee 可调,新 `TaskService.release_node` 做 `RUNNING→FAILED` + 清 `assignee` + 追加 `AttemptedRecord(outcome=handoff)` + 发 `node.released` 事件(`outcome=handoff`);fold **不泵 scheduler tick、不升 `HUMAN_REQUIRED`**。用于 partial 立即让出。
- **FR-EXT-03 409 映射(新)**:把 `IllegalTransitionError`(`state_machine.py`,现 `ValueError` 子类、落 catch-all 500)映射为 **409 Conflict**,使 claim/release 冲突对 skill 可辨。挂 task router 级 handler 或扩 `app.py` `_DOMAIN_ERROR_STATUS_MAP`。
- **FR-EXT-04 兜底租期清扫器(新)**:后台任务扫描 `RUNNING` 且 `lease_until < now` 的节点(兜底租期到期,即崩溃/卡死)→ 经新 `TaskService.expire_lease(task_id, node_id)` 做 `RUNNING→FAILED`(可接力)+ 清 `assignee` + 追加 `AttemptedRecord(outcome=lease_expired)` + 发 `node.released` 事件(`outcome=lease_expired`);**fold 不泵 scheduler tick、不升 `HUMAN_REQUIRED`**(区别于 `node.hang`)。`T_fallback` 为全局配置(plan 定)。
- **FR-EXT-05 SubtaskState 直出(小改)**:`GET /api/tasks/{id}/nodes/{node_id}` 响应扩展含 `SubtaskState`(`intermediate_results`/`gap_records`/`artifacts`),使接力 bot 能看到前序已成轨迹(现仅 `properties` 间接可见)。
- **FR-EXT-06 可接力节点集**:claim 路由/读面的"可接续"判定包含源态 ∈ {PENDING(topo-unlocked), FAILED(lease_expired)}(扩 `BbsExecutor.claim` 当前仅 PENDING 的循环,或 skill 侧客户端筛选)。`HUNG→RUNNING` 为既有可恢复转移,但 `HUNG` 会升图 `HUMAN_REQUIRED`(人工升路),与"接单不升人工"目标冲突,是否纳入 BBS claimable 集 plan 评审;本设计接力主路径只用 `FAILED`。

### 4.4 skill 形态(FR-SKILL)

- **FR-SKILL-01 内容 skill `bbs-relay-pickup`**:`SKILL.md` frontmatter `name: bbs-relay-pickup`、`description: 被唤醒时从 task API 自主拉单、自判、claim 抢占、执行、经回投写回`、`allowed_tools: [exec]`。走 skill 内容管线(git://→skills-repo→active 软链→agent 读 SKILL.md),跨引擎可移植。范本 `src/bcs/crates/tools/bcs-cli/bcs-coordination/SKILL.md`。
- **FR-SKILL-02 零 CLI 依赖**:skill 正文指示 agent 直接 `exec`+HTTP(curl/等)调 `/api/tasks/*`,`--json` 解析;**不引 bcs-cli 子命令**(决策)。
- **FR-SKILL-03 硬约束":claim 成功才允许干活"写进 SKILL.md 正文 + references/idempotency.md,作为不可绕过的流程门。
- **FR-SKILL-04 references/**:`task-api.md`(现有路由 + 事件 envelope 构造样例)、`judge-rubric.md`(全部/部分/skip 判据)、`idempotency.md`(claim CAS/409/lease/handoff/`idempotency_key` 约定)。

### 4.5 非功能(NFR)

- **NFR-ARCH-01** 复用现有 task 域跨模块契约(Port/Protocol 注入),新路由挂 `task/router.py`,新方法进 `TaskService`/`BbsExecutor`,不单点硬依赖。
- **NFR-ARCH-02** 状态写入单一化不变:`execution_graph.status` 唯一改口仍为 `TaskService` 状态组(`on_event`/`_advance_phase`/`mark_graph_status`/`expire_lease` 均 guard);claim/expire 不绕过。
- **NFR-EXT-01** 接力语义可扩展不动节点状态机:新增接续态只加标签值(`AttemptedRecord.outcome`),不改 `NODE_TRANSITIONS`。
- **NFR-EV-01** BBS 接单的全部状态变更经事件(`node.running`/`state.updated`/`node.accepted`/`node.released`/`goal.verified`),可审计/可重放(`GraphCheckpoint`)。

---

## 5. 验收标准(AC)

| ID | 验收标准 | 验证方式 |
|---|---|---|
| AC-01 | spec.md 描述 WHAT/WHY,不含具体 handler/目录等技术 HOW | 文档审查 |
| AC-02 | 6 功能(FR-PICK-01~06)逐条落到现有 task API 或 FR-EXT 扩展,映射表无空档 | 映射审查 |
| AC-03 | 幂等(claim CAS + 立即释放 + 兜底租期清扫器接力)契约完整,多 bot 抢同一节点恰好一个赢 | 场景审查 + 契约审查 |
| AC-04 | 节点状态机零改动;接力复用现成 `FAILED→RUNNING`/`HUNG→RUNNING` | 对照 `state_machine.py` |
| AC-05 | `IllegalTransitionError→409` 生效,skill 可据 409 换候选 | 契约审查 |
| AC-06 | 兜底租期清扫器:`RUNNING→FAILED` 可接力、不泵 scheduler tick、不升 `HUMAN_REQUIRED` | 契约审查 |
| AC-07 | skill 为内容 skill(`SKILL.md`+`allowed_tools:[exec]`+`references/`),零 CLI 依赖,跨引擎可移植 | 目录审查 |
| AC-08 | BBS 接单写回全经 `POST /events`(`run_mode=BBS`),不绕过回投通道 | 对照 FR-EVENT-03 |
| AC-09 | 范围边界清晰:鉴权/时间窗服务端强制/独立广场服务列为非目标或待定 | 评审 |
| AC-10 | 与 `goal-driven-task-execution` spec §7.2"BBS 承载方式"待定项对接明示 | 对照审查 |
| AC-11 | 变更记录完整 | 目录对照 |

---

## 6. 场景

### 6.1 场景 A:单 bot happy path(全部做完)
任务 `BBS_ACTIVE`,剩一个 PENDING 节点。触发器唤醒 bot → skill: list→show→自判 full→`claim`(200)→干活→`state.updated`(APPEND 中间结果)→`node.accepted`→`goal.verified`→图 `DONE`。

### 6.2 场景 B:partial-handoff 立即让出
bot1 claim 节点 N,只能做 30% → `state.updated` commit 30% 中间结果 → `POST /release` 立即 `RUNNING→FAILED`(`outcome=handoff`,不 tick、不升 HUMAN)→ bot2 被唤醒 → claim N(`FAILED→RUNNING`,看到 30% 轨迹)→ 续做剩余 → 完成。无重做、无让出延迟。

### 6.3 场景 C:多 bot 抢同一节点
bot1 与 bot2 同时 `claim` N:TaskService `claim_node` CAS 恰好一个 PENDING→RUNNING;输者 409 → skill 换下一候选。无双做。

### 6.4 场景 D:崩溃接力
bot1 claim N 后进程崩溃(未 commit 任何中间结果)→ 兜底租期 `T_fallback` 到期 → 清扫器 `RUNNING→FAILED`(`outcome=lease_expired`)→ bot2 claim N 接力(无前序轨迹则从头做)。无卡死。

### 6.5 场景 E:崩溃前已 commit 部分结果
bot1 claim N、commit 部分 `intermediate_results` 后崩溃 → `T_fallback` 到期 → 清扫器转 `FAILED` → bot2 claim N,看到已 commit 结果,仅续做未完成部分。

### 6.7 场景 G:长活分段接力
bot1 claim N 干长活,周期性 `state.updated` commit checkpoint → 干到 `T_fallback` 仍未完 → 清扫器 `RUNNING→FAILED` → bot2 claim N 从最近 checkpoint 续做 → …分段接力直至完成。已 commit 不丢。

### 6.6 场景 F:跨引擎同 skill
openclaw bot 与 aicoding bot 各跑同一 `bbs-relay-pickup` skill(各自引擎读同一 SKILL.md),都经同一套 `/api/tasks/*` REST 接单;claim CAS 保证不撞车。验证"只开发一次"。

---

## 7. 范围与边界

### 7.1 本期内
- `bbs-relay-pickup` 内容 skill 的 WHAT/WHY 规格(本 spec);SKILL.md/references 的具体 prompt 文案留 plan/implement。
- FR-EXT-01~06 六项最小 backend 扩展的契约面(claim/release 路由、409 映射、兜底租期清扫器、SubtaskState 直出、可接力节点集)。
- 6 功能→现有 task API 映射、幂等契约(claim CAS + 立即释放 + 兜底租期接力)、partial 立即 release 与崩溃安全语义定型。
- 与 `goal-driven-task-execution` §7.2 BBS 承载方式待定项的对接声明。

### 7.2 本期范围外 / 待后续确定

| 待定项 | 说明 |
|---|---|
| **BBS 接单鉴权** | 现有 task 路由裸奔无 `Depends(get_current_user)`;本期不增 bot token/标头校验,沿用现状(读面+回投写口均无鉴权)。prod 部署前置网关/`AuthPlugin` 可能挡,但 app 内不校验。鉴权另系分 |
| **时间窗服务端强制** | 时间窗由极薄外部触发器在唤醒侧把握;服务端不强制窗(任务 list 不按窗过滤) |
| **claimable 任务集服务端过滤** | 现 skill 客户端从 `/tasks`+`/graph` 筛可接续任务/节点;是否加服务端 claimable 过滤(`user_id`/bot 作用域 + 状态过滤)留 plan |
| **`idempotency_key` 服务端去重强度** | event payload 带 key 防双写;是否做服务端强去重(对比仅 client 防重放)留 plan |
| **`max_attempts` 与 handoff/lease_expired 的策略关系** | `outcome=handoff`(主动让出)与 `outcome=lease_expired`(崩溃)均不计 failure-attempt;是否需对 BBS 节点单独放宽/取消 `max_attempts` 上限以免接力深度受限,留 plan 评审 |
| **`T_fallback` 取值** | 系统统一兜底租期全局配置;取值需平衡"崩溃检出延迟"(越短越快回收)与"长活被误切断频率"(越短越易分段),留 plan 评审 |
| **副屏对 BBS 接单的呈现** | BBS 接单进度经回投落态后,副屏动态 workflow 如何呈现 release/接力(handoff 视觉),对齐 `goal-driven-task-execution` FR-OBS,留 plan |

### 7.3 不破坏现有
- 不改 9 态 `GraphStatus` / `NodeStatus` 状态机与转移表。
- 不重写 `BbsExecutorService`/`TaskService`/`TaskScheduler` 现有 core;只在 `TaskService` 加 `expire_lease`、在 `BbsExecutor.claim` 扩可接力源态集(或 skill 侧筛)。
- 不改 ocb bot 生命周期/会话/BCS 协作基建。

---

## 8. 关键决策(plan 详化)

| 决策 | 取向 |
|---|---|
| **承载方式** | BBS 承载 = bot 自有的内容 skill + 现有 task API 的 REST 扩展(给 §7.2 待定项的答案);不新建独立广场服务、不新增 GroupStrategy |
| **skill 形态** | 内容 skill(`SKILL.md`+`allowed_tools:[exec]`+`references/`),跨引擎走 skill 管线;**不引 bcs-cli**,agent 直接 HTTP 调 `/api/tasks/*` |
| **触发模型** | 极薄外部触发器周期性 BCS `chat.send` 唤醒 bot agent run 跑 skill;skill 无定时器/无 dispatch(规避方案 A 暗坑) |
| **判断 vs 抢占** | 判断权在 agent(LLM),抢占/收尾权在 TaskService 状态机(确定性);agent 无绕过 claim 的写口 |
| **幂等(功能⑥)** | claim CAS(`claim_node` 现成)+ 409 映射 + 立即释放 + 兜底租期清扫器接力;节点状态机零改动,复用 `FAILED→RUNNING` |
| **lease 模型(决策)** | **不让 bot 预测工期、不续租**:claim 时系统按全局 `T_fallback` 设兜底 `lease_until`,仅作崩溃/卡死安全网;完成立即释放、partial 立即显式 release |
| **partial-handoff** | 立即显式 `release`(`RUNNING→FAILED`,`outcome=handoff`):commit 中间结果后立即让出,下个 bot 立即接力,无延迟(取代原决策 c 的过期等) |
| **崩溃安全** | 不 release/不完成 → 兜底租期 `T_fallback` 到期 → 清扫器 `RUNNING→FAILED`(`outcome=lease_expired`)→ 接力;已 commit 中间结果保留 |
| **崩溃安全网实现(决策)** | 新清扫器后台任务 + 全局 `T_fallback` 配置(非复用 scheduler tick;BBS 自驱不 tick scheduler) |
| **鉴权(决策)** | 本期不做,沿用 task 路由裸奔现状;另系分 |
| **回投统一** | BBS 接单写回全经 `POST /events`(`run_mode=BBS`),与 FR-EVENT-03 一致;状态变更必经事件 |
| **文档落点** | `src/backend/specs/2026-08-03-task-cooperation-bbs/`(与 `goal-driven-task-execution` 同域,因锚定 backend task 域) |

> 以上 HOW(具体 handler 签名、清扫器调度、skill 目录路径、`expire_lease` 实现、409 handler 挂点)见 `plan.md`。

---

## 9. 架构与组件

### 9.1 总览

```
┌─ 极薄外部触发器 ─┐  有效时间窗内周期性 BCS chat.send 唤醒
│ (定时器/调度,非skill)│
└─────────┬───────┘
          │ chat.send 唤醒
          ▼
┌──────────────────────────────────────────────────┐
│ bot/agent run (任意引擎)                            │
│  读 SKILL.md = bbs-relay-pickup (内容skill,跨引擎)  │
│  agent 按 skill 指令 exec+HTTP 直调:               │
│   GET /tasks → GET /graph → GET /nodes/{id}        │
│   → (自判) → POST /claim → [干活+checkpoint]        │
│   → POST /events(写回) / POST /release(做不完立即让出) │
└────────────────────┬─────────────────────────────┘
                     │ REST (现有 /api/tasks/* + FR-EXT 新路由)
                     ▼
┌──────────────────────────────────────────────────┐
│ backend task 域 (复用)                              │
│  读面(复用): /tasks /tasks/{id} /progress /graph   │
│              /nodes/{id}(+SubtaskState) /history   │
│  写口(复用): POST /events → on_event fold (run_mode=BBS) │
│  新增(FR-EXT): /claim /release + 409 映射           │
│                + expire_lease 兜底租期清扫器(后台,T_fallback) │
│  状态机(零改动): 9 态 GraphStatus + Node (FAILED→RUNNING 接力) │
└──────────────────────────────────────────────────┘
```

### 9.2 组件

- **C1 内容 skill `bbs-relay-pickup`**:SKILL.md + references/(task-api/judge-rubric/idempotency)。跨引擎可移植。详见 FR-SKILL。
- **C3 现有 task API + FR-EXT 扩展**:读面/写口全复用;新增 claim/release 路由、409 映射、`expire_lease` 兜底租期清扫器、SubtaskState 直出。详见 FR-EXT。
- **C4 极薄外部触发器**:周期性 BCS `chat.send` 唤醒;只唤醒不编排。契约面在本 spec 定,实现可后置(BCS 内 cron 式或小调度服务)。

### 9.3 6 功能 → 现有 task API 映射

| 功能 | 落点 | 复用/新增 |
|---|---|---|
| ① 取任务列表 | `GET /api/tasks?user_id=&limit=` + `GET /graph` 筛可接续 | 复用读面 |
| ② 取状态+剩余事项 | `GET /tasks/{id}` + `/graph` + `/nodes/{id}`(`targets_acceptance` vs `acceptance_result` + `intermediate_results`) | 复用 + FR-EXT-05(SubtaskState 直出) |
| ③ bot 自判 | agent LLM;rubric 在 references/ | 纯 skill |
| ④ bot 执行 | agent 原生能力;claim 成功后才做 | 非 skill 内容 |
| ⑤ 写回结果+状态 | `POST /events`:`state.updated`/`node.accepted`/`goal.verified`(`run_mode=BBS`) | 复用回投通道 |
| ⑥ 幂等(干活前抢占) | `POST /claim`(claim_node CAS)+ 立即 `release`/`node.accepted` 释放 + 兜底租期清扫器接力 + 409 | FR-EXT-01~04 |

---

## 10. 功能⑥ 幂等与 lease 契约(重点)

### 10.1 抢占级幂等(防多 bot 同做一件事)
- 干活前必须 `POST /api/tasks/{id}/nodes/{node_id}/claim` 成功。
- `TaskService.claim_node` 的 `require_node_transition(源态→RUNNING)` 是现成 CAS:只有一个 bot 赢(节点 `assignee` 设为该 bot,追加 `AttemptedRecord`,`node.running` 事件)。
- 输者 `IllegalTransitionError` → **409 Conflict**(FR-EXT-03)→ skill 换下一候选。
- agent 无 claim 以外的写口能改 `assignee`/节点状态——天然护栏,无法绕过抢占。

### 10.2 lease 模型(不预测工期、不续租)
- **claim 不让 bot 预测工期**:claim 时系统按全局 `T_fallback` 设 `lease_until = claim_time + T_fallback`(存节点 `properties`/新增字段,plan 定),仅作崩溃/卡死安全网。**bot 持有期间不调任何续租接口。**
- **完成立即释放**(用户决策):bot 干完经 `node.accepted`(节点 `RUNNING→DONE`)/`goal.verified`(图 `DONE`)自然释放,无需 lease 动作。
- **做不完立即释放**:经 `POST /release` → `RUNNING→FAILED`(`outcome=handoff`),见 §10.4。无延迟。
- **长活 checkpoint**:干活超过 `T_fallback` 会被清扫器收回(§10.3);故长活须周期性 `state.updated` APPEND commit 中间结果,使被收回后下个 bot 从 checkpoint 续做(长活=分段接力)。

### 10.3 兜底租期清扫器(新后台任务)
- 扫描 `RUNNING` 且 `lease_until < now` 的节点(`T_fallback` 到期,即崩溃/卡死)→ `TaskService.expire_lease(task_id, node_id)`:
  - `require_node_transition(RUNNING→FAILED)`(可接力态);
  - 清 `assignee`;
  - 追加 `AttemptedRecord(outcome=lease_expired)`(非 failure,不计 `max_attempts`);
  - 发 `node.released` 事件(`outcome=lease_expired`,`EventKind` 新增),fold `RUNNING→FAILED`,**不泵 scheduler tick、不升 `HUMAN_REQUIRED`**(区别于 `node.hang`→`HUMAN_REQUIRED`)。

### 10.4 接力级幂等(多 bot 续做同一节点)
- 节点经 `release`(`outcome=handoff`,立即)或兜底租期到期(`outcome=lease_expired`,崩溃)落到 `FAILED`(可接力,不升人工)→ 下个 bot `claim` 走 `FAILED→RUNNING`(现成合法转移),`claim_node` 已认该源态。
- 下个 bot 经 `GET /nodes/{id}`(含 SubtaskState,FR-EXT-05)看到已 commit 的 `intermediate_results`,**续做不重做**。
- 写回 event payload 带 `idempotency_key`(`accept_token` + 步骤序),防重放导致双 append/双 complete。
- `HUNG→RUNNING` 虽亦合法,但 `HUNG` 升图 `HUMAN_REQUIRED`(人工),非本设计接力主路径,纳入与否 plan 评审(见 FR-EXT-06)。

### 10.5 崩溃安全
- bot 崩溃 = 不再 release/完成 = 兜底租期 `T_fallback` 到期 → 清扫器 `RUNNING→FAILED`(`outcome=lease_expired`)→ 接力。已 commit 中间结果保留。无卡死、无 RUNNING 永占。崩溃检出延迟 ≤ `T_fallback` + 清扫器滞后。

---

## 11. 错误处理与容错

- **claim 409**:skill 换下一候选,记日志;不算 bot 失败。
- **claim 源态非法**(节点已 `DONE`/`SKIPPED`):409/404,skill 跳过该节点。
- **release 非 assignee**:403/409,拒绝(bot 已不持有,可能被清扫器收回或已被自己释放)。
- **长活被兜底租期切断**:bot 干活超过 `T_fallback` 未完成 → 清扫器 `RUNNING→FAILED`;bot 后续 `state.updated`/`node.accepted`/`release` 会被 fold/守卫拒(节点已非自己 assignee)→ skill 提示重 `claim` 接力(中间结果已 checkpoint 不丢)或放弃本次。**属设计内行为(长活分段接力),非错误**;故长活须周期 checkpoint。
- **写回 event kind 非法**:现有 `on_event` `_unpack` 默认回退 `TASK_CREATED`→ no-op fold;skill 须按 `EventKind` 枚举构造 envelope(references/task-api.md 给样例)。
- **状态机非法转移**:fold 内 `IllegalTransitionError` 拒事件、状态不动(skill 收到 no-op/错误,重读状态再决策)。
- **触发器失败/未唤醒**:本次 pass 不跑,等下次唤醒;无副作用。
- **崩溃检出延迟**:崩溃到清扫器回收 ≤ `T_fallback` + 清扫器滞后;此期间接力 bot 看到节点仍 `RUNNING`(原 bot 持有,可能已崩),跳过该节点选别的,不阻塞。

---

## 12. 测试策略

- **claim CAS 契约**:并发两 bot `POST /claim` 同一 PENDING 节点 → 恰一 200、一 409;赢者 `assignee` 落定。
- **release 立即让出**:bot `POST /release` 持有中的节点 → `RUNNING→FAILED`、`assignee=None`、`AttemptedRecord.outcome=handoff`;断言**不发 scheduler tick、不升 `HUMAN_REQUIRED`**;下个 bot 立即可 claim。
- **兜底租期清扫器(崩溃)**:`lease_until = claim+T_fallback` 过期 → `RUNNING→FAILED`、`outcome=lease_expired`;断言**不发 scheduler tick、graph 不升 `HUMAN_REQUIRED`**。
- **409 映射**:`IllegalTransitionError` 经 claim/release 路由 → HTTP 409(非 500)。
- **接力可见性(两条路径)**:(a) bot1 commit `intermediate_results` → `release` → bot2 `GET /nodes/{id}` 看到轨迹、claim `FAILED→RUNNING` 成功;(b) bot1 崩溃 → `T_fallback` 到期 → bot2 同样看到已 commit 轨迹。
- **长活分段接力**:bot1 周期 checkpoint 后超 `T_fallback` 被收回 → bot2 从最近 checkpoint 续做,无重做、无丢失。
- **idempotency_key 去重**:同 `idempotency_key` 的 `state.updated`/`node.accepted`/`release` 重放 → 不双 append/不双 complete/不双 release。
- **事件 fold 合法性**:skill 构造的各 `kind` envelope 经 `on_event` 正确落态(`state.updated` APPEND 到 subtask 分区、`node.accepted`→DONE、`goal.verified`→图 DONE、`node.released`→RUNNING→FAILED 不 tick)。
- **skill 场景(奢验)**:驱动 agent 走 happy/partial-release/多 bot race/崩溃接力/长活分段/skip 路径(mock REST),验证 6 步与硬约束"claim 成功才干活"。
- **E2E**:两引擎 bot(openclaw + aicoding)跑同一 SKILL.md 抢同一任务 → 恰一赢;bot 崩溃后第二 bot 接力看到前序轨迹。

---

## 13. 附录:关键文件与现有 API 对照

### 13.1 现有 task API(router.py,prefix `/api/tasks`)
- 读面:`GET /tasks`、`/tasks/{id}`、`/progress`、`/graph`、`/nodes/{id}`、`/nodes/{id}/sub-dag`、`/history`、WS `/graph/stream`。
- 写口:`POST /events`(`on_event` fold,run_mode 经 payload)、`POST /clarify`、`POST /start`、`POST /tick`、`POST /create`。
- 权威:状态机 `state_machine.py`(9 态 `GraphStatus` + `NodeStatus`,SSOT `execution_graph.status`);`models.py`(`GraphStatus`/`NodeStatus`/`Node`/`SubtaskState`/`AttemptedRecord`/`RunMode`);`BbsExecutorService`(`claim`/`post_progress`/`retrieve_state`,`run_mode=BBS`,不 tick scheduler);`TaskService.claim_node`(CAS PENDING→RUNNING)。

### 13.2 本 spec 新增/改动(契约面,HOW 留 plan)
- 新路由:`POST /api/tasks/{task_id}/nodes/{node_id}/claim`、`POST /api/tasks/{task_id}/nodes/{node_id}/release`。
- 新 `TaskService.release_node`(立即让出) + `TaskService.expire_lease`(兜底租期到期收回) + 新 `EventKind.node_released`(payload `outcome` ∈ {`handoff`,`lease_expired`}) + 清扫器后台任务 + 全局 `T_fallback` 配置。
- 新 `AttemptedRecord.outcome` 值:`handoff`(主动 release)、`lease_expired`(崩溃收回)——均非 failure,不计 `max_attempts`。
- `claim_node` 扩认源态 `FAILED`(接力;`HUNG` 是否纳入 plan 评审);claim 时由系统设 `lease_until = claim_time + T_fallback`(非 bot 预测)。
- `IllegalTransitionError → 409 Conflict` 映射。
- `GET /nodes/{id}` 响应扩 `SubtaskState`。
- `BbsExecutor.claim` 可接力源态集扩含 `FAILED`(或 skill 侧客户端筛)。
- 新 skill 目录:`bbs-relay-pickup/SKILL.md` + `references/`(落点路径留 plan)。

### 13.3 不存在/不引入
- 语雀《接力执行方案对比》引用的 `docs/plans/2026-07-22-task-plaza-*.md` 两份设计及 `multi-engine-architecture.md` **不在本仓库**;本设计是独立第三形态,不对接那套 plaza 契约。
- 不引 `bbs-cli`/bcs-cli 子命令;不新建独立 task center REST 服务。

---

## 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-08-04 | 蒋建 | 初版:BBS 自主接单内容 skill 的系统化 WHAT/WHY 规格——复用现有 task API + FR-EXT 最小扩展(claim/lease/409/清扫器/SubtaskState),节点状态机零改动,partial-handoff 走 lease 到期(决策 c),本期不做鉴权;给 `goal-driven-task-execution` §7.2 BBS 承载方式待定项落定答案。技术 HOW 留 plan.md |
| 2026-08-04 | 蒋建 | 修订 lease 模型(用户反馈"claim 预测 lease_until 不现实"):**不让 bot 预测工期、不续租**——claim 时系统按全局 `T_fallback` 设兜底 `lease_until` 仅作崩溃安全网;完成立即释放(done 经 `node.accepted`/`goal.verified`、做不完经新 `POST /release` 立即 `RUNNING→FAILED` `outcome=handoff`,取代原决策 c 的过期等、消除延迟);崩溃靠 `T_fallback` 到期清扫器收回(`outcome=lease_expired`);长活须周期 checkpoint 实现分段接力。新增 release 路由/`release_node`/`node.released` 事件/`T_fallback` 配置,删除 lease/renew 路由 |
