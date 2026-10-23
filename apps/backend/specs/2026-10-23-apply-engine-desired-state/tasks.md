# Tasks: The apply engine takes a typed desired state

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
>
> Groups are PR-sized units; the suite is green at the end of every group.
> Inside Group 2 each materialiser is its own commit, but the suite is green
> only once the group is complete (the orchestrator switches input type
> with the first materialiser; there is no typed-to-raw shim).

## Group 1 — The component and the model

### Task 1: Behaviour lock and the package skeleton
- **Goal:** Record what must keep passing; create `core/apply_engine/` with its boundary.
- **Files:** `core/apply_engine/{__init__,README}.md`, `tests/community/architecture/test_module_boundaries.py`
- **Done when:**
  - [ ] The Manifest, creation, DI, HTTP apply and genome suites are run on the branch tip and their pass counts noted in the PR description's Validation section.
  - [ ] `apply_engine` is in `BOUNDARY_SIGNIFICANT_MODULES`; its README carries a `## Context Boundary` naming what it provides and consumes; the boundary test passes with the empty package.
- **Depends on:** —

### Task 2: The desired-state model and the producer contract
- **Goal:** The typed input, with its invariants, and the producer/StateRef types.
- **Files:** `core/apply_engine/desired_state.py`, `core/apply_engine/producer.py`; `tests/community/core/apply_engine/test_desired_state.py`
- **Done when:**
  - [ ] Every type in plan § The model exists, frozen, with enums for the closed sets; `DesiredState.declaration(construct)` and `DesiredState.EMPTY`.
  - [ ] `__post_init__` refuses a malformed digest, a tree path without `/`, a file path with one, a duplicate identity within a declaration, an empty server code; tests cover each.
  - [ ] `McpOverride.as_config()` round-trips the mapping the MCP ports take today (headers lower-cased only in the comparison helper, as `mcp.py` does now).
  - [ ] `DesiredStateProducer`, `StateRef` (`manifest()` constructor), `Produced` defined and exported.
- **Depends on:** Task 1

## Group 2 — Move the engine, split the Manifest into a producer

### Task 3: Move the engine-side modules
- **Goal:** Relocate everything that runs after a state exists; imports updated, no behaviour change.
- **Files:** `git mv` per plan § Key Files: constructs/capabilities, delivery mode, context, order, outcomes, registry, orchestrator, materialisers, delivery, delegates, narrow ports, managed_files, redeliver, carry_forward, triggers, apply_task, run/{service,report_codec,lock_reaper,termination}, repository/apply_models, cli_tools; repository protocols/impls; `api/apply_run_service.py`; DI modules; HTTP imports; `creation.py`, `create_job.py`, agentswarm host; test moves and `_fakes.py` split.
- **Done when:**
  - [ ] `BotConfigManifestApplyService` → `ApplyRunService`, protocol → `ApplyRunServiceProtocol`; every caller and test updated; no re-export shim left in `bot_config_manifest`.
  - [ ] `bot_config_manifest/README.md` boundary lists only what remains; `apply_engine/README.md` lists the moved exports; the boundary, narrow-ports, service-API conformance and no-module-level-instance tests pass.
  - [ ] All moved tests pass at their new paths with import changes only; the pass count equals Task 1's.
- **Depends on:** Task 2

### Task 4: The Manifest producer
- **Goal:** One place that reads a document's grammar, fetches, files bytes and emits a `DesiredState`.
- **Files:** `core/bot_config_manifest/sources/` (moved funnel), `core/bot_config_manifest/producer/{declared,session,produce}.py`, `producer/entries/{script,mcp,identity,skills,resources,cli_tools,engine_config}.py`, `schema/validator.py` docstring; `tests/community/core/bot_config_manifest/producer/test_{declared,script,mcp,identity,skills,resources,cli_tools,produce}.py`, `tests/community/core/bot_config_manifest/sources/*` (moved)
- **Done when:**
  - [ ] `declared_entries` returns `[]` for `script: null` and `None` for an absent `script`; both pinned by one test (#2862).
  - [ ] Each entry producer emits the typed entries and `EntryFailure`s for every refusal the matching materialiser test asserts today (lifted case by case, same reason text); fetch, `keep_last` notes, moved-ref notes, budget expiry and `on_fetch_failure: fail` are exercised with the existing fakes.
  - [ ] Inline identity/resource content and resource trees are filed as produced content and are `is_reachable` for the bot; skills are filed as acm-tree-v1 under the delivery's receipt URL.
  - [ ] CLI tools: a pinned digest already reachable is emitted without a fetch; a git source is fetched every time; `DesiredCliTool.pin` is `None` over git.
  - [ ] `produce` returns `Produced(state, sources)` with the git `SourceResolution` rows the session adopted, and the session's checkout trees are gone when it returns.
- **Depends on:** Task 3

### Task 5: Orchestrator and the script, mcp, identity materialisers on the model
- **Goal:** The engine takes a `DesiredState`; three materialisers consume typed entries.
- **Files:** `core/apply_engine/{orchestrator,registry,context,outcomes}.py`, `materialisers/{script,mcp,identity}.py`, `delivery.py` (`content` port); `tests/community/core/apply_engine/{test_apply_engine,test_orchestrator_stays_generic,test_identity_materialiser}.py` and the mcp cases in `test_apply_engine.py`
- **Done when:**
  - [ ] `ApplyOrchestrator.apply(ctx, state, …)`; `declaration.failures` abort the construct exactly like `ResolveResult` failures (one test pins the entry outcomes and the untouched area).
  - [ ] `ApplyContext` has no `budget`/`source_session`; `entry_identity` takes typed entries.
  - [ ] script/mcp/identity `resolve` keep only the engine-side checks in plan § contract; identity reads bytes through the `content` port and refuses non-UTF-8.
  - [ ] Their tests build typed entries directly; engine-side cases unchanged in count.
- **Depends on:** Task 4

### Task 6: Skills, resources and cli_tools materialisers on the model
- **Goal:** The three fetching materialisers stop fetching.
- **Files:** `materialisers/{skills,resources,cli_tools}.py`, `cli_tools/{declarations,service}.py` (`CliToolDecl.content`, `_acquire` reads the store); tests `test_skills_materialiser.py`, `test_resources_materialiser.py`, `test_cli_tools_materialiser.py`, `cli_tools` service tests
- **Done when:**
  - [ ] Skills: decoded tree → `validate_directory` → canonical zip → upload; a Center or repo skill resolves to a failure naming RSI-04; `keep_last`/stored-copy notes come from `DesiredSkill.note`.
  - [ ] Resources: admission per member from stored bytes; trees decoded; nesting ban and path re-validation kept; `_DECLARED_TREE` and the tree-delete-then-write order kept (clear-then-write is Task 11).
  - [ ] CLI tools: no `entry` on `CliToolDecl`, no resolver on `CliToolService`; `install` reads `decl.content` from the store; the upload road is untouched; convergence key from `pin`.
  - [ ] No materialiser imports `bot_config_manifest`; the "materialisers name no engine" and "orchestrator stays generic" tests pass.
- **Depends on:** Task 5

### Task 7: The apply run on producers
- **Goal:** `ApplyRunService` starts from a `StateRef` and resolves a producer.
- **Files:** `core/apply_engine/run/service.py`, `apply_task.py`, `run/report_codec.py`, `outcomes.py` (`ApplyReport.state`), `di/modules/{apply_engine_module,manifest_fetch_module,bot_management_module}.py`, HTTP apply routes and schemas, `creation.py`, `create_job.py`; tests `test_apply_service_lifecycle.py`, `test_apply_service_uses_strategy.py`, `test_apply_task.py`, `di/test_apply_engine_module_wiring.py`, HTTP apply tests, creation tests
- **Done when:**
  - [ ] `start_apply(..., state=StateRef)` and `dry_run(..., state=StateRef)`; an unknown producer kind is a `ValueError` before the lock; the payload carries `state_producer`/`state_reference`; the worker produces then applies.
  - [ ] `recent_applies` exposed for the Manifest producer's baselines; `_last_resolutions` lives in the producer.
  - [ ] Reports carry `state`; a persisted report without it reads back as `manifest`/`""`; the HTTP payload shows it; OpenAPI snapshot updated.
  - [ ] The producers map is an injector map keyed by kind with the Manifest producer registered; wiring test covers it.
  - [ ] Pass count equals Task 1's plus the new tests.
- **Depends on:** Task 6

## Group 3 — Resources totality

### Task 8: Engine adapter upload ledger and clear capability
- **Goal:** The engine records what the platform uploaded and can remove it in one call.
- **Files:** `engine/adapter/src/engine/community/core/engine/{capability,base}.py`, `core/file/{protocol,models,upload_ledger}.py`, `api/file/{router,schemas}.py`, `plugin_api/{openclaw,claude_code}/file.py`, `core/adapters/{openclaw,claude_code}/file.py`, `plugins/{openclaw,claude_code}/_file.py`, `engines/{openclaw,claude_code}/engine.py`; tests `core/file/tests/test_upload_ledger.py`, `api/tests/test_file_router.py`, `plugins/openclaw/tests/test_file_port.py`, `api/tests/test_claude_code_local_files.py`, capability parity tests
- **Done when:**
  - [ ] `file.uploads.clear` declared, in the `_file` domain, supported by both engines; `validate_capabilities` passes.
  - [ ] `upload(origin=)` records; `list_uploads(origin)` and `clear_uploads(origin)` answer by origin or all; a file the bot deleted in between is dropped silently; the ledger survives a crash mid-write (atomic rename).
  - [ ] Routes: `upload` with the optional `origin` field; `uploads/list`; `uploads/clear`; 501 without the capability; router tests cover each.
  - [ ] `engine/adapter/scripts/ci_test.sh` passes.
- **Depends on:** —

### Task 9: Backend resource road and port
- **Goal:** Every upload path names its origin; the device road exposes list/clear.
- **Files:** `core/devices/services/{device_filesystem,baas_device_filesystem,local_device_filesystem,teclaw_device_filesystem}.py`, `core/services/resource_file_service.py` (`UploadOrigin`), `core/ports/resource_file_port.py`, `core/apply_engine/resource_files.py`, `core/apply_engine/managed_files/ports.py`, console `file_router`, OpenAPI resources router, legacy resources router; tests for each filesystem, `test_device_port_forwarding.py`, `managed_files/test_store_ports.py`, resource route tests
- **Done when:**
  - [ ] `DeviceFileSystem.write_file(..., origin)`, `list_uploads`, `clear_uploads`; BaaS and local-BaaS call the new routes and map 501 to `None`; local pathlib mode keeps an in-process ledger; teclaw answers `None`.
  - [ ] `ResourceFileService.upload_file(..., origin: UploadOrigin)` with every caller passing one (`USER` on console/OpenAPI, `MANIFEST` from the engine); `list_uploads`/`clear_uploads` forwarded.
  - [ ] `ResourceFilePort` gains the two members; `DeviceResource` forwards; `PlatformResource` lists and deletes the resources category.
- **Depends on:** Task 3 (paths), Task 8 (route names)

### Task 10: Resources materialiser converges by clear then write
- **Goal:** Close #2162.
- **Files:** `core/apply_engine/materialisers/resources.py`; `tests/community/core/apply_engine/test_resources_materialiser.py`, `_fakes.py` (`FakeResourceFileService` ledger)
- **Done when:**
  - [ ] `plan` classifies from `list_uploads(MANIFEST)` when available (no per-member `exists`), reports `removals = listed − declared + declared trees`; with `None` it keeps today's probes and adds the transport note.
  - [ ] `write` clears first, then deletes declared trees, then uploads with `origin=MANIFEST`; a failed clear fails the category with nothing written; a failed upload after the clear reports per entry as today.
  - [ ] A path declared by the previous document and omitted now is gone after the apply; a file the bot wrote itself is untouched; a user upload is untouched; one test each.
  - [ ] Module docstring rewritten: the "cross-entry removals are v1-empty" paragraph replaced by the clear-then-write rule and the transport fallback.
- **Depends on:** Task 9

## Group 4 — Genome: produce, observe drift, drop the delivery records

### Task 11: The genome producer and `compile` on the model
- **Goal:** A revision renders into the same `DesiredState`; the contract examples prove equivalence.
- **Files:** `core/bot_genome/produce.py` (new), `compile.py` (deleted), `registry.py`, `genome_registry_protocol.py`, `models.py` (`CompiledManifest` deleted), `promotion/applier.py`, `di/modules/{bot_genome_module,apply_engine_module}.py`; `api-contracts/genome/v1/examples/manifest-v1/*/compiled.yaml` (deleted), `manifest-v1-compatibility.md`, `README.md` §7; tests `bot_genome/test_produce.py` (replaces `test_compile.py`), `contracts/test_bot_genome_schema.py`, `bot_config_manifest/producer/test_equivalence.py`
- **Done when:**
  - [ ] `produce_state` covers every spec member per plan § Genome producer; `GenomeStateProducer` resolves a promotion id to its revision and refuses a missing or finished promotion.
  - [ ] `GenomeRegistryProtocol.compile(bot, rev) -> DesiredState`; `RevisionApplier.preflight/start` take it; `PendingDeliveryApplier` still refuses everything; the genome producer is registered under `genome` with no production caller.
  - [ ] Equivalence holds for every manifest-v1 case with a revision; the contract test no longer reads `compiled.yaml`; the compatibility doc describes the equivalence.
- **Depends on:** Task 7

### Task 12: Drift observed; the promotion without delivery records
- **Goal:** Drift by reading the bot; remove `applied_revision`, `by_apply_reference`, `apply_reference`, `document_*`.
- **Files:** `core/bot_genome/{registry,genome_registry_protocol,models,errors}.py`, `promotion/{service,applier}.py`, `repository/models.py`, `sql/2026_10_19_bot_genome.sql`, `core/repository/{protocols,implementations}/bot/genome.py`, `adapters/http/openapi_v1/bots/{genome,schemas_genome}.py`, `errors_genome.py`, `di/modules/bot_genome_module.py`; tests `test_registry_service.py`, `test_promotion_service.py`, `service_fixtures.py`, `test_genome_repository.py`, `test_genome_routes.py`, `endpoints/test_openapi_genome.py`, `test_domain_error_status_map_complete.py`
- **Done when:**
  - [ ] `drift(bot)` answers `None` without `active`, `None` when the observed state equals `active` under its policy, `Drift(active, observed)` otherwise, and propagates `CaptureRefused`; nothing is recorded; tests with the capture `FakeReader`.
  - [ ] Promotion checks drift for non-user actors only and raises `Drifted(active, observed)`; the 409 body carries both.
  - [ ] The three columns and the index are gone from DDL, ORM and row; the DDL/ORM pin test passes; `finish(status, apply_detail)`.
  - [ ] `PromotionPayload` has no `document_digest`, `apply_reference`, `document`; OpenAPI snapshot updated; the registry has no `applies` dependency and the genome boundary README no longer names the apply service.
- **Depends on:** Task 11

## Group 5 — Docs and closure

### Task 13: Docs, ADR, work items, context map
- **Goal:** Every document says what the code does.
- **Files:** `docs/bot-evolution/{01-genome,08-promotion,06-evolution-run,work-items}.md` (+ zh-CN), `docs/adr/0022-apply-engine-desired-state-and-observed-drift.md` (+ zh-CN), `CONTEXT-MAP.md`, `core/{apply_engine,bot_config_manifest,bot_genome}/README.md`, `api-contracts/genome/v1/README.md` §7–8
- **Done when:**
  - [ ] Sections listed in the spec rewritten: the compiled document is the apply engine's desired state; drift is observed; no apply-id join, no document digest on reports or promotions; RSI-04 scope reduced to binding, Center/repo skills, service-bot publish, singlebox.
  - [ ] The ADR records: typed desired state as the engine's only input with producers; drift observed, not tracked; supersedes the digest-mapping paragraphs of ADR 0019/0020 where they conflict (link, do not edit them).
  - [ ] `work-items.md` RSI-03 "Left to RSI-04" and RSI-04 updated; #2162 and #2862 rows in the contract README § Known limitations closed.
- **Depends on:** Tasks 10, 12

### Task 14: Final gates and PR
- **Goal:** Green, within limits, reviewable.
- **Done when:**
  - [ ] `ruff check` and `ruff format --line-length 120 --check` clean on every new file; no touched file over 1,000 lines; no formatting churn in untouched lines.
  - [ ] `DEPLOY_PROFILE=test pytest tests/community` and `./scripts/ci_test.sh` pass; the engine adapter `ci_test.sh` passes.
  - [ ] PR body per template: Problem / Solution / Validation (pass counts before and after, equivalence cases, engine adapter suite) / Compatibility and risk (clear-then-write window, pre-existing uploads, skills receipt format, DDL edited in place) / Related issues (#2162, #2862, #3064, RSI-04).
- **Depends on:** Task 13
