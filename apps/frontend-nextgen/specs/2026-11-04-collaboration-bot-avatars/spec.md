---
status: proposed
date: 2026-11-04
owner: frontend-nextgen
scope: collaboration-avatars-and-bot-info-repair
---

# 协作群头像展示与 Bot 信息修复

## 1. 目标与已确认需求

在 `apps/frontend-nextgen` 的「协作群」页面和共享身份选择框中，消费 BCS PR #3488 与 #3622 提供的头像数据：

1. 侧边 Group 列表使用成员的 `avatar_url` 展示缩略头像堆。
2. 会话历史消息与实时消息展示实际发送 Bot 的头像。
3. 无头像 Bot 复用 Bot 管理列表的默认头像规则：调用 `generateBotAvatar(botId)`，同一 Bot 跨群、跨会话、刷新及改名后保持一致。
4. 身份选择框消费 `bots/mine` 返回的 `avatar_url`，当前选中身份与弹层候选项采用同一 Bot 头像规则；同一 Bot 在身份选择框、侧栏和会话中的默认图一致。
5. 内部 Bot 的「公开设置 → 协作能力」增加「修复 Bot 信息」行，点击「修复信息」调用 Backend 的单 Bot 协作信息修复接口。

第 3 项已由用户确认。本文是待评审的设计，不代表业务实现或联调已完成。

## 2. 依据与当前缺口

### 2.1 前端基线与已部署接口

- 前端开发/合并目标：`REL20261105`；2026-11-05 获取的 `origin/REL20261105` 最新提交为 `bf74bac9141751ddfb00c9773ac553c108c697c9`。本文的前端文件、入口和测试均以此为基线。
- 文档分支：`docs/rel20261105-collaboration-avatars`，从该远端提交创建，迁入原文档提交 `19448431d5`。不向 `dev` 或发布分支直接提交。
- **部署前提（用户已确认）：BCS 部署使用 dev，本文涉及的 BCS 头像查询与 Backend 修复接口均已部署。** 前端分支不必包含对应服务端源码；不得以 `REL20261105` 本地 BCS/Backend 文件缺失或版本较旧为由补写服务端实现、迁入服务端提交或等待上线。
- 本文只规定前端消费的 HTTP 请求、响应、权限和错误语义；服务端内部存储、模块、迁移和执行流程不属于前端改造范围。接口来源为 PR #3488、#3622、#3569，仅作追溯，不作为本地源码依赖。
- [架构约束](../../../../docs/arch/arch.rules.md)、[CI 要求](../../../../docs/arch/ci.enforce.md)、[领域文档约定](../../../../docs/agents/domain.md)。本变更不引入系统级所有权或生命周期决策，无需新增或替代 ADR。
- 本分支已把原「通用配置」菜单和弹窗改名为「公开设置」，组件名仍为 `BotGeneralConfigDialog`。用户截图中的位置对应当前「内部 Bot → 公开设置 → 协作能力」，本功能沿用当前命名。

| 消费端接口 | 已部署响应字段 | 消费规则 |
| --- | --- | --- |
| `GET /openapi/v1/collaboration/groups` | 每个 Group 的 `participants[]: ParticipantSummary[]` | 用于侧栏展示，顺序沿用响应 |
| `GET /openapi/v1/collaboration/groups/{group_id}` | 完整成员的 `avatar_url` | 保留完整角色、模式等成员信息 |
| `GET /openapi/v1/collaboration/groups/{group_id}/sessions` | 每个 Session 自己的成员摘要，含 session-only 动态成员 | 不用 Group 成员替代 Session 成员 |
| `GET /openapi/v1/collaboration/sessions/{session_id}` | 完整会话成员的 `avatar_url` | 作为选中会话的成员详情 |
| 复用上述详情/成员模型的写操作响应 | 同样携带 `avatar_url` | 更新、增删成员后的现有刷新链路保留头像 |
| `GET /openapi/v1/collaboration/bots/mine`（#3622） | `data.items[]` 中 Bot/Human 行均显式返回 `avatar_url: string \| null`；Human 恒为 null | Bot 使用该行头像或按 `bot_id` 生成默认图；Human 沿用用户头像逻辑 |

`ParticipantSummary` 只包含 `actor_id`、`actor_kind`、可为 null 的 `name` 和 `avatar_url`，没有 `role`、`mode`、`message_view_scope`。名称是当前 Group/Session 成员快照，头像是接口返回的当前 Bot 展示资料。Human 的 BCS 头像为 null。历史消息或 WebSocket 消息不提供本功能所需的新增头像字段；前端从当前成员资料解析头像，不依赖 legacy 响应扩展。

`mine` 头像读取失败会使请求失败，不返回伪造的全 null 成功结果。前端应沿用身份加载失败和重试机制；只有成功响应中的无头像 Bot 才进入正常默认图分支。

### BCS 消费端响应约定

Group/Session 列表和 mine 沿用 BCS envelope：正常响应 `code: 20000`，分页数据位于 `data.items`，保留 `offset`、`limit`、`total` 和当前调用方的查询参数、身份视角。详情位于 `data`。现有消费边界支持的 envelope 兼容方言不因头像而改变。

列表成员摘要示例（`participants[]` 元素，不包含控制字段）：

```json
{"actor_id":"bot-1","actor_kind":"bot","name":"Alpha","avatar_url":"/assets/alpha.png"}
```

完整 Group/Session 成员在相同身份字段之外继续提供既有 `role`、`mode`、`message_view_scope` 等控制字段，并增加 `avatar_url: string | null`；这些字段不可用摘要默认值代替。完整成员 `name` 缺省时沿用现有前端处理；摘要 `name` 可为 null。无头像 Bot 和 Human 的 `avatar_url` 为 null。Group/Session 列表中 `participants: []` 与兼容响应完全缺省该字段必须区分。

mine 的分页元素按 `bot_id` 标识，而不是 `actor_id`：

```json
{"code":20000,"data":{"items":[{"bot_id":"bot-1","kind":"bot","name":"Alpha","avatar_url":"/assets/alpha.png"},{"bot_id":"human_1","kind":"human","name":"用户","avatar_url":null}],"offset":0,"limit":50,"total":2}}
```

以上 JSON 仅展示本功能消费字段；既有 status/reachability/engine/type、分页和认证逻辑保持。URL 必须按返回值消费，不从本地服务端文件、Bot 名称或 Provider 地址拼接。接口错误继续走现有加载错误/重试路径，不把错误伪装成成功的空头像响应。

### 2.2 现有实现

| 位置 | 观察到的缺口 |
| --- | --- |
| `services/backendApi/collaboration/{collaborationGroupController,sessionController}.ts` | 成员 DTO 未声明头像，列表响应复用详情类型，未区分轻量摘要 |
| `services/workspace/mappers.ts` | Group 列表固定映射为 `participants: []`；Session/成员映射未保留头像 |
| `services/workspace/groupService.ts` | 详情内联映射丢失头像；为侧栏补拉 Group 详情，间接还会加载会话列表 |
| `pages/Workspace/hooks/useGroupWorkspaceMutations.ts` | 仍假设列表没有成员，逐群补拉并缓存详情成员 |
| `pages/Workspace/components/GroupSidebar/GroupAvatarStack.tsx` | 仅接受 HTTP(S) 地址，过滤相对地址及生成头像的 data URI |
| `pages/Workspace/components/GroupChatPane/messageHelpers.tsx` | `senderName`/`botName` 分支在按 ID 查成员前返回；有名称时会跳过实际头像；Bot 图片没有失败回退 |
| `pages/Workspace/hooks/useSessionMemberSync.ts` | 以 `participants.length` 区分是否已有详情；不能把摘要直接塞进该数组 |
| `components/BotWorkshop/BotCard/BotInfoCell.tsx` | 已采用 `bot.avatarUrl || generateBotAvatar(bot.id || bot.name)`，是默认头像的参照 |
| `services/backendApi/collaboration/collaborationBotController.ts` | `CollaborationBotDto.avatar_url` 已存在，但只声明 `string`，需接受已部署接口的显式 null，兼容旧响应缺省 |
| `services/workspace/identityService.ts`、`identityStore.ts` | 已读取 `avatar_url`，Store 比较也包含 `avatarUrl`；应保留链路，补充 null/清空/失败测试，不重复造头像查询 |
| `hooks/workspaceIdentityMapper.ts` | 将 `IdentityView.avatarUrl` 映射到 `Identity.avatar`，缺省时写名称首字；Bot 显示层不能把这个字符视为头像来源 |
| `components/Workspace/IdentitySelector/{IdentitySelectorParts,InlineIdentityList}.tsx` | 触发按钮/普通选项与 inline 选项各有 HTTP-only 过滤；未应用 Bot ID 固定默认图 |
| `components/CollaborationPrivacy/BotGeneralConfigDialog/index.tsx` | 协作能力卡片目前位于协作查询成功分支，新增修复入口需在查询失败/无目标数据时独立保留 |
| `components/CollaborationPrivacy/settingsSections.tsx` | 当前 296 行且被内部/外部配置和权限页复用；新增修复行需提取能力组件并以内部入口专用插槽注入，避免扩大所有页面行为 |

### 2.3 REL20261105 前端差异约束

- `BotGeneralConfigDialog`、菜单、外部 Bot 入口及已有测试使用「公开设置」「正在加载公开设置」「公开设置加载失败」，不恢复旧标题。
- `GroupItem.tsx` 当前 279 行，Human 新建会话使用 `MessageViewScopeMenuButton`，Bot 使用 `IconButton`。当前分支没有 `CreateSessionMenu` / `GroupContextDelivery`；头像任务不引入其他分支的会话上下文投递功能。
- `sessionService.createNewSession` 当前参数为 groupId/title/contextQuery/messageViewScope，Controller 请求不含 context_delivery。新增列表/详情头像映射时保持此签名与现有请求行为。
- Bot 头像编辑当前先本地暂存，点击「保存修改」才持久化；默认图仍由 `generateBotAvatar(bot.id || bot.name)` 产生。本功能复用生成器，不改保存链路；修复针对已保存的信息，不提交编辑器尚未保存的头像。
- 沿用发布分支自己的 package-lock、公开设置与群聊测试，不以旧分支完整文件覆盖或恢复已删除的会话创建测试。

## 3. 方案比较与选择

| 方案 | 优点 | 代价或问题 |
| --- | --- | --- |
| **A. 消费 BCS 列表摘要和详情，共用 Bot 头像展示规则（推荐）** | 使用当前身份可见的数据；列表无需为头像逐群补拉；支持 session-only Bot | 要区分摘要与完整成员，并修正消息发送者解析 |
| B. 仅给详情补映射，继续逐群补拉 | 改动入口较少 | 浪费列表新增字段，保留 N+1 请求；首屏缩略头像仍依赖详情到达 |
| C. 额外读取 Bot 管理列表或逐 Bot 查询头像 | 可使用 Backend 侧资料 | 需要额外权限、请求与缓存，不能覆盖所有其他用户及外部 Bot，也绕开协作查询的展示契约 |

采用 A。头像展示不新增依赖、服务端接口、上传入口、轮询或全局头像缓存。新增的手动修复按钮独立消费 #3569 已有接口，按用户点击执行一次，不作为头像加载的自动补偿。

身份选择框同样消费它已有的 `mine` 数据链路并复用展示组件；不单独查询头像，也不以 Bot 管理资料覆盖 `mine` 的头像状态。

## 4. 数据模型与状态边界

### 4.1 摘要与详情分开

Controller 层分别声明 Group/Session 列表 DTO 和详情 DTO，并共用轻量 `ParticipantSummaryDto`。按新契约，摘要的四个字段均存在，`name` 与 `avatar_url` 可为 null。兼容旧服务时，仅在读取边界允许头像字段或整份摘要数组缺省；不得把完整成员必需的角色/模式改成可选。

领域层引入 `ParticipantSummaryView`，仅含 `actorId`、`kind`、`name`、`avatarUrl?`。`ParticipantView` 保留完整角色、模式和可见域要求。

在 `GroupView`、`SessionView` 增加 `participantSummaries?: ParticipantSummaryView[]`：

- 列表映射把轻量行写入 `participantSummaries`，完整 `participants` 保持空数组，不伪造 `member` / `auto` 等控制字段。
- 详情映射继续填充完整 `participants`，并由同一份响应派生对应的 `participantSummaries`，两者一起更新。
- `participantSummaries === undefined` 表示旧响应没有此字段；`[]` 表示服务端明确返回空名单，两者不可混淆。
- 展示组件优先消费 `participantSummaries`，缺省时才投影已有完整成员。管理、权限、发言模式、可见域及已加入状态继续依赖完整成员。
- 摘要非空不代表详情加载完成；Group/Session 使用内部 `participantDetailsLoaded` 标记区分列表（false）与完整详情（true），完整空详情同样为 true。`useSessionMemberSync` 仍须取得选中会话的详情；仅为头像补拉得到的群成员数组不等于完整群配置，不将 Group 标记为详情已加载。

映射时，非空头像字符串去除首尾空白后保留；null、缺省、空串及纯空白归一为无头像。摘要名称为 null/空白时使用该行 `actor_id` 展示，不用名称建立成员身份。Human 不应用 Bot 默认头像规则。

### 4.2 刷新、兼容与缓存

新列表已携带摘要时，侧栏直接渲染，**不得因头像为 null 或摘要为空而启动逐群详情补拉**。无头像属于正常数据。

仅当旧响应完全缺少摘要字段，且现有完整成员不足时，保留当前有界并发补拉作为兼容路径。补拉不新增 Bot 管理接口请求。该兼容路径的缓存和异步结果按身份、群及列表加载代次隔离；身份切换后旧结果不得写入新列表。

当前有效列表/详情响应中的摘要替换旧展示数据，包括显式空名单和头像清空。已有缓存不能覆盖新摘要；迟到的兼容补拉也不能覆盖本轮已返回的摘要。详情成员和摘要需分别管理，不能为了刷新头像把详情控制信息伪装成已加载。新列表替换完整群详情后，已打开的群管理面板必须按需重拉当前群详情，恢复真实角色、模式、Owner 和群配置；关闭面板不触发该请求，完整空详情不循环重拉。此请求属于管理数据加载，不属于为头像逐群补拉。

固定群/固定会话入口不加载全量群目录，也必须能刷新详情和应用成员变更。详情请求的身份初始化与失效判断不能依赖曾执行过列表加载；身份/代次隔离同样适用于这些入口。

修改成员后沿用现有详情刷新；重新进入页面、搜索或现有列表刷新时采用新响应。本文不承诺 Bot 管理页保存后跨页即时推送，也不增加定时刷新。上游 BCS 未同步头像时，展示稳定默认头像，前端不越过 BCS 查询其他来源补偿。

### 4.3 身份列表的数据链路

沿用 `listMyBots → identityService.loadIdentities → applyIdentityLoadResult → IdentityView → mapIdentityViewToIdentity → WorkspaceIdentitySelector`，不改变请求参数、分页、模块级并发请求复用、身份筛选与默认选中策略。

- `CollaborationBotDto.avatar_url` 在消费边界声明为 `string | null | undefined`：null 是已部署接口的有效响应，缺省仅用于兼容旧接口。共享此 DTO 的其他调用点应同步归一化 nullable 字段，避免将 null 泄漏到要求 `string | undefined` 的展示类型。
- `IdentityView.avatarUrl` 仍只存服务端的非空字符串或 undefined。头像读取只接受字符串并 trim；null、缺省、空串、纯空白或非字符串按无头像处理，不从对象的 `name/type` 等字段推测头像。
- 保留 `Identity.id` 对应的 Bot 身份，不为头像截断、拼接或重新解析 `bot_id`；以该 ID 调用同一生成器。身份选择、请求参数与消息身份比较不因头像展示改变。
- 现有 `Identity.avatar` 展示字段可保持兼容；首字等非图片值由 Bot 展示分支识别为无头像并回退 `generateBotAvatar(identity.id)`，不扩展为全站 Identity 模型迁移。
- 既有 Bot 元数据补充请求仍只承担引擎/类型信息补齐，不新增请求，不用补充接口的头像覆盖 `mine` 结果，更不能用旧图覆盖显式 null。
- `applyIdentityLoadResult` 已把头像纳入差异比较；只修改或清空头像时也须更新展示，同时保留仍在列表中的 `activeIdentityId`，不触发身份切换及其群/会话重置副作用。
- `mine` 失败沿用已有错误态和重试；不得把失败转换为成功的空身份列表或仅含默认头像的伪造列表。

## 5. 头像展示规则

### 5.1 共用展示单元

在 Workspace 的共享展示组件中集中实现 Bot 图片与失败回退；侧栏头像堆、Group 顶栏复用处、消息发送者和身份选择框使用同一规则。复用 `services/botWorkshop/botAvatarGenerator.ts` 的 `generateBotAvatar`，不复制算法、不改生成器参数或 Bot 管理页的行为。

图片优先级：

1. 当前成员、已解析发送者或 `mine` 身份行的可用头像地址。
2. `generateBotAvatar(actorId)`，使用默认 `index = 0`。
3. 极端情况下生成图片也无法渲染时，以本地 Bot 图标占位，结束回退，不循环触发错误。

正常 Bot 必须使用稳定 ID 作为 seed，禁止使用群 ID、会话 ID、名称、数组下标或随机数。历史消息没有任何 Bot ID 时，使用固定 seed `bot`，不借用其他成员身份。

接受已有产品支持的图片来源：HTTP(S)、协议相对地址、站点根相对路径和 `data:image/...`（含 DiceBear 输出的 SVG data URI）。只通过图片元素渲染，不把 SVG 内容插入 HTML。其他不可作为图片的值使用默认头像，不把 URL 文本当作 emoji 显示。

URL 加载失败时自动切换到该 Bot 的默认头像，不阻断聊天、不弹 Toast。失败状态绑定当前 actor ID 和源地址；源地址变更后尝试新图，不能让旧地址的失败状态使新头像永久不可见。默认头像仅用于展示，不写回 DTO、Store 中的业务头像字段或后端。

### 5.2 侧栏与共用 Group 顶栏

- 普通 Group 行继续使用现有叠放样式，按服务端成员顺序取前两个，超出的数量显示现有 `N+`。保留 Human，不按是否有头像重新排序。
- Bot 使用上述图片/默认头像规则；Human 保留现有头像或首字占位规则。
- `totalCount` 优先使用当前响应有效的 `participant_count`（包括显式 0），缺省或非法值时使用本响应展示成员数量；剩余数量为 `max(0, totalCount - shown.length)`。mapper 必须在原始计数仍可区分时完成回退，不能先把缺省转为 0 再让显示层判断。没有计数且没有任何成员资料时回退 0，后续完整详情按实际人数更新。
- 展开侧栏沿用 20px 头像；收起侧栏复用紧凑 16px 头像堆，在现有入口中保留点击、Tooltip 和选中指示。Group 顶栏继续复用该组件及原尺寸。
- 没有任何可展示成员时，不生成虚构 Bot；展开行沿用无头像堆，收起入口沿用群图标占位。
- 保持现有布局与交互；底板、边框和占位符使用项目语义 token。头像图案沿用已有生成器颜色，不新增组件内硬编码色板。
- 图片提供成员名称替代文本，群入口仍有群名称，人数标识继续可被辅助技术读取。

### 5.3 会话消息的 Bot 身份与头像

名称解析与头像解析分开。名称继续优先使用可读的 `senderName`、`botName`，随后是匹配成员名称及现有兜底；有名称不能跳过头像查找。

对于 assistant/Bot 消息：

1. 使用非空 `extra.botUuid`，否则使用非空 `extra.senderId`，作为唯一的 Bot 查找 ID；两者冲突时沿用 `botUuid` 的 Bot 身份，不尝试用另一 ID 借头像。
2. 以此 ID 精确匹配当前 Session 的展示成员；Session 没有该 Bot 时，再匹配当前 Group 的展示成员。只匹配 `kind: bot`，不按名称、成员顺序或当前登录身份匹配。
3. 匹配到成员后使用该成员的头像状态；其头像为空时直接用该 ID 的默认头像，不退回其他池或消息里的旧头像，以免清空后旧头像复活。
4. 两处均没有该成员时，可使用消息已有的非空 `senderAvatarUrl`；否则使用该消息 Bot ID 的默认头像。该字段仅为既有本地兼容数据，不要求 BCS 消息增加字段。
5. 没有 ID 的消息可使用自身已有头像，否则使用固定通用 seed；不能按同名 Bot 查头像。

同一会话中，不论历史消息是否带 `bot_name`，或实时消息是否带 `senderName`，都遵守上述规则。Session-only Bot 使用 Session 资料。离群/删除 Bot 的历史消息仍有 BCS 成员展示记录时使用该记录；没有记录时按发送者 ID 回退，不请求 Bot 管理接口。

成员资料到达或刷新后，现有消息由渲染时解析得到新头像，不把默认头像固化进消息历史。BCS 未发送头像事件时，不保证页面长期停留期间自动追踪头像修改。

Human 消息保留当前登录用户头像和其他 Human 的匹配规则；system 消息保留系统行。尚未知道发送者的 pending / Thinking 占位继续匿名，不提前显示 Driver、Manager 或第一个 Bot 的头像。

### 5.4 身份选择框

- 协作群 `CollaborationIdentityHeader` 的当前身份触发区域、弹层中的全部 Bot 候选项及只读身份卡片均应用同一规则。切换身份后，触发区域立即使用新选中 Bot 的 ID 与 URL，不沿用上一 Bot 的图片失败状态。
- 通过共享 `IdentityAvatar` 和 inline 选项的头像入口统一接入，覆盖 `WorkspaceIdentitySelector` 已有 `default`、`sidebar`、`collapsed`、`collaboration`、`sidebarChip`、`inline` 布局，避免只修触发按钮或只修弹层列表。各布局保持原头像尺寸、搜索、分组、选中标识、Tooltip、引擎标签和在线状态。
- Bot URL 优先来自其 `mine` 行；缺失、非法图片来源或图片加载失败时，使用同一 `identity.id` 的固定默认图。根相对路径及 data URI 与侧栏/会话使用相同支持范围。
- 用户身份继续使用调用方传入的 `userAvatarUrl` 和既有 Human 回退规则；`mine` 中 Human 的 null 不应抹掉另一路已提供的用户头像，也不将 Human 渲染成 Bot 默认图。
- 同一 Bot 在不同入口读到相同 URL 时显示同一图；均无头像时默认图必须一致。不同接口的数据刷新时间可能不同，本期不建立跨接口头像缓存，也不宣称 `mine` 能覆盖 Group/Session 的明确头像状态。
- 共享身份选择框在其他页面的复用入口随组件一起生效；范围仅限该组件，不扩展为其他页面的卡片或消息头像重构。

## 6. 预期改动边界

| 层 | 负责的变化 |
| --- | --- |
| API Controller DTO | 区分列表摘要与详情；声明增量头像字段及 `mine` 的 nullable 头像；保持请求路径、参数、认证和分页语义 |
| `domain/collaboration/types.ts` | 定义只读展示摘要，保持完整成员的控制契约 |
| Workspace service / mapper | Group、Session 列表与详情保留头像；共用详情成员映射；兼容 legacy 缺头像，不承诺 legacy 返回新字段 |
| Group / Session hooks 与管理面板宿主 | 新摘要路径跳过头像补拉；固定入口独立初始化身份；管理面板跟随详情失效按需重拉；隔离兼容请求 |
| Workspace 头像展示组件 | URL、稳定默认图、错误回退及源变化后的重试 |
| GroupAvatarStack / GroupItem / CollapsedRail / GroupHeader | 选择展示成员并复用头像规则，不读取 API DTO |
| GroupChatPane 消息解析与调用方 | 独立解析发送者名称和头像，传递 Session/Group 展示成员 |
| identityService / identityStore / workspaceIdentityMapper | 保留 `mine` 头像，处理 null/缺省和头像单独更新，维持当前选中身份 |
| IdentitySelectorParts / InlineIdentityList / CollaborationIdentityHeader | 触发区域、候选项及只读身份展示共用 Bot 图片规则；保留 Human 分支和现有布局 |
| BotGeneralConfigDialog / 修复行 / 修复 Hook 与 service | 仅内部 Bot 提供手动修复；空 body POST、Owner 范围、报告校验与结果反馈 |
| useCollaborationPrivacy / 共享设置行 | 自动刷新在上下文结束后不得写共享 Store；拆分能力组件时保留发布审批共用的 SettingRow |

不改 BCS/Backend 的协议、存储、同步、授权或消息发送逻辑；除共享身份选择框和内部 Bot 修复入口外，不覆盖协作广场、DM、Bot 管理页其他头像组件及全站头像重构。新生成头像不增加远程服务依赖。代码拆分遵守现有 guardrails 的 Component 300 行、Hook 250 行、Store 150 行和仓库源文件 1,000 行限制。

### 6.1 内部 Bot 修复入口与布局

布局依据用户提供的桌面截图 `1.png`：在现有「协作能力」卡片内按上下行展示，左侧标题和说明，右侧操作；第二行前有 `border-border` 分隔线。截图仅用于设计参考，不提交包含个人桌面路径或运行资料的图片。

第一行保留标题「参与协作群聊」、说明「控制当前 Bot 是否可参与群聊。关闭后无法加入新协作群，已加入的协作群也不再回复。」及既有开关。第二行标题为「修复 Bot 信息」，按钮为「修复信息」。用户示例中的“好友关系”不属于接口修复范围。经用户确认，采用“归属”替代，最终说明为：

> 修复当前 Bot 在「协作群」中的基本信息（名称、头像、归属、协作者等）

布局使用现有 `Button`（outline、sm）、语义颜色和圆角，不增加新确认弹窗。内部 Bot 分区采用单列完整行，避免把修复按钮塞进原两列网格；「公开 Bot 画像」等既有适用能力保留，在修复行之后显示，不能为匹配截图删掉其他能力。提取能力分区时，`SettingRow` 仍由协作能力和发布审批共同使用；发布审批行的渲染、提交、加载和错误行为必须保留。外部 Bot、独立协作权限页面不出现此入口。已有 Coding Bot 未声明 BCN 的整组隐藏规则保持。

修复入口由内部 Bot 配置入口身份判定，不通过 ID 或引擎名猜测。沿用 Backend Owner 和编辑锁限制，使用 Backend 列表行 `bot.id`，不能把卡片展示用的 `targetBot.id`（BCS UUID）用作请求寻址。群聊开关关闭、Bot 离线、协作读取失败或未命中不应禁止 Owner 发起修复；BCS 目标不存在由修复接口明确拒绝，不自动注册或启动 Bot。协作配置异常时保留其现有错误/空态，并独立展示「协作能力」修复卡；无需先成功读取 BCS 才能修复。

### 6.2 修复接口与报告

```http
POST /openapi/v1/bots/{bot_id}/collaboration/repair
```

路径变量使用 Backend 列表行 `bot.id` 并通过 `encodeURIComponent` 编码。使用现有 Backend 请求封装和 user-scoped 认证；不传 data/rawBody，不发送 `{}`、null、业务字段或 owner/BCS ID 覆盖参数。禁止自动重试。后端只允许当前 Backend Owner，非 Owner 按现有资源隐藏策略返回 404，前端不把所有 404 都解释成“Bot 被删除”。

报告为 `Envelope<{bot_id,status,items}>`，Backend envelope 成功码与 BCS 码不同。必须同时校验 envelope、报告结构、四个固定顺序分项和整体状态一致性，不能仅凭 HTTP 200 弹成功：

| 字段 | 合同 |
| --- | --- |
| `status` | `succeeded`、`partial_failed`、`failed` |
| `items[].component` | 固定顺序 `ownership`、`profile`、`managers`、`caller_config` |
| `items[].status` | `succeeded`、`failed`、`unknown`、`skipped_dependency`、`not_applicable` |
| `items[].error_code` | nullable 稳定原因码；不展示原始下游 payload |

HTTP 200 的 Backend 正常 envelope 使用 `code: 200000`，不是 BCS 的 `20000`。部分失败示例：

```json
{
  "code": 200000,
  "data": {
    "bot_id": "bot-1",
    "status": "partial_failed",
    "items": [
      {"component":"ownership","status":"succeeded","error_code":null},
      {"component":"profile","status":"succeeded","error_code":null},
      {"component":"managers","status":"unknown","error_code":"delivery_timeout"},
      {"component":"caller_config","status":"not_applicable","error_code":null}
    ]
  }
}
```

报告固定四项；存在 failed/unknown/skipped_dependency 且至少一项 succeeded 时为 partial_failed，无任何成功项时为 failed，其余适用项均确认成功时为 succeeded；not_applicable 不算失败。`data.bot_id` 是 Backend 标识，前端以发起操作时的 Bot 上下文呈现结果，不用响应去改变当前选中身份。

| HTTP 结果 | 前端处理合同 |
| --- | --- |
| 401 / 403 | 沿用认证/无权限处理，不显示修复成功 |
| 404 | Bot 不可访问或协作目标不存在；保留资源隐藏语义，不自动注册 |
| 409 | 当前身份或资料存在冲突；提示失败，允许用户处理后再次手动发起 |
| 502 / 503 / 504 | 上游错误、暂不可用或超时；不自动重发，不把超时解释为未发生修改 |
| 其他异常 / 网络中断 / 非法报告 | 未能确认修复结果，按已有错误机制处理 |

前置拒绝采用 Backend 错误 envelope，`data: null`；它不是执行报告。已部署并不保证每次调用成功，上述错误分支仍需覆盖。

真实修复范围是 Owner、名称/描述/头像、有效协作者及适用的调用配置，**不修复好友关系、好友审批、可见性、Group/Session 或运行状态**。前端不能因为展示文案包含好友关系，就额外发起关系写操作。

### 6.3 按钮状态与反馈

- 点击立即进入「修复中…」loading/disabled；同一 Bot 的重复点击、快速关闭重开期间仍在途的点击共用一次请求。请求结束后可再次手动执行；不做后台重试或轮询。
- `succeeded`：提示「Bot 信息已修复」。`partial_failed`：提示「部分 Bot 信息修复失败」。`failed`：提示「Bot 信息修复失败」。后两种在行下展示四项中文结果（归属、基本资料、协作者、调用配置），`unknown` 明确显示「结果未确认」，不声称没有修改或已经回滚。
- 网络断开、超时、返回格式异常：显示「未能确认修复结果，请稍后重试」；HTTP 401/403/404/409/502/503/504 保留现有错误处理与重试能力，错误提示通过已有去重 helper，避免全局与局部重复 Toast。
- 在同一 Bot 的成功或部分失败报告后，尝试一次现有 `privacy.load()` 刷新配置；刷新失败保留修复报告，另注明「修复请求已完成，配置刷新失败，请重新打开查看」，不把读失败改写成修复失败。不自动切换身份或重置群/会话，不手工覆盖头像缓存。
- 弹窗关闭或切换 Bot 后，不把旧响应结果显示在新 Bot 上；关闭不意味着服务端取消或回滚。请求仍可完成，组件卸载后不更新其状态或发出旧 Toast。此隔离覆盖 POST 完成后的配置刷新窗口：关闭/切 Bot/身份变化后，旧 `privacy.load()` 的成功、失败及 finally 均不得改写共享 `collaborationPrivacyStore` 的 overview/error/loading。仅保护修复 Hook 的 generation 或给弹窗加 key 不足以保证这一点，必须在读取 Hook 的 Store 写入边界使旧上下文请求失效。

推荐「独立修复 Controller → service → Hook → 卡片行」：便于保留执行报告与单次请求语义。直接复用现有协作开关更新会混淆接口和状态；在 UI 手动逐项同步会绕开 Backend 权威事实和授权，因此不采用。

## 7. 可测试验收条件

| ID | 当… | 应该… |
| --- | --- | --- |
| AC-01 | Group 列表返回含有效 URL 的成员摘要 | 首次列表渲染即显示前两个成员头像和正确的 `N+`，不等待详情 |
| AC-02 | 列表返回摘要，包括空数组或成员头像全为 null | 为头像发起的逐群详情/会话列表/Bot 资料请求数为 0 |
| AC-03 | 旧响应缺少摘要或头像字段 | 页面仍可展示，必要时使用现有有界详情补拉，Bot 使用固定默认头像 |
| AC-04 | Bot 头像缺省/null/空串/空白，或 Bot 改名、跨群、刷新 | 相同 ID 的默认图与 `generateBotAvatar(id)` 一致，不随名称及会话变化 |
| AC-05 | URL 为根相对路径、HTTP(S) 或有效 data URI | 侧栏和消息作为图片展示，不被 HTTP-only 过滤 |
| AC-06 | 图片加载失败，随后同一 Bot 得到新 URL | 先回退同一 ID 默认图；新 URL 到达后重新尝试，不留破图、不循环重试 |
| AC-07 | 消息含 `senderName` 或 `botName`，并匹配到有头像的 Bot | 名称沿用原展示优先级，头像来自该 ID 成员，不被名称分支短路 |
| AC-08 | 两个同名 Bot 或多个 Bot 并发发言 | 每条消息精确按 Bot ID 展示自己的头像，互不串用 |
| AC-09 | Bot 仅在 Session 中，或同一 ID 的 Session/Group 展示资料不同 | 优先使用 Session；Session 明确无头像时使用默认图，不借 Group 旧图 |
| AC-10 | 历史 Bot 已不在成员名单，或消息完全没有 ID | 有 ID 时按该 ID 回退；无 ID 时显示通用默认图，不借其他 Bot 头像 |
| AC-11 | 已有头像在新有效响应中清空，或新摘要为空 | UI 清除旧头像/旧成员；缓存及迟到补拉不得恢复旧值 |
| AC-12 | 选中会话的列表摘要已加载但详情未加载 | 继续请求详情；管理权限、mode、message view scope 不由摘要伪造 |
| AC-13 | 切换身份或列表后旧兼容请求完成 | 不能覆盖当前视角摘要、成员或头像 |
| AC-14 | 当前用户/其他 Human、system、未知发送者等待占位出现 | 保持现有身份与占位规则，不套 Bot 默认图或猜测回复 Bot |
| AC-15 | 展开/收起侧栏或在 Group 顶栏查看同一群 | 对应 Bot 使用同一头像规则，点击、Tooltip、选中态和人数可访问性不回归 |
| AC-16 | `mine` 返回带头像的 Bot，包括 HTTP(S)、根相对路径或有效 data URI | 当前身份触发区域、普通/inline 弹层候选项及只读身份卡片显示该图；各布局无 HTTP-only 过滤 |
| AC-17 | `mine` 中 Bot 头像为 null/缺省/空串/空白/非字符串，或加载失败 | 使用 `generateBotAvatar(identity.id)`；与侧栏和会话中同 ID 的默认图一致，不随名称及选项顺序变化 |
| AC-18 | `mine` 刷新仅改变头像，或把已有头像清空 | 触发区域和候选项更新/回退，保留仍有效的当前身份，不触发身份切换及群/会话重置 |
| AC-19 | 从图片失败的 Bot A 切换到 Bot B，或 A 的 URL 更新 | 尝试当前 Bot 的新 URL，不沿用旧失败状态；搜索、在线状态、标签和选择动作不回归 |
| AC-20 | `mine` Human 行返回 null，但调用方提供用户头像 | 用户仍展示该用户头像；未提供时按现有 Human 规则回退，不生成 Bot 图 |
| AC-21 | 身份选择框首次加载或展开 | 复用现有 `mine` 和元数据加载结果，不为头像增加请求；元数据补充不覆盖 `mine` 的头像或清空状态 |
| AC-22 | `mine` 因头像读取错误而失败 | 沿用身份加载错误态和已有重试入口，不把请求失败伪装为成功的默认头像列表 |
| AC-23 | Owner 打开内部 Bot 公开设置 | 协作能力中开关下有分隔线、修复说明和右侧按钮；其他适用能力保留；外部 Bot 与独立权限页无按钮 |
| AC-24 | 点击「修复信息」 | 仅对 Backend `bot.id` 发送一次无 body POST；不使用 BCS UUID，不修改开关或发起注册/好友写操作 |
| AC-25 | 修复在途时连续点击或关闭重开后再点击 | 同一 Bot 只存在一次 POST；按钮显示修复中且不可重复提交，结束后可手动重试 |
| AC-26 | HTTP 200 返回全部成功/部分失败/失败报告 | 仅 succeeded 提示成功，其他显示对应结果和分项；unknown 明确是未确认，不声称回滚 |
| AC-27 | 非 Owner、锁被其他人持有，或后端拒绝/超时/报告格式错误 | UI 遵守既有可操作限制；服务端失败不伪装成功、不自动重试、不暴露原始敏感响应 |
| AC-28 | 群聊关闭、Bot 离线或协作配置读取失败/未命中 | 符合 Owner 条件且具备 BCN 能力的内部 Bot 仍有可用修复入口；缺失目标由服务端拒绝 |
| AC-29 | 关闭/切换 Bot 后旧请求完成 | 新 Bot 不显示旧结果，不自动关闭新弹窗或改变当前身份/群/会话 |
| AC-30 | 成功或部分失败后配置刷新失败 | 修复报告保留，独立提示读取失败；不重发修复 POST |
| AC-31 | 固定群/固定会话入口未加载全量群目录，随后刷新详情或修改群成员 | 新详情、成员和头像正常应用，不因 token 尚未初始化而丢弃；旧身份结果仍被隔离 |
| AC-32 | 群管理面板保持打开，当前群被新列表摘要替换 | 新摘要生效并按需补齐完整管理详情，不永久显示成员加载态；空详情不循环重拉，面板关闭时不补拉 |
| AC-33 | 列表含 4 名摘要成员但 participant_count 缺省/非法，或显式为 0 | 真实映射到渲染链路中，前者显示 4 人及 2+，显式 0 保留为 0 |
| AC-34 | A 修复 POST 已完成，但配置刷新在途时关闭 A 并打开 B | A 的迟到成功、失败和 finally 均不覆盖 B 的 overview/error/loading；不重发修复请求 |
| AC-35 | 提取协作能力组件后打开内部团队 Bot 的发布审批分区 | 共享 SettingRow 仍能渲染、提交及表达 loading/error，外部 Bot 和独立权限页无修复入口 |

## 8. 验证计划与本轮证据

实现阶段应补充有行为断言的测试，重点覆盖数据丢失、发送者误认、请求数量和详情加载边界：

- `test/services/workspace/mappers.test.ts`、`groupService.test.ts`：摘要/详情契约、null/缺省、头像透传及 service 请求断言。
- `test/pages/Workspace/hooks/useGroupWorkspace.test.ts`、`useSessionMemberSync.test.ts`：新列表零头像补拉、旧接口兼容、缓存覆盖、身份竞态及摘要不能阻止详情加载。
- `test/pages/Workspace/components/GroupChatPane/messageHelpers.test.ts`、`GroupChatBubble.identity.test.tsx`：历史/实时消息带名字时仍正确查头像、同名 Bot、Session-only Bot、清空和 Human 回归。
- 为共用头像和头像堆补充 React 渲染测试：真实 `src`、错误事件、新 URL、默认生成结果及人数；默认图断言复用生成器返回值，不硬编码整段 SVG。
- `test/services/workspace/identityService.test.ts`、`identityStore.test.ts`、`test/hooks/workspaceIdentityMapper.test.ts`：`mine` 头像透传、null/旧字段缺省/非字符串、清空、仅头像变化、元数据不覆盖头像、请求数及失败语义。
- `test/components/Workspace/IdentitySelector.display.test.tsx`、`IdentitySelector.layout.test.tsx`、`InlineIdentityList.test.tsx`、`test/pages/Workspace/components/CollaborationIdentityHeader.test.tsx`：Bot 触发区域/候选项/只读卡片、各布局、URL 失败与切换、固定默认图及 Human 头像回归。
- 新增修复 Controller/service/Hook 测试：无 body、正确寻址、报告结构与状态、同 Bot 在途去重、失败不重试、过期响应隔离和刷新错误分离；扩展内部/外部公开设置组件测试验证入口、布局、Owner 约束及发布审批回归。
- `test/pages/Workspace/GroupWorkspaceAreaManagePanel.test.tsx` 与真实 workspace Hook 的集成 harness：固定群详情刷新、管理面板打开时同群列表替换及空详情停止补拉。
- `test/hooks/useCollaborationPrivacy.test.tsx` 和真实配置弹窗/共享 Store 集成：POST 后刷新在途时关闭/切 Bot，旧请求忽略 abort 后仍不能写 Store；分别覆盖旧读取成功、失败和 finally。
- 计数测试贯穿 DTO→mapper→GroupAvatarStack，区分缺省、非法与显式 0，不只单测展示 helper。

在 `apps/frontend-nextgen` 运行相关 Jest 测试，再按模块要求运行 `npm run ci`（guardrails、typecheck、lint、全量 Jest、build）。检查所有新增/修改源文件行数；有门禁失败时记录实际原因，不放宽规则。

浏览器验收使用混合 Human/Bot 群，覆盖已保存头像、无头像、失效 URL、同名 Bot、Session-only Bot，检查侧栏两种形态及历史/实时消息。网络面板确认新摘要路径无逐群头像补拉；改名不变默认图，已有刷新后头像清空/更新生效。

身份选择框验收在相同数据下检查当前选中项和候选列表，切换有头像/无头像/图片失败的 Bot，并核对其在 Group/Session 中的默认图。刷新 `mine` 的 URL/null 后，头像应更新而当前身份、选中群和会话不变。保留既有元数据请求预算，不新增头像请求。

**本轮基线核对：** 已 fetch `origin/REL20261105`，从 `bf74bac9141751ddfb00c9773ac553c108c697c9` 创建分支并 cherry-pick 文档；逐一核对计划涉及的前端文件和测试，更新「公开设置」、会话创建入口与头像保存语义。服务端部分仅保留上面的已部署接口合同，不依赖本分支的服务端实现。

**审阅修订（2026-11-05）：** 补充固定入口请求生命周期、管理详情失效重拉、修复后共享 Store 读取隔离、SettingRow 发布审批复用和人数映射边界，对应新增 AC-31 至 AC-35。

**本次文档校验：** `git diff --check` 通过；35 项 AC 唯一性及任务矩阵覆盖、代码围栏、本地链接和既有任务路径检查通过。另对本文计划中的请求 token、零头像补拉、人数回退、管理面板 effect 和读取 cleanup 片段执行无依赖最小逻辑校验，均通过；这些校验使用简化依赖，不是 React 生命周期、Jest、类型检查或浏览器集成测试。

**验证边界：** 本轮只修改文档，未运行前端功能测试、CI 或浏览器联调，未调用修复接口或修改 Bot 数据。用户已确认服务端接口部署完成；后续验证的是新前端对这些接口的消费结果，而非再次验证是否已部署。

## 9. 兼容、风险与后续

服务端契约和持久化无变化；前端只消费增量字段。主要风险是把轻量摘要当完整成员、图片失败状态未重置、旧缓存覆盖新数据，以及按名称错误匹配 Bot；上述模型边界和验收条件分别约束这些风险。

相关接口均已部署，本计划没有等待服务端合并、迁移或发布的前置步骤。服务端临时错误仍需按接口合同处理；旧字段兼容只保护已有测试数据和历史响应形态，不代表目标环境尚未上线。头像刷新与身份切换须区分，避免重置协作上下文。

回滚前端变更恢复旧展示和入口；不安排服务端回滚。实施步骤见同目录 plan.md；本轮不进入业务实现。
