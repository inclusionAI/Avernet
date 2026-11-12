# Group Context 前端方案草案

前端实现范围为 `apps/frontend-nextgen`。

## 一、群聊共享信息

## 交互

- 在群聊会话顶栏的历史消息与资源管理之间增加「共享信息」图标，点击打开右侧只读面板。复用现有顶栏按钮和面板切换方式。
- 打开面板后加载可读条目，按「群级 → 会话级 → 轮次级」分组。分组标题保留截图中的可见条目数。每张卡片直接展示名称、正文和「xxx 更新于 xxx」。本期省略顶栏按钮数字角标和卡片右上角「只读」标签。
- 群级使用当前 Group，会话级和轮次级使用当前 Session。轮次级展示当前 Session 所有轮次的可读条目，按轮次标识区分同名条目。面板处理加载、空列表和加载失败。
- 切换会话或协作身份时，清理上一视角的卡片，并丢弃迟到响应。重新打开面板时重新请求数据。

## 读取规则

| 当前协作身份 | 显示范围 |
| --- | --- |
| Bot | 当前 Bot 有读权限的上下文 |
| Human 有符合成员资格的受管 Bot | 该 Human 本人及其管理且在当前 Group 或当前 Session 内的 Bot 可读上下文的并集 |
| Human 没有符合成员资格的受管 Bot | 当前范围内全员可见的上下文，以及该 Human 本人可读的定向条目 |

并集按 `(scope_level, scope, name)` 去重，跨作用域的同名条目分别展示。仅临时加入当前 Session 的受管 Bot 计入并集，按它自身在各作用域的实际读取权限计算。全员可见对应 `visible_to.userIds` 为空。读权限沿用 Group Context 的现有规则，包括条目 owner 的读取权限。服务端从已认证身份取 Human 的 `human_<staff_no>` 和 Bot 的 `bot_uuid`，两种标识都沿用现有字符串匹配。Human 本人的读权限在有无合格 Bot 的情况下均生效，且只用于 Human 视角。

## 接口和数据

- 新增 BCS 共享信息专用只读接口，接收 `groupId`、`sessionId`，直接返回卡片所需的名称、作用域、正文、最后更新人和更新时间。服务端验证会话归属、调用身份、Bot 管理权限和成员资格。BCS 没有独立的轮次实体。轮次级查询按 `scope_level = round` 和现有 `{sessionId}:` 字面前缀匹配，要求冒号后有轮次标识。查询使用已校验的当前 Session ID，避免混入 ID 前缀相近的其他会话。
- 当前 `/groupcontext/list` 基于单一 actor，且会返回只有写权限的条目。当前 `/groupcontext/get` 同样以单一 actor 读取，只有条目 owner 能看到权限配置。服务端复用已有权限判断和数据查询，并按请求解析可用的 Bot 集合，再执行过滤。浏览器仅接收可读结果。
- 最后更新人取当前版本的 `creator`。更新会写入新版本并将实际写入者记为该版本的 `creator`，所以可直接复用。界面优先展示可解析的人或 Bot 名称，无法解析时展示身份标识。时间使用当前版本的 `gmt_modified`。
- 使用现有 Group Context 表、版本数据和权限配置。沿用前端现有会话 API 的认证链路，前端只在 `apps/frontend-nextgen` 接入。专用接口在服务端记录独立的请求指标与错误分类，区分参数、权限和存储错误，便于定位问题。

## 涉及组件

- `apps/frontend-nextgen/src/pages/Workspace/components/GroupHeader.tsx:261` 在历史消息与资源管理之间接入入口。
- `apps/frontend-nextgen/src/pages/Workspace/GroupWorkspaceArea.tsx:254` 挂载共享信息面板。请求绑定当前会话和协作身份。
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:295` 复用读取权限逻辑。
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:283` 将写入者记录为新版本的创建者。
- `apps/bcs/crates/contracts/bcs-domain/src/group_context.rs:51` 沿用全员可见的定义。
- `apps/bcs/crates/adapters/http/bcs-http/src/routes/caller.rs:45` 定义现有 Human 与 Bot 的 Group Context actor 标识。
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/permissions.rs:11` 对同一 actor 标识复用现有读权限判断。

## 验证

- 覆盖三种协作身份、受管的临时 Session 成员、可读并集去重、仅有写权限、当前 Session 的多个轮次、ID 前缀相近的其他会话轮次和切换身份期间返回的旧请求。
- 验证 Human 有或没有合格 Bot 时均能看到仅授权给本人的条目。切换至 Bot 身份后，只有该 Bot 本身可读的条目会出现。
- 验证初次创建及后续由其他身份更新时，卡片展示当前版本的实际写入者和更新时间。
- 对照截图验证按钮位置、面板开合、三个作用域的可见数量、卡片正文和加载状态。

## 二、群管理 · 群规则

- 在现有群管理抽屉的 `GroupManagePanel.tsx:96` 加「群规则」页签。进入时加载抽屉当前群的 preset 列表，每项展示名称和描述。
- 采用逐条管理。支持新增、编辑和删除，表单只有名称和描述，名称必填，描述可空。删除前确认，写入成功后刷新当前群列表。名称区分大小写，在群内唯一。重命名或删除 preset 后，已有上下文保留原名和内容。
- 使用群主 Bot 的 Owner 或 Manager 资格控制新增、编辑和删除入口，写入使用 Human 身份，由 BCS 做最终鉴权。列表沿用当前调用身份。仅临时加入 Session 的 Bot 查询时带上同群的 `sessionId`。切换群或身份后清空旧数据，忽略迟到响应。
- 说明文案描述名称和用途的预定义。采用截图中的抽屉与页签位置，列表和编辑表单按 preset 的两个字段设计。

### 接口

复用 BCS 在 `apps/bcs/crates/adapters/http/bcs-http/src/router.rs:268` 注册的四个 POST 接口。前端通过现有认证链路接入，具体转发路径在实现时核对。

| 接口 | 输入与返回 |
| --- | --- |
| `/groupcontext/manage/list-preset` | Human 使用 `groupId` 查询，返回 `presets` 和 `can_manage` |
| `/groupcontext/list-preset` | 查询参数 `groupId`，临时 Session 成员 Bot 另传 `sessionId`，读取返回的 `presets` |
| `/groupcontext/manage/add-preset` | 查询参数 `groupId`，请求体 `name` 和可选的 `description` |
| `/groupcontext/manage/update-preset` | 查询参数 `groupId`，请求体传原 `name` 和有改动的 `new_name`、`description` |
| `/groupcontext/manage/delete-preset` | 查询参数 `groupId`，请求体传当前 `name` |

### 验证

- 覆盖列表、空列表、增删改、重名、清空描述、无写权限、请求失败与切换群。分别验证临时 Session 成员 Bot 列表请求和具备权限的 Human 写入请求。

## 三、群管理 · 共享信息

- 在 `apps/frontend-nextgen/src/pages/Workspace/components/ManagePanel/GroupManagePanel.tsx:96` 的「群规则」之后增加「共享信息」页签。进入时加载抽屉当前群的群级 Group Context 实例。卡片复用群聊共享信息的展示方式，包含名称、正文和「xxx 更新于 xxx」。本期为只读展示。
- 群管理页签按当前已认证 Human 本人的读权限过滤。返回全员可见条目、明确授权给该 Human 的条目，以及该 Human 作为条目 owner 可读的条目。受管 Bot 的读权限只参与群聊顶栏的共享信息视角。
- 复用第一项的 BCS 专用只读查询和卡片响应。群管理页签只传 `groupId`，由服务端校验当前 Human 对该群的访问资格，限定 `scope_level = group` 并按本人身份过滤。群聊入口带 `sessionId`，沿用第一项的会话范围和权限规则。
- 验证全员可见、Human 本人授权、仅受管 Bot 可读、仅可写、会话级和轮次级同名条目，以及切换群后旧请求返回的情况。

## 浏览器接口联调草案

前端客户端集中在 `src/services/backendApi/collaboration/groupContextController.ts`。暂按 Gateway 登录态与 `/api/v1/collaboration/groupcontext/<operation>` 接入。Bot 视角发送 `view_bot_id`，由服务端核验身份并据此解析调用者。群管理共享信息只传 `groupId`，使用当前登录 Human 的身份。

- `shared-info` 返回 `items`。每项包含 `name`、`scope_level`、`scope`、`content`、`creator`、`gmt_modified`。
- `list-preset` 返回 `presets`。Human 页面使用 `manage/list-preset` 获取 `can_manage`，据此显示写入入口。需要联调确认浏览器接口的路径、响应形态和该权限字段的提供方式。BCS 写接口继续校验群主 Bot 的 Owner 或 Manager 权限。
