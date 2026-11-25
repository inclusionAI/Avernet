# Bot friend roster in the public-settings dialog: Owner views and manages friends (design)

## Record

- Work item: 2026112400120934898 — 【公开设置】Bot 好友名单：Owner 可查看并管理 Bot 好友.
  (Private tracker; only the opaque id is recorded here per the public-repo rule.)
- Owner: cross-app (BCS API + Backend work-order domains + frontend-nextgen). Because the scope is cross-module, the design and plan live in `docs/specs/2026-11-24-bot-friend-roster/` (`design.md` + `plan.md`) per `CONTEXT-MAP.md` ("A cross-module contract or plan: use `docs/specs/YYYY-MM-DD-<topic>/`"), not under `apps/bcs/specs/`. Implementation lands on its own branches later.
- Branch: `doc/dima-2026112400120934898` (documents only).
- Status: proposed design, awaiting requester review. Every decision marked
  "executor's call" is a guess the requester can veto; it is not something they
  agreed to. The linked PRD (`Teamclaw_PRD/Teamclaw_PRD_new`, Bot好友名单_PRD
  v1.3) is **not accessible** to the executing identity (403); this design is
  built from the work-item description excerpt and acceptance criteria ①–④
  quoted there. Presentation details the PRD may pin beyond that excerpt (exact
  copy, row layouts, empty states) are left to the builder and the PR review.

## Intent and why

Today a Bot's friendships can only be perceived through notification-center
approval messages: once a request is decided the message sinks into history,
friends added under the no-approval policy produce no message at all, and only
the *adder* can unilaterally remove the relationship (`DELETE
/friend-connections` resolves the caller as the acting side only —
`bcs-app-invitation/src/lib.rs:894`). The Bot Owner has no standing way to see
"who is friends with my Bot" and no way to remove one.

The change adds a permanent 「好友名单」 section inside the Bot
public-settings dialog (Owner only): a summary line with per-type counts
(好友用户 / 好友Bot), a 「管理好友」 entry opening a right-side drawer with two
tabs (好友用户 / 好友Bot) listing every friend with search, and an Owner-initiated
remove action with two-step confirmation that dissolves both sides of the
friendship and notifies the removed party in the notification center (for a
friend Bot, its Owner).

## Goals, non-goals, anti-goals

Goals:

- G1 — Owner sees a 「好友名单」 section in the public-settings dialog with
  friend counts split user/bot, and a management drawer listing **all**
  friends under two tabs with search (acceptance ①, ④).
- G2 — Owner removes a friend (user or Bot) through a second-confirmation;
  the relationship is revoked on both sides, the roster refreshes in place,
  and the removed party (or the friend Bot's Owner) receives a
  notification-center system message (acceptance ②).
- G3 — Non-Owner viewers never see the section, the entry, nor can they call
  the removal API (acceptance ③).
- G4 — Every existing endpoint keeps its current contract byte-for-byte
  (all changes additive).

Non-goals:

- No change to friend-request creation, approval/免审批 policies, or the
  legacy `apps/frontend` surfaces (its GroupChat friend UI and the legacy
  `bcs-http` friend routes keep exactly today's behavior).
- No notification for *self*-removal (the adder unfriending from their own
  side stays silent, as today). The new notice covers Owner-initiated removal
  only.
- No server-side search parameter, and no new statistics/counts endpoint (D2/D3).
- No change to friendship storage or migrations: membership stays
  edge-grant-based.

Anti-goals (failure even if "it works"):

- Removing an edge without authorization checks, or authorization that reads
  historical `created_by` instead of the current authority edge (the
  bot-authority plan's rule).
- Notification failure blocking or silently rolling back the removal
  (removal must succeed and be observable even when the work-order intake is
  down; a WARN trail is required, matching the create-path policy).
- A removal that revokes non-friend edges (ownership, rules, other profiles)
  or resurrects revoked legacy mirrors.
- Listing that is wrong under pagination (page-local totals, dropped entries
  without an explicit reason).

## Current state (evidence)

- **Settings dialog.** `BotGeneralConfigDialogBody`
  (`apps/frontend-nextgen/src/components/CollaborationPrivacy/BotGeneralConfigDialog/BotGeneralConfigDialogBody.tsx`)
  renders titled section cards (`sectionCardClass`): 发布审批 /
  Bot可见性 / 好友审批 / 协作能力, with per-section degrade (loading skeleton,
  error `Empty` + retry). Owner detection already exists:
  `bot.ownerId === userId` at `:77-81`. A reusable right-side `Drawer`
  (`apps/frontend-nextgen/src/components/ui/Drawer.tsx`, `side="right"`) and a
  `ConfirmDialog` (`components/ui/ConfirmDialog`) with destructive variant are
  already used in this dialog.
- **Friend list API.** V1 `GET /openapi/v1/collaboration/friend-connections`
  (`friendships.yaml`, `FriendConnectionsPath`) lists approved default-profile
  connections for a bot or human actor, `target_type` filter (human|bot),
  page/page_size pagination with `total`.
  `InvitationFriendshipServiceImpl.list_friend_connections`
  (`bcs-app-invitation/src/lib.rs:859`) authorizes acting-as-Bot through the
  live `can_manage` edge (`resolve_acting_actor`, `:619`), so a Bot Owner can
  already list their Bot's friends. Today entries come back **unenriched**:
  `DbConnectService::list_friends_paginated`
  (`bcs-edge-permission/src/connect/lifecycle.rs:649`) returns
  `name: None, summary: None, is_online: false`. The frontend already owns the
  enrichment path used by every friend surface today:
  `botFriendConversationService.loadDirectory`
  (`frontend-nextgen/src/services/workspace/botFriendConversationService.ts:220`)
  fetches **all** pages for `target_type=human|bot` in parallel
  (`loadAllRelations`, page size 100), dedupes by actor id, resolves display
  names and online state in one batched `queryCollaborationBots({ bot_ids })`
  call (the same endpoint `managedBotConversationService` uses for human
  names), and degrades per type with typed `DomainError`s.
- **Removal API.** V1 `DELETE /friend-connections` resolves
  `caller_default_actor` only (`lib.rs:894-908`) — the caller can only
  dissolve their *own* side. `ConnectService::revoke_friend`
  (`connect/lifecycle.rs:538`) revokes exactly the friend-profile edge(s)
  between two actors in both directions, triggers the human→bot
  friend-auth-sync revoke best-effort, and drops the legacy friendship mirror
  (`writeback_remove_friendship`). Calling it as `revoke_friend(A, B)` vs
  `revoke_friend(B, A)` revokes the same edge set; only the auth-sync branch
  is orientation-sensitive (fires for human caller → bot target).
- **No-approval friends.** Auto-approval (`friend_check_in_strategy == "open"`
  or dept allowlist) still writes approved request rows and grants durable
  friend edges (`connect/lifecycle.rs:193-250`), so 免审批 friends live in the
  same edge store and appear in the same list (acceptance ④ needs no extra
  source).
- **Notification path.** BCS emits friend lifecycle notices through
  `FriendConnectNotificationPort`
  (`bcs-service-api/port/friend_connect_notification.rs`) with kinds
  ApprovalRequested / AutoApproved / Reviewed — today only `ApprovalRequested`
  is emitted by production code (`connect/lifecycle.rs:168`); the
  "申请已处理" notices come from Backend's own work-order approval flow, not
  from BCS. The port's implementation `HttpFriendConnectNotificationPort`
  (`bootstrap/bcs/src/friend_connect_notification.rs`) posts to the Backend
  internal `POST /api/v1/work-orders/events` (`adapters/http/work_orders/router.py`).
  The owner's recipients come from `target_notification_recipients`
  (`connect.rs:459`): current `authority.ownership`, WARN + `created_by`
  fallback. Backend `WorkOrderEventType` (`core/work_orders/models.py:135-150`)
  already carries the four friend event types. **The work-order intake
  strictly validates the event catalog**: `work_order_service.create_event`
  (`core/work_orders/services/work_order_service.py:181-188`) raises
  `WorkOrderInvalidEventError` (HTTP 422) for any unregistered `event_type`
  and checks the category matches — so a new removal event type **must be
  registered in the Backend catalog before (or together with) the BCS
  rollout**; BCS-first deploys would degrade to WARN-only notification loss
  (the removal itself still succeeds and is logged). The intake's NOTICE
  rules also pin the payload shape: NOTICE requires `recipient_user_ids` and
  forbids approvers/applicant ids, and `applicant` falls back to the acting
  principal's user id (`:170-174`) — exactly how the existing kinds ride.
- **Work-order event flow for removal requires no Backend route change**:
  the intake passes `event_type` through to the service
  (`adapters/http/work_orders/converter.py:44-57`); only the catalog enum and
  its HTTP-schema documentation copy need additive edits. The notification
  center (`frontend-nextgen/src/components/Admin/WorkOrderCard/index.tsx`)
  renders `title` + `content` generically, and `domain/admin/workOrderMeta.ts`
  keeps a tested 1:1 `WORK_ORDER_TYPE_META` catalog
  (`test/domain/admin/workOrderMeta.test.ts` asserts full coverage) that the
  new event types join additively (functional texts still come from the
  backend `title`/`content`).

## Design decisions

### D1 — Roster data source: existing V1 friend-connections list (executor's call)

The section and drawer read `GET /friend-connections` with
`actor_type=bot&actor_id=<bot_uuid>` and `target_type=human|bot` — one
authoritative edge-grant source, no new read model, 免审批 friends included by
construction (acceptance ④). The dialog section stays inside the dialog's
per-section degrade pattern: a failed read shows the section's error state
with retry, never blanks the whole dialog.

### D2 — UI structure (executor's call)

- New section card 「好友名单」 in `BotGeneralConfigDialogBody`, placed after
  the 好友审批 card, rendered **only when** `userId` is non-empty and
  `bot.ownerId === userId` (strict Owner; managers/collaborators/non-owners
  never see it — acceptance ③). External-Bot variant of the dialog renders it
  identically (the dialog's friend APIs are actor-generic); the requester may
  scope this down to internal Bots if the PRD says otherwise.
- Summary row: 「好友用户 N · 好友 Bot M」 + a 「管理好友」 button opening a
  `Drawer side="right"` titled 「管理好友」.
- Drawer: two tabs (好友用户 / 好友 Bot), a search input, a scrollable list.
  Each row: avatar, display name (fallback raw id), online badge for Bots,
  and a 「移除」 destructive action per row.
- One data load serves both summary and drawer: the section hook calls
  `botFriendConversationService.loadDirectory(<botUuid>)` once on mount (two
  parallel fetch-all loops are already its unit of work), derives the counts
  from the two section totals, and keeps the tab lists for when the drawer
  opens. **`<botUuid>` is `targetBot.id`** — the BCS collaboration Bot UUID
  (with `:port` suffix), the same id the dialog's identity band displays
  (`displayId = targetBot?.id ?? bot.id`); the Backend list-row `bot.id` is a
  different identifier and would 404/403 against `load_bot`. The section is
  rendered inside the resolved-`targetBot` branch together with the other
  config sections: an unjoined-BCN or failed-read dialog keeps its existing
  degraded rendering and shows no roster section (never a fake 0/0). Each
  type degrades independently (a failed 好友用户 read shows that type's
  error+retry state without emptying 好友Bot). A successful removal refetches
  the affected type, refreshing both counts and the tab. There is **no page
  cap**: the fetch-all loop pages until the section `total` is satisfied, so
  "list all friends" and "search all friends" hold at any volume. Loop safety
  comes from progress checking instead of an arbitrary ceiling: a page that
  returns zero rows while `total` is still unmet aborts the loop into that
  type's error state (with retry), never a silent partial list. A >20-page
  regression test is part of the plan (Task 7).
- No capability flag: the sibling 好友审批/可见性 sections are not
  capability-gated either (only the novel 发布审批 section is), and the
  roster manages an existing relationship rather than introducing a new
  permission model (executor's call; a veto can add a
  `getCapabilities()`-driven gate the same way 发布审批 does it).

### D3 — Search is client-side over the fetched set (executor's call)

The drawer filters the **fully fetched** tab list (D2 pages until `total`,
no cap) by case-insensitive substring on display name **or** actor id (human
staff no / bot uuid). No new server parameter: the V1 read contract stays
byte-identical, and because the fetch is complete, client-side search covers
every friend rather than a prefix of them. If a future Bot's volume makes
fetch-all itself too expensive (a measured, not assumed, problem), the
follow-up is a server-side `keyword` filter on the repository page query —
deliberately not built now.

### D4 — Owner-side removal: extend `DELETE /friend-connections` additively (executor's call)

Wire shape (all new params optional, snake_case):

```
DELETE /openapi/v1/collaboration/friend-connections
  ?actor_type=bot&actor_id=<bot_uuid>
  &target_actor_type=human|bot&target_actor_id=<id>
```

- `actor` absent: today's behavior, unchanged (caller-side self-removal).
- `actor` present: must be a bot; both actor params required together or the
  request is 400. The application service resolves the acting actor and
  requires the caller to be the bot's **current Owner** via
  `BotAuthorityPort.require_owner` (`port/bot_authority.rs:35`), failing 403 —
  reason: the destructive direction is Owner-scoped in the requirement,
  while the read side keeps its existing `can_manage` act-as semantics.
- Revocation orientation inside the application service:
  - target human → `connect.revoke_friend_as_owner(human_<id>, <bot_uuid>)`
    (orientation caller=human, target=bot, so the existing friend-auth-sync
    revoke trigger fires for the *friend's* teardown, and the same edge set
    is revoked as computed in Current state);
  - target bot → `connect.revoke_friend_as_owner(<bot_uuid>, <friend_bot>)`
    (revokes both directions of the bot↔bot friendship).
- Idempotent **and concurrency-safe**: no matching friend edge → `deleted: false`,
  **no notification**. Today's `revoke_friend` lists active grants and then
  revokes each without a status predicate or affected-row check, so two
  concurrent removals can both observe the same edges and both notify. The
  owner-removal path must instead revoke each candidate edge through an
  atomic conditional update (`… SET status='revoked' WHERE edge_id=? AND
  status='approved'`) exposed as a new port method
  `revoke_grant_if_active(edge_id, env) -> ServiceResult<bool>` (default
  implementation keeps external implementors compiling; the durable store
  overrides it atomically). Only edges whose update reported a transition
  count as revoked; the notice is emitted **only when at least one edge
  actually transitioned**. The guarantee is therefore "at most one notice
  per successful state transition", enforced by the database and covered by a
  concurrent-removal test (two parallel requests ⇒ one notice, one
  `deleted:true`, one `deleted:false`).

A new defaulted trait method replaces ad-hoc orientation logic in the
application layer (default keeps noop/test impls compiling):

```rust
/// Owner-initiated removal: revoke the friend-profile edge(s) between the
/// two given actors, mirroring the orientation rules the caller chooses
/// (human→bot auth-sync fires only for a human first argument). The default
/// delegates to `revoke_friend` with the mechanically correct order; the
/// durable implementation overrides to also emit the RemovedByOwner notice.
async fn revoke_friend_as_owner(
    &self, first: &str, second: &str,
    request_auth: Option<RequestAuthHeaders>,
) -> ServiceResult<Vec<u64>> { self.revoke_friend(first, second, request_auth).await }
```

(no schema/migration change; only friend-profile edges are touched, per the
existing `revoke_friend` guarantees — ownership/rules edges untouched, legacy
mirror cleaned by the same call.)

### D5 — Removal notice reuses the friend work-order pipeline (executor's call)

New kind `FriendConnectNotificationKind::RemovedByOwner` emitted by
`DbConnectService::revoke_friend_as_owner` after a successful non-empty
revoke, **best-effort** (WARN + continue on failure, mirroring the
create-path policy — the removal itself must not depend on the work-order
intake):

- Removed party is a human → recipients = `[<staff_no>]`;
  event_type `HUMAN2BOT_FRIEND_REMOVED`; catalog title 「你已被移出 Bot 好友名单」,
  content 「Bot「<bot 名>」的管理者已将你移出其好友名单，好友关系已解除。」 (names
  fall back to raw ids via the existing resolve-by-name helpers).
- Removed party is a Bot → recipients = that Bot's current Owner via
  `target_notification_recipients(to_bot, cfg)` — the signature requires the
  friend Bot's `BotActorConfig`: load it first; if the config is absent, WARN
  with the bot id and skip the notice (removal still succeeds, Review Focus
  #6). Event_type `BOT2BOT_FRIEND_REMOVED`; catalog title
  「你的 Bot 已被移出好友名单」, content
  「Bot「<bot 名>」的管理者已将你的 Bot「<friend 名>」移出其好友名单，好友关系已解除。」.
- **Titles are owned by the Backend event catalog, not by BCS.** Registered
  events resolve through `notification_title_for` / `notification_summary_for`
  (`apps/backend/.../work_orders/models.py:214`), so whatever title BCS puts
  in its payload is a fallback for unregistered types only. The plan therefore
  asserts the **persisted (catalog-resolved) title**, and D5's strings must
  equal the Task 6 catalog strings exactly.
- `event_category=NOTICE`; `biz_type=BOT_FRIEND`; `biz_data.request_ids`
  carries the revoked edge ids as strings (correlation evidence; no request
  row exists); `applicant_actor_id` = removed actor id (drives the existing
  human/bot event-type mapping); the acting Owner travels in the forwarded
  gateway principal exactly like the other kinds — Backend derives the actor
  from it.
- Self-removal (D4 `actor` absent) stays silent, matching today.

Backend (`apps/backend`) gains exactly two enum values in
`WorkOrderEventType` (`HUMAN2BOT_FRIEND_REMOVED`,
`BOT2BOT_FRIEND_REMOVED`, category NOTICE, canonical title/summary strings),
with the matching additive rows in the HTTP schema's documentation enum
(`adapters/http/openapi_v1/work_orders/schemas.py`, the Literal/enum
docblock the public intake mirrors). Registration is **mandatory**: the
intake 422s unregistered types. No route or callback change: the events
intake and the NOTICE rendering path are already generic, and removals are
not approvable (no callback-dispatcher entry). The frontend keeps the change
text-only too: two additive `WORK_ORDER_TYPE_META` rows in
`domain/admin/workOrderMeta.ts` plus the expected baseline update in
`test/domain/admin/workOrderMeta.test.ts` (repo convention keeps that map at
full 1:1 coverage of the backend catalog).

### D6 — Friend names: reuse the frontend's existing enrichment service (executor's call)

The drawer needs display names to be searchable and readable; today's V1 rows
are id-only. Two ways were considered; the design takes the already-shipped
one:

- **Client-side enrichment (chosen).**
  `botFriendConversationService.loadDirectory`/`loadSection` resolve names,
  online state and per-type degradation for exactly this list
  (`loadAllRelations` pages through `GET /friend-connections`, one batched
  `queryCollaborationBots` resolves `human_<staff>` nick names and bot display
  names). It is the same resolution pipeline the workspace's 他人会话 sidebar
  already uses, it carries its own tests, and it keeps the V1 read contract
  byte-identical — nothing to add, coordinate, or dual-maintain server-side.
  The roster hook consumes the service's `DomainResult` sections and maps them
  into drawer entries; entries whose details do not resolve keep the raw id
  as display (dangling edges stay visible and removable).
- **Server-side enrichment (rejected).** Populating `name`/`summary`/
  `is_online` in `DbConnectService::list_friends_paginated` or the V1
  application service looked attractive until the consumer inventory: `GET
  /friend-connections` is shared by at least four existing frontend flows —
  `collaborationSquare/friendConnectionRelationships.ts` (throws
  `protocol_error` on any non-20000 code), `botFriendConversationService.ts`,
  `managedBotConversationService.ts` (renders `name?.trim() || userId`
  itself), and `collaborationCandidateService.ts`. Enriching server-side
  would change displayed names on all of them, add a registry batch per page
  to each of their paginations, and — under a fail-closed error policy —
  turn a registry blip into broken lists on screens that render fine today.
  It would also put the same name-resolution logic in a second place to
  dual-maintain. Recorded as the fallback if a second server-side consumer
  (e.g. another client) later needs enriched rows: it would arrive as its
  own deliberate contract decision (with the absent-vs-null
  `skip_serializing_if` nuance spelled out), not smuggled under "additive".

### D7 — Two-step confirm + in-place refresh (executor's call)

Removal UX: row action → `ConfirmDialog` (destructive, copy
「移除后双方好友关系将同步解除，<对方>将在通知中心收到系统消息」) →
DELETE call → on success close confirm, refetch the tab, toast success.
on `deleted: false` treat as already-removed: refetch + neutral toast.
Errors surface through the section/drawer error state + the shared
error-notify observer, never a silent toast-only failure.

### D8 — Requester confirmations requested (defaults proposed; plan tests encode defaults)

- CF-1 — External Bots: the friend-roster section also renders in the
  external-Bot variant of the dialog (proposed: yes; the data/permission
  path is identical). Veto ⇒ gate the section on `!isExternalBot`.
- CF-2 — Removal authorization beyond Owner: proposed Owner-only
  (`require_owner`) per acceptance ③'s wording; if the product wants
  managers to manage friends too, drop to `can_manage` (one-line change,
  tests flip).
- CF-3 — Notification text/标题 copy (D5) is proposed copy only; the PRD
  v1.3 may fix exact wording. The plan pins the proposed strings in tests so
  a copy edit is a diff the PR review can see.
- CF-4 — Counts display granularity 「好友用户 N · 好友 Bot M」 is proposed;
  PRD v1.3 labels may differ. Presentation only.
- CF-5 — May a removed party immediately re-request friendship with the
  Bot? Proposed: yes (status quo; no block/deny concept exists anywhere in
  `create_connect` or the edge store, and building one is a deny list +
  policy gate + probable migration far beyond this item). Veto (removal must
  stick) re-opens scope, not a flip of a test default.

- **Confirmed 2026-11-25 by the requester:** all defaults above are accepted —
  CF-1 yes (external-Bot dialog renders the section), CF-2 Owner-only removal,
  CF-3 Backend-catalog notification copy, CF-4 the proposed counts labels,
  CF-5 a removed party may re-request immediately. Implementation may proceed;
  the plan's per-task gates are satisfied. PR base routing for the
  implementation: `apps/bcs` changes target `dev`; `apps/backend` and
  `apps/frontend-nextgen` changes target `REL20261125`, in separate PRs.

## Left to the builder

- Exact JSX structure/styles of the section, drawer rows, tabs and search
  input, matching the dialog's existing visual language and design tokens
  (shadcn semantic classes, no hardcoded palette).
- Final notification copy subject to CF-3, and section/tab copy subject to
  CF-4.
- Test names/locations following each crate's existing suites and the
  frontend `test/` layout (mirroring `BotGeneralConfigDialog.test.tsx` /
  jest house style; component tests needing DOM use the jsdom docblock).
- Whether `FriendWorkOrderEventRequest` titles resolve through the new enum
  or keep BCS-supplied titles (Backend prefers the canonical catalog; the
  converter path already chooses).

## Access cost

- Section mount: one `loadDirectory` burst — two parallel fetch-all loops of
  ⌈total/100⌉ list pages, then one batched `queryCollaborationBots` (≤ all
  distinct peer ids; the endpoint is already the house batch path for actor
  display). Drawer open costs nothing extra (data already loaded); a removal
  refetches exactly one type's loop.
- Removal: existing `revoke_friend` cost (≤2 default-profile reads + ≤2
  revoke writes per direction, ≤3 legacy mirror writes) + one best-effort
  ownership read for a removed-Bot recipient + one work-order POST. No new
  indexes, no per-item queries, no N+1: every display-name lookup is batched.
- Notification volume unchanged in shape: one event per removal, only when
  edges actually revoked.

## Rollout and compatibility

- **Wire compatibility** is additive (optional query params on `DELETE
  /friend-connections`; the GET contract is untouched): old HTTP requests keep
  behaving exactly as today. **Rust source compatibility is a separate
  matter.** Adding `actor` to the public `DeleteFriendConnection` struct
  breaks struct literals, and adding a `FriendConnectNotificationKind` variant
  breaks exhaustive `match`es — default trait methods do not help either.
  `bcs-service-api/CONTEXT.md` records this class of change as a **major Rust
  Service API version bump** (precedent: the `filters` field addition).
  Task 1 therefore bumps the crate version, updates the owning
  `CONTEXT.md`, enumerates and fixes every in-workspace constructor/`match`
  (verified by a workspace-wide `cargo check`), and the rollout notes state
  the promise as "old HTTP requests remain compatible", not "source
  compatible".
- **Deploy order is a hard release gate, not an option.** Backend must
  register the two removal event types before (or in the same train as) the
  BCS change: the work-order intake 422s unregistered `event_type`s
  (`work_order_service.py:181-188`, mapped to a 400-class envelope error).
  If BCS ships first, removals keep working but their notices are lost with
  only a WARN trail until Backend catches up — acceptance ② would silently
  fail in the gap. The reverse order is fully safe (unused catalog entries
  are inert). The frontend workOrderMeta addition is text-only and can ride
  either side. The implementation PR's rollout notes must state this
  sequencing explicitly.
- **Contract governance for the DELETE change.** Adding optional query
  params to an existing V1 operation follows the accepted additive precedent
  (the 2026-11-13 `sort` params on the group/session lists). The
  implementation PR must run the checked-in contract validator
  (`apps/bcs/scripts/validate_openapi_contract.py`), regenerate the gateway
  snapshot(s) via `dump_openapi.py` so the diff contains only this change,
  and pass the `apps/bcs/tests/openapi/` pytest contract suites; no
  operation is added, so the operation counts documented in
  `api-contracts/README.md` stay unchanged.
- No migrations, no config changes, no new runtime flags. Local/in-memory
  stores stay compilable through defaulted trait methods.
- The legacy friend surfaces (`apps/frontend`, `bcs-http` friend routes,
  legacy friend list facade) are untouched; their authorization and payloads
  do not change.

## Acceptance (maps to the work-item criteria)

- ① Owner sees the section with split counts and the drawer lists every
  friend per tab — covered by D1/D2/D6, proven by frontend component tests
  (dialog render owner vs non-owner, counts line, two-tab list incl. a
  no-approval-origin friend row unchanged by origin) and hook/service tests
  over the reused `loadDirectory` path.
- ② Removal requires confirmation, list refreshes in place, removed party is
  notified — covered by D4/D5/D7, proven by BCS route/service tests (edge
  set revoked both directions, auth-sync orientation, notification payload
  per party kind, no-edge idempotency without notification), backend event
  catalog test, and frontend interaction tests.
- ③ Non-Owner sees nothing — covered by the D2 render gate (frontend tests:
  manager/collaborator/stranger renders do not contain the section) and the
  D4 `require_owner` API gate (BCS HTTP tests: manager/other-owner/anonymous
  → 403/401; wire contract unchanged when `actor` params absent).
- ④ 免审批-origin friends appear — covered by reading the single edge store
  (D1) with an integration test seeding an auto-approved friendship
  (`friend_check_in_strategy=open`) and asserting it is listed and removable
  through the new path.

## Verification gates for implementation

- BCS: run from `apps/bcs` (or pass `--manifest-path apps/bcs/Cargo.toml`);
  the repository does not ship a `.cargo/config.toml`, so set
  `CARGO_TARGET_DIR=/tmp/cargo/Avernet-target` explicitly (or rely on the
  container-level untracked config if present). Focused suites
  (`bcs-service-api`, `bcs-edge-permission`, `bcs-app-invitation`,
  `bcs-api-http` friend routes, bootstrap notification adapter); `df -h /tmp`
  discipline; no global fmt; frozen migrations untouched (none created).
- Backend: focused `pytest` over `work_orders` model/converter suites.
- Frontend-nextgen: `npx jest` for the new/changed test files (including the
  workOrderMeta 1:1 baseline) + `npm run typecheck`; open-core boundary
  check before PR.
- Report genuinely-unavailable gates (live MySQL, full E2E) separately in
  the implementation PR, mirroring house style.

## Review revisions (2026-11-25, PR review round 1)

Each inline review comment and how this revision addresses it:

| Review item | Resolution |
|---|---|
| Remove private work-item link from public docs | Record only the opaque id `2026112400120934898`; no private-tracker URL anywhere in these documents. |
| Cross-module spec location | Both documents moved to `docs/specs/2026-11-24-bot-friend-roster/` per `CONTEXT-MAP.md`; this design is `design.md`. |
| 20-page cap conflicts with "all friends" + search | Cap removed; fetch-all pages until `total`, zero-progress aborts into the type's error state (never a silent partial list); >20-page regression test added to the plan. |
| `revoke_friend` ordering cannot prevent concurrent double-notify | Owner removal now requires an atomic conditional revoke (`revoke_grant_if_active`, affected-row semantics) and notifies only when ≥1 edge actually transitioned; concurrent test added. |
| Notification title inconsistency / authoritative source | Titles unified with the Backend catalog; Backend `notification_title_for` is authoritative; tests assert the persisted catalog title. |
| Rust Service API source compatibility | Rollout section now separates wire compatibility (additive) from Rust source compatibility (major `bcs-service-api` version bump + `CONTEXT.md` + workspace-wide constructor/match fixes). |
| `RemovedByOwner` actor/target construction | Plan Task 3 now uses `first` (human) / `second` (bot) as the applicant actor per direction, loads `BotActorConfig` for `target_notification_recipients(to_bot, cfg)`, and asserts the adapter payload end to end. |
| CF gating | Plan's Global Constraints gate Task 2/5 on CF-2, Tasks 3/4/6 on CF-3, Tasks 7–9 on CF-1/CF-4, and overall implementation on CF-5. |
| OpenAPI validator command | Plan uses `--root apps/bcs/api-contracts/v1` (verified: 81 operations). |
| DELETE `deleted` return type | Plan adds `DeleteFriendConnectionResult { deleted: boolean }` and updates the envelope generic plus a contract test. |
| Cargo/directory commands | Plan runs from `apps/bcs` or uses `--manifest-path`; `CARGO_TARGET_DIR` replaces the non-existent worktree config; backend/frontend commands run in separate subshells. |
