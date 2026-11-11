# 以 BCS 为核心的 Bot 发现与好友连接设计

日期：2026-11-09
状态：设计及两份分模块实施计划已按审阅意见修订，尚未实施。
范围：`apps/bcs`、`apps/frontend-nextgen`、相关 API 契约，以及仅部署 BCS 时的前端部署配置。

## 问题与目标

前端「发现 → 公开 Bot」目前依赖 Backend 的 Bot Catalog 加载列表、智能搜索结果和好友申请弹窗画像，但 Bot 可见性与好友关系的权威数据在 BCS。仅部署 Frontend + BCS 而不部署 Backend/Gateway 时，无法完成「发现 → 申请 → 审批 → 建立好友关系」。目标是仅凭 BCS + Frontend 完成整个好友关系流程，并将 Backend 提供的丰富展示信息和服务 Bot 发布状态过滤保留为可选增强。独立的前端静态资源服务和已选定的浏览器 OAuth 身份提供方仍是部署前提；“仅部署 BCS”是指 BCS 是唯一的 Avernet 业务 API 服务，不表示 BCS 负责托管前端构建产物或充当 OAuth 身份提供方。

**验收边界：**两名已登录用户可以选择 Human 或自己管理的 Bot 角色，发现对当前角色可见的 Bot，提交申请并查看已发申请及待处理状态；目标 Bot 的管理者切换到该 Bot 角色后，可以查看收到的申请、通过或拒绝申请；双方最终可以看到准确的好友关系。上述过程不向 Backend、Gateway 或 BCSFuse 发送必需请求。工单通知、Engine 聊天和 Backend 创建的私聊会话不属于此好友关系闭环。Human 自动建立好友关系后，仅在已配置 Backend 时创建 Backend 私聊并跳转；否则原地确认好友关系已建立。

## 方案对比与选择

1. **采用：收敛到已有的 v1 `/bots/{bot_id}/candidates`。** 将旧好友关系读取切换到 Connect/edge，在分页前补齐 Human 视角的发现语义；`/eligible-candidates` 保留为标记待废弃的兼容别名。智能搜索和名称回退复用现有内部 v1 `candidates/search` 路由及共享搜索 Core；申请等其余流程复用现有 v1 `friend-connections` 路由。最大程度复用 BCS 的存储、授权、Service API 和 HTTP 交付能力。
2. 基于同一套 Core 另增一个公开目录接口：容易提供更窄的返回结构，但会与现有候选列表契约重复。只有在方案 1 因具体安全或兼容性约束无法成立时，才另行评审。
3. 前端直接调用未版本化的 `/bots/search`：不采用。正式页面会依赖旧 HTTP 返回格式，而且面向浏览器的 v1 契约中未明确约束其查看身份的授权语义。

无需重新实现搜索或新增好友状态存储。但 Human 可见性、已批准的好友边和所选角色授权均属于业务语义，不能靠前端过滤或 HTTP 适配器自行决定；因此需要同步调整应用层 Service API 及版本化 HTTP 契约。

## BCS 契约与应用层行为

### 普通列表：复用公开 v1 候选接口

`GET /openapi/v1/collaboration/bots/{bot_id}/candidates?purpose=discovery&name=<optional>&offset=<n>&limit=<1..100>` 是正式列表入口。`bot_id` 是当前用户自己的 `human_<user-id>` Actor，或当前用户**此刻仍有管理权**的物理 Bot。继续通过 `BotServiceImpl::authorize_candidate_perspective`，根据已验证的 Human 身份和实时 Bot 管理关系授权；不能信任前端传来的用户 ID 或历史 `created_by`。列表请求前，已有 `bots/mine` 应能完成 Human self Actor 的创建；当前前端加载角色时已经调用该接口。

`purpose=discovery` 时，必须在计算总数和执行 `offset/limit` **之前**筛选目标：

- Human 视角：使用 `user_visibility IN (public, protected)`；目标面向 Bot 的 `visibility` 不限制本次查询。
- Bot 视角：使用 `visibility IN (public, protected)`；目标的 `user_visibility` 不限制本次查询。
- 环境隔离、只返回物理 Bot、排除自身、稳定排序、名称匹配，以及现有状态和可达性行为保持不变；如需改变，另行修改契约。出现在发现列表中不代表一定可以添加好友：写入接口仍须重新检查实时状态、部门范围和好友策略。

`purpose=collaboration` 时，保留 Bot 视角的现有 `public OR friend` 语义；Human 视角的公开分支应使用 `user_visibility`，好友分支使用同一套已批准的 edge 关系。通过测试锁定两类视角及现有调用方行为，不能在修改 Human 发现能力时意外改变 Bot 协作行为。

Human 和 Bot 视角的 `is_friend` 及依赖好友关系的筛选，统一以 Connect/edge 中已批准的关系为准。正式列表、兼容别名和搜索共用应用层严格好友集合读取：使用已有的 `ConnectService::list_friends_paginated`，指定 `target_type=Bot`、每批最多 100 条，完整收集 approved default-profile edge 对应的物理 Bot 好友。不能直接复用旧 `list_friends`：它的仓储接口没有错误通道，SQL 失败可能被包装成成功的空集，而且会逐目标读取默认权限配置。这样旧存储中独有的好友记录不再是该 v1 响应的权威来源；通过兼容性测试评估旧记录的影响，不得悄悄混合两套权威。内存和 SQL 存储实现均需控制查询及分页成本。

严格好友读取必须使用真实 Connect → edge store 链路验证：任意页的 edge/profile 联合查询或行解析失败，整个列表／搜索返回错误，不能使用部分好友集，也不能转名称回退。每页沿用现有集合 JOIN 查询，不逐好友调用 `get_default_profile_id`；静态 F 个 Bot 好友需要 `max(1, ceil(F/100))` 次好友页查询。首批 total 固定本次扫描预算；后续 total 变化、页长与预期不符或出现重复身份时返回可重试错误，不追着增长的 total 无限扫描，也不承诺跨页事务快照。每个业务请求独立读取一次完整集合，搜索的语义／名称分支复用它，不加跨请求好友缓存。完整集合仍占 O(F) 内存，分页 JOIN/COUNT 的扫描成本和前端补页带来的重复读取都必须计入访问成本证据。

候选存储不能继续用每个好友一条 `UNION ALL SELECT ?` 构造 CTE；该写法在本机 SQLite 的同构 SQL 复现中，501 条即失败。将好友集排序、去重后序列化为一个绑定的 JSON 数组，SQLite 用 `json_each`、MySQL 用 `JSON_TABLE` 展开为查询内的 `friend_uuids`，复用仓库已有的方言能力，不新增表或修改迁移。普通列表、搜索及各自 COUNT 使用同一集合和过滤条件，再统一排序／分页；不能按好友分块各自 LIMIT 后拼接。真实 SQLite 与 MySQL 用例覆盖 0、100、500、501、1,000 个好友，证明页长、总数、身份匹配和查询参数数量均正确。

`GET /openapi/v1/collaboration/bots/{bot_id}/eligible-candidates` 继续保留原请求及响应结构，但委托给更新后的 `candidates` 应用逻辑；在权威 OpenAPI 契约中标记 `deprecated: true`，并在 `api-contracts/README.md` 说明替代路径。同步更新 BCS 契约、运行时及 Gateway 发布的 OpenAPI 测试。本轮不删除路由或其现有测试。

### 智能搜索：复用已有内部 v1 能力

`GET /api/v1/collaboration/bots/{bot_id}/candidates/search?q=<nonempty>&purpose=discovery` 已定义于 `api-contracts/v1/internal.yaml`，属于版本化且需要 Human 身份认证的接口。本轮前端通过同源路径调用该接口，不额外新增 `/openapi/v1` 别名或第二套推荐服务。将 Frontend 记为它的只读消费者，并确保仅部署 BCS 和现有 Gateway 的两种配置均将此路径转发给 BCS；不得为了这一个查询而新增宽泛的内部接口公开转发规则。

保留现有返回结构及 `search_mode: semantic | name_fallback | empty_query`。配置 BCSFuse 时保留 BCS 现有语义候选排序；未配置 BCSFuse 或没有可用推荐结果时，由 BCS Core 执行已有的名称搜索回退，前端明确标注为名称匹配，不冒充语义推荐。空查询仍返回空结果且不发起搜索；首次打开页面的默认列表走普通 `candidates` 接口。

语义命中与名称回退都必须采用与普通列表相同的 v1 应用层角色授权，使用该视角的 `user_visibility` 或 `visibility`，并从已批准的 Connect/edge 关系取得 `is_friend`。当前共享候选搜索 Core 使用面向 Bot 的可见性与旧好友关系，名称回退还会经过另一条 registry 查询路径。应在共享 Core 的输入中明确视角和关系来源，并测试 v1 与旧调用方；HTTP handler 不选择好友存储，也不承担业务策略。

**召回与权威筛选是两个边界，不能只修改收到推荐后的过滤。** 当前 `WorkerRecommendCommand` / Fuse client 请求没有过滤范围，BCSFuse 默认排除 `availability=private`，而 availability 来自 Bot 的 `visibility`；这会在 BCS 收到结果之前丢掉 Human-only Bot。BCS 计划须同步扩展该 Core 契约、`bcs-fusion` 实现和 `bcs-fuse-client` 请求：新增类型化 `WorkerRecommendationScope`，legacy 调用明确使用 `LegacyDefault`（不发送 filters，保持原行为）；v1 Human 查询及两类视角的 collaboration 查询使用 `AllVisibilities`，出站发送 `filters.availability=[public,protected,private]`，避免提前排除 Human-only 或 private 好友；v1 Bot discovery 保留原召回范围。召回范围不是授权，返回项仍由 BCS 以环境、物理 Bot、自身排除、当前视角和 approved edge 重新筛选；runtime_state 等未声明变更的 provider 默认行为不变。

**搜索容量约定：** BCSFuse 现有推荐协议的 `topK` 上限为 20，本轮不扩大该协议、不新增补召回循环，也不承诺“全目录中可见结果的前 20 名”。语义模式是一次最多 20 条 provider 召回结果的授权子集，可以不足 20 条；按原推荐顺序过滤，不先按控制面创建时间重新截断。无推荐、推荐服务失败或权威筛选后无可用命中时走名称回退；控制面或严格 registry 读取失败则返回错误，不触发成功降级。名称回退在存储分页前按视角过滤，最多 20 条。用真实出站 JSON 契约测试验证召回范围，不能仅以 fake provider 主动返回 Human-only Bot 证明兼容性。

两分支如需回填 `RegisteredBot`，使用现有 `registry.try_get_many(&ids).await?`；`get_by_ids` 的持久化实现逐条调用 `get` 且吞掉读取错误，不能视作严格批量读取。区分真实不存在的 ID 与读取失败，覆盖冷缓存的分块查询数和故障传播；标签增强失败仍可沿用现有空标签降级。

### 好友关系流程：不新增路由

- `POST /openapi/v1/collaboration/friend-connections/requests`：Human 或所选且可管理的 Bot 向目标 Bot 的规范 UUID 发起申请；BCS 再次核验操作角色的权限和目标策略。
- `GET .../requests?actor_type=human|bot&actor_id=<selected-id>&direction=sent|received&status=pending&page=...&page_size=...`：可刷新、按所选角色隔离的申请列表。现有 v1 应用服务核验 Human self Actor 或所选 Bot 的实时管理权限；目标 Bot 的管理者切换到该 Bot 角色，查看收到的申请。
- 继续复用通过、拒绝和可选的撤销接口。`GET /openapi/v1/collaboration/friend-connections` 返回所选角色已建立的好友关系。待审批不是已建立关系，刷新或重新连接后不得误显示为好友。
- Backend 工单通知和 friend-auth-sync 端口维持现有的可选装配方式。仅部署 BCS 时，它们的 Noop 实现不得阻断 BCS 的持久化、申请查询、管理者审批或已批准 edge 的读取。本轮不建设新的通知系统。

## 前端与可选 Backend 的行为

保留现有 `CollaborationSquareGateway` 抽象和公开 Bot 页面。传输协议适配集中在 `CollaborationSquareApiAdapter` 及其 Controller 中，不让页面组件同时处理两套响应格式。申请目标的规范身份只来自 BCS Bot UUID。只映射现有 v1 候选结果中适合展示的字段；不能将内部 `friend_ext`、AgentCard 或 descriptor skills 当作 Backend 已审核、已发布的 MCP/Skill 能力展示。未配置 Backend 时，申请弹窗画像使用已授权候选项中的名称、头像及摘要；已公开能力列表为空，Owner 展示名缺失时明确降级，不请求 Backend。BCS 申请接口会重新校验过期的候选项；申请被拒绝时显示实际失败原因，不报告成功。

好友申请列表统一由「通知中心」承载，发现页不提供「好友申请」入口或面板。管理者选择自己管理的 Bot 后，在通知中心查看所选角色发出和收到的申请、读取该 Bot 收到的待处理申请，并通过现有 BCS Controller 直接通过或拒绝；Human 角色可以查看自己发出的申请。刷新后展示最新状态，审批完成后刷新申请列表和卡片状态。好友审批说明的入口保持指向「通知中心」，不随部署形态改指发现页。不要求主动推送通知。

角色隔离同样约束写操作的异步回调。申请、通过、拒绝、撤销及后续建聊在发起时捕获登录用户、所选 actor 和操作代次；角色变化、离开页面或卸载使旧代次失效。每次 await 后及 catch/finally 中，只有仍属于当前代次的结果可以更新卡片／申请列表、提示成功或失败、清理当前 busy 状态、启动建聊或导航；A→B→A 也不能复活 A 的旧响应。AbortController 只用于取消客户端等待，不代表服务端写入已回滚；过期写入成功不撤销已持久化关系，切回该角色时通过新请求读回。

由前端组装层或 capabilities 层明确决定是否启用 Backend 增强，不在页面组件或 Service 内散落原始环境变量访问。未配置 Backend 时，该流程**不得发送任何** Backend Catalog、metadata、profile 或创建私聊请求。该开关也覆盖进入发现页及切换角色所依赖的身份加载：`identityService.loadIdentities()` 继续读取 BCS `bots/mine`，但跳过 `enrichBotMetadata()` 对 Backend `GET /openapi/v1/bots` 的补充请求，保留 BCS 返回的 Human 与受管 Bot 身份；不能靠捕获 404 实现“未配置”。BCS-only 的禁用边界还包括发现页外壳的自动副作用：通过同一 capability 让 `getShellVisibility().value.notificationBell=false`，不挂载通知铃铛及未读轮询；`ensurePersonalSpaceOnAppEntry()` 在身份补拉及个人空间初始化请求之前直接退出。继续运行 `workspaceService.initWorkspace()` 与 BCS 身份加载，不通过捕获 Backend 404 实现降级，也不把空间／通知接口转发给 BCS。全栈默认保留身份增强、铃铛轮询和个人空间初始化。配置 Backend 后：

- 分批调用 `POST /openapi/v1/bots/metadata/queries`，每次最多 100 组精确的 Bot/Owner 身份；按稳定的 BCS Bot 引用匹配响应，不按数组下标拼接。只有查到 Backend 记录，且 `bot_type=service`、`online_available !== true` 时才排除；查不到 Backend 记录的 BCS 原生 Bot 仍可见。普通列表、智能语义结果及智能名称回退共用这一步 metadata 关联与过滤；智能结果最多 20 条，只过滤不补页，保留原顺序、分数、短画像和 `search_mode`，过滤后为空也不能改写为 BCS 的名称回退。`online_available` 表示发布及绑定可用性，不代表实时健康或权限，不得覆盖 BCS 的可见性判断。可补充 Backend 独有的引擎、头像、Owner 展示名（`metadata/queries` 返回的 `owner_name` 回填卡片，未匹配记录保持「未公开」）和已公开能力画像。已配置的 metadata 服务出现网络或 5xx 错误时，保留 BCS 列表并提示增强不可用；认证或授权错误沿用现有鉴权／错误策略，不能静默当作未配置 Backend。
- BCS 返回 **Human** 好友关系已批准后，只有配置了 Backend 增强才调用 Backend 创建私聊并跳转。建聊失败不能回滚或误报已建立的好友关系；页面先展示“已成为好友”，再报告独立的跳转失败。仅部署 BCS 时，刚刚成为好友的操作及已有好友的卡片都不能请求 Backend 私聊接口。

发现页当前使用滚动加载，不展示精确总数或页码。启用 Backend 过滤后，前端连续读取 BCS 候选页并分批关联 metadata，直到凑齐当前展示页并确认是否还有下一条符合条件的 Bot；对页面暴露 `hasMore`，保留下一次使用的 BCS 原始游标（或预读一条后的缓存）。不能从未过滤的 BCS `total` 推导 `hasMore`，也不能以 `展示页码 × 页大小` 重置 BCS 原始游标。仅部署 BCS 时直接使用 BCS 分页。角色、搜索词或搜索模式改变时，中止并清空旧请求与游标；重试不能造成候选项重复或跳过。已有分享链接反查逻辑依赖 `total`，必须改为游标／`hasMore` 遍历，或采用同等权限校验下的精确目标查询，避免过滤后提前停止。智能搜索保留固定前 20 条，不采用无限加载。

分享反查还须处理状态语义，而不只是分页：BCS mapper 将 `online|hidden` 写入独立的 `PublicBot.bcsStatus`，不写入仍供 Backend Catalog 兼容调用方使用的生命周期 `status`，也不伪造 `ACTIVE`。旧分享链接守卫优先检查 `bcsStatus === online`；只有没有 BCS 状态的旧 Catalog 项才沿用 `status` 缺失或 `ACTIVE` 的判断。`hidden`、查无目标仍走原失效提示。此规则只替换旧链接的可用性检查，不追加普通候选列表的在线过滤，不把 BCS 状态解释为实时健康或申请授权；metadata/profile 增强不得覆盖该来源明确的状态。

## 仅部署 BCS 时的部署与安全边界

前端静态文件仍由前端静态服务提供。通过同源路由，将 `/openapi/v1/auth/**`、必要的 `/openapi/v1/collaboration/**` Bot／好友接口，以及现有内部候选搜索 GET 转发到唯一的 BCS 业务服务。因此需要独立的「仅 BCS」前端代理配置：当前 Nginx 模板要求 Gateway 等多个上游服务。不能为了让其他无关工作台功能表面可访问，就将它们需要的 Backend/Gateway 上游一概指向 BCS。本设计只保证「发现／加好友」流程，不扩展到其他工作台模块。

配置 BCS 已有的显式 `[api.auth]` 纯 OAuth 身份链和受信任浏览器来源，让 v1 受保护接口在没有 Gateway Principal 的情况下验证浏览器的 HttpOnly 会话。本地默认的旧 `[auth]` 身份链**不会**自动继承到 `[api.auth]`；仅 BCS 的端到端测试必须验证同源登录、`bots/mine`、列表／搜索和好友关系写入。保留 v1 Principal 验证、邀请码准入以及 Cookie 身份源的 CSRF 校验。不在浏览器中引入 Bot 凭证或特权 Principal 请求头；所有所选角色访问均由 BCS 应用服务授权。外部 OAuth 身份提供方和前端静态服务不属于 Backend/Gateway 业务服务。

## 错误处理、兼容性与验证

- BCS 列表或搜索失败时必须返回错误，不能将目录不可用或持久化写入失败伪装成成功的空列表。范围或管理权限变更后，下一次请求必须拒绝失效视角；前端重置并重新加载，而非展示仍可访问的旧结果。
- BCS 智能搜索的名称回退应明确返回 `name_fallback`，不能伪造推荐分数。缺少可选 Backend 不得使好友流程整体失败。受部门范围约束的目标可能出现在发现列表中，却在申请时被部门策略拒绝；前端应准确展示该错误。
- 同步更新权威的 BCS v1 公开／内部 OpenAPI 契约、`api-contracts/README.md`、应用／Core 契约以及现有符合性和兼容性测试。分别说明 v1 Service API 语义与 HTTP 交付契约的变更。标记 `eligible-candidates` 待废弃，但不虚构移除日期或迁移保障；本轮不删除接口。
- BCS 单元／路由／契约测试覆盖：Human 与 Bot 的交叉可见性（包括 `visibility=private,user_visibility=public` 及反向情况）、Owner／Manager 授权、已批准 edge 与仅有旧关系的区别、待处理／通过／拒绝流程、身份与状态的准确回读、有无 BCSFuse 时的空查询／语义结果／名称回退、内存及 SQL 存储的过滤与分页计数、待废弃别名与正式接口等价，以及 Fuse 出站 filters 的 Human／Bot／legacy 差异、冷缓存严格批量回填及其失败传播；还要覆盖真实 edge/profile 读取失败、后续好友页失败、分页漂移、千级好友集合的 SQL 容量，以及从应用入口统计好友读取／控制面／registry／投影各阶段的查询数。不得只用 Connect 错误 stub 或 registry 局部计数证明整条链路不存在吞错与 N+1。保持原有 Bot 视角行为。
- 前端测试覆盖：BCS DTO 映射、角色切换重置游标、过滤与缺失 metadata 时填满展示页、过期或中止的分页请求、画像降级、建立好友关系后不建聊，以及可选 Backend 建聊失败；挂载真实 `AppShell`、身份加载与发现页链路，验证 Human + 受管 Bot 冷启动、等待通知轮询周期及页面恢复可见后，metadata、通知和空间初始化请求均为零；用 deferred Promise 验证切换角色、A→B→A 和卸载后的写操作成功／失败回调均不污染当前状态或触发过期建聊导航；普通／语义／名称回退三条路径的 metadata 过滤一致，旧链接用 BCS `online|hidden` DTO 验证而非只用 `ACTIVE` fixture。不得将未获发布批准的 BCS descriptor 误展示为“已公开能力”。
- 使用真实 BCS v1 OAuth 会话及前端静态服务运行浏览器端到端测试，在 Backend、Gateway、BCSFuse 都不存在时走通「申请者 → Owner → 申请者」。实施后运行 BCS Cargo 和 API 契约测试、前端开源边界检查／类型检查／Lint／单测／构建，以及仅 BCS 的端到端检查。无法运行的门禁应明确说明，不能弱化 CI 覆盖基线。

## 实施计划文档边界

本设计对应以下**两份独立实施计划文档**，已同步修订；审阅确认后按各自计划实施，不合并为一份跨模块计划：

1. `apps/bcs/specs/2026-11-09-bcs-standalone-friend-discovery/plan.md`：负责 BCS 应用／Core／存储变更、v1 HTTP 与 API 契约、待废弃标记、身份配置指引、BCS 符合性测试和 BCS 侧验证。
2. `apps/frontend-nextgen/specs/2026-11-09-bcs-standalone-friend-discovery/plan.md`：负责前端 Gateway 抽象／Controller／界面改动、可选 Backend 装配、游标分页、仅 BCS 的前端部署配置、前端测试和跨模块浏览器端到端测试。

BCS 计划先明确并验证前端要消费的 v1 契约，前端计划再依该契约集成。两份计划各自列明任务、验证命令和验收证据。本设计文档仍作为跨模块共同遵循的契约与协调依据；不另写第三份合并实施计划，也不将 BCS 和前端工作项混入同一份计划。

本次仅修订设计及两份实施计划，不涉及业务代码或运行时验证。实施及相应测试须分别经过两份实施计划的审阅。
