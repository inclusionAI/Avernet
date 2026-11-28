# 运行查询范围、分页与跨实例重试

API 模式下，运行查询优先从 ClawWeb 读取；当前 session 查询保留原有本地查询作为兜底：

| 命令 | 范围 |
| --- | --- |
| `runs [workflowId]` | 当前 Bot 发起的运行记录，包含它的所有 session。 |
| `runs [workflowId] --session` | 仅当前 Bot、当前 session 发起的运行记录。 |
| `runs [workflowId] --all` | 跨 Bot、跨 session，查询当前用户有权查看的所有 workflow 的运行记录；权限由服务端判断。 |

默认用 `runs`；用户限定“本次对话”时用 `--session`；要求“跨 Bot”“所有 Bot”或“我能查看的全部运行记录”时用 `--all`。
`flows` 是 `runs` 的兼容别名。`runs <workflowId>` 与 `--workflowId <workflowId>` 均可用，显式参数优先。
`--global` 仅保留旧语义：API 模式等同当前 Bot，不能理解为跨 Bot，也不能与新范围参数混用。
本地模式保留原行为：默认与 `--session` 查本地当前会话，`--global` 查本实例 registry；`--all` 和游标分页需要 API。

```text
/workflow runs tech-research-v2
/workflow runs tech-research-v2 --session
/workflow runs --all --status failed --limit 20
/workflow inspect <flowId>
/workflow logs <flowId> --nodeId <nodeId> --level error --limit 20
```

MCP 使用 `workflow_runs(scope="bot" | "session" | "all", workflowId=..., beforeId=...)`，默认 `scope="bot"`。
用户身份来自运行时请求上下文，不接受工具参数指定其他用户。workflow 固定用户和 Bot ID 不用于 `--all` 授权；无法确认当前用户时明确报错。
服务端在排序和分页前应用 session 或 workflow 查看权限（包括仅能查看指定 Bot 运行的授权），不从取到的一页中再做权限过滤。`--all` 的权限信息缺失时不会放开查询。

## 分页和列表标记

CLI 与 MCP 都默认每页 20 条，最多 50 条。运行记录按入库 ID 倒序，用 `beforeId` 游标继续查询。
每次返回本页数量、来源 Bot、当前 session 标记，以及是否还有更多记录。
有下一页时返回完整命令，自动保留范围、workflow、状态、identity、隐藏记录选项和每页条数，例如：

```text
runs --all --workflowId "tech-research-v2" --status "failed" --limit 20 --beforeId 123
```

用户说“下一页”时，Agent 执行上次返回的命令；无需用户记忆游标或重填筛选条件。这里不新增 `runs next` 命令，也不依赖进程内分页状态。
新增记录不会推移后续页；权限与状态变化会在每次查询时重新生效。日志按 ID 正序，用 `afterId` 翻页；seq 可能随重启重复，不能作游标。

「当前 session」先核对 Bot，再优先比较来源 session ID；无法比较 ID 时比较 session key。
相同 key 但 session ID 已变化的旧运行不标记为当前 session，不同 Bot 使用相同 key 也不算同一 session。
旧服务或记录缺少来源字段时，只针对当前页逐条尝试 session 绑定的查询来确认匹配；无法确认标记「未知」，不读取全部本地历史。
本地归属标记查询失败不影响 API 列表。当前 Bot 和 `--all` 查询在 API 失败时报错。
`--session` 在 API 失败、API 配置或 session 查询身份缺失、旧服务不支持范围，或首屏返回空记录时，调用原有 `boundTaskFlow.list()` 查询本地 session，保留 workflow、状态、identity、隐藏记录和条数筛选。结果明确标注「本地 session 兜底」和原因；本地查询也失败时报告错误。
正常翻页到空页表示查询结束，不触发本地兜底。翻页请求失败时，本地结果从头展示并提示可能重复，不沿用 API 的 `beforeId`；本地兜底沿用原有条数限制，不提供 API 游标分页。
`--session` / `--all` 要求服务端回传生效的范围；旧服务忽略新参数时，`--session` 使用本地兜底，`--all` 明确要求升级。

单条日志显示前 2000 字符并提示截断。`state/debug` 共用 inspect。
默认 Bot 范围及日志仍按运行环境 bot/owner 查询，无归属旧记录不包含在内；`--all` 按 workflow 查看权限查询；只有该 workflow 的所有 Bot 查看权限才包含来源 Bot 缺失的旧记录。
内部接口沿用服务级 Ed25519 签名信任，身份和范围位于签名 POST body 中，不构成独立的终端用户登录认证。

## 日志写入长度保护

`@avernet/workflow` 的 `RunLogRepository.insertBatch` 在构造 SQL 参数时，将每条 `message` 限制为最多 65,535 个 UTF-8 字节，包含末尾的 `\n...[truncated]` 标记。未超限消息保持原样；超长消息保留完整 Unicode 码点组成的前缀，不拆开中文字符或 emoji 的代理对，也不修改调用方的数组或条目。截断后的尾部不会另存，不能通过日志翻页恢复。

这是最终写库保护，与查询时显示前 2000 字符的限制不同。即使客户端没有做长度保护，调用 `/api/internal/run-logs/batch` 的正常日志写入也会经过此处。该行为不修改表结构、请求字段或成功响应格式。

OCB 的内部 ClawWeb Host 从 `@avernet/workflow` 导入此 Repository 和路由，不维护另一份限长实现。发布 OCB 时须确保其构建引用包含此修复的 Avernet 版本，并重新构建、部署服务；仅修改工作区源码或升级客户端不代表服务端已生效。此保护不修复上游损坏的 workflow state，也不能代替实际数据库列类型及错误列名的核验。


## 发布顺序

本节原有顺序描述查询/重试邮箱能力依赖；**不能据此单独先发当前整版 server**。
本版同时移除 4505/服务端业务超时校验，旧 120 分钟误判 writer 会真实写成 failed。
必须先暂停/隔离错误旧 writer 并处理在途/排队请求，再发 server/schema 契约，
升级验证 Mind 和遗留 TaskGuard，最后恢复写入。不能隔离时不得单独发布去校验版。
Nginx 应急过滤默认 off；本版没有可保留旧预算校验的内置 runtime feature flag。
详见 [发布门禁](../../../../docs/workflow-timeout-write-policy.md#release-gate-and-rollback)。
以下步骤都在该隔离窗口内执行：

1. Avernet ClawWeb Shared 数据库迁移 v119：增加 `flow_retry_requests.options_json`，增加共享列表查询索引。
2. 发布 Avernet workflow 模块和 OCB Host 接线，再更新所有 ClawMind 实例（本地库迁移 v38）。
3. 用 A 实例创建的 flow，在 B 实例执行上面的列表、详情和日志查询。

托管 MySQL 若没有自动 DDL 权限，先由数据库发布流程执行：

```sql
ALTER TABLE flow_retry_requests ADD COLUMN options_json TEXT;
CREATE INDEX idx_flow_runs_origin_id ON flow_runs (origin_bot_id, id);
```

新增内部接口为 `POST /api/internal/run-reads/runs`、`POST /api/internal/run-reads/logs`。
新客户端遇到旧查询服务时，当前 session 按上述规则使用本地兜底；其他范围明确报错。

重试邮箱完整保存 `useCurrentDef`、`debug`、`inputOverrides`。
带选项的请求发布前会检查服务能力，旧执行实例不能领取带选项的请求。
因此滚动升级期间，可能需要等待持有该 flow 的实例升级后才能执行重试。
原实例离线接管、跨实例 recent_events 和状态镜像可靠性增强不在本批范围内。

## Source ownership

Run reads and the retry mailbox are owned by `@avernet/workflow`; schema migrations are in `@avernet/clawweb-shared`. OCB only composes the routers behind its existing internal signature middleware. The ClawMind engine remains in the separate ClawMind repository; Avernet taskguard is unchanged by this migration.

## Internal API contract

For creation-only `parent_flow_id` / `root_flow_id`, exact readback, callback
placement, and schema v157 rollout, see [flow run lineage](flow-run-lineage.md).
These fields do not change the separate scoped run-read summary contract below.

All paths below are relative to `/api/internal`, behind the host's service signature middleware.
`POST /runs` accepts optional `state_json` as a serialized JSON object. When present, the run and its initial state are inserted in one SQL statement. Clients that omit the field remain compatible; existing runs with null state are unchanged.
The server must understand this field before clients use it, but that additive
API dependency does not waive the isolation-first release gate above.
Read requests use POST so their scope and filters are covered by the existing body signature.

| Endpoint | Request body | Response `data` |
|---|---|---|
| `POST /run-reads/runs` | Required `botId`, `ownerId`; optional `scope` (bot/session/all, default bot), `workflowId`, `status`, `identityKey`, `includeHidden`, `beforeId`, `limit`; session scope requires `sessionId` or `sessionKey`; all scope requires runtime `userId` | `{ items: RunSummary[], nextCursor: number \| null, scope: "bot" \| "session" \| "all" }` |
| `POST /run-reads/logs` | Required `botId`, `ownerId`, `flowId`; optional `nodeId`, `level`, `afterId`, `limit` | `{ items: RunLog[], nextCursor: number \| null }` |
| `GET /retry-requests/capabilities` | None | `{ executionOptionsVersion: 1 }` |
| `POST /retry-requests` | Existing fields plus optional `options: { useCurrentDef?: boolean, debug?: boolean, inputOverrides?: Record<string,string> }` | Existing retry row, including nullable `options_json` |
| `POST /retry-requests/claim` | Existing fields plus optional `options_version: 1` | Existing `{ claimed: RetryRequest[] }`, each including `options_json` |

Success envelope: `{ success: true, data: ... }`. Read errors return
`{ success: false, message: string }`: 400 for invalid scope/filters/cursors,
404 for a log flow outside the requested ownership scope, and 500 for a database failure.
Missing/invalid signatures are rejected by the host before querying repositories.

`limit` defaults to 20 and is restricted to integers 1–50. Cursors are nonnegative safe integers.
Runs are ordered by descending database ID and use an exclusive `beforeId`; logs use ascending
ID and an exclusive `afterId`. `nextCursor: null` ends the current filtered result set.
Run summaries include nullable `origin_session_key` and `origin_session_id` alongside
`id`, `flow_id`, `workflow_id`, `status`, `origin_bot_id`, `identity_key`, `started_at`, and `gmt_modified`.
These identify the originating session; the client compares them with its current session context.
Run summaries omit state/credentials payloads. Logs contain `id`, `node_id`, `level`, `source`,
`message`, `message_length`, and `timestamp`; the message is capped at 2000 characters.
Bot/session ownership matches the exact persisted `origin_bot_id = botId + ':' + ownerId`.
All scope uses `listRunViewScopes(userId, workflowId?)` as its single permission gate. It resolves direct, wildcard-owner, and inherited grants together; `can_view=1` or `can_edit=1` makes a grant readable. Empty grants return no records. Direct permissions are loaded in one query, and the Bot directory is consulted once per request; it does not re-query permissions for each workflow. Bot-specific grants preserve both `botId` and `ownerId` and match the exact `origin_bot_id = botId + ':' + ownerId`, excluding records without a known originating Bot. Only an explicit permission row with owner `*` authorizes all owners of that Bot ID; concrete-owner grants never become a prefix match.
Run queries apply at most 100 grant predicates per SQL batch to bound expression depth and placeholders, including when one workflow grants many Bots. Each batch applies every filter and the exclusive ID cursor before reading at most `limit+1` rows. Results are deduplicated by ID and merged in descending ID order, retaining only the global top `limit+1` rows; the returned cursor belongs to the combined result.
`RunReadRepository(db, permissions)` requires the `RunViewPermissions` contract. The host must create its permission repository with the configured `BotDirectory` and inject that same instance; the run repository does not select or construct a default implementation. In OCB, the internal router uses `repos.botWorkflowPermissionRepo`; without a configured permission service the host does not mount run-read routes. Deploy the matching OCB composition change with this API update. Collaborator access uses the live Bot/owner relationship, and removal, deletion, or ownership changes revoke inherited access.
Session scope prefers exact session ID; rows without an ID can match the session key. No session identity is an error. The runtime requester ID excludes workflow defaults and Bot identity fallbacks.
New clients require an echoed matching scope for session/all, so old servers cannot silently serve a different range. Bot queries remain compatible with old responses.

Retry options are optional so existing rows remain readable. Consumers without
`options_version: 1` can claim only requests with no execution options. The ClawMind client
checks server capabilities before posting an options-bearing request and verifies the persisted
options in the response; existing open requests retain their original parameters.

## 用户中止与重试约束

`cancelled`（兼容历史 `canceled`）是用户中止的权威终态，不是可自动恢复的普通失败。正常 `failed` 运行仍可按既有策略重试；需要重新开展已中止的业务时，应显式创建新运行。

- 迟到的 completion、status 和 retry-resume 写不能重新打开已中止运行；取消保护同样适用于不提供 `retry_epoch` 的旧客户端。
- `POST /retry-requests` 对已中止运行返回 **409**，即使邮箱已有 pending/claimed 请求也不能以去重成功绕过取消。`POST /retry-requests/claim` 在候选查询和实际 CAS 领取时均排除已中止运行，包含过期 claimed 请求。
- `PUT /runs/:flowId/status`、`PUT /runs/:flowId/retry-failed`、`PUT /runs/:flowId/retry-resume` 不得用重试撤销中止，取消冲突返回 **409**。`retry-failed` 已废弃：已有运行始终 **409**，引导协调 `retry-resume`；只读检查，不重置节点也不修改运行。缺失运行仍 **404**，无数据库 **503**，读取异常 **500**。status 分类读取的数据库故障仍返回 **500**，不得误报为记录不存在。
- 人工重试必须使用原执行状态捕获的 `retry_epoch + 1` 调用 `retry-resume`；不得读共享最新 epoch 自动放行旧请求。默认选择 failed/blocked/waiting 节点；显式节点 ID 可重跑已成功片段，但必须进入新代次。`status running` 不能重新打开任何终态（包括 epoch 0）。`retry-resume` 保留原始开始时间及节点历史，同轮 running 响应丢失重放不再次写入。
- `retry-resume` 可选 `state_json` 必须是含匹配下一代 `retryEpoch`（TaskGuard）或 `recoveryEpoch`（Mind）的对象 JSON 字符串；至少有一种，两者出现须一致，坏值不得 fallback；显式 `workflowId` 须匹配原运行。与 epoch+1 同事务、单次 UPDATE 持久化 canonical state 和已有 `params_json._clawmind_state`，保留 embedded 的字符串/对象形态及其他参数。非法请求 **400**，保存的 checkpoint/embedded 内容或代次矛盾 **409**，DB 异常 **500** 全部回滚；不得回退无 fence 的 `/state`。同代 running 重放只读，不覆盖后续 checkpoint；省略 state 保持 Mind 既有兼容行为。
- `/state` 使用 `{ state_json, retry_epoch? }`，`/phase` 使用 `{ phase, retry_epoch? }`。省略仅表示 epoch 0；正代次 state 必须显式携带匹配的 `retryEpoch`/`recoveryEpoch`。事务锁内匹配原执行代次，终态只允许同值只读确认；旧代/不同值 **409**。state 镜像把 fanout 可信合并后的结果同步到已有 embedded checkpoint（保留字符串/对象形态），两存储同事务回滚，避免第二次 retry 因合法 progress 镜像产生双存分歧。
- `/result-json` 与 `/started-at` 同样废弃，route 和直接 repo 均只读：合法已有运行 **409**、缺失 **404**、无 DB **503**、读错 **500**，不改 result/started_at/completed_at、不触 observer。final result 只归 fenced completion，retry-resume 保留原 start；Mind/TaskGuard 必须去掉重试依赖旧 resetStartedAt 的调用，不能靠它重置时钟。
- 已入邮箱、已领取或已保存为 retry intent 都不是执行授权。取消发生在领取之后时，ClawMind 仍需在实际尝试、Guardian 救援及重启恢复前重新检查权威状态，并结束已取消请求而不是反复 release；回调的实际发送和网络重投也需要取消检查。

上述服务端保护需与匹配的 ClawMind 取消检查和信号接线一起发布。节点截止或 watchdog 请求 abort 不自动等同于用户中止。中止不会撤销已发生的外部副作用，也不保证已交付引擎的工具或模型请求立即停止。

回归入口：`server/repositories/__tests__/run-cancellation.test.ts`、`flow-retry-cancellation-race.test.ts` 及 `server/routes/internal/__tests__/cancellation.test.ts`。本地双连接 SQLite 竞态测试不替代目标 MySQL/ZDAS 环境的并发与部署验证。
