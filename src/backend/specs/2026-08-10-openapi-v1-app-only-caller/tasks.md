# Tasks: Admit the App-Principal-Only Caller Against an Owner's Grant

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Branch: `claude/sdd-implementation-0m0aaj`, based on `dev`.
>
> Scope reminder: this admits a caller carrying **only** an application identity,
> on **eleven named operations**, resolving the owner from the grant record #937
> shipped. It is the inverse of that feature — there both parties were present
> and the record was written; here only the application is present and the record
> stands in for the absent owner.
>
> **The negative promise is the load-bearing one.** A caller naming an end user
> must come out of this feature bit-for-bit unchanged on all 56 operations, and
> every operation outside the eleven must refuse the app-only caller. An existing
> test expectation edited to accommodate this work is a scope escape, not a fix.

---

## Task 1: Revoke grants when a bot is deleted  `[ ]`

> **The blocker, and it ships first.** Until this holds, resolution on
> `(app_id, bot_id)` would find a row for a bot its owner deleted. Doing it here
> rather than filtering at each reader is the whole point — the invariant holds
> for every reader at once.

- **Goal:** Deleting a bot withdraws every authorization standing against it, as
  part of the deletion, with the withdrawal recorded in history.
- **Files:**
  - `src/agentclaw/community/core/repository/protocols/bot/app_grant.py`
  - `src/agentclaw/community/core/repository/implementations/bot/app_grant.py`
  - `src/agentclaw/community/core/bot_app_grant/services/grant_service.py`
  - `src/agentclaw/community/api/bot_app_grant_service.py`
  - `src/agentclaw/community/core/bot_management/services/bot_service.py`
  - `src/agentclaw/community/di/modules/` (provider wiring)
- **Done when:**
  - [ ] `BotAppGrantRepositoryProtocol.revoke_all_for_bot(bot_id, owner_id) -> int`
        added as an `@abstractmethod` with a docstring saying why the count is
        returned (the caller logs it; nothing branches on it).
  - [ ] The implementation deletes every live row for the bot and appends one
        `revoked` log event per deleted row, inside **one**
        `transactional_orm_session()`. Log rows are built from the live rows, not
        from arguments, so the recorded `app_name` is the one at consent time.
  - [ ] `BotAppGrantService.revoke_all_for_bot(*, bot_id, owner_id) -> int`, with
        a docstring distinguishing it from `revoke`: no `GrantNotFoundError`, because
        "the bot had no authorizations" is a normal outcome of deletion, not a
        caller error.
  - [ ] `BotService.delete_bot` calls it **before** `soft_delete_by_owner`, via a
        provider callable following `_device_service_provider`. A comment states
        the ordering argument: a failure must abort the deletion, never leave a
        deleted bot with live grants.
  - [ ] Failures propagate. Nothing is caught and logged-as-success.
  - [ ] Tests: grant → delete leaves no live row and exactly one `revoked` log
        row per grant; delete with no grants succeeds; a repository failure
        aborts the deletion and leaves the bot undeleted.
  - [ ] `core/bot_app_grant/README.md`'s "Known gap, carried deliberately" note
        is **deleted**, not amended — the gap is closed.
- **Depends on:** —

---

## Task 2: Move the admission guard out of the verifier  `[ ]`

> The single highest-risk edit in this workstream. The guard **moves up one
> layer; it is not deleted.** Verification stops requiring an end user and starts
> requiring "an end user *or* an application"; `require_principal` takes over the
> stricter rule for every route that does not opt in.

- **Goal:** Make "an application with no end user" a verifiable identity set,
  while every existing route keeps refusing it.
- **Files:**
  - `src/agentclaw/community/core/gateway_principal/verifier.py`
  - `src/agentclaw/community/core/gateway_principal/README.md`
  - `src/agentclaw/community/adapters/http/openapi_v1/dependencies.py`
- **Done when:**
  - [ ] `_require_user_principal` → `_require_admissible_principal`: refuses a set
        naming neither a `user` nor an `app`; keeps the blank-subject-id check
        for a user it does name; keeps naming the carried types for the operator
        only.
  - [ ] Its docstring is rewritten. The current one names this issue as the place
        to lift the guard; the replacement must say **where the guard went** and
        why that placement still holds for routes nobody has written yet. A
        reader arriving from #950 must not be sent looking for a rule that moved.
  - [ ] `VerifiedCaller.has_user -> bool` and `VerifiedCaller.app_id -> int | None`
        added, with `None` documented as the real state "this set names no
        application".
  - [ ] `VerifiedCaller.user_id`'s docstring updated: `""` is now reachable (an
        app-only caller) and `caller_owner_id` turning it into `401` is the
        wanted answer, not a degraded one.
  - [ ] `VerifiedCaller.tenant`'s docstring updated where it cites
        `_require_user_principal` for "asserts no tenant ⇒ user-only". Still
        true — an app asserts a tenant — but the citation must point at the
        renamed guard.
  - [ ] `require_principal` gains the end-user requirement, funnelling into the
        same `MissingPrincipalError` / `1008` refusal so no caller can tell which
        half failed.
  - [ ] `require_operating_caller` added: admits a user-bearing **or** app-only
        set. Docstring states it is the opt-in and that declaring it is the only
        way a route accepts a user-less caller.
  - [ ] `resolve_avernet_tenant`'s docstring extended: it now resolves an
        app-only caller's tenant on routes that will still `401`, and the safety
        argument is the existing one.
  - [ ] Verifier tests: app-only admitted; user+app admitted; user-only
        unchanged; access-key-only and bot-only refused; blank user subject id
        still refused; `app_id` read off the set; tenant contradiction unchanged.
  - [ ] **No route behavior changes in this task.** The full existing public-API
        suite passes untouched.
- **Depends on:** —

---

## Task 3: Declare the admitted operations  `[ ]`

- **Goal:** One place that says which operations accept the app-only caller.
- **Files:** `src/agentclaw/community/adapters/http/openapi_v1/app_only_routes.py` (new)
- **Done when:**
  - [ ] `APP_ONLY_OPERATIONS: frozenset[tuple[str, str]]` holds the eleven
        `(method, FastAPI path template)` pairs from `plan.md`, grouped and
        commented by family (bot lifecycle / sessions / messages / download).
  - [ ] The module docstring states the rule the list encodes: a grant is
        all-or-nothing per bot, the list is a property of the surface identical
        for every grant, and adding an entry is a change to an authorization
        boundary rather than to a table.
  - [ ] It records **why each family is in** and, explicitly, why bot create /
        update / delete, authorization management, bot logs and resource
        mutations are out.
- **Depends on:** —

---

## Task 4: Resolve the acting owner from the grant  `[ ]`

- **Goal:** Given an app-only caller and a bot, produce the owner the request
  acts for — from the record, never from the wire.
- **Files:**
  - `src/agentclaw/community/core/repository/protocols/bot/app_grant.py`
  - `src/agentclaw/community/core/repository/implementations/bot/app_grant.py`
  - `src/agentclaw/community/core/bot_app_grant/services/grant_service.py`
  - `src/agentclaw/community/api/bot_app_grant_service.py`
- **Done when:**
  - [ ] `find_by_app_and_bot(app_id, bot_id) -> Optional[BotAppGrantRecord]` added
        to the protocol. Its docstring states that taking **no `owner_id`** is the
        point — the owner is the output — and that the tenant is absent because
        the tenant guard appends its own predicate to every read.
  - [ ] The implementation filters `(app_id, bot_id, env)`, orders by `id`, takes
        the first, and logs a warning when more than one row matched, naming what
        an ambiguous match would mean (whose data is read). A comment notes that
        legacy `default` bot ids are non-unique across tenants and that the
        tenant guard is what confines the lookup.
  - [ ] `BotAppGrantService.resolve_owner(*, app_id, bot_id) -> str`, raising
        `GrantNotFoundError` rather than returning `str | None`. Docstring states
        why: a `None` here is one `if` away from a request scoped by nothing.
  - [ ] Service-level tests: resolves; no grant → raises; grant for another bot →
        raises; grant held by another app → raises; a grant in another tenant is
        invisible (guard-driven).
- **Depends on:** Task 1 (so no test can resolve a deleted bot's grant)

---

## Task 5: The acting-owner dependency  `[ ]`

- **Goal:** One seam the eleven operations take their owner from, behaving
  identically to today for a user caller and reading the grant for an app-only
  one.
- **Files:**
  - `src/agentclaw/community/adapters/http/openapi_v1/principal.py`
  - `src/agentclaw/community/adapters/http/openapi_v1/errors.py`
  - `src/agentclaw/community/adapters/http/openapi_v1/responses.py`
  - `src/agentclaw/community/adapters/http/app.py`
- **Done when:**
  - [ ] `require_acting_owner` / `ActingOwnerDep` added, with `user_id` declared
        `str | None = None`. A comment justifies the optional against AGENTS.md:
        `None` is an intentional contract state (an app-only caller supplies
        none) and both branches define behavior — not defensive widening.
  - [ ] User-bearing branch is byte-identical in outcome to `require_user_id`:
        absent `user_id` → `422`, mismatched → `403`, match → the id. The `422`
        is now raised explicitly rather than by FastAPI's required-parameter
        check, and a test pins that the envelope is unchanged.
  - [ ] App-only branch: `user_id` present → `403` (refused, never ignored);
        otherwise resolve `bot_id` then the owner.
  - [ ] `_request_bot_id(connection)` reads `path_params` first, then
        `query_params`, with the ordering argument in the docstring, and refuses
        when neither carries one.
  - [ ] `GrantNotResolvableError` added to `errors.py`, mapped to
        `(404, "Not found")` in `responses.py` **byte-identical** to
        `BotNotFoundError`, and given an `app.py` handler alongside
        `UserIdMismatchError` — a dependency-raised error never reaches
        `@envelope_errors`.
  - [ ] `_for_log` bounding applies to app-only refusals too.
  - [ ] A description constant for the eleven operations' `user_id`, stating
        required-for-user / refused-for-application. `USER_ID_DESCRIPTION` is
        left untouched for the other 45.
- **Depends on:** Tasks 2, 3, 4

---

## Task 6: Swap the dependency on the eleven operations  `[ ]`

- **Goal:** The admitted operations accept the app-only caller; nothing else in
  their bodies changes.
- **Files:**
  - `src/agentclaw/community/adapters/http/openapi_v1/bots/router.py`
  - `src/agentclaw/community/adapters/http/openapi_v1/engine_runtime/sessions/router.py`
  - `src/agentclaw/community/adapters/http/openapi_v1/resources/router.py`
- **Done when:**
  - [ ] Each of the eleven takes `owner_id: ActingOwnerDep` in place of
        `UserIdDep`, and declares the eleven-operation `user_id` description.
  - [ ] **No handler body changes.** Once the owner is resolved, every downstream
        call — `bot_service.get_bot(bot_id, owner_id)`, the ownership-masked
        `404`, the tenant guard — is the same code on the same values. If a body
        needs editing, stop: it means the resolution seam is in the wrong place.
  - [ ] `PUT` / `DELETE /openapi/v1/bots/{bot_id}` and every resource mutation
        are visibly **not** touched, and a comment at the top of each edited
        router names its admitted operations and points at
        `app_only_routes.py`.
- **Depends on:** Task 5

---

## Task 7: The fail-closed route-inventory test  `[ ]`

> The test the issue asks for by name: adding a route must not silently inherit
> the relaxation.

- **Goal:** Make "not opted in ⇒ refused" a property the suite enforces, not one
  a reviewer remembers.
- **Files:** `tests/community/adapters/http/openapi_v1/test_principal_seam.py`
- **Done when:**
  - [ ] Every route on the built public app depends on `require_principal` **or**
        `require_operating_caller`, never both and never neither.
  - [ ] The set of `(method, path)` depending on `require_operating_caller`
        **equals** `APP_ONLY_OPERATIONS`. Set equality, so a route added to the
        surface without the list, or to the list without the surface, both fail.
  - [ ] The failure message names the offending routes and says what to do —
        this test fires for someone who has never read this spec.
  - [ ] The existing `test_public_routes_require_principal` is strengthened
        rather than replaced, and its docstring explains the two-dependency rule.
- **Depends on:** Task 6

---

## Task 8: Behavioral tests for the admitted path  `[ ]`

- **Goal:** Pin every promise in `spec.md`'s acceptance criteria.
- **Files:** `tests/community/adapters/http/openapi_v1/` (new module for the
  app-only path), plus the existing router suites.
- **Done when:**
  - [ ] App-only happy path on an admitted operation returns the same body the
        owner's own call returns.
  - [ ] No grant → `404`, and the response is compared **byte-for-byte** against
        the nonexistent-bot response.
  - [ ] `user_id` supplied by an app-only caller → `403`.
  - [ ] Grant for a different bot, and a grant held by a different application,
        both → `404` — resolution never widens.
  - [ ] App-only caller → `401` on a sample of non-admitted operations, including
        the adjacent siblings `PUT /bots/{bot_id}`, `DELETE /bots/{bot_id}` and
        `POST /bots/resources/upload`.
  - [ ] Access-key-only and bot-only callers → `401`, including on the eleven.
  - [ ] A bot deleted after the grant → refused through the resolution path.
  - [ ] The existing user-caller suites pass **with no expectation edited**.
- **Depends on:** Tasks 6, 7

---

## Task 9: The gateway route-security rules  `[ ]`

- **Goal:** Let the App identity reach the backend on the eleven paths, and
  nowhere else.
- **Files:**
  - `src/gateway/configs/application.yaml`
  - `src/gateway/tests/unit/core/authn/` (resolution tests)
- **Done when:**
  - [ ] Rules added for the admitted paths as `{user: optional, app: optional}`,
        method-qualified where a sibling method must not opt in
        (`GET /openapi/v1/bots/{bot_id}` vs `PUT` / `DELETE`).
  - [ ] A comment block explains, in this file's established voice: why both are
        optional (the table cannot say "at least one of"); that declaring `app`
        is what makes it reach the principal at all; that "neither present" now
        dies at the backend's `require_principal` rather than at the edge, and
        that this is a **relocation** of the refusal, not a removal.
  - [ ] `RouteSecurity.resolve` tests: each admitted path resolves to the new
        requirement; each non-admitted sibling — including `PUT`/`DELETE` on
        `/bots/{bot_id}` and every other `/openapi/v1/bots/**` path — still
        resolves to `user: required`.
  - [ ] Run the gateway tests explicitly; the gateway module has no standalone
        lint step in the pre-push hook.
- **Depends on:** Task 6

---

## Task 10: Documentation and the published description  `[ ]`

- **Goal:** The surface's own docs say who may call the eleven, and the carried
  gaps from #937 are settled or restated accurately.
- **Files:**
  - `src/agentclaw/community/core/bot_app_grant/README.md`
  - `src/agentclaw/community/adapters/http/openapi_v1/__init__.py`
  - `docs/openapi-v1/` (published description)
- **Done when:**
  - [ ] The grant README's opening claim — "nothing here admits such a caller
        today" — is replaced with what now does, and its Context Boundary lists
        the new members.
  - [ ] `openapi_v1/__init__.py`'s surface commentary gains a short section on
        the two admission dependencies and points at `app_only_routes.py`.
  - [ ] The published description is regenerated and the diff inspected: only the
        eleven operations' `user_id` description may change.
  - [ ] The `owner_id`-width and coverage-manifest findings carried from #937 are
        restated in `spec.md` Out of Scope with their current standing (they are
        already there — confirm the wording still matches reality after this
        change).
- **Depends on:** Tasks 6, 9

---

## Task 11: Spec acceptance verification  `[ ]`

- **Goal:** Walk `spec.md`'s acceptance criteria against the built system and
  tick them, or reopen what does not hold.
- **Files:** `specs/2026-08-10-openapi-v1-app-only-caller/spec.md`
- **Done when:**
  - [ ] Every checkbox in `spec.md` is ticked, or explicitly struck with a reason
        recorded in the file.
  - [ ] Both Open Questions are closed in the file — including the restart
        question, which the user may want struck.
  - [ ] `scripts/ci/python_sast_local.sh` (or the backend SAST entrypoint) passes
        for the changed modules; the gateway tests from Task 9 are run explicitly.
  - [ ] The negative promise is verified deliberately: `git diff` shows no edited
        expectation in a pre-existing test, and `require_user_id` /
        `USER_ID_DESCRIPTION` are unchanged.
- **Depends on:** Tasks 7, 8, 9, 10

---

## Groups

- **Group A — Clear the blocker:** Task 1
  - Theme: Deletion means deletion. Lands green and useful on its own — it fixes
    a real gap whether or not the rest of this feature ships.
- **Group B — Make the caller expressible:** Tasks 2, 3
  - Theme: The guard moves up a layer and the admitted set is declared. No route
    behavior changes yet; the suite must be green with the surface unchanged.
- **Group C — Resolve the owner:** Tasks 4, 5
  - Theme: `(app_id, bot_id) → owner_id`, and the one seam the handlers take it
    from. Still no route uses it.
- **Group D — Turn it on:** Tasks 6, 7
  - Theme: The eleven operations opt in, and the fail-closed property becomes a
    test. After this group the feature is end-to-end against a locally-signed
    principal.
- **Group E — Prove and publish:** Tasks 8, 9, 10
  - Theme: Behavioral pins, the edge rule, the docs.
- **Group F — Verification:** Task 11
  - Theme: Acceptance walk, including the negative promise that nothing moved for
    a caller who names an end user.
