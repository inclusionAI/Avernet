---
status: approved
date: 2026-10-22
owners: [bcs, frontend]
---

# 从协作定义副屏使用自己的 Bot 发起协作

## 1. 当前实施决策（2026-10-23 更新）

用户已授权采用“副屏预览 + frontend 主屏现有建群浮层”，并进一步要求通用建群入口和 Draft 初始内容可编辑。本轮保留既有 BCS 用户接口，不修改 Backend、Gateway、Runtime 或 frontend-nextgen。

- 通用宿主回调为 `onOpenCreateGroup(options?)`。`options` 仅支持可选 `draft`（schema 2 四字段固定引用）；不传 Draft 时打开普通建群浮层。事件只打开配置，不自动提交。
- `CreateGroupModal` 增加 `selectOriginator` 和可选 `initialDraft` 两个独立参数。原入口默认使用当前身份；副屏入口要求选择当前 Human 的自有 Bot，不切换聊天身份。通用 `useGroupCreation` 管理自有 Bot、候选与提交，不再提供 Draft 专用建群 hook 或 wrapper。
- Draft 只作为初始 YAML 来源：读取固定 revision 时核对 UTF-8 字节/hash，一次填入原有自由编辑器。带 Draft 时固定自定义协作并隐藏群类型、自由编辑/模板切换；载入后自动校验，成功直接展开角色绑定与流程图，失败停留编辑器并显示诊断。用户可通过“重新编辑”修改 YAML；编辑不更新 Draft，不创建新 revision。取消重开、身份刷新和表单校验不能用初始 Draft 覆盖用户修改。
- 沿用现有 YAML 编辑、校验、角色绑定、群名、目标、高级设置及 Loop 图。编辑后使旧校验/绑定失效，必须重新校验和绑定；自有 Bot 入口所有声明槽位显式绑定。
- 提交使用普通建群表单的参数。自定义协作提交前校验当前编辑缓冲区，传递该字符串原始字节；不重读 Draft 覆盖用户修改。已成功读取的原文与手动粘贴文本同样可以编辑使用，后续分享撤销不追溯撤回已经取得的内容。
- 不带 Draft 的自有 Bot 入口支持自由聊天、任务协作和自定义协作，与同一浮层的普通入口复用参数组装；仅用户身份认证通道不同。
- 候选按所选发起 Bot 的范围分页查询；切换发起方清空旧成员和角色绑定，搜索/翻页保留选择。好友及画像筛选仍待 Human 契约接入。
- 保留提交前用户复核、单次 POST、未知结果锁与当前 Human 导航。提交中不可编辑或关闭；关闭重开同一浮层保留编辑和提交状态。
- 不保留旧回调、Draft 专用建群路径或只读表单分支；尚未发布的调用方、测试和文档一起更新。未接入宿主仍提供只读预览，frontend-nextgen 及聊天控制见 roadmap。

### 本次验收

1. 传/不传 Draft 均可通过通用回调打开同一个浮层；事件本身不建群。
2. 带 Draft 时不显示群类型和模板切换；自动校验成功直接展开绑定/流程图，失败可编辑重试。原文只初始化一次；重新编辑、校验、取消重开保留内容，原 Draft 不变。
3. 编辑使旧校验失效；提交精确使用当前已校验文本，校验失败或用户变化时不 POST。
4. 不带 Draft 的通用入口继续支持三种群类型和模板；自有 Bot 的选择、成员/角色配置使用同一表单参数，正常入口无回归。
5. 重复点击、失败/未知状态及导航继续满足原约束；真实建群、私聊及线上验证仍遵循此前暂缓决定。

## 2. 依据与已核对事实

| 依据 | 本版使用方式 |
| --- | --- |
| [固定 Draft 与只读分享契约](../../api-contracts/collaboration-drafts/v1.yaml) | 继续使用 schema 2、固定 revision、readToken、原文字节/hash 核对；不修改原管理权限 |
| [Panel 组件与宿主约定](../../assets/panel/README.md) | 复用卡片、预览/源码子 Tab、逻辑 Loop 图、宿主 API base 和资源绑定 |
| [V1 发起方授权](../../crates/application/v1/bcs-app-group/src/authorization.rs) | `authorize_originator` 已允许 Human 使用自己拥有的 Bot；Bot 调用者不能冒用别的 Bot |
| [候选查询](../../crates/application/v1/bcs-app-bot/src/lib.rs) | `authorize_candidate_perspective` 校验用户对视角 Bot 的所有权；`purpose=collaboration` 决定候选范围 |
| [V1 建群编排](../../crates/application/v1/bcs-app-group/src/create.rs) | 校验发起方、参与者和绑定，创建初始会话并调用 BCS 的状态机运行服务 |
| [建群 HTTP DTO](../../crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs) | 接收原文 `content_yaml` 和独立 `participant_bindings`；没有通用客户端幂等 key |
| [初始会话输入](../../crates/services/bcs-group/src/application/management/create.rs) | 任务说明通过 `context` 进入既有 `{ query: ... }` 输入语义，不发明新的输入协议 |

遵守 [架构约束](../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../docs/arch/ci.enforce.md)、[领域文档规则](../../../../docs/agents/domain.md)、[BCS 规则](../../AGENTS.md)、[BCS 分层](../../CLAUDE.md)、[Group 应用边界](../../crates/application/v1/bcs-app-group/CONTEXT.md) 和 [Draft 应用边界](../../crates/application/bcs-app-collaboration-draft/CONTEXT.md)。本设计不改变已有跨服务身份信任机制，不以 Draft 分享凭据替代 Human 身份。

已知缺口必须纳入交付，不能由 UI 测试掩盖：

- 实施前 Draft 只有原生 `/collaboration/drafts/...` 路由。本版已补齐 V1 只读投影；部署仍需真实验证宿主转发，设置 base 本身不会发布路由。
- 现有用户建群入口没有为普通建群定义请求超时后的幂等重放协议；按钮防连点不能宣称服务端 exactly-once。
- 宿主已绑定 `bcsPanel` 是用户负责的接入前提。本版不重新检查或自动安装资源绑定，但必须验证实际用户认证、路由和群打开能力。

## 3. 范围与模块边界

### 3.1 本版范围

- 通用宿主事件打开既有建群配置；可选 schema 2 固定 Draft 作为初始内容。
- 展示当前操作用户，查询其自有 Bot，选择发起 Bot。
- 按发起 Bot 查询、搜索和分页展示可协作 Bot，并绑定角色。
- 编辑群名、任务说明与本次建群 YAML；带 Draft 固定自定义协作，不显示模板切换；无 Draft 保留模板和三种群类型，不修改共享 Draft。
- 用户确认后调用现有 BCS 建群入口；自定义协作沿用建群即启动语义，显示结果并提供进入新群的入口。
- 补齐本链路需要的 BCS Draft V1 只读入口和 Panel 响应适配；验证真实登录用户权限和部署路由。
- 补齐原生 BCS 宿主的 Human 浏览器入口，复用现有认证链和 V1 应用服务，保持 Gateway 认证独立。

### 3.2 不在本版范围

聊天操作、助手代用户调用 CLI、用户凭据交给模型、跨 Bot 委托、Draft 用户写权、修改已有群、模板市场、自动加好友、自动邀请尚无权限的 Bot、通用任务执行入口、自动重试建群、跨设备恢复未提交配置。

历史 schema 1 保持只读预览；不自动上传、转换或发起协作。需要发起时由助手交付 schema 2 固定版本。

### 3.3 修改范围

| 模块 | 职责 |
| --- | --- |
| `apps/bcs/assets/panel/` | 固定版本预览、源码复制和显式宿主回调；不查询用户或提交建群 |
| `apps/frontend/` | 共用 UMD 宿主与既有建群浮层；用户上下文、候选查询、配置状态、原文复核、请求组装和结果导航 |
| BCS HTTP adapter / application | 补齐必要的 Draft 读取入口，复用已存在的用户身份解析、所有权和建群授权；不在 UI 里实现最终权限裁决 |
| BCS core/store | 沿用现有 Draft、Bot 关系、Group 和运行数据；没有经验证必要的缺口时不新增持久化结构 |
| frontend-nextgen | 本轮不修改；对应回调、认证、导航及验收列入 roadmap |

本版实现限定于 `apps/bcs/` 和 `apps/frontend/`。不修改 Backend、Gateway、Engine Runtime、frontend-nextgen 或 Singlebox/profile。文中复用的 `CollaborationRuntimeService` 是 BCS 已有用例，不意味着修改独立 Engine Runtime。若真实宿主缺少认证或转发能力，应记录为接入阻塞，独立评审范围，不能临时通过请求体 `userId`、Bot token 或新增跨服务委托绕过。

## 4. 用户流程

### 4.1 入口与布局

在现有预览头部提供“用我的 Bot 发起协作”。用户点击后，Panel 调用宿主注入的回调，frontend 通过 Portal 打开已有 `CreateGroupModal`，副屏保持预览。取消仅关闭浮层，回到原聊天。

通用回调为 `onOpenCreateGroup(options?)`，frontend 共用 UMD 宿主向副屏组件注入该函数。无参数或 `{}` 打开普通建群配置；`{ draft: { schemaVersion: 2, draftId, revision, readToken } }` 载入固定版初始 YAML。宿主检查参数形状与严格四字段引用；消息不能指定回调、用户身份或导航地址。BCS Preview 仅为 schema 2 显示此入口，schema 1 保持只读。未接入回调的宿主提示暂不支持建群，保留预览/源码/复制。

直接复用主屏浮层，通过 `selectOriginator` 开启自有 Bot 选择，通过可选 `initialDraft` 初始化自由编辑器。两个参数独立；普通入口不传参数时保留当前身份与原行为。带 Draft 固定自定义协作并隐藏群类型及模板切换，初次载入自动校验并展开角色绑定和流程图；“重新编辑”保留 YAML 编辑能力。不带 Draft 时保留原有群类型和模板选择。复用群名、协作目标、高级设置、角色页签、Bot 列表和逻辑 Loop 图；图与角色绑定保持既有联动。Bot 名称搜索和分页使用 Human 候选契约；好友筛选和画像推荐尚未接入，见 roadmap。

### 4.2 发起 Bot

- 使用原生 select/options 选择发起 Bot，不显示自有 Bot 搜索框或平铺按钮。常见少量 Bot 只显示下拉框；超过现有每页 20 条时才显示翻页，翻页保留当前已选项。同名选项补充 ID 区分。
- 使用可信当前用户查询“我的 Bot”。仅一个符合条件的 Bot 时预选，多个时要求选择；不依据聊天对象、助手 ID 或全局当前 Bot 自动代选。
- 没有可用 Bot 时展示“需要先拥有一个可用于协作的 Bot”，保留取消入口，不自动创建或注册 Bot。
- 查询身份由登录认证决定，不接受消息内传入的 `owner/userId` 作为过滤或授权凭据。
- 选择只作用于当前配置，不写全局 `activeIdentity`，不切换聊天对象。
- 切换发起 Bot 后重新查询候选。清空旧角色绑定，要求重新选择；同一发起方的翻页和搜索不清空绑定。

### 4.3 角色绑定与候选

- 角色名称、描述和必填标识来自当前编辑 YAML 的校验结果。逻辑 Loop 仅显示一组角色，不按展开轮次重复绑定。
- 发起 Bot 本身可以被选择；其他 Bot 以服务端候选契约为准。搜索和分页在服务端执行，不拉取全量目录做匹配。
- 首版每个角色选择一个 Bot。同一 Bot 可绑定多个角色，只要既有定义校验与建群服务允许；参与者集合按 Bot ID 去重。
- 首版所有已声明角色（包括可选角色）均须明确绑定，避免沿用原文中的旧 Bot 默认值。支持可选角色留空需先明确“显式清空”契约，后续单独推进。
- 显示 Bot 名称、必要的归属/标识和服务返回的状态，用于区分同名候选；选择保存真实 ID，不按名称提交。
- 离线与不可授权分别展示；是否允许开始由既有服务语义决定，不能将“列表可见”显示成“保证能运行”。
- 不因 Draft 中已有实际 Bot ID、绑定建议或相同名称自动加入参与者。既有定义若要求外部绑定无法表达的固定身份，应明确阻止提交并提示用户改为角色槽位后重新校验，不偷偷改写原文或替换身份。
- 首版不做模型推荐、不把用户候选目录发送给协作助手，不新增好友申请或授权弹窗。

### 4.4 确认与完成

带 Draft 的浮层显示初始版本与“可自由编辑；修改仅用于本次建群”，并展示发起 Bot、角色绑定、群名、协作目标和高级设置。自定义协作按钮为“建群并开始协作”，紧邻明确提示“确认后将建群并立即开始协作”；自由聊天和任务协作使用普通“创建协作群”按钮。群名以版本标题预填，无标题时使用“自定义协作”，用户清空后按去重 Bot 名称生成（超过五个追加“等”）。自定义协作的目标必填；目标作为独立 `context` 提交。

高级设置与 frontend 建群采用相同约束：Webhook 只支持无用户名/密码、查询参数和片段的 HTTP(S) URL；提交既有 metadata-only 群事件订阅。开场白支持群、会话及 Run 模板变量，限制 UTF-8 64 KiB，保留用户原始字符串。未填写时省略对应字段；两项均走现有 Group 创建契约，不改 Draft 或扩展服务端 API。

提交成功且创建后的用户复核通过时，自动关闭建群浮层和副屏，以当前 Human 视角跳转到服务端返回的 `group_id` 和 `initial_session_id`；无需再次点击“进入协作群”。只有本次提交确认成功才触发跳转，重复点击、重开浮层、失败或未知结果不触发跳转。成功状态保留“进入协作群”链接作为回退。启动请求被接受不等于流程已完成，不能显示“任务完成”。导航由当前 frontend 宿主的既有群/会话路由执行，不让模型编造链接；真实新群进入仍须单独验收。

用户本人沿用既有 Human 建群的参与/可见规则，确保能进入初始会话并处理 Human 节点。协作助手不自动入群，不向原聊天额外发送消息；对新群的通知和执行交给 BCS 既有建群链路。

## 5. 状态、隔离与原文

### 5.1 本次配置

仅在当前副屏宿主持有的浮层内存中保存：可选初始 `draftId + revision + contentHash`、当前编辑 YAML、可信用户上下文、发起 Bot、角色绑定、群名、任务说明、查询状态和提交状态。

配置按“可信 BCS 部署 + 当前用户 + 副屏实例 + 建群操作”隔离；分享凭据不进入配置 key、URL 或日志。转发同一方案后，接收者重新选择自己的 Bot，不继承发送者的绑定。关闭重开浮层保留本次内存配置及提交状态；关闭副屏或刷新不承诺恢复配置，不写共享 Draft 或持久浏览器缓存。

用户切换/退出登录时取消旧查询、清空发起 Bot、成员/角色绑定、目标与高级设置；不以身份刷新覆盖当前编辑文本。宿主或初始引用变化时新建浮层实例，旧响应不能回写新实例。已发出的建群请求不能因前端取消而宣称被撤销，见第 7 节。

### 5.2 原文与使用权限

1. 传入 Draft 时只读取指定 revision，核对 ID、UTF-8 长度和 SHA-256，初始化一次编辑器并调用既有 YAML 校验流程，成功进入展开的角色绑定/流程预览，校验失败留在编辑器；不可用或完整性不符时禁用配置并允许重试。无 Draft 不发任何版本请求。
2. 初始化成功后，当前编辑缓冲区成为本次建群的内容来源。修改后（无 Draft 入口也包含换模板）清空旧校验和绑定，用户重新校验并选择角色。关闭重开、身份轮询或表单校验不重读 Draft 覆盖编辑。
3. 确认时在任何 await 之前快照普通表单参数。自定义协作将当前 YAML 送到已有 definitions/validate，再组装建群 POST；自由聊天/任务协作不做定义校验。复用同一个 `CreateGroupParams`，只在 API 层适配 Human DTO。
4. `content_yaml` 使用快照中的原始字符串，不 trim、不 parse/dump、不改换行、不从图重建、不经模型转写；绑定独立提交。此流程不写 Draft，不创建 revision。
5. 取得合法分享的原文后，用户以自己的建群权限使用本地文本副本。readToken 不授予建群权，不能进入当前文本校验或建群请求。之后撤销分享不追溯回收已取得的副本，也不会撤销已建群；分享不是持续执行许可证。
6. 新版本不会替换旧配置；明确打开另一引用会启动新浮层实例。相同引用（或同一个无 Draft 入口）关闭重开保留配置及成功/未知状态；原群继续使用创建时的定义和绑定。

## 6. 请求与权限契约

### 6.1 两类认证通道

| 操作 | API | 授权来源 |
| --- | --- | --- |
| 读取/校验固定 Draft | 原生 `/collaboration/drafts/{id}/revisions/{revision}` 及其 `/validate` | `X-BCS-Draft-Read-Token`，沿用单版读取权限 |
| 本版补齐的部署读取入口 | `/api/v1/collaboration/drafts/{id}/revisions/{revision}` 及其 `/validate` | 同一 Draft 读取裁决；不会把普通用户登录升级为 Draft 管理权 |
| 校验当前编辑文本 | 原生 `POST /collaboration/definitions/validate`，请求 `{definition_yaml}` | 既有只读校验契约；不需要 Draft 分享凭据 |
| 当前用户 | `GET /openapi/v1/collaboration/me` | 已验证 Human 的最小投影，不返回凭据 |
| 查询我的 Bot | `GET /openapi/v1/collaboration/bots/mine` | 宿主可信 Human 认证 |
| 查询可协作候选 | `GET /openapi/v1/collaboration/bots/{originator}/candidates?purpose=collaboration` | Human 认证 + 对视角 Bot 的所有权 |
| 创建并开始协作 | `POST /openapi/v1/collaboration/groups` | Human 认证 + 发起 Bot 所有权 + 参与者可协作检查 |
| 原生 BCS 宿主的用户操作 | `/collaboration/user/me`、`/bots/mine`、`/bots/{originator}/candidates`、`POST /groups` | 现有 BCS Human 浏览器认证链，复用上述 V1 handler 和应用裁决 |

Draft V1 入口属于 BCS 适配器增量，只开放本流程所需的 GET 和 validate POST，不增加管理路由。V1 使用已有标准响应 envelope，Panel 解包后执行与原生响应相同的固定版/原文校验；原生协议不变。应用服务复用现有 Draft 服务，不能复制一套鉴权或存储实现。

现场核对确认：本地宿主只转发 `/bcnproxy`，直接请求 `/openapi` 返回宿主 404；直连 BCS V1 缺少 Gateway 签名 Principal 返回 401。本版通过独立原生浏览器入口解决，不让 Gateway 验证失败后降级。原生入口拒绝 Bot、Actor、Gateway Principal 和 Draft 凭据，调用既有 BCS Human cookie 认证链（本地沿用已配置的 local Human），不从 Bot owner 或请求体构造用户。

Panel 的 Draft 预览继续使用已有 `apiBaseUrl/baseUrl`。主屏浮层由 frontend 自己选择可信请求入口：Draft 使用 `/bcnproxy/collaboration/drafts`，Human 操作使用 `/bcnproxy/collaboration/user`。成功链接为 `/bcn/chat/detail?id={groupId}&session={sessionId}&bot_uuid={humanActorId}`，未知结果链接为 `/bcn/chat/list?bot_uuid={humanActorId}`。不从消息读取 API base 或导航路径，不在认证失败后切换入口。frontend-nextgen 的 V1 路径与导航接入属于 roadmap，不再通过 Panel 默认值宣称已接通。

用户范围请求携带 `X-BCS-Expected-User`，仅与服务端已验证 Human 做相等比较，不提供身份。原生 POST 必须携带此非简单请求头，并拒绝 `Sec-Fetch-Site: cross-site`；V1 既有调用者未提供该头时保持原行为。身份不一致在 handler 前拒绝；客户端清空配置，重新确认用户。原生入口和身份投影均 no-store。

所有用户请求沿用宿主现有 cookie/可信认证链，由 BCS 服务端解析 Human。浮层不使用协作助手的 Bot 凭据，不依靠 `userId` 请求字段授予权限，不调用原生历史建群接口绕过 V1 所有权校验。登录信息缺失、凭据仅代表 Bot 或宿主认证不可用时，保留预览并禁用建群。

必须以真实请求验证默认入口、已配置入口、header 透传和 V1 envelope。未完成该验证不能把“已有 props”视为可用部署，也不修改 Gateway 配置来掩盖本版范围以外的问题。

### 6.2 建群参数映射

| 字段 | 来源 |
| --- | --- |
| `group_kind` | 固定 `normal` |
| `originator` | 用户明确选择、服务端验证归属的自有 Bot ID |
| `driver_bot_uuid` | 自定义协作使用发起 Bot；自由聊天/任务协作沿用原浮层选择的群主/Manager |
| `name` / `context` | 用户确认的群名 / 任务说明；`context` 沿用当前 `{query: ...}` 会话输入语义 |
| `participants` | 同一表单组装的成员 + 发起 Bot + driver 去重；群级角色沿用普通建群规则，不能把 YAML binding 名当群角色 |
| `collaboration.strategy` | 当前表单选择的 `chat` / `manager_worker` / `state_machine` |
| `collaboration.definition.content_yaml` | 仅自定义协作：提交前校验通过的当前编辑字符串，保持 UTF-8 字节与换行 |
| `collaboration.participant_bindings` | 仅自定义协作：`[{binding, actor_ids: [botId]}]`，按当前校验结果映射，与 definition 同级 |

不调用 Backend task execute，不另发“启动一次”请求，不附带 Draft readToken、助手 token 或声明式用户 ID。Human 会话参与者按既有可信 caller 规则产生，不由模型填入的 Human ID 决定。

BCS 在提交时重新校验发起方所有权、参与者关系、必需绑定和定义约束。候选请求与创建之间发生的权限变化必须拒绝，浮层刷新相关选择，不退回协作助手身份。

## 7. 提交状态与失败处理

状态流为 `配置中 → 确认前复核 → 创建中 → 已创建 / 明确未创建 / 结果未知`。前两阶段无建群副作用；同一配置只能有一个创建请求在途，按钮、回车和重复点击共用提交锁。

| 场景 | 行为 |
| --- | --- |
| 初始引用读取失败、复核网络失败或当前定义无效 | 不发送建群 POST；保留可恢复配置，允许重新复核 |
| 登录过期或明确的权限/参数拒绝 | 显示具体原因；仅当服务契约证明尚无创建副作用时归类为“明确未创建” |
| 创建成功 | 保存响应中的真实群/会话 ID，显示进入入口；再次点击进入同一群，不重复创建 |
| 创建请求超时、断网、5xx、响应无法解析 | 归类为“结果未知”，不自动重试、不换 key 重发，不声称群已回滚 |
| 用户切换账号时请求已发出 | 丢弃对新用户 UI 的回写；旧请求可能已创建，不能向新用户暴露旧群详情 |
| 服务报错且补偿结果不确定 | 按“结果未知”处理，不能因为 HTTP 非 2xx 就推定没有群或 Run |

“结果未知”显示：**“未能确认创建结果，请先到群列表检查，避免重复建群。”** 提供既有群列表入口，保留本次群名与配置用于人工核对；名称相同或查询不到都不能自动证明创建结果。首版不提供自动“重试创建”按钮。

同一副屏关闭重开浮层保留成功/未知状态，不允许重复提交。刷新或关闭副屏后重新打开不会自动恢复/提交建群请求。用户再次主动发起是新操作，可能形成新群；首版只保证单个在途操作防重，不承诺跨刷新、跨设备或跨标签的服务端 exactly-once。更强的结果恢复与幂等重试单列 roadmap，不为本次 UI 新建通用操作记录平台。

## 8. 实施顺序与交付门槛

### L01：真实宿主接入核对

在已配置 `bcsPanel` 的宿主，用登录用户验证“我的 Bot → 指定自有 Bot 的 candidates → V1 建群认证”的调用条件，并核对进入群/群列表的导航能力。只读检查不能擅自创建真实群；写操作验收使用明确授权的测试 Bot 和群。记录请求入口、实际 Human 身份来源、响应格式和失败结果，不记录凭据。

这是实现前置。宿主身份不明时先解决或记录接入边界，不能用假用户 ID、助手 token、私聊 session 反查或浏览器私有存储补身份。

### L02：BCS 只读路由与契约

补齐 Draft V1 读取/校验、版本 envelope 解码及同源宿主 base 契约；回归原生 Draft 读取、撤销、分享状态和管理权限。更新 BCS API contract 与契约测试，不扩大到用户 Draft 写入。

### L03：副屏回调与主屏浮层

Panel 只实现宿主回调。frontend 共用 UMD 宿主按需打开既有建群浮层，在同一表单内接入自有发起 Bot、Human 候选、可选 Draft 初始内容、当前 YAML 校验、提交锁和导航；不复制一套建群 UI，不新增助手工具命令。

### L04：真实验收

使用现场 2174 字节样例、至少一个自有发起 Bot 和可协作 Bot，在已配置宿主完成“预览 → 选择 → 确认 → 新群 → 流程启动”。核对读取源码、提交源码和创建后的定义语义/角色绑定，并确认群与 Run 没有重复创建。

沿用用户此前暂缓私聊页面验收的决定：本轮优先验收现有群聊中的副屏入口；私聊上下文的组件级隔离可以测试，但没有实际入口证据时不能标记私聊端到端已通过。

## 9. 验收标准

| 编号 | 必须证明的结果 |
| --- | --- |
| L-AC01 | 打开/预览卡片不建群；schema 1 保持只读；schema 2 显式进入配置 |
| L-AC02 | 我的 Bot 来自可信用户；共享协作助手下两个用户不能看见或复用对方配置 |
| L-AC03 | 非自有 Bot 不能作 originator；前端篡改 ID 仍由 BCS 拒绝；助手身份不能替用户提交 |
| L-AC04 | candidates 按所选发起 Bot 查询；分页/搜索不误删已有合法选择；切换发起 Bot 后清空旧绑定 |
| L-AC05 | 必填槽位完整、同名 Bot 可区分、相同 Bot 多槽位按契约处理、参与者去重、助手不自动入群 |
| L-AC06 | 初始读取校验固定 revision 与 UTF-8/hash；编辑只作用于本次表单，POST 精确使用当前文本；head 或身份刷新不覆盖编辑 |
| L-AC07 | 初始读取时撤销/删除/完整性异常阻断初始化；当前文本校验失败不 POST；readToken 不进入校验文本/建群请求；无身份降级 |
| L-AC08 | 建群 POST 使用 Human 身份和指定 originator；群 driver、绑定、context 与用户确认一致；Human 可进入会话 |
| L-AC09 | 按钮明确告知启动；成功进入真实新群并观察到首个状态机运行，未额外重复发送启动请求 |
| L-AC10 | 连点/回车只发一次；未知结果不自动重试；服务器失败与不确定补偿不显示虚假“未创建” |
| L-AC11 | 用户/base/版本切换及乱序响应不会串身份、源码或绑定；取消 UI 请求不声称取消服务端操作 |
| L-AC12 | 默认与部署 base、Draft V1 路由、真实用户认证、envelope 和导航均有实测；不能只用 fetch mock 通过 |
| L-AC13 | 预览/源码/复制和逻辑 Loop 无回归；通用事件带/不带 Draft 均可打开同一浮层，带 Draft 的固定类型/无模板/自动校验展开、无 Draft 的三种类型/模板、重新编辑与取消重开均符合入口约束 |

验证分层：Panel `npm run verify`；frontend 群聊测试、lint、构建及实际页面交互；BCS Draft 与 V1 HTTP 契约测试；现有 Group 所有权/参与者检查回归；实际宿主集成。按真实服务端改动范围执行相关 Cargo 测试；没有 Rust 改动时不为文档或 UI 修改重跑全工作区。新增/修改源码检查行数，禁止因既有大文件顺带拆分或降低门禁。

## 10. 当前验证状态

2026-10-22 已收敛为通用宿主回调与 frontend 既有建群浮层参数，支持无 Draft 建群与 Draft 初始内容自由编辑；原生 Human 入口和 Draft V1 投影继续复用。本轮验证记录见 verification。

用户明确选择“暂不创建，保留真实验收待办”：没有新建测试 Bot、关系或群。L-AC08/09 的真实建群、进入和初始 Run，以及 L-AC12 的真实部署接入未完成，不能由组件或应用测试替代。私聊验收继续暂缓。各层测试、环境限制和后续验收步骤见 [verification](./verification.md) 与 [tasks](./tasks.md)。
