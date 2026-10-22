# Workflow repair v2：问题列表与候选审核控制面

提供 Workflow 问题列表、人工无需处理/恢复、冻结修订、候选审核与反馈修订，以及独立的确认、精确候选部署和效果验证。Host 未注入 `RepairExecutionPort` 时生成返回 503；未注入 `RepairPublicationPort` 时部署能力关闭。AIS jobId、生成结果、审核确认、发布请求和验证结论分别持久化，不能互相当作成功证明。已有单条/分组建议应用协议保留。

## 审阅、部署与验证

### 手动优化的最简动线

选择建议 → 生成草稿 → 审阅改动 → 明确发布。手动草稿将建议当作生成目标，
不要求建议来源摘要、原建议基线、DB/Git 配置或预先整合的方案一致；不要求先消除建议间的目标冲突。
服务端读取当前生效标签的真实 Git Pack，把所选原始建议和现有证据交给生成过程。
公开 Host 的 AIS 适配器和内源 Host 均将 `purpose='draft'` 透传至 Git 基线读取；仅该用途跳过部署 JSON 与 Git 配置相等检查，标签、Pack、工作流、路径及原始字节身份仍须有效。未指定该用途的读取保留原校验。
证据不足在草稿中保留标记，不阻止生成。后台来源更新不清空选择、不增选其他方案成员。
权限、资源大小边界、请求幂等及运行中的任务所有权仍有效；草稿不会自动应用或发布。
自动定时筛选仍沿用其已有策略，发布端的精确候选及生效版本检查不因放宽手动草稿而删除。

手动候选显示“待确认发布”；点击“审阅并发布”立即打开确认弹窗，阅读改动后一次确认审阅与发布，不另设审阅审批步骤。读取发布信息失败只重试读取，不描述为已提交发布；确认后的请求仍沿用同一幂等编号。

生成新修订前读取生效部署包失败时，返回 `CAPABILITY_UNAVAILABLE`、
`reasonCode=BASELINE_READ_FAILED`、`stage=baseline`、`outcome=not_created`。
该标记仅适用于创建修订事务和派发之前的基线读取；不用于任意 500、网络断连或未知写入结果。
前端明确提示“部署包读取失败，草稿未创建”，保留选择与要求，不进入“核对任务—重试”的循环。
已创建请求先走原幂等查找，即使后续 Git 读取不可用也返回同一修订，不重复派发。
已有结构化基线错误（如配置不一致）保留原分类。服务端记录底层诊断，客户端不暴露 Git 错误或凭据。

1. Git Diff 仅展示真实变更行和少量上下文，提供前后行号与变更导航。读取失败、二进制和截断显式提示；截断不能确认。
2. `POST /:taskId/confirm` 冻结最新可审阅候选的 commit、完整 Pack tree、报告摘要、当前部署基线和有权访问的目标 Bot。确认不部署；未覆盖的业务验证需明确确认风险。
3. `POST /:taskId/deployment` 必须单独明确确认。先持久化发布身份，再交 Host 发布已审阅的完整 Pack。线上基线已变化时停止发布；不能自动覆盖或改用最新 saved 快照。
4. `POST /:taskId/deployment/:deploymentId/reconcile` 核对同一请求。未知提交结果不允许盲目新建部署；真实 Git tag、部署历史和激活记录一致后才标记已部署。实际修改项进入 awaiting_verification。
5. `GET /:taskId/deployment/:deploymentId/verification` 只读取精确 workflow_deploy_number 的现有业务运行，旧版本与版本不明的运行不纳入；无样本即等待观察，不自动触发业务运行。
6. 同路径 POST 逐项记录 verified、ineffective 或 inconclusive；必须提供判断依据，有效/无效结论需要已完成的新版本运行样本。运行成功本身不自动关闭问题。明确无效后可通过 `reopen-item` 重新加入待修复，新任务读取当前部署为新基线；原部署和结论保留，不隐式回滚。

所有门户读写沿用已验证登录和工作流权限；目标 Bot 权限另外校验。运行聚合仅对工作流全量可见用户开放。一次性发布执行授权仅走签名内部接口，绑定冻结目标和工作进程，数据库只保存凭证摘要。确认、部署和验证使用 requestId 幂等及状态版本 CAS。迁移 142 增加审核发布记录和私有领取记录。

## 页面使用

修复工作区按持久化状态展示选择建议、审核 Pack、部署候选和效果验证。审阅包含行级 Diff、检查报告、逐项结果和历史轮次；长内容仍保留常驻下一步操作栏。任务内往返保留未提交的范围与文字，切换页面不提交。生成中、只读或能力未接入时解释限制原因；部署与效果入口仅在对应条件满足后开放。
首次失败通过“重新生成 Pack”沿用本轮冻结输入，无须填写修改反馈或取消任务；需要改变选择或要求时进入范围修订。修复要求和修改反馈都是选填；选好建议即可提交新轮次，保留原基线和最近成功父候选。修订页提供固定底部操作栏和明确的禁用原因。后续失败保留最近成功候选，可明确返回审阅。新轮次从最近成功候选继续，其轮次编号不必紧邻当前尝试；没有成功候选则从原任务基线开始。
远端状态仍在运行或无法核对时，不开放重复生成。结果读取冲突返回结构化 `recoveryAction: retry-result` 和 `reasonCode`；浏览器不展示原始 API 响应，提供“核对原结果”，只检查原执行与身份绑定的 Git 结果，不创建轮次或派发。可信适配器确认没有结果后才允许再次显式生成；已有结果恢复到审核页，同时保留未提交调整。写请求响应丢失则先核对任务状态，再以相同内容和请求编号重试，不因网络失败自动创建另一请求。AIS 已成功但 Git 结果读取失败时，重新读取原执行结果，不再派发 Job；恢复记录保留此前读取错误。Git writer 使用准备时的分支头作为推送前置条件，旧执行不能覆盖更新后的任务分支。

工作流级“问题与优化”保持单一的聚合问题列表，不另外展示“修复收件箱”。先独立展示问题页，再以一次 `/api/workflow-repairs/candidates` 请求批量读取本页建议预览，不阻塞问题首屏。
批次修复的 item/revision/candidate 是内部控制面；后续从现有问题列表选择来源后显式进入候选稿流程，不再构造第二套问题投影。
主路径为：问题列表选择建议 → 进入修复工作区核对范围与要求 → 生成 Pack 草稿 → 审核 Pack。主列表分页单位始终是问题；工作区表格分页单位是独立建议，两者不混用。

建议接口返回完整可见范围的 `facets.nodes/failureModes`，在节点/类型筛选和分页之前计算；首屏即可选择后页的节点与问题类型，空筛选结果仍保留完整选项。工作区使用主题语义颜色，独立 Host 提供默认 token 映射，嵌入 Host 可通过现有 shadcn 变量覆盖品牌、表面和前景。
节点和问题类型在服务端过滤后计数、分页，翻页必须更换问题行。不再把全局建议分页结果拼回完整问题列表。
问题没有与建议一一对应的处理状态，因此主列表不混用建议状态统计或状态筛选。

问题卡片只保留影响等级、近24小时/7天发现次数和最近发生时间。发现次数按问题关联 flowId 去重并使用真实运行时间；默认按影响等级、最近发生排序，“近7天发现次数”按次数排序，均先排序再分页。列表不扫描无关运行、不读取节点执行记录，不计算或展示分析覆盖率、发生率和趋势。
建议预览每个问题最多展示 3 条，优先待处理且来源可用的建议，并展示准确总数及“查看全部”入口；不是截断后假装已加载全部。无建议的问题直接标明空状态。下述建议状态筛选和任务详情保留在详情区域：

每个问题及其建议组成独立卡片，以卡片间距、外边框和浅色问题标题区区分归属，建议不再套同等强度的框。首次读取预览时保留带加载提示的建议占位区，不把尚未返回误显示为“无建议”。
主列表问题页与建议预览使用 30 秒新鲜期、5 分钟未使用缓存回收；预览按工作流、历史范围、权限、可见问题签名及来源版本隔离。返回近期已看页直接复用，后台核对时保留内容，但暂禁写操作。来源变更、处置/任务变更、历史范围或权限变化会淘汰其他页旧预览；读取失败不继续展示旧预览，尤其不能在 403 后复用缓存绕过授权。

可按待处理、处理中、待验证、已关闭、暂不处理或全部筛选，
同组建议先按结构化修改动作做精确去重：目标、动作、路径和值完全一致时，即使自然语言描述不同，也合并为一个建议并累积来源；
同一节点或同一 prompt 的修改值不同，则保留为独立建议。主列表展示节点、问题类型、简短摘要和涉及运行数。
每个问题只保留一个“问题详情”入口，抽屉内切换原因、修复建议和历史证据。
抽屉按问题签名单独读取建议并分页，不受外层问题页码截断；预览已包含该问题全部建议时复用预览。建议卡片并列展示标题、修改摘要、来源运行数、状态和独立复选框，
不以单选下拉框切换建议；展开某条修改与依据后才读取完整证据。“全部建议与历史”保留独立历史项及跨问题建议查询入口。
单条建议详情分别展示完整修改目标值和判断依据；长 Prompt 可展开并复制。未取得对应版本基线时不伪造前后对比，最终仍审核 Pack diff。
原始来源标识与完整载荷合并在默认收起的技术详情中。
逐项保留选择与处置状态。有可继续的批次时显示任务入口，不能误建第二批。
首次进入默认不选择任何建议；用户逐项明确勾选。问题和建议翻页均不自动增选，状态/节点/类型筛选不丢弃已有选择。
支持查看跨问题、跨页已选，直接打开已选项复核、逐项移除和清空，上限 100 项；来源摘要变化清空旧选择并提示重新确认，历史范围变化重新初始化。
问题页操作栏展示已选建议数及工作区入口；工作区右侧常驻完整已选范围与修复要求，底部只保留一次生成动作。生成服务不可用、只读权限、任务占用等原因就地展示。结构化修改作用于同一节点相同或重叠路径且值/动作不同时，明确提示冲突并保留原来源供候选生成与审核，不自动取舍；确认、部署和效果结论均为独立明确操作。
这是保守的前端冲突提示，不代表实际基线 diff 或语义兼容性检查；服务端仍校验来源、状态和权限，最终审核真实 Pack。
生成成功后打开对应任务详情，不自动应用或部署。复制目标值使用带可访问名称和结果反馈的行内图标。
详情分“问题原因 / 修复建议 / 证据与历史”，只在进入证据区时加载单次完整分析；带运行/分析深链时直接打开证据区。
历史版本折叠预览仅使用该版本来源的分析文字，不复用跨版本聚合摘要或摘要元数据。历史聚合摘要明确标注摘要覆盖运行数与当前问题运行数；长摘要、原因展开和待确认内容折叠，避免与可执行建议混淆。
无聚合摘要时按来源分别展示最近原始诊断、运行及时间，标注“尚未汇总”；原始原因也为空时明确提示未记录。聚合失败提示不能替代已有诊断正文，也不能把不同运行的原因合成统一结论。
建议页和原因详情使用工作流隔离的查询缓存（30 秒有效、闲置 5 分钟回收）。来源摘要、聚合状态、权限、历史范围或处置刷新改变时使用新键；重开相同详情不反复清空。缓存仅用于展示，写入仍由服务端重读校验。
修复建议先在完整版本/快照范围内恢复整合方案，再按节点、问题类型、签名和状态在服务端筛选后分页，每页 20 条；统计和总数遵循节点/类型/签名范围，状态计数涵盖范围内所有状态。
建议分页仅出现在建议区域并明确标注，不改变主列表的问题页码；无匹配建议时明确展示空结果。历史分析每页 10 条，可访问后续页。
首屏问题请求使用 `view=summary&page=&pageSize=&nodeId=&failureMode=`，返回 groups、page 和全量节点/类型 facets；
只返回本页短摘要与运行引用，不传 proposal、诊断长文本或 evidence payload。打开问题后按签名读取完整聚合，不带分页的默认接口保持兼容。
聚合来源投影使用版本校验的只读缓存（最多 4 个工作流、每份 8 MiB、10 秒 TTL），先筛选分页再读取冻结摘要。
每次请求仍鉴权；聚合生成始终重读当前来源。冷请求仍需扫描并归组完整来源，并非持久化投影的 SQL 分页，实际冷加载耗时须在目标环境验收。
进入单条建议详情时只补齐该项证据，不再重建两遍来源；列表和抽屉共享该项缓存，来源摘要变化后失效。
问题列表后台刷新失败时隐藏缓存的问题行和其中的建议预览，显示明确错误与重试；等待刷新期间仍可保留上一页占位，不把失败当作空结果。问题摘要与修复控制面独立展示加载/错误，不因任一请求失败或等待而整页阻塞。问题摘要、候选列表和单项详情读取在 30 秒后中止并允许显式重试；不自动重试写请求。
工作流修复上下文读取失败时清除上次快照、选择与详情缓存，不回退为旧建议计数，也不开放旧应用入口；问题内建议读取失败局部显示错误和重试，不伪装成空结果。
401、403、读取超时与服务异常分别提示；403 只说明访问被拒绝，不据此断定用户固定缺少权限。
列表明确标注节点和问题类型，历史摘要以“摘要待更新”短标签标识；详情集中展示摘要状态及来源范围，不承诺未确认的自动重试。
生成候选稿时服务端仍重新读取并冻结所选项的完整证据，不以轻量列表代替任务输入。

默认宿主不再启动 AI 建议聚合 worker，也不等待聚合任务。列表只折叠同版本、同问题/节点且完整建议内容与约束相同的待处理项，保留原始成员 ID；不同目标和冲突取值不合并。
手动和自动修复均把选中的原始建议与完整证据交给 AIS 联合修复，自动选择仍跳过冲突项。重复卡片和单项之间的冲突同样检查；已有任务的冻结输入与执行记录不重写。

默认候选范围只读取最近 7 天仍出现的问题，避免早已不复现的历史原因持续占据待处理列表，也缩小大工作流的首屏扫描范围。
用户可显式勾选“包含历史未复现”查看完整历史；处理中和待验证的旧建议始终保留。时间范围属于本次选择快照的一部分，
生成候选稿或人工处置时沿用相同范围校验 inputDigest。系统不会仅因长期未复现就自动写成“暂不处理”，人工处置和审计状态保持不变。
修复来源读取会跳过单条无法兼容的旧分析记录并记录服务端告警，其他有效来源继续展示；数据库整体不可用仍会失败，不伪装成空列表。

“暂不处理”对应可恢复的 `no_action`，需要填写原因；未持久化的旧应用/验证历史只读，原控制入口保留在
“诊断证据与历史应用”区域。没有可执行建议的诊断也仍能从该区域查看。筛选和打开的任务按工作流保存在会话存储。

任务详情分开显示“最近尝试”和“最近成功候选”。支持按版本查看逐项结果、静态/Mock 检查、未覆盖项，
并比较任务基线或上一成功候选的文件差异。反馈下一版重新确认选择，但不改变原任务基线。
页面分别展示优化任务与当前版本问题。任务入口按实际阶段显示数量和状态徽标，每条任务标明修订、建议条数与明确的查看/审阅按钮；原始建议或警告文字作为任务说明，不充当任务标题。
当前版本无问题记录时只显示一个紧凑空态，收起无效排序筛选和分页；有筛选条件但无结果时保留筛选入口。未选择建议时不显示页面底部生成栏，历史版本浏览与全部建议查找入口保留。
“本版处理范围”保留原始建议与证据，并折叠展示所选版本的实际模型核心输入和可选材料索引。
新版 AIS 在现有结果的 `draft.modelInputs` 中按调用顺序返回 `{phase?, itemId, coreInput, materials}`：
可选 `phase` 仅允许 `planning`（全局规划）或 `generation`（分组生成），省略时按旧版生成记录处理。全局规划可与后续生成记录使用同一所选建议 ID；每份结果最多一条全局规划记录，生成记录按建议 ID 唯一，省略阶段和显式 `generation` 不能为同一建议重复记账。现有输入展示按实际调用顺序标注显式阶段，旧记录保留原展示。
`coreInput` 是交给模型 CLI 的原文，`materials` 是该次本地目录的 manifest 加 `root`，包含路径、用途、大小、哈希及来源归属，不传材料正文。
记录随现有 draft 一起持久化并参与 `checks.candidateDigest`；沿用现有结果大小限制，不新增表、接口或下载入口。
报告写入前校验可选 `modelInputs`：记录的建议 ID 必须属于冻结选择，按阶段和建议 ID 校验唯一性并限制最多一条全局规划记录，未知阶段无效，核心输入非空；材料索引须绑定同一任务与修订，校验文件元数据结构、非负整数字节数、SHA-256 格式、来源归属及唯一性，不读取 AIS 本地文件。
异常记录返回 `INVALID_INPUT`，不保存该候选；省略字段、空数组、仅覆盖部分所选项的模型调用，以及包含多个原始成员或无成员公共参考文件的索引仍合法。
没有记录的旧版、尚未回传或未调用模型的任务明确显示空态，不从冻结证据重建并冒充实际输入。
材料路径只对应模型调用时的 AIS 本地文件，不能作为浏览器下载地址；展示记录不代表发布、执行或效果验证。
取消释放建议占用且保留历史，不能等同于已终止远端 AIS 计算。AIS 明确拒绝派发时可重试同一冻结输入；网络超时或进程中断导致派发结果不确定时禁止盲目重试，需先核对 AIS Job，避免重复生成。

公共 Host 在 `CLAWWEB_AISTUDIO_TOKEN`、`CLAWWEB_REPAIR_AIS_SNAPSHOT_ID`、`CLAWWEB_REPAIR_CLAWFLOWS_GIT_DIR` 均配置后装配 AIS/Git 执行提供方；Git 目录须为预先配置了 `origin`、能读取部署 Tag 和修复分支的独立镜像。Token 由 MIST 注入，不写入仓库或日志。两个修复专属配置项都未提供时，允许查看、整理和处置；只提供部分配置、Snapshot ID 无效或 Git 镜像不可用时启动失败，避免静默禁用生成。
不会生成模拟候选。确认候选不部署；部署必须独立明确确认。原问题签名诊断只作为复现证据，未诊断不能当作未复现；人工验证不自动触发业务运行或灰度分配。
AIS Snapshot 必须运行 TaskGuard 的 Workflow Repair 入口，并具备 ClawFlows 推送权限；ClawWeb 只读镜像必须能从同一 ClawFlows origin 拉取。部署前用测试工作流核对 Tag/`workflow.pack.yaml`/部署历史 `spec_json` 一致，确认 Snapshot ID 和 Git 权限；不能把本地模拟通过等同于预发联调成功。后台每 15 秒轮询；AIS 最多等待 40 分钟，AIS 成功后 Git 结果最多等待 5 分钟。

## HTTP 与服务边界

### 发布适配器归属与配套部署

部署目标仅展示当前用户有访问权限、具有工作流编辑权限且引擎受支持的**生产 Bot**。
不随 ClawWeb 当前运行环境切换为预发目标；历史预发部署仍可查看，但不再允许新确认、派发或恢复执行。
候选状态由确认及部署记录共同决定：已确认、正在部署、部署失败、结果待核对、已部署不能再显示为“待审阅”。
页面只保留一个底部主操作，依次执行“审阅完成，选择生产 Bot → 确认候选 → 部署到生产 Bot → 确认部署”；确认候选不触发部署。
确认失败/明确部署失败后，可在同一候选上更换生产目标并重新确认，保留旧部署记录；结果不确定时禁止换目标或新建部署，先核对原请求。
版本号与真实状态是主要信息，完整 SHA/任务编号收起到版本技术信息；只有 Host 提供安全仓库 URL 时才显示 Git 提交与比较链接，不用 repoId 猜地址。

`createWorkflowRepairRuntime` 的 `execution`（AIS 生成）与 `publication`（目标 Bot 发布）是独立宿主能力。
公共 `public-bootstrap.ts` 只装配 AIS/Git 生成，未提供目标 Bot 认证与执行适配器时，发布能力明确关闭；不能据此将生成成功视为可部署。
内部预发/生产由 OCB Host 启动，不使用公共 bootstrap。OCB 的 `server/index.ts` 调用
`createInternalRepairPublication` 并向共享 runtime 注入 `publication.port`，同时安装签名 claim/report 路由和部署历史 `reviewedPublicationGuard`。
这部分依赖 OCB 配套分支 `codex/workflow-repair-release-flow` 和 ClawMind 配套发布入口；只部署 Avernet 不会自动获得内部发布能力。

公共 claim 服务的 `authorizeHistory` 只验证凭证与冻结部署身份，不是完整内容授权器。
内部 Host 的 `reviewedPublicationGuard` 必须在身份检查后，从冻结候选 Git 对象验证完整 Pack tree 与 specDigest，
重读 YAML 生成 `specJson`，并从同一 Pack manifest 派生 `facadeCommands`，覆盖工作进程上报内容。
不得直接把仅凭证/元数据检查作为内容授权器；无完整 guard 时，带 expectedActiveDeployNumber 的发布请求拒绝执行。
OCB 已实现以上内容重建；部署历史事务激活前还会再次调用该 guard，防止停止后旧授权继续激活。

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
| `GET /candidates?workflowId=&state=&page=&pageSize=&includeHistorical=&includeTasks=&nodeId=&failureMode=&signature=&versionScope=&snapshotKey=` | 默认读取最近 7 天，显式 `includeHistorical=true` 包含历史；可选节点/类型/签名先过滤再分页和计数；inputDigest 仍绑定整个时间范围的来源，支持跨页选择；不读取证据 payload，不物化写库。`includeTasks` 默认 true，false 时跳过修复版本历史读取并返回空 tasks；POST 查询语义相同 |
| `POST /candidates/query` | 只读查询，与 GET 使用相同 view 权限、过滤和快照；JSON 请求体包含 workflowId 和查询字段，批量 previewSignatures 最多 50 个，解压后请求体最多 256 KiB，避免长签名撑大 URL。返回每个问题的真实总数及最多 3 项预览，与全局建议页独立；先为基础响应和所有总数预留字节，再按剩余 4 MiB 响应预算添加完整项，不截断建议。预算不足时预览可少于 3 项或为空，仍可通过“查看全部”分页读取。旧 GET previewSignature 参数兼容保留，新客户端不再使用 |
| `GET /items/:itemId?workflowId=` | 展开单条时读取该项完整建议、来源与证据 payload |
| `POST /` | 校验当前来源摘要，原子写入处理项、修订、workflow_repair task 和 draft step，提交后派发，202 |
| `GET /:taskId` | 当前尝试、最近成功候选、修订列表和派发状态 |
| `GET /:taskId/revisions/:revision` | 指定冻结修订 |
| `POST /:taskId/revisions` | 原任务冻结基线 + 最近成功父候选 + 当前选择/反馈，新增修订，202 |
| `POST /items/:itemId/disposition` | no_action / restore；请求带工作流、内容版本、状态版本和 requestId |
| `POST /:taskId/cancel` | 显式取消未发布任务并释放占用，200 `{ok:true}` |
| `POST /:taskId/retry-dispatch` | 仅当前 drafting 且 created/dispatch_failed，同一次输入重派，202 `{ok:true}` |
| `POST /:taskId/retry-generation` | edit 权限；请求含 expectedRevision、requestId。先确认上一执行结束并回读原结果：已推送有效候选则恢复原轮次，Git 网络/校验未知时阻止新轮次；可信 adapter 明确确认没有原结果时才原子创建下一轮及步骤，沿用冻结范围、要求、反馈和基线。相同请求返回同一轮，不重复派发 |
| `POST /:taskId/retry-result` | edit 权限；请求含 expectedRevision。仅当前轮 GIT_RESULT_UNAVAILABLE 或 AIS_FAILED/AIS_STOPPED/AIS_TIMEOUT；核对已结束的原执行及身份绑定的 Git 结果。可信无结果只返回成功供用户后续显式修订；有效结果校验后恢复当前轮。不会创建轮次或 Job；已恢复的当前轮重复调用返回成功 |
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

真实 AIS/Git 适配与审核发布端口均由 Host 配置。新增的确认、受控发布、同一发布身份恢复和人工效果验证已可本地验收；预发仍需升级三个仓库对应包、目标 Bot 插件、OB 配置和增量迁移。源码、测试或 PR 不代表已在生产完成部署或取得实际业务改善。

## 宿主 Git 结果兼容

内部宿主兼容当前写入端的 `ClawMind workflow-repair-result/v1` 提交头，区分 Pack candidate commit 和承载结果元数据的 commit；同时保留旧 `ClawMind repair result v1` 读取。读取当前协议时校验任务、轮次、执行身份、候选父节点、Pack tree/digest、检查摘要及 manifest。`previousReportRef` 随读取请求传入，只有精确匹配上一成功轮次且绑定原父候选的旧结果，才可确认本轮尚未写结果。

分支不存在必须由成功的远端 `ls-remote` 明确确认，不能从 fetch 失败推断。首次任务确认没有分支返回 `RepairResultAbsentError`；已有成功候选但分支消失、远端不可达或结果校验不通过仍属于未知状态，继续阻止重复生成。

### 版本归属与历史读取

建议的来源版本从冻结分析引用或原运行对应的唯一部署/保存快照读取，独立于补丁 `baseSpecDigest`；文字建议同样能关联版本，明确的身份不符仍保留未确认状态。原补丁和摘要不改写，自动生成继续执行既有补丁基线校验。

历史区先按部署版本、保存测试和版本未确认分组，再展开问题；只有展开的问题读取建议。可选 `snapshotKey` 与 `versionScope=historical` 一起在计数、分页前过滤该来源版本。筛选、问题行与分页属于一个列表容器。

版本列表读取复用有界的短期投影，部署、运行或分析版本变化使其失效；聚合任务准备和候选写入仍读取权威来源。摘要响应仅保留来源身份、版本和短文本，不传完整分析 facts/inferences。


## 任务标题、来源与详情展示

候选列表的任务摘要和任务详情增加可选展示字段 `title`、`source`（`automatic` / `manual`），旧消费者可忽略，不新增持久化字段。服务从首次冻结修订生成标题和来源，后续反馈或版本切换不改任务标题。单条有效简短建议摘要优先；多条建议按涉及节点概括，无法定位时显示“工作流优化（N 条建议）”。基线重评提醒和长指令不直接作为标题，不调用模型取名。旧自动分批任务未记录总建议数时仅标注批次数，不把首批数量当成任务总数。

任务列表和详情把自动/手动来源与处理状态分开标注。详情依次提供“优化目标”“改动与发布”“发布后效果”；版本选择称“生成版本”，包含失败和无需变更的历史尝试。原始建议、证据和基线继续保留；实际模型核心输入及可选材料索引位于目标之后的折叠区，不用原始证据冒充实际输入。改动页先展示逐项目标处理结果和检查摘要，大批任务先展示前三项；文件差异、完整检查与 Git 记录仍可查看。生成修改与检查通过不表示业务效果已验证，发布仍需原有显式确认。

自动分批任务末批为 `no_change`、但完整校验链仍包含修改待审时，列表任务摘要显示 `review`，详情保留各批真实 phase；用户仍可明确取消该任务，释放积累问题进入下一轮。


待审阅和其他需跟进任务直接全部展示，任务入口统一为“查看任务”。详情任务操作区直接提供“重新优化”“取消本次任务”“发布此版本”；发布确认弹窗只负责最终发布确认。取消仍需明确确认，已发布任务不可取消。

完整自动分批链校验通过后，允许同一任务接收人工反馈修订。新版本复用最后完整候选作为父提交和原冻结基线，保留自动来源标记、原建议与历史版本；使用既有人工修订请求，不增加执行协议字段。未完成、失败或校验不完整的自动链不可由人工修订绕过。反馈页通过已有历史分页读取完整原建议，单次 100 项上限保持不变，超出时要求用户明确调整范围，不自动删选。

人工反馈返回 no_change 时，仅在候选 commit/Pack digest、父提交、基线和完整建议范围均与原成功候选一致且检查通过时，保留该候选供审阅；原记录及待处理占用保留。不同范围、失败或未完整校验的结果不会被当作这种保持不变的反馈。

### 大证据的冻结与按需读取

原始来源与核心包使用不同预算。请求仍限制为 64 KiB / 100 项，核心
`workflow-repair/v2` 输入仍限制为 1 MiB。完整来源及单次冻结材料另有
`16 MiB - 4 KiB` 上限，适配现有 MEDIUMTEXT 记录存储；不是无界放大核心包。

当完整输入超过 256 KiB 且证据投影能减小输入时，同一事务将完整建议、诊断、报告与事件证据写入
现有 `workflow_repair_release_records`（`record_kind=repair_material`），核心
输入仅保留完整 proposal/instruction、前三个来源样本、代表证据摘要及
`materials: {schemaVersion: 'repair-frozen-materials/v1', recordId, sha256, bytes, flowCount}`。
不等到 1 MiB 才处理；核心总上限仍为 1 MiB。纯目标内容若投影无收益且仍在上限内，则保持内联。
`context.evidenceSummary` 的 `sourceCount/sourceRefsSha256` 绑定完整来源数组
（规范 JSON 的 SHA-256），`flowCount/eventCount` 保留总数，运行和事件编号各仅
内联前三个样本。完整索引随原始材料保存；批次 `materials.flowCount` 是跨建议去重
的运行总数，页面不能将样本数量误作覆盖范围。必要目标及操作不裁剪，超限错误
返回各字段字节统计；日志每批只记录一条大小摘要，不输出材料正文。
凭据/PII 脱敏继续执行，生成材料不再沿用
聚合预览的文本/集合裁剪预算；完整材料超限会明确拒绝，不静默裁剪原文。

记录与修订创建、建议占用原子提交，写入失败全部回滚。材料按任务/来源版本
及 SHA-256 绑定，不会被实时来源覆盖。重试可以引用同一任务更早版本的不可变
材料；读取时核对归属、字节数、哈希及其核心投影，缺失或损坏即拒绝派发。

`GET /api/workflow-repairs/:taskId/revisions/:revision/materials/:itemId` 使用任务
所属工作流的 view 授权，只返回该修订明确选择的冻结原始项。页面展开
“原始建议与证据”才读取，不用实时材料填补读取失败。

派发前服务端校验冻结材料，但不再把完整材料装回 AIS 参数。核心仍包含完整
修改目标、用户约束、来源身份样本及完整索引摘要、代表诊断事实和关键错误摘录；完整报告作为参考。
下载失败不能抹去核心依据，也不能把无法核实的目标宣称为已解决。

支持 `repair-material-download/v1` 的执行端收到核心输入及逐条索引
`materialAccess: {schemaVersion, token, files: [{itemId, bytes, sha256}]}`。
Host 添加配置中的服务 baseUrl，以 `generate_candidate_materials_v1` 或
`integrate_candidate_materials_v1` 动作派发；旧消费者会拒绝未知动作，不静默忽略材料。
不支持下载的 Host 明确拒绝大材料任务，不回退为传完整报告。
生产者与 TaskGuard 下载端需一同更新来源索引语义；本次不迁移旧冻结任务。

一次执行的随机只读凭证仅以摘要保存到现有记录表（repair_material_access），
绑定 task/revision/executionId，最长两小时；执行结束、取消或被重试替换即失效。
凭证仅供 Python 下载层使用，不进入模型 Prompt、文件索引或日志。
`GET /api/workflow-repairs/material-download/:taskId/:revision/:itemId?offset=N`
使用 Authorization Bearer 凭证，只读本次执行的冻结选择；每块至多 64 KiB，返回
base64 字节、offset/nextOffset/totalBytes/sha256，不提供任意 flowId 或实时数据查询。
响应设置 no-store；页面查看材料仍使用原 view 授权接口。

TaskGuard 构建当前建议或聚合方案的 Prompt 前，仅下载其原始 itemIds 的材料。
校验各块身份、顺序、大小及最终 SHA-256，再使用既有本地材料机制脱敏、保存、
建立 README/manifest，并向 Prompt 说明路径、用途、可用状态及缺失材料的判断限制。
缓存命中仍验证完整哈希，不下载其他未处理建议；重定向、损坏、超限和越界均拒绝。
下载失败保留核心输入并标注 unavailable，必要原文缺失时要求报告 unresolved。
日志记录 start/complete/cache_hit/failed、执行/建议/方案身份、字节、哈希、耗时及
安全失败分类；不输出凭证、完整报告或上游原始错误。

需要 public Workflow、Host 和 TaskGuard 消费者配套上线。离线 HTTP 回放不代表
真实 AIS 网络、参数注入或业务执行已经验证；不新增存储服务、队列或业务工具。

Public ClawWeb 的 `createRepairAisExecution` 和 OCB Host 均装配上述下载协议。
Public bootstrap 传入既有 `getClawWebPublicBaseUrl()` 的配置来源；远程地址必须为
HTTPS origin，本地 HTTP 仅允许 loopback。无需把用户 Cookie 或凭据写入 URL。
创建、反馈修订及失败重试在事务提交前检查执行端支持；不支持时回滚新修订、
任务及材料写入，不留不可派发的新版本。派发时仍保留防御检查。


### Manual joint generation and failure diagnostics

Manual multi-selection is frozen as the existing `workflow-proposal-plan/v1` plan input.
Suggestions with overlapping node scopes are reviewed together; multi-node sources stay
atomic and connect their affected scopes. Unspecified/full-file scopes are reviewed
with the other selected goals. Unselected originals are never added. Independent groups
can generate separately, followed by the existing whole-candidate checks. Source and
integrated-goal outcomes remain mandatory; conflicts or incomplete coverage remain
unresolved. Retrying an older failed manual multi-selection adds this plan only to the
new revision, preserving the original revision, baseline, goals and materials.

The compatible optional `error.details` object carries safe parse reason/position,
item IDs, item index/count and plan ID. Model preparation, invocation, parsing, checks
and push errors identify their stage. The task page displays these details directly;
raw Prompt/output files remain optional diagnostic evidence, not a prerequisite for
understanding a failure. Historical errors without details remain readable.

After checkout, TaskGuard may persist a <=32 KiB failure receipt as commit metadata:
`refs/heads/repair-failure/<taskId>/<revision>-<executionId>`, with header
`ClawMind workflow-repair-failure/v1\n`. It contains the failed
`workflow-repair-result/v1` plus `parentCandidateCommit`; its sole parent and tree
must match the requested semantic parent candidate or baseline. It is create-only,
contains no candidate/files/Prompt/raw response, and never advances the task branch.
Readers validate every execution identity field and prefer an already valid checked
candidate result, including after an ambiguous push. Only confirmed result absence
permits reading the failure receipt. Reconciliation retains the receipt's error when
AIS reports failure. Older writers or Git transport failure retain the existing AIS
error fallback; no new callback, credentials, DB schema or business execution is added.
Failure refs follow the repository's diagnostic retention policy.
