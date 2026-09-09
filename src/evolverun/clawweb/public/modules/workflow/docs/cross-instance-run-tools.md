# 跨实例运行查询与重试参数

API 模式下，多个实例使用同一个 ClawWeb、同一组 BOT_ID / OWNER_ID。

```text
/workflow runs --global --status failed --limit 20
/workflow runs --global --status failed --limit 20 --beforeId <上一页游标>
/workflow inspect <flowId>
/workflow logs <flowId> --nodeId <nodeId> --level error --limit 20
/workflow logs <flowId> --nodeId <nodeId> --level error --limit 20 --afterId <上一页游标>
```

MCP 对应 `workflow_runs(global=true, beforeId=...)`、`workflow_inspect`、`workflow_logs(afterId=...)`。
`workflow_runs` 默认仍查当前会话。`state/debug` 入口共用 inspect。

共享列表按入库 ID 倒序、日志按 ID 正序分页，翻页时保留筛选条件。
日志序号 seq 可能随进程重启重复，不能用作分页游标。
每页最多 50 条；单条日志显示前 2000 字符并提示截断，完整归档仍可通过 inspect 获取。
列表和日志使用运行环境的 bot/owner 范围，MCP 不提供任意 owner 切换。
无归属的旧记录不包含在共享查询中；空日志只表示尚无匹配的已上报日志。
内部接口沿用服务级 Ed25519 签名信任，查询 scope 位于签名的 POST body 中；不构成独立的每 bot 身份认证。

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
新客户端遇到旧查询服务会明确报错，不降级成误导性的空列表。

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
| `POST /run-reads/runs` | Required `botId`, `ownerId`; optional `workflowId`, `status`, `identityKey`, `includeHidden`, `beforeId`, `limit` | `{ items: RunSummary[], nextCursor: number \| null }` |
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
Run summaries omit state/credentials payloads. Logs contain `id`, `node_id`, `level`, `source`,
`message`, `message_length`, and `timestamp`; the message is capped at 2000 characters.
Ownership matches the exact persisted `origin_bot_id = botId + ':' + ownerId`.

Retry options are optional so existing rows remain readable. Consumers without
`options_version: 1` can claim only requests with no execution options. The ClawMind client
checks server capabilities before posting an options-bearing request and verifies the persisted
options in the response; existing open requests retain their original parameters.
