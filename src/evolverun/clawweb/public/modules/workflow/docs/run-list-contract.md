# Run list HTTP contract

Owner: `@avernet/workflow`. Route: `GET /runs` relative to the host API mount
(normally `GET /api/runs`). This is a read-only Service API, not an engine
Plugin API. The optional `query` parameter is an additive, backward-compatible
extension; the response envelope and existing filters are unchanged.

## Keyword search

`query` accepts a single string. Leading and trailing whitespace is removed.
Omitted, empty, whitespace-only, repeated (array), or structured values do not
activate keyword filtering. Clients should send one URL-encoded string.

An active keyword performs a literal substring match against any of:

- `flow_id` (Run ID);
- `triggered_by` (initiator);
- `origin_bot_id` (stored Bot identity, including the owner suffix when present).

It does not search input JSON or node logs. `%`, `_`, and `!` are escaped as
literal characters rather than LIKE patterns. Values are SQL-bound parameters.
Case sensitivity follows the database column collation; portable clients must
not rely on a particular case-folding behavior.

## Composition with existing filters

The keyword's three field matches are grouped with OR. That group is combined
with AND against `workflowId`, `status`/`statuses`, `inputQuery`, `from`/`to`,
the existing origin-Bot scope, and workflow view permissions.

- A non-empty comma-separated `statuses` list takes precedence over `status`.
- `inputQuery` retains its separate existing input-JSON LIKE semantics.
- `from` and `to` retain inclusive `started_at` bounds and accept epoch seconds,
  epoch milliseconds, or ISO timestamps. Omitting them searches all history;
  the run list does not inherit the dashboard's metric window.
- `botOwnerId`/`botId` retain their existing origin-scope behavior. The keyword
  does not grant access or override the host's authorization decision.
- Non-admin workflow visibility is applied before counts, ordering, and
  pagination. An empty allowed-workflow scope yields no rows or counts.
  Administrators retain their existing unrestricted view.

Results are ordered by `started_at DESC`. `limit` (default 30, maximum 2000)
and `offset` (default 0) select a page of matching, authorized records.
Records with equal start times have no additional guaranteed order.

## Response compatibility

The JSON envelope remains `{ runs, total, limit, offset, statusCounts? }`.
`total` is the number of authorized matches **before** pagination; `runs`
contains only the requested page. A no-match result is HTTP 200 with
`runs: []` and `total: 0`. Hidden matches must not affect either field.

`statusCounts` is present only when `status`, non-empty `statuses`, non-empty
`inputQuery`, and active `query` are all absent. When present, it observes the
same workflow, time, origin, and permission scope as the list. Searching
clients must not interpret an omitted `statusCounts` as real zero metrics;
dashboard metrics use their independent unfiltered query.

Examples:

```http
GET /api/runs?workflowId=example&status=failed&query=bot_123&limit=20&offset=0
GET /api/runs?workflowId=example&statuses=cancelled,canceled&query=gateway
GET /api/runs?workflowId=example&query=%20run-123%20
```

## Compatibility tests

`server/repositories/__tests__/run-list-search.test.ts` exercises both the real
SQLite repository and the Express HTTP route. Coverage includes normalization,
non-scalar/omitted parameters, unchanged response shape, filter intersections,
status-list precedence, literal wildcards, permission-scoped counts and rows,
pagination, empty permissions, and the admin view. Run it with:

```sh
npm test --workspace @avernet/workflow -- server/repositories/__tests__/run-list-search.test.ts
```
