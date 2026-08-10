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
> bit-for-bit unchanged on all 71 operations, and no *request* schema may change.
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

## Task 2: Rekey the repository and service  `[x]`

- **Goal:** Every read and write keys on the delegating user, and the app's view
  stops assuming granted bots belong to them.
- **Files:** `core/repository/protocols/bot/app_grant.py` and its implementation,
  `core/bot_app_grant/services/grant_service.py`, `api/bot_app_grant_service.py`
- **Done when:**
  - [x] `grant()` takes `user_id` and `owner_id` as separate values.
  - [x] `find(bot_id, user_id, app_id)` rekeyed. Still a unique-key point probe;
        its docstring now says explicitly that a record coming back means the
        delegation exists, **not** that the request may proceed — the live
        collaborator check is the caller's separate job.
  - [x] `revoke(bot_id, user_id, app_id)` rekeyed, plus
        `revoke_all_for_app_on_bot(bot_id, app_id)`.
  - [x] `list_for_app(app_id, user_id)` rekeyed, **and its liveness filter
        fixed**. Needed a new repository member: no owner-blind liveness read
        existed, so `BotRepository.filter_live_bot_ids(bot_ids) -> set[str]`
        was added beside `list_live_bot_ids_by_owner`. One query, as before.
  - [x] `list_for_bot(bot_id)` no longer scopes by `owner_id`.
  - [x] `revoke_all_for_bot(bot_id) -> int`. Both sweeps share one `_sweep`
        helper so they cannot drift in locking or logging, and both build log
        rows from the live rows — the only way they can record a delegator they
        were never told about.
  - [x] Service tests: 28 pass, including two collaborators delegating the same
        app on the same bot, independent withdrawal, the owner override, both
        sweeps, and a granted bot the delegator does not own surviving the
        liveness filter.
  - [x] The test double `_LiveBots` was modelling one bot id under two owners,
        which is incoherent once the filter is owner-blind; rebuilt around a flat
        live set, matching production.

- **Depends on:** Task 1

---

## Task 3: Let a collaborator delegate  `[x]`

- **Goal:** A user may authorize an application for any bot they can operate.
- **Files:** `adapters/http/openapi_v1/authorized_apps/router.py`
- **Done when:**
  - [x] `grant_authorized_app` adjudicates "may this user operate this bot",
        reusing `core/engine_runtime/gate.py`'s `require_bot_operator` rather
        than restating the rule. Needed two supporting pieces: an owner-blind
        resolve (`BotService.get_bot_by_id`, which decides nothing on its own
        and says so loudly) and a group-level `authorized_apps/gating.py` that
        pairs resolve with adjudication so no operation can run only half of it.
  - [x] It records `user_id = caller` and `owner_id = the resolved bot's owner`.
  - [x] A caller who may not operate the bot still gets the masked `404`,
        pinned byte-for-byte against a nonexistent bot.
  - [x] The module docstring is rewritten, carrying the counter-argument and
        pointing at `gating.py` for the full reasoning.
  - [x] The other three operations moved onto the same gate in this task too —
        they shared the owner-scoped read being replaced, so leaving them behind
        would have left the tree incoherent between commits.
  - [x] `test_collaborator_may_operate_but_may_not_grant` asserted the policy
        being reversed; rewritten as
        `test_collaborator_may_delegate_the_access_they_have`, and a new
        `test_stranger_may_not_grant` pins that the widening admits
        collaborators rather than everyone.

- **Depends on:** Task 2

---

## Task 4: The owner's visibility and override  `[x]`

- **Goal:** A bot's owner can see and stop any machine access to their bot.
- **Files:** `authorized_apps/router.py`, `authorized_apps/schemas.py`
- **Done when:**
  - [x] `GET /bots/{bot_id}/authorized-apps` returns every live grant on the bot
        for the owner, and only the caller's own for a non-owner collaborator.
  - [x] `AuthorizedApp` gains `user_id` — the only response change in this
        feature, and additive. `AuthorizedBot` deliberately does **not**: there
        it would only echo the `user_id` the caller sent.
  - [x] `DELETE …/{app_id}`: the owner removes every delegation of that
        application on the bot; a collaborator only their own. Both the router
        and the schema carry the reasoning.
  - [x] Tests for both roles on both operations, including a collaborator being
        unable to reach across to the owner's delegation (404, not a silent
        success).

- **Depends on:** Task 3

---

## Task 5: Revoke grants when a bot is deleted  `[x]`

- **Goal:** Deleting a bot withdraws every authorization against it.
- **Files:** `core/bot_management/services/bot_service.py`, `di/modules/`
- **Done when:**
  - [x] `BotService.delete_bot` sweeps via a **required** provider callable.
        Required rather than defaulted-None: there is no "grants not
        configured" state worth modelling, and the alternative to a provider is
        a silent security hole.
  - [x] Placed **earlier than planned** — before the device release and passport
        destruction rather than just before `soft_delete_by_owner`. Same
        argument, taken further: a failure after those steps would leave a bot
        already unusable with live authorizations and no deletion left to
        re-trigger the sweep. Recorded in the router, the README and the tests.
  - [x] Failures propagate; nothing caught and logged as success.
  - [x] Tests: sweep called with the right scope, a failed sweep aborts before
        anything destructive, and an unauthorized bot deletes normally.
  - [x] The README's "Known gap, carried deliberately" note is replaced by the
        ordering argument.
  - [x] 17 test files construct `BotService` directly (11 by kwargs, 17 via
        `__new__`); all updated mechanically, then audited to confirm every
        inserted kwarg landed inside a `BotService(...)` call — one had gone
        into `BaasPublishPoller`, which shares the parameter name.
  - [x] `core/bot_management`'s Context Boundary declares the new dependency.

- **Depends on:** Task 2

---

## Task 6: Move the admission guard out of the verifier  `[x]`

> The highest-risk edit here. The guard **moves up one layer; it is not deleted.**

- **Files:** `core/gateway_principal/verifier.py`, its `README.md`,
  `adapters/http/openapi_v1/dependencies.py`
- **Done when:**
  - [x] `_require_user_principal` → `_require_admissible_principal`: refuses a
        set naming neither a user nor an app; `access_key` and `bot` stay
        refused outright; the blank-subject-id check is kept, and a blank user
        beside an app is still refused rather than demoted to "app-only".
  - [x] Its docstring says where the guard went and why the placement holds for
        routes nobody has written.
  - [x] `VerifiedCaller.has_user` and `.app_id -> int | None` added.
        `user_id`'s `""` fallback is now **reachable**, and its docstring
        reframes the property as "the end user the credential names", not
        "the user this request acts for".
  - [x] `require_principal` carries the end-user requirement;
        `require_operating_caller` added as the opt-in. Both refuse through one
        `_refuse` helper so the two failures are indistinguishable from outside.
        **Superseded in Task 8** — see the correction there and in `plan.md`:
        a route-level dependency cannot relax a router-level one, so
        `require_operating_caller` is dropped and `require_principal` consults
        the admission table instead. `_refuse` and everything else here stands.
  - [x] `resolve_avernet_tenant`'s docstring extended for the app-only case.
  - [x] Verifier tests updated to the new contract; six seam tests added for the
        admission split, including that an app-only refusal is byte-identical to
        no credential.
  - [x] **No route behavior changed**: the whole `openapi_v1` suite passes
        untouched (819 tests with the principal and architecture suites).

- **Depends on:** —

---

## Task 7: The admission table and the acting caller  `[x]`

- **Files:** `adapters/http/openapi_v1/admission.py` (new)
- **Done when:**
  - [x] `AdmissionMode` and `ADMISSION` cover **every** public operation.
        **The surface has 71 operations, not the 63 the artifacts claimed** —
        verified by enumerating the built router; spec and plan corrected. Mode
        counts land exactly where the plan predicted: 34 A1, 16 A2, 2 B, 1 C,
        4 OPEN, 14 REFUSED.
  - [x] The module docstring states the rule each mode encodes and that a mode
        follows from the operation's shape.
  - [x] The A1/A2 split is explained, including why A1 needs no special handling
        for shared bots — they are unreachable there for a human too.
  - [x] Mode D entries carry their individual reasons.
  - [x] `ActingCaller` with `user_id`, `app_id: int | None`, `require_bot()`
        returning the grant's owner, and `granted_bot_ids()` returning `None`
        for a human versus an empty set for an ungranted application.
  - [x] `BODY_BOT_ID_OPERATIONS` and `SKILL_SCOPED_OPERATIONS` name the five
        operations Task 10 handles, so the exception list is data rather than
        something a test hard-codes.
  - [x] `GrantNotResolvableError` added, documented as byte-identical to
        bot-not-found and explicitly not a 403.

- **Depends on:** —

---

## Task 8: The seam — authorize `user_id`, resolve the owner  `[x]`

- **Files:** `adapters/http/openapi_v1/principal.py`,
  `engine_runtime/params.py`, `errors.py`, `responses.py`, `adapters/http/app.py`
- **Done when:**
  > **Plan correction.** `require_operating_caller` (Task 6) cannot serve as a
  > per-route opt-in: `build_public_router` applies `require_principal` to every
  > route via `_PUBLIC_AUTH`, and FastAPI merges router-level dependencies into
  > each route — a route can add a check, never relax one. Verified directly.
  > `require_principal` therefore consults `ADMISSION` itself, which keeps the
  > single declaration and makes the fail-closed default *stronger*: an
  > operation absent from the table is refused, so a new route is refused by
  > omission rather than by remembering not to opt in.

  - [x] `require_operating_caller` removed; `require_principal` consults
        `ADMISSION` via the matched route in the connection scope, and refuses
        when the route is absent from the table.
  - [x] `require_user_id` keeps its signature and gains the app-only branch.
        **The branch requires a positively-identified application**, not merely
        "names no user" — testing the latter alone was fail-open, since every
        unusable credential also names no user, and the existing suite caught it.
  - [x] The user-bearing path is unchanged: `422` absent, `403` mismatched,
        `401` unverifiable.
  - [x] `caller_names_a_user` / `caller_app_id` mirror `caller_owner_id`'s
        tolerance for a bare string or mapping, so the surface's existing
        principal stand-ins keep working rather than all looking app-only.
  - [x] `resolve_owner_id` takes the addressed owner from the grant record and
        refuses a request naming a different one, before the resolve.
  - [x] `GrantCheckedDep` reads `bot_id` path-first then query, and refuses an
        application when neither carries one.
  - [x] The grant reader is resolved **lazily**, only for an application. A
        declared injection would have made the grant service a hard requirement
        of every route taking the dependency — and of every test app mounting
        one. Absent reader ⇒ `require_bot` refuses, so it fails closed.
  - [x] `GrantNotResolvableError` → `(404, "Not found")` in the same map as
        `BotNotFoundError`, with its own `app.py` handler.
  - [x] 821 tests pass across the public surface, the principal seam and the
        architecture gates, with no existing expectation edited.

- **Depends on:** Tasks 2, 6, 7

---

## Task 9: Turn on Modes A1 and A2  `[x]`

- **Files:** the `bots`, `identity`, `resources`, `routines`, `skills`,
  `sessions`, `engine`, `models`, `approvals`, `connection` routers
- **Done when:**
  - [x] All 50 A1/A2 operations take the grant check, and **exactly** those —
        verified by walking the built router's effective routes and diffing
        against `ADMISSION`. Declared at `include_router` for the four groups
        that are wholly A1 (identity, resources, routines, skills = 25 routes),
        per route for the mixed `bots` group (9), and transitively via
        `OwnerIdDep` for the 16 engine-runtime operations.
  - [x] **No handler body changed.**
  - [x] Two real bugs found and fixed while wiring:
        a **user+app** caller was being treated as an application, which would
        have put every ordinary collaborator request through a grant lookup and
        refused the ones with no grant; and `BotAppGrantService` had no `find`,
        so the probe reached a member that did not exist.
  - [x] **The invariant test** passes: remove the collaboration and the
        application is refused on its next request with the grant row intact;
        re-add it and access returns with no re-granting.
  - [x] A grant from a collaborator confers no more than that collaborator has.
  - [x] A2 with `owner_id` naming anyone but the grant's owner → 404 **before
        any forward**; omitted → resolved from the record; agreeing → accepted.
  - [x] A human caller addressing a shared bot is unchanged.

- **Depends on:** Task 8

---

## Task 10: Mode A where the bot is not on the wire  `[x]`

> The five operations that would otherwise pass unchecked.

- **Files:** `routines/router.py`, `skills/router.py`, the skill query service
- **Done when:**
  - [x] `POST /bots/routines` checks `body.bot_id` immediately after parsing,
        before any service call.
  - [x] The four `skills/{skill_id}` routes resolve the skill's bot through the
        existing user-scoped read — so another user's skill is refused before
        the grant is consulted — and check it before acting. The read is skipped
        entirely for a human caller.
  - [x] The shared dependency **defers** for exactly these five, read from
        `admission.py`'s two frozensets rather than inferred from a missing
        `bot_id`. Any *other* operation arriving with no bot id is refused, so
        the exception list cannot grow by accident.
  - [x] Tests: a skill on an ungranted bot **belonging to the same user** is
        refused on all four operations and is not touched; the granted bot still
        works; a routine on an ungranted bot is refused before creation.
  - [x] Not needed: the fallback to Mode D. Clean pre-handler resolution was
        available for all five.

- **Depends on:** Task 8

---

## Task 11: Modes B, C and C-open  `[x]`

- **Files:** `bots/router.py`, `authorized_apps/router.py`, `mcp/router.py`
- **Done when:**
  - [x] `GET /openapi/v1/bots` narrows to the granted set; a human caller is
        unfiltered. Needed a `bot_ids` filter threaded through
        `list_bots_by_conditions` and `list_by_conditions` — `None` means
        unrestricted, `[]` means none, and the two must stay distinguishable or
        "may reach nothing" becomes "show everything".
  - [x] Filtering happens in the query, **before** pagination, asserted through
        the call the service receives rather than the page that comes back.
        Granted nothing short-circuits without asking for a page to discard.
  - [x] `GET /bots/authorized` accepts the app-only caller and is the complete
        discovery view — pinned with a bot the delegating user does not own,
        which can never appear in the owner-scoped listing.
  - [x] `GET /bots/ceiling` admits an app holding ≥1 delegation from the named
        user, 404 otherwise.
  - [x] `GET /bots/check-name` admits on authentication alone; the three MCP
        catalogue reads inherit the same OPEN mode from the table.
  - [x] The three MCP configuration operations stay refused (table entries with
        their reason).

- **Depends on:** Task 8

---

## Task 12: The fail-closed inventory test  `[x]`

> The test the issue asks for by name.

- **Files:** `tests/community/adapters/http/openapi_v1/test_principal_seam.py`
- **Done when:**
  - [x] Every route appears in `ADMISSION` exactly once, and every entry is a
        live route — both directions, with messages naming the offenders and
        what to do.
  - [x] Every grant-checked mode actually runs the check, and **only** those do.
        Checked against the built router's *effective* dependencies: the check
        is declared three ways (group-level, per route, transitively via
        `OwnerIdDep`), so a test looking for one spelling would pass while the
        others rotted. The older helpers in this package walk
        `original_router`, which does **not** carry include-time dependencies —
        that would have made this test pass vacuously.
  - [x] The five deferring operations are named, are still grant-checked modes,
        and cannot overlap; the set size is pinned so growth is deliberate.
  - [x] Every mode is used, and `ADMITTING_MODES` is total.
  - [x] The failure messages name the routes and the fix.
  - [x] `test_public_routes_require_principal` strengthened rather than
        replaced, and extended to the socket plane, whose effective context
        carries neither path nor dependant.
  - [x] **Verified the guard fires**: removing one table entry fails two tests;
        restoring it returns the file byte-identical and the suite to green.

- **Depends on:** Tasks 9, 10, 11

---

## Task 13: Behavioral tests  `[x]`

- **Files:** a new app-only module under
  `tests/community/adapters/http/openapi_v1/`, plus existing suites
- **Done when:**
  - [x] No grant → 404; grants for another bot, another application or another
        delegating user → 404 (in the engine-runtime module, against the real
        gate).
  - [x] Mode B: narrowed listing, matching count, filtering before pagination,
        empty page for an ungranted app, unfiltered for a human, and a shared
        bot visible only in the application's own view.
  - [x] Mode D: **all fourteen** enumerated from `ADMISSION`, driven over the
        whole assembled surface — thirteen HTTP plus the socket handshake, which
        refuses with close code 1008 because it has no status to carry a 401.
  - [x] The app-only refusal is byte-identical to having no credential; a human
        caller still reaches those same operations.
  - [x] access-key and bot callers → 401 even on an admitting operation.
  - [x] Owner override tests live with the router (Task 4).
  - [x] **Full suite: 11337 passed**, 2 failed. Both are the same environmental
        failure — `rsync` is not installed in this sandbox — and are unrelated
        to this change. No existing expectation was edited.

- **Depends on:** Tasks 9, 10, 11

---

## Task 14: The gateway route-security rules  `[x]`

- **Files:** `src/gateway/configs/application.yaml`,
  `src/gateway/tests/unit/core/authn/`
- **Done when:**
  - [x] `/openapi/v1/bots/**` becomes `{user: optional, app: optional}`, with
        explicit `user: required` restored on every Mode D path. Three of those
        (`GET`/`DELETE` authorized-apps, the MCP configuration pair) previously
        *inherited* the requirement and would have been quietly opened.
  - [x] `GET /bots/authorized` relaxed to `{user: optional, app: required}` —
        it is the operation an integration calls to discover its own scope, and
        requiring a human would make that discoverable only by the person it
        acts for.
  - [x] The comment explains why the refusals are enumerated rather than the
        admissions, why both identities are optional, and that "neither" now
        dies at the backend — a **relocation** of the refusal, not a removal.
  - [x] Two stale comments corrected: they described the inheritance that no
        longer holds.
  - [x] Gateway tests: the wide rule admits a machine caller, every Mode D path
        still requires a human (parametrised), and the app view is
        user-optional/app-required. 527 gateway unit tests pass.
  - [x] Three socket tests asserted the wide rule's `user: required` as a proxy
        for "not exempt"; rewritten to assert what they are actually about, so
        they no longer break when the wide rule's requirements change.
  - [x] **Not derived from `ADMISSION` programmatically**, and deliberately so:
        the two live in separate packages, and reimplementing the path matcher
        on the backend side would be the second implementation of
        "most specific" that `gateway/core/paths/_pattern.py` exists to
        prevent. The list is mirrored on the gateway side with a comment naming
        the backend table as the authority, and `admission.py` points back.

- **Depends on:** Tasks 9, 10, 11

---

## Task 15: Documentation and the published description  `[x]`

- **Files:** `core/bot_app_grant/README.md`, `openapi_v1/__init__.py`,
  `docs/openapi-v1/`
- **Done when:**
  - [x] The grant README's stale line is gone (Task 1), its Context Boundary
        gains a `consumed_by` naming both new readers, and it says who reads
        `find` and when.
  - [x] `openapi_v1/__init__.py` gains a "Who may call" section covering the two
        caller shapes, the per-operation decision and the indistinguishable
        refusals, pointing at `admission.py`.
  - [x] The `user_id` description says what the parameter means for an
        application caller.
  - [x] `docs/openapi-v1/README.md`: the identity-admission row updated from
        "user only" and a changelog entry added.
  - [x] Published description regenerated and the diff **checked
        structurally**, not by eye: no path added or removed, no parameter,
        request body or response status changed on any of the 71 operations, and
        exactly one schema property added — `AuthorizedApp.user_id`.

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
