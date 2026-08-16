# BBS 自主接力 skill — 系统设计规格(spec.md)

> 负责人:蒋建。背景对比见语雀《接力执行方案对比》(https://yuque.antfin.com/mad/enxdbg/ncmbmw3z37kh8sm6)。
> 落点域:ocb backend(task 域)+ bot/agent 自有的内容 skill;具体代码落点(新路由挂载、守卫实现、skill 目录)属 HOW,见 `plan.md`。
> 上游基线:`src/backend/specs/2026-08-09-task-goal-driven-execution-framework/spec.md`(权威,目标驱动 6 态执行框架)、`2026-08-09-task-goal-driven-task-runner/`(runner 适配)、`2026-08-09-task-goal-driven-task-runner-callback/`(回投回调面)。本 spec 为其 delta,**声明不破坏上游契约**。
> 日期:2026-08-15(2026-08-04 初版按旧 9 态/`BbsExecutor`/`on_event` 基线;本版按最新 6 态 `ExecutionEngine` 全量重落地)。

---

## 1. 概述

### 1.1 背景

语雀《接力执行方案对比》给"任务协作广场"提了两套设计:方案 A(插件模式,引擎内自驱)与方案 B(外部调度器模式,BCS 会话驱动)。本仓库现有的是 `task-goal-driven-execution-framework` 任务系统——6 态 `Status`(`PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG`)、`Goal+Acceptance` 唯一收敛神谕、`plan→dispatch→execute→verify→re-plan` 闭环、`ExecutionEngine` 事件驱动编排核、`TaskGraphService` SSOT 单一写口、`TaskLoopCallback`→`on_report` 回投面;`run_mode ∈ {single_bot, coop_group, bbs}`(字符串)。其中 **BBS 模态在框架侧已具雏形**:`on_miss@MAX_DEPTH → _hung_and_escalate` 置节点 `HUNG` + 图 `extend_props.bbs_mode=True` + `loop_round++`;`run_mode="bbs"` 是 dispatcher 退化路径、runner BBS 交付为 no-op log。但**没有任何机制让 bot 自主把 BBS 升级任务接起来做**——BBS 节点不会被框架自动消费(no-op),生产代码也不标 `run_mode="bbs"`,真实 BBS 交付留 corp `DeliveryPort` seam(`TaskRunner.set_delivery`)。

本设计是**第三形态**:把"自主接力执行"做成 **bot/agent 自有的一个内容 skill + 现有 task API 的最小加法扩展**,经 REST 复用现有 task 读面/`on_report` 回投写口,bot 自驱——发现已升 BBS 的任务 → CAS 独占其根节点 → 自判剩余 → 自挂"能做的那部分"的新节点 → 执行 → 回投 → 释放,下个 bot 接力。它既不是方案 A(不依赖 `openclaw/plugin-sdk`、不重蹈"每引擎一份"硬伤),也不是方案 B(不引入单点全局调度器)——编排下沉进可移植的 skill 内容,抢占/收口语义收口在现有 `ExecutionEngine` + `TaskGraphService` 的状态机。

### 1.2 这是什么

一个**内容 skill `bbs-relay-pickup` + 现有 task API 的最小加法 REST 扩展**,让任意引擎的 bot 被唤醒后自主完成 6 步:①经 `GET /api/task/list`(筛 `bbs_mode=True`)+ `GET /api/task/dashboard` 发现 BBS 升级任务并取整图 → ②`POST /api/task/bbs/claim` CAS 独占该任务根节点(恰一赢、输者 409 让出)→ ③读根 `Goal`+`Acceptance` 与 DONE 叶子、自判剩余里能做哪部分 → ④把"能做的那部分"新建一个 `run_mode="bbs"` 节点挂到图上、执行 → ⑤经 `POST /api/task/bbs/result` → `on_bbs_report`(collector-free,见 FR-EXT-03)写回结果与最新状态 → ⑥本次 pass 完成隐式释放 claim,下个 bot 按新的 DONE 叶子继续接力。

**判断权在 agent(LLM),抢占/收尾权在状态机(确定性)。** skill 只教流程与判据,无定时器、无 dispatch、不自带编排。claim 落在**任务状态图的根节点**(任务级 CAS),lease 复用 `TaskHarness` SLA(非 bot 预测、不续租、崩溃到期自动释放)。

### 1.3 为什么是现在

- 6 态 `Status` / `Goal+Acceptance` 收敛 / `ExecutionEngine` / `TaskGraphService` SSOT / `on_report` 回投面 / `_hung_and_escalate` 升 BBS 标志均已就绪,在其上叠加"BBS 自主接力 skill + 最小加法"条件成熟。
- `execution-framework` spec 把"BBS 承载方式"留作待定(新 GroupStrategy / 独立 task-plaza / 他域 / corp seam)。本设计落定:**BBS 承载 = bot 自有的内容 skill + 现有 task API 的加法扩展**,作为 corp `DeliveryPort` seam 的 Avernet 社区默认实现——不动状态机、不新增调度器。
- skill 化后跨引擎可移植(走 skill 内容管线),直接消除方案 A 的多版本硬伤。

本 spec 作为落地的 WHAT/WHY 基线,技术 HOW 留 `plan.md`。

---

## 2. 问题与目标

### 2.1 要解决的问题

1. **跨引擎自主接力**:任意引擎(openclaw / aicoding / teclaw …)的 bot 都能自主接 BBS 升级任务续做,不每种引擎重写一份。
2. **多 bot 抢占安全**:同一 BBS 任务多 bot 同时接,只有一个赢(占其根)、其余干净让出。
3. **接力续做**:前一个 bot 做不完或崩溃,下一个 bot 沿已完成(DONE 叶子)续做,不重做。
4. **复用不重写**:吃现有 task 读面与 `on_report` 回投写口 + 最小加法,不新建独立广场服务、不改 6 态状态机、不改 `on_*` 契约。
5. **可观测/可重放**:状态变更仍走现有编排核(`on_report`→`update_task_node_info`)/dashboard,BBS 接力不绕过 SSOT 写口。

### 2.2 目标

| 目标 | 含义 |
|---|---|
| **G1 skill 化承载** | 自主接力 6 步落进一个跨引擎可移植的内容 skill,作为 BBS 承载方式定案 |
| **G2 最小加法扩展** | 仅新增 `bbs/{claim,attach,result}` 路由 + drain/harness bbs 守卫 + `TaskSummary.bbs_mode` 直出 + 409 扩展;复用其余全部现有 task API 与编排核 |
| **G3 幂等收口** | 任务级 claim CAS(根)+ 一次性 scoped 节点 + harness SLA 兜底崩溃,防多 bot 同做、防崩溃卡死,均服务端确定性 |
| **G4 不动状态机** | `Status`/转移表/`TaskNodePatch`/`PlanResult`/`on_*` 签名零改动;不引入 `FAILED→RUNNING`/`HUNG→RUNNING`;接力=读 DONE 叶子 + 挂新 scoped 节点 |
| **G5 与现有口径同构** | BBS 经 `on_report` 统一回投,与其他 `run_mode` 同一写口,不绕过 `ExecutionEngine` |

### 2.3 非目标

- 本期不做 BBS 接单路径的鉴权:`POST /api/task/*` 现无鉴权,`/task_loop/callback/*` 已 HMAC 鉴权;BBS 新路由本期沿用 `/api/task/*` 裸奔现状,鉴权另系分(见 §7.2)。
- 不新建独立"任务协作广场"REST 服务;不对接语雀文档里那套 plaza/lease/schedule 全新契约。
- 不改 6 态 `Status` 状态机、不重写 `ExecutionEngine`/`TaskGraphService`/`TaskHarness` core、不引入全局调度器;BBS 节点不参与框架 dispatch/drain 自动消费。
- 不在本期实现时间窗的服务端强制;时间窗由极薄外部触发器在唤醒侧把握(见 §8)。
- skill 不含"干活"本身——执行是 agent 原生能力,skill 只编排接力 loop。

---

## 3. 利益相关者与角色

| 角色 | 关注点 |
|---|---|
| **接力 bot/agent**(任意引擎) | 被 wake 后跑 `bbs-relay-pickup`:发现→CAS 占根→自判→挂节点→执行→回投→释放 |
| **极薄外部触发器** | 周期性唤醒 bot agent run 跑 skill;只唤醒不编排 |
| **backend task 域** | 现有读面 + `on_report` 回投复用;新增 `bbs/{claim,attach,result}` 路由 + drain/harness bbs 守卫 |
| **任务发起用户/owner bot** | 任务 `on_miss` 升 BBS 后由广场 bot 接力续做;进度经回投落态、dashboard 可见 |

---

## 4. 需求列表

> FR = 功能需求。技术 HOW(具体 handler 签名、守卫实现、skill 目录路径)见 `plan.md`。
> FR-PICK 对应自主接力 6 步;FR-IDEM 为幂等/lease 专项;FR-EXT 为现有 task API 的最小加法项;FR-SKILL 为 skill 形态。

### 4.1 自主接力主流程(FR-PICK)

- **FR-PICK-01 发现 BBS 任务(步①)**:skill 经 `GET /api/task/list`(扩 `TaskSummary` 暴露 `bbs_mode`,或加 `?status=bbs` 过滤)取任务,筛 `extend_props.bbs_mode=True` 者;对候选经 `GET /api/task/dashboard?task_id=` 取整图(`TaskExecutionGraph`:根 `Goal`/`Acceptance` + 全节点/关系/状态)。
- **FR-PICK-02 CAS 占根(步②)**:经 `POST /api/task/bbs/claim {task_id, bot_id}` 条件写根 `bbs_owner`(根节点所属权字段;具体落点 `run_info.assignee` 或 `extend_props["bbs_owner"]` 由 plan 定);per-task `RLock` + 态机裁,**恰一赢**,输者 `TaskStateError`→**409→skip 换任务**。claim 仅校验该任务 `bbs_mode=True`;"图空闲 + 根 `PLANNING`"前提不在 claim 判,而由步④ `bbs/attach` 经 `add_task_nodes` 的 a/b/c/d 触发条件自然裁(不满足→`GraphIntegrityError`→409)。
- **FR-PICK-03 bot 自判(步③)**:agent 读根 `Goal`+`Acceptance` + 整图节点状态(已成 DONE 叶子、前序 scoped 节点 `run_info.output` checkpoint)→ 自算"剩余" → 判"我能做哪部分"(`full`/`partial`/`skip`)。判据 rubric 在 skill `references/judge-rubric.md`。LLM 能力,确定性代码不判。
- **FR-PICK-04 挂节点 + 执行(步④)**:把"能做的那部分"封装成 `TaskSpec` → `POST /api/task/bbs/attach`(`add_task_nodes` 薄封装,仅 claim 持有者可调)在**根下新建一个 `run_mode="bbs"` 子节点** + 翻 `PENDING→RUNNING`(`assignee=bot_id`,attach 合并 create+start)→ 原生能力执行这一个节点。**前提**:任务图空闲(无 `RUNNING`)、根 `PLANNING`(可委托);图级 `HUNG`(`root_stuck`/`loop_exhausted`)= 硬终态,跳过。
- **FR-PICK-05 写回(步⑤)**:经 `POST /api/task/bbs/result` → `engine.on_bbs_report(TaskNodePatch{acceptance_result?, output_patch?, exec_error?}, root_verified)`(**collector-free**,见 FR-EXT-03:不跑 `_on_pass_collect`/`_on_fail_collect`/`_drain`,避免框架经 owner-bot 重规划与接力冲突)。`verdict=PASS`→节点 `DONE`;`verdict=FAIL`+gaps→节点 `FAILED`;**根目标满足时 bot 带 `root_verified=true`** → 根 acceptance `PASS`→根 `DONE` + 图 `DONE`;报完**自动清根 `bbs_owner`** 释放。长活须周期性 `output_patch` 作 checkpoint(见 FR-IDEM-02)。
- **FR-PICK-06 pass 边界 + 接力(步⑥)**:一次唤醒/claim = 一个 scoped 节点;`bbs/result` 落 scoped 节点终态后,服务端**自动清根 `bbs_owner`** 释放 claim(无独立 release 路由);根未满足 → 下个 bot 再 claim → 再判"根 goal + 更多 DONE 叶子" → 再挂节点 → …接力。无可做/pass 完 → 结束本次唤醒。

### 4.2 幂等与接力(FR-IDEM)

- **FR-IDEM-01 抢占级幂等(任务级 CAS,防双做)**:`POST /bbs/claim` 对根 `bbs_owner` 条件写;per-task `RLock` + 态机裁恰一赢,输者 409→skip。`bbs/attach`/`bbs/result` 校验调用者 = claim 持有者——agent 无绕过 claim 的写口,天然护栏。
- **FR-IDEM-02 lease 复用 harness(不预测、不续租)**:claim 生命周期 = 该 scoped 工作节点的生命周期;lease 直接复用 `TaskHarness` SLA(无 `T_fallback`)。bot 持有期间不续租;正常报完 → 释放 claim;崩溃 = 节点长 `RUNNING` 不报 → SLA 到期 → harness **清 `bbs_owner` + 死节点标终态**(不重派)→ 下个 bot 接力。长活须周期性 `output_patch` checkpoint,使被 SLA 切断后下个 bot 从 checkpoint 续做(长活 = 分段接力)。
- **FR-IDEM-03 接力级幂等(不重做)**:下个 bot 读"根 goal + DONE 叶子(+ 已报 `output_patch` 已做部分)" → 重新判剩余 → 挂新 scoped 节点续做;**已 DONE 不重做**。partial = 报 `FAIL+gaps` 带 `output_patch` 作 checkpoint,下个 bot 据此续。
- **FR-IDEM-04 深度闸**:`MAX_DEPTH`(结构分解深度,既有)/`BBS_MAX_DEPTH`(BBS 接力深度,默认 3)替 `max_attempts`;**BBS 接力深度由 per-task `bbs_relay_count`(图 `extend_props`,每次 `bbs/attach` +1)计,区别于 `loop_round`(升级计数,由 `MAX_LOOP` 兜底)**;`bbs_relay_count >= BBS_MAX_DEPTH` → 图 `HUNG`(`bbs_relay_exhausted`)= 唯一人工入口。

### 4.3 现有 task API 最小加法(FR-EXT)

- **FR-EXT-01 bbs/claim 路由(新)**:`POST /api/task/bbs/claim {task_id, bot_id}` → 条件写根 `bbs_owner`;恰一赢,输者 409;赢者返回 claim 句柄(含 root `node_id`)。
- **FR-EXT-02 bbs/attach 路由(新)**:`POST /api/task/bbs/attach {task_id, parent_node_id, task_spec, bot_id}` → 仅 claim 持有者可调;封装 `TaskGraphService.add_task_nodes` 在根下新建 `run_mode="bbs"` 子节点 + 翻 `PENDING→RUNNING`(`assignee=bot_id`);返回 `node_id`。守卫保证框架 dispatch/drain 不自动消费该节点。
- **FR-EXT-03 bbs/result 路由(新)**:`POST /api/task/bbs/result {task_id, node_id, bot_id, acceptance_result?, output_patch?, exec_error?, root_verified=false}` → 仅 claim 持有者可调;→ 新增 `engine.on_bbs_report(TaskNodePatch, root_verified)`(**collector-free**:仅 `update_task_node_info` 翻 scoped 节点终态 + 可选根收口 + 清根 `bbs_owner`,不跑 `_on_pass_collect`/`_on_fail_collect`/`_drain`)。`root_verified=true` → 根 acceptance `PASS`→根 `DONE` + 图 `DONE`。不直调 `on_report`(其 `_on_pass_collect` 会经 owner-bot 重规划+派发,与 §10.4 接力冲突)。
- **FR-EXT-04 409 扩展**:把 `TaskStateError→409`(现仅回调路径有)扩展到 `bbs/claim`(claim 冲突)/`bbs/attach`(非 claim 持有者)。扩 `_DOMAIN_ERROR_STATUS_MAP` 或 task router 级 handler(注意:`TaskError` 当前不在该 map,见 §13.2)。
- **FR-EXT-05 TaskSummary.bbs_mode 直出(小改)**:`GET /api/task/list` 响应扩展含 `bbs_mode`(来自图 `extend_props`),使接力 bot 可客户端筛 BBS 任务(亦可加 `?status=bbs` 过滤,plan 评审)。
- **FR-EXT-06 drain/harness bbs 守卫(新)**:dispatch/drain **跳过 `run_mode="bbs"` 节点**(不自动 `PENDING→RUNNING`、不 no-op 空转);`TaskHarness` 对 bbs 节点 SLA 到期走"**清 `bbs_owner` + 死节点标终态**",**不走重派**。加法行为,不动类型/转移/`on_*` 签名。

### 4.4 skill 形态(FR-SKILL)

- **FR-SKILL-01 内容 skill `bbs-relay-pickup`**:`SKILL.md` frontmatter `name: bbs-relay-pickup`、`description: 被唤醒时从 task API 发现 BBS 升级任务、CAS 占根、自判剩余、挂节点、执行、经回投写回`、`allowed_tools: [exec]`。走 skill 内容管线,跨引擎可移植。范本参考 e2e `planning`/`search`/`acceptance` skill(`tests/community/core/task/singlebox_e2e/skills/`)。
- **FR-SKILL-02 零 CLI 依赖**:skill 正文指示 agent 直接 `exec`+HTTP 调 `/api/task/*` 与 `bbs/*` 新路由,`--json` 解析;**不引 bcs-cli 子命令**。
- **FR-SKILL-03 硬约束流程门**:claim 成功才允许 attach/干活;attach 必须挂 `run_mode="bbs"` 节点;写回必经 `bbs/result`→`on_bbs_report`——写进 SKILL.md 正文 + `references/idempotency.md`,作不可绕过的流程门。
- **FR-SKILL-04 references/**:`task-api.md`(现有 `/api/task/*` + `bbs/*` 新路由 + `bbs/result` envelope 构造样例含 `acceptance_result`/`output_patch`/`root_verified`)、`judge-rubric.md`(全部/部分/skip 判据)、`idempotency.md`(claim CAS/409/harness SLA lease/接力读 DONE 叶子 约定)。

### 4.5 非功能(NFR)

- **NFR-ARCH-01** 复用现有 task 域跨模块契约(Port/Protocol 注入);新路由挂现有 task router 同域,新守卫进 `ExecutionEngine`/`TaskHarness`,不单点硬依赖。
- **NFR-ARCH-02** 状态写入单一化不变:`TaskGraphService`(`update_task_node_info`/`add_task_nodes`/`update_task_graph_info`)唯一改口;claim/attach/result 均经其,不绕过。
- **NFR-EXT-01** 接力语义可扩展不动状态机:新增接续只用 `run_mode="bbs"` 标签 + `extend_props`,不改 `Status`/转移表。
- **NFR-EV-01** BBS 接力的全部状态变更经 `on_bbs_report`→`update_task_node_info`(节点终态)/`update_task_graph_info`(图终态),可审计/dashboard 可见。

---

## 5. 验收标准(AC)

| ID | 验收标准 | 验证方式 |
|---|---|---|
| AC-01 | spec 仅 WHAT/WHY,不含具体 handler/目录等技术 HOW | 文档审查 |
| AC-02 | 6 步循环(FR-PICK-01~06)逐条落到现有 task API 或 FR-EXT 加法,映射表无空档 | 映射审查 |
| AC-03 | **任务级 claim CAS**:多 bot `POST /bbs/claim` 同一 BBS 任务根,恰一 200、一 409;赢者根 `bbs_owner` 落定 | 场景 + 契约审查 |
| AC-04 | 状态机零改动;不引入 `FAILED→RUNNING`/`HUNG→RUNNING`;接力 = 读 DONE 叶子 + 挂新 scoped 节点 | 对照 `task_graph_service.py` 转移表 |
| AC-05 | `TaskStateError→409` 在 `bbs/claim` 冲突生效(非 500) | 契约审查 |
| AC-06 | lease 复用 `TaskHarness` SLA:崩溃→到期→清 `bbs_owner` + 死节点终态(不重派、不升人工,除非深度闸)→接力 | 契约审查 |
| AC-07 | `bbs-relay-pickup` 为内容 skill(greenfield,`SKILL.md`+`allowed_tools:[exec]`+`references/`),零 CLI 依赖,跨引擎可移植 | 目录审查 |
| AC-08 | 写回经 `on_bbs_report`(经同一 SSOT `update_task_node_info`,collector-free),不走已删 `POST /events` / `EventKind` | 对照 FR-EVENT |
| AC-09 | 范围边界:鉴权/时间窗/claimable 服务端过滤列为非目标或待定 | 评审 |
| AC-10 | 与 `execution-framework` BBS 承载方式待定项对接明示;声明不破坏上游契约 | 对照审查 |
| AC-11 | 守卫:`dispatch/drain` 跳过 `run_mode="bbs"`;`harness` bbs 到期不重派、清 `bbs_owner` | 契约审查 |
| AC-12 | 变更记录完整 | 目录对照 |

---

## 6. 场景

### 6.1 场景 A:单 bot happy path(全部做完)
任务 `on_miss` 升 BBS(`bbs_mode=True`,根仍 `PLANNING`,图空闲)。触发器唤醒 bot → skill:`/list` 筛 `bbs_mode` → `/dashboard` 取图 → 自判 `full` → `bbs/claim`(200,占根) → `bbs/attach` 挂 `run_mode="bbs"` 节点 + start → 执行 → `bbs/result`(`acceptance PASS`)→ 节点 `DONE` → 根 acceptance `PASS` → 图 `DONE`。

### 6.2 场景 B:partial-handoff 立即接力
bot1 `claim` 根,只能做 30% → `bbs/attach` 挂节点做 30% → `bbs/result`(`FAIL+gaps`,带 `output_patch` 30% checkpoint,节点 `FAILED`)→ `bbs/result` 自动清根 `bbs_owner` 释放 → bot2 被唤醒 → `claim` 根 → 读 `goal` + DONE 叶子 + 前序 scoped 节点 `run_info.output`(含 30% checkpoint)→ 判剩余 → `bbs/attach` 挂新节点续做剩余 → 完成。无重做、无让出延迟(释放即接续)。

### 6.3 场景 C:多 bot 抢同一任务
bot1 与 bot2 同时 `bbs/claim` 同一 BBS 任务根:`RLock`+态机恰一 200、一 409;输者 `bbs/claim` 409 → skip 换任务。无双做。

### 6.4 场景 D:崩溃接力
bot1 `claim` 根、`attach` 节点后进程崩溃(未 `bbs/result`)→ 节点长 `RUNNING` → `TaskHarness` SLA 到期 → **清 `bbs_owner` + 死节点标终态**(不重派)→ bot2 `claim` 根 → 读 `goal` + DONE(无前序 DONE 则从头)→ 接力。无卡死、无根被永占。

### 6.5 场景 E:崩溃前已 commit 部分结果
bot1 `claim`→`attach`→`bbs/result`(`output_patch` commit 部分)再崩溃 → SLA 到期 → harness 标节点终态 + 清 claim → bot2 `claim` → 读 `goal` + DONE 叶子 + 前序 scoped 节点 `run_info.output`(含已 commit)→ 仅续做未完成部分。

### 6.6 场景 F:跨引擎同 skill
openclaw bot 与 aicoding bot 各跑同一 `bbs-relay-pickup` skill(各自引擎读同一 `SKILL.md`),都经同一套 `/api/task/*` + `bbs/*` 接单;`bbs/claim` CAS 保证不撞车。验证"只开发一次"。

### 6.7 场景 G:图级 HUNG 硬终态(边界)
任务升 BBS 且根已冒泡 `HUNG`(`loop_exhausted`/`root_stuck`)→ bot `/dashboard` 发现但根不可委托(无 `PLANNING` 可挂点)→ skip 该任务(不碰,等人工)。验证硬终态边界,BBS 不强行救已 HUNG 图。

### 6.8 场景 H:长活分段接力
bot1 `claim` 长活 → `attach` 节点 → 周期性 `bbs/result`(`output_patch`)commit checkpoint → 干到 SLA 仍未完 → harness 标节点终态 + 清 claim → bot2 `claim` → 读 `goal` + 前序 scoped 节点 `run_info.output`(最近 checkpoint)→ `attach` 新节点从 checkpoint 续做 → …分段接力直至完成。已 commit 不丢。

---

## 7. 范围与边界

### 7.1 本期内
- `bbs-relay-pickup` 内容 skill 的 WHAT/WHY 规格(本 spec);`SKILL.md`/`references` 的具体 prompt 文案留 plan/implement。
- FR-EXT-01~06 六项最小加法的契约面(`bbs/{claim,attach,result}` 路由、`TaskSummary.bbs_mode` 直出、drain/harness bbs 守卫、409 扩展)。
- 6 步→现有 task API 映射、幂等契约(任务级 CAS + harness SLA lease + 接力读 DONE)、partial 接力与崩溃安全语义定型。
- 与 `execution-framework` BBS 承载方式待定项的对接声明。

### 7.2 本期范围外 / 待后续确定

| 待定项 | 说明 |
|---|---|
| **BBS 接单鉴权** | `POST /api/task/*` 现裸奔;本期 `bbs/*` 沿用现状(同 `/api/task/*`)。`/task_loop/callback/*` 已 HMAC 鉴权,但 BBS 走 `/api/task/bbs/*` 不走 callback。prod 部署前置网关/`AuthPlugin` 可能挡,app 内不校验。鉴权另系分 |
| **时间窗服务端强制** | 时间窗由极薄外部触发器在唤醒侧把握;服务端不强制窗(任务 list 不按窗过滤) |
| **claimable 任务集服务端过滤** | 现 skill 客户端从 `/list` 筛 `bbs_mode=True` + 图空闲;是否加服务端 claimable 过滤(状态 + 空闲 + bot 作用域)留 plan |
| **`TaskSummary.bbs_mode` 过滤形态** | 直出字段供客户端筛,还是加 `?status=bbs` 服务端过滤,plan 评审 |
| **单次 pass 节点数** | 本期一次 pass = 一个 scoped 节点;是否允许多节点/批量 attach 留 plan 评审 |
| **`BBS_MAX_DEPTH` 取值与传递** | 框架 spec 默认 3;e2e 经 `execution_config` 传入但当前生产无写入点(plan 定接入) |
| **副屏对 BBS 接力的呈现** | BBS 接力进度经回投落态后,副屏动态 workflow 如何呈现接力/handoff 视觉,对齐 `execution-framework` FR-OBS,留 plan |
| **与 corp `DeliveryPort` 的关系** | 本 skill 是 corp `DeliveryPort` seam 的 Avernet 社区默认实现;corp 是否覆盖/并存留部署侧 |

### 7.3 不破坏现有
- 不改 6 态 `Status` 状态机与转移表;不引入 `FAILED→RUNNING`/`HUNG→RUNNING`。
- 不重写 `ExecutionEngine`/`TaskGraphService`/`TaskHarness` existing core;仅在 engine 加 bbs drain 跳过、在 harness 加 bbs 到期分支,均为加法。
- 不改 `TaskNodePatch`/`TaskGraphPatch`/`PlanResult`/`on_*` 签名。
- 不改 ocb bot 生命周期/会话/BCS 协作基建;不动 `/task_loop/callback/*` 既有鉴权与回调契约。

---

## 8. 关键决策(plan 详化)

| 决策 | 取向 |
|---|---|
| **承载方式** | BBS 承载 = bot 自有的内容 skill + 现有 task API 的加法扩展(corp `DeliveryPort` seam 的 Avernet 社区默认实现);不新建独立广场服务、不新增 GroupStrategy |
| **skill 形态** | 内容 skill(`SKILL.md`+`allowed_tools:[exec]`+`references/`),跨引擎走 skill 管线;不引 bcs-cli,agent 直接 HTTP |
| **触发模型** | 极薄外部触发器周期性唤醒 bot agent run 跑 skill;skill 无定时器/无 dispatch(规避方案 A 暗坑) |
| **判断 vs 抢占** | 判断权在 agent(LLM),抢占/收尾权在状态机(确定性);agent 无绕过 claim 的写口 |
| **claim 粒度(决策)** | **任务级**(任务状态图根节点 `bbs_owner`),非节点级;bot 按整图/根目标干,抢的是任务,恰一赢输者 409 |
| **幂等(多 bot)** | 任务级 claim CAS + 409 输者 + 一次性 scoped 节点 + harness SLA lease;状态机零改动,不引入 `FAILED→RUNNING` |
| **lease 模型(决策)** | **复用 `TaskHarness` SLA**(不预测工期、不续租、无 `T_fallback`);崩溃到期自动清 `bbs_owner` + 死节点标终态(不重派) |
| **接力模型(决策)** | 读"根 goal + DONE 叶子(+ `output_patch`)"重新判剩余 → 挂新 scoped 节点续做;已 DONE 不重做;partial = `FAIL+gaps`+checkpoint |
| **深度闸** | `MAX_DEPTH`(结构分解)/`BBS_MAX_DEPTH`(BBS 接力,默认 3)替 `max_attempts`;BBS 接力深度 by per-task `bbs_relay_count`(每次 attach +1,≠ `loop_round`);超 → 图 `HUNG`(`bbs_relay_exhausted`)唯一人工入口 |
| **BBS 节点交付** | 框架 dispatch/drain 跳过 `run_mode="bbs"`;BBS 节点由 bot 经 `bbs/attach` 自驱、`bbs/result` 回投;不在 runner BBS no-op 路径 |
| **鉴权(决策)** | 本期不做,`bbs/*` 沿用 `/api/task/*` 裸奔现状;另系分 |
| **回投统一** | BBS 写回经 `bbs/result`→`on_bbs_report`(**collector-free**,经同一 SSOT `update_task_node_info`);不直调 `on_report`(避免 `_on_pass_collect` 重规划与接力冲突),不绕过 `ExecutionEngine` |
| **文档落点** | `src/backend/specs/2026-08-09-task-goal-driven-task-runner-bbs/`(与 `execution-framework` 同域) |

> 以上 HOW(具体 handler 签名、守卫挂点、skill 目录路径、`BBS_MAX_DEPTH` 接入)见 `plan.md`。

---

## 9. 架构与组件

### 9.1 总览

```
┌─ 极薄外部触发器 ─┐  有效时间窗内周期性唤醒(BCS chat.send 等,非 skill)
└─────────┬───────┘
          │ 唤醒
          ▼
┌──────────────────────────────────────────────────┐
│ bot/agent run (任意引擎)                            │
│  读 SKILL.md = bbs-relay-pickup (内容skill,跨引擎)  │
│  agent 按 skill 指令 exec+HTTP 直调:               │
│   GET /api/task/list(筛 bbs_mode)                   │
│   → GET /api/task/dashboard(取整图)                 │
│   → POST /api/task/bbs/claim(CAS 占根)              │
│   → (自判:根goal+DONE叶子 → 能做哪部分)             │
│   → POST /api/task/bbs/attach(挂 run_mode=bbs 节点) │
│   → [干活 + 周期 bbs/result output_patch checkpoint] │
│   → POST /api/task/bbs/result(acceptance 回投)      │
│   → (本次 pass 完;根未满足则下个 bot 接力)          │
└────────────────────┬─────────────────────────────┘
                     │ REST (现有 /api/task/* + bbs/* 加法)
                     ▼
┌──────────────────────────────────────────────────┐
│ backend task 域 (复用 + 加法)                        │
│  读面(复用): /api/task/list(+bbs_mode) /dashboard  │
│  写口(复用): on_report→update_task_node_info(SSOT)  │
│  加法(FR-EXT): /bbs/claim /bbs/attach /bbs/result  │
│               + drain 跳过 bbs + harness bbs 到期分支│
│  升级spine(复用,不改): on_miss→_hung_and_escalate   │
│    (节点HUNG + 图bbs_mode=True + loop_round++)      │
│  状态机(零改动): 6 态 Status(不引入 FAILED→RUNNING) │
│  lease(复用): TaskHarness SLA                       │
└──────────────────────────────────────────────────┘
```

### 9.2 组件

- **C1 内容 skill `bbs-relay-pickup`**:`SKILL.md` + `references/`(task-api/judge-rubric/idempotency)。跨引擎可移植。详见 FR-SKILL。
- **C3 现有 task API + FR-EXT 加法**:读面/`on_report` 写口全复用;新增 `bbs/{claim,attach,result}` 路由、`TaskSummary.bbs_mode` 直出、drain 跳过 bbs、harness bbs 到期分支、409 扩展。详见 FR-EXT。
- **C4 极薄外部触发器**:周期性唤醒;只唤醒不编排。契约面在本 spec 定,实现可后置(BCS 内 cron 式或小调度服务)。

### 9.3 6 功能 → 现有 task API 映射

| 功能 | 落点 | 复用/新增 |
|---|---|---|
| ① 发现 BBS 任务 | `GET /api/task/list`(+`bbs_mode`) + `GET /api/task/dashboard` 整图 | 复用读面 + FR-EXT-05 |
| ② CAS 占根 | `POST /api/task/bbs/claim`(根 `bbs_owner` CAS) | FR-EXT-01 |
| ③ bot 自判 | agent LLM;rubric 在 `references/` | 纯 skill |
| ④ 挂节点 + 执行 | `POST /api/task/bbs/attach`(`add_task_nodes` + start)+ 原生能力 | FR-EXT-02(+ FR-EXT-06 守卫) |
| ⑤ 写回结果+状态 | `POST /api/task/bbs/result` → `on_bbs_report`(`acceptance_result`/`output_patch`/`exec_error`/`root_verified`,collector-free) | FR-EXT-03 |
| ⑥ 幂等(抢占+接力+崩溃) | `bbs/claim` CAS + 409 + 一次性 scoped 节点 + harness SLA lease + 读 DONE 接力 | FR-EXT-01/02/03/06 |

---

## 10. 幂等·lease·接力契约(重点)

### 10.1 抢占级幂等(防多 bot 同做一个任务)
- 干活前必须 `POST /api/task/bbs/claim {task_id, bot_id}` 成功。
- claim 对根 `bbs_owner`(根节点所属权字段)做条件写:per-task `RLock` + `TaskGraphService` 态机裁,**恰一 bot 赢**(根 `bbs_owner = bot_id`);输者 `TaskStateError` → **409 Conflict**(FR-EXT-04)→ skill skip 换任务。
- `bbs/attach`/`bbs/result` 校验调用者 = 当前 `bbs_owner`,否则拒——agent 无 claim 以外的写口能改 `bbs_owner`/节点状态,天然护栏,无法绕过抢占。

### 10.2 lease 模型(复用 harness,不预测工期、不续租)
- **claim 生命周期 = bot 该 scoped 工作节点的生命周期**;lease 直接复用 `TaskHarness` SLA(默认 `RUNNING` 600s;`execution_config` 可配),无 `T_fallback`、bot 不续租。
- **完成立即释放**:bot 干完经 `bbs/result`(`acceptance PASS`,**`root_verified=true`**)`on_bbs_report` 落节点 `DONE` + 根 acceptance `PASS`→根 `DONE` + 图 `DONE`,并**自动清根 `bbs_owner`** 释放 claim;无需 lease 动作。
- **partial 立即让出**:bot 经 `bbs/result`(`FAIL+gaps` + `output_patch` checkpoint)落节点 `FAILED`,`bbs/result` 处理后**自动清根 `bbs_owner`** 释放 claim,下个 bot 立即接力(见 §10.4)。无延迟。
- **长活 checkpoint(分段接力)**:干活超过 SLA 会被 harness 切断(§10.3);长活按段接力——每段一次 `claim`→干活→`bbs/result`(`output_patch` checkpoint)。**每次 `bbs/result`(含 checkpoint)都会清根 `bbs_owner` 释放 claim**(见上文完成/partial),故不在单次 claim 内中途 checkpoint;被切断后下个 bot 从最近 checkpoint(节点 `run_info.output`)续做。

### 10.3 崩溃安全(harness 兜底,非 lease 清扫器)
- bot 崩溃 = 不再 `bbs/result` = 节点长 `RUNNING` → `TaskHarness` SLA 到期 → **清 `bbs_owner` + 死节点标终态**(FR-EXT-06,**不走重派**)→ 下个 bot `claim` 接力。已 commit `output_patch` 保留。
- 崩溃检出延迟 ≤ SLA + harness 扫描滞后(已知代价);此期间接力 bot 看到根 `bbs_owner≠空`(被占),skip 选别的,不阻塞。

### 10.4 接力级幂等(多 bot 续做同一任务)
- 下个 bot `claim` 根后,经 `/dashboard` 读"根 `Goal`+`Acceptance` + DONE 叶子(+ 已报 `output_patch`)" → 重新判"剩余" → `bbs/attach` 挂新 scoped 节点续做;**已 DONE 不重做**。
- partial 接力:前 bot 报 `FAIL+gaps` + `output_patch` checkpoint → 节点 `FAILED` → 下个 bot 读到该节点 `run_info.output`(已做部分)+ DONE 叶子,续做未完成部分。
- 深度闸:BBS 接力深度由 per-task `bbs_relay_count`(每次 `bbs/attach` +1,≠ `loop_round`)计;`>= BBS_MAX_DEPTH`(默认 3)→ 图 `HUNG`(`bbs_relay_exhausted`)= 唯一人工入口(区别于节点 `HUNG` 冒泡的 `root_stuck`、图 `loop_exhausted`)。

### 10.5 seam:可恢复红线
- BBS pickup 只在"任务图空闲(无 `RUNNING`)+ 根 `PLANNING`(可委托)"时触发;该红线由 `bbs/attach` 经 `add_task_nodes` 的 a/b/c/d 触发条件裁(根须 `PLANNING` 等,不满足→`GraphIntegrityError`),claim 仅校验 `bbs_mode=True`;图级 `HUNG`(`loop_exhausted`/`root_stuck`)= 硬终态,BBS 不碰,bot skip(场景 G)。
- 不引入 `HUNG→RUNNING`/`FAILED→RUNNING` 复位转移;接管是"读旧 DONE + 挂新节点",不是"复活旧节点"。

---

## 11. 错误处理与容错

- **claim 409**:输者 skip 换任务,记日志;不算 bot 失败。
- **attach/result 非 claim 持有者**:403/409 拒(已被自己释放/被 harness 清/图级终态)。
- **attach 前提不满足**(图非空闲 / 根不可委托 / 图级 `HUNG`):409 或 404,skill skip 该任务。
- **长活被 SLA 切断**:bot 干活超 SLA 未完 → harness 清 `bbs_owner`+标节点终态;bot 后续 `bbs/result` 被 `on_bbs_report` 拒(节点已终态/非自己持有)→ skill 提示重 `claim` 接力(`output_patch` checkpoint 不丢)或放弃本次。**属设计内行为(长活分段接力),非错误**;故长活须周期 checkpoint。
- **`bbs/result` 字段非法**(缺 `acceptance_result`/`exec_error` 或 verdict 非法):`on_bbs_report` 走 fold-only(仅更新非状态字段)或拒;skill 须按 `TaskNodePatch` 字段构造(references/task-api.md 给样例)。
- **状态机非法转移**:`on_bbs_report` 经 `update_task_node_info` 时 `TaskStateError` 拒事件、状态不动(skill 收 no-op/错,重读状态再决策)。
- **触发器失败/未唤醒**:本次 pass 不跑,等下次唤醒;无副作用。
- **崩溃检出延迟**:见 §10.3;此期间接力 bot skip 该任务,不阻塞。

---

## 12. 测试策略

- **任务级 claim CAS**:并发两 bot `POST /bbs/claim` 同一 bbs 任务根 → 恰一 200、一 409;赢者根 `bbs_owner` 落定。
- **attach 仅持有者**:非持有者 `POST /bbs/attach` → 403/409;持有者成功新建 `run_mode="bbs"` 节点 + 翻 `PENDING→RUNNING`。
- **harness lease(崩溃)**:节点 `RUNNING` 不 report → SLA 到期 → 清 `bbs_owner` + 节点终态;断言**不重派、不升人工**(除非深度闸)。
- **409 扩展**:`TaskStateError` 经 `/bbs/claim` 冲突 → HTTP 409(非 500)。
- **接力可见性**:(a) bot1 `bbs/result`(`acceptance PASS`→DONE)→ bot2 `/dashboard` 看到 DONE 叶子 → claim+attach 续做;(b) bot1 `FAIL+gaps`+`output_patch` → bot2 读到根 `output` 续做未完成部分。
- **长活分段接力**:bot1 周期 `bbs/result`(`output_patch`)checkpoint 后超 SLA 被切 → bot2 从最近 checkpoint `output` 续做,无重做、无丢失。
- **drain 守卫**:`run_mode="bbs"` 节点不被 `_drain` 自动翻 `RUNNING`/no-op;harness bbs 到期走"清 claim+标终态"非重派。
- **深度闸**:接续达 `BBS_MAX_DEPTH` → 图 `HUNG`(`stuck`)。
- **图级 HUNG 边界**:根已 `HUNG` 的 bbs 任务 → bot skip 不碰。
- **skill 场景(奢验)**:驱动 agent 走 happy / partial-接力 / 多 bot race / 崩溃接力 / 图级 HUNG skip / 长活分段(mock REST),验证 6 步与硬约束"claim 成功才 attach/干活"。
- **E2E**:两引擎 bot(openclaw + aicoding)跑同一 `SKILL.md` 抢同一 BBS 任务 → 恰一赢;bot 崩溃后第二 bot 接力看到前序 DONE/`output_patch`。

---

## 13. 附录:关键文件与现有 API 对照

### 13.1 现有 task API(`adapters/http/task/router.py`,prefix `/api/task`)
- 读面:`POST /api/task/execute`、`GET /api/task/dashboard`(含 `?task_id=`、`?node_id=` 子树投影)、`GET /api/task/list`(含 `?status=`)、`POST /api/task/callback/report`。另有 `task_callback_router`(prefix `/task_loop/callback`,`/workflow_start`/`/workflow_result`/`/node_start`/`/node_result`,HMAC 鉴权)——BBS 不走此(其为 workflow 引擎回调)。
- 权威:`domain/models.py`(6 态 `Status`、`AcceptanceVerdict`、`TaskNode`、`TaskExecutionGraph`、`RuntimeInfo`、`TaskNodePatch`、`TaskGraphPatch`、`PlanResult`、`TaskCallbackData`);`task_graph/task_graph_service.py`(SSOT 写口 `initialize_graph`/`add_task_nodes`/`update_task_node_info`/`update_task_graph_info` + 转移表 `_ACCEPTANCE_TRANSITIONS`/`_DIRECT_TRANSITIONS`,`FAILED→{PENDING,HUNG}`、`HUNG` 终态);`task_center/engine.py`(`ExecutionEngine.on_execute/on_start/on_report/on_miss/on_harness` + 本 spec 新增 `on_bbs_report` + `_hung_and_escalate`/`_bump_loop_round`/HUNG 冒泡);`task_harness/harness.py`(`TaskHarness` SLA backstop);`task_center/task_service.py`(`TaskService` facade:`execute`/`get_task_dashboard`/`list_tasks`/`.callback`)。

### 13.2 本 spec 加法(契约面,HOW 留 plan)
- 新路由:`POST /api/task/bbs/claim`、`POST /api/task/bbs/attach`、`POST /api/task/bbs/result`。
- 读面小扩:`GET /api/task/list`/`TaskSummary` 暴露 `bbs_mode`。
- 守卫(引擎加法):`dispatch/drain` 跳过 `run_mode="bbs"` 节点;`TaskHarness` 对 bbs 节点 SLA 到期走"清 `bbs_owner`+死节点标终态",不重派。
- 编排核加法:`ExecutionEngine.on_bbs_report`(collector-free 回投路径,见 FR-EXT-03);BBS 深度闸 per-task `bbs_relay_count`(图 `extend_props`,每次 `bbs/attach` +1);`bbs/result` 落终态后自动清根 `bbs_owner`。
- 409 扩展:`TaskStateError→409` 扩到 `bbs/claim`/`bbs/attach`(注意 `TaskError` 当前不在 `_DOMAIN_ERROR_STATUS_MAP`,需 router 级 handler 或扩 map)。
- 新 skill 目录:`bbs-relay-pickup/SKILL.md` + `references/`(落点路径留 plan)。

### 13.3 不存在/不引入(本版清除的旧基线概念)
本版不再引用、代码库亦不存在:`BbsExecutorService`(及其 `claim`/`post_progress`/`retrieve_state`)、`TaskService.claim_node` CAS、`release_node`、`expire_lease`、`lease_until`、`T_fallback`、`node_released` 事件、`AttemptedRecord.outcome`(handoff/lease_expired)、`SubtaskState`、`RunMode` 枚举、`EventKind`(`state.updated`/`node.accepted`/`goal.verified`/`node.released`)、`on_event` fold、`POST /events`、`idempotency_key`、`FAILED→RUNNING`/`HUNG→RUNNING` 转移、9 态 `GraphStatus`/`NodeStatus` 机、复数 `/api/tasks/*` 路由树。语雀《接力执行方案对比》引用的 plaza/lease/schedule 契约不引入。

---

## 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-08-04 | 蒋建 | 初版(旧 9 态/`BbsExecutor`/`on_event`/`claim_node`/lease-handoff 基线):BBS 自主接单内容 skill 的 WHAT/WHY——复用旧 task API + 最小扩展,节点状态机零改动;给旧 `goal-driven-task-execution` §7.2 BBS 承载方式待定项落定答案。HOW 留 plan.md |
| 2026-08-04 | 蒋建 | 修订 lease 模型(旧基线内):不让 bot 预测工期、不续租;claim 时按全局 `T_fallback` 设兜底 `lease_until`;新增 release 路由/`release_node`/`node.released` 事件(均已随基线重写而废弃) |
| 2026-08-15 | 蒋建 | **按 `2026-08-09-task-goal-driven-execution-framework` 全量重落地**:放弃旧 9 态机/`BbsExecutorService`/`on_event` fold/`claim_node` CAS/`release_node`/`expire_lease`/`lease`/`EventKind`/`POST /events`/复数路由 全部旧基线(代码已"从零重新实现",逐一删除 §13.3)。BBS 自主接力重定义为:①发现 bbs_mode 任务(整图)→②**任务根级 claim CAS**(`POST /api/task/bbs/claim`,恰一赢输者 409)→③读根 goal+DONE 叶子自判剩余→④挂**一次性** `run_mode="bbs"` scoped 节点(`bbs/attach`)执行→⑤`bbs/result`→`on_report` 统一回投→⑥释放、下个 bot 读更多 DONE 接力。lease **复用 `TaskHarness` SLA**(无 `T_fallback`,崩溃到期清 `bbs_owner`+标终态,不重派);深度闸 `MAX_DEPTH`/`BBS_MAX_DEPTH`。新增 `bbs/{claim,attach,result}` 路由 + `TaskSummary.bbs_mode` + drain/harness bbs 守卫 + 409 扩展;声明不破坏上游契约(不动 `Status`/转移表/`TaskNodePatch`/`PlanResult`/`on_*` 签名)。spec 作为 corp `DeliveryPort` seam 的 Avernet 社区默认实现落点。HOW 留 plan.md |
| 2026-08-16 | 蒋建 | 按 `plan.md` 自检回填 4 处 HOW-级 refinement(实现期厘清):(1)`bbs/result` 不直调 `on_report`,改走新增 `ExecutionEngine.on_bbs_report`(**collector-free**——`on_report` 的 `_on_pass_collect` 会经 owner-bot 重规划+派发,与 §10.4 bot 自挂接力冲突);(2)根收口信号 `root_verified: bool`(bot 判根 goal 满足时带 true→根 DONE+图 DONE);(3)BBS 深度闸明确为 per-task `bbs_relay_count`(图 `extend_props`,每次 attach +1,区别 `loop_round`/`MAX_LOOP`),默认 `BBS_MAX_DEPTH=3`,超→图 `HUNG(bbs_relay_exhausted)`;(4)claim 仅校验 `bbs_mode`,空闲/根 PLANNING 由 `bbs/attach` 经 `add_task_nodes` a/b/c/d 裁。同步更新 FR-EXT-03/FR-PICK-05/FR-IDEM-04/FR-PICK-02/§8/§9.3/§10.2/§10.4/§10.5/§11/§13.2/AC-08。 |
| 2026-08-16 | 蒋建 | 最终评审建议回填:§10.2 长活-checkpoint 措辞对齐实现。`on_bbs_report` 的 `finally` 在**每次** `bbs/result`(含仅 `output_patch` 的 checkpoint)都清根 `bbs_owner` 释放 claim,故长活 = 分段接力(每段一 claim/result),不在单次 claim 内中途 checkpoint;被切断后下个 bot 从节点 `run_info.output` checkpoint 续做。 |
