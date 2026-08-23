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
  - [x] `CollaboratorBotCapabilityAuthorizationHook.can_manage_bot` and the protocol are deleted, along with every call site and the DI binding; nothing is left implementing a hook nobody calls.
  - [x] The service-side audit write is reconciled — and the reconciliation is to **keep** it. Reading both writers side by side: the seam guards its write with `level < PermissionLevel.OWNER`, so it never audits an owner acting on their own bot, which this service does audit today; and the two rows differ in content (`{"route", "method"}` against `{"action": "skill_set_create"}`). Deleting it drops rows rather than de-duplicating them. The overlap is one extra row for a non-owner on four operations, which is the cheaper outcome. This write was never the check — it runs after the mutation — so consolidating the check did not make it redundant.
  - [x] Tests: refusal moved to the seam and is byte-identical to an absent bot (`test_skill_set_acl_denial_moved_to_the_seam_and_changed_status`); the owner's own mutation is still audited (`test_the_service_keeps_writing_its_own_audit_row_after_the_seam_took_over`); no capability or space check is lost.
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
- **Goal:** 26 rows — the largest group — while keeping bot-type gating intact.
- **Files:** `core/engine_runtime/relay.py`, `core/engine_runtime/gate.py`, `.../openapi_v1/engine_runtime/gating.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] The bar is derived from `gate.py:OPERATOR_LEVEL` (`MEMBER`) and recorded, with the docstring's "one bar for every operation on the surface" quoted as the evidence.
  - [ ] All 26 rows are `Check(MEMBER)`.
  - [ ] `require_bot_operator` is deleted from `relay.resolve_bot` (`relay.py:140`); `require_operable_bot` in `gating.resolve_operable_bot` **stays**, so an unsupported bot type still answers 501.
  - [ ] `relay.py:289`'s self-resolve (`resolve_bot(bot_id, owner_id, owner_id)`) is checked and its behaviour is unchanged.
  - [ ] Tests: below-bar caller refused byte-identically to an absent bot; a personal bot addressed at a published stage still answers 501, not 404.
- **Depends on:** Task 9

## Task 11: Adjudicate the fifteen retiring engine-runtime addresses
- **Goal:** Keep the path-addressed legacy twins checked once `require_bot_operator` is gone from the relay.
- **Files:** `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] All 15 legacy rows (approvals ×3, engine ×3, models ×2, sessions ×7) are `Check(MEMBER)`. **`/bots/connection/{bot_id}` is not among them** — it shares `connection.py:build` with its replacement, which is deferred, so it keeps the check it has. They need nothing from the seam: each declares `{bot_id}` on its path already, just at a different position than its replacement.
  - [ ] A test drives each and its replacement with the same caller and asserts identical admit/refuse.
  - [ ] `INHERITED` falls from 42 to 27 — 20 twin an `OWNER_SCOPED` address, 6 are the legacy skills addresses, and 1 is the legacy connection address; each is untouched along with the current row it shares its check with.
- **Depends on:** Task 10

## Task 12: Move the service-publication facade onto the seam
- **Goal:** 16 rows at two different bars, without breaking the edit lock that reads the same value.
- **Files:** `core/service_bot/services/service_publication_facade.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Each row's bar is derived from the `required_level=` argument its handler passes (`MEMBER` by default; `OWNER` at `:361`, `:429`, `:498`, `:699`) and recorded per row.
  - [ ] All 16 rows are `Check(MEMBER)` or `Check(OWNER)` to match.
  - [ ] Only `if level < required_level: raise ServicePublicationNotFoundError` is deleted from `_resolve_bot`. **`level` stays computed** — `:242`–`:266` use it for lock applicability and an OWNER branch — and the `require_service` bot-type refusal stays.
  - [ ] `_require_draft_lock` (`:544`) is untouched and still refuses when another collaborator holds the lock.
  - [ ] Tests: the lock still refuses; an OWNER-barred operation still refuses an ADMIN collaborator; `_actions` (`:245`, `:250`) reports the same list it does today, since it branches on the `level` this task keeps computing. The facade writes no audit rows of its own, so the seam's is the only one.
- **Depends on:** Task 11

## Task 13: Move the channels group onto the seam
- **Goal:** 6 rows at two bars, keeping the edit lock #1323 promised to leave alone.
- **Files:** `.../openapi_v1/channels/router.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Bars derived from `_require_admin(..., required_level=PermissionLevel.ADMIN)` (`:165`) for writes and from the read path for `GET`, recorded per row.
  - [ ] The 6 rows are `Check(MEMBER)` / `Check(ADMIN)` to match.
  - [ ] Every `_require_admin` call is deleted and the helper with them; **`_require_edit_lock` (`:173`) stays**, along with the documented 423 response.
  - [ ] `_authorize` (`:136`) survives if the lock still needs the resolved bot.
  - [ ] Tests: the lock still returns 423 when another collaborator holds it; a MEMBER is refused a write and admitted a read.
- **Depends on:** Task 12

## Task 14: Move the editors group onto the seam
- **Goal:** 5 rows, checked by the very service the endpoints manage.
- **Files:** `core/bot_collaborator/services/collaborator_service.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Bars derived from each method's `required_level=` (`ADMIN` at `:325`, `:389`; `MEMBER` at `:419`; and the remaining sites the task enumerates) and recorded per row.
  - [ ] The 5 rows are `Check(MEMBER)` / `Check(ADMIN)` to match.
  - [ ] Only `_check_operable_permission` calls are deleted. **`_editor_policy.require_capability` (`:392`) and `require_team_space_member` (`:393`) stay** — they are capability and space checks, not collaborator bars.
  - [ ] The gate reading levels from the same service whose rows the handlers write is exercised by a test that adds an editor and immediately re-adjudicates.
  - [ ] Tests: refusal byte-identical to an absent bot; `DELETE /editors/me` still lets a MEMBER remove themselves. `collaborator_service` writes no audit rows of its own, so the seam's is the only one.
- **Depends on:** Task 13

## Task 15: Tests & Verification
- **Goal:** Ensure the feature meets every spec acceptance criterion.
- **Files:** `tests/community/adapters/http/openapi_v1/`, `src/backend/docs/openapi-v1/README.md`
- **Done when:**
  - [ ] Task 2's burn-down is un-`xfail`ed and passes as a strict equality: the rows still `ServiceChecked` are exactly the ten in `_DEFERRED_OPERATIONS` (6 harness, 3 skills, 1 connection), and every one cites the module that really checks it.
  - [ ] `scaffolding_row_count()` has fallen from 181 to **77** — 89 `ServiceChecked` rows leave (87 to `Check`, 2 to `NoCheck`) and 15 `INHERITED` twins become `Check`, so 104 in total. The end state is 10 `ServiceChecked` (6 harness, 3 skills, 1 connection), 67 `NoCheck`, 40 `OWNER_SCOPED`, 27 `INHERITED`, 102 `Check`, summing to the table's 246. The numbers are asserted, not described.
  - [ ] Every `Check` row's handler declares `OwnerIdDep` and its route declares `{bot_id}` on the path — both enforced at assembly by Task 1, now also asserted over the live surface.
  - [ ] No operation outside the migrated set changed which callers it admits or refuses.
  - [ ] `docs/openapi-v1/README.md`: the five-modes table, the burn-down numbers and a new dated changelog entry all reflect the end state.
  - [ ] Full backend suite and `scripts/ci/python_sast_local.sh` in changed-files mode both clean.
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
