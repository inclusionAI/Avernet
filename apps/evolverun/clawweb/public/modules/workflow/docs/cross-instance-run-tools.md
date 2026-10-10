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

## 发布顺序

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

All paths below are relative to `/api/internal`, behind the host's service signature middleware.
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
All scope uses `getViewByIdsForOwner(userId)` and `resolveRunViewScope(workflowId, userId)` to apply both workflow and per-Bot grants before pagination; empty or absent grants return no records. Bot-specific grants preserve both `botId` and `ownerId` and match the exact `origin_bot_id = botId + ':' + ownerId`, excluding records without a known originating Bot. Only an explicit permission row with owner `*` authorizes all owners of that Bot ID; concrete-owner grants never become a prefix match.
`RunReadRepository(db, permissions)` requires the `RunViewPermissions` contract. The host must create its permission repository with the configured `BotDirectory` and inject that same instance; the run repository does not select or construct a default implementation. In OCB, the internal router uses `repos.botWorkflowPermissionRepo`; without a configured permission service the host does not mount run-read routes. Deploy the matching OCB composition change with this API update. Collaborator access uses the live Bot/owner relationship, and removal, deletion, or ownership changes revoke inherited access.
Session scope prefers exact session ID; rows without an ID can match the session key. No session identity is an error. The runtime requester ID excludes workflow defaults and Bot identity fallbacks.
New clients require an echoed matching scope for session/all, so old servers cannot silently serve a different range. Bot queries remain compatible with old responses.

Retry options are optional so existing rows remain readable. Consumers without
`options_version: 1` can claim only requests with no execution options. The ClawMind client
checks server capabilities before posting an options-bearing request and verifies the persisted
options in the response; existing open requests retain their original parameters.
