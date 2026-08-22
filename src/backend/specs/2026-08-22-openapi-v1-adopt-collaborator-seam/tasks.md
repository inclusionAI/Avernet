# Tasks: Adopt the Collaborator Authorization Seam

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Each **group** below is one PR against `dev_refactory_collaboration`. Land and
merge a group before starting the next: `authorization.py` is edited by every
group and by the base branch, so overlapping PRs conflict in that one file.

Every group after Group A follows the same three moves — **derive**, **flip**,
**delete** — plus its own tests. "Derive" always means reading the bar out of
the code being deleted and recording the evidence (the `required_level=`
argument, the module constant, or the literal), never copying the number from
the table.

---

## Task 1: Refuse a Check row the seam cannot key on
- **Goal:** Make the seam's structural limit enforced instead of documented — the gate decides before the handler runs, so it can only adjudicate an operation whose bot is on the path.
- **Files:** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py`
- **Done when:**
  - [ ] `_assert_check_rows_are_enforceable` gains a third refusal: a `Check` row on a route that does not declare `{bot_id}` on its path fails assembly, naming the route.
  - [ ] The docstring says why — the gate reads `BotIdPath`, so such a row would adjudicate a value the handler never saw — and names the two sets this excludes: harness (bot in the request body) and the retiring skills addresses (bot in the query, or resolved from the skill record).
  - [ ] A fixture-router test covers it in both directions.
  - [ ] No row changes mode; the seam itself is untouched.
- **Depends on:** —

## Task 2: Retire the no-adopter assertion
- **Goal:** Replace the test that encodes "the seam has no adopter", which stops being true in Group B.
- **Files:** `tests/community/adapters/http/openapi_v1/test_authorization_inventory.py`
- **Done when:**
  - [ ] `test_no_live_operation_carries_the_gate` is **deleted**, not skipped or weakened.
  - [ ] Its inverse exists: every remaining `ServiceChecked` row cites the harness module and nothing else. It fails today (11 other modules still cited), so it is marked `xfail` with a reason naming this feature and flips to a real assertion in the last group.
  - [ ] `scaffolding_row_count()` and `SCAFFOLDING_MODES` are untouched.
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
- **Goal:** First real adopter — 2 rows, router-local check, no lock, no audit, no twins.
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
  - [ ] The bar is derived from `render_screens/gating.py:58` (`level < PermissionLevel.MEMBER`) and recorded.
  - [ ] The three write rows are `Check(MEMBER)`; `require_editable_bot`'s level check is deleted while `resolve_readable_bot` stays.
  - [ ] The `GET /render-screens` row stays `NoCheck` — share and group viewers hold no Editor relation — and a test pins that it is still reachable by a viewer the three writes refuse.
- **Depends on:** Task 4

## Task 6: Give the authorized-apps handlers the owner they adjudicate
- **Goal:** Satisfy `_assert_check_rows_are_enforceable`, which refuses a `Check` row whose handler does not itself declare `OwnerIdDep`.
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
  - [ ] The bar is derived from `authorization_hook.py:34` (`PermissionLevel.MEMBER`) and recorded.
  - [ ] All 19 rows are `Check(MEMBER)`.
  - [ ] `CollaboratorBotCapabilityAuthorizationHook.can_manage_bot` and the protocol are deleted, along with every call site and the DI binding; nothing is left implementing a hook nobody calls.
  - [ ] The service-side audit write is reconciled: `skill_set_control_plane.py:61` takes an `audit_log_repo` and writes its own `BotCollabLog` row, and the seam now writes one too. Delete the service-side write so a mutation leaves exactly one row.
  - [ ] Tests: refusal byte-identical to an absent bot; **exactly one** audit row per mutating request; no capability or space check is lost.
- **Depends on:** Task 7

## Task 9: Move the bot-skill assets onto the seam
- **Goal:** 10 rows across several resolver classes.
- **Files:** `core/skill_center/services/bot_skill_asset_service.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] The bar is derived from every `check_collaborator_permission(..., PermissionLevel.MEMBER)` site (`:304`, `:405`, and any other the task enumerates) and recorded per row.
  - [ ] All 10 rows are `Check(MEMBER)`; each permission call is deleted while the bot resolution and skill-kind logic stay.
  - [ ] The service-side audit write is reconciled the same way: `local_skill_upload_service.py:89` writes its own row; delete it so the seam's is the only one.
  - [ ] Tests: refusal byte-identical to an absent bot; **exactly one** audit row per mutating request.
- **Depends on:** Task 8, and **Task 10 must land first or in the same change** — deleting this check uncovers the six legacy addresses until Task 10 carries it into `deprecated/skills.py`.

## Task 10: Keep the six retiring skills addresses checked
- **Goal:** Carry the collaborator check into the `deprecated/` package before Task 9 deletes it from the service, so six retiring addresses are not silently uncovered.
- **Files:** `.../openapi_v1/deprecated/skills.py`
- **Done when:**
  - [ ] `_bot_behind` (`:226`) performs the collaborator check beside the grant check it already performs, against the `(bot, owner)` it already resolves from the record.
  - [ ] The collection and upload shims get the same check; their `bot_id` is a query parameter, which is enough here because this runs after resolution rather than before the handler.
  - [ ] The module docstring is extended: it already says the second mechanism "moves here and dies here" about the grant; it now says the same about the collaborator bar, and why these six cannot be adjudicated (two carry the bot in the query, four resolve it from the skill).
  - [ ] All six rows stay `INHERITED` — the row is honest, because what governs them is not decided in the table.
  - [ ] A test drives each of the six and its replacement with the same caller and asserts identical admit/refuse and identical refusal bodies.
- **Depends on:** Task 8

## Task 11: Move the engine-runtime groups onto the seam
- **Goal:** 26 rows — the largest group — while keeping bot-type gating intact.
- **Files:** `core/engine_runtime/relay.py`, `core/engine_runtime/gate.py`, `.../openapi_v1/engine_runtime/gating.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] The bar is derived from `gate.py:OPERATOR_LEVEL` (`MEMBER`) and recorded, with the docstring's "one bar for every operation on the surface" quoted as the evidence.
  - [ ] All 26 rows are `Check(MEMBER)`.
  - [ ] `require_bot_operator` is deleted from `relay.resolve_bot` (`relay.py:140`); `require_operable_bot` in `gating.resolve_operable_bot` **stays**, so an unsupported bot type still answers 501.
  - [ ] `relay.py:289`'s self-resolve (`resolve_bot(bot_id, owner_id, owner_id)`) is checked and its behaviour is unchanged.
  - [ ] Tests: below-bar caller refused byte-identically to an absent bot; a personal bot addressed at a published stage still answers 501, not 404.
- **Depends on:** Task 9

## Task 12: Adjudicate the sixteen retiring engine-runtime addresses
- **Goal:** Keep the path-addressed legacy twins checked once `require_bot_operator` is gone from the relay.
- **Files:** `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] All 16 legacy rows (approvals ×3, connection, engine ×3, models ×2, sessions ×7) are `Check(MEMBER)` with the default `bot_from="path"`.
  - [ ] A test drives each and its replacement with the same caller and asserts identical admit/refuse.
  - [ ] `INHERITED` falls from 42 to 26 — 20 twin an `OWNER_SCOPED` address and 6 are the legacy skills addresses Task 10 covers inside the `deprecated/` package.
- **Depends on:** Task 11

## Task 13: Decide and record what guards the operator credential
- **Goal:** Settle the one row where the plan proposes an exception, explicitly rather than by omission.
- **Files:** `core/engine_runtime/connection.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] The `GET /bots/{bot_id}/connection` row is `Check(MEMBER)`.
  - [ ] **A decision is recorded, with its argument, between:** keeping `require_bot_operator` inside `build` (`connection.py:205`) because the check guards *credential composition* — its own comment says the rule is about what may be composed, not how it is served, and what is composed grants `operator.admin` over every session on the device — **or** deleting it behind a test proving `build` has no caller other than this route.
  - [ ] Whichever is chosen, `connection.py`'s comment is rewritten to describe what is now true.
  - [ ] Tests: a below-bar caller receives no credential, byte-identically to an absent bot.
- **Depends on:** Task 12

## Task 14: Move the service-publication facade onto the seam
- **Goal:** 16 rows at two different bars, without breaking the edit lock that reads the same value.
- **Files:** `core/service_bot/services/service_publication_facade.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Each row's bar is derived from the `required_level=` argument its handler passes (`MEMBER` by default; `OWNER` at `:361`, `:429`, `:498`, `:699`) and recorded per row.
  - [ ] All 16 rows are `Check(MEMBER)` or `Check(OWNER)` to match.
  - [ ] Only `if level < required_level: raise ServicePublicationNotFoundError` is deleted from `_resolve_bot`. **`level` stays computed** — `:242`–`:266` use it for lock applicability and an OWNER branch — and the `require_service` bot-type refusal stays.
  - [ ] `_require_draft_lock` (`:544`) is untouched and still refuses when another collaborator holds the lock.
  - [ ] Tests: the lock still refuses; an OWNER-barred operation still refuses an ADMIN collaborator; `_actions` (`:245`, `:250`) reports the same list it does today, since it branches on the `level` this task keeps computing. The facade writes no audit rows of its own, so the seam's is the only one.
- **Depends on:** Task 13

## Task 15: Move the channels group onto the seam
- **Goal:** 6 rows at two bars, keeping the edit lock #1323 promised to leave alone.
- **Files:** `.../openapi_v1/channels/router.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Bars derived from `_require_admin(..., required_level=PermissionLevel.ADMIN)` (`:165`) for writes and from the read path for `GET`, recorded per row.
  - [ ] The 6 rows are `Check(MEMBER)` / `Check(ADMIN)` to match.
  - [ ] Every `_require_admin` call is deleted and the helper with them; **`_require_edit_lock` (`:173`) stays**, along with the documented 423 response.
  - [ ] `_authorize` (`:136`) survives if the lock still needs the resolved bot.
  - [ ] Tests: the lock still returns 423 when another collaborator holds it; a MEMBER is refused a write and admitted a read.
- **Depends on:** Task 14

## Task 16: Move the editors group onto the seam
- **Goal:** 5 rows, checked by the very service the endpoints manage.
- **Files:** `core/bot_collaborator/services/collaborator_service.py`, `.../openapi_v1/authorization.py`
- **Done when:**
  - [ ] Bars derived from each method's `required_level=` (`ADMIN` at `:325`, `:389`; `MEMBER` at `:419`; and the remaining sites the task enumerates) and recorded per row.
  - [ ] The 5 rows are `Check(MEMBER)` / `Check(ADMIN)` to match.
  - [ ] Only `_check_operable_permission` calls are deleted. **`_editor_policy.require_capability` (`:392`) and `require_team_space_member` (`:393`) stay** — they are capability and space checks, not collaborator bars.
  - [ ] The gate reading levels from the same service whose rows the handlers write is exercised by a test that adds an editor and immediately re-adjudicates.
  - [ ] Tests: refusal byte-identical to an absent bot; `DELETE /editors/me` still lets a MEMBER remove themselves. `collaborator_service` writes no audit rows of its own, so the seam's is the only one.
- **Depends on:** Task 15

## Task 17: Tests & Verification
- **Goal:** Ensure the feature meets every spec acceptance criterion.
- **Files:** `tests/community/adapters/http/openapi_v1/`, `src/backend/docs/openapi-v1/README.md`
- **Done when:**
  - [ ] Task 4's inverse assertion is un-`xfail`ed and passes: the only remaining `ServiceChecked` rows are the 6 harness rows.
  - [ ] `scaffolding_row_count()` has fallen from 181 to **72** — 93 `ServiceChecked` rows leave (91 to `Check`, 2 to `NoCheck`) and 16 `INHERITED` twins become `Check`, so 109 in total. The end state is 6 `ServiceChecked`, 66 `NoCheck`, 40 `OWNER_SCOPED`, 26 `INHERITED`, 107 `Check`, summing to the table's 245. The numbers are asserted, not described.
  - [ ] Every `Check` row's handler declares `OwnerIdDep` and its route declares `{bot_id}` on the path — both enforced at assembly by Task 1, now also asserted over the live surface.
  - [ ] No operation outside the migrated set changed which callers it admits or refuses.
  - [ ] `docs/openapi-v1/README.md`: the five-modes table, the burn-down numbers and a new dated changelog entry all reflect the end state.
  - [ ] Full backend suite and `scripts/ci/python_sast_local.sh` in changed-files mode both clean.
- **Depends on:** Task 16

---

## Groups

- **Group A — Make the limit enforced:** Tasks 1, 2
  - Theme: The prerequisite, and it does **not** touch the seam. Assembly starts refusing a `Check` row the gate could not key on, and the no-adopter assertion becomes a burn-down one. No row changes mode; nothing about the surface changes yet.
- **Group B — Say what the bot-chat operations really do:** Task 3
  - Theme: A correction, not a migration. Two rows stop claiming a check that does not exist.
- **Group C — First adopters:** Tasks 4, 5
  - Theme: The smallest, least entangled groups — no lock, no audit, no twins — put the `Check` path in front of real callers.
- **Group D — Authorized apps:** Tasks 6, 7
  - Theme: The one group needing a handler change before its rows can flip; the change lands separately from the flip so each is reviewable alone.
- **Group E — Skill centre:** Tasks 8, 10, 9
  - Theme: 29 rows behind two MEMBER checks, both services' audit writes reconciled, and the six retiring addresses that cannot be adjudicated keeping their check inside the `deprecated/` package. Task 10 lands before Task 9 so nothing is uncovered in between.
- **Group F — Engine runtime:** Tasks 11, 12, 13
  - Theme: The largest group and its sixteen path-addressed twins, keeping bot-type gating intact, and the argued decision about what guards the operator credential.
- **Group G — The entangled three:** Tasks 14, 15, 16
  - Theme: The groups where the permission check shares code with something that must survive it — a computed level, an edit lock, a capability policy.
- **Group H — Verification:** Task 17
  - Theme: Final spec acceptance check and the documentation end state.
