# Tasks: Public API — Name the End User in the Request

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Add the seam and its refusal

- **Goal:** One place that answers "which end user is this request for, and may
  this caller act for them?", plus the 403 it raises.
- **Files:**
  `src/backend/src/agentclaw/community/adapters/http/openapi_v1/principal.py`,
  `…/openapi_v1/errors.py`,
  `…/openapi_v1/responses.py`,
  `src/backend/src/agentclaw/community/adapters/http/app.py`
- **Done when:**
  - [ ] `resolve_request_user(principal, claimed)` returns the id when it equals
        the caller's, raises `MissingPrincipalError` when there is no verified
        caller, and raises `UserIdMismatchError` otherwise.
  - [ ] Both ids are on a `logger.warning` line at the point of refusal; neither
        appears in any response.
  - [ ] `require_user_id` / `UserIdDep` read `user_id` from the query string and
        delegate the decision to `resolve_request_user`.
  - [ ] `UserIdMismatchError` maps to `(403, "Forbidden")` in `ENVELOPE_ERRORS`,
        and `app.py` registers a concrete-type handler for it beside
        `_principal_error_handler` — with the reason in the docstring.
  - [ ] `principal.py`'s module docstring states the placement rule and says
        this function is what delegation changes.
- **Depends on:** —

## Task 2: Document the 403 without claiming it surface-wide

- **Goal:** Only the operations that can return a 403 advertise one.
- **Files:** `…/openapi_v1/contracts.py`
- **Done when:**
  - [ ] `USER_SCOPED_ERROR_RESPONSES = {**ERROR_RESPONSES, 403: …}` exists.
  - [ ] `ENGINE_RUNTIME_ERROR_RESPONSES` is built from it, not from
        `ERROR_RESPONSES`.
  - [ ] `403` is **not** in `ERROR_RESPONSES`; the comment says why
        (`test_openapi_error_schema.py:55` requires every operation to document
        every status in that dict, and Bot Logs cannot return one).
  - [ ] `tests/…/openapi_v1/test_openapi_error_schema.py` still passes untouched.
- **Depends on:** Task 1

## Task 3: Wire the dependency per group, and settle the double-declaration

- **Goal:** Every user-scoped route requires the parameter by assembly, not by
  each handler remembering; Bot Logs is left alone.
- **Files:** `…/openapi_v1/__init__.py`
- **Done when:**
  - [ ] `_USER_SCOPED = [*_PUBLIC_AUTH, Depends(require_user_id)]`, with the
        comment recording why `require_principal` stays declared independently
        (FastAPI skips a dependency whose own parameters failed to validate, so
        folding it in makes a malformed request answer 422 instead of 401).
  - [ ] Every group except `bot_logs` is mounted with `_USER_SCOPED` and
        `USER_SCOPED_ERROR_RESPONSES`; `bot_logs` keeps `_PUBLIC_AUTH` and
        `ERROR_RESPONSES`.
  - [ ] **Resolved against the generated document:** the 11 body operations do
        not end up advertising the query parameter *as well as* the body field.
        Pick one — exclude them from the router-level dependency, or accept the
        duplicate — implement it, and write the reason in the comment.
  - [ ] The module docstring carries the placement rule next to the addressing
        rule, phrased so a new endpoint's author can apply it without reading
        this spec.
- **Depends on:** Tasks 1, 2

## Task 4: Convert the 49 query-parameter operations

- **Goal:** Every operation that takes its user id from the query string reads it
  from `UserIdDep` instead of from the principal.
- **Files:** `…/openapi_v1/{bots,mcp,resources,routines,skills,identity}/router.py`,
  `…/openapi_v1/engine_runtime/{sessions,engine,models,approvals,connection}/router.py`
- **Done when:**
  - [ ] No router imports `caller_owner_id`; `principal.py` is its only caller.
  - [ ] Handlers that used the value take `UserIdDep` in the position
        `principal: PrincipalDep` occupied, and pass it where `owner_id` /
        `actor_id` went.
  - [ ] The 8 that do not use it (`check_bot_name`; `list_mcp_servers`,
        `list_mcp_tenants`, `get_mcp_server`; `list_resources`,
        `create_resource`, `get_resource`, `update_resource`) declare it and
        `del` it with a one-line reason — the shape `bot_logs/router.py:44` uses.
  - [ ] Every module docstring or comment that describes identity as coming from
        the principal is corrected (`bots`, `mcp`, `resources`, `routines`,
        `identity`, `dependencies.py`).
  - [ ] `ruff check --select E,F,W` is clean on the package, with no dead import
        left behind and no new `E501`.
- **Depends on:** Task 3

## Task 5: Convert the 11 body operations, and move `bot_id` with them

- **Goal:** Requests whose body this API defines name their user — and their bot,
  where it was a query parameter — in that body.
- **Files:** `…/openapi_v1/{bots,resources,routines,identity,mcp}/schemas.py`,
  `…/engine_runtime/{sessions,approvals}/schemas.py`, and the 11 handlers in the
  matching routers
- **Done when:**
  - [ ] All 11 models declare a required `user_id: str` (min length 1), with the
        same description text as the query parameter.
  - [ ] `ResourceCreate`, `ResourceUpdate` and `RoutineUpdate` declare a required
        `bot_id`, and their handlers drop the `bot_id: str = Query(...)`
        parameter. `RoutineCreate` is left as it is — it already complies.
  - [ ] Each of the 11 handlers resolves via
        `resolve_request_user(principal, body.user_id)` and keeps
        `principal: PrincipalDep`.
  - [ ] `SessionCreate`'s comment — "No user_id / engine fields: the caller is
        the authenticated principal" — is rewritten; `engine` stays rejected by
        `extra="forbid"`.
  - [ ] Nothing forwards `body.user_id` downstream un-adjudicated: the relay
        bodies in `sessions`/`approvals` still send the resolved value.
- **Depends on:** Task 3

## Task 6: Bring the existing suite to the new contract

- **Goal:** The 499 existing openapi_v1 tests exercise the new request shape, and
  the two that assert the old contract are inverted deliberately rather than
  deleted.
- **Files:** `src/backend/tests/community/adapters/http/openapi_v1/**`
- **Done when:**
  - [ ] Query-carried operations are covered by setting the default parameter
        once per client (`client.params = {"user_id": …}`), after confirming
        httpx merges it — if it does not, a helper does the same job in one
        place rather than at 232 call sites.
  - [ ] Body-carried tests send `user_id` (and `bot_id` where it moved).
  - [ ] `test_sessions.py:507` and `test_approvals.py:94` assert 403 for another
        user's id and still assert 422 for `engine` / unknown fields, with a
        comment explaining the inversion.
  - [ ] The four `principal=None` cases in `resources/test_resources_handlers.py`
        exercise the seam rather than four handlers, and still pin "no silent
        bot-derived owner".
  - [ ] Direct-invocation tests (`identity`, `routines`, `resources`) pass the
        resolved id, and their docstrings no longer describe a principal
        argument.
  - [ ] Pre-handler failures are answered by importing `app.py`'s real handlers,
        not by re-implementing them in a fixture.
  - [ ] `pytest tests/community/adapters/http/openapi_v1/` is green.
- **Depends on:** Tasks 4, 5

## Task 7: Pin the contract with a convention test

- **Goal:** A later operation that breaks the rule fails the build, not review.
- **Files:**
  `src/backend/tests/community/adapters/http/openapi_v1/test_explicit_user_id.py`
  (new)
- **Done when:** asserted against the generated document, in the shape of
  `test_path_convention.py`:
  - [ ] All 60 non-Bot-Logs operations require a `user_id`; none of the 5 Bot
        Logs ones gains one.
  - [ ] Placement follows the rule: `$ref`-bodied operations carry it in the
        body, every other operation in the query string — and never both.
  - [ ] `bot_id` sits wherever `user_id` sits on the same operation, when it is a
        parameter at all; path `{bot_id}` is exempt.
  - [ ] 403 is documented on exactly the 60, and `403 not in ERROR_RESPONSES`.
  - [ ] Behaviour: another user's id is 403 on **both** the query and the body
        path; two rejected ids give byte-identical bodies; a missing parameter is
        422; no verified caller is 401 **even when the parameter is also
        missing**.
- **Depends on:** Task 6

## Task 8: Write the rule where the next author will find it

- **Goal:** The convention is documented, not just enforced.
- **Files:** `src/backend/docs/openapi-v1/README.md` (and the `.zh-CN` counterpart
  if it mirrors this section)
- **Done when:**
  - [ ] The placement rule, the three body-less exceptions, and the 403 are
        described in the handoff doc.
  - [ ] The changelog and status board record this change, per the doc's own
        "if your change moved a checkbox, move it here too" rule.
  - [ ] The doc says plainly what did **not** change: who may call, and what a
        request with no verified user principal answers.
- **Depends on:** Task 7

## Task 9: Tests & Verification

- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** —
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off, including the
        negative ones (internal `/api` untouched; Bot Logs untouched).
  - [ ] `pytest tests/community` is green — not just the openapi_v1 subtree.
  - [ ] `scripts/ci/python_sast_local.sh src/backend` passes.
  - [ ] Changed-line coverage meets the backend gate.
  - [ ] The generated document is diffed against `HEAD` and every change is one
        this spec asked for — no accidental schema or path drift.
  - [ ] Anything that could not be run locally is named explicitly, with why.
- **Depends on:** Task 8

---

## Groups

- **Group A — The seam:** Tasks 1, 2, 3
  - Theme: one place to ask "which user, and may you?", the 403 it raises, and
    the assembly that puts it on every user-scoped route. Nothing else moves, so
    the surface still behaves exactly as before this group.
- **Group B — The 60 operations:** Tasks 4, 5
  - Theme: every handler takes its user from the request. Mechanical and large,
    but one shape per operation; reviewable as two diffs split by placement.
- **Group C — Tests:** Tasks 6, 7
  - Theme: the existing suite moved to the new contract, and a convention test
    that makes the rule survive the next endpoint.
- **Group D — Docs and verification:** Tasks 8, 9
  - Theme: write the rule down where it will be read, then check the whole thing
    against the spec.
