# Tasks: `GET /bots/{bot_id}/skills` Returns Every Skill the Bot Has

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Sequencing: bases on `dev_refactory_collaboration`, branch
> `claude/list-skills-endpoint-68yola`. Tasks 1–3 are independent edits behind
> the service; Task 4 is the only one that changes what HTTP returns, so nothing
> is half-switched before it.
>
> Note: Tasks 1–3 were partly drafted in the working tree before this spec was
> written. Each is still verified against its "Done when" list here.

## Group A — The bridge and the paging query

### Task 1: `BotSkillSetBridge` and `bot_skillset_bridge`  `[x]`

- **Goal:** One read that answers which Skills a SkillSet brings to a bot and
  which of them Installation should hold.
- **Files:**
  `core/repository/skill_set_control_plane_types.py`,
  `core/repository/protocols/skill_set_control_plane.py`,
  `core/repository/implementations/skill_center/skill_set_control_plane.py`
- **Done when:**
  - [x] `BotSkillSetBridge(members, activate, deactivate)` exists, with the
        invariants from `plan.md` *API / Interface Changes* in its docstring.
  - [x] The Sets read are the bot's own ordinary Sets, the bot's own Default
        Set, and the platform Default for its engine — the same reachability
        `get_all_active_skill_sets_for_env` gives the runtime.
        `_bot_sets` is the helper; `list_sets` keeps its own narrower scope.
  - [x] A Default Set is treated as active regardless of its `is_active`
        column, matching `skill_set_item`.
  - [x] Owner exclusions are dropped from `members` for Default Sets only —
        they are the only thing that drops a row (`spec.md` *Acceptance* 3).
        An exclusion row naming an ordinary Set is proven inert.
  - [x] No branch on a Skill's source prefix anywhere in the bridge
        (`spec.md` *Decisions* 3). Removed from the pre-SDD draft.
  - [x] A Skill claimed by both an active and an inactive Set is in `activate`
        only (`spec.md` *Decisions* 4).
  - [x] Not in the plan, added while implementing: membership is read through
        a tenant/env-scoped join on `Skill`, not off the membership row, so the
        repair can never install a Skill the listing would refuse to show.
- **Tests:** `tests/community/repository/skill_center/test_skill_set_control_plane_uow.py`
  — one test per bullet above, seeded like the existing
  `test_ensure_active_skillset_installations_*` cases.
- **Depends on:** —

### Task 2: `list_bot_skills`  `[x]`

- **Goal:** Page the union of Bot-owned rows and bridged rows.
- **Files:**
  `core/repository/protocols/skill_center.py`,
  `core/repository/implementations/skill_center/skill.py`
- **Done when:**
  - [x] `list_bot_local_skills` is gone; `list_bot_skills` takes
        `skill_set_member_ids` and drops the `local://` predicate.
  - [x] With no bridged ids the predicate stays exactly "Bot-owned", so a bot
        with no SkillSet sees no change.
  - [x] A bridged row owned by neither the bot nor the caller is returned —
        and passing an id in is not a way around the tenant read guard.
  - [x] `keyword`, `active`, `total`, ordering, and paging all apply to the
        merged set; `total` is the filtered size, not the page's.
  - [x] `_public_local_skill` is renamed `_public_bot_skill`; no caller of the
        old name remains.
- **Tests:** `tests/community/repository/skill_center/test_local_skill_query_tenant_isolation.py`
  (existing, updated to the new name) plus a new case for the bridged-row union
  and one asserting a bridged row from another tenant stays invisible.
- **Depends on:** —

## Group B — Service and adapter

### Task 3: Repair Installation, then page  `[x]`

- **Goal:** Sequence authorize → bridge → repair → page in the query service.
  The repair runs before the page because `active` is a filter: `total` and the
  page boundary are both wrong if Installation is stale (`spec.md`
  *Acceptance* 6).
- **Files:**
  `api/local_skill_query_service.py`,
  `core/skill_center/services/local_skill_query_service.py`,
  `di/modules/skill_center_module.py`
- **Done when:**
  - [x] `list_local_skills` is renamed `list_bot_skills` on the Service API
        protocol and the implementation; the signature is otherwise unchanged.
        The router call site and the four test fakes move with the rename, so
        no commit leaves the protocol half-switched.
  - [x] `_require_view_access` returns the bot it already loads; the engine
        (`runtime_layout_engine_for_bot` first, then `active_engine`) and `env`
        come from it, with no second bot read — pinned by a test on the read
        count.
  - [x] The repair installs `activate - installed` and uninstalls
        `deactivate ∩ installed`, and does nothing else.
  - [x] Authorization runs before any write — an actor who cannot see the bot
        cannot cause one; proven for both an invisible bot and a collaborator
        without permission.
  - [x] `get_local_skill` is untouched: the deprecated per-skill routes still
        resolve a bot from a Local Skill only.
  - [x] The DI provider passes the two new repositories.
- **Tests:** `tests/community/skill_center/` — a service-level test with a fake
  bridge and a recording Installation repository: installs the missing, deletes
  the stale, writes nothing for a Skill no Set reaches (`spec.md`
  *Acceptance* 7), and writes nothing at all when authorization fails. Second
  call writes nothing (`spec.md` *Acceptance* 10).
- **Depends on:** Task 1, Task 2

### Task 4: Switch the endpoint  `[x]`

- **Goal:** The public listing answers with every Skill the bot has.
- **Files:**
  `adapters/http/openapi_v1/skills/router.py`
- **Done when:**
  - [x] `list_skills` calls `list_bot_skills`; the docstring says what the
        operation now returns and that `active` is desired state.
  - [x] The deprecated `GET /openapi/v1/bots/skills` shim still resolves and
        still returns the same body as the canonical route — `test_legacy_parity`
        and `test_deprecation_headers` pass unchanged.
  - [x] No change to the route's dependencies, response model, or errors —
        `test_admission_inventory` and `test_authorization_inventory` pass
        unchanged.
  - [x] End to end: a Skill only a SkillSet ties to the bot is listed, gains
        its Installation row, and answers the `active=true` filter.
- **Tests:** `tests/community/adapters/http/openapi_v1/test_skills_endpoints.py`,
  `test_skills_shared_bot_grant.py`, `test_app_only_bot_not_on_the_wire.py`,
  `test_self_checked_routes_refuse.py` — existing fakes renamed; one HTTP test
  that a bridged non-Local Skill appears in the page.
- **Depends on:** Task 3

## Group C — Close out

### Task 5: Record the exception and run the gates  `[x]`

- **Goal:** The README stops asserting something the code no longer does, and
  the change is verified.
- **Files:** `core/skill_center/README.md`
- **Done when:**
  - [x] The *Change impact* paragraph names this listing as the one GET that
        repairs Installation, that it uses `repair_bot_skillset_installations`
        rather than the materializer, and that it deletes rows and reads
        Default exclusions (`spec.md` *Decisions* 1–2).
  - [x] `internal_dependencies` still covers every import the touched modules
        make — `tests/community/architecture/` passes (170).
  - [x] Backend unit tests for the touched areas pass: openapi_v1, core and
        repository `skill_center`, and contracts — 2481 passed, 5 skipped.
- **Depends on:** Task 4
