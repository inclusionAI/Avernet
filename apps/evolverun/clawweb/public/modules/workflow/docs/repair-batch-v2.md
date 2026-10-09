# Workflow repair v2：问题列表与候选审核控制面

当前提供 Workflow 所属的问题列表、人工无需处理/恢复、冻结修订、任务/步骤持久化、候选审核与反馈修订服务和 HTTP 路由。Host 未注入 `RepairExecutionPort` 时，列表和人工处理仍可用，生成返回 503；`publication` 始终为 false。注入后，服务会保存 AIS jobId、轮询平台状态，并在 Job 成功后从 Git repair 分支读取候选结果；不接受 AIS 回调，也不使用 OSS 保存 Pack。审核后发布、灰度和效果验证仍未实现，不能据此认为发布链路已完成。既有单条/分组建议应用协议保留。

## 页面使用

工作流级“问题与优化”保持单一的聚合问题列表，不另外展示“修复收件箱”，也不在首屏请求 `/api/workflow-repairs/candidates`。
批次修复的 item/revision/candidate 是内部控制面；后续从现有问题列表选择来源后显式进入候选稿流程，不再构造第二套问题投影。
主路径为：问题列表 → 问题内比较和多选建议 → 确认本次修复范围 → 生成草稿 → 任务审核。主列表分页单位始终是问题；
节点和问题类型在服务端过滤后计数、分页，翻页必须更换问题行。不再把全局建议分页结果拼回完整问题列表。
问题没有与建议一一对应的处理状态，因此主列表不混用建议状态统计或状态筛选。
下述建议状态筛选、选择和任务详情契约保留为显式进入的修复控制面能力：

可按待处理、处理中、待验证、已关闭、暂不处理或全部筛选，
同组建议先按结构化修改动作做精确去重：目标、动作、路径和值完全一致时，即使自然语言描述不同，也合并为一个建议并累积来源；
同一节点或同一 prompt 的修改值不同，则保留为独立建议。主列表展示节点、问题类型、简短摘要和涉及运行数。
每个问题只保留一个“问题详情”入口，抽屉内切换原因、修复建议和历史证据。
抽屉按问题签名单独读取建议并分页，不受外层问题页码截断。建议卡片并列展示标题、修改摘要、来源运行数、状态和独立复选框，
不以单选下拉框切换建议；展开某条修改与依据后才读取完整证据。“全部建议与历史”保留独立历史项及跨问题建议查询入口。
单条建议详情分别展示完整修改目标值和判断依据；长 Prompt 可展开并复制。未取得对应版本基线时不伪造前后对比，最终仍审核 Pack diff。
原始来源标识与完整载荷合并在默认收起的技术详情中。
逐项保留选择与处置状态。有可继续的批次时显示任务入口，不能误建第二批。
首次进入默认不选择任何建议；用户逐项明确勾选。问题和建议翻页均不自动增选，状态/节点/类型筛选不丢弃已有选择。
支持查看跨问题、跨页已选，直接打开已选项复核、逐项移除和清空，上限 100 项；来源摘要变化清空旧选择并提示重新确认，历史范围变化重新初始化。
确认范围列出所有已选建议标题。结构化修改作用于同一节点相同或重叠路径且值/动作不同时，要求移除不采用的方案后才能生成。
这是保守的前端冲突提示，不代表实际基线 diff 或语义兼容性检查；服务端仍校验来源、状态和权限，最终审核真实 Pack。
生成成功后打开对应任务详情，不自动应用或部署。复制目标值使用带可访问名称和结果反馈的行内图标。
详情分“问题原因 / 修复建议 / 证据与历史”，只在进入证据区时加载单次完整分析；带运行/分析深链时直接打开证据区。
历史聚合摘要明确标注摘要覆盖运行数与当前问题运行数；长摘要、原因展开和待确认内容折叠，避免与可执行建议混淆。
修复建议按节点、问题类型、签名和状态在服务端筛选后分页，每页 20 条；统计和总数遵循节点/类型/签名范围，状态计数涵盖范围内所有状态。
建议分页仅出现在建议区域并明确标注，不改变主列表的问题页码；无匹配建议时明确展示空结果。历史分析每页 10 条，可访问后续页。
首屏问题请求使用 `view=summary&page=&pageSize=&nodeId=&failureMode=`，返回 groups、page 和全量节点/类型 facets；
只返回本页短摘要与运行引用，不传 proposal、诊断长文本或 evidence payload。打开问题后按签名读取完整聚合，不带分页的默认接口保持兼容。
聚合来源投影使用版本校验的只读缓存（最多 4 个工作流、每份 8 MiB、10 秒 TTL），先筛选分页再读取冻结摘要。
每次请求仍鉴权；聚合生成始终重读当前来源。冷请求仍需扫描并归组完整来源，并非持久化投影的 SQL 分页，实际冷加载耗时须在目标环境验收。
进入单条建议详情时只补齐该项证据，不再重建两遍来源；列表和抽屉共享该项缓存，来源摘要变化后失效。
问题摘要与修复控制面独立展示加载/错误，不因任一请求失败或等待而整页阻塞。问题摘要、候选列表和单项详情读取在 30 秒后中止并允许显式重试；不自动重试写请求。
工作流修复上下文读取失败时清除上次快照、选择与详情缓存，不回退为旧建议计数，也不开放旧应用入口；问题内建议读取失败局部显示错误和重试，不伪装成空结果。
401、403、读取超时与服务异常分别提示；403 只说明访问被拒绝，不据此断定用户固定缺少权限。
列表明确标注节点和问题类型，历史摘要以“摘要待更新”短标签标识；详情集中展示摘要状态及来源范围，不承诺未确认的自动重试。
生成候选稿时服务端仍重新读取并冻结所选项的完整证据，不以轻量列表代替任务输入。

默认候选范围只读取最近 30 天仍出现的问题，避免早已不复现的历史原因持续占据待处理列表，也缩小大工作流的首屏扫描范围。
用户可显式勾选“包含历史未复现”查看完整历史；处理中和待验证的旧建议始终保留。时间范围属于本次选择快照的一部分，
生成候选稿或人工处置时沿用相同范围校验 inputDigest。系统不会仅因长期未复现就自动写成“暂不处理”，人工处置和审计状态保持不变。
修复来源读取会跳过单条无法兼容的旧分析记录并记录服务端告警，其他有效来源继续展示；数据库整体不可用仍会失败，不伪装成空列表。

“暂不处理”对应可恢复的 `no_action`，需要填写原因；未持久化的旧应用/验证历史只读，原控制入口保留在
“诊断证据与历史应用”区域。没有可执行建议的诊断也仍能从该区域查看。筛选和打开的任务按工作流保存在会话存储。

任务详情分开显示“最近尝试”和“最近成功候选”。支持按版本查看逐项结果、静态/Mock 检查、未覆盖项，
并比较任务基线或上一成功候选的文件差异。反馈下一版重新确认选择，但不改变原任务基线。
取消释放建议占用且保留历史，不能等同于已终止远端 AIS 计算。派发未确认显示错误码和同版本重试入口。

公共/内部 Host 已装配真实来源读取和权限；执行提供方默认未注入。没有 AIS 时允许查看、整理和处置，
不会生成模拟候选，也不显示可点击的发布/灰度操作。复发归因、后续发布和新验证闭环不属于这次页面交付。

## HTTP 与服务边界

### 请求诊断

每次修复接口请求生成独立 `X-Repair-Request-Id`，403/500 响应正文也包含 `requestId`，便于浏览器与服务日志关联。
日志关键词为 `[workflow-repair] request diagnostic`，每次请求用单行 JSON 记录实例、路由模板、状态码、已验证 actorId、工作流及授权结果。
拒绝原因区分 `MISSING_IDENTITY`、`INSUFFICIENT_VIEW_SCOPE`、`INSUFFICIENT_EDIT_PERMISSION`；自定义 Host 鉴权器未提供细分原因时记录 `HOST_DENIED`。
`stagesMs` 分别记录 principal、permissions、stored_items、sources_summary/full、source_groups/suggestions/evidence 和 revisions 耗时。
这些阶段存在父子包含关系，不能直接求和；总耗时为 `elapsedMs`。并发请求上下文隔离，不记录请求头、Cookie、Token、Bot 列表或证据正文。
部署后按同一请求编号比对成功与失败身份以及阶段耗时，再决定修复身份链路、权限数据或读取瓶颈；诊断日志本身不改变授权规则。

工厂为 `createRepairWorkbenchService(db, sourcePort, executionPort?)` 和 `createRepairBatchesRouter({ service, authorize })`，挂载前缀 `/api/workflow-repairs`。路由只依赖服务接口，Host 注入已验证的登录与工作流权限。浏览器提供 itemId、摘要和反馈；proposal、证据、基线和 actor 由服务端决定。受限于部分 Bot 的运行查看权限不能读取整个工作流的修复批次。

| 请求 | 行为 |
| --- | --- |
| `GET /candidates?workflowId=&state=&page=&pageSize=&includeHistorical=&nodeId=&failureMode=&signature=` | 默认读取最近 30 天，显式 `includeHistorical=true` 包含历史；可选节点/类型/签名先过滤再分页和计数；inputDigest 仍绑定整个时间范围的来源，支持跨页选择；不读取证据 payload，不物化写库 |
| `GET /items/:itemId?workflowId=` | 展开单条时读取该项完整建议、来源与证据 payload |
| `POST /` | 校验当前来源摘要，原子写入处理项、修订、workflow_repair task 和 draft step，提交后派发，202 |
| `GET /:taskId` | 当前尝试、最近成功候选、修订列表和派发状态 |
| `GET /:taskId/revisions/:revision` | 指定冻结修订 |
| `POST /:taskId/revisions` | 原任务冻结基线 + 最近成功父候选 + 当前选择/反馈，新增修订，202 |
| `POST /items/:itemId/disposition` | no_action / restore；请求带工作流、内容版本、状态版本和 requestId |
| `POST /:taskId/cancel` | 显式取消未发布任务并释放占用，200 `{ok:true}` |
| `POST /:taskId/retry-dispatch` | 仅当前 drafting 且 created/dispatch_failed，同一次输入重派，202 `{ok:true}` |
| `GET /:taskId/revisions/:revision/diff?base=baseline\|parent` | 只用已保存的精确 commit，通过可信提供方读取差异 |

view/edit 在每条路由检查，任务请求先定位其真实 workflowId，再鉴权；不采用 body.actorId。来源变化返回 SOURCE_CHANGED/409；状态或幂等内容冲突 409；容量超限 413；缺失来源/任务/修订 404；未注入生成或 diff 能力返回 503。

`RepairSourcePort.load(tx, workflowId, mode)` 的 `summary` 模式用于列表，`full` 模式用于单条详情和所有写事务。
提供 `hydrate` 的来源适配器先读取 summary，再仅为所选项加载证据；提供 `readVersion` 的 Host 可复用版本校验的只读快照（最多 4 份、每份 8 MiB、10 秒 TTL）。
每次请求仍鉴权并检查来源/处置版本，写事务始终重读并清除缓存；缓存不是授权或提交依据。冷请求仍需要整理完整来源，尚非持久化投影上的 SQL 分页。
两种模式的 inputDigest 只绑定建议身份、来源引用和处置轮次，不绑定 evidence payload 展开形式，因此分页读取和提交校验一致。
新问题与历史任务并列展示；旧协议中已应用/验证/忽略的未迁移项作为只读历史，不自动变成新 pending 项。只有同建议的新证据时保留原状态，预览刷新 context；人工写入可刷新 item 的证据投影，但历史 revision.input_json 永不更新。

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

同轮精确身份是 workflowId + groupKey + proposalKey + episodeKey。结构化 proposalKey 只由规范化 operations 计算，不包含 summary/instruction 等表述文本；
因此同动作不同说法精确合并，不同参数值仍是不同 proposalKey。没有结构化 operations 的纯文本建议继续按来源区分，不能模糊去重。
读取已持久化项时还会用同一结构化动作兼容旧 proposalKey：保留原 itemId、处置状态、任务占用和冻结文案，只刷新当前来源与证据；
若旧算法曾产生多个同动作项，优先保留处理中、待验证或已人工处置的项，不把它们重新呈现为新的 pending 建议。
proposalKey、确定 episodeKey 及更新关联必须由可信来源加载服务计算；仓库只保证给定身份的不可覆盖和幂等性，不代替来源权限/摘要核对，也不自动认定复发。

workflowId 和 taskId 使用 `[A-Za-z0-9][A-Za-z0-9_-]*`，分别最多 190 / 64 字符，与任务 Git ref 契约一致。190 是现有 Shared MySQL VARCHAR 转换的真实上限。v2 校验不会修改旧协议支持的 ID。

Shared 增量迁移 136–138 新增 items/revisions、审计索引和 healing outcome 可空关联列。SQLite 沿用 v117 的可空 lesson/task/step；MySQL/ZDAS 补 lesson 可空和已有 SQLite 专属迁移中的 suggestion/task/step 列，同时扩展历史 outcome 的 64 字符 workflow_id，使其支持当前 190 字符边界。旧记录不删除，不伪造 task 或 lesson。

OceanBase/MySQL 建表与兼容变更的完整参考 SQL 位于
[`workflow-repair-ob-ddl.sql`](./workflow-repair-ob-ddl.sql)。生产仍应由版本化迁移执行；手工执行前必须先核对
`workflow_healing_outcomes` 是否已经包含其中的扩展列和索引，避免重复 ALTER。

既有分析/证据表（`workflow_evolution_analysis_runs`、`workflow_run_evidence_events`）与建议扩展列由原部署流程提供，
不在 Shared 122–124 中。新建数据库只运行 Shared 迁移并不足以启动该来源 API；缺表显式失败，不能当作“没有问题”。

完整多列唯一键超过 ZDAS 的 767 字节索引上限，因此唯一约束使用 canonical JSON 的 SHA-256 identity_digest / request_key，查回时再比较原始身份。工作流索引仅含 workflowId，时间排序在查询时执行，避免复合索引超限。ZDAS 的审计索引用 ALTER TABLE ADD INDEX，避免其方言跳过独立 CREATE INDEX。

选择/人工处理请求最多 64 KiB、100 项；冻结输入最多 1 MiB，候选 manifest 512 KiB，检查报告 256 KiB；均使用 UTF-8 字节检查和 MEDIUMTEXT（SQLite 渲染成 TEXT）。列表每页最多 200 项、响应最多 4 MiB；完整单项/任务详情最多 8 MiB，超限明确返回 413 + limit/actualBytes。Pack 不入库。模型输入和 Pack 容量的执行方检查由 AIS 集成负责。

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

测试通过项目既有的 `better-sqlite3` 驱动执行真实 SQLite，并在最低支持版本 Node 20 下覆盖现有 SqliteDatabase 的 query/exec/transaction、旧 DDL 升级、历史保留、审计/任务步骤失败回滚、并发建批、冻结输入、取消、失败保留候选、回报和派发重试，以及本机 HTTP 读写鉴权和完整反馈/diff 路径。

MySQL/ZDAS 仅有 DDL 渲染契约检查；真实目标数据库迁移、事务隔离、并发建批和恢复仍需环境验收。服务对共用同一 IDatabase 的 SQLite 调用排队，防止 BEGIN 重叠；跨连接和跨进程的互斥仍依赖数据库行锁及调用方的忙重试，不能用单连接并发测试代替目标环境证明。

39/100 项用例为合成契约数据，尚未拿到方案要求的真实脱敏 39 条 fixture；真实容量验收仍未完成。
本轮问题分页/修复动线调整在 Node 22 下通过 Workflow 与 ClawEvolve 的完整测试、check 和 build；
浏览器使用真实组件及明确标注的合成接口检查问题翻页、筛选空结果、建议多选、跨页复核和冲突取舍。
这不能代替预发部署后的真实权限、冷加载耗时或 AIS 联调验收。

后续仍须完成：执行票据/真实 AIS 提供方接入、dispatching 中断恢复、完整人工复发/效果验证，以及审核确认和受控发布。生产登录、目标数据库和实际部署须分别验收。
