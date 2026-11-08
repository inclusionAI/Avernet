# Tasks: Content store as its own component

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: [x] Create `core/content_store/` with the store, its limits and the tree encoding
- **Goal:** Move the content store out of the Manifest as one component that imports nothing from `bot_config_manifest` at runtime, with `DIGEST_RE`, `MAX_AUDIT_ACTOR_CHARS` and the acm-tree-v1 encoding as its own.
- **Files:** `core/content_store/{__init__,limits,tree,errors,models,settings,service_protocol,service}.py`, `core/content_store/sql/2026_08_31_manifest_content.sql`, `core/bot_config_manifest/fetch/limits.py`, `core/bot_config_manifest/apply/entry_delivery.py`, `core/repository/protocols/bot/{__init__,content_store}.py`, `core/repository/implementations/bot/content_store.py`, `core/schema.py`, plus the import-path and rename updates in every consumer and test so the tree imports at this commit (the README lands with Task 3)
- **Done when:**
  - [x] `git mv` moved the six modules and the DDL; classes renamed to `ContentStoreService` / `ContentStoreProtocol` / `ContentStoreRepositoryProtocol` / `ContentStoreRepository`.
  - [x] `modifier` is `varchar(1100)` in the DDL and `String(MAX_AUDIT_ACTOR_CHARS)` in the ORM; `_MODIFIER_MAX` derives from the constant.
  - [x] `fetch/limits.py` re-exports `DIGEST_RE` from the store; `entry_delivery.py` no longer defines or exports the tree functions.
  - [x] `python -c "import agentclaw.community.core.content_store"` succeeds with `bot_config_manifest` absent from `sys.modules` afterwards (type-only dependency).
  - [x] `tests/community/core/content_store/` (moved `test_content_store.py` + new `test_tree.py`) and `tests/community/repository/bot/test_content_store_repository.py` (moved, plus the DDL/ORM width test) pass; `test_manifest_ddl_contract` covers `content_store/sql` (pulled forward from Task 3 because the move broke it).
- **Depends on:** —

## Task 2: [x] Point the Manifest and the Genome at the component; wire it at the composition root
- **Goal:** Every consumer imports from `core.content_store`; the component binds in its own DI module; the genome's actor widths derive from the shared constant.
- **Files:** `core/bot_config_manifest/apply/{entry_delivery,fetch_context,source_fetchers,source_resolver}.py`, `core/bot_config_manifest/apply/materialisers/resources.py`, `core/bot_genome/{limits,registry,canonical,diff,spec_checks}.py`, `core/bot_genome/capture/capture.py`, `core/bot_genome/patch/ops.py`, `core/bot_genome/repository/models.py`, `core/bot_genome/models.py`, `di/modules/content_store_module.py` (new), `tests/community/di/test_content_store_module_wiring.py` (new), `di/modules/{bot_management_module,manifest_fetch_module,bot_genome_module}.py`, `di/container.py`
- **Done when:**
  - [x] `grep -rn "bot_config_manifest.content\|bot_config_manifest/content\|ManifestContentService\|ManifestContentRepository\|entry_delivery import.*tree_bytes" apps/backend/src` is empty (done in Task 1 so every commit imports).
  - [x] `ContentStoreModule` binds the repository and provides `ContentStoreProtocol`; the two old bindings are gone; `build_injector` resolves `ContentStoreProtocol` and `GenomeRegistryProtocol` under the test profile.
  - [x] Genome `String(1100)` columns read `String(MAX_AUDIT_ACTOR_CHARS)`; `bot_genome.limits.MAX_AUDIT_ACTOR_CHARS` is the store's constant.
  - [x] All tests under `tests/community/core/bot_config_manifest`, `tests/community/core/bot_genome`, `tests/community/repository/bot`, `tests/community/contracts/test_bot_genome_schema.py`, `tests/community/e2e/test_genome_flows.py` pass with import paths updated; the registry width test asserts 1100 is recorded and 1101 refused.
- **Depends on:** Task 1

## Task 3: [x] Declare the boundary, exempt the module, update the docs
- **Goal:** The architecture gates know about the new component and the module READMEs and design docs name the new home.
- **Files:** `core/content_store/README.md` (Context Boundary), `core/bot_config_manifest/README.md`, `core/bot_genome/README.md`, `core/repository/README.md`, `docs/bot-evolution/01-genome.md`, `docs/bot-evolution/01-genome.zh-CN.md`, `tests/community/architecture/test_module_boundaries.py`, `tests/community/framework/flow_coverage.py`, `tests/community/core/bot_config_manifest/test_manifest_ddl_contract.py`
- **Done when:**
  - [x] `agentclaw.community.core.content_store` is in `BOUNDARY_SIGNIFICANT_MODULES` and its README declares `purpose` / `provides` / `consumes` / `consumed_by` / `internal_dependencies`.
  - [x] `bot_genome`'s declared `internal_dependencies` no longer lists `bot_config_manifest.content` or `bot_config_manifest.apply.entry_delivery`; `bot_config_manifest` lists `core.content_store`.
  - [x] `SINGLEBOX_E2E_EXEMPT` has a `content_store` entry with a reason (`test_manifest_ddl_contract` was already updated in Task 1).
  - [x] `tests/community/architecture` passes, apart from the four cold-import/boot tests that fail identically on the clean `dev` tip in this sandbox (the editable install of the project could not be built behind the blocked PyPI mirror; they import `agentclaw` in a subprocess without `src` on the path).
- **Depends on:** Task 2

## Task 4: [x] Verification
- **Goal:** Ensure the change meets every spec acceptance criterion.
- **Done when:**
  - [x] The full command block in plan.md §Testing Strategy passes (2816 passed; the four cold-import/boot tests pass once the project is importable in the venv).
  - [x] `./scripts/ci_test.sh --base origin/dev`: 25345/25369 cases pass, total line coverage 90.04 %, changed-line coverage 97.17 % (103/106). The 24 failures are `rsync: not found` in `core/service_bot/services/` tests, which fail identically on the clean `dev` tip in this sandbox (no rsync binary); unrelated to this change.
  - [x] Every spec acceptance criterion is checked off.
- **Depends on:** Task 3

---

## Groups

- **Group A — The component:** Task 1
  - Theme: the content store exists on its own and its own tests pass.
- **Group B — Consumers and wiring:** Task 2
  - Theme: Manifest and Genome consume the component; the app composes it.
- **Group C — Boundaries and docs:** Task 3
  - Theme: the architecture gates and the READMEs describe the new shape.
- **Group D — Verification:** Task 4
  - Theme: final spec acceptance check.
