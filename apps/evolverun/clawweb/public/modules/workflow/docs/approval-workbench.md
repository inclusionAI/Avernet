# ClawWeb 审批工作台：使用与接口

> 状态：本文描述当前工作区中的实现，而非已部署能力。前端和
> `createApprovalWorkbenchRouter` 已有实现，但截至本次核对，公共宿主
> `public/app/clawweb/server/public-bootstrap.ts` 只挂载原有审批路由，尚未挂载
> 工作台路由。下述路径描述本地实现，不代表已部署能力；各宿主的接入情况
> 与端到端验证需分别确认。

## 使用

在任务护航侧栏选择「工作台」，不需要先选择某个工作流。工作台初始选中
「待审批」，默认查询**登录本人**；切换「已办」查看本人曾同意或驳回的记录。
管理员也默认查看本人，需要点击「启用管理员」进入跨人查询模式：工号留空查询
全部人员，填写工号并点击「查询」则查询指定人员；再次点击「关闭管理员」返回本人。
普通用户不能启用管理员模式，权限始终由服务端验证。可按工作流 ID、创建日期
筛选，已办还可选择全部、已同意或已驳回；「重置」清空筛选，保留当前管理员
模式。列表支持「上一页」「下一页」和每页条数，切换条件后回到第一页；使用游标
分页，不提供总页数或随机跳页。日期输入按浏览器本地时区的自然日计算，结束日期包含当天。

可操作待办行显示「进行审批」，只读或已办行显示「查看详情」；点击后打开侧栏。每行提供「复制链接」，复制服务端返回的
`approvalUrl`；若宿主未配置 `publicBaseUrl`，前端会以当前站点 origin 将相对
路径转换为绝对地址，但不携带工作台页面参数或访问凭证。只有经服务端认证、
且是该卡实际指定审批人的当前
用户，才能对仍可处理的审批进行同意或驳回；管理员查询或查看他人的权限**不**
赋予代审批权限。可处理性还取决于卡片仍待处理、是该运行和节点的最新卡片、
运行状态仍活跃；工作台提交端在事务内要求本人尚未处理，重复决定与改票均返回冲突。
旧审批路径的语义未改变，页面展示不能替代提交时的服务端检查。
查询本人时的「已办」表示个人已操作，不等于整个卡片或工作流已经结束。

管理员查询全部人员时以审批卡为分页单位，同一会签卡如有不同审批人分别已办和待办，
可能同时出现在两个列表；状态列展示匹配当前筛选的审批人及其个人状态。

列表只覆盖 `card-web` 审批卡，不是所有类型审批的总账。已结束运行或被新卡
替代的待办被排除；无法确认运行状态的待办可显示为「待确认」，不应当视为
可审批。无匹配结果不代表不存在其他投递方式、无权查看或尚未接入的数据。

## 查询接口（路由工厂合同）

前端请求 `GET /api/approval/search`；该路径需要宿主将工作台路由挂载到
`/api/approval`，并提供经过验证的用户及管理员身份解析器。身份和角色必须由
可信服务端解析，不能以浏览器传入的工号或自称管理员的请求头作为凭据。

| 参数 | 说明 |
| --- | --- |
| `empId` | 目标审批人工号；不传且未指定 `scope=all` 时使用认证用户。非管理员查询他人返回 403。 |
| `scope` | 显式传 `all` 查询全部审批人；只允许经服务端验证的管理员使用，不能同时传 `empId`。不传时保留原先按本人或指定工号查询的行为。 |
| `workflowId`、`flowId` | 精确筛选工作流 ID、运行 ID；UI 目前只提供工作流 ID。 |
| `personalStatus` | `pending`、`handled`、`approved`、`rejected`；`handled` 包含个人同意及驳回。不传不限制个人状态。 |
| `status` | 卡片整体状态：`pending`、`approved`、`rejected`；与个人状态是两个独立条件，可同时筛选。 |
| `createdFrom`、`createdTo` | 创建时间下限（含）和上限（不含），Unix 秒数字符串或带时区的 ISO 8601 日期时间；上下限同时指定时前者必须小于后者。纯日期不接受。 |
| `limit`、`cursor` | 游标分页；`limit` 默认为 20，范围 1–100。用上次响应的 `nextCursor` 取下一页；更改查询人或筛选条件后不能复用旧游标。 |
| `start`、`end` | 另一种按匹配结果序号取区间的方式，均为包含端点的 1 起整数；必须同时传入，`end` 不超过 10000，每次最多 100 条；不能与 `limit` 或 `cursor` 同用。 |

例如，使用现有登录会话查询本人待办（仅在宿主完成挂载后）：

```http
GET /api/approval/search?personalStatus=pending&limit=20
```

成功返回 `items`、`nextCursor` 和 `page`（`returned`、`hasMore`、`nextCursor`）。
每项包含 `cardId`、`flowId`、`nodeId`、`workflowId`、`workflowTitle`、`message`、
`approverIds`、卡片 `status`、`personalStatus`、`actionability`、ISO 格式
`createdAt` 和 `approvalUrl`。全部人员范围的响应顶层返回 `scope: "all"` 以确认服务端应用了该范围，另有 `matchedApprovers`，列出匹配当前个人状态筛选的工号与个人状态；因为可能同时存在多种个人状态，此范围的顶层 `personalStatus` 为 `null`。卡片仍以单条记录分页。`actionability` 为 `available`、`unknown` 或
`none`，只是查询时的快照；实际审批仍以服务端复核为准。列表按创建时间、卡片
ID 倒序，先按个人和条件过滤再分页。`approvalUrl` 使用宿主配置的
`publicBaseUrl`，未配置时是 `/approval/<cardId>` 相对路径。

认证缺失返回 401；越权查他人返回 403；参数或游标无效返回 400；查询服务失败
返回 503。响应设置 `Cache-Control: no-store`。列表匹配会去除工号首部零后精确
核对，但存储层先用子串筛选候选，数据范围及全部投递方式需要另行核对，不能将空列表视为全渠道完整性保证。

## 详情与审批（路由工厂合同）

在同一挂载前提下，`GET /api/approval/workbench/:id` 只允许实际审批人或
管理员查看详情，并返回 `canAct`。`POST /api/approval/workbench/:id/resolve`
的 JSON 请求体使用 `action: "approve" | "reject"`，可附字符串 `comment`
（UTF-8 最多 10000 字节）和对象 `detail`（序列化后最多 64000 字节）。
服务端从认证上下文取得操作人，不信任请求中的工号。提交要求配置
`publicBaseUrl`，且 `Origin` 与其 origin 一致；若提供 `Sec-Fetch-Site`，
必须为 `same-origin`。缺失身份、非审批人、卡片不存在或状态冲突分别返回
401、403、404 或 409；服务不可用或结果未确认返回 503，应刷新后确认，
不要假定失败或成功。详情和动作响应同样禁止缓存。

## 内部接口与后续 Skill 委托的限制

路由工厂可在宿主显式设置 `allowPost: true` 时提供同语义的 `POST /search`
（JSON 参数）；`allowGet` 可关闭 GET。具体挂载方式和认证机制由宿主决定，
路由工厂本身不实现服务间身份委托。

后续 Skill/Bot 代用户查询仍需由宿主实现并验证服务间认证、用户身份及管理员
角色的可信委托、权限边界和部署挂载，再提供实际调用方式与端到端验证；当前
不能宣称 Skill 已可安全调用，也不能让 Skill 用任意 `empId` 或自报管理员角色
绕过个人范围。本文不承诺 Skill 代审批能力。

参考实现：`server/routes/approval-workbench.ts`、
`server/services/approval-workbench.ts`、`server/repositories/approval-workbench-store.ts`
及 `web/components/workflow-workspace/ApprovalWorkbench.tsx`；身份与旧审批流程见
[dingtalk-approval-auth.md](dingtalk-approval-auth.md)。

## 发布状态

此功能仍处于开发中，尚须完成评审与端到端验收；在此之前不应作为已交付能力使用。
