# V1 Group list: sort by conversation last activity (design)

## Record

- Work item: [2026080400118003354](https://project.alipay.com/workItem?workItemId=2026080400118003354) — 群组列表支持按会话最近活跃时间排序
- Owner: BCS. Scope: public Avernet BCS only.
- Branch: `doc/dima-2026080400118003354` (documents only; implementation lands on its own branch later).
- Status: proposed design, awaiting requester review. Every call marked "executor's
  call" below is a guess the requester can veto; it is not something they agreed to.

## Intent and why

The workspace conversation list (frontend-nextgen) is a WeChat-style list of
groups; users expect the group with the newest conversation activity on top.
Today the V1 group list has a fixed `created_at DESC, group_id ASC` ordering
(`api-contracts/v1/openapi/groups.yaml`), and the frontend simulates a
"lastActivity" sort client-side by mapping `lastMessageAt` from
`GroupSummary.updated_at`
(`apps/frontend-nextgen/src/services/workspace/mappers.ts`). Group `updated_at`
moves only on group mutations (rename, visibility, members, version) — never on
message traffic (`bcs-group-store` message handling does not touch
`bcs_groups`), so the current "activity" order is wrong, and the client-side
sort can only order one fetched page, not the whole list.

This change makes the V1 group list able to order by the conversation's real
most-recent activity, and exposes the activity timestamp so clients can render
and sort correctly across pagination.

## Goals, non-goals, anti-goals

Goals:

- `GET /openapi/v1/collaboration/groups` accepts an optional sort selector and
  can order the full filtered result set by last conversation activity
  (descending) before pagination.
- Group summaries carry the conversation's last-activity timestamp.
- Existing default behavior is byte-compatible: callers that pass no sort
  parameter keep today's ordering and response semantics (plus one additive
  field).

Non-goals:

- Legacy HTTP surfaces (`GET /groups`, `/groups/my`, `/bots/{id}/groups`) keep
  their `updated_at`-based ordering. They serve CLI/internal callers; the
  product conversation list consumes V1. (Executor's call — trivially
  extendable later by the same vertical if the requester wants it.)
- `GET /openapi/v1/collaboration/public-groups` (discovery catalog) keeps
  `updated_at DESC`.
- Session list endpoints are unchanged.
- No frontend-nextgen change in this work item; the contract addition is what
  the frontend needs to switch its sort key (follow-up consumer task).
- No schema migration, no denormalized `last_activity_at` column, no change to
  the message write path.

Anti-goals (failure even if "it works"):

- A per-group activity query (N+1) or a full-table aggregate scan on the list
  hot path.
- Changing the default ordering of any existing endpoint.
- Sorting after pagination (page-local sort that lies about global order).

## Current state

- V1 `GroupService.list_groups` (`crates/application/v1/bcs-app-group/src/service.rs`)
  collects all of the view actor's groups (direct membership, plus the
  session-only union via `SessionManagementService`), filters in memory, sorts
  with `DomainGroup::cmp_by_created_at_desc_group_id_asc`, then paginates, then
  projects summaries. Sorting is already global-before-pagination; swapping the
  sort key is a local change.
- Conversation messages are persisted to `bcs_messages` (`group_id`,
  `created_at` epoch ms) by the delivery pipeline via `MessageRepoPort`
  (`crates/services/bcs-message-store/src/delivery.rs`, `mysql.rs`), independent
  of the `store_messages` legacy toggle. Index `idx_group_created(group_id,
  created_at)` exists in both dialects.
- No endpoint, repo port, or DTO carries a group-level activity timestamp or a
  sort parameter today; ordering is a fixed contract per endpoint, declared in
  the OpenAPI response description.
- Layering precedent: V1 application facades consume application-level history
  services (`SessionServiceImpl` already takes
  `Arc<dyn GroupMessageHistoryService>`); repo ports are not held by V1 facades
  directly.
- `MessageRepoPort` and `GroupMessageHistoryService` both use defaulted trait
  methods, so additive methods do not break noop/test implementations.

## Design decisions

### D1 — Activity semantics (executor's call)

`last_activity_at` = max `created_at` (epoch ms) of persisted messages in
`bcs_messages` for the group, considering every message row regardless of
sender, message type, or visibility audience (activity happened even if this
viewer could not read it); the repo store applies its own `env` isolation as
usual. A group with no persisted messages falls back to its own `created_at`,
so the field is always populated and ordering is total. Rationale: the message
log is the only signal that is honest on both dialects — `bcs_group_sessions.gmt_modified`
auto-bumps on message append only on MySQL (SQLite has no `ON UPDATE`) and also
moves on non-message session mutations.

Caveat accepted by design: history reads are cut over to the persisted log by
`message_history.cutoff_timestamp`; groups whose only traffic predates the
cutover have no rows in `bcs_messages` and therefore report their `created_at`.
Activity means "activity in the persisted message log", which is exactly what
post-cutover clients can also read back via history. Reconstructing activity
from the legacy bot-delivery fallback is rejected — it would couple a read
model to delivery internals for a shrinking set of pre-cutoff groups.

### D2 — Read-time aggregate, no persistence change (executor's call)

Compute activity at list time; do not add a column or bump writes on the
message hot path. One grouped query per list call:

```sql
SELECT group_id, MAX(created_at) FROM bcs_messages
WHERE env = ? AND group_id IN (…) GROUP BY group_id
```

Each group resolves through `idx_group_created` (index range max). Trade-off:
reads pay one bounded aggregate query; writes stay untouched and both dialects
behave identically. Revisit a denormalized `bcs_groups.last_activity_at` only if
measurement shows this query hot (very large per-actor group counts or very
large message tables per group).

### D3 — API shape: additive `sort` param, default unchanged (executor's call)

V1 `GET /groups` gains optional query param `sort`:

- `created_at` (default): today's `created_at DESC, group_id ASC`.
- `last_activity`: `last_activity_at DESC, group_id ASC` (fallback `created_at`
  from D1 participates in the key).

An explicit opt-in param keeps the existing contract byte-compatible while
satisfying "支持按会话最近活跃时间排序"; the repo has no sort-param precedent,
and fixed-per-endpoint ordering is the convention — the default therefore stays.
Unknown values are rejected 400 by the adapter's enum deserialization, matching
existing enum query params (`membership`, `kind`).

### D4 — Summary field (executor's call)

`NormalGroupSummary` and `DirectMessageGroupSummary` both gain
`last_activity_at: Option<u64>` (epoch ms), serialized with the existing
`skip_serializing_if = "Option::is_none"` convention. The models are shared by
`list_groups` and `list_public_groups`, so population is per-endpoint, not
per-type:

- `list_groups`: always `Some`, from the same batched lookup used for sorting
  (the D1 `created_at` fallback applies), independent of the requested sort —
  clients render relative-time labels without a second pass, and `kind=dm`
  behaves identically (DM conversations live in the same message table).
- `list_public_groups`: always omitted (null). The discovery catalog keeps its
  exact current behavior — no extra read on that path, and no "active 2 minutes
  ago" signal leaked onto a public plaza driven by internal bot traffic.

An optional field (instead of a fake `created_at` value on the catalog path)
keeps the additive-compatibility promise: old JSON still deserializes, and
consumers that ignore the field are unaffected.

### D5 — Vertical layering (executor's call)

- `port::repo::message::MessageRepoPort` gains
  `last_activity_by_group_ids(&self, group_ids: &[String]) -> Result<HashMap<String, u64>, MessageRepoError>`
  with a default returning an empty map (unimplemented/noop stores degrade to
  the `created_at` fallback per D1). Implemented by `bcs-message-store` SQL
  (D2 query; binds chunked per the store's existing batching precedent) and
  memory implementations.
- Application `GroupMessageHistoryService` gains the same-shaped method
  (`GroupUseCaseError`), defaulting to the empty map. Only `MessageService`
  (`services/bcs-message`) implements it — it is the type bootstrap always
  wires for this port (`use_case_wiring.rs::create_group_message_history_service`)
  and the only implementation holding `Arc<dyn MessageRepoPort>`.
  `BcsGroupMessageHistory` (`services/bcs-message-flow`, the legacy
  bot-delivery fallback) keeps the defaulted empty map: it has no repo
  dependency, and per D1 we do not source activity from the legacy path.
  `MessageService` answers the new method from its repo directly, never
  delegating to the fallback.
- V1 `GroupServiceImpl` takes the history service through the crate's existing
  optional-dependency pattern — `with_group_message_history(Arc<dyn GroupMessageHistoryService>)`
  storing an `Option` (same style as `with_bot_authority_hook` /
  `with_participant_view_bindings`), so the many existing test constructors
  stay untouched. Unwired facades behave like an unimplemented store (D6):
  empty map, `created_at` fallback. Bootstrap wires the already-built
  `group_message_history` instance in `resource_builders.rs`.
- `list_groups` flow: build filtered set → one batched activity lookup for that
  set's ids (skipped when empty) → sort by the D3 key → paginate → project
  summaries with the map value (fallback `created_at`). `list_public_groups`
  passes no map and projects `last_activity_at: None` (D4).
- OpenAPI: `api-contracts/v1/openapi/groups.yaml` gains the `sort` parameter
  and its ordering description names both orders; `domain-models.yaml` gains
  optional `last_activity_at` on both summary models.

### D6 — Failure and degradation (executor's call)

A store error from the activity lookup fails the request (500 via
`ApplicationError::internal`), matching how `project_summary` read failures
behave today — no silently mis-ordered page. Stores that do not override the
new defaulted method simply yield the `created_at` fallback ordering; behavior
stays correct, only less activity-aware, which keeps noop/test wiring compiling
without changes.

### D7 — Pagination over a mutable sort key (executor's call)

Offset/limit pagination stays as-is. Under `sort=last_activity` the sort key
changes on every message, so a group can shift position between two page
requests and a page boundary can repeat or skip an entry — standard live-feed
behavior for activity-sorted conversation lists (the WeChat mental model, BCS
principle P2), and acceptable for the known consumer (the workspace fetches one
page of 50 today). Introducing a cursor/snapshot mode is deliberately out: it
is new API surface that contradicts the additive shape of this change. The
OpenAPI description for `last_activity` states the instability explicitly.

## Left to the builder

- Exact SQL/bind chunking inside `bcs-message-store` (follow its existing
  IN-list conventions), and where the memory implementation keeps its scan.
- Test names/locations following each crate's existing suites.
- Precise OpenAPI wording, matching house style.

## Access cost

Per V1 `list_groups` call: +1 grouped aggregate query with one `IN` list of the
actor's filtered group ids (chunked), each group resolved via
`idx_group_created`; zero extra queries when the filtered set is empty; no
per-item reads (the batched lookup precedes pagination and also feeds summary
projection). No writes, no new indexes, no cache, no transaction changes. Memory
mode scans its in-memory messages once per call. Public-groups and legacy list
endpoints are untouched. Worst case is bounded by the actor's group count, not
by table size; there is no unbounded scan.

## Rollout and compatibility

- Fully additive: new optional query param, new response field. Rust consumers
  of `GroupSummary` in-repo are updated in the same change; serde defaults keep
  old JSON deserializable.
- Config, migrations, plugin APIs, WS protocol: unchanged on both dialects.

## Acceptance

- `sort=last_activity` returns the actor's groups ordered by real message
  activity (fallback `created_at`), `group_id ASC` tie-break, with `total`
  computed before pagination; pagination slices the globally ordered result.
- No `sort` (or `sort=created_at`) reproduces today's ordering exactly.
- `last_activity_at` is populated and correct on normal and DM summaries in
  `list_groups`, including groups with zero persisted messages (fallback
  `created_at`), and is omitted from `list_public_groups` items.
- `membership=session_only` groups participate in both sort orders and carry
  the field.
- Invalid `sort` values get 400; store failure surfaces as 500 (not a
  mis-ordered 200).
- Focused suites pass (service-api build, bcs-message-store, bcs-message,
  bcs-message-flow, bcs-app-group, bcs-api-http group routes); live-MySQL and
  full singlebox E2E reported separately as unavailable unless the
  implementation environment provides them.
