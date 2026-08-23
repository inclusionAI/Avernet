# Tasks: Adopt the Collaborator Authorization Seam

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Each **group** below is one PR against `dev_refactory_collaboration`. Land and
merge a group before starting the next: `authorization.py` is edited by every
group and by the base branch, so overlapping PRs conflict in that one file.

Every group after Group A follows the same three moves — **trace**, **flip**,
**delete** — plus its own tests.

**Trace** is the first move and the load-bearing one. For each row in the group,
follow the route to the code that actually performs its check, and record the
mapping in that group's change. Do not assume the module the row cites is the
one that checks it: for the skills group the citation was wrong for 3 of 10
rows, and that error survived a full planning pass. The bar comes from what the
trace found — the `required_level=` argument, the module constant, or the
literal — never from the number in the table. If a trace disagrees with a
citation, correct the citation in the same change.

A group is only ready to flip once every one of its rows has been traced and the
deletion is known to change no caller's answer.

---

## Task 1: Refuse a Check row the seam cannot key on
- **Goal:** Make the seam's structural limit enforced instead of documented — the gate decides before the handler runs, so it can only adjudicate an operation whose bot is on the path.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py`
- **Done when:**
  - [x] `_assert_check_rows_are_enforceable` gains a third refusal: a `Check` row on a route that does not declare `{bot_id}` on its path fails assembly, naming the route.
  - [x] The docstring says why — the gate reads `BotIdPath`, so such a row would adjudicate a value the handler never saw — and names the set this really excludes: the retiring skills addresses (bot in the query, or resolved from the skill record). It also records that this refusal does **not** catch harness, which does carry `{bot_id}` on its path; harness is stopped by the `OwnerIdDep` refusal, and its real blocker is the `entity_id` divergence #1323 filed.
  - [x] A fixture-router test covers it in both directions.
  - [x] No row changes mode; the seam itself is untouched.
- **Depends on:** —

## Task 2: Retire the no-adopter assertion
- **Goal:** Replace the test that encodes "the seam has no adopter", which stops being true in Group B.
- **Files:** `tests/community/adapters/http/openapi_v1/test_authorization_inventory.py`
- **Done when:**
  - [x] `test_no_live_operation_carries_the_gate` is **deleted**, not skipped or weakened.
  - [x] Its inverse exists, keyed on the **operation** rather than the cited module: the set of rows still `ServiceChecked` must equal exactly the ten deferred operations (6 harness, 3 skills, 1 connection). Keying on citations would let a row dodge migration by re-citing itself to an already-deferred module. It fails today, naming the 89 rows still to migrate, so it is `xfail(strict)` with a reason naming this feature and flips to a real assertion in the last group.
  - [x] `scaffolding_row_count()` and `SCAFFOLDING_MODES` are untouched.
- **Depends on:** Task 1

## Task 3: Record what the bot-chat operations actually do
- **Goal:** Correct the two rows that cite a collaborator check which does not exist.
- **Files:** `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Both `/bots/{bot_id}/chats` rows are `NoCheck` with a reason stating that the records are scoped to the acting user and the addressed owner is never read.
  - [ ] The finding is recorded in the change's description: `core/bot_chat/service.py` contains no collaborator check, and the handler does `del owner_id` before calling `list_sessions(owner_id=user_id)` (`bot_chats/router.py:107`, `:166`).
  - [ ] No handler, no service and no dependency changes — `require_granted_addressed_bot` stays exactly as it is.
  - [ ] A test asserts the two operations admit and refuse exactly the callers they do today.
- **Depends on:** Task 2

## Task 4: Move the diagnostics group onto the seam
- **Goal:** 2 rows. **TRACED 2026-08-23 and the plan was wrong about this group:** its check is *not* router-local. `_authorize` (`diagnostics/router.py:151`) calls `resolve_operable_bot` → `relay.resolve_bot_off_loop` → `relay.resolve_bot` → `require_bot_operator` (`gate.py:OPERATOR_LEVEL`, MEMBER) — the *same* deletion site as engine-runtime's 26 rows. Migrating the two groups separately leaves one of them unguarded in between, so **this task moves into Group F and lands with Task 10**. Keep the bot-type gate in `_authorize` (`active_engine != DEFAULT_ENGINE_TYPE` → 501).
- **Files:** `.../openapi_v1/diagnostics/router.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] The bar is derived from `diagnostics/router.py:_authorize` and the evidence recorded.
  - [ ] Both rows are `Check(<derived level>)`; `_authorize`'s permission check is deleted along with any parameter that only fed it.
  - [ ] Tests: below-bar caller refused byte-identically to an absent bot; owner and at-bar collaborator still admitted.
- **Depends on:** Task 3

## Task 5: Move the render-screens group onto the seam
- **Goal:** 3 rows behind `require_editable_bot`, with a deliberate `NoCheck` sibling to preserve.
- **Files:** `.../openapi_v1/render_screens/gating.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] The bar is derived from `render_screens/gating.py:58` (`level < PermissionLevel.MEMBER`) and recorded.
  - [x] The three write rows are `Check(MEMBER)`; `require_editable_bot`'s level check is deleted while `resolve_readable_bot` stays.
  - [x] The `GET /render-screens` row stays `NoCheck` — share and group viewers hold no Editor relation — and a test pins that it is still reachable by a viewer the three writes refuse.
- **Depends on:** Task 4

## Task 6: Give the authorized-apps handlers the owner they adjudicate
- **Status: `[!]` BLOCKED — needs a design decision, recorded 2026-08-23.**
- **What the trace found.** The swap itself is behaviour-preserving: these three
  operations are human-only (`refuse_app_only_caller` on the router, and all
  three rows are admission `REFUSED`), and for a human `OwnerIdDep` resolves to
  `owner_id or caller` — identical to `resolve_delegable_bot`'s
  `addressed_owner = owner_id or caller_id`. Verified along the whole path:
  `resolve_owner_id` → `AddressedBotGrantDep` → `require_granted_addressed_bot`
  → `_require_granted_bot` → `ActingCaller.require_bot`, whose docstring states
  "a **human** caller is not governed by a grant, so there is nothing to check
  and the addressed owner passes straight through."
- **Why it is blocked anyway.** `OwnerIdDep` transitively declares
  `require_granted_addressed_bot`, which is an **admission** dependency.
  `test_every_grant_checked_operation_declares_its_modes_dependency` refuses an
  operation that declares it while its admission mode is not
  `GRANT_CHECKED_ADDRESSED_BOT` — and these three are `REFUSED`. The failure is
  correct: the declaration and the table would be saying different things.
- **The fork.** `OwnerIdDep` does two jobs — resolve *which* owner, and check
  the app's grant. A human-only operation needs the first and must not carry the
  second. Options: (a) a human-only owner resolver that
  `_assert_check_rows_are_enforceable`'s `_consumes` also accepts, which changes
  the seam's dependency contract; (b) leave these 3 rows `ServiceChecked` and
  move on; (c) revisit whether `REFUSED` operations should publish `owner_id` at
  all. This is architecturally significant, so it is not being improvised.
- **Original goal:** Satisfy `_assert_check_rows_are_enforceable`, which refuses a `Check` row whose handler does not itself declare `OwnerIdDep`.
- **Files:** `.../openapi_v1/authorized_apps/router.py`
- **Done when:**
  - [ ] All three handlers take `OwnerIdDep` in place of whatever names the owner today, and act on that resolved value.
  - [ ] The published parameter description matches the surface-wide `OWNER_ID_DESCRIPTION`.
  - [ ] No row changes yet — this task is provably behaviour-preserving on its own.
- **Depends on:** Task 5

## Task 7: Move the authorized-apps group onto the seam
- **Goal:** 3 rows behind `resolve_delegable_bot`.
- **Files:** `.../openapi_v1/authorized_apps/gating.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] The bar is derived from `authorized_apps/gating.py:112` (`require_bot_operator` → `OPERATOR_LEVEL`) and recorded.
  - [ ] The three rows are `Check(MEMBER)`; the `require_bot_operator` call is deleted while the bot resolution, the empty-owner refusal and the `BotNotFoundError` masking stay.
  - [ ] Tests: refusal byte-identical to an absent bot; one audit row per mutating request.
- **Depends on:** Task 6

## Task 8: Move the skill-centre capability hook onto the seam
- **Goal:** 19 rows behind one boolean — the largest group with no entanglement.
- **Files:** `core/skill_center/authorization_hook.py`, its callers, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] The bar is derived from `authorization_hook.py:34` (`PermissionLevel.MEMBER`) and recorded.
  - [x] All 19 rows are `Check(MEMBER)`.
  - [x] **`CollaboratorBotCapabilityAuthorizationHook.can_manage_bot` STAYS.** It was deleted, and that was wrong — a P1 review finding caught it. This service has two callers and the seam covers one: `adapters/http/skill_center/skillsets.py` is mounted at `/api/skillsets`, outside `/openapi/v1` and governed by no row, and four of its routes carry no `CollaboratorPermissionInterceptor` — `GET`/`PUT /{skill_set_id}`, `GET /{skill_set_id}/skills`, `GET /{skill_set_id}/mcps`. All four take `entity_id` and `bot_id` as caller-supplied query parameters, so `can_manage_bot` was the only thing between an authenticated stranger and another owner's SkillSet: a read on three, a **write** on the `PUT`. Same shape as Task 9's `bot_skill_asset_service`, which was traced correctly; this one was not, and the difference is that Task 9 asked "who else calls this?" and Task 8 did not.
  - [x] Pinned by `test_the_control_plane_check_the_legacy_surface_relies_on_still_exists`, verified non-vacuous: deleting the call fails it with the route list.
  - [x] The service-side audit write is reconciled — and the reconciliation is to **keep** it. Reading both writers side by side: the seam guards its write with `level < PermissionLevel.OWNER`, so it never audits an owner acting on their own bot, which this service does audit today; and the two rows differ in content (`{"route", "method"}` against `{"action": "skill_set_create"}`). Deleting it drops rows rather than de-duplicating them. The overlap is one extra row for a non-owner on four operations, which is the cheaper outcome. This write was never the check — it runs after the mutation — so consolidating the check did not make it redundant.
  - [x] Tests: `test_skill_set_acl_denial_is_adjudicated_at_both_gates` states which gate answers on which surface — the masked 404 from `bot_access` on `/openapi/v1`, the service's own 403 on `/api/skillsets`, which was never behind the masked-404 contract; the owner's own mutation is still audited (`test_the_service_keeps_writing_its_own_audit_row_after_the_seam_took_over`); no capability or space check is lost.
- **Depends on:** Task 7

## Task 9: Move the bot-skill `{skill_id}` operations onto the seam
- **Goal:** 7 rows behind `bot_skill_asset_service`, and correct the 3 rows that never were.
- **Files:** `core/skill_center/services/bot_skill_asset_service.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] The bar is derived from every `check_collaborator_permission(..., PermissionLevel.MEMBER)` site in `bot_skill_asset_service` (`:304`, `:405`, and any other the task enumerates) and recorded per row.
  - [x] The 7 `{skill_id}` rows — `GET`, `DELETE`, `/content`, `/parameters` `GET` and `PUT`, `/activate`, `/deactivate` — are `Check(MEMBER)`.
  - [x] **The permission calls inside `bot_skill_asset_service` are *not* deleted**, unlike Task 8's hook. The hook had exactly one caller family; these two sites (`_resolve_local:303`, `_RepoAssetAdapter.resolve:404`) are reached from three surfaces, only one of which the seam covers:
    - `/openapi/v1/bots/{bot_id}/skills/{skill_id}/…` — covered, these are the 7 rows.
    - The retiring twins in `deprecated/skills.py`. Their router *is* built with `route_class=PublicAPIRoute` (`_shim.legacy_router`), so a twin does read its own row and would carry the gate — but their address is `/openapi/v1/bots/skills/{skill_id}`, with no `{bot_id}` on the path, so the `unkeyable` refusal makes `Check` unassignable there. They stay `INHERITED`, and are the four already exempted in `_TWINS_CHECKED_INDEPENDENTLY`.
    - `adapters/http/skill_center/skills.py`, mounted at **`/api/skills`** outside `/openapi/v1` entirely, calling `get_skill` and `set_active`. Governed by no `AUTHORIZATION` row at all, so nothing in this feature reaches it.
  - [x] Deleting the calls would strip authorization from the latter two. `local_skill_delete_service._authorize:491` is the same story for `DELETE`, reached by `deprecated/skills.py:267`.
  - [x] So the row moves and the service check stays: after the flip the seam is the declared authority for the 7 rows and enforces first, and the in-service check is a second gate at the same bar for them and the only gate for the other two surfaces. That is what makes the row honest — `ServiceChecked` claimed the service was the authority, and it is not, for these paths.
  - [x] Recorded per row: today a denied collaborator gets `LocalSkillNotFoundError`; after the flip the seam refuses first with `BotAccessRefusedError`. Both are 404 and both are masked, so nothing is newly probeable, but the error code changes and a test states it.
  - [x] The 3 collection rows — `GET` and `POST /bots/{bot_id}/skills`, `POST /bots/{bot_id}/skills/upload-folder` — **stay `ServiceChecked`**, with their citation corrected to name the module that really checks them: `local_skill_query_service._require_view_access:97` for the read, `local_skill_upload_service._authorize:111` for the two writes.
  - [x] The corrected citations read `…core.skill_center.services.local_skill_query_service` and `…core.skill_center.services.local_skill_upload_service`. These three rows stay in `_DEFERRED_OPERATIONS` in `test_authorization_inventory.py`, which is keyed on the operation rather than the citation — so a wrong spelling here is a documentation defect rather than a silent hole, and re-citing a row cannot be used to dodge migration.
  - [x] `local_skill_query_service` and `local_skill_upload_service` are **not modified**. A test asserts it, because those two also keep all six retiring skills addresses checked — including four the seam could never adjudicate, whose bot the skill id resolves inside the handler.
  - [x] `local_skill_upload_service`'s audit write stays too, since its rows are not migrating; no double-audit arises here.
  - [x] Tests: refusal byte-identical to an absent bot on the 7 migrated rows; the 6 retiring skills addresses admit and refuse exactly as they do today, driven end-to-end rather than argued.
- **Depends on:** Task 8

## Task 10: Move the engine-runtime groups onto the seam
- **Goal:** the engine-runtime rows, while keeping bot-type gating intact — 16 of the 26, once the group was traced.
- **Files:** `core/engine_runtime/relay.py`, `core/engine_runtime/gate.py`, `.../openapi_v1/engine_runtime/gating.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] The bar is derived from `gate.py:OPERATOR_LEVEL` (`MEMBER`) and recorded, with the docstring's "one bar for every operation on the surface" quoted as the evidence.
  - [x] 16 rows are `Check(MEMBER)`: engine (4), models (2), nodes (1), approvals (3), diagnostics (2) — plus the six session **file** operations, which call `resolve_operable_bot` directly.
  - [x] **Ten session rows are deferred, and this is the group's own finding.** `sessions/router.py:_resolve_session_backend` is not a single-level bar: it calls `resolve_operable_bot`, and **on `BotNotFoundError`**, at draft stage, asks `HumanBotFriendshipService.is_friend` — a BCN-verified friend who is no collaborator reaches the bot's sessions through `ExpertChat`. `Check(MEMBER)` refuses before the handler runs, so it closes that path outright instead of relocating it. The seam adjudicates one level; this is a disjunction, and expressing it is a seam change this feature ruled out. Recorded in `_DEFERRED_OPERATIONS` with the reason. Caught by `test_sessions.py`'s friend tests, not by reading the table.
  - [x] **`require_bot_operator` stays in `relay.resolve_bot`**, where the task said to delete it. The deferred sessions handlers turn that exact refusal into the friend fallback, so deleting it would not merely leave them unchanged — it would make `resolve_operable_bot` *succeed* for a stranger and serve them as an operator. With it kept, the sixteen migrated rows are adjudicated by the seam first and re-checked at the same bar underneath, which is the Task 9 pattern; the ten deferred ones are untouched.
  - [x] `require_operable_bot` in `gating.resolve_operable_bot` **stays**, so an unsupported bot type still answers 501.
  - [x] `relay.py`'s self-resolve (`resolve_bot(bot_id, owner_id, owner_id)`) is unchanged — it addresses the owner as caller, which the adjudication admits.
  - [x] Tests: the twenty-route operator sweep in `test_operator_access.py` now drives the real gate — its seam doubles read the same `relay.operators` the fake does, so `add_operator` still describes one world; a personal bot addressed at a published stage still answers 501, not 404.
- **Depends on:** Task 9

## Task 11: Adjudicate the fifteen retiring engine-runtime addresses
- **Goal:** Keep the path-addressed legacy twins carrying the same bar as their replacements.
- **Files:** `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] The 8 legacy rows whose replacement migrated (approvals ×3, engine ×3, models ×2) are `Check(MEMBER)`. They need nothing from the seam: each declares `{bot_id}` on its path already, just at a different position than its replacement. `test_a_retiring_twin_migrates_with_its_replacement` is what named them — it fired on the first attempt and listed all fifteen, which is what the guard was written for.
  - [x] **The 7 sessions twins stay `INHERITED`**, because their replacements did not migrate (Task 10): they share the friend-fallback handlers. **`/bots/connection/{bot_id}` is not among the migrated either** — it shares `connection.py:build` with its replacement, which is deferred.
  - [x] The twins are driven against their replacements by `test_legacy_parity.py`, which passes unchanged; the guard above is what asserts the bars match, per pair, at assembly time.
  - [x] `INHERITED` falls by 8. The rest are untouched along with the current row each shares its check with.
- **Depends on:** Task 10

## Task 12: Move the service-publication facade onto the seam
- **Goal:** 16 rows at two different bars, without breaking the edit lock that reads the same value.
- **Files:** `core/service_bot/services/service_publication_facade.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] Each row's bar is derived from the `required_level=` argument its handler passes (`MEMBER` by default; `OWNER` for container restart, convert-to-service, service-config update and initial-draft delete) and recorded per row — verified against the routers, not assumed from the table: all 16 matched.
  - [x] All 16 rows are `Check(MEMBER)` or `Check(OWNER)` to match. `test_the_four_owner_operations_kept_their_bar` reads every one of them back, because `required_level` was the only record of which sat where and deleting it would otherwise have left that nowhere.
  - [x] **The bar STAYS in `_resolve_bot`.** It was deleted — parameter and all — on the reasoning that the seam adjudicates first and a bar written twice can drift. A P2 in review round 4 caught what that missed: `ServicePublicationFacadeProtocol`'s Service API contract says *"Resolve, authorize and orchestrate"*, so the deletion left the contract claiming a behaviour the implementation no longer had, while every signature kept the `actor_id` that implies it. `AGENTS.md` makes contracts the authority for inter-component behaviour, so the divergence is the defect, not the duplication. Nothing outside `openapi_v1` calls the protocol today — verified — which is why this was not an incident, not why it was correct. **`level` stays computed and returned** — `_actions` branches on it to decide whether a caller sees `delete` on an unpublished draft, which is a projection, not a permission — and the `require_service` bot-type refusal stays.
  - [x] `_require_draft_lock` is untouched and still refuses when another collaborator holds the lock.
  - [x] The COSEC note on lock takeover moved rather than vanished: it now sits at `steal_lock`'s call site and names the row that carries its MEMBER bar.
  - [x] Tests: the lock still refuses; the rows are read back by `test_the_four_owner_operations_kept_their_bar` **and** the facade is driven directly with no seam in front of it by `test_the_facade_itself_refuses_below_the_bar` — neither substitutes for the other, and both were checked for vacuity. Domain refusals kept (convert rejects a local or already-service bot, delete honours the domain rule, the legacy `ext` shape still parses); the owner-scoped resolve is still a not-found from the facade. The facade writes no audit rows of its own, so the seam's is the only one.
- **Depends on:** Task 11

## Task 13: Move the channels group onto the seam
- **Goal:** 6 rows at two bars, keeping the edit lock #1323 promised to leave alone.
- **Files:** `.../openapi_v1/channels/router.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] Bars derived from `_require_admin(..., required_level=PermissionLevel.ADMIN)` for the four writes and from `_authorize`'s member-level bot resolve for the two reads, recorded per row.
  - [x] The 6 rows are `Check(MEMBER)` / `Check(ADMIN)` to match.
  - [x] Every `_require_admin` call is deleted and the helper with them, along with the `CollaboratorServiceProtocol` parameter the four writes only had for it. **`_require_edit_lock` stays**, with its 423 — it asks who holds the draft, which is a concurrency question, not a permission one.
  - [x] `_authorize` survives: the handlers call it for the **resolved owner** every write is scoped by, and for the bot-type refusal the shared engine-runtime gate performs. The module docstring now says which half of the old pair went where.
  - [x] Tests: the lock still returns 423 when another collaborator holds it; the admin-bar test is rewritten to read the four write rows and the two read rows back, since there is no longer a collaborator double to refuse with.
- **Depends on:** Task 12

## Task 14: Move the editors group onto the seam
- **Goal:** 5 rows, checked by the very service the endpoints manage.
- **Files:** `core/bot_collaborator/services/collaborator_service.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [x] Bars derived per method: `add_editor` and `update_editor` at ADMIN, `list_editors` at MEMBER, `remove_editor` at ADMIN (spelled inline rather than through the helper), `leave_editors` at MEMBER (through the public `check_permission`). All five matched the table.
  - [x] The 5 rows are `Check(MEMBER)` / `Check(ADMIN)` to match.
  - [x] Only the bar calls are deleted, and `_check_operable_permission` with them once nothing called it. **`require_capability` and `require_team_space_member` stay** — they are capability and space checks, not collaborator bars. `remove_editor` still resolves `operator_level`, because it answers a second question: an ADMIN may not remove themselves while an OWNER may. `check_permission` stays public — the internal `/api` collaborator methods still use it, and `add_collaborator` still re-checks ADMIN underneath `add_editor`.
  - [x] Verified single-surface first: nothing outside `openapi_v1/editors/router.py` calls the five editor methods, and none of the five addresses has a retiring twin.
  - [x] Tests: `test_an_unsupported_bot_is_not_disclosed_before_the_caller_is_admitted` replaces the ordering test that pinned "authorization before bot-type disclosure" — the ordering is unassertable from inside the service now, and the disclosure is closed harder, since a stranger's masked 404 no longer confirms the Bot exists. It reads all five bars back, `DELETE /editors/me` included, because leaving is the one editor operation whose caller is not an admin and a bar that drifted upward would trap a member on the Bot. `collaborator_service` writes no audit rows of its own, so the seam's is the only one.
- **Depends on:** Task 13

## Task 15: Tests & Verification
- **Goal:** Ensure the feature meets every spec acceptance criterion.
- **Files:** `tests/community/adapters/http/openapi_v1/`, `src/backend/docs/openapi-v1/README.md`
- **Done when:**
  - [x] Task 2's burn-down is un-`xfail`ed and passes as a strict equality. It reported the finish itself: the day the last migrating row left `ServiceChecked` the marker failed as an `XPASS(strict)` and had to be removed deliberately, which is what the marker was for.
  - [x] The rows still `ServiceChecked` are exactly the **25** in `_DEFERRED_OPERATIONS`, and every one cites the module that really checks it. Fifteen more than the ten this plan predicted, and each addition is a trace finding rather than a retreat: the 10 session operations (a disjunction, not a level — Task 10), the 2 product-chat reads (they discard the addressed owner the seam adjudicates against), and the 3 authorized-app operations (`REFUSED` admission against an `OwnerIdDep` that publishes a grant dependency).
  - [x] `scaffolding_row_count()` has fallen from **181 to 99**. End state: 82 `Check`, 65 `NoCheck`, 40 `OWNER_SCOPED`, 34 `INHERITED`, 25 `ServiceChecked` — 246 rows. The plan predicted 77; the 22 difference is exactly the 15 extra deferrals plus the 7 sessions twins that follow them. The numbers are asserted by the burn-down test, not described.
  - [x] Every `Check` row's route declares `{bot_id}` on its path and its gate resolves `OwnerIdDep` — both refused at assembly time by Task 1, so a row that could not be keyed cannot be added at all.
  - [x] No operation outside the migrated set changed which callers it admits or refuses. Two changes *inside* it are caller-visible and stated where they happen: the 19 skill-centre operations move a denied collaborator from 403 to the masked 404, and the 7 bot-skill ones change error code within 404.
  - [x] `docs/openapi-v1/README.md`: the stale *Adopting it (the follow-up work)* section — which still said "No row is `Check` yet" and pointed at a deleted test — is replaced by what the migration actually found, and a dated changelog entry carries the end-state numbers. The five-modes table needed no change: all five still exist, and three are still scaffolding.
  - [x] `scripts/ci/python_sast_local.sh` clean in changed-files mode — run under the project's Python 3.12 via `PYTHON_SAST_CMD`, because the system `flake8` is 3.11 and reports a spurious `E999` on `contracts.py`'s PEP 695 `class Envelope[T]`. That finding is pre-existing: the pre-change file fails identically.
- **Depends on:** Task 14

---

## Groups

- **Group A — Make the limit enforced:** Tasks 1, 2
  - Theme: The prerequisite, and it does **not** touch the seam. Assembly starts refusing a `Check` row the gate could not key on, and the no-adopter assertion becomes a burn-down one. No row changes mode; nothing about the surface changes yet.
- **Group B — Say what the bot-chat operations really do:** Task 3
  - Theme: A correction, not a migration. Two rows stop claiming a check that does not exist.
- **Group C — First adopter:** Task 5
  - Theme: The smallest, least entangled groups — no lock, no audit, no twins — put the `Check` path in front of real callers.
- **Group D — Authorized apps:** Tasks 6, 7 — **BLOCKED**, see Task 6
  - Theme: The one group needing a handler change before its rows can flip; the change lands separately from the flip so each is reviewable alone.
- **Group E — Skill centre:** Tasks 8, 9
  - Theme: 19 hook rows plus the 7 `{skill_id}` asset rows, `skill_set_control_plane`'s audit write reconciled, and three rows deliberately left behind with their citation corrected — because the modules that check them also check six retiring addresses this feature will not touch.
- **Group F — Engine runtime:** Tasks 4, 10, 11
  - Theme: The largest group and its fifteen path-addressed twins, keeping bot-type gating intact. The connection row and its twin are deferred — their check guards credential composition rather than route access, and that trade-off is not settled inside a migration.
- **Group G — The entangled three:** Tasks 12, 13, 14
  - Theme: The groups where the permission check shares code with something that must survive it — a computed level, an edit lock, a capability policy.
- **Group H — Verification:** Task 15
  - Theme: Final spec acceptance check and the documentation end state.
