# Tasks: Admit the App-Principal-Only Caller Against a User's Grant

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Branch: `claude/sdd-implementation-0m0aaj`, based on `dev`.
>
> **Revised twice at the gate.** Draft 1 dropped `user_id` and derived the owner
> from `(app_id, bot_id)`; draft 2 kept the grant owner-only. Both were wrong.
> `user_id` stays a required parameter and names the **delegating user**; the
> grant gains that field alongside `owner_id`; and a member-level collaborator
> may delegate the access they have.
>
> **The invariant is the feature.** An application's reach is exactly its
> granting user's reach, re-adjudicated per request, never a snapshot. Task 9's
> test is the one that proves it; if anything in this list gets cut, not that.
>
> **The negative promise.** A caller naming an end user must come out of this
> bit-for-bit unchanged on all 63 operations, and no *request* schema may change.
> The single response change is additive. An existing test expectation edited to
> accommodate this work is a scope escape, not a fix.

---

## Task 1: Add the delegating user to the record  `[x]`

> **Time-boxed by reality.** The tables are deployed but unpopulated, so this is
> an `ALTER` with nothing to backfill. Verify that is still true before writing
> it.

- **Goal:** `ac_bot_app_grant` and its log carry the delegating user, keyed on it.
- **Files:** `core/bot_app_grant/models.py`, `core/bot_app_grant/sql/`,
  `core/bot_app_grant/README.md`
- **Done when:**
  - [x] **First:** confirm both tables are empty. **Not verifiable from here** —
        this environment has no access to the deployed database. Carried on the
        user's statement that the tables are unpopulated. The migration states
        the assumption at the top and spells out the backfill it needs if that
        has stopped being true, so applying it blind fails loudly rather than
        silently mis-migrating.
  - [x] `user_id VARCHAR(256) NOT NULL` added to both tables, with an explicit
        `COLLATE utf8mb4_bin` and a comment saying why. **Collation is in the
        DDL only, not the ORM** — SQLite is the local runtime and has no
        `utf8mb4_bin`, so declaring it on the model would break `create_all`.
        Every comparison on the column is against a bound parameter rather than
        another column, so the runtimes agree regardless; noted at both sites.
  - [x] `uk_bot_app_grant_scope` rekeyed to
        `(avernet_tenant, app_id, bot_id, user_id, env)`, with the arithmetic and
        the collision argument in the comment.
  - [x] `idx_bot_app_grant_app_owner` → `idx_bot_app_grant_app_user` on
        `(avernet_tenant, app_id, user_id, env)`.
  - [x] `idx_bot_app_grant_bot_owner` left **unchanged**, with the note on its
        `(tenant, bot_id)` prefix serving the owner's listing and the sweep.
  - [x] `sql/2026_08_11_bot_app_grant_delegating_user.sql` carries the `ALTER`s;
        the `CREATE` updated to match. Verified by compiling the ORM metadata and
        diffing against both files — same columns in the same order, same four
        indexes.
  - [x] `BotAppGrantRecord` gains `user_id`; README rewritten around the record's
        new meaning and the live-reach invariant.
- **Depends on:** —

---

## Task 2: Rekey the repository and service  `[ ]`

- **Goal:** Every read and write keys on the delegating user, and the app's view
  stops assuming granted bots belong to them.
- **Files:** `core/repository/protocols/bot/app_grant.py` and its implementation,
  `core/bot_app_grant/services/grant_service.py`, `api/bot_app_grant_service.py`
- **Done when:**
  - [ ] `grant()` takes `user_id` and `owner_id` as separate values.
  - [ ] `find(bot_id, user_id, app_id)` rekeyed from `owner_id`. **No new read
        member is needed for authorization** — the delegating user is on the
        wire, so this stays a unique-key probe.
  - [ ] `revoke(bot_id, user_id, app_id)` rekeyed, plus
        `revoke_all_for_app_on_bot(bot_id, app_id) -> int` for the owner's
        override.
  - [ ] `list_for_app(app_id, user_id)` rekeyed, and **its liveness filter
        fixed**: it runs `list_live_bot_ids_by_owner(owner_id)` today, which
        under the new model drops every shared granted bot. It becomes a
        liveness check by `bot_id`. A comment records that this was a real bug
        introduced by the model change, not a refactor.
  - [ ] `list_for_bot(bot_id)` no longer scopes by `owner_id`; the caller decides
        whether to narrow to one delegating user.
  - [ ] `revoke_all_for_bot(bot_id) -> int` — the deletion sweep, whoever
        delegated: deletes every live row and appends one `revoked` event per row
        in **one** `transactional_orm_session()`, log rows built from the live
        rows so the recorded `app_name` is the one at consent time.
  - [ ] Service tests for each, including two delegations of one app on one bot
        being independently withdrawable.
- **Depends on:** Task 1

---

## Task 3: Let a collaborator delegate  `[ ]`

- **Goal:** A user may authorize an application for any bot they can operate.
- **Files:** `adapters/http/openapi_v1/authorized_apps/router.py`
- **Done when:**
  - [ ] `grant_authorized_app` stops resolving through the owner-scoped
        `get_bot(bot_id, caller)` and adjudicates "may this user operate this
        bot" — owner, or collaborator at member level or above, reusing the
        existing gate rather than a second rule.
  - [ ] It records `user_id = caller` and `owner_id = the resolved bot's owner`.
  - [ ] A caller who may not operate the bot still gets the masked `404`.
  - [ ] **The module docstring is rewritten.** It currently argues the opposite
        — that the grant bar is deliberately narrower than the operate bar — and
        left as is it would contradict the code beneath it. The replacement must
        carry the counter-argument: a delegation is bounded by the delegator's
        live access and re-adjudicated per request, so it confers nothing they do
        not already hold and cannot outlive it.
- **Depends on:** Task 2

---

## Task 4: The owner's visibility and override  `[ ]`

- **Goal:** A bot's owner can see and stop any machine access to their bot.
- **Files:** `authorized_apps/router.py`, `authorized_apps/schemas.py`
- **Done when:**
  - [ ] `GET /bots/{bot_id}/authorized-apps` returns **every** live grant on the
        bot for the owner, and only the caller's own for a non-owner
        collaborator.
  - [ ] `AuthorizedApp` gains the delegating user — an **additive** response
        change, and the only response change in this feature. Without it the
        owner cannot tell who let an application in.
  - [ ] `DELETE /bots/{bot_id}/authorized-apps/{app_id}`: for the owner it
        removes **every** delegation of that application on that bot; for a
        collaborator, only their own. A comment records why the path needs no new
        segment: the key is now `(app_id, bot_id, user_id)`, so the operation had
        to say which delegation it means, and "all of this app's" is what an
        owner means by revoking an app's access to their bot.
  - [ ] Tests for both roles on both operations.
- **Depends on:** Task 3

---

## Task 5: Revoke grants when a bot is deleted  `[ ]`

- **Goal:** Deleting a bot withdraws every authorization against it.
- **Files:** `core/bot_management/services/bot_service.py`, `di/modules/`
- **Done when:**
  - [ ] `BotService.delete_bot` calls `revoke_all_for_bot` **before**
        `soft_delete_by_owner`, via a provider callable following
        `_device_service_provider`, with a comment carrying the ordering
        argument: a failure must abort the deletion, never leave a deleted bot
        with live grants.
  - [ ] Failures propagate; nothing is caught and logged-as-success.
  - [ ] Tests: grants → delete leaves none and one `revoked` row each; delete
        with no grants succeeds; a repository failure aborts the deletion.
  - [ ] The README's "Known gap, carried deliberately" note is **deleted**.
- **Depends on:** Task 2

---

## Task 6: Move the admission guard out of the verifier  `[ ]`

> The highest-risk edit here. The guard **moves up one layer; it is not deleted.**

- **Files:** `core/gateway_principal/verifier.py`, its `README.md`,
  `adapters/http/openapi_v1/dependencies.py`
- **Done when:**
  - [ ] `_require_user_principal` → `_require_admissible_principal`: refuses a set
        naming neither a `user` nor an `app`; keeps the blank-subject-id check.
  - [ ] Its docstring is rewritten to say **where the guard went** and why that
        placement still holds for routes nobody has written. A reader arriving
        from #950 must not be sent looking for a rule that moved.
  - [ ] `VerifiedCaller.has_user` and `.app_id -> int | None` added, `None`
        documented as "names no application".
  - [ ] `require_principal` gains the end-user requirement;
        `require_operating_caller` added as the opt-in. Both funnel into the same
        `MissingPrincipalError` / `1008`.
  - [ ] `resolve_avernet_tenant`'s docstring extended for the app-only case.
  - [ ] Verifier tests across the admission matrix.
  - [ ] **No route behavior changes in this task.** The existing suite passes
        untouched.
- **Depends on:** —

---

## Task 7: The admission table and the acting caller  `[ ]`

- **Files:** `adapters/http/openapi_v1/admission.py` (new)
- **Done when:**
  - [ ] `AdmissionMode` and `ADMISSION` cover **every** public operation — all 63,
        none omitted — grouped and commented by mode.
  - [ ] The module docstring states the rule each mode encodes, and that the mode
        follows from the operation's *shape*: which ids it takes and how it
        resolves its bot.
  - [ ] The A1/A2 split is explained: A1 resolves by `get_by_id_and_owner`, so a
        shared bot is unreachable there for a human too and the invariant needs
        no special handling; A2 takes a second owner parameter and admits
        collaborators, which is where delegation pays off.
  - [ ] Mode D entries carry their individual reasons.
  - [ ] `ActingCaller` with `user_id`, `app_id: int | None`, `require_bot()`
        returning the bot's `owner_id`, and `granted_bot_ids()` returning `None`
        for a human (no filtering) versus an empty set (granted nothing).
- **Depends on:** —

---

## Task 8: The seam — authorize `user_id`, resolve the owner  `[ ]`

- **Files:** `adapters/http/openapi_v1/principal.py`,
  `engine_runtime/params.py`, `errors.py`, `responses.py`, `adapters/http/app.py`
- **Done when:**
  - [ ] `require_user_id` keeps its signature and required `user_id`, and gains
        the app-only branch. Its docstring's promise — "stops comparing the two
        ids and asks whether the delegation was granted" — is rewritten as
        delivered.
  - [ ] The user-bearing path is unchanged: `422` absent, `403` mismatched.
  - [ ] `resolve_owner_id` gains the app-only branch: default to the **grant's**
        `owner_id` rather than to `user_id`, and refuse a supplied value that
        disagrees. A comment marks this as the single point where the app-only
        path differs from the human path on the 16 A2 operations.
  - [ ] `GrantCheckedDep` reads `bot_id` from `path_params` then `query_params`
        (path first, with the reason) and refuses when neither carries one.
  - [ ] `GrantNotResolvableError` → `(404, "Not found")` **byte-identical** to
        `BotNotFoundError`, with an `app.py` handler alongside
        `UserIdMismatchError`, because a dependency-raised error never reaches
        `@envelope_errors`. A comment records why it is `404` and not `403`.
  - [ ] The grant probe runs once per request; `_for_log` bounding applies to
        app-only refusals.
- **Depends on:** Tasks 2, 6, 7

---

## Task 9: Turn on Modes A1 and A2  `[ ]`

- **Files:** the `bots`, `identity`, `resources`, `routines`, `skills`,
  `sessions`, `engine`, `models`, `approvals`, `connection` routers
- **Done when:**
  - [ ] Every A1/A2 operation whose `bot_id` is on the wire takes the
        grant-checked dependency in place of `UserIdDep`.
  - [ ] **No handler body changes** in this task. If a body needs editing, stop —
        the check is in the wrong place.
  - [ ] `connection`'s gate runs inside `EngineConnectionService`, so the
        owner-substitution is applied at that seam, with a comment saying it is
        the one group where the router does not show the adjudication.
  - [ ] **The invariant test**, and it is the one that must not be cut: U
        collaborates on P's bot at member level and grants app A; A reaches the
        bot; **removing U as a collaborator refuses A on the next request with
        the grant row still present**; re-adding restores it.
  - [ ] A acting as a member-level U is refused exactly what a member-level U is
        refused, by the gate rather than by a new rule.
  - [ ] A shared bot stays unreachable on A1 groups even with a grant, because it
        is unreachable for the human too.
  - [ ] A2 with `owner_id` naming anyone but the grant's owner → `404`; with it
        omitted → resolved from the record.
- **Depends on:** Task 8

---

## Task 10: Mode A where the bot is not on the wire  `[ ]`

> The five operations that would otherwise pass unchecked.

- **Files:** `routines/router.py`, `skills/router.py`, the skill query service
- **Done when:**
  - [ ] `POST /bots/routines` calls `require_bot(body.bot_id)` immediately after
        parsing, before any service call.
  - [ ] The four `skills/{skill_id}` routes resolve the skill's `bot_id` and
        check the grant **before** acting, reading through the existing
        user-scoped skill path so another user's skill is refused first.
  - [ ] If clean pre-handler resolution proves impossible, these four move to
        Mode D and `spec.md` Decision 2 is amended. **Admitting them unchecked is
        not an option** — the service scopes by user only, so an application
        would reach a skill on an ungranted bot.
  - [ ] Tests: each of the five refuses when the resolved bot is not granted.
- **Depends on:** Task 8

---

## Task 11: Modes B, C and C-open  `[ ]`

- **Files:** `bots/router.py`, `authorized_apps/router.py`, `mcp/router.py`
- **Done when:**
  - [ ] `GET /openapi/v1/bots` narrows to `granted_bot_ids()` when set; the
        user's own call is unfiltered.
  - [ ] Filtering happens **before** pagination, with a comment on why: filtering
        a page afterwards leaks the size of what was withheld and returns short
        pages.
  - [ ] `GET /bots/authorized` accepts the app-only caller and is the **complete**
        discovery view — a granted bot the delegating user does not own appears
        here and in no bot listing.
  - [ ] `GET /bots/ceiling` admits an app-only caller holding ≥1 live grant from
        the named user, `404` otherwise.
  - [ ] `GET /bots/check-name` and the three MCP catalogue reads admit on
        authentication alone, with a comment recording why that is not a new
        exposure.
  - [ ] The three MCP **configuration** operations stay refused, reason in the
        router.
- **Depends on:** Task 8

---

## Task 12: The fail-closed inventory test  `[ ]`

> The test the issue asks for by name.

- **Files:** `tests/community/adapters/http/openapi_v1/test_principal_seam.py`
- **Done when:**
  - [ ] Every route on the built app appears in `ADMISSION` exactly once — on the
        surface and absent from the table fails, and vice versa.
  - [ ] Each route's declared dependency matches its mode; no route declares both
        `require_principal` and `require_operating_caller`.
  - [ ] Every A1/A2 route either takes the grant-checked dependency or is one of
        the five in Task 10, named explicitly so the exception list cannot grow
        silently.
  - [ ] The failure message names the offending routes and says what to do.
  - [ ] `test_public_routes_require_principal` is strengthened, not replaced.
- **Depends on:** Tasks 9, 10, 11

---

## Task 13: Behavioral tests  `[ ]`

- **Files:** a new app-only module under
  `tests/community/adapters/http/openapi_v1/`, plus existing suites
- **Done when:**
  - [ ] No grant → `404` compared **byte-for-byte** against nonexistent-bot;
        grant for another bot, another application, or another delegating user →
        `404`; deleted bot → refused.
  - [ ] Mode B: two bots, one granted → one returned and the count says one; the
        user's own call returns both; no grants → empty `200`; a granted shared
        bot appears in the application's own view.
  - [ ] Mode D: `401` on **all fourteen**, enumerated from `ADMISSION` rather than
        sampled, so the list cannot rot.
  - [ ] Access-key-only and bot-only callers → `401`, including on admitted routes.
  - [ ] Owner override: owner sees a collaborator's grant with its delegator and
        can withdraw it; the collaborator sees only their own.
  - [ ] The existing user-caller suites pass **with no expectation edited**.
- **Depends on:** Tasks 9, 10, 11

---

## Task 14: The gateway route-security rules  `[ ]`

- **Files:** `src/gateway/configs/application.yaml`,
  `src/gateway/tests/unit/core/authn/`
- **Done when:**
  - [ ] `/openapi/v1/bots/**` becomes `{user: optional, app: optional}`, with the
        Mode D overrides restoring `user: required` (and `app: required` where it
        is required today).
  - [ ] A comment explains, in this file's voice: why the **refusals** are
        enumerated rather than the admissions; why both identities are optional;
        and that "neither present" now dies at the backend's `require_principal`
        — a **relocation** of the refusal, not a removal.
  - [ ] `RouteSecurity.resolve` tests assert `user: required` for exactly the Mode
        D paths, derived from `ADMISSION` so the two expressions cannot drift.
  - [ ] Gateway tests run explicitly; the module has no standalone lint step.
- **Depends on:** Tasks 9, 10, 11

---

## Task 15: Documentation and the published description  `[ ]`

- **Files:** `core/bot_app_grant/README.md`, `openapi_v1/__init__.py`,
  `docs/openapi-v1/`
- **Done when:**
  - [ ] The grant README's "nothing here admits such a caller today" is replaced,
        its Context Boundary lists the new members, and its model description
        carries the delegation meaning and the invariant.
  - [ ] `openapi_v1/__init__.py` gains a section on the admission modes and the
        two id models, pointing at `admission.py`. Its existing note on why
        bot-logs sits outside the caller-scope rule is extended — the same
        asymmetry is why bot-logs is Mode D.
  - [ ] The `user_id` description on admitted operations says what the parameter
        means for an application caller.
  - [ ] The published description is regenerated and the diff inspected: **no
        request-schema change may appear**; the only response change is the
        additive delegating user on `AuthorizedApp`.
- **Depends on:** Tasks 9, 10, 11, 14

---

## Task 16: Spec acceptance verification  `[ ]`

- **Files:** `specs/2026-08-10-openapi-v1-app-only-caller/spec.md`
- **Done when:**
  - [ ] Every checkbox in `spec.md` is ticked or explicitly struck with a reason
        recorded in the file.
  - [ ] All three Open Questions are closed in the file.
  - [ ] Backend SAST/lint passes for the changed modules; gateway tests run.
  - [ ] The negative promise verified deliberately: `git diff` shows no edited
        expectation in a pre-existing test and no request-schema change.
- **Depends on:** Tasks 12, 13, 14, 15

---

## Groups

- **Group A — The record:** Tasks 1, 2
  - Theme: The delegation the rest of the feature reads. Lands green on its own;
    the `ALTER` is only free while the tables are empty.
- **Group B — Delegation by a collaborator:** Tasks 3, 4, 5
  - Theme: Who may grant, who may see and stop it, and deletion meaning
    deletion. Complete and useful with no app-only caller in sight.
- **Group C — Make the caller expressible:** Tasks 6, 7
  - Theme: The guard moves up a layer and the policy is written down. No route
    behavior changes; the suite must be green with the surface unchanged.
- **Group D — The seam:** Task 8
  - Theme: `user_id` authorized against the grant, `owner_id` resolved from it.
- **Group E — Turn it on:** Tasks 9, 10, 11
  - Theme: The modes applied, ordered by how much each handler changes — none,
    then a check, then a filter.
- **Group F — Prove and publish:** Tasks 12, 13, 14, 15
- **Group G — Verification:** Task 16
