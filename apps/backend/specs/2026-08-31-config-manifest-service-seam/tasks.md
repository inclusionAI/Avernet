# Tasks: One Seam for the Five Categories Apply Touches

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Groups run in order. Within a group, tasks are independent. The inertness proof
is the same in every task: **the existing endpoint tests pass unmodified.** If
one needs editing, stop and report it — that is a behaviour change, not a fixup.

---

## Group A — Move the checks to their core homes

## [x] Task 1: Resources path policy
- **Goal:** The three workspace-path rules and the coordinate resolution become
  callable without a request.
- **Files:** `src/backend/.../core/services/resource_file_service.py`,
  `.../core/services/resource_addressing.py`,
  `.../adapters/http/openapi_v1/resources/router.py`
- **Done when:**
  - [x] `safe_workspace_path` and `require_workspace_path` live beside
        `is_readonly` and raise `InvalidResourcePathError`, exactly as the
        router's `_safe_path` / `_require_path` do today.
  - [x] `is_write_forbidden(path) -> bool` carries the ancestor walk. It returns
        a verdict and raises nothing.
  - [x] `_reject_read_only` stays in the router as a two-line mapper over it, so
        the 403 body is bit-identical rather than argued to be equivalent.
  - [x] `resource_coords_from_record` takes `BotRepository` as an argument, not
        from the DI container, and returns `BotConfigCoords`.
  - [x] `resource_coords_from_spec` builds the same type from a create request's
        `entity_id` / `entity_type` / `engine_type` and an allocated `bot_id`,
        touching no repository. No caller until W13; pinned by unit test.
  - [x] The three path validators take values only — given a `BotConfigCoords`
        they never reach a repository, so they run identically on both paths.
  - [x] The router's `_safe_path`, `_require_path`, `_file_coords` names are
        bindings to the core functions — no second body anywhere.
  - [x] `test_openapi_resources.py` and `test_resources_handlers.py` pass
        **unedited**.
- **Depends on:** —

## [x] Task 2: Engine-config ownership and addressing
- **Goal:** The ownership guard and the entity resolution become callable
  without a request. This is the only in-scope group whose guard is an explicit
  service call rather than an implicit resolution.
- **Files:** `src/backend/.../core/services/engine_config.py`,
  `.../adapters/http/openapi_v1/bots/engine_config.py`
- **Done when:**
  - [x] `engine_config_coords_from_record(bot_id, owner_id, *, bot_service)` performs the
        `get_bot(bot_id, owner_id)` guard and reads `entity_id`, `entity_type`,
        `active_engine` off the record, raising `BotNotFoundError` on both of
        today's paths — an unresolvable bot, and a bot with no `entity_id`.
  - [x] `engine_config_coords_from_spec` reads the same three values off the
        create request and performs **no** ownership guard — there is no record
        to own in phase 1, and the caller's right to create is what
        `check_create_bot_preflight` already decides (`create_flow.py:494`).
  - [x] Both handlers call the record constructor; neither keeps a local
        `_engine_config_target`.
  - [x] The `# ownership/tenant guard` comment moves with the code it annotates.
  - [x] The engine-config endpoint tests pass **unedited**.
- **Depends on:** —

## [x] Task 3: Identity file type and coordinates
- **Goal:** The `.md` re-suffixing and the staff-entity resolution become
  callable without a request.
- **Files:** `src/backend/.../core/services/identity.py`,
  `.../adapters/http/openapi_v1/identity/router.py`
- **Done when:**
  - [x] `physical_file_name(file_type)` produces the `<type>.md` form
        `IdentityService.validate_file_type` requires, and is the only place
        that suffix is applied.
  - [x] `identity_coords_from_record` returns `BotConfigCoords` with
        `engine_type=None` — identity addresses no engine, and a defaulted engine
        here would be a value it never had.
  - [x] `identity_coords_from_spec` returns the same, from request parameters.
  - [x] `physical_file_name` reaches no repository, so the identity file-type
        rule runs at preflight.
  - [x] All three handlers call them; no handler re-spells `entity_type =
        "staff"`.
  - [x] The identity endpoint tests pass **unedited**.
- **Depends on:** —

## [x] Task 4: Skills addressed-bot binding
- **Goal:** "This skill belongs to the bot the address names" becomes callable
  without a request.
- **Files:** `src/backend/.../core/skill_center/services/skill_query_service.py`,
  `.../adapters/http/openapi_v1/skills/router.py`
- **Done when:**
  - [x] `require_addressed_bot(record, bot_id)` raises `LocalSkillNotFoundError`
        on mismatch, keeping the 404 mask and the enumeration-oracle reasoning
        in its docstring. Its `record` is a *skill* record, not a bot record, so
        it is already record-free in the sense that matters — W13 declares skills
        that do not exist yet, and has nothing to compare.
  - [x] `skill_coords_from_spec` exists alongside the record constructor.
  - [x] `_require_skills_grant`, `_directory_relative_paths` and the
        `application/zip` header check **stay in the router**, each with a
        comment naming which side of Rule 7's line it is on and why.
  - [x] `test_openapi_bot_skills_read.py` passes **unedited** — including the
        test whose docstring names both helpers by their router spelling, which
        stays accurate because the names stay bound there.
- **Depends on:** —

## [x] Task 5: MCP coordinates
- **Goal:** The staff-entity resolution becomes callable without a request.
- **Files:** `src/backend/.../core/mcp/config_flow.py`,
  `.../adapters/http/openapi_v1/mcp/router.py`
- **Done when:**
  - [x] `mcp_coords_from_record` replaces the router's `_ENTITY_TYPE` constant,
        and `mcp_coords_from_spec` builds the same type from request parameters.
  - [x] The catalogue reads, the network-type visibility masking and
        `_REFUSES_APP_ONLY` are untouched — none is a config-category write.
  - [x] The mcp endpoint tests pass **unedited**.
- **Depends on:** —

---

## Group B — Declare the table and prove it holds

## [x] Task 6: The `CONFIG_SURFACE` table
- **Goal:** One declared list of what governs each category, and the coordinate
  type the five rows share.
- **Files:** `src/backend/.../core/bot_config_surface/__init__.py`,
  `.../core/bot_config_surface/README.md`
- **Done when:**
  - [x] `BotConfigCoords` is frozen and carries `(bot_id, owner_id,
        entity_type, entity_id, engine_type)`, with `engine_type: str | None`
        and no default.
  - [x] `CONFIG_SURFACE` has exactly five rows — including `engine_config`,
        which W4 excludes from phase 1 but which must have its plug ready.
  - [x] Each row carries `from_record`, `from_spec` and `validators` as three
        separate fields, so that "which of these needs a bot record" is answerable
        by reading the table rather than by reading five implementations.
  - [x] The module imports nothing from `fastapi` or `adapters`, and defines no
        behaviour of its own — it names functions that live elsewhere.
  - [x] `README.md` carries a Context Boundary block per
        `docs/arch/context-boundary-format.md`, and says in as many words that
        this module is an index and must not grow logic.
- **Depends on:** Tasks 1–5

## [x] Task 7: Prove router and table share one object
- **Goal:** Make the anti-drift guarantee structural rather than claimed.
- **Files:** `src/backend/tests/community/core/bot_config_surface/test_config_surface.py`
- **Done when:**
  - [x] One `is` assertion per moved function, binding the router's name to the
        table's entry. `is`, not `==` — two functions that merely behave alike
        are the failure this test exists to catch.
  - [x] A test asserts `CONFIG_SURFACE` covers exactly the five categories, and
        names any that is missing.
  - [x] Each moved function has a direct unit test that calls it with **no
        request, no app, and no DI container** — the capability the whole
        feature exists to deliver, proven rather than assumed.
  - [x] **The record-free test:** build coordinates through every row's
        `from_spec` and run every row's validators against them, with no bot
        record anywhere in the fixture. This rehearses W13's preflight before
        W13 exists. If it needs a record to pass, the split did not happen and
        W13 will be forced to write a second validation copy.
  - [x] A validator that reaches a repository fails this test. That is the
        point: `from_spec` and `from_record` return the same frozen type
        precisely so a validator cannot tell which path it is on, and this is
        what catches one that smuggled the dependency back in.
- **Depends on:** Task 6

## [x] Task 8: Guard against a new handler-only check
- **Goal:** The precedent's "omission is not survivable" property, obtained by
  test since there is no assembly step to hook.
- **Files:** `src/backend/tests/community/core/bot_config_surface/test_no_handler_only_checks.py`
- **Done when:**
  - [x] The test walks the five router modules and fails on a module-private
        callable that only a handler calls.
  - [x] The four deliberate exceptions — `_require_skills_grant`,
        `_directory_relative_paths`, the `application/zip` check, and the
        `_reject_read_only` mapper — are listed **with their reason strings**, so
        the list is reviewable rather than a mute allowlist.
  - [x] Adding a sixth private check to one of these routers fails the test.
- **Depends on:** Tasks 1–5
- **Note:** This is the task `plan.md` names as the last thing to cut under
  schedule pressure, and the reason not to.

---

## Group C — Finish

## [x] Task 9: Verify inertness end to end and open the PR
- **Goal:** Prove nothing moved, then ship.
- **Files:** —
- **Done when:**
  - [x] The full `openapi_v1` endpoint suite passes with **zero test files
        edited**. `git diff --stat -- tests/` shows only the three new files.
  - [x] `src/gateway/configs/schemas/bots.openapi.json` is unchanged — the seam
        is invisible in the published document.
  - [x] Pushed with `OCB_PRE_PUSH_RUN_CI=1`, per work-items §8.
  - [x] Draft PR titled `refactor(backend): <outcome>` per `AGENTS.md`, with
        Problem / Solution / Validation sections and a Spec section pointing at
        this directory. Closes #1509.
  - [x] The PR body states plainly which acceptance criteria this round did not
        reach, if any — work-items §7 says every item ships narrower than its
        criteria, and knowing which ones is more useful than quietly trimming
        them.
- **Depends on:** Tasks 1–8


---

## Implementation notes — where the build differed from this plan

Recorded because a plan that quietly absorbs its own surprises teaches nothing.

1. **Resources policy landed only in `resource_file_service.py`**, not also in
   `resource_addressing.py` as planned. That module's docstring states it
   "imports only `core.workspace` + `core.config_compose`" and that the
   resources service does not import it — putting a coordinate resolver needing
   `BotRepository` and the engine resolver there would have falsified both
   claims. Everything went beside `is_readonly` instead, which is where the
   plan wanted the path rules anyway.

2. **`engine_config_coords_from_bot` was split out**, unplanned. A second
   consumer exists that the inventory missed: the data-init operation
   (`openapi_v1/bots/router.py:1495`) imported `_engine_config_target`, fetches
   the bot itself, applies its own bot-type rule, and only then wants the
   address. Folding the fetch into one function would have made it resolve the
   same bot twice — a behaviour change dressed as a tidy-up. So there are two
   functions: the guarded one the engine-config handlers call, and the
   record-to-address half both share.

3. **Nothing moved out of the mcp router, and the row's `validators` is empty.**
   The category the manifest work most expected to need this seam turned out to
   have arrived already: the `server_code` permission rule lives in
   `DirectActivationService` and the unified config flow in
   `core.mcp.config_flow`. An earlier revision of this build routed the router's
   account-level `_ENTITY_TYPE` constant through `mcp_coords_from_record` for
   symmetry; that was wrong and was reverted — those config operations take no
   `bot_id` at all, so the two address different things and forcing them
   together would have claimed a sharing that does not exist.

4. **The module is `coords.py` + `table.py` with an inert `__init__.py`.** The
   category homes import the coordinate type and `table` imports from those
   homes, so re-exporting either from `__init__` turns importing the leaf into a
   cycle.

5. **Three test files were edited, not zero** as Task 9 assumed, and the
   assumption was simply wrong: this repository governs its architecture through
   test-resident inventories, so adding a core module *requires* registering it
   in each. All three are registrations; none weakens a check.

   | File | Why |
   | --- | --- |
   | `architecture/test_module_boundaries.py` | `BOUNDARY_SIGNIFICANT_MODULES` — without it the new `README.md` would be decorative, governed by nothing |
   | `framework/flow_coverage.py` | `_STRUCTURAL_NON_BUSINESS` — every core module must be flow-covered or exempt, and an index is neither; see note 8 |
   | `architecture/test_http_adapter_layer_is_http_only.py` | `_CORE_SERVICE_NAMES_OK` — the guard's own failure message directs you here for a pure helper, and `is_readonly` sits there already for the same reason |

   Only the first was found before the first push. The other two surfaced in the
   full suite afterwards, which is the cost of pushing on partial validation.
   **No behaviour test was modified**, which is what the criterion was really
   protecting: the endpoint suites are the inertness proof and they are
   untouched.

   `core/mcp/README.md` and `core/skill_center/README.md` gained the new
   dependency too — source files, not tests.

6. **Two now-unused imports were removed** from the resources and skills
   routers (`InvalidResourcePathError`, `LocalSkillNotFoundError`) — the moved
   functions raise them from `core` now, so the routers no longer name them.
   A consequence of the move, caught by lint.

7. **A pre-existing circular import was found and left alone.** Importing
   `core.services.resource_file_service` first, cold, fails through
   `devices → service_bot → task_queue`. Verified identical on an unmodified
   tree before touching anything; the suite imports in an order that avoids it.
   Not this feature's to fix.


8. **`bot_config_surface` is structural, not exempt.** `flow_coverage.py` draws
   a distinction worth respecting: `SINGLEBOX_E2E_EXEMPT` means "not covered
   yet", and every entry names what would unblock a flow, while
   `_STRUCTURAL_NON_BUSINESS` means "IS covered, just not nameable as a `covers`
   entry". An index whose every object is defined elsewhere and exercised by the
   resources, mcp and skill-centre flows is the second. Filing it as exempt
   would have claimed it uncovered and required a "drain when…" reason nothing
   could ever satisfy — no flow can cover an index directly.

9. **The full suite's two remaining failures are pre-existing.** Verified rather
   than assumed: `test_bot_build_service_skill_artifact.py` fails those same two
   parametrisations on a worktree at the merge base `b8754443`, with none of
   this change present. Final local result: **15813 passed**, 2 pre-existing
   failures, 60 skipped.

10. **A "green" report was wrong once, and the cause is worth keeping.** The
    first full run was reported as passing off a task notification's `exit code
    0` — which was the `tail` pipeline's status, not pytest's. The run had
    actually failed, and because it used `-x` it had stopped at the first of two
    failures and hidden the second. Read the `N passed, M failed` line; never a
    piped exit code.
