# Tasks: Admit the App-Principal-Only Caller Against an Owner's Grant

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Branch: `claude/sdd-implementation-0m0aaj`, based on `dev`.
>
> **Revised after review.** The first draft dropped `user_id` on app-only calls
> and derived the owner from `(app_id, bot_id)`; and it admitted eleven
> bot-scoped operations and nothing else. Both were wrong. `user_id` stays a
> required parameter everywhere and is *authorized against the grant* instead of
> compared to the caller — which is exactly what `require_user_id`'s docstring
> said this change would be. And owner-scoped listings are admitted with their
> results narrowed to granted bots, rather than refused.
>
> **The negative promise is the load-bearing one.** A caller naming an end user
> must come out of this bit-for-bit unchanged on all 63 operations, and no
> request or response schema may change at all. An existing test expectation
> edited to accommodate this work is a scope escape, not a fix.

---

## Task 1: Revoke grants when a bot is deleted  `[ ]`

> The blocker, and it ships first. It matters more than the issue framed it: with
> `DELETE /bots/{bot_id}` admitted for an application, an app can delete a bot it
> was granted, and its own authorization must go with it.

- **Goal:** Deleting a bot withdraws every authorization standing against it, as
  part of the deletion, recorded in history.
- **Files:**
  - `core/repository/protocols/bot/app_grant.py`
  - `core/repository/implementations/bot/app_grant.py`
  - `core/bot_app_grant/services/grant_service.py`, `api/bot_app_grant_service.py`
  - `core/bot_management/services/bot_service.py`, `di/modules/`
- **Done when:**
  - [ ] `revoke_all_for_bot(bot_id, owner_id) -> int` added as an
        `@abstractmethod`, with a docstring saying the count is for the caller's
        log and nothing branches on it.
  - [ ] The implementation deletes every live row and appends one `revoked` log
        event per deleted row in **one** `transactional_orm_session()`. Log rows
        are built from the live rows, not from arguments.
  - [ ] `BotAppGrantService.revoke_all_for_bot(*, bot_id, owner_id) -> int`, with
        a docstring distinguishing it from `revoke`: no `GrantNotFoundError`,
        because "the bot had no authorizations" is a normal outcome of deletion.
  - [ ] `BotService.delete_bot` calls it **before** `soft_delete_by_owner`, via a
        provider callable following `_device_service_provider`, with a comment
        carrying the ordering argument.
  - [ ] Failures propagate; nothing is caught and logged-as-success.
  - [ ] Tests: grant → delete leaves no live row and one `revoked` row per grant;
        delete with no grants succeeds; a repository failure aborts the deletion
        and leaves the bot undeleted.
  - [ ] `core/bot_app_grant/README.md`'s "Known gap, carried deliberately" note is
        **deleted**, not amended.
- **Depends on:** —

---

## Task 2: Move the admission guard out of the verifier  `[ ]`

> The highest-risk edit in this workstream. The guard **moves up one layer; it is
> not deleted.**

- **Goal:** Make "an application with no end user" verifiable while every
  existing route keeps refusing it.
- **Files:** `core/gateway_principal/verifier.py`, its `README.md`,
  `adapters/http/openapi_v1/dependencies.py`
- **Done when:**
  - [ ] `_require_user_principal` → `_require_admissible_principal`: refuses a set
        naming neither a `user` nor an `app`; keeps the blank-subject-id check.
  - [ ] Its docstring is rewritten to say **where the guard went** and why that
        placement still holds for routes nobody has written. A reader arriving
        from #950 must not be sent looking for a rule that moved.
  - [ ] `VerifiedCaller.has_user` and `VerifiedCaller.app_id -> int | None` added,
        `None` documented as the real state "names no application".
  - [ ] `VerifiedCaller.user_id`'s docstring notes `""` is now reachable and that
        `caller_owner_id` turning it into `401` is the wanted answer.
  - [ ] `require_principal` gains the end-user requirement, funnelling into the
        same `MissingPrincipalError` / `1008`.
  - [ ] `require_operating_caller` added, documented as the opt-in.
  - [ ] `resolve_avernet_tenant`'s docstring extended for the app-only case.
  - [ ] Verifier tests: app-only, user+app, user-only, access-key-only, bot-only,
        blank subject id, `app_id` accessor.
  - [ ] **No route behavior changes in this task.** The full existing suite passes
        untouched.
- **Depends on:** —

---

## Task 3: The admission table and the acting caller  `[ ]`

- **Goal:** One reviewable statement of which operation is in which mode, and one
  object carrying what an admitted handler needs.
- **Files:** `adapters/http/openapi_v1/admission.py` (new)
- **Done when:**
  - [ ] `AdmissionMode` and `ADMISSION: dict[tuple[str, str], AdmissionMode]`
        cover **every** public operation — all 63 — with none omitted.
  - [ ] Entries are grouped and commented by mode, and the module docstring states
        the rule each mode encodes and why the mode follows from the operation's
        *shape* rather than from taste.
  - [ ] The Mode D entries carry their individual reasons: creation has no bot to
        cover and spends the owner's quota; the authorization operations are the
        consent moment; bot-logs' `user_id` means "whose traces" not "whose call";
        MCP config is account-level configuration a grant does not speak to.
  - [ ] `ActingCaller` with `owner_id`, `app_id: int | None`, `require_bot()` and
        `granted_bot_ids()`. A comment justifies the optional against AGENTS.md:
        `None` means "a human caller", every consumer branches on it explicitly.
  - [ ] `require_bot` raises `GrantNotResolvableError`; `granted_bot_ids` returns
        `None` for a human caller, meaning "no filtering", distinct from an empty
        set meaning "granted nothing".
- **Depends on:** —

---

## Task 4: Authorize `user_id` against the grant  `[ ]`

- **Goal:** `user_id` keeps its place and its schema; what it is checked against
  becomes the grant when there is no user on the wire.
- **Files:** `adapters/http/openapi_v1/principal.py`, `errors.py`, `responses.py`,
  `adapters/http/app.py`
- **Done when:**
  - [ ] `require_user_id` keeps its signature and its **required** `user_id`, and
        gains the app-only branch. Its docstring's promise — "stops comparing the
        two ids and asks whether the delegation was granted" — is rewritten as
        delivered, with the grant named as what stands in for consent.
  - [ ] The user-bearing path is unchanged: `422` absent, `403` mismatched, id
        returned on match.
  - [ ] `GrantCheckedOwnerDep` reads `bot_id` from `path_params` then
        `query_params` (path first, with the reason), refuses when neither
        carries one, and calls `require_bot`.
  - [ ] `ActingCallerDep` returns the `ActingCaller` for the modes whose handlers
        do their own narrowing or checking.
  - [ ] `GrantNotResolvableError` added, mapped to `(404, "Not found")`
        **byte-identical** to `BotNotFoundError`, with an `app.py` handler
        alongside `UserIdMismatchError` — a dependency-raised error never reaches
        `@envelope_errors`.
  - [ ] A comment records why the missing grant is `404` and not `403`: on this
        surface `403` means "authenticated, not yours", which confirms the bot
        exists.
  - [ ] The grant probe runs once per request; `_for_log` bounding applies to the
        app-only refusals too.
- **Depends on:** Tasks 2, 3

---

## Task 5: Mode A — the grant-checked operations  `[ ]`

- **Goal:** ~50 operations admit the app-only caller behind a grant check, with
  no handler body changes.
- **Files:** the `bots`, `sessions`, `approvals`, `engine`, `models`,
  `connection`, `identity`, `resources`, `routines`, `skills` routers
- **Done when:**
  - [ ] Every Mode A operation whose `bot_id` is on the wire takes
        `GrantCheckedOwnerDep` in place of `UserIdDep`.
  - [ ] **No handler body changes** in this task. If a body needs editing, stop —
        it means the check is in the wrong place.
  - [ ] Each edited router carries a header comment naming its modes and pointing
        at `admission.py`.
  - [ ] The Mode D routes in `bots` and `mcp` are visibly untouched.
- **Depends on:** Task 4

---

## Task 6: Mode A where the bot is not on the wire  `[ ]`

> The five operations that would otherwise pass unchecked. `create_routine` takes
> its `bot_id` from the request body; the four `skill_id` routes name a skill that
> "selects its Bot" and never put the bot on the wire.

- **Goal:** Check the grant for operations whose bot is knowable but not a
  parameter — or refuse them, never admit them unchecked.
- **Files:** `routines/router.py`, `skills/router.py`, the skill query service
- **Done when:**
  - [ ] `POST /openapi/v1/bots/routines` calls `caller.require_bot(body.bot_id)`
        immediately after parsing, before any service call.
  - [ ] `GET`/`DELETE /skills/{skill_id}` and
        `POST /skills/{skill_id}/{activate,deactivate}` resolve the skill's
        `bot_id` and check the grant **before** acting.
  - [ ] The resolution reads through the existing owner-scoped skill query path,
        so a skill belonging to another owner is already refused before the grant
        is consulted.
  - [ ] If clean pre-handler resolution proves impossible, these four are moved to
        Mode D and the spec's Decision 2 is amended to say so. **Admitting them
        unchecked is not an option** — the underlying service scopes by owner
        only, so an application would reach a skill on a bot it was never granted.
  - [ ] Tests: each of the five refuses when the resolved bot is not granted.
- **Depends on:** Task 4

---

## Task 7: Mode B — grant-filtered listings  `[ ]`

- **Goal:** An application asking for the owner's bots gets exactly the granted
  ones.
- **Files:** `bots/router.py`, `authorized_apps/router.py`
- **Done when:**
  - [ ] `GET /openapi/v1/bots` narrows to `caller.granted_bot_ids()` when set, and
        the owner's own call is unfiltered.
  - [ ] Filtering happens **before** pagination, so the page count describes the
        filtered result. A comment states why: filtering a page after the fact
        leaks the size of what was withheld and returns short pages.
  - [ ] `GET /openapi/v1/bots/authorized` accepts the app-only caller and scopes
        by the principal's `app_id`, making it the discovery endpoint an
        integration can actually reach.
  - [ ] No grants → empty result and `200`, not an error.
- **Depends on:** Task 4

---

## Task 8: Modes C and C-open  `[ ]`

- **Goal:** The five operations with no bot dimension get the right gate.
- **Files:** `bots/router.py`, `mcp/router.py`
- **Done when:**
  - [ ] `GET /bots/ceiling` admits an app-only caller holding ≥1 live grant from
        the named owner, and `404`s otherwise.
  - [ ] `GET /bots/check-name` and the three MCP catalogue reads admit the
        app-only caller on authentication alone, with a comment recording why
        that is not a new exposure: there is no owner on the wire to gate on, and
        every authenticated caller in the tenant already gets the same answer.
  - [ ] The three MCP **configuration** operations remain refused, with the reason
        in the router.
- **Depends on:** Task 4

---

## Task 9: The fail-closed inventory test  `[ ]`

> The test the issue asks for by name: adding a route must not silently inherit
> the relaxation.

- **Goal:** Make the whole policy a property the suite enforces.
- **Files:** `tests/community/adapters/http/openapi_v1/test_principal_seam.py`
- **Done when:**
  - [ ] Every route on the built public app appears in `ADMISSION` exactly once —
        a route present on the surface and absent from the table fails, and vice
        versa.
  - [ ] Each route's declared dependency matches its mode: Mode D routes depend on
        `require_principal`; admitted routes depend on `require_operating_caller`,
        directly or transitively; no route depends on both.
  - [ ] Every Mode A route either takes `GrantCheckedOwnerDep` or is one of the
        five in Task 6, named explicitly in the test so the exception list cannot
        grow silently.
  - [ ] The failure message names the offending routes and says what to do.
  - [ ] `test_public_routes_require_principal` is strengthened, not replaced.
- **Depends on:** Tasks 5, 6, 7, 8

---

## Task 10: Behavioral tests  `[ ]`

- **Goal:** Pin every acceptance criterion in `spec.md`.
- **Files:** a new app-only module under
  `tests/community/adapters/http/openapi_v1/`, plus existing router suites
- **Done when:**
  - [ ] Mode A: grant present → response identical to the owner's own call; no
        grant → `404` compared **byte-for-byte** against the nonexistent-bot
        response; grant for another bot → `404`; grant held by another application
        → `404`; deleted bot → refused.
  - [ ] Mode B: two bots, one granted → one returned and the count says one; the
        owner's call still returns both; no grants → empty `200`.
  - [ ] Mode C: `ceiling` admitted with a grant, `404` without; C-open admitted
        with no grant at all.
  - [ ] Mode D: `401` on **all fourteen**, enumerated from `ADMISSION` rather than
        sampled, so the list cannot rot.
  - [ ] Access-key-only and bot-only callers → `401`, including on admitted routes.
  - [ ] The existing user-caller suites pass **with no expectation edited**.
- **Depends on:** Tasks 5, 6, 7, 8

---

## Task 11: The gateway route-security rules  `[ ]`

- **Goal:** Let the App identity reach the backend everywhere it is admitted, and
  nowhere it is not.
- **Files:** `src/gateway/configs/application.yaml`,
  `src/gateway/tests/unit/core/authn/`
- **Done when:**
  - [ ] `/openapi/v1/bots/**` becomes `{user: optional, app: optional}`, with the
        six Mode D overrides restoring `user: required` (and `app: required` where
        it is required today).
  - [ ] A comment explains, in this file's voice: why the **refusals** are
        enumerated rather than the admissions (six rules a reviewer can hold
        against fifty-five they cannot); why both identities are optional (the
        table cannot say "at least one of"); and that "neither present" now dies
        at the backend's `require_principal` — a **relocation** of the refusal,
        not a removal.
  - [ ] `RouteSecurity.resolve` tests assert `user: required` for exactly the Mode
        D paths and the optional pair for the rest, derived from the same table so
        the two expressions of the policy cannot drift.
  - [ ] Gateway tests run explicitly; the module has no standalone lint step.
- **Depends on:** Tasks 5, 6, 7, 8

---

## Task 12: Documentation and the published description  `[ ]`

- **Files:** `core/bot_app_grant/README.md`,
  `adapters/http/openapi_v1/__init__.py`, `docs/openapi-v1/`
- **Done when:**
  - [ ] The grant README's "nothing here admits such a caller today" is replaced
        with what now does, and its Context Boundary lists the new members.
  - [ ] `openapi_v1/__init__.py` gains a section on the admission modes, pointing
        at `admission.py`. Its existing note on why bot-logs sits outside the
        caller-scope rule is extended — the same asymmetry is why bot-logs is
        Mode D here.
  - [ ] The `user_id` description on admitted operations says what the parameter
        means for an application caller. `USER_ID_DESCRIPTION` stays a single
        shared constant; only its text may change, and identically everywhere.
  - [ ] The published description is regenerated and the diff inspected: **no
        schema change may appear**, only description text.
- **Depends on:** Tasks 5, 6, 7, 8, 11

---

## Task 13: Spec acceptance verification  `[ ]`

- **Files:** `specs/2026-08-10-openapi-v1-app-only-caller/spec.md`
- **Done when:**
  - [ ] Every checkbox in `spec.md` is ticked or explicitly struck with a reason
        recorded in the file.
  - [ ] Both Open Questions are closed in the file.
  - [ ] Backend SAST/lint passes for the changed modules; the gateway tests from
        Task 11 are run explicitly.
  - [ ] The negative promise is verified deliberately: `git diff` shows no edited
        expectation in a pre-existing test, and no request/response schema change
        in the regenerated description.
- **Depends on:** Tasks 9, 10, 11, 12

---

## Groups

- **Group A — Clear the blocker:** Task 1
  - Theme: Deletion means deletion. Lands green and useful on its own.
- **Group B — Make the caller expressible:** Tasks 2, 3
  - Theme: The guard moves up a layer and the policy is written down. No route
    behavior changes yet; the suite must be green with the surface unchanged.
- **Group C — The seam:** Task 4
  - Theme: `user_id` authorized against the grant. Still no route uses it.
- **Group D — Turn it on:** Tasks 5, 6, 7, 8
  - Theme: The four modes applied, in order of how much each handler changes —
    none, then a check, then a filter, then a gate.
- **Group E — Prove and publish:** Tasks 9, 10, 11, 12
  - Theme: The fail-closed test, the behavioral pins, the edge rules, the docs.
- **Group F — Verification:** Task 13
  - Theme: Acceptance walk, including the negative promise that nothing moved for
    a caller who names an end user.
