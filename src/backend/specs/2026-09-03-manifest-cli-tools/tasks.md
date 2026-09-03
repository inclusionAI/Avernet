# Tasks: `cli_tools` — Platform-Managed Command-Line Tools (W9)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Spec: `spec.md` · Plan: `plan.md` · Work item W9, issue #1477. Revision 4.

## Task 1: Add the `ac_bot_cli_tool` table, record and repository
- **Goal:** Give the platform its own record of what a bot has installed.
- **Files:** `core/bot_config_manifest/cli_tools/models.py` (new), `core/repository/{protocols,implementations}/bot/cli_tool.py` (new), `core/schema.py`
- **Done when:**
  - [ ] The ORM model and the pydantic record exist with the columns `plan.md` names, including `installed_by` and `md5`.
  - [ ] `UniqueConstraint(env, bot_id, name)` makes a duplicate command name unwritable, not merely invalid.
  - [ ] The `env` column and `register_avernet_tenant_guard` are present, matching `ac_bot_startup_script`.
  - [ ] The side-effect import is registered in `core/schema.py` so local `create_all` emits the table.
  - [ ] Protocol and implementation are split under `core/repository/…/bot/`, protocol declared as a base class (§8).
  - [ ] The repository covers: upsert by `(bot_id, name)`, delete by name, list by bot, delete-all by bot.
- **Depends on:** —

## Task 2: Add the `tools/` namespace
- **Goal:** Place tools outside the namespace the resources API is confined to.
- **Files:** `core/config_compose/teclaw_paths.py`
- **Done when:**
  - [ ] `TOOLS_NS` exists, is in `_NAMESPACES`, and `to_engine_relative` accepts it.
  - [ ] The constant's comment states *why* it is a sibling of `workspace/` — structural exclusion, not a filter (spec D-5).
  - [ ] Existing namespace tests pass unedited.
- **Depends on:** —

## Task 3: Define the engine port and build the ARCA implementation
- **Goal:** Place bytes and set the executable bit through channels that already exist.
- **Files:** `core/bot_config_manifest/cli_tools/engine_port.py` (new), `.../arca_port.py` (new)
- **Done when:**
  - [ ] `CliToolEnginePort` declares `upload` / `delete` / `list` / `replace_all`.
  - [ ] `replace_all` has a default implementation that loops, so an engine that cannot batch still works.
  - [ ] `ArcaCliToolPort.upload` writes through the existing device file chain into the `tools/` namespace, then `chmod 0755` via `execute_baas_shell_command`.
  - [ ] A non-zero `chmod` exit **raises** with the command's stderr, so the tool is never recorded as installed.
  - [ ] The tool name is `shlex.quote`d into the command, and a test passes a hostile name.
  - [ ] `list` answers from the engine's own view, for the drift read.
- **Depends on:** Task 2

## Task 4: Build the teclaw port
- **Goal:** Bytes into the managed-files store; the artifact is the delivery.
- **Files:** `core/bot_config_manifest/cli_tools/teclaw_port.py` (new), `core/bot_config_manifest/managed_files/store.py`
- **Done when:**
  - [ ] `upload` puts the object under the bot's tools prefix; `delete` removes it; `list` lists it.
  - [ ] The store gains the `cli_tools` category and the `tools/` prefix, and `category_of` classifies it; `purge` removes tools with the rest.
  - [ ] `ENGINE_LAYOUT_SEGMENT`'s "ARCA holds no platform copy" comment is corrected — the table is now the record for both families.
  - [ ] No `md5` sidecar index is needed: the metadata table holds `md5`, `version`, `digest` and `subpath`.
- **Depends on:** Task 1, Task 2

## Task 5: Build `CliToolService`
- **Goal:** The one component that installs, removes, lists and replaces.
- **Files:** `core/bot_config_manifest/cli_tools/service.py` (new), `.../verify.py` (new), `.../README.md` (new, with a Context Boundary block per §8)
- **Done when:**
  - [ ] `install` runs fetch → `sha256` → unpack → `select_subpath` → `verify_amd64_elf` → `md5` → `engine.upload` → record, in that order, recording nothing for a step that failed.
  - [ ] `select_subpath` refuses an absent member, a non-regular file, or one escaping the tree after symlink resolution.
  - [ ] `verify_amd64_elf` refuses a non-ELF file and a wrong `e_machine`, naming what was found.
  - [ ] `replace_all` makes the installed set equal the declaration, computing removals **from the table**, and returns a per-tool outcome list.
  - [ ] `remove`, `list` and `drift` behave as `plan.md` describes; `drift` compares the table against `engine.list`.
  - [ ] Fetching goes through `EntryFetcher` under the `cli_tools` category and its existing 200 MiB width.
  - [ ] Nothing in the service branches on engine type.
- **Depends on:** Task 3, Task 4

## Task 6: Service tests
- **Goal:** Pin the pipeline and the failure modes that would otherwise be silent.
- **Files:** `tests/community/core/bot_config_manifest/cli_tools/test_service.py` (new)
- **Done when:**
  - [ ] The eleven cases named in `plan.md`'s service test strategy pass.
  - [ ] `nothing_is_recorded_when_placement_fails` and `chmod_failure_fails_the_entry_with_stderr` both hold.
  - [ ] `replace_all_computes_removals_from_the_table_not_the_engine` holds.
- **Depends on:** Task 5

## Task 7: The management API
- **Goal:** A surface that delegates to the service and implements nothing itself.
- **Files:** `api/bot_cli_tool_service.py` (new), `adapters/http/openapi_v1/bots/cli_tools.py` (new), `adapters/http/openapi_v1/bots/schemas_cli_tools.py` (new)
- **Done when:**
  - [ ] `POST` / `GET` / `DELETE /openapi/v1/bots/{bot_id}/cli-tools` exist, each with its own `ADMISSION` line, mounted before the `{bot_id}` wildcard group.
  - [ ] Collaborator-scoped like the config-manifest group: MEMBER to read, ADMIN to write.
  - [ ] The `api/` contract is registered in the consistency `_PAIRS`; `core` never imports that layer.
  - [ ] A declaration without `digest` is refused; a duplicate name answers 409.
  - [ ] The routes contain no fetch, verification or placement logic.
- **Depends on:** Task 5

## Task 8: API endpoint tests
- **Goal:** Prove the surface and its authorization.
- **Files:** `tests/community/endpoints/test_openapi_cli_tools.py` (new)
- **Done when:**
  - [ ] The five cases named in `plan.md`'s endpoint test strategy pass.
  - [ ] `every_route_has_an_admission_line` holds.
- **Depends on:** Task 7

## Task 9: Prove CLI tools are unreachable from the resources API
- **Goal:** Establish the isolation as a property, not an endpoint-by-endpoint filter.
- **Files:** `tests/community/core/resources/test_cli_tools_are_unreachable.py` (new)
- **Done when:**
  - [ ] A test asserts `build_workspace_mapper` raises on a `tools/`-prefixed logical path.
  - [ ] A test asserts no `path` value a resources endpoint accepts can address the tools namespace — `_logical` always prefixes `workspace/`, and `safe_workspace_path` refuses `..`.
  - [ ] A test asserts an installed tool never appears in a resources listing for that bot.
  - [ ] **No resources endpoint is modified** and no filter is added; a test pins that the resources router and service are unchanged.
- **Depends on:** Task 3, Task 7

## Task 10: The materialiser, registration and the capability unlock
- **Goal:** Make manifest apply a caller of the service, and let the category be accepted.
- **Files:** `core/bot_config_manifest/apply/materialisers/cli_tools.py` (new), `.../apply/{registry,delivery}.py`, `.../capabilities.py`, `.../apply/materialisers/__init__.py`
- **Done when:**
  - [ ] The materialiser's `write` is one `CliToolService.replace_all` call; it adds no fetch, verification or placement of its own.
  - [ ] `plan` reads the table: matching `(digest, subpath)` plans `unchanged`, rows the declaration no longer names plan removals; `version` never affects convergence.
  - [ ] `MaterialiserPorts` gains `cli_tool_service`, bound per strategy with the family's engine port already inside it.
  - [ ] `ManifestCategory.CLI_TOOLS` maps to `None` in `blocked` and `_REASON_CLI_TOOLS` is deleted; desktop and unknown-engine refusals still win.
  - [ ] **`order.py` is not modified** — `ON_CONTAINER` on ARCA, and `TeclawDelivery.phase_of` re-phases it to `PRE_CONTAINER` under the switch, generically.
  - [ ] The stale "`cli_tools` arrives with W9" comments in `registry.py:218` and `materialisers/__init__.py:10` are removed.
  - [ ] W13 creation provisions tools through the same service call; a failed creation removes the rows and the placed files.
  - [ ] The existing "orchestrator stays generic" and "no materialiser names an engine" tests pass unedited.
- **Depends on:** Task 5

## Task 11: Materialiser and apply tests
- **Goal:** Pin delegation, convergence and the two-caller equivalence.
- **Files:** `tests/community/core/bot_config_manifest/apply/test_cli_tools_materialiser.py` (new), `tests/.../test_iteration1_ordering.py`, `tests/.../test_capabilities.py`
- **Done when:**
  - [ ] The six cases named in `plan.md`'s materialiser test strategy pass, including `api_and_apply_refuse_the_same_hostile_declaration`.
  - [ ] A test pins `cli_tools` as `ON_CONTAINER` on ARCA and `PRE_CONTAINER` on teclaw under the switch, added as a new row rather than by editing an existing assertion.
  - [ ] Capabilities report `cli_tools` supported on ARCA and teclaw, unsupported on desktop and unknown engines.
- **Depends on:** Task 10

## Task 12: Confirm the `PATH` directory per deploy runtime
- **Goal:** Establish that a placed tool is actually invokable by the model.
- **Files:** `docs/bot-config-manifest/engine-requirements.zh-CN.md`
- **Done when:**
  - [ ] The tools directory is confirmed to be on the agent process's `PATH` for the ACK and managed runtimes — or a directory is named and the requirement recorded for the engine to expose.
  - [ ] A2 is updated to what shipped rather than left as an open confirmation.
  - [ ] Any runtime that does not fit is recorded with what it would need, rather than silently assumed to work.
- **Depends on:** Task 3

## Task 13: Carry `cli_tools` in the teclaw artifact
- **Goal:** Emit the refs; `ownership.cli_tools` already exists.
- **Files:** `core/config_compose/{protocols,models}.py`, `services/config_composer.py`, `core/bot_config_manifest/managed_files/reader.py`
- **Done when:**
  - [ ] `CollectedCliTool` exists and the reader answers from the **metadata table** joined to the store's objects — the table is where `md5` and `version` live.
  - [ ] `BotConfigArtifact` is built with `cli_tools=cli_tools or None`, so a bot with no tools omits the key and composes byte-identical output to today's.
  - [ ] Each entry is `{name, store, path, md5, version}` per `cliToolRef`.
  - [ ] `SCHEMA_VERSION` stays 4 and the existing drift test passes untouched.
  - [ ] The composer's `_ownership` docstring loses its "nothing composes a `cli_tools` list yet" paragraph.
- **Depends on:** Task 4, Task 5

## Task 14: Compose-side tests
- **Goal:** Prove the teclaw arm and the no-tools invariant.
- **Files:** `tests/community/core/config_compose/test_cli_tools_refs.py` (new)
- **Done when:**
  - [ ] The artifact carries refs with the platform-computed `md5` from the table.
  - [ ] A bot with no tools omits `cli_tools` and is byte-identical to today's artifact.
  - [ ] `ownership.cli_tools` follows the operation, as W8 established.
- **Depends on:** Task 13

## Task 15: Documentation and work-item reconciliation
- **Goal:** State the limits and the new surface where they are read, and correct what is stale.
- **Files:** `docs/bot-config-manifest/{manifest-schema,user-manual,teclaw-cli-contract}.zh-CN.md`, `docs/bot-config-manifest/work-items{,.zh-CN}.md`
- **Done when:**
  - [ ] Schema §3.7 states that a delivered tool is one self-contained executable file, and that a tool needing an in-package helper must be built as a static binary.
  - [ ] The user manual gains a `cli_tools` section: the two source forms, the mandatory `digest`, the management API, that a `PUT` takes effect immediately on both families (**no §2.6 exception**), and that teaching the model to *use* a tool is the owner's job.
  - [ ] The manual states that CLI tools are platform-managed and deliberately absent from the file/resources surface, and why.
  - [ ] The `cli_tools` rows are removed from the gate tables in schema §7 and work-items §5 W1.
  - [ ] `teclaw-cli-contract.zh-CN.md` is reconciled: the `schema_version` 4 → 5 claim and the `entrypoints` / "unpacked directory" language are corrected to the shipped one-entry-one-file shape.
  - [ ] The W9 entry in both work-items files reflects what shipped, and the stale rows are fixed (`${BOT_ARCH}` landed with W1; the artifact field landed with #1734).
- **Depends on:** Task 12

## Task 16: Tests & Verification
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** the whole feature
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off.
  - [ ] `uv run pytest tests/community/core/bot_config_manifest tests/community/core/config_compose tests/community/core/resources tests/community/endpoints tests/community/kernel/test_bot_config_artifact.py` is green.
  - [ ] No existing manifest, artifact, resources, creation or deploy test has an edited assertion.
  - [ ] No deploy-path file and no `core/skill_center/*` file is modified.
  - [ ] `engine_config` is still unsupported with its existing reason.
- **Depends on:** Task 15

---

## Groups

- **Group A — Record and namespace:** Tasks 1, 2
  - Theme: The platform gets its own table for what a bot has installed, and tools get a namespace outside the one the resources API can address. Nothing user-reachable yet.
- **Group B — Placement:** Tasks 3, 4
  - Theme: Both engine ports — device write plus `chmod` on ARCA, store plus artifact on teclaw — behind one four-operation protocol with a batch call.
- **Group C — The service:** Tasks 5, 6
  - Theme: The one component that fetches, verifies, places, records and replaces, with its failure modes pinned.
- **Group D — The API and the isolation guarantee:** Tasks 7, 8, 9
  - Theme: A management surface that delegates, and a test that CLI tools are unreachable from the resources API as a property rather than a filter.
- **Group E — Manifest apply:** Tasks 10, 11
  - Theme: Apply becomes a second caller of the same service, with full-override semantics and the per-family phase inherited generically.
- **Group F — teclaw artifact:** Tasks 13, 14
  - Theme: The artifact carries the refs, and a bot without tools composes byte-identical output.
- **Group G — PATH, docs and verification:** Tasks 12, 15, 16
  - Theme: Confirm a placed tool is invokable, write down the limits and the new surface, and check off the spec.
