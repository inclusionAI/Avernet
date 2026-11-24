# Bot friend roster in the public-settings dialog — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bot Owners see a 「好友名单」 section in the public-settings dialog (per-type counts), open a management drawer listing every friend user / friend Bot with search, and remove a friend through a second confirmation — dissolving both sides of the friendship and notifying the removed party (or the friend Bot's Owner) in the notification center. Non-Owners see and can do nothing.

**Architecture:** The read side reuses the existing frontend pipeline end to end — V1 `GET /openapi/v1/collaboration/friend-connections` (act-as-Bot via `can_manage`, already authorized) consumed through `botFriendConversationService.loadDirectory`/`loadSection`, which already fetch-all-paginates per type, batch-resolves display names/online state via `queryCollaborationBots`, and degrades per type; the V1 read contract stays byte-identical. Removal extends `DELETE /friend-connections` additively with optional `actor_type/actor_id` (bot, `require_owner`-gated) routed through a new defaulted `ConnectService::revoke_friend_as_owner` whose durable override emits a new `RemovedByOwner` friend work-order notice; Backend registers two NOTICE event types (`HUMAN2BOT_FRIEND_REMOVED`, `BOT2BOT_FRIEND_REMOVED`) **before/with** BCS rollout (the intake 422s unknown types); the notice rides the generic NOTICE rendering path plus two additive `workOrderMeta` rows.

**Tech Stack:** Rust (axum, async-trait defaulted methods), Python FastAPI (`StrEnum` event catalog), React 18 + Jest (frontend-nextgen house style). No migrations, no config flags.

**Spec:** [2026-11-24-bot-friend-roster-design.md](2026-11-24-bot-friend-roster-design.md) (decisions D1–D8; the plan argues from it).

## Global Constraints

- Work item: 2026112400120934898. Branch/lifecycle (commit, push, PR) is owned by the dispatching pipeline task, not by this plan — no `git` mutation steps appear below.
- **Deploy order:** Backend catalog (Task 6) must land before or together with the BCS notification change (Tasks 1–4); BCS-first loses notices with only a WARN trail (Backend intake 422s unregistered event types). The frontend (Tasks 7–9) can ship either side.
- Wait for requester answers to design D8 CF-1…CF-4 before starting Tasks 7–8 (frontend hook/UI copy/scope). Tests encode the defaults: section renders for external Bots too (CF-1), removal is Owner-only (CF-2), proposed notification copy (CF-3), proposed counts labels (CF-4). API/backend tasks (1–6) are not gated.
- Every existing contract stays byte-compatible: new query params on DELETE are optional, GET untouched. Legacy surfaces (`apps/frontend`, `bcs-http` friend routes, legacy list facade) untouched.
- Authorization rules: list keeps live `can_manage` act-as; removal with `actor` requires live `require_owner`; never historical `created_by`.
- Notification is best-effort (WARN + continue) and only emitted when at least one edge was actually revoked (D5); self-removal (no `actor`) stays silent.
- Source files stay ≤ 1,000 lines: `connect.rs` is at 909 — removal+notify goes in `connect/lifecycle.rs` (764); `bcs-app-invitation/src/lib.rs` is at 962 — new application code goes in a new module file (`friend_roster.rs`) with a `mod` + delegation from `lib.rs`.
- No `cargo fmt` / global formatting; only touched lines change. No `cargo clean`; build via `.cargo/config.toml` target-dir (`/tmp/cargo/Avernet-target`); `df -h /tmp` before heavy runs; focused `cargo test -p <pkg> --test <name>` only until the final sweep.
- Do not add routes/endpoints outside the listed files; all new user-visible copy is Chinese, matching dialog style.

## Review Focus

1. Partial actor params (`actor_type` without `actor_id`, or an unknown `actor_type` variant) → 400 `invalid_request`, never silently ignored (Task 5).
2. Manager (has `can_manage`, is not owner) calling removal with `actor` → 403, while the same caller *listing* friends succeeds (Tasks 2, 5) — the two paths intentionally differ.
3. Double-clicking remove / removing an already-removed friend → `deleted: false`, **no** notification emitted (Task 3).
4. Work-order intake down (or event types not yet registered) at removal time → removal still succeeds, WARN logged with recipient ids (Task 3); no retry loop.
5. A friend whose details do not resolve (deleted/unregistered actor) → row renders the raw id and stays removable (Tasks 7–8); dangling edges are never silently dropped.
6. Friend Bot whose Owner cannot be resolved (`ownership` empty + no `created_by`) → the bot-friend notice is skipped with WARN; the human-friend path is unaffected (Task 3).
7. 免审批 auto-approved friend (`friend_check_in_strategy=open`) appears in the list and is removable via the new path (Task 5 integration coverage).
8. `keyword` search matches display name *and* raw actor id, case-insensitive, and only filters the active tab (Task 7).

## Tasks

### Task 1: BCS service-api contract surface

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/application/v1/friend_connection.rs` (`DeleteFriendConnection` `:162`)
- Modify: `crates/service-api/bcs-service-api/src/application/connect.rs` (`ConnectService` `:60`)
- Modify: `crates/service-api/bcs-service-api/src/port/friend_connect_notification.rs` (`FriendConnectNotificationKind` `:8`)

**Interfaces:**
- Produces:
  - `DeleteFriendConnection` gains `pub actor: Option<FriendConnectionActor>` (doc: absent = caller-side self-removal, present = managed-Bot removal; the transport layer validates both-or-none).
  - `FriendConnectNotificationKind` gains `RemovedByOwner` (doc: emitted only from `revoke_friend_as_owner` after a non-empty revoke; best-effort).
  - `ConnectService` gains the defaulted method from design D4 (exact signature): `async fn revoke_friend_as_owner(&self, first: &str, second: &str, request_auth: Option<RequestAuthHeaders>) -> ServiceResult<Vec<u64>>` with default body `self.revoke_friend(first, second, request_auth).await`.

- [ ] **Step 1: Write failing tests** in `application/v1/friend_connection.rs` (or its test module): serde round-trip of `FriendConnectionActor` unchanged; struct literal with `actor: None` compiles (existing tests become the regression).
- [ ] **Step 2: Run RED:** `cargo test -p bcs-service-api --lib` — fails on missing field/variant/method.
- [ ] **Step 3: Implement** the three additions exactly as above.
- [ ] **Step 4: Run GREEN** + `cargo check -p bcs-test-support -p bcs-edge-permission -p bcs-app-invitation -p bcs-app-bot` proving every existing `ConnectService` implementation compiles unedited against the defaulted method.

### Task 2: V1 application service — authorized owner removal

**Files:**
- Modify: `crates/application/v1/bcs-app-invitation/src/lib.rs` — only a `mod friend_roster;` line and delegating `delete_friend_connection` into it (file is at 962/1,000 lines)
- Create: `crates/application/v1/bcs-app-invitation/src/friend_roster.rs`
- Modify: `crates/application/v1/bcs-app-invitation/tests/support/mod.rs` (stub `ConnectService` records `revoke_friend_as_owner` orientation through the inherited default body) and `crates/application/v1/bcs-app-invitation/tests/v1_invitation_friendship_service.rs`

**Interfaces:**
- Consumes: Task 1 `DeleteFriendConnection.actor` + `revoke_friend_as_owner`; existing `BotAuthorityPort.require_owner`, `load_bot`, `require_authenticated_user`.
- Produces: `delete_friend_connection(command)` behavior —
  - `actor: None` → today's path, byte-identical.
  - `actor: Some(bot)` → require `caller.user` present (401 otherwise); `load_bot(bot.id)` (404 unknown); `require_owner(user.id, bot.id)` → 403 on manager/no-role; orientation: human target → `revoke_friend_as_owner("human_<target.id>", bot.id, request_auth)`, bot target → `revoke_friend_as_owner(bot.id, <target.id>, request_auth)`; result `DeleteResult { deleted: !revoked.is_empty() }`.
  - `actor` with kind human or an empty `actor.id` → `invalid_request` (defense in depth; the route layer already 400s, see Task 5).

- [ ] **Step 1: Write failing tests** (stub authority + stub connect): no-actor path byte-identical regression; owner caller + human target → orientation `(human_x, bot_b)`; owner + bot target → `(bot_b, friend_bot)`; manager caller → forbidden and connect never called; anonymous → unauthorized; unknown bot → not_found; empty revoked vec → `deleted:false`.
- [ ] **Step 2: Run RED** on focused names: `cargo test -p bcs-app-invitation --test v1_invitation_friendship_service owner_remove`.
- [ ] **Step 3: Implement** `friend_roster.rs` + the two-line `lib.rs` delegation.
- [ ] **Step 4: Run GREEN**, then the crate's full suite `cargo test -p bcs-app-invitation`.

### Task 3: Durable removal + RemovedByOwner notification

**Files:**
- Modify: `crates/services/bcs-edge-permission/src/connect/lifecycle.rs` (override `revoke_friend_as_owner`; reuse `revoke_friend`; reuse `emit_friend_connect_notification` / `target_notification_recipients` from `connect.rs`)
- Modify: `crates/services/bcs-edge-permission/src/connect.rs` only if the notify helper needs a visibility tweak (keep the file ≤1,000 lines)
- Modify: `crates/services/bcs-edge-permission/src/tests/notifications.rs` and `crates/services/bcs-edge-permission/src/tests/connect_lifecycle.rs` (fixtures: `assemble()`, `seed_bot`, `RecordingFriendConnectNotificationPort`, `service_with_notification`)

**Interfaces:**
- Consumes: Task 1 defaulted trait; existing `revoke_friend`, `target_notification_recipients`, `resolve_actor_display_name`, fake `FriendConnectNotificationPort` in tests.
- Produces: `DbConnectService::revoke_friend_as_owner(first, second, request_auth)` —
  call `revoke_friend(first, second, request_auth)`; empty result → return without notifying (Review Focus #3); otherwise recipients: `first` human → `[<staff_no>]` (`strip_prefix("human_")`); both bots → `target_notification_recipients(second)` (the removed friend's Owner, Review Focus #6); `target_bot_id` = `second` when `first` is human, else `first` (the managed bot). Notify with `kind=RemovedByOwner`, `applicant_actor_id=second` (removed actor), display names via `resolve_actor_display_name`, `request_ids` = revoked edge ids stringified, `request_auth` forwarded; notification errors WARN + `Ok(revoked)` (Review Focus #4).

- [ ] **Step 1: Write failing tests**: owner removes human friend → exactly one notice, recipients `[<staff>]`, kind, `target_bot_id=second`, edge ids in payload; owner removes bot friend → recipients = friend's ownership resolution (never the friend bot itself); empty revoke → zero notify calls; notification port error → result still `Ok` with edges returned; existing revoke/auth-sync lifecycle tests pass unmodified (regression guard).
- [ ] **Step 2: Run RED:** `cargo test -p bcs-edge-permission removed_by_owner`.
- [ ] **Step 3: Implement** the override in `connect/lifecycle.rs`.
- [ ] **Step 4: Run GREEN** + the crate's full suite `cargo test -p bcs-edge-permission` + `cargo check -p bcs-friend`.

### Task 4: Bootstrap notification adapter mapping

**Files:**
- Modify: `crates/bootstrap/bcs/src/friend_connect_notification.rs` (`event_type_for` `:144`, `event_category_for` `:159`, `notification_kind_label` `:167`, `title_for` `:175`, `content_for` `:183` — add the `RemovedByOwner` arm; file at 834 lines, stay terse)
- Test: same file's `#[cfg(test)] mod tests`

**Interfaces:**
- Consumes: Task 1 `RemovedByOwner` + design D5 payload contract.
- Produces: mapping — category `"NOTICE"`; `event_type_for` → `HUMAN2BOT_FRIEND_REMOVED` (human removed) / `BOT2BOT_FRIEND_REMOVED` (bot removed) via the existing `is_human_actor(applicant_actor_id)` split; label `"removed_by_owner"`; title 「好友关系已解除」; content per design D5 (human: `Bot「<bot>」的管理者已将你移出其好友名单，好友关系已解除。`; bot: `Bot「<bot>」的管理者已将你的 Bot「<friend>」移出其好友名单，好友关系已解除。`), name fallbacks to raw ids; routing through `recipient_user_ids` like other NOTICE kinds; `applicant_user_id` omitted (NOTICE forbids approver/applicant fields on the intake).

- [ ] **Step 1: Write failing payload tests** mirroring the existing `builds_*_payload` tests: human-removed event_type/title/content with resolved names; bot-removed content with per-field name fallback; serialized JSON omits `applicant_user_id` and `approver_user_ids`; `biz_data.request_ids` carries the revoked edge ids.
- [ ] **Step 2: Run RED:** `cargo test -p bcs --lib friend_connect_notification`.
- [ ] **Step 3: Implement** the match arms.
- [ ] **Step 4: Run GREEN.**

### Task 5: V1 HTTP adapter + OpenAPI contract

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/friend_connection.rs` (`DeleteFriendConnectionQuery` — add optional `actor_type`, `actor_id`)
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/friend_connection.rs` (`delete_friend_connection` handler — validate and pass through)
- Modify: `api-contracts/v1/openapi/friendships.yaml` (`FriendConnectionsPath.delete` parameters + description of the owner-removal semantics, the both-or-none rule, and the 403 meaning)
- Test: `crates/adapters/http/bcs-api-http/tests/friendship_routes.rs` + `tests/friendship_routes/write.rs` (existing `openapi_friend_connection_routes_*` layout)

**Interfaces:**
- Consumes: Task 2 application behavior.
- Produces: route mapping — `actor_type`+`actor_id` both present → `Some(FriendConnectionActor)`; both absent → `None`; exactly one present → 400 `invalid_request`; `actor_type` reuses the `FriendConnectionActorType` serde enum (unknown variant → 400, matching list query precedent); response envelope unchanged.

- [ ] **Step 1: Write failing route tests**: absent actor → current behavior (`openapi_friend_connection_routes_forward_decisions_and_delete` keeps passing byte-identically); owner principal with `actor_type=bot&actor_id=<bot>` → `actor` forwarded to the stubbed service; partial params / unknown type → 400; manager/stale-owner with actor → 403.
- [ ] **Step 2: Run RED:** `cargo test -p bcs-api-http --test friendship_routes`.
- [ ] **Step 3: Implement** DTO + handler; update `friendships.yaml`.
- [ ] **Step 4: Contract governance** (additive-optional-param precedent: the 2026-11-13 `sort` params): run `python apps/bcs/scripts/validate_openapi_contract.py`; regenerate the public gateway snapshot via `python apps/bcs/scripts/dump_openapi.py apps/gateway/configs/schemas/bcn.openapi.json` (and the internal one with the matching entrypoint/prefix arguments if it covers this path — diff must contain only this change); run the `apps/bcs/tests/openapi/` pytest contract suites; record every command + result in the PR. No new operation means the counts in `api-contracts/README.md` stay unchanged — do not edit them.
- [ ] **Step 5: 免审批 regression (Review Focus #7):** integration case under the crate's fixtures: `friend_check_in_strategy="open"`, create a human→bot and a bot→bot friendship without approval, assert both appear in `GET /friend-connections`, and the owner `DELETE` with `actor` revokes each (`deleted:true`, second delete `deleted:false`).

### Task 6: Backend work-order event catalog (deploy gate Task 1–4)

**Files:**
- Modify: `apps/backend/src/agentclaw/community/core/work_orders/models.py` (`WorkOrderEventType`, right after `BOT2BOT_FRIEND_REVIEWED` `:147-150`)
- Modify: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/work_orders/schemas.py` (the public intake's event-type documentation enum, `:225-262` — add the two values; this is what external callers see)
- Test: `apps/backend/tests/community/core/work_orders/test_work_order_service.py` (or the tests that already pin `notification_title_for`/`notification_summary_for` and event registration for friend types; extend, don't rewrite) and the schemas/contract sibling that enumerates event types if one exists under `tests/community/adapters/http/openapi_v1/work_orders/`

**Interfaces:**
- Produces: `HUMAN2BOT_FRIEND_REMOVED = ("HUMAN2BOT_FRIEND_REMOVED", NotificationCategory.NOTICE, "你已被移出 Bot 好友名单", "一个 Bot 的管理者已解除与你的好友关系。")` and `BOT2BOT_FRIEND_REMOVED = ("BOT2BOT_FRIEND_REMOVED", NotificationCategory.NOTICE, "你的 Bot 已被移出好友名单", "一个 Bot 的管理者已解除与你的 Bot 的好友关系。")`. Auto-included in `NOTICE_EVENT_TYPES`; **no** changes to `FRIEND_APPROVAL_EVENT_TYPES`, `REVIEWED_EVENT_TYPES`, or the callback dispatcher. The intake then accepts these types and category-checks NOTICE automatically (`work_order_service.py:181-188`).

- [ ] **Step 1: Write failing tests**: both types resolve via `event_type_definition`, category NOTICE, non-empty Chinese title/summary, absent from `FRIEND_APPROVAL_EVENT_TYPES` / `REVIEWED_EVENT_TYPES`; the `create_event` path accepts a NOTICE event with each new type and recipients (registers without 422).
- [ ] **Step 2: Run RED:** `cd apps/backend && python -m pytest tests/community/core/work_orders -q`.
- [ ] **Step 3: Implement** the enum entries + schema documentation rows.
- [ ] **Step 4: Run GREEN** over the whole `tests/community/core/work_orders` tree (plus the schemas sibling suite if found in Step 1).

### Task 7: frontend-nextgen roster data hook

**Files:**
- Modify: `apps/frontend-nextgen/src/services/backendApi/collaboration/collaborationFriendConnectionController.ts` (`DeleteFriendConnectionParams` `:64-68` gains optional `actor_type` / `actor_id`, passed through as query params; comment notes both-or-none is enforced server-side)
- Modify: `apps/frontend-nextgen/src/services/workspace/botFriendConversationService.ts` — **only if** the roster needs a behavior the service lacks; prefer zero edits (its `loadDirectory`/`loadSection` already cover fetch-all, dedupe, name/online resolution, per-type degrade)
- Create: `apps/frontend-nextgen/src/domain/collaborationPrivacy/botFriends.ts`
- Create: `apps/frontend-nextgen/src/hooks/useBotFriendRoster.ts`
- Test: `apps/frontend-nextgen/test/domain/collaborationPrivacy/botFriends.test.ts`, `test/hooks/useBotFriendRoster.test.tsx` (jsdom docblock if DOM needed), and keep `test/services/workspace/botFriendConversationService.test.ts` green unmodified (regression pin)

**Interfaces:**
- Consumes: `botFriendConversationService.loadDirectory(botId)` → `{ human: DomainResult<BotFriendPage<BotIdentityFriendView>>, bot: … }` and its exported `loadSection(botId, targetType)` for per-type refetch.
- Produces:
  - domain: `filterFriendViewsByKeyword(views: BotIdentityFriendView[], keyword: string)` — trim + case-insensitive substring over `displayName` or `actorId` (Review Focus #8); `rosterCounts(human, bot)` → `{ users, bots }` from each section's `total` when ok, `null` when failed.
  - hook `useBotFriendRoster(botUuid: string)`: **`botUuid` is the BCS collaboration Bot UUID (`targetBot.id`, with `:port` suffix) — never the Backend row `bot.id`** (the dialog already distinguishes the two; the wrong one 404s/403s against `load_bot`). Returns `sections { human, bot }` + `counts` derived; `reload(type?)` (full directory or one type via `loadSection`); drawer-local `tab` + `keyword` state stays in the drawer component (hook holds data only); `removeFriend(view)` → optimistic guard (`removingIds`) → `deleteFriendConnection({ actor_type: 'bot', actor_id: botUuid, target_actor_type: view.actorType, target_actor_id: view.actorId })` → on success `loadSection(view.actorType)` (counts+tab converge, acceptance ② in-place refresh); `{ deleted: false }` → neutral already-removed path (no error state); errors surface as the hook's `removeError` for the drawer to render; per-type section errors keep their existing `DomainError` retry semantics.
- The roster ignores `BotIdentityFriendView.disabledReason` (conversation-surface concern), renders raw `actorId` when `detailsResolved` is false (Review Focus #5).

- [ ] **Step 1: Write failing tests** — controller param serialization with actor (and unchanged query when omitted); domain filter (display-name match, staff-no match, bot-uuid match, case folding, empty keyword); hook (mock `loadDirectory`/`loadSection`/controller): counts from two section totals, failed type degrades to `null` count + retry without blanking the other, remove success → only that type refetched, remove 403 → `removeError` set, `{ deleted:false }` → neutral path with no error.
- [ ] **Step 2: Run RED:** `npx jest test/domain/collaborationPrivacy/botFriends.test.ts test/hooks/useBotFriendRoster.test.tsx`.
- [ ] **Step 3: Implement** the controller param addition, domain helpers, and the hook (no edits to `botFriendConversationService` unless a test in Step 1 proves a real gap — record the gap in the PR if so).
- [ ] **Step 4: Run GREEN** + the existing service suite + `npm run typecheck`.

### Task 8: frontend-nextgen 公开设置 section + management drawer + notice meta

**Files:**
- Modify: `apps/frontend-nextgen/src/components/CollaborationPrivacy/settingsSections.tsx` (export `BotFriendListSettings`)
- Create: `apps/frontend-nextgen/src/components/CollaborationPrivacy/BotFriendManageDrawer/index.tsx` (+ `FriendRow.tsx` if needed to respect the line ceiling)
- Modify: `apps/frontend-nextgen/src/components/CollaborationPrivacy/BotGeneralConfigDialog/BotGeneralConfigDialogBody.tsx` (render the section card only when `Boolean(userId && bot.ownerId === userId)`; mount drawer + confirm)
- Modify: `apps/frontend-nextgen/src/domain/admin/workOrderMeta.ts` (two additive rows for `HUMAN2BOT_FRIEND_REMOVED` / `BOT2BOT_FRIEND_REMOVED`)
- Test: `apps/frontend-nextgen/test/components/CollaborationPrivacy/BotFriendRoster.test.tsx` (jsdom docblock) and the existing `test/domain/admin/workOrderMeta.test.ts` (extend its 1:1 baseline, do not weaken)

**Interfaces:**
- Consumes: Task 7 hook; existing `Drawer` (`side="right"`), `ConfirmDialog` (destructive), `Avatar`, `Empty`, `Skeleton`, `Segmented`, design tokens.
- Produces:
  - Section 「好友名单」: title + summary 「好友用户 {users} · 好友 Bot {bots}」 with per-type loading/error+retry degrade (failed count shows as `-` with retry) + 「管理好友」 button.
  - Drawer: tabs 好友用户/好友 Bot; search input (「搜索好友」); rows = Avatar + display name (raw id fallback), bot subtitle = online badge + uuid, human subtitle = staff no; per-row 「移除」; per-tab empty states; truncation hint when a type hit the 20-page cap.
  - Confirm flow (D7): 「移除后双方好友关系将同步解除，对方将在通知中心收到系统消息。确定移除好友「{name}」吗？」(bot variant names the friend Bot and mentions notifying its Owner), destructive variant.
  - Owner gate: section + drawer mount only under the strict owner check (acceptance ③); the external-Bot dialog variant behaves the same (CF-1 default).
  - Notice meta: `WORK_ORDER_TYPE_META` gains the two removal types with 标签/颜色 matching the existing NOTICE friend entries' visual language. The locked baseline (`test/domain/admin/workOrderMeta.test.ts`, documented "后端增删事件必须显式改基线") asserts an exact catalog — today 16 keys (5 APPROVAL / 11 NOTICE) — update it deliberately to 18 keys (5 / 13) in the same diff.
  - Actor id source: the section passes `targetBot.id` into `useBotFriendRoster` (never `bot.id`); renders only inside the resolved-`targetBot` branch (no fake 0/0 for unjoined/failed dialogs).

- [ ] **Step 1: Write failing tests**: owner sees section + counts; manager/stranger render lacks the section; open drawer → both tab lists render fetched views; search narrows by name and by id; remove → confirm copy → success refetch + toast; cancel keeps the row; `{ deleted:false }` → already-removed toast; unresolved-details row renders the raw id and remains removable; hook receives the collaboration uuid, not the Backend row id; external-bot variant renders the section for its owner; workOrderMeta baseline passes with the two new rows present.
- [ ] **Step 2: Run RED:** `npx jest test/components/CollaborationPrivacy/BotFriendRoster.test.tsx test/domain/admin/workOrderMeta.test.ts`.
- [ ] **Step 3: Implement** section + drawer + dialog wiring + meta rows.
- [ ] **Step 4: Run GREEN** + existing dialog suites (`BotGeneralConfigDialog.test.tsx`, `ExternalBotGeneralConfigDialog.test.tsx`) for regression + `npm run typecheck`.

### Task 9: Final verification sweep

- [ ] **Step 1:** `df -h /tmp` first; BCS focused sweep recording real output: `cargo test -p bcs-service-api --lib`, `cargo test -p bcs-edge-permission`, `cargo test -p bcs-app-invitation`, `cargo test -p bcs-api-http --test friendship_routes`, `cargo test -p bcs --lib friend_connect_notification`.
- [ ] **Step 2:** Backend: `cd apps/backend && python -m pytest tests/community/core/work_orders -q` (plus the schemas sibling suite from Task 6 if one was found).
- [ ] **Step 3:** Frontend: changed-file jest set + `npm run typecheck` + `npm run check:open-core`.
- [ ] **Step 4:** `git diff --check`; verify every touched file ≤ 1,000 lines; report truly-unavailable gates (live MySQL, full singlebox E2E, workspace-wide cargo test) explicitly in the implementation PR.

## Verification commands

```sh
df -h /tmp
cargo test -p bcs-service-api --lib
cargo test -p bcs-edge-permission
cargo test -p bcs-app-invitation
cargo test -p bcs-api-http --test friendship_routes
cargo test -p bcs --lib friend_connect_notification
cd apps/backend && python -m pytest tests/community/core/work_orders -q
cd apps/frontend-nextgen && npx jest test/domain/collaborationPrivacy/botFriends.test.ts test/hooks/useBotFriendRoster.test.tsx test/components/CollaborationPrivacy/BotFriendRoster.test.tsx test/domain/admin/workOrderMeta.test.ts test/services/workspace/botFriendConversationService.test.ts && npm run typecheck && npm run check:open-core
git diff --check
```

## Contract (acceptance pinned by tests above)

Owner (and only the Owner) sees the 好友名单 section with 好友用户/好友 Bot split counts and opens the two-tab manage drawer listing **all** friends — including no-approval-origin ones — with display names (raw-id fallback) and client-side search. Removal asks for confirmation, revokes the friendship on both sides through the edge store (the legacy mirror is cleaned by the same call), is a no-op when the friendship is already gone, and emits one NOTICE work-order (`HUMAN2BOT_FRIEND_REMOVED` to the removed human, `BOT2BOT_FRIEND_REMOVED` to the removed Bot's current Owner) best-effort after a real revoke — with the Backend catalog registered first so the intake accepts it. The roster refreshes in place. Every pre-existing endpoint, the legacy frontends, and the approval flows behave byte-identically; non-owner API callers passing an `actor` parameter get 403; partial `actor` params get 400.
