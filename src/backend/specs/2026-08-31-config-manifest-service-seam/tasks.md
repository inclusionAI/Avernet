# Tasks: One Seam for the Five Categories Apply Touches

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Groups run in order. Within a group, tasks are independent. The inertness proof
is the same in every task: **the existing endpoint tests pass unmodified.** If
one needs editing, stop and report it — that is a behaviour change, not a fixup.

---

## Group A — Move the checks to their core homes

## [ ] Task 1: Resources path policy
- **Goal:** The three workspace-path rules and the coordinate resolution become
  callable without a request.
- **Files:** `src/backend/.../core/services/resource_file_service.py`,
  `.../core/services/resource_addressing.py`,
  `.../adapters/http/openapi_v1/resources/router.py`
- **Done when:**
  - [ ] `safe_workspace_path` and `require_workspace_path` live beside
        `is_readonly` and raise `InvalidResourcePathError`, exactly as the
        router's `_safe_path` / `_require_path` do today.
  - [ ] `is_write_forbidden(path) -> bool` carries the ancestor walk. It returns
        a verdict and raises nothing.
  - [ ] `_reject_read_only` stays in the router as a two-line mapper over it, so
        the 403 body is bit-identical rather than argued to be equivalent.
  - [ ] `resource_coords_from_record` takes `BotRepository` as an argument, not
        from the DI container, and returns `BotConfigCoords`.
  - [ ] `resource_coords_from_spec` builds the same type from a create request's
        `entity_id` / `entity_type` / `engine_type` and an allocated `bot_id`,
        touching no repository. No caller until W13; pinned by unit test.
  - [ ] The three path validators take values only — given a `BotConfigCoords`
        they never reach a repository, so they run identically on both paths.
  - [ ] The router's `_safe_path`, `_require_path`, `_file_coords` names are
        bindings to the core functions — no second body anywhere.
  - [ ] `test_openapi_resources.py` and `test_resources_handlers.py` pass
        **unedited**.
- **Depends on:** —

## [ ] Task 2: Engine-config ownership and addressing
- **Goal:** The ownership guard and the entity resolution become callable
  without a request. This is the only in-scope group whose guard is an explicit
  service call rather than an implicit resolution.
- **Files:** `src/backend/.../core/services/engine_config.py`,
  `.../adapters/http/openapi_v1/bots/engine_config.py`
- **Done when:**
  - [ ] `engine_config_coords_from_record(bot_id, owner_id, *, bot_service)` performs the
        `get_bot(bot_id, owner_id)` guard and reads `entity_id`, `entity_type`,
        `active_engine` off the record, raising `BotNotFoundError` on both of
        today's paths — an unresolvable bot, and a bot with no `entity_id`.
  - [ ] `engine_config_coords_from_spec` reads the same three values off the
        create request and performs **no** ownership guard — there is no record
        to own in phase 1, and the caller's right to create is what
        `check_create_bot_preflight` already decides (`create_flow.py:494`).
  - [ ] Both handlers call the record constructor; neither keeps a local
        `_engine_config_target`.
  - [ ] The `# ownership/tenant guard` comment moves with the code it annotates.
  - [ ] The engine-config endpoint tests pass **unedited**.
- **Depends on:** —

## [ ] Task 3: Identity file type and coordinates
- **Goal:** The `.md` re-suffixing and the staff-entity resolution become
  callable without a request.
- **Files:** `src/backend/.../core/services/identity.py`,
  `.../adapters/http/openapi_v1/identity/router.py`
- **Done when:**
  - [ ] `physical_file_name(file_type)` produces the `<type>.md` form
        `IdentityService.validate_file_type` requires, and is the only place
        that suffix is applied.
  - [ ] `identity_coords_from_record` returns `BotConfigCoords` with
        `engine_type=None` — identity addresses no engine, and a defaulted engine
        here would be a value it never had.
  - [ ] `identity_coords_from_spec` returns the same, from request parameters.
  - [ ] `physical_file_name` reaches no repository, so the identity file-type
        rule runs at preflight.
  - [ ] All three handlers call them; no handler re-spells `entity_type =
        "staff"`.
  - [ ] The identity endpoint tests pass **unedited**.
- **Depends on:** —

## [ ] Task 4: Skills addressed-bot binding
- **Goal:** "This skill belongs to the bot the address names" becomes callable
  without a request.
- **Files:** `src/backend/.../core/skill_center/services/skill_query_service.py`,
  `.../adapters/http/openapi_v1/skills/router.py`
- **Done when:**
  - [ ] `require_addressed_bot(record, bot_id)` raises `LocalSkillNotFoundError`
        on mismatch, keeping the 404 mask and the enumeration-oracle reasoning
        in its docstring. Its `record` is a *skill* record, not a bot record, so
        it is already record-free in the sense that matters — W13 declares skills
        that do not exist yet, and has nothing to compare.
  - [ ] `skill_coords_from_spec` exists alongside the record constructor.
  - [ ] `_require_skills_grant`, `_directory_relative_paths` and the
        `application/zip` header check **stay in the router**, each with a
        comment naming which side of Rule 7's line it is on and why.
  - [ ] `test_openapi_bot_skills_read.py` passes **unedited** — including the
        test whose docstring names both helpers by their router spelling, which
        stays accurate because the names stay bound there.
- **Depends on:** —

## [ ] Task 5: MCP coordinates
- **Goal:** The staff-entity resolution becomes callable without a request.
- **Files:** `src/backend/.../core/mcp/config_flow.py`,
  `.../adapters/http/openapi_v1/mcp/router.py`
- **Done when:**
  - [ ] `mcp_coords_from_record` replaces the router's `_ENTITY_TYPE` constant,
        and `mcp_coords_from_spec` builds the same type from request parameters.
  - [ ] The catalogue reads, the network-type visibility masking and
        `_REFUSES_APP_ONLY` are untouched — none is a config-category write.
  - [ ] The mcp endpoint tests pass **unedited**.
- **Depends on:** —

---

## Group B — Declare the table and prove it holds

## [ ] Task 6: The `CONFIG_SURFACE` table
- **Goal:** One declared list of what governs each category, and the coordinate
  type the five rows share.
- **Files:** `src/backend/.../core/bot_config_surface/__init__.py`,
  `.../core/bot_config_surface/README.md`
- **Done when:**
  - [ ] `BotConfigCoords` is frozen and carries `(bot_id, owner_id,
        entity_type, entity_id, engine_type)`, with `engine_type: str | None`
        and no default.
  - [ ] `CONFIG_SURFACE` has exactly five rows — including `engine_config`,
        which W4 excludes from phase 1 but which must have its plug ready.
  - [ ] Each row carries `from_record`, `from_spec` and `validators` as three
        separate fields, so that "which of these needs a bot record" is answerable
        by reading the table rather than by reading five implementations.
  - [ ] The module imports nothing from `fastapi` or `adapters`, and defines no
        behaviour of its own — it names functions that live elsewhere.
  - [ ] `README.md` carries a Context Boundary block per
        `docs/arch/context-boundary-format.md`, and says in as many words that
        this module is an index and must not grow logic.
- **Depends on:** Tasks 1–5

## [ ] Task 7: Prove router and table share one object
- **Goal:** Make the anti-drift guarantee structural rather than claimed.
- **Files:** `src/backend/tests/community/core/bot_config_surface/test_config_surface.py`
- **Done when:**
  - [ ] One `is` assertion per moved function, binding the router's name to the
        table's entry. `is`, not `==` — two functions that merely behave alike
        are the failure this test exists to catch.
  - [ ] A test asserts `CONFIG_SURFACE` covers exactly the five categories, and
        names any that is missing.
  - [ ] Each moved function has a direct unit test that calls it with **no
        request, no app, and no DI container** — the capability the whole
        feature exists to deliver, proven rather than assumed.
  - [ ] **The record-free test:** build coordinates through every row's
        `from_spec` and run every row's validators against them, with no bot
        record anywhere in the fixture. This rehearses W13's preflight before
        W13 exists. If it needs a record to pass, the split did not happen and
        W13 will be forced to write a second validation copy.
  - [ ] A validator that reaches a repository fails this test. That is the
        point: `from_spec` and `from_record` return the same frozen type
        precisely so a validator cannot tell which path it is on, and this is
        what catches one that smuggled the dependency back in.
- **Depends on:** Task 6

## [ ] Task 8: Guard against a new handler-only check
- **Goal:** The precedent's "omission is not survivable" property, obtained by
  test since there is no assembly step to hook.
- **Files:** `src/backend/tests/community/core/bot_config_surface/test_no_handler_only_checks.py`
- **Done when:**
  - [ ] The test walks the five router modules and fails on a module-private
        callable that only a handler calls.
  - [ ] The four deliberate exceptions — `_require_skills_grant`,
        `_directory_relative_paths`, the `application/zip` check, and the
        `_reject_read_only` mapper — are listed **with their reason strings**, so
        the list is reviewable rather than a mute allowlist.
  - [ ] Adding a sixth private check to one of these routers fails the test.
- **Depends on:** Tasks 1–5
- **Note:** This is the task `plan.md` names as the last thing to cut under
  schedule pressure, and the reason not to.

---

## Group C — Finish

## [ ] Task 9: Verify inertness end to end and open the PR
- **Goal:** Prove nothing moved, then ship.
- **Files:** —
- **Done when:**
  - [ ] The full `openapi_v1` endpoint suite passes with **zero test files
        edited**. `git diff --stat -- tests/` shows only the three new files.
  - [ ] `src/gateway/configs/schemas/bots.openapi.json` is unchanged — the seam
        is invisible in the published document.
  - [ ] Pushed with `OCB_PRE_PUSH_RUN_CI=1`, per work-items §8.
  - [ ] Draft PR titled `refactor(backend): <outcome>` per `AGENTS.md`, with
        Problem / Solution / Validation sections and a Spec section pointing at
        this directory. Closes #1509.
  - [ ] The PR body states plainly which acceptance criteria this round did not
        reach, if any — work-items §7 says every item ships narrower than its
        criteria, and knowing which ones is more useful than quietly trimming
        them.
- **Depends on:** Tasks 1–8
