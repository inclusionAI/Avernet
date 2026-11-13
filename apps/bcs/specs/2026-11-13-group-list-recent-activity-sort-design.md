# V1 Group/Session lists: sort by conversation last activity (design)

## Record

- Work item: [2026080400118003354](https://project.alipay.com/workItem?workItemId=2026080400118003354) — 群组列表支持按会话最近活跃时间排序
- Owner: BCS. Scope: public Avernet BCS only.
- Branch: `doc/dima-2026080400118003354` (documents only; implementation lands on its own branch later).
- Status: proposed design, awaiting requester review. Every call marked "executor's
  call" below is a guess the requester can veto; it is not something they agreed to.
- Revision 2 (2026-11-13, first review round): expands scope to the full DIMA
  request — the requester quoted the original two-item description during review:
  > 1. 群组级别：BCN群组列表支持按其相关会话的最后一次会话时间进行排序，最近活跃的群组排在前面。
  > 2. 会话级别：群组内的会话列表，也希望支持按各个会话的最后会话时间进行排序。

  Revision 1 covered item 1 only and explicitly de-scoped the session list; that
  narrowing is reverted. Revision 2 also corrects the access-cost claims (review
  experiment reproduced by the author on SQLite 3.26), resolves a plan/design
  conflict on `last_activity_at` population under the default sort, and gates
  implementation on the open semantics confirmations in D8.

## Intent and why

The workspace conversation list (frontend-nextgen) is a WeChat-style list of
groups; users expect the group with the newest conversation activity on top, and
inside a group they expect the same ordering over its conversations. Today the
V1 group list has a fixed `created_at DESC, group_id ASC` ordering
(`api-contracts/v1/openapi/groups.yaml`) and the V1 session list a fixed
`created_at DESC, session_id ASC` ordering
(`api-contracts/v1/openapi/sessions.yaml#list_sessions`). The frontend simulates
a "lastActivity" group sort client-side by mapping `lastMessageAt` from
`GroupSummary.updated_at`
(`apps/frontend-nextgen/src/services/workspace/mappers.ts`). Group `updated_at`
moves only on group mutations (rename, visibility, members, version) — never on
message traffic (`bcs-group-store` message handling does not touch
`bcs_groups`), so the current "activity" order is wrong, and the client-side
sort can only order one fetched page, not the whole list.

This change makes both V1 lists able to order by the conversation's real
most-recent activity, and exposes the activity timestamp on both summary models
so clients can render and sort correctly across pagination.

## Goals, non-goals, anti-goals

Goals:

- `GET /openapi/v1/collaboration/groups` accepts an optional sort selector and
  can order the full filtered result set by last conversation activity
  (descending) before pagination (DIMA item 1).
- `GET /openapi/v1/collaboration/groups/{group_id}/sessions` accepts the same
  sort selector and orders the full viewer-visible candidate set by last
  conversation activity before pagination (DIMA item 2).
- Group and Session summaries carry the conversation's last-activity timestamp,
  populated identically for a given item under every sort mode.
- Existing default behavior is byte-compatible: callers that pass no sort
  parameter keep today's ordering and response semantics (plus one additive
  field).

Non-goals:

- Legacy HTTP surfaces (`GET /groups`, `/groups/my`, `/bots/{id}/groups`,
  legacy session lists) keep their current ordering entirely.
- `GET /openapi/v1/collaboration/public-groups` (discovery catalog) keeps
  `updated_at DESC` and gains no field.
- Session detail (`get_session`) gains no field; the activity timestamp is a
  list-ordering signal, exposed on list items only.
- No denormalized `last_activity_at` column and no message write-path change in
  this work item. Additive indexes are **not** pre-committed either way: D2
  keeps the default path index-free and names the additive covering index as
  the pre-authorized contingency, decided by the plan's cost-verification gate
  with recorded evidence (see Access cost). Denormalization/log-level
  compaction stays a separate later phase.
- No viewer-relative activity filtering. D8 CF-1 records the requester
  confirmation; if the answer is "viewer-visible", this design is re-opened —
  the aggregates below do not implement it.
- No frontend-nextgen change in this work item; the contract addition is what
  the frontend needs to switch its sort keys (follow-up consumer task).

Anti-goals (failure even if "it works"):

- A per-item activity query (N+1) or any read touching rows outside the
  request's candidate set on the list hot path.
- Cost claims in the design/PR that are not backed by an `EXPLAIN` or a
  measured seeded-volume run (the revision-1 "bounded by group count" claim is
  the canonical counter-example).
- Changing the default ordering of any existing endpoint.
- Sorting after pagination (page-local sort that lies about global order).

## Current state

- V1 `GroupService.list_groups` (`crates/application/v1/bcs-app-group/src/service.rs`)
  collects all of the view actor's groups (direct membership, plus the
  session-only union via `SessionManagementService`), filters in memory, sorts
  with `DomainGroup::cmp_by_created_at_desc_group_id_asc`, then paginates, then
  projects summaries. Sorting is already global-before-pagination.
- V1 `SessionService.list` (`crates/application/v1/bcs-app-session/src/lib.rs`)
  is viewer-scoped by contract: only Sessions in which the resolved View Actor
  is a participant are candidates. It paginates **at the repo** (`list_by_group`
  applies `created_at DESC, session_id ASC` + offset/limit) and reads `total`
  via `count_by_group`; the in-memory re-sort is a safety net only. Global
  activity sort therefore moves sorting for that mode into the application
  layer (candidate fetch with `offset=0, limit=total`, in-app sort, in-app
  pagination), mirroring the group facade precedent.
- Conversation messages are persisted to `bcs_messages` (`group_id`,
  `session_id`, `env`, `created_at` epoch ms) by the delivery pipeline via
  `MessageRepoPort` (`crates/services/bcs-message-store/src/delivery.rs`,
  `mysql.rs`), independent of the `store_messages` legacy toggle. Indexes
  `idx_group_created(group_id, created_at)` /
  `idx_messages_group_created(group_id, created_at)` and
  `idx_session_created(session_id, created_at)` /
  `idx_messages_session_created(session_id, created_at)` exist in both
  dialects. `env` is **not** part of either index.
- Sessions are persisted in `bcs_group_sessions` with `gmt_create`
  (projected to epoch-ms `Session.created_at`) and are hard-deleted on
  `delete_session` (`bcs-session-store` issues `DELETE FROM bcs_group_sessions`);
  their message rows in `bcs_messages` are retained.
- `SessionServiceImpl` already holds `history: Arc<dyn GroupMessageHistoryService>`
  (constructor-injected), so the session facade needs no new wiring class for
  the message-side lookup. `GroupServiceImpl` follows the optional
  `with_*` pattern (`with_bot_authority_hook`) for the same service.
- `MessageRepoPort`, `GroupMessageHistoryService`, `SessionManagementService`
  and `SessionRepoPort` all use defaulted trait methods, so additive methods do
  not break noop/test implementations.

## Design decisions

### D1 — Unified activity semantics (executor's call; gated by D8)

One primitive, two levels. "活动时间" is defined from the persisted message log
plus session creation, per `env`:

- **Session activity** `A(s)` = max `created_at` of persisted messages in
  `bcs_messages` with `session_id = s`; a session with no persisted messages
  falls back to its own `created_at` (from `bcs_group_sessions.gmt_create`).
- **Group activity** `A(g)` = max over {max `created_at` of persisted messages
  with `group_id = g`} ∪ {`created_at` of every persisted session of `g`};
  a group with no messages and no sessions falls back to its own `created_at`.
  Equivalently: the group key is the max over its **related sessions'**
  activity, where "related" means every session row currently persisted for the
  group — the direct reading of the DIMA wording "按其相关会话的最后一次会话
  时间进行排序".

Consequences, stated and tested rather than implied:

- **A new empty session makes its group "active".** A newly created session
  with no messages yet pops its group to the top under `sort=last_activity`
  even before the first message. The alternative (message-log-only group key)
  is recorded as the fallback in D8 CF-2; the two-level invariant
  `A(g) = max A(s)` would then be replaced by documenting that group and
  session levels can disagree.
- **Deleted sessions** no longer contribute their `created_at` (the row is
  gone) but their persisted messages still count at group level — the message
  log is append-only signal, session creation is row-lifespan signal. Deleted
  sessions can never appear in the session list itself, so no session-level
  rule is needed.
- **Audience and type are ignored at both levels**: every persisted message row
  counts regardless of sender, `message_type`, or visibility audience. This is
  a *global activity clock*, identical for every viewer — explicitly **not**
  the viewer-visible "last readable message" and not a drop-in for the
  client's per-viewer `lastMessageAt`. Consequences owned by this choice
  (pending D8 CF-1): a member sees the group/session time and rank move on
  activity they cannot read (other sessions of the group, `FullOnly` or
  directed messages); "刚刚活跃" can open onto no visible new messages.
- Equal keys order by `group_id ASC` (group list) / `session_id ASC` (session
  list) — the stable deterministic tie-break, matching the existing contracts.
- Pre-cutoff caveat unchanged: history reads are cut over to the persisted log
  by `message_history.cutoff_timestamp`; traffic predating the cutover has no
  rows and therefore does not count. Reconstructing activity from the legacy
  bot-delivery fallback is rejected — it would couple a read model to delivery
  internals for a shrinking pre-cutoff set.

Rationale for the message-log primitive (unchanged from revision 1): the
message log is the only signal honest on both dialects —
`bcs_group_sessions.gmt_modified` auto-bumps on message append only on MySQL
(SQLite has no `ON UPDATE`) and also moves on non-message session mutations.

### D2 — Read-time aggregates, honest cost, verification gate (executor's call)

Compute activity at list time from three bounded aggregate shapes:

```sql
-- Q1 group message activity
SELECT group_id, MAX(created_at) FROM bcs_messages
WHERE env = ? AND group_id IN (…) GROUP BY group_id;
-- Q2 group session-creation activity (store-folded per group)
SELECT group_id, MAX(gmt_create) FROM bcs_group_sessions
WHERE env = ? AND group_id IN (…) GROUP BY group_id;   -- or folded in Rust
-- Q3 session message activity
SELECT session_id, MAX(created_at) FROM bcs_messages
WHERE env = ? AND session_id IN (…) GROUP BY session_id;
```

What the indexes do and do not guarantee (corrects revision 1):

- `idx_group_created(group_id, created_at)` / `idx_session_created(...)` give
  the planner a per-candidate index range. They do **not** make each candidate
  "read only its last message": neither index contains `env`, and the
  GROUP-BY-MAX has to evaluate the `env = ?` predicate per row. Reproduced on
  SQLite 3.26 with the repo's own DDL (review experiment, independently
  re-run): 2 candidate groups × 100 messages each → 206 VM steps; × 10,000
  each → 20,006 VM steps, plan `SEARCH TABLE bcs_messages USING INDEX
  idx_messages_group_created (group_id=?)`. The scan scales with the
  candidates' message history, not with the candidate count.
- A composite covering index `(env, group_id, created_at)` removes table-row
  fetches but does **not** reduce the scanned row count on SQLite (same 20,006
  steps, `USING COVERING INDEX`). Whether MySQL applies a loose index scan
  (per-group index jump) for this shape is **unverified** — it must be shown
  with `EXPLAIN` on a real MySQL before any claim is made.
- Decision gate (wired into the plan's verification task): ship the read-time
  aggregates only with recorded evidence — `EXPLAIN QUERY PLAN` on the SQLite
  flavor (always runnable in CI), `EXPLAIN` on real MySQL if the environment
  provides it, plus a seeded skewed-volume run (few candidates × deep history;
  many candidates × shallow history) with timings in the implementation PR. If
  the evidence is unacceptable, the pre-authorized ladder is: (a) add the
  covering index(es) above as an additive migration and re-measure; (b) if
  still unacceptable, stop and re-open this design — denormalized activity
  columns are the next phase and require a write-path decision this work item
  explicitly does not take.

### D3 — API shape: additive `sort` param on both list endpoints (executor's call)

`GET /groups` and `GET /groups/{group_id}/sessions` each gain optional query
param `sort`:

- `created_at` (default): today's ordering — `created_at DESC, group_id ASC`
  for groups; `created_at DESC, session_id ASC` for sessions.
- `last_activity`: `last_activity_at DESC, same id ASC` tie-break; the D1
  `created_at` fallback participates in the key.

An explicit opt-in param keeps both existing contracts byte-compatible while
satisfying both DIMA items; fixed-per-endpoint ordering is the repo convention,
so defaults stay. Unknown values are rejected 400 by the adapters' enum
deserialization, matching existing enum query params (`membership`, `kind`,
`status`).

### D4 — Summary fields and per-sort population paths (executor's call)

`NormalGroupSummary`, `DirectMessageGroupSummary` and `SessionSummary` gain
`last_activity_at: Option<u64>` (epoch ms), serialized with the existing
`skip_serializing_if = "Option::is_none"` convention. Population is per
endpoint, and — resolving the revision-1 plan/design conflict — identical in
value for the same item under every sort mode:

- `sort=last_activity`: one batched activity lookup over the **full filtered
  candidate set** runs before sorting (the key is needed globally), then
  sort → paginate → project with the map values.
- default / `sort=created_at`: today's sort and pagination run first; one
  batched lookup over the **page's** ids then populates the field. The value
  for a given item is the same map entry either way, so a group created
  yesterday with traffic one minute ago reports the same timestamp under both
  modes; the page-scoped lookup avoids paying a full-candidate query on the
  default path.
- Unwired facades, unimplemented stores, and empty candidate sets degrade to
  the D1 `created_at` fallback everywhere (D6).
- `list_public_groups`: always omitted (null). The discovery catalog keeps its
  exact current behavior — no extra read on that path, and no "active 2 minutes
  ago" signal leaked onto a public plaza driven by internal bot traffic.

Session-list scope is unchanged and viewer-relative before anything else: only
sessions where the resolved View Actor is a participant enter the candidate
set, and both the D1 `A(s)` values and the `total` apply within that set.

### D5 — Vertical layering (executor's call)

- `port::repo::message::MessageRepoPort` gains
  `last_activity_by_group_ids(&self, group_ids: &[String]) -> Result<HashMap<String, u64>, MessageRepoError>`
  and
  `last_activity_by_session_ids(&self, session_ids: &[String]) -> Result<HashMap<String, u64>, MessageRepoError>`,
  both defaulting to an empty map. Implemented by `bcs-message-store` SQL
  (Q1/Q3; binds chunked per the store's existing batching precedent) and memory
  implementations. Ids with no rows are absent from the map.
- `port::repo::session::SessionRepoPort` gains
  `max_created_at_by_group_ids(&self, group_ids: &[String]) -> ServiceResult<HashMap<String, u64>>`
  (default: empty map) — Q2 implemented by `bcs-session-store` SQL and memory.
- Application `GroupMessageHistoryService` gains the two message-side methods
  (defaulting to empty maps). Only `MessageService` (`services/bcs-message`)
  implements them — it is the implementation holding
  `Arc<dyn MessageRepoPort>`. `BcsGroupMessageHistory`
  (`services/bcs-message-flow`, the legacy bot-delivery fallback) keeps the
  defaults, per D1's rejection of legacy-path sourcing.
- Application `SessionManagementService` gains `max_created_at_by_group_ids`
  (default empty map), implemented by `SessionManagementServiceImpl`
  (`services/bcs-session`) delegating to its repo port. Noop/test impls ride
  the default.
- V1 `GroupServiceImpl` takes the history service through the existing
  optional-dependency pattern
  (`with_group_message_history(Arc<dyn GroupMessageHistoryService>)` storing an
  `Option`, same style as `with_bot_authority_hook`), and merges the two
  per-group maps with `max()` per D1. Unwired facades degrade per D4/D6.
  Bootstrap wires the already-built `group_message_history` instance in
  `resource_builders.rs`.
- V1 `SessionServiceImpl` consumes its already constructor-injected
  `history: Arc<dyn GroupMessageHistoryService>`; no new wiring or constructor
  change.
- Flows (both endpoints, per D4): `last_activity` → full-candidate lookup →
  in-app global sort → paginate → project; `created_at` → today's sort and
  pagination → page-ids lookup → project. For the session endpoint's
  `last_activity` path the candidate fetch is `list_by_group(group_id, status,
  0, total, None, participant_id)` after the existing `count_by_group`; the
  in-app `(activity DESC, session_id ASC)` sort replaces the repo ordering as
  the authoritative order for that mode, and offset/limit slice the sorted
  vector. `total` stays the pre-pagination count.
- OpenAPI: `api-contracts/v1/openapi/groups.yaml` and `sessions.yaml` gain the
  `sort` parameter with ordering descriptions naming both orders and the D7
  instability note; `api-contracts/v1/domain-models.yaml` gains optional
  `last_activity_at` on all three summary models, documenting the public
  catalog omission.

### D6 — Failure and degradation (executor's call)

A store error from any activity lookup (full-set or page-scoped) fails the
request (500 via `ApplicationError::internal`), matching how `project_summary`
read failures behave today — no silently mis-ordered page, no silently
mis-dated item. Stores that do not override the new defaulted methods simply
yield the `created_at` fallback ordering and field values; behavior stays
correct, only less activity-aware, which keeps noop/test wiring compiling
without changes.

### D7 — Pagination over a mutable sort key (executor's call)

Offset/limit pagination stays as-is on both endpoints. Under
`sort=last_activity` the sort key changes on every message and on session
creation, so an entry can shift position between two page requests and a page
boundary can repeat or skip an entry — standard live-feed behavior for
activity-sorted conversation lists (the WeChat mental model, BCS principle
P2), and acceptable for the known consumer (the workspace fetches one page of
50 today). Introducing a cursor/snapshot mode is deliberately out: it is new
API surface that contradicts the additive shape of this change. The OpenAPI
description for `last_activity` states the instability explicitly on both
endpoints.

### D8 — Requester confirmations required before implementation

Revision 1 treated the choices below as settled; review correctly flagged them
as product/permission semantics that the executor cannot self-authorize.
Implementation starts only after the requester answers CF-1…CF-4 in the PR
thread (defaults proposed; plan tests encode the defaults):

- **CF-1 — global vs viewer-visible clock.** Proposed: global activity clock
  (D1). Confirm the three accepted consequences: (a) a member of session A
  only sees the group's time/rank move on session B traffic; (b) `FullOnly` or
  directed messages leak "activity happened" via timestamp and ordering; (c)
  "刚刚活跃" can open onto no visible new messages. If the product answer is
  viewer-visible activity, this design is re-opened — per-viewer aggregates are
  correlated per-candidate reads (an N+1 in disguise) or need per-viewer
  denormalized state, neither of which fits D2's shape.
- **CF-2 — session creation counts as activity.** Proposed: yes; group key =
  max over related sessions' activity (D1), so a brand-new empty session tops
  both lists. Alternative: message-log-only group key with explicitly
  documented group/session divergence — chosen if the requester vetoes.
- **CF-3 — related-session set.** Proposed: every session row persisted for
  the group, regardless of viewer visibility or session status
  (running/completed). Deleted sessions contribute retained messages but not
  their creation time (D1).
- **CF-4 — stable tie-break.** Proposed: `last_activity_at DESC, group_id ASC`
  / `…, session_id ASC`.

## Left to the builder

- Exact SQL/bind chunking inside `bcs-message-store` / `bcs-session-store`
  (follow their existing IN-list conventions), whether Q2 folds in SQL or Rust,
  and where the memory implementations keep their scans.
- Test names/locations following each crate's existing suites.
- Precise OpenAPI wording, matching house style.

## Access cost

Per `list_groups` call under `sort=last_activity` (worst-paid path): +⌈G/C⌉
round trips for Q1 (+⌈G/C⌉ for Q2) where G = the actor's filtered group count
and C the store's bind chunk size; returned rows ≤ G per shape; scanned rows ≤
Σ over candidate groups of their `bcs_messages` (Q1) / `bcs_group_sessions`
(Q2) rows for the request env — the revision-1 claim "bounded by group count,
not table size" is retracted (D2 carries the reproduced counter-evidence).
Under default/`created_at` sort: same shapes over the ≤ `limit` page ids after
pagination. Zero work when the candidate set (or page) is empty; no per-item
reads.

Per `list_sessions` call: `last_activity` fetches the full viewer-scoped
candidate set (`count_by_group` total unchanged) and pays Q3 over it —
+⌈S/C⌉ round trips, returned rows ≤ S (candidate session count), scanned rows
≤ Σ candidate-session message rows; `created_at` pays Q3 over the page only.

No writes, no cache, no transaction changes; new indexes are a gated D2
contingency, not a default. Memory mode scans its in-memory messages/sessions
once per call per shape. Public-groups and all legacy list endpoints are
untouched. The implementation PR must attach the D2 evidence (SQLite
`EXPLAIN QUERY PLAN` always; MySQL `EXPLAIN` when available; skewed-volume
timings) — "群少但单群消息很多" and large candidate sets are the two cases that
decide.

## Rollout and compatibility

- Fully additive on both endpoints: new optional query params, new response
  fields. Rust consumers of `GroupSummary`/`SessionSummary` in-repo are updated
  in the same change; serde defaults keep old JSON deserializable.
- Config, migrations (default path), plugin APIs, WS protocol: unchanged on
  both dialects. A D2-triggered additive index migration changes exactly one
  sentence of this section and must be called out in the implementation PR.

## Acceptance

- Groups: `sort=last_activity` returns the actor's groups ordered by D1
  activity (fallback `created_at`), `group_id ASC` tie-break, `total` before
  pagination; a brand-new empty session lifts its group (CF-2 test); a deleted
  session's messages still count (D1 test).
- Sessions: `sort=last_activity` orders the viewer's participant sessions by
  D1 `A(s)` (fallback session `created_at`), `session_id ASC` tie-break,
  globally before pagination (page 1 and page 2 reflect one global order);
  `total` unchanged.
- No `sort` (or `sort=created_at`) reproduces today's ordering exactly on both
  endpoints.
- `last_activity_at` is populated and **identical for the same item across all
  three sort modes** (no-sort / `created_at` / `last_activity`), including a
  zero-message group/session (fallback value) — tests use message times well
  after creation so an accidental `created_at` fallback fails.
- `membership=session_only` groups participate in both sort orders and carry
  the field; session list keeps its viewer-participant scoping under both
  sorts.
- DM summaries populate the field identically; `list_public_groups` items omit
  it and keep `updated_at DESC` exactly.
- Invalid `sort` values get 400 on both endpoints; any activity-store failure
  surfaces as 500 on either path (not a mis-ordered or mis-dated 200).
- Cost evidence per D2 is attached in the implementation PR.
- Focused suites pass (service-api build, bcs-message-store, bcs-message,
  bcs-message-flow, bcs-app-group, bcs-app-session, bcs-session-store,
  bcs-session, bcs-api-http group/session routes); live-MySQL and full
  singlebox E2E reported separately as unavailable unless the implementation
  environment provides them.
