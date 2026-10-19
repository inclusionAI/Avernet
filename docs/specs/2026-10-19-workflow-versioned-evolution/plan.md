# 工作流版本快照与问题进化 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 在当前会话按任务实施；遵守用户不派发子任务、不另开会话的要求。本文不授权启动实现、真实模型、运行、回填或部署。

**Goal:** 实现每次运行/分析的固定快照关联、当前版本问题优先聚合、历史折叠和人工引用、每日当前版本范围。

**Architecture:** 复用已有部署历史、保存 Git 快照和修复任务/队列。新增小型引用契约与必要关联字段，不新增快照仓库或调度系统；公共业务、OCB Host、ClawMind 与生成消费者通过公开契约配对。

**Tech Stack:** TypeScript、现有 SQLite/MySQL/ZDAS 适配、Vitest、Node test、现有 Python 生成消费者测试。

**Spec:** [spec.md](spec.md)。扩展指标单独执行 [metrics-plan.md](metrics-plan.md)。

状态：2026-10-19 按生产影响与投入范围复审；仅文档，尚未实施或运行产品测试。

实施主次：先用现有运行版本关联部署快照，修正分析输入；再交付当前问题聚合、历史查看勾选和每日当前版本范围。remote仅补实际缺失的固定身份转发，指标独立扩展。普通部署路径不等待ClawMind改造，不要求全库回填、全历史模型重算或新生成协议。

## Global Constraints

- 最新版本 = `workflow_deploy_history.is_active=1`；版本号与部署编号不混用。
- 用户可勾选历史建议；每日任务只默认当前版本。旧补丁不能原样跨版本 apply。
- 复用快照，缺失不猜；不得改写旧建议摘要、已有冻结输入或原始证据。
- 不修改已有retry参数、接口或执行行为；不因本功能的快照关联缺失新增运行/重试阻断条件，不改造低频续跑场景。
- 增量字段、兼容旧客户端，服务端先行、客户端渐进上报；不要求生产插件同步升级。改动必须直接对应R1–R6，指标独立扩展，不顺带改运行引擎或部署流程。
- 保留完整分页、原始来源、权限、幂等、Git 完整性和现有大小限制；不增加自动发布/回滚。
- 不创建会话或派发子任务。按可独立验证的行为提交，不按格式、文档或每个函数拆微小提交。
- 实现时使用隔离工作树，保护主目录和原产品工作树的 dirty 修改；每个实际工作树安装现有 hooks。
- 修改的源码不得超过1000行；不复制公共业务到 OCB，也不跨包源码导入。

## Review Focus

1. A/B/C 并发 save 与延迟分析是否偷换来源配置：任务1、2验证。
2. 新部署内容相同、回滚及旧版迟到分析是否继承错误生命周期/分页范围：任务3验证。
3. 历史建议指向已删除节点、相同字段不同输出要求、引用是否改写旧生命周期、v1消费者是否逐项覆盖：任务4验证。
4. 用户核对后提交前/自动后续批次变版、旧任务占用、重启或竞争，是否换目标/回退历史/重复派发：任务4、5验证。
5. 旧数据 snapshot 缺失与真实 Git 缺 manifest，是否伪装为 queued/已解决或阻断全部读取：任务2、3、6验证。
6. 旧聚合条目跨版本混合，是否被一个旧itemId错误归为单版本：任务3验证。

## 0. 实施前定位与依赖

文档保存在主仓库，仅文档新增；本次代码调研使用公共工作树 a9d9adbe、OCB fa558ae155、ClawMind 82979f69 的相关源码，并对有 dirty 的 ClawMind 关键路径核对 committed HEAD。部署 SHA 未据此确认。

复审另核对生成消费者 `/Users/wenyang/.codex/worktrees/workflow-proposal-plan-taskguard` 的 `736491cf`：已有 v1 当前文件整合和逐项覆盖。以下计划优先复用该行为，不预设 v2/action/状态枚举升级；不能用主目录仅支持 generate_candidate 的旧 checkout 推断现有能力。

下列 `apps/evolverun/clawweb` 为当前 dev 布局；部分旧 checkout 是 `src/evolverun`。实施前核对实际基线，不能为了套计划搬目录。

公共路径前缀简写：`CW=apps/evolverun/clawweb`，`WF=$CW/public/modules/workflow`，`EV=$CW/public/modules/clawevolve`，`SH=$CW/public/shared`。文中的路径均是所属仓库相对路径。

- [ ] 列出已有工作树、状态和 refs，选择合适隔离工作树；不修改原产品任务工作树。读取所在模块 AGENTS 和架构四文档。
- [ ] 核对公共/镜像/Host/ClawMind/消费者真实 revision 和包 exports；任何历史 mirror 结论须重新验证。
- [ ] 将真实 IDS 全量分页、七个原始样本、版本72/73快照作为脱敏离线 fixture，保留证据的实际边界；不用构造 fixture 冒充线上复现。
- [ ] 将日志 b01fc3d4... 另列故障追踪，已有只读通道可核对workflow/ref/阶段；无通道则记录缺口，不触发worker试探，不将其闭环设为本功能前置条件。

## Task 1：固定运行快照关联与原子持久化（R1；A1、A2、A4）

**Files**

- Create SH/server/contracts/workflow-snapshot.ts：spec §4 的引用和 binding，纯结构校验/稳定 snapshotKey。
- Create SH/server/schema-workflow-snapshot.ts，并在 SH/server/schema.ts 注册；SH/server/db.ts 的现有 runMigrations 执行：两张表的 `workflow_snapshot_json` 幂等 nullable TEXT 迁移，迁移号取实施基线下一个未占用值，不改写旧迁移。
- Modify WF/server/repositories/flow-run-repository.ts、WF/server/routes/internal/runs.ts：注册事务内保存、读取关联。
- ClawMind仅在remote固定身份未到达服务端时补最小转发，优先核对src/db/api-repositories/flow-run-api-repository.ts及对应类型/本地适配；复用现有workflowPin/workflowSnapshot，不预设改controller、Git保存/加载或执行流程。
- Tests：SH/server/__tests__/workflow-snapshot-migration.test.ts（新）、WF/server/routes/internal/__tests__/run-snapshot-binding.test.ts（新）；若修改remote上报，再新增ClawMind tests/run-snapshot-binding.test.ts。

**Interfaces**

- `workflowSnapshotKey(ref: WorkflowSnapshotRef): string`；部署 key 绑定 workflow/version/deploy，保存 key 绑定 workflow/repoId/commit/specPath；repoId复用Host已有授权仓库身份，不存凭据/绝对本地路径。
- `validateWorkflowSnapshotBinding(value: unknown): WorkflowSnapshotBinding`；不解析环境、不执行 Git。
- 部署来源由服务端从既有版本/部署字段解析，无新增字段也可resolved；`/runs`可选增量字段`workflow_snapshot`仅承接必要的remote固定身份，不升级原必填契约，API/本地两条路径一致。
- 保持 `workflow_version=-1` 和 deploy NULL 的已有 remote 含义，不能强填当前 active 版本。

**Steps**

- [ ] 先写失败回归：39/34保留且旧插件无需新增字段就能关联；remote B/C 都是-1但key不同；历史暂缺/身份不符标unresolved且不拒绝合法运行注册；数据库关联写入失败不返回假成功。
- [ ] 复用已有启动快照捕获，普通部署解析不读Git；remote只补遗漏的身份转发，不增加执行前联网确认或新补报系统。沿用同flowId幂等处理且不重跑业务；仓库绑定变化后旧引用仍定位原对象。
- [ ] 候选引用沿用已有注册身份核验，不能猜成active部署。
- [ ] 验证回归先失败，再实现最小引用、迁移和运行解析/转发；不复制配置内容或添加第二套保存命令。
- [ ] 运行 focused tests 与相应包 type check。迁移测试必须重复运行迁移，并覆盖 SQLite 真实执行和 MySQL/ZDAS SQL 生成；后者不冒充真实数据库集成通过。
- [ ] 如涉及remote上报，文档同步ClawMind docs/INDEX.md指向的已有运行/版本文档；不为未改的客户端强制新增工作，按完整行为提交。

## Task 2：分析按运行快照取输入并固定关联（R2；A2、A3、A4）

**Files**

- Create WF/server/services/workflow-snapshot-reader.ts：解析已绑定部署记录/保存来源；通过 Host 端口读取 Git，不能在 core 硬编码远端。
- EV/server/repositories/workflow-evolution-repository.ts：创建分析时绑定引用；完成结果/建议投影保留分析引用。
- EV/server/routes/internal/evolve.ts：替换最新 workflow_specs 查询，通过注入 reader 返回固定 spec/digest/metadata。
- 公共与 OCB composition root：注入公开 reader；OCB internal/bootstrap/clawweb/server/repair-git-store.ts 提供按固定保存身份读取的适配。
- ClawMind src/db/api-repositories/evolve-api-repository.ts、src/evolution/evolution-agent.ts：新增元数据兼容读取，提示语随常规升级改为“本次运行的工作流配置”；原workflowSpec/workflowSpecDigest字段已被消费，服务端修正内容即可生效，不以插件同步升级作为前置，不新增分析轮次。
- Tests：EV/server/routes/internal/__tests__/analysis-snapshot-input.test.ts（新）、WF/server/services/__tests__/workflow-snapshot-reader.test.ts（新）、OCB对应 snapshot reader 适配测试。

**Interfaces**

- 注入端口 `readWorkflowSnapshot(ref: WorkflowSnapshotRef): Promise<{workflowSpec: Record<string,unknown>; workflowSpecDigest: string}>`，加载后校验身份和摘要。
- 分析输入新增 `workflowSnapshot`，保留原 `workflowSpec/workflowSpecDigest` 名称；创建后不能换引用。
- 复用evidence manifest的capturedAt/maxId范围，不把后续运行或配置变化同步覆写到已完成分析。
- 缺失返回 `WORKFLOW_SNAPSHOT_UNAVAILABLE`；已证明旧分析配置不相符返回 `ANALYSIS_SNAPSHOT_MISMATCH`，保留原始结果可见。
- 引用不存在与存储读取故障分别报告，数据库/Git错误不能被吞成“无快照”。

**Steps**

- [ ] 失败回归：A运行后save B，分析读A；remote B后save C，分析读B；重试返回同引用/摘要；快照缺失不回退当前；同分析重复创建不同引用被拒。
- [ ] 使用既有 source/deploy snapshot 定位；实际规范化 fixture 验证39/34配置摘要，不混比 raw YAML、commit、pin字符串。
- [ ] 测试跨版本多run输入拒绝混用一个spec；旧错误分析不能仅按flowId回填成可信。验证configDigest与实际分析payload一致，目标Git语义身份仍复用proposalSpecDigest。
- [ ] 旧数据先通过已有版本字段/快照只读解析；分析创建时固定关联，不要求全库回填。批量持久化回填仅作为可选维护工具，按需盘点后处理，不在普通GET写回、不执行线上脚本。
- [ ] 测试签名/权限与无循环依赖：Clawevolve 不导入 Workflow concrete 服务；composition root 注入；shared不反向导入两个业务包。
- [ ] 运行 focused route/repository tests、受影响包 check/build，提交完整分析输入修复。

## Task 3：问题优先的版本范围、历史折叠与后台整合（R3、R4；A5、A6、A7、A11、A15）

**Files**

- WF/server/contracts/repair-workbench.ts、repair-proposal.ts；EV/server/repositories/issue-aggregation-repository.ts：版本作用域查询与计数。
- WF/server/repositories/repair-source-adapter.ts、services/repair-batch-service.ts、repair-proposal-integration.ts、repair-inbox-projection.ts。
- $CW/public/app/clawweb/server/repair-workbench-runtime.ts：聚合只读 snapshot identity 接口与生成 Git 基线解耦。
- $CW/public/app/clawweb/server/repair-proposal-model.ts：分类输入改用该工作单元的sourceSnapshot；保留现有provider传输、预算与错误处理。
- WF/web/components/workflow-workspace：EvolutionTab、IssueDetailDrawer 及 repair-batch 的查询 hook、IssueRepairPreview、ProposalEvidence、RepairItemDetail；实施前核对实际文件名。
- WF/web/api/repair-batches.ts：版本查询、响应元数据与历史选择请求字段。
- Tests：已有 repair-proposal-integration.test.ts、repair-inbox-projection.test.ts；新增 versioned-issue-scope.test.ts、VersionedIssueArchive.test.tsx。

**Interfaces**

- `RepairCandidatesQuery`/问题查询增加 `versionScope?: 'current'|'historical'|'all'`，缺省 current；旧 `includeHistorical` 保持来源时间范围语义。
- 响应增加 `activeSnapshot`、`versionScope` 和按问题归属的版本分组元数据；条目携带source snapshot/key与服务端判定的引用可选状态/原因；摘要和完整读取身份一致。无active与身份异常不能fallback到最新save。
- 问题 signature 不改；新版本 item/plan identity 包含 snapshotKey，不继承旧生命周期。可证明单来源的旧itemId附加关联；混合来源旧item保留原任务并标需核对，不任取一个版本、不用于新任务；不新建通用ID映射平台。
- coordinator读取返回 `ProposalReadSnapshot = {items: RepairInboxItem[]; activeSnapshot: WorkflowSnapshotBinding | null}`；null明确表示尚无active，身份异常用unresolved，不与来源缺失混淆。条目自带来源binding。模型分类端口改为 `classify({sourceSnapshot: WorkflowSnapshotRef, items: RepairInboxItem[], signal: AbortSignal}): Promise<unknown>`，按来源快照整合，不能用当前active替换历史来源。GET/扫描不为分桶执行 Git manifest 读取，生成时仍解析完整 RepairBaseline。

**Steps**

- [ ] 失败回归：同问题当前/历史分别总数分页；新部署/迟到分析/回滚；相同补丁新版本不继承verified；用户已选历史不因刷新静默消失。
- [ ] 覆盖无active、多active、混合来源旧条目。先按版本过滤再计数排序分页；缓存/分页key带snapshotKey，部署切换不沿用旧页；历史仍可展开。
- [ ] 固定真实 IDS 三条并集 fixture，验证一项方案保留全部原始成员；另测不同输出约定/不同快照不强合并、exact对保留独立来源。
- [ ] 更新分类协议/缓存key，验证active切换不作废未变的历史整合结果；来源变动仍supersede。旧格式job保留可读，不拿旧完整baseline的缓存结果当新快照协议结果。
- [ ] 实现 current/history 查询、历史折叠与跨页选择；仅历史的问题不占默认页，开启历史视图可发现。用户文案统一“工作流运行版本”。
- [ ] 时间窗口与版本范围独立；archive开启既有includeHistorical全历史读取，超过30天的保留建议仍可查看/勾选。选中ID完整读取和提交复核不被默认列表时间窗口截断。
- [ ] 修正 bounded summary 的结构化修改展示。worker状态和归档/需核对状态分开；未入队不可说已排队；给任务/快照key/阶段/原因/时间的安全日志，不记录凭据或完整模型输入。
- [ ] 保持原lease/预算/重试/验证；当前版本优先整合，历史读取已有结果。测试归档/展开/切版不新增全历史模型重算，选中历史仅在本次生成整合；沿用provider缺失、429、输入过大及GET不派发模型回归。Git故障只做已归因的独立最小修复，不加全局吞错。
- [ ] 运行问题分页/投影/整合/UI focused tests、check/build，提交一个版本视图与整合行为变更。

## Task 4：历史建议显式引用生成（R5；A8、A12、A13）

**Files**

- WF/server/contracts/repair-batch.ts、repair-workbench.ts、repair-proposal.ts；services/repair-selection-review.ts、repair-proposal-integration.ts、repair-batch-service.ts。
- WF/server/repositories/repair-batch-repository.ts；services/repair-release-service.ts、repair-verification-service.ts、repair-auto-verification.ts及changedChainItems等实际回写/释放调用点：历史只读来源与目标任务结果分开。
- WF/server/routes/repair-batches.ts；前端repair selection/request构造与错误恢复。
- OCB internal/bootstrap/clawweb/server/repair-execution.ts 及其 tests/docs。
- 生成消费者：优先核对 src/repair_ais/workflow_repair/integrated_writer.py、integration_context.py、schema.py、agent_writer.py；相关writer/checker/runner仅在真实缺口需要时改动。新增fixture至 tests/test_workflow_repair_integration.py，保留 tests/test_workflow_repair.py 回归。
- Tests：新增 historical-reference-generation.test.ts、历史引用生命周期/发布回归、现有v1 envelope/parser/writer/checker配对测试；沿用真实Git临时仓库和确定性writer。

**Interfaces**

- 请求新增 `targetSnapshotKey`、`historicalItemIds?: string[]`；服务端核验目标等于active，历史集合恰好等于全部非目标成员。三者与内容版本进入幂等摘要；旧客户端当前-only兼容，历史缺目标key拒绝。
- 客户端随勾选自动构造目标/历史字段；沿用现有生成按钮，展示目标与历史来源数量，不增加逐条二次确认或审批。
- 冻结 item.context 中sourceSnapshot/targetSnapshot/referenceOnly；保持原始摘要与operations。复用 nested `workflow-proposal-plan/v1` + `integrate_candidate_v1`，baselineSpecDigest指目标，原始成员全覆盖一次；不降级逐项补丁执行。
- 每原始成员结果沿用 changed/not_needed/unresolved；not_needed原因区分已满足/不适用，未知必须unresolved。历史结果写入目标任务记录，不改原历史item生命周期或占用。
- 未通过实际配对测试的消费者在派发前返回 `HISTORICAL_REFERENCE_UNSUPPORTED`；核心交付必须配对可用，不以不支持作为完成。

**Steps**

- [ ] 失败回归：历史集合缺报/多报返回具体ID；参考模式不篡改原摘要；权限/内容/快照缺失仍拒；不支持整合的消费者不派发、不创建假processing。
- [ ] 先跑实际现有v1配对消费者基线，证明context保留、源摘要不需等于目标摘要、当前完整文件整合、逐项覆盖和真实diff范围正确；无缺口不改消费者生产代码/协议。不扩展单case分析、工具权限、独立重新评估模型或新服务。
- [ ] 生成前将写入范围重叠的选中目标归入同一整合单元，互不重叠才分计划，防止跨版本计划依次覆盖同一Prompt。覆盖已满足、节点删除、结构变化、scalar真冲突、prompt互补与旧输出契约不适用；未知不写补丁。
- [ ] 测试“选择时A、提交前B”和准备期间变化均返回 `TARGET_VERSION_CHANGED`且保留选择；同配置新部署也不能静默换目标。已提交请求/人工反馈重试沿冻结target与幂等身份。
- [ ] 历史来源的pending/processing/verified/no_action不单独禁选，内容版本与本次目标互斥仍核对；生成/反馈、失败、取消、发布预留与成功/失败回调、效果验证均不抢占或重写旧item。现有changedChainItems/源选择检查不能将引用当待处理item；当前项既有生命周期回归不变。
- [ ] 防重查询复用现有任务记录，绑定workflow/目标key/所选来源内容；同requestId返回原任务，不能通过占用旧历史item实现新目标任务防重。
- [ ] 端到端以旧原始建议+当前目标→实际producer envelope→实际parser/writer/checker→真实临时Git候选验证。模型可确定性替身；不得声称因此线上模型质量已验证。
- [ ] 当前-only v1 suite必须继续通过；历史参考配对可用是核心完成条件，不能只交付“暂不支持”按钮。仅在测试证明缺口时修改消费者；不做无关TaskGuard重构。

## Task 5：每日范围固定当前版本（R6；A9、A11、A14）

**Files**

- WF/server/services/workflow-automation-selection.ts、workflow-automation-service.ts、workflow-automation-round.ts；repositories/workflow-automation-repository.ts；contracts/workflow-automation.ts。
- 对应已有workflow-automation测试与新增 versioned-automatic-scope.test.ts。

**Interfaces**

- 自动周期data保存 `targetSnapshotKey`、原有cutoff/selection/input fingerprints；由服务端current解析，不由UI传历史选择。
- `selectAutomaticScope` 先限制来源快照/问题/方案全部为active，再应用既有影响、时间、完整成员和大小约束；自动请求historicalItemIds为空。
- 缺当前建议/快照、版本变化分别产生可解释原因，不拿历史补足。
- activeDraft去重查询按目标key；旧review/blocked任务不永久阻塞新目标。真实在途/结果未知/既有互斥仍延期，显示任务与原因，不自动取消或放开并发。

**Steps**

- [ ] 失败回归：高影响旧版与低影响新版共存仅选新版；全是历史时不建空任务；remote测试与未知来源不入正式scope。
- [ ] 实现选取/派发前版本复验；变版使旧范围过期再选，已派发请求不重建。新版本同名问题不能被旧cycle fingerprint永久跳过。
- [ ] 每个后续批次/恢复/重试生成也复验，不继续旧目标；结果未知先按requestId查清，不能换版新建重复请求。测试旧草稿review/blocked与真实在途的区别。
- [ ] 选取前版本过滤贯穿问题排序、完整方案成员、单项读取、inputDigest/revisions/fingerprint及scope缓存；历史结果变化不使当前选择无故SOURCE_CHANGED，任何混合成员方案不得混入自动任务。
- [ ] 重启、跨实例、当日无新证据、关闭开关、owner变化和自然日cutoff沿用原有回归；不修改用户既有调度时间/开关。
- [ ] 运行automation相关suite与包check，提交完整每日范围变更。

## Task 6：兼容、联合验收和交付证据

**Files**

- 上述模块测试、公开API/Host使用文档和当前目录的验收记录。新增源码自测行数，不扩大CI allowlist。

**Steps**

- [ ] 按spec A1–A9、A11–A15逐项走本地真实router+SQLite+临时Git+配对消费者，记录实际输出；包括旧客户端/旧字段NULL读取、历史引用原状态和仅支持旧generate_candidate的消费者拒绝。A10由指标计划验收，不假称本期全部完成。
- [ ] 当前功能定向通过后运行所有受影响模块suite、typecheck/build及现有包边界检查；不因测试慢/失败放宽门槛。
- [ ] 核查新迁移幂等与回滚：旧代码可忽略新增列，但不得恢复/派发带referenceOnly的新任务，因为旧服务会按当前项占用/回写；回滚前暂停派发并保留未完成任务。v1消费者配对复用不等于旧公共/Host服务兼容历史引用。核对archive不写生命周期、两环境scopeKey隔离。
- [ ] 核对新增字段不要求旧客户端上报、不改变既有retry契约，也不因关联缺失阻断生产运行；上线顺序先兼容服务端、后逐步启用新上报及功能。关联未知仅限制本功能，不把同步升级作为前置条件。
- [ ] 预发只读验收读取实际已安装版本、worker任务/原因/投影及真实IDS原文；功能须写入才能验证的项目明确未执行，不用自动触发模型/扫描获取证据。
- [ ] 交付列明公共/Host/ClawMind/消费者精确SHA、CI与制品支持，安装/配置/真实执行另行授权。生成、合并、部署和实际修复效果分别报告。

## 验证命令

以下是实施时要执行的命令，不是本次文档已执行的结果。工作目录必须是隔离checkout；先按该checkout公开依赖说明安装/构建需要的依赖，不借用陈旧`.build`链接。

```bash
# Avernet，工作目录 apps/evolverun/clawweb
npm run test --workspace=@avernet/workflow -- server/services/__tests__/repair-proposal-integration.test.ts
npm run test --workspace=@avernet/workflow -- server/services/__tests__/historical-reference-generation.test.ts
npm run test --workspace=@avernet/clawevolve -- server/routes/internal/__tests__/analysis-snapshot-input.test.ts
npm run test --workspace=@avernet/clawweb-shared -- server/__tests__/workflow-snapshot-migration.test.ts
npm run test --workspace=@avernet/workflow
npm run test --workspace=@avernet/clawevolve
npm run test --workspace=@avernet/clawweb-shared
npm run test --workspace=@avernet/clawweb
npm run ci:check
npm run ci:build

# ClawMind，仅修改该仓库时运行；工作目录对应隔离checkout
node --import tsx --test tests/run-snapshot-binding.test.ts tests/multi-version-run.test.ts tests/versioned-pack-runtime.test.ts
npm run build

# OCB，工作目录 apps/evolverun/clawweb，公共依赖须指向本次验证的精确revision
npm run test:ci:internal
npm run ci:check:internal

# 生成消费者，工作目录对应配对checkout
uv run pytest tests/test_workflow_repair.py tests/test_workflow_repair_integration.py
```

发布前重新确认这些入口未漂移；不得因目录漂移跳过测试，也不得在未执行时写“通过”。本计划不要求每步commit或reviewer子任务，按六个行为任务和仓库边界合并提交。


## 实施记录（2026-10-19，本地）

本轮完成 Task 1–5 的四项主链路和 Task 6 的本地兼容验证，指标扩展未实现。没有修改 retry 参数、接口或执行行为，没有增加定时任务或 TaskGuard 能力。

- 公共模块目录以当前 dev 为准：`apps/evolverun/clawweb/public/{shared,modules,app}`。
- 快照解析放在 shared 的 `server/services/workflow-snapshot.ts`，不反向依赖业务模块。部署使用原有版本/部署编号；保存测试使用已捕获的运行配置和原提交，读取分析无需再访问 Git。不新增 Host Git 快照仓库或 reader 端口。
- `flow_runs` 在新注册时写服务端核验后的关联；旧客户端无需提供新字段。分析创建同时冻结到既有 `scope_json.workflowSnapshot`，新列可用时一并写入。托管数据库尚未加列仍可创建带固定关联的分析。未知关联保留记录，不阻断合法运行；关联写入失败不会返回成功。
- 保存引用必有 commit/configDigest/sourceFlowId。已知 repoId/specPath/packDigest 可附带，旧 pin 未提供时不编造。来源配置从原运行已捕获快照读取；完整 Pack 候选校验仍沿用既有 Git 完整性检查。
- 旧 issue API 不传 versionScope 时保留原行为；新页面和每日任务显式传 current。历史折叠单独传 historical，全量查找传 all；选中 ID 核对读取完整历史。普通列表和聚合准备不依赖 Git baseline。
- 历史建议沿用现有 `workflow-proposal-plan/v1` 一次性整合目标。Host 只声明已存在的 consumer 能力，旧 Host 继续支持当前版本生成；缺能力时历史生成在写任务/派发前明确拒绝。源 digest、归属和状态不改。
- 后续反馈保持任务冻结目标；新的任务选择要求目标仍为 active。每日每批及恢复核对目标，未知派发先按原 requestId 查证，不重新派发。
- SQLite 真实迁移重复执行；MySQL/ZDAS 仅验证 DDL 生成，未连接真实数据库。本地测试使用确定性模型、发布端口和临时仓库；没有真实模型、业务运行、线上回填或部署。

实际新增测试集中在 shared `workflow-snapshot.test.ts`、ClawEvolve `analysis-snapshot-input.test.ts` 与 `evolve-knowledge.test.ts`，Workflow `versioned-issue-scope.test.ts`、`historical-reference-generation.test.ts`、每日任务现有测试及 `HistoricalIssueArchive.test.tsx` / `EvolutionVersionScope.test.tsx`。

部署顺序：先上线兼容的公共服务与 Host（更新镜像仍走研发指南规定的人工流程），再按常规节奏升级 ClawMind 的可选远程快照上报。已有部署运行不依赖插件同步升级。当前交付只包含本地代码、测试和构建，不代表 CI、合并或生产验证完成。

本地联合回归：Workflow 949、Shared 138、公共 Host 43、OCB Host Git/派发 18、ClawMind 51，相关 TypeScript 检查与公共/内部构建通过。ClawEvolve 对最终依赖的全量 1233 与最新分析边界 26 均通过。既有 v1 consumer 在原工作树只读运行 10 项测试通过，没有改动 TaskGuard 源码。MySQL/ZDAS 无真实连接验证，未进行 CI、线上运行或部署。
