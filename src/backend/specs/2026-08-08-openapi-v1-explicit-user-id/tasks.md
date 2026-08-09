# Tasks: Public API — Name the End User in the Request

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Add the seam and its refusal  [x]

- **Goal:** One place that answers "which end user is this request for, and may
  this caller act for them?", plus the 403 it raises.
- **Files:**
  `src/backend/src/agentclaw/community/adapters/http/openapi_v1/principal.py`,
  `…/openapi_v1/errors.py`,
  `…/openapi_v1/responses.py`,
  `src/backend/src/agentclaw/community/adapters/http/app.py`
- **Done when:**
  - [x] `require_user_id` reads a required `user_id` query parameter, returns it
        when it equals the caller's id, raises `MissingPrincipalError` when there
        is no verified caller, and raises `UserIdMismatchError` otherwise.
  - [x] `UserIdDep` is exported for routers to declare.
  - [x] Both ids are on a `logger.warning` line at the point of refusal; neither
        appears in any response.
  - [x] `UserIdMismatchError` maps to `(403, "Forbidden")` in `ENVELOPE_ERRORS`,
        and `app.py` registers a concrete-type handler for it beside
        `_principal_error_handler` — with the reason in the docstring.
  - [x] `principal.py`'s module docstring states the placement rule, why the
        query string and not a body or a path, and that this function is what
        delegation changes.
- **Depends on:** —

## Task 2: Document the 403 on exactly the operations that can return it  [x]

- **Goal:** The published description matches what each operation actually does.
- **Files:** `…/openapi_v1/contracts.py`, `…/openapi_v1/__init__.py`
- **Done when:**
  - [x] `USER_SCOPED_ERROR_RESPONSES = {**ERROR_RESPONSES, 403: …}` and a
        `USER_SCOPED_403` single-entry dict for per-route merging.
  - [x] `ENGINE_RUNTIME_ERROR_RESPONSES` is built from the user-scoped set.
  - [x] `403` is **not** in `ERROR_RESPONSES`; the comment says why
        (`test_openapi_error_schema.py:55` requires every operation to document
        every status in that dict, and Bot Logs cannot return one).
  - [x] `build_public_router` attaches the user-scoped table to the nine groups
        with no exemption, and leaves `bots` / `mcp` / `bot_logs` on
        `ERROR_RESPONSES`.
  - [x] The `__init__.py` module docstring carries the placement rule next to the
        addressing rule, phrased so a new endpoint's author can apply it without
        reading this spec — including the four exemptions and why they are
        exempt.
  - [x] `test_openapi_error_schema.py` still passes untouched.
- **Depends on:** Task 1

## Task 3: Convert the 41 operations in the nine unexempted groups  [x]

- **Goal:** Every operation in a group with no exemption takes its user from the
  request.
- **Files:** `…/openapi_v1/{resources,routines,skills,identity}/router.py`,
  `…/openapi_v1/engine_runtime/{sessions,engine,models,approvals,connection}/router.py`
- **Done when:**
  - [x] Handlers take `user_id: UserIdDep` in the position `principal:
        PrincipalDep` occupied, and pass it where `owner_id` / `actor_id` went.
  - [x] The four `resources` handlers that do not use it (`list_resources`,
        `create_resource`, `get_resource`, `update_resource`) declare it and
        `del` it, with a comment saying they are the *not-yet-enforced* case —
        the gap `specs/2026-08-02-public-api-user-only-principal/` records — not
        the no-user-dimension case.
  - [x] No router in this set imports `caller_owner_id`.
  - [x] Every module docstring or comment describing identity as coming from the
        principal is corrected.
  - [x] `ruff check --select E,F,W` is clean on the package: no dead import left
        behind, no new `E501`.
- **Depends on:** Task 2

## Task 4: Convert `bots` and `mcp`, and leave the four exemptions alone

- **Goal:** The two groups that mix scoped and unscoped operations, done
  deliberately rather than in bulk.
- **Files:** `…/openapi_v1/bots/router.py`, `…/openapi_v1/mcp/router.py`
- **Done when:**
  - [ ] 12 of 13 `bots` handlers and 3 of 6 `mcp` handlers take `user_id:
        UserIdDep` and carry `responses=USER_SCOPED_403` on their route
        decorator, merged with any `responses=` already there.
  - [ ] `check_bot_name`, `list_mcp_servers`, `list_mcp_tenants` and
        `get_mcp_server` are **unchanged on the wire**: no `user_id` parameter,
        no 403 documented. Their `caller_owner_id(principal)` assertion is
        replaced by nothing — `_PUBLIC_AUTH` already requires the caller — and a
        comment records that they have no user dimension, with the reason.
  - [ ] `caller_owner_id` is imported by `principal.py` alone; the routers no
        longer reference it.
  - [ ] The generated document shows exactly four `/openapi/v1/bots/**`
        operations without `user_id`, and they are those four.
- **Depends on:** Task 2

## Task 5: Bring the existing suite to the new contract

- **Goal:** The 499 existing openapi_v1 tests exercise the new request shape.
- **Files:** `src/backend/tests/community/adapters/http/openapi_v1/**`
- **Done when:**
  - [ ] Confirm `httpx` merges a client's default `params` into every request; if
        it does, set `client.params = {"user_id": …}` once per `TestClient`, and
        if not, add one helper that does the same job in one place.
  - [ ] Tests for the four exempt operations do **not** send the parameter, and
        one asserts that sending it is still accepted as an unknown query
        parameter rather than becoming a silent scope.
  - [ ] The four `principal=None` cases in `resources/test_resources_handlers.py`
        exercise the seam rather than four handlers, and still pin "no silent
        bot-derived owner".
  - [ ] Direct-invocation tests (`identity`, `routines`, `resources`) pass the
        resolved id, and their docstrings no longer describe a principal
        argument.
  - [ ] Pre-handler failures are answered by importing `app.py`'s real handlers,
        not by re-implementing them in a fixture.
  - [ ] `test_sessions.py:507` and `test_approvals.py:94` are **unchanged and
        green** — a caller-supplied `user_id` in the body is still a 422, because
        the body still forbids it. Confirm this rather than assuming it.
  - [ ] `pytest tests/community/adapters/http/openapi_v1/` is green.
- **Depends on:** Tasks 3, 4

## Task 6: Pin the contract with a convention test

- **Goal:** A later operation that breaks the rule fails the build, not review.
- **Files:**
  `src/backend/tests/community/adapters/http/openapi_v1/test_explicit_user_id.py`
  (new)
- **Note:** the *behavioural* half of this file was pulled forward into Group A
  (commit "cover the user-id seam"), because the changed-line coverage gate fails
  a group that adds code whose tests land two groups later. What remains here is
  the document-level half — the assertions that need the operations to actually
  carry the parameter.
- **Done when:** asserted against the generated document, in the shape of
  `test_path_convention.py`:
  - [ ] All 56 user-scoped operations require `user_id`, and it is `in: query` on
        every one.
  - [ ] The exempt set is *exactly* the four documented addresses — a fifth fails
        here.
  - [ ] `user_id` appears in no request body schema and in no path template.
  - [ ] `bot_id`'s placement is unchanged from `HEAD` on all 65 operations.
  - [ ] Bot Logs gains nothing: no parameter, no 403.
  - [ ] 403 is documented on exactly the 56, and `403 not in ERROR_RESPONSES`.
  - [x] Behaviour: another user's id is 403; two rejected ids give
        byte-identical bodies; a missing parameter is 422; no verified caller is
        401 **even when the parameter is also missing**. _(landed in Group A)_
- **Depends on:** Task 5

## Task 7: Write the rule where the next author will find it

- **Goal:** The convention is documented, not just enforced.
- **Files:** `src/backend/docs/openapi-v1/README.md` (and the `.zh-CN`
  counterpart if it mirrors this section)
- **Done when:**
  - [ ] The placement rule, the four exemptions with a reason each, and the 403
        are described in the handoff doc.
  - [ ] The rejected alternatives are recorded in one short paragraph — body
        field, path segment, header — so the question is not reopened from
        scratch.
  - [ ] The changelog and status board record this change, per the doc's own
        "if your change moved a checkbox, move it here too" rule.
  - [ ] The doc says plainly what did **not** change: who may call, and what a
        request with no verified user principal answers.
- **Depends on:** Task 6

## Task 8: Tests & Verification

- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** —
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off, including the
        negative ones (internal `/api` untouched; Bot Logs untouched; `bot_id`
        untouched).
  - [ ] `pytest tests/community` is green — not just the openapi_v1 subtree.
  - [ ] `scripts/ci/python_sast_local.sh src/backend` passes.
  - [ ] Changed-line coverage meets the backend gate.
  - [ ] The generated document is diffed against `HEAD` and every change is one
        this spec asked for: 56 added query parameters, 56 added 403s, and
        nothing else — no schema, path, or `bot_id` drift.
  - [ ] Anything that could not be run locally is named explicitly, with why.
- **Depends on:** Task 7

---

## Groups

- **Group A — The seam:** Tasks 1, 2
  - Theme: one place to ask "which user, and may you?", the 403 it raises, and
    where that 403 is documented. Nothing calls it yet, so the surface behaves
    exactly as before this group.
- **Group B — The 56 operations:** Tasks 3, 4
  - Theme: every user-scoped handler takes its user from the request. Split by
    group shape — the nine clean groups in one diff, the two mixed ones in
    another, so the four exemptions get their own review rather than being
    buried in a bulk change.
- **Group C — Tests:** Tasks 5, 6
  - Theme: the existing suite moved to the new contract, and a convention test
    that makes the rule survive the next endpoint.
- **Group D — Docs and verification:** Tasks 7, 8
  - Theme: write the rule down where it will be read, then check the whole thing
    against the spec.
