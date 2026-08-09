# Tasks: Repository Consolidation into `core/repository/`

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

**Standing constraint (R9): every task below lands in ONE commit.** Tasks are
review units, not commits — `corp/ocb` imports these module paths, so the tree
must never be pushed mid-move. Work through them in order, keep the working tree
uncommitted until Task 16, then commit once.

**Standing hygiene:** `uv sync` rewrites `src/backend/uv.lock` to the PyPI index
override. Run `git checkout -- src/backend/uv.lock` before committing. Never
commit that file.

---

## Task 1: Re-derive the moving set from the current tree
- **Goal:** Regenerate the classification and drift lists against `dev` at
  implementation time, because both have already moved once.
- **Files:** scratch scripts only; no tree changes.
- **Done when:**
  - [ ] The 44 top-level `plugins/` modules re-classified; the 36/8 split confirmed or the delta reported.
  - [ ] The in-core set re-confirmed (7 implementations).
  - [ ] The `SkillRepository` drifted-member set re-derived by AST diff (was 2, now 3 — expect further growth).
  - [ ] Any new repository or Protocol added on `dev` since 2026-08-08 is reported before proceeding.
  - [ ] `pytest tests/community/architecture/ -q` green as the pre-move baseline.
- **Depends on:** —

## Task 2: Separate the six co-located domain types
- **Goal:** Move records and errors out of Protocol source files so `protocols/`
  can be contract-only.
- **Files:** `core/quality/models.py` (new), `core/channel/models.py` (new),
  `core/caller_identity/contracts.py`, `core/skill_center/errors.py`,
  `core/bot_management/errors.py`, plus their importers.
- **Done when:**
  - [ ] `QualityTaskRecord`, `ChannelRecord`, `CallerIdentityLockMismatchError`, `CallerIdentityEngineChangedError`, `ActiveSkillSetReferenceError`, `BotLookupAmbiguousError` each live in a non-Protocol module.
  - [ ] All 4–12 external importers of each re-point.
  - [ ] `pytest tests/community -q` green (no behaviour change).
- **Depends on:** Task 1

## Task 3: Relocate the five shared ORM models out of `plugins/local/`
- **Goal:** Remove the Rule-14 layering violation at its root (R7).
- **Files:** `core/devices/repository/models.py` (new), `core/skill_center/orm.py`
  (new), `core/system_config/orm.py` (new); delete
  `plugins/local/sqlite_models.py`, `plugins/local/system_config_models.py`.
- **Done when:**
  - [ ] The 5 model classes moved with `__tablename__` and columns byte-identical.
  - [ ] `plugins/local/database.py:150` bootstrap imports re-pointed (all three new modules).
  - [ ] `plugins/local/device_lifecycle.py` and the 12 test modules importing `EntityDeviceBinding` re-pointed.
  - [ ] Local SQLite boot creates every table it did before (`pytest tests/community/plugins/local/test_database_bootstrap_*.py`).
- **Depends on:** Task 1

## Task 4: Create `core/repository/protocols/` with abstract contracts
- **Goal:** All 46 Protocols in 22 domain-grouped modules, every member
  `@abstractmethod`, zero runtime domain imports.
- **Files:** `core/repository/protocols/*.py` (22 new), `core/repository/__init__.py`.
- **Done when:**
  - [ ] All 46 Protocols present; `@runtime_checkable` preserved where it exists today.
  - [ ] Every public member carries `@abstractmethod`.
  - [ ] Every module opens `from __future__ import annotations`; every `agentclaw` import sits under `if TYPE_CHECKING:`.
  - [ ] `QuarantineRepositoryProtocol` split out of `core/skills_pool/quarantine.py`, leaving its services behind.
  - [ ] Each module imports cleanly in isolation (`python -c "import ..."` per module).
- **Depends on:** Task 2

## Task 5: Author the two missing `bot_chat` Protocols
- **Goal:** Give `OpenBotChatRepository` and `BotChatDbRepository` contracts
  derived from their current public surface — no members added or dropped (R3b).
- **Files:** `core/repository/protocols/bot_chat.py`.
- **Done when:**
  - [ ] Both Protocols mirror the existing public method sets exactly (AST-diffed against the implementations).
  - [ ] Both are `@runtime_checkable` with `@abstractmethod` members.
- **Depends on:** Task 4

## Task 6: Retire `plugin_api/local_skill_cleanup.py`
- **Goal:** Move `LocalSkillCleanupRepository` to `protocols/skill_center.py`; it
  is a repository contract with one implementation, not a plugin contract.
- **Files:** delete `plugin_api/local_skill_cleanup.py`; update
  `di/modules/skill_center_module.py` and `plugin_api/README.md`.
- **Done when:**
  - [ ] The Protocol lives in `protocols/skill_center.py`; the old module is gone.
  - [ ] `plugin_api/README.md`'s Protocol count is corrected.
  - [ ] `pytest tests/community/architecture/test_protocol_contracts.py` green (it never discovered this Protocol — confirm the count is unaffected).
- **Depends on:** Task 4

## Task 7: Move the 36 plugin-layer implementations
- **Goal:** Relocate bodies to `implementations/`, each declaring its Protocol(s)
  as base, with no behaviour change.
- **Files:** `core/repository/implementations/*.py` (36); delete the originals.
- **Done when:**
  - [ ] All 36 modules moved under their existing basenames.
  - [ ] Each class declares its Protocol(s); `SkillRepository` and `SkillsPoolLayoutRepository` declare two each.
  - [ ] Bodies unchanged apart from imports, the `class` line, and vendor-docstring rewrites.
  - [ ] The 27 `ZDAS` docstring mentions rewritten to capability language.
- **Depends on:** Tasks 3, 4, 6

## Task 8: Move the 7 in-core implementations
- **Goal:** Bring `common_config`, the four governance repos, and both `bot_chat`
  repos into `implementations/` under their new names.
- **Files:** `implementations/{common_config,governance_*,bot_chat_*}_repository.py`;
  delete `core/{common_config,bot_chat}/repository/` and
  `core/economy/governance/repositories/`.
- **Done when:**
  - [ ] All 7 moved and renamed per `plan.md`.
  - [ ] `core/economy/governance/repositories/orm.py` → `core/economy/governance/orm.py`, with its 3 `domain/` importers and the `plugins/local/database.py:160` bootstrap re-pointed.
  - [ ] Emptied `repository/` packages deleted, their `__init__.py` re-exports removed from importers.
- **Depends on:** Tasks 4, 5

## Task 9: Relocate the eight non-repositories
- **Goal:** Move mixins and helpers without giving them contracts (R4).
- **Files:** `implementations/skills_pool_{capability,operational,post_cutover,quarantine}_repository.py`,
  `implementations/skills_pool_{layout_persistence,cutover_diagnostics}.py`,
  `implementations/governance_task_record_query.py`, `core/skills_pool/runtime.py`.
- **Done when:**
  - [ ] Seven plain modules sit in `implementations/`; none gains a Protocol.
  - [ ] `SkillsPoolRuntime` lives at `core/skills_pool/runtime.py`; its Protocol stays in `core/skills_pool/ports.py`.
  - [ ] `plugins/http_client.py` is untouched and still bound.
- **Depends on:** Tasks 7, 8

## Task 10: Resolve the `SkillRepository` contract drift
- **Goal:** Reconcile the drifted members so the class constructs under R2, with
  no behaviour change for any caller.
- **Files:** `core/repository/protocols/skill_center.py`.
- **Done when:**
  - [ ] The drifted set from Task 1 is resolved (expected: drop the orphaned declarations from `SkillRepository`; every caller reaches them via `SkillSetRepository`).
  - [ ] `SkillRepository` constructs without `TypeError`.
  - [ ] No call site changes.
- **Depends on:** Task 7

## Task 11: Re-point all DI wiring
- **Goal:** Every binding resolves against the new paths, on every profile.
- **Files:** ~20 `di/modules/*.py`, `di/modules/infrastructure/**`.
- **Done when:**
  - [ ] No `agentclaw.community.plugins.<repo>` import remains outside `plugins/local` and `plugins/community`.
  - [ ] `BotChatDbRepositoryProtocol` bound; its 3 construction sites inject instead of building (R3b).
  - [ ] `build_injector()` succeeds on all four profiles (`pytest tests/community/di -q`).
- **Depends on:** Tasks 7, 8, 9

## Task 12: Update guards, allowlists, and boundary declarations
- **Goal:** Every path-keyed guard reflects the new tree, with no new suppression.
- **Files:** `tests/community/architecture/test_no_oversized_modules.py`,
  `test_module_boundaries.py`, 21 domain `README.md`, `core/repository/README.md` (new).
- **Done when:**
  - [ ] The oversized allowlist re-keyed to `core/repository/implementations/skill_repository.py`; no stale entry.
  - [ ] `agentclaw.community.core.repository` added to `BOUNDARY_SIGNIFICANT_MODULES`, with a `README.md` carrying a Context Boundary section (Rule 22 + §8 "declared role").
  - [ ] 21 domain READMEs declare the new dependency.
  - [ ] `_ALLOWLIST` in `test_core_no_concrete_plugin_imports.py` still empty.
  - [ ] `skills_pool_layout_repository.py` ≤ 1000 lines and `bot_repository.py` < 1000 — measured, not assumed.
  - [ ] `pytest tests/community/architecture/ -q` green with no new allowlist entry.
- **Depends on:** Task 11

## Task 13: Add the contract-enforcement guard
- **Goal:** Make R2 self-enforcing so the contract cannot silently drift again.
- **Files:** `tests/community/architecture/test_repository_contracts.py` (new).
- **Done when:**
  - [ ] Every Protocol member asserted `@abstractmethod`.
  - [ ] Every implementation asserted to declare a Protocol base.
  - [ ] A deliberately incomplete subclass raises `TypeError` naming the missing member.
  - [ ] `protocols/` asserted to hold no runtime `agentclaw` imports.
- **Depends on:** Task 12

## Task 14: Move and re-point the test tree
- **Goal:** Test location mirrors code location (D3).
- **Files:** 49 modules under `tests/community/plugins/` → `tests/community/repository/`;
  59 further modules re-point imports.
- **Done when:**
  - [ ] All 108 affected test modules import the new paths.
  - [ ] No repository test lands under any `tests/*/endpoints/` directory (the new `test_no_mock_in_endpoint_tests.py` is path-keyed).
  - [ ] `tests/community/plugins/` retains only genuine-plugin tests (`local/`, `community/`, `test_http_client.py`).
  - [ ] `pytest tests/community -q` green.
- **Depends on:** Task 11

## Task 15: Produce the path map
- **Goal:** Deliver R6 — the artifact `corp/ocb` is updated from.
- **Files:** `specs/2026-08-05-core-repository-consolidation/path-map.md` (new).
- **Done when:**
  - [ ] Every moved module listed old → new, covering implementations, Protocols, the 5 ORM models, the 6 co-located types, the 8 non-repositories, and deleted modules.
  - [ ] Every moved class listed with its old and new import path.
  - [ ] Generated from the tree, not hand-written, then spot-checked.
- **Depends on:** Task 14

## Task 16: Verification, commit, and coverage re-baseline
- **Goal:** Confirm every spec acceptance criterion, land the single commit, and
  reconcile the coverage gate from real CI numbers.
- **Files:** `scripts/ci/singlebox_coverage_modules.yaml` (only if CI says so).
- **Done when:**
  - [ ] All 8 spec success criteria verified explicitly, one by one.
  - [ ] `pytest tests/community -q` and `tests/community/architecture/` green.
  - [ ] R8 confirmed: a body-level diff review shows every moved implementation differs only in imports, `class` line, and docstrings.
  - [ ] `git checkout -- src/backend/uv.lock`; one commit; force-push with lease.
  - [ ] CI read after push. Any `core_min_percent` breach re-pinned **only** for modules whose denominator actually moved, each with a justification comment matching the `harness` 41.59→41.30→40.86 precedent. No `core_paths` trimmed.
  - [ ] PR description updated to Problem / Solution / Validation.
- **Depends on:** Task 15

---

## Groups

- **Group A — Unblock the layering:** Tasks 1, 2, 3
  - Theme: Re-derive the truth, then clear the two things that make the move
    illegal — domain types living inside contracts, and ORM models living in the
    local-profile plugin package. Nothing has moved into `core/repository/` yet.

- **Group B — Build the contract surface:** Tasks 4, 5, 6
  - Theme: `protocols/` exists, complete and abstract, with the two missing
    contracts authored and the misfiled one reclaimed from `plugin_api/`.
    Implementations still sit where they are.

- **Group C — Move the bodies:** Tasks 7, 8, 9, 10
  - Theme: All 43 implementations and 8 non-repositories land in their new home,
    each declaring its contract, with the drift that blocks construction resolved.

- **Group D — Rewire and enforce:** Tasks 11, 12, 13
  - Theme: DI, guards, allowlists, and boundary declarations catch up, and the
    contract becomes self-enforcing so this cannot regress.

- **Group E — Tests, map, and landing:** Tasks 14, 15, 16
  - Theme: Test tree mirrors the code, the path map ships for the `ocb` side, and
    the whole thing lands as one commit with the coverage gate reconciled.
