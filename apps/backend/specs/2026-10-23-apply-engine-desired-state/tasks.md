# Tasks: The apply engine takes a typed desired state

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> One pull request (owner's decision, review round 1). Groups are the
> order of work (inside Group 2 the compiler and the materialiser switch,
> Tasks 4–7, land before the mechanical move, Task 3, so the move is pure
> relocation); the suite is green at the end of every group. Inside
> Group 2 each materialiser is its own commit, but the suite is green only
> once the group is complete (the orchestrator switches input type with
> the first materialiser; there is no typed-to-raw shim).

## Group 1 — The component and the model

### Task 1: Behaviour lock and the package skeleton `[x]`
- **Goal:** Record what must keep passing; create `core/apply_engine/` with its boundary.
- **Files:** `core/apply_engine/{__init__,README}.md`, `tests/community/architecture/test_module_boundaries.py`
- **Done when:**
  - [x] The Manifest, creation, DI, HTTP apply and genome suites are run on the branch tip and their pass counts noted in the PR description's Validation section.
  - [x] `apply_engine` is in `BOUNDARY_SIGNIFICANT_MODULES`; its README carries a `## Context Boundary` naming what it provides and consumes; the boundary test passes with the empty package.
- **Depends on:** —

### Task 2: The desired-state model and the compiler contract `[x]`
- **Goal:** The typed input, with its invariants, and the compiler/StateRef types.
- **Files:** `core/apply_engine/{desired_state,compiler,content,constructs}.py` (the construct enums moved here from `bot_config_manifest/capabilities.py`, which imports them back until Task 3 rewrites the importers); `tests/community/core/apply_engine/test_desired_state.py`
- **Done when:**
  - [x] Every type in plan § The model exists, frozen, with enums for the closed sets; `DesiredState.declaration(construct)`, `DesiredState.EMPTY`, `RESOURCES_ROOT`.
  - [x] `__post_init__` refuses a malformed digest, a tree path without `/`, a file path with one, a resource path outside the root, a duplicate identity within a declaration, an empty server code; tests cover each.
  - [x] `McpOverride.as_config()` round-trips the mapping the MCP ports take today (headers lower-cased only in the comparison helper, as `mcp.py` does now).
  - [x] `DesiredStateCompiler`, `ManifestStateRef`, `GenomeStateRef` (with `to_payload`/`from_payload`), `Compiled` defined and exported.
- **Depends on:** Task 1

## Group 2 — Move the engine, split the Manifest into a compiler

### Task 3: Move the engine-side modules `[x]`
- **Goal:** Relocate everything that runs after a state exists; imports updated, no behaviour change.
- **Files:** `git mv` per plan § Key Files: constructs/capabilities, delivery mode, context, order, outcomes, registry, orchestrator, materialisers, delivery, delegates, narrow ports, managed_files, redeliver, carry_forward, triggers, apply_task, run/{service,report_codec,lock_reaper,termination}, repository/apply_models, cli_tools; repository protocols/impls; `api/apply_run_service.py`; DI modules; HTTP imports; `creation.py`, `create_job.py`, agentswarm host; test moves and `_fakes.py` split.
- **Done when:**
  - [x] `BotConfigManifestApplyService` → `ApplyRunService`, protocol → `ApplyRunServiceProtocol`; every caller and test updated; no re-export shim left in `bot_config_manifest`.
  - [x] `bot_config_manifest/README.md` boundary lists only what remains; `apply_engine/README.md` lists the moved exports; the boundary, narrow-ports, service-API conformance and no-module-level-instance tests pass.
  - [x] All moved tests pass at their new paths with import changes only; the pass count equals Task 1's.
- **Depends on:** Task 2
- **Notes:** landed after Tasks 4–7, as planned. Deviations from the plan's file list: `capabilities.py` moved whole and stays beside `constructs.py` rather than merging into it; `support_matrix.py` (its lazy import), `fetch/unpack.py` (the cli_tools service unpacks; the archive caps now live on `apply_engine/unpack.py` and `fetch/limits` re-exports them) and the identity-file vocabulary (`apply_engine/identity_types.py`, re-imported by `schema/entries`) moved too, because the engine may not import the Manifest package. The protocol module is `run/service_protocol.py` (the API-layer gate accepts re-exports only from a `*_protocol` module). Two narrow Protocols keep the engine free of Manifest imports: `CapabilityResolver` (what the run service asks of the Manifest service) and `ManifestPresence` (the managed-files reader's one question). The creation triggers moved to `apply_engine/triggers.py`. Tests: one shared `_fakes.py` under `tests/community/core/apply_engine/` (the Manifest compiler and sources suites import it) rather than the two-way split the plan sketched; `apply_engine` is listed in the singlebox E2E exemption beside `bot_config_manifest`, whose flows cover it.

### Task 4: The Manifest compiler `[x]`
- **Goal:** One place that reads a document's grammar, fetches, files bytes and emits a `DesiredState`.
- **Files:** `core/bot_config_manifest/sources/` (moved funnel), `core/bot_config_manifest/compiler/{declared,session,compile}.py`, `compiler/entries/{script,mcp,identity,skills,resources,cli_tools,engine_config}.py`, `schema/validator.py` (`script: null` doc, `resources_outside_root`), `schema/entries.py`; `tests/community/core/bot_config_manifest/compiler/test_{declared,script,mcp,identity,skills,resources,cli_tools,compile}.py`, `tests/community/core/bot_config_manifest/sources/*` (moved), `test_manifest_schema.py`
- **Done when:**
  - [x] `declared_entries` returns `[]` for `script: null` and `None` for an absent `script`; both pinned by one test (#2862).
  - [x] The validator refuses a resource path outside `data/` at PUT with `resources_outside_root`; the compiler emits the same as an `EntryFailure` for a stored document.
  - [x] Each entry compiler emits the typed entries and `EntryFailure`s for every refusal the matching materialiser test asserts today (lifted case by case, same reason text); fetch, `keep_last` notes, moved-ref notes, budget expiry and `on_fetch_failure: fail` are exercised with the existing fakes.
  - [x] Inline identity/resource content and resource trees are filed as produced content and are `is_reachable` for the bot; skills are filed as acm-tree-v1 under the delivery's receipt URL.
  - [x] CLI tools: a pinned digest already reachable is emitted without a fetch; a git source is fetched every time; `DesiredCliTool.pin` is `None` over git.
  - [x] `compile` returns `Compiled(state, sources)` with the git `SourceResolution` rows the session adopted, and the session's checkout trees are gone when it returns.
- **Depends on:** Task 2 (the move, Task 3, follows the switch)
- **Notes:** the keep_last / moved-ref / budget cases stay covered by the fetch-funnel suites (`test_source_resolver`, `test_source_fetchers`), which the compiler calls unchanged; the Manifest schema doc (`docs/bot-config-manifest/manifest-schema.zh-CN.md` §3.2) and the `script: null` note are written in Task 12.

### Task 5: Orchestrator and the script, mcp, identity materialisers on the model `[x]`
- **Goal:** The engine takes a `DesiredState`; three materialisers consume typed entries.
- **Files:** `core/apply_engine/{orchestrator,registry,context,outcomes}.py`, `materialisers/{script,mcp,identity}.py`, `delivery.py` (`content` port); `tests/community/core/apply_engine/{test_apply_engine,test_orchestrator_stays_generic,test_identity_materialiser}.py` and the mcp cases in `test_apply_engine.py`
- **Done when:**
  - [x] `ApplyOrchestrator.apply(ctx, state, …)`; `declaration.failures` abort the construct exactly like `ResolveResult` failures (one test pins the entry outcomes and the untouched area).
  - [x] `ApplyContext` has no `budget`/`source_session`; `entry_identity` takes typed entries.
  - [x] script/mcp/identity `resolve` keep only the engine-side checks in plan § contract; identity reads bytes through the `content` port and refuses non-UTF-8.
  - [x] Their tests build typed entries directly; engine-side cases unchanged in count.
- **Depends on:** Task 4
- **Notes:** the suites written against raw entries run through `CompilingMaterialiser` / `EngineRig` (`_fakes.py`): a raw entry goes through the real entry compiler over the same fakes and the typed result is what the materialiser sees, so every refusal keeps its test whichever side now owns it. The fetch-funnel suites take a `fetch_context()` (a `CompileSession`) since the apply context no longer carries the budget or the session. Two belts landed on the way: `file_single` files a produced row when a delivery filed no receipt the bot can reach, and the skills compiler accepts a stored `acm-tree-v1` blob served back by `keep_last` for a git package.

### Task 6: Skills, resources and cli_tools materialisers on the model `[x]`
- **Goal:** The three fetching materialisers stop fetching.
- **Files:** `materialisers/{skills,resources,cli_tools}.py`, `cli_tools/{declarations,service}.py` (`CliToolDecl.content`, `_acquire` reads the store); tests `test_skills_materialiser.py`, `test_resources_materialiser.py`, `test_cli_tools_materialiser.py`, `cli_tools` service tests
- **Done when:**
  - [x] Skills: decoded tree → `validate_directory` → canonical zip → upload; a Center or repo skill resolves to a failure naming RSI-04; `keep_last`/stored-copy notes come from `DesiredSkill.note`.
  - [x] Resources: admission per member from stored bytes; trees decoded; nesting ban kept; the tree-delete-then-write order kept for now (clear-then-write is Task 9).
  - [x] CLI tools: no `entry` on `CliToolDecl`, no resolver on `CliToolService`; `install` reads `decl.content` from the store; the upload road is untouched; convergence key from `pin`.
  - [x] No materialiser imports `bot_config_manifest` beyond what the move (Task 3) relocates; the "materialisers name no engine" and "orchestrator stays generic" tests pass.
- **Depends on:** Task 5
- **Notes:** `CliToolService` takes a `ContentReader` in place of the resolver; `FETCH_CATEGORY` and `CliToolDecl.from_entry` are gone (the grammar lives in `compiler/entries/cli_tools.py`, the `DesiredCliTool → CliToolDecl` mapping is pinned in the materialiser suite). Resource test documents moved under `data/`, the root the validator now enforces.

### Task 7: The apply run on compilers `[x]`
- **Goal:** `ApplyRunService` starts from a `StateRef` and resolves a compiler.
- **Files:** `core/apply_engine/run/service.py`, `apply_task.py`, `run/report_codec.py`, `outcomes.py` (`ApplyReport.state`), `di/modules/{apply_engine_module,manifest_fetch_module,bot_management_module}.py`, HTTP apply routes and schemas, `creation.py`, `create_job.py`; tests `test_apply_service_lifecycle.py`, `test_apply_service_uses_strategy.py`, `test_apply_task.py`, `di/test_apply_engine_module_wiring.py`, HTTP apply tests, creation tests
- **Done when:**
  - [x] `start_apply(..., state=StateRef)` and `dry_run(..., state=StateRef)`; an unregistered reference type is a `ValueError` before the lock; the payload carries `state`; the worker compiles then applies.
  - [x] The Manifest compiler reads the bot's recent reports for its baselines; `last_resolutions` lives in `compiler/baselines.py` (tests ported to `compiler/test_baselines.py`).
  - [x] Reports carry `state` inside the JSON; a persisted report without it reads back as `manifest`; the HTTP payload shows it (`ConfigManifestApply.state`).
  - [x] The compilers map (`StateCompilers`) is an injector map multibinding keyed by reference type with the Manifest compiler registered beside the fetch pipeline; wiring test covers it.
  - [x] Pass count equals Task 1's plus the new tests.
- **Depends on:** Task 6
- **Notes:** two deviations from the plan. (1) The compiler reads the apply *repository* for baselines rather than a `recent_applies` on the run service: the run service holds the compilers map, so a compiler holding the run service would be a construction cycle. (2) `DesiredStateCompiler` gained a synchronous `check(ctx, ref)`: `start_apply` keeps refusing a stored document that no longer validates *to the caller*, before an apply id exists (the HTTP 422 contract), and the genome compiler will refuse a missing or finished promotion the same way; the compile itself, which fetches, stays on the worker. A compile that raises on the worker is a failed apply with the lock released, the same terminal path a raising orchestrator takes.

## Group 3 — Resources totality

### Task 8: Resource road: list and clear the root; rows follow `[x]`
- **Goal:** The port can list and clear `data/`; the platform's resource rows can be replaced for a bot.
- **Files:** `core/ports/resource_file_port.py`, `core/services/resource_file_service.py` (`list_root`, `clear_root`), `core/apply_engine/resource_files.py`, `core/apply_engine/managed_files/ports.py`, `core/resources/service.py` (`replace_manifest_rows`); tests `test_device_port_forwarding.py`, `managed_files/test_store_ports.py`, resource service tests
- **Done when:**
  - [x] `list_root()` returns every file path under `data/` relative to the workspace (a level-by-level walk — no engine's `recursive` flag is trusted anywhere in the service, so the plan's `list_dir(recursive=True)` became the walk the directory download already uses, minus its curation); `clear_root()` is `delete_tree("workspace/data")` and answers `False` on an absent root; both forwarded by `DeviceResource`; `PlatformResource` lists and deletes its resources category.
  - [x] `replace_manifest_rows(files, created_by)` deletes the bot's file rows under `data/` and inserts one row per written file with source `manifest`; a write failure raises. Not one transaction: the resource repository offers no transaction seam, so it is delete-then-insert through the repository's own calls, and the materialiser reports a failed row write as a confirmed partial write for the next apply to converge.
- **Depends on:** Task 3
- **Notes:** the root is named once, as `RESOURCES_ROOT_DIR` in `core/workspace/constants.py` (the vocabulary both the file service and the engine can reach); the engine's `RESOURCES_ROOT` derives from it. The rows reach the materialiser through a new narrow port, `core/ports/resource_rows_port.py` (`ResourceRowsPort`, implemented by `ResourceService`), as a bot-id → port factory on `MaterialiserPorts.resource_rows`, which both DI bundles fill from `ResourceServiceFactoryProtocol`.

### Task 9: Resources materialiser converges by clear then write `[x]`
- **Goal:** Close #2162.
- **Files:** `core/apply_engine/materialisers/resources.py`; `tests/community/core/apply_engine/test_resources_materialiser.py`, `_fakes.py` (`FakeResourceFileService` keeps a tree)
- **Done when:**
  - [x] `plan` classifies from one `list_root()` (no per-member `exists` probes) and reports `removals = listed − declared member paths`; the declared-tree marker is gone.
  - [x] `write` clears the root first (a clear that does not land raises `ResourcesRootNotCleared`: the category is reported aborted and partially written — nothing was uploaded, but a device `rmtree` that errored may have taken part of the root — and its removals are not claimed), then uploads every member in declaration order with today's per-entry outcomes, then replaces the rows; a row failure raises `ConfirmedPartialWriteError`, which the orchestrator reports as a confirmed partial write (the files are on the bot, the rows are not).
  - [x] Tests: a path declared by the previous document and omitted now is gone; a bot-written file under `data/` is gone and listed in `removals`; a file outside `data/` is untouched; rows match the written set; the platform-store road mirrors each.
  - [x] Module docstring rewritten: the "cross-entry removals are v1-empty" paragraph replaced by the resources-root rule and clear-then-write.
- **Depends on:** Task 8
- **Notes:** the materialiser's production change landed with Task 8's commit (the registry hands it the rows factory, so the two could not be split cleanly); Task 9's commit is the suite, rewritten to the new semantics: three cases whose premise was the per-tree delete are gone, the rest keep their assertions, and the nine acceptance cases above are new.

## Group 4 — Genome: compile, observe drift, drop the delivery records

### Task 10: The genome compiler and `compile` on the model `[x]`
- **Goal:** A revision renders into the same `DesiredState`; the contract examples prove equivalence.
- **Files:** `core/bot_genome/compile.py` (rewritten), `registry.py`, `genome_registry_protocol.py`, `models.py` (`CompiledManifest` deleted), `spec_checks.py` (root rule), `promotion/applier.py`, `di/modules/{bot_genome_module,apply_engine_module}.py`; `api-contracts/genome/v1/examples/manifest-v1/*/compiled.yaml` (deleted), `manifest-v1-compatibility.md`, `README.md` §7; tests `bot_genome/test_compile.py` (rewritten), `test_spec_checks.py`, `contracts/test_bot_genome_schema.py`, `bot_config_manifest/compiler/test_equivalence.py`
- **Done when:**
  - [x] `compile_spec` covers every spec member per plan § Genome compiler; `GenomeCompiler` resolves a promotion id to its revision and refuses a missing or finished promotion.
  - [x] `GenomeRegistryProtocol.compile(bot, rev) -> DesiredState`; `RevisionApplier.preflight/start` take it; `PendingDeliveryApplier` still refuses everything; the genome compiler is registered with no production caller.
  - [x] Equivalence holds for every manifest-v1 case with a revision; the contract test no longer reads `compiled.yaml`; the compatibility doc describes the equivalence.
- **Depends on:** Task 7
- **Notes:** the equivalence test (`compiler/test_equivalence.py`) drives the real `ManifestCompiler` over the fixture sources (a fake git client serving a temporary copy of `sources/git-content`, since the source session removes a checkout's root when it closes; the two OSS fixtures in a fake object store) and compares each construct with `compile_spec`'s declaration as a set — a revision keeps its categories sorted by key, a document keeps its author's order. A category the document leaves out is undeclared on the Manifest side and declared empty on the genome side (the revision was captured from an empty bot), which the test reads as equal. `registry.py`, `genome_registry_protocol.py` and `models.py` carry Task 11's changes in the same commit, since one file cannot be split.

### Task 11: Drift observed; the promotion without delivery records `[x]`
- **Goal:** Drift by reading the bot; remove `applied_revision`, `by_apply_reference`, `apply_reference`, `document_*`.
- **Files:** `core/bot_genome/{registry,genome_registry_protocol,models,errors}.py`, `promotion/{service,applier}.py`, `repository/models.py`, `sql/2026_10_19_bot_genome.sql`, `core/repository/{protocols,implementations}/bot/genome.py`, `adapters/http/openapi_v1/bots/{genome,schemas_genome}.py`, `errors_genome.py`, `di/modules/bot_genome_module.py`; tests `test_registry_service.py`, `test_promotion_service.py`, `service_fixtures.py`, `test_genome_repository.py`, `test_genome_routes.py`, `endpoints/test_openapi_genome.py`, `test_domain_error_status_map_complete.py`
- **Done when:**
  - [x] `drift(bot)` answers `None` without `active`, `None` when the observed state equals `active` under its policy, `Drift(active, observed)` otherwise, and propagates `CaptureRefused`; nothing is recorded; tests with the capture `FakeReader`.
  - [x] Promotion checks drift for non-user actors only and raises `Drifted(active, observed)`; the 409 body carries both; a `CaptureRefused` from the check surfaces as 422 naming what could not be read.
  - [x] The three columns and the index are gone from DDL, ORM and row; the DDL/ORM pin test passes; `finish(status, apply_detail)`.
  - [x] `PromotionPayload` has no `document_digest`, `apply_reference`, `document`; OpenAPI snapshot updated; the registry has no `applies` dependency and the genome boundary README no longer names the apply service.
- **Depends on:** Task 10
- **Notes:** `ApplyResult` keeps `kind` and `detail` only (`reference` went with `apply_reference`); the promotion row records the delivery's answer and nothing of the state it delivered. The `Drifted` 409 carries `{active, observed}`. `CaptureRefused` already maps to 422 through the domain error map, so the promotion path needed no new mapping. The backend checks in no OpenAPI snapshot; the wire shape is pinned by the route and endpoint tests.

## Group 5 — Docs and closure

### Task 12: Docs, ADR, work items, context map
- **Goal:** Every document says what the code does.
- **Files:** `docs/bot-evolution/{01-genome,08-promotion,06-evolution-run,work-items}.md` (+ zh-CN), `docs/adr/0022-apply-engine-desired-state-and-observed-drift.md` (+ zh-CN), `CONTEXT-MAP.md`, `core/{apply_engine,bot_config_manifest,bot_genome}/README.md`, `api-contracts/genome/v1/README.md` §7–8, the Manifest schema docs (resources root, `script: null`)
- **Done when:**
  - [ ] Sections listed in the spec rewritten: the compiled document is the apply engine's desired state; drift is observed; no apply-id join, no document digest on reports or promotions; RSI-04 scope reduced to binding, Center/repo skills, service-bot publish, singlebox.
  - [ ] The ADR records: typed desired state as the engine's only input with compilers; drift observed, not tracked; the resources root; links to ADR 0019/0020 where they describe the digest mapping (link, do not edit them).
  - [ ] `work-items.md` RSI-03 "Left to RSI-04" and RSI-04 updated; #2162 and #2862 rows in the contract README § Known limitations closed.
- **Depends on:** Tasks 9, 11

### Task 13: Final gates and PR
- **Goal:** Green, within limits, reviewable.
- **Done when:**
  - [ ] `ruff check` and `ruff format --line-length 120 --check` clean on every new file; no touched file over 1,000 lines; no formatting churn in untouched lines.
  - [ ] `DEPLOY_PROFILE=test pytest tests/community` and `./scripts/ci_test.sh` pass.
  - [ ] PR body per template: Problem / Solution / Validation (pass counts before and after, equivalence cases) / Compatibility and risk (resources root rule on stored documents, clear-then-write window, skills receipt format, DDL edited in place) / Related issues (#2162, #2862, #3064, RSI-04).
- **Depends on:** Task 12
