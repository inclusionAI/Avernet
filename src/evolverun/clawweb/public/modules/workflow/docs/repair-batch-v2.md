# Workflow repair v2：问题列表与候选审核控制面

当前提供 Workflow 所属的问题列表、人工无需处理/恢复、冻结修订、任务/步骤持久化、候选审核与反馈修订服务和 HTTP 路由。Host 未注入 `RepairExecutionPort` 时，列表和人工处理仍可用，生成返回 503；`publication` 始终为 false。注入后，服务会保存 AIS jobId、轮询平台状态，并在 Job 成功后从 Git repair 分支读取候选结果；不接受 AIS 回调，也不使用 OSS 保存 Pack。审核后发布、灰度和效果验证仍未实现，不能据此认为发布链路已完成。既有单条/分组建议应用协议保留。

## 页面使用

工作流级“问题与优化”先展示修复收件箱。可按待处理、处理中、待验证、已关闭、暂不处理或全部筛选，
同组多条建议保持独立状态；有可继续的批次时显示任务入口，不能误建第二批。选择默认包含可处理项，
可剔除/重新多选，上限 100 项；超限或来源变化明确提示，不静默只取前 20 条。

“暂不处理”对应可恢复的 `no_action`，需要填写原因；未持久化的旧应用/验证历史只读，原控制入口保留在
“诊断证据与历史应用”区域。没有可执行建议的诊断也仍能从该区域查看。筛选和打开的任务按工作流保存在会话存储。

任务详情分开显示“最近尝试”和“最近成功候选”。支持按版本查看逐项结果、静态/Mock 检查、未覆盖项，
并比较任务基线或上一成功候选的文件差异。反馈下一版重新确认选择，但不改变原任务基线。
取消释放建议占用且保留历史，不能等同于已终止远端 AIS 计算。派发未确认显示错误码和同版本重试入口。

公共/内部 Host 已装配真实来源读取和权限；执行提供方默认未注入。没有 AIS 时允许查看、整理和处置，
不会生成模拟候选，也不显示可点击的发布/灰度操作。复发归因、后续发布和新验证闭环不属于这次页面交付。

## HTTP 与服务边界

工厂为 `createRepairWorkbenchService(db, sourcePort, executionPort?)` 和 `createRepairBatchesRouter({ service, authorize })`，挂载前缀 `/api/workflow-repairs`。路由只依赖服务接口，Host 注入已验证的登录与工作流权限。浏览器提供 itemId、摘要和反馈；proposal、证据、基线和 actor 由服务端决定。受限于部分 Bot 的运行查看权限不能读取整个工作流的修复批次。

| 请求 | 行为 |
| --- | --- |
| `GET /candidates?workflowId=` | 只读合并当前来源与持久化处理历史，返回 inputDigest、权限和能力；不物化写库 |
| `POST /` | 校验当前来源摘要，原子写入处理项、修订、workflow_repair task 和 draft step，提交后派发，202 |
| `GET /:taskId` | 当前尝试、最近成功候选、修订列表和派发状态 |
| `GET /:taskId/revisions/:revision` | 指定冻结修订 |
| `POST /:taskId/revisions` | 原任务冻结基线 + 最近成功父候选 + 当前选择/反馈，新增修订，202 |
| `POST /items/:itemId/disposition` | no_action / restore；请求带工作流、内容版本、状态版本和 requestId |
| `POST /:taskId/cancel` | 显式取消未发布任务并释放占用，200 `{ok:true}` |
| `POST /:taskId/retry-dispatch` | 仅当前 drafting 且 created/dispatch_failed，同一次输入重派，202 `{ok:true}` |
| `GET /:taskId/revisions/:revision/diff?base=baseline\|parent` | 只用已保存的精确 commit，通过可信提供方读取差异 |

view/edit 在每条路由检查，任务请求先定位其真实 workflowId，再鉴权；不采用 body.actorId。来源变化返回 SOURCE_CHANGED/409；状态或幂等内容冲突 409；容量超限 413；缺失来源/任务/修订 404；未注入生成或 diff 能力返回 503。

`RepairSourcePort.load(tx, workflowId)` 在写事务内重新加载，来源摘要不混入可变处理状态。新问题与历史任务并列展示；旧协议中已应用/验证/忽略的未迁移项作为只读历史，不自动变成新 pending 项。只有同建议的新证据时保留原状态，预览刷新 context；人工写入可刷新 item 的证据投影，但历史 revision.input_json 永不更新。

来源按精确建议内容而非聚合总结组织。文本建议仍保留自己的来源身份，并附能定位到的同签名诊断/证据；
缺失的历史诊断/事件显式标记，不以其他运行猜补。当前加载器尚未生成复发 episode；旧的已关闭项不会因新证据自动重开。

task config 仅保存 workflowId、latestAttemptRevision、latestSuccessfulRevision、approvedRevision 小引用。每一轮只有一个 `workflow_repair_draft` step；不创建 publish step。待审核和可重试失败时任务仍为 running，面板应按 revision.phase 显示状态。

旧 Evolve 列表的行查询和 total 同时排除 `workflow_repair`，旧 detail/retry/cancel/input/report 入口均拒绝此类型，
不能用旧任务接口绕过 Workflow 工作流权限或改写新状态机。

派发接口必须以 taskId/revision 幂等。网络异常保留原输入和 dispatch_failed，刷新后可显式重试；不会用新诊断替换已提交输入。进程中断留下的 dispatching 不自动重派，须由执行方核对结果。新建或反馈修订不会授权发布。

可信 Host 可调用工厂返回对象的 `report`，本路由没有公开回调入口。报告必须绑定候选 commit、父 commit、Pack digest、完整 itemResults 和逐项 coverage，checks.candidateDigest 等于 canonical draft 摘要。检查失败进入 blocked，成功或未覆盖进入 review，均不自动 applied/verified。Host 的 AIS 接口所有者仍须先验证 execution ticket、task/step/revision/executionId 和重报身份。

## 库接口与调用方向

Workflow 服务调用 `RepairBatchRepository` / `RepairTaskRepository`；仓库消费公开的 `@avernet/clawweb-shared/server/db` 的 `IDatabase`。现有 `./server/*` package export 暴露这些接口，没有跨模块私有实现依赖。仓库是内部持久化 API，不是执行票据验证或对外授权边界。

所有写方法的第一个参数必须是调用方 `db.transaction(async tx => ...)` 的事务对象；任务创建、步骤结算和 config 指针更新必须使用同一个 `tx`。方法不会自行提交事务。调用方不能吞掉写异常后继续提交，也不能把普通连接作为多项修改的事务传入。只读方法按显式 workflowId 限定查询，但调用前仍须鉴权。

| 方法 | 作用 |
| --- | --- |
| `lockWorkflow(tx, workflowId)` | 锁既有 workflow_specs 行；SQLite 先取得写预留，MySQL/ZDAS 使用 FOR UPDATE |
| `materializeItem(tx, {workflowId, episodeKey, item})` | 精确身份去重，合并来源；内容不覆盖；新内容/轮次新建 pending 项 |
| `setDisposition(tx, {...})` | 未占用 pending ↔ no_action，版本 CAS，追加人工审计；同请求先返回原响应 |
| `claimItems(tx, {...})` | 逐项 stateVersion CAS，占用 pending 项或推进本任务的 processing 项 |
| `createRevision(tx, {...})` | 请求幂等、当前修订 CAS、同工作流 v2 任务互斥、校验固定基线/成功父候选、冻结输入和项占用；移出项恢复 pending |
| `completeDraft(tx, {...})` | 只结算当前 drafting 修订；候选与检查绑定同一 commit，首次成功后不可覆盖 |
| `failDraft(tx, {...})` | 只结算当前 drafting 修订，不释放可重试任务占用，不修改上一成功候选 |
| `cancelTask(tx, {...})` | 仅允许未发布状态明确取消，并释放 processing 项 |
| `getItem / getRevision / latestSuccessfulRevision` | 读取持久化内容和最近成功修订 |

同一当前修订的成功/失败结果完全相同则幂等返回；冲突结果、已取消或被新一轮替代的回报返回 STATE_CONFLICT。执行身份、step、ticket、executionId 必须由后续可信服务先验证；仅知道 taskId/revision 不构成回报授权。

批次仓库本身不更新 `ce_tasks`。服务通过任务仓库在同一事务维护指针，并在新轮次发起时清除 approvedRevision。读取最近成功候选只用于保留和展示，不自动恢复发布确认。

## 身份、容量与迁移

同轮精确身份是 workflowId + groupKey + proposalKey + episodeKey。proposalKey、确定 episodeKey 及更新关联必须由可信来源加载服务计算；自然语言建议按来源区分，不能模糊去重。仓库只保证给定身份的不可覆盖和幂等性，不代替来源权限/摘要核对，也不自动认定复发。

workflowId 和 taskId 使用 `[A-Za-z0-9][A-Za-z0-9_-]*`，分别最多 190 / 64 字符，与任务 Git ref 契约一致。190 是现有 Shared MySQL VARCHAR 转换的真实上限。v2 校验不会修改旧协议支持的 ID。

Shared 增量迁移 122–124 新增 items/revisions 和 healing outcome 可空关联列。SQLite 沿用 v117 的可空 lesson/task/step；MySQL/ZDAS 补 lesson 可空和已有 SQLite 专属迁移中的 suggestion/task/step 列，同时扩展历史 outcome 的 64 字符 workflow_id，使其支持当前 190 字符边界。旧记录不删除，不伪造 task 或 lesson。

OceanBase/MySQL 建表与兼容变更的完整参考 SQL 位于
[`workflow-repair-ob-ddl.sql`](./workflow-repair-ob-ddl.sql)。生产仍应由版本化迁移执行；手工执行前必须先核对
`workflow_healing_outcomes` 是否已经包含其中的扩展列和索引，避免重复 ALTER。

既有分析/证据表（`workflow_evolution_analysis_runs`、`workflow_run_evidence_events`）与建议扩展列由原部署流程提供，
不在 Shared 122–124 中。新建数据库只运行 Shared 迁移并不足以启动该来源 API；缺表显式失败，不能当作“没有问题”。

完整多列唯一键超过 ZDAS 的 767 字节索引上限，因此唯一约束使用 canonical JSON 的 SHA-256 identity_digest / request_key，查回时再比较原始身份。工作流索引仅含 workflowId，时间排序在查询时执行，避免复合索引超限。ZDAS 的审计索引用 ALTER TABLE ADD INDEX，避免其方言跳过独立 CREATE INDEX。

选择/人工处理请求最多 64 KiB、100 项；冻结输入最多 1 MiB，候选 manifest 512 KiB，检查报告 256 KiB；均使用 UTF-8 字节检查和 MEDIUMTEXT（SQLite 渲染成 TEXT）。列表读取最多 4 MiB，完整任务详情最多 8 MiB，超限明确返回 413 + limit/actualBytes，不静默丢弃历史；超大历史分页仍待实现。Pack 不入库。模型输入和 Pack 容量的执行方检查由 AIS 集成负责。

## 本地验证与尚未验证项

正式的 `vitest.repair.config.ts` 使用 Node 环境和系统临时目录缓存，避免依赖浏览器测试组件。`tsconfig.repair.json` 对控制面、来源、权限与存储做 Shared 源码映射类型检查，不替代整个 Workflow 的 check。

在 Workflow 包目录，用 Node 24 执行：

```sh
vitest run --config vitest.repair.config.ts --configLoader runner
tsc -p tsconfig.repair.json --noEmit
vitest run --config vitest.repair-web.config.ts --configLoader runner
```

公共 Host 包还有 `vitest.repair-runtime.config.ts`，用实际旧来源仓库、新服务和 HTTP 路由检查 39 项读取、
冻结派发、任务查询和取消释放。浏览器验收使用真实 React 页面加明确标注的合成 HTTP 响应；这与真实 AIS 联调不同。

测试通过 Node 24 内建 `node:sqlite` 的真实 SQLite 引擎执行现有 SqliteDatabase 的 query/exec/transaction，覆盖旧 DDL 升级、历史保留、审计/任务步骤失败回滚、并发建批、冻结输入、取消、失败保留候选、回报和派发重试，以及本机 HTTP 读写鉴权和完整反馈/diff 路径。由于本机共享 better-sqlite3 为 Node 22 ABI，此检查点没有验证该原生驱动在 Node 24 下运行。

MySQL/ZDAS 仅有 DDL 渲染契约检查；真实目标数据库迁移、事务隔离、并发建批和恢复仍需环境验收。服务对共用同一 IDatabase 的 SQLite 调用排队，防止 BEGIN 重叠；跨连接和跨进程的互斥仍依赖数据库行锁及调用方的忙重试，不能用单连接并发测试代替目标环境证明。

39/100 项用例为合成契约数据，尚未拿到方案要求的真实脱敏 39 条 fixture；真实容量验收仍未完成。完整 Workflow check 在当前复用依赖下缺少已构建的 `@avernet` 包而失败，已单独通过本检查点源码类型检查。

后续仍须完成：执行票据/真实 AIS 提供方接入、dispatching 中断恢复、超大历史分页、完整人工复发/效果验证，以及审核确认和受控发布。生产登录、目标数据库和实际部署须分别验收。
