# Tasks: `cli_tools` — Platform-Managed Command-Line Tools (W9)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Spec: `spec.md` · Plan: `plan.md` · Work item W9, issue #1477. Revision 5.

## Task 1: Add the `ac_bot_cli_tool` table, record and repository
- **Goal:** Give the platform its own record of what a bot has installed.
- **Files:** `core/bot_config_manifest/cli_tools/models.py` (new), `core/repository/{protocols,implementations}/bot/cli_tool.py` (new), `core/schema.py`
- **Done when:**
  - [ ] The ORM model and the pydantic record exist with the columns `plan.md` names, including `installed_by` and `md5`.
  - [ ] `UniqueConstraint(env, bot_id, name)` makes a duplicate command name unwritable, not merely invalid.
  - [ ] The `env` column and `register_avernet_tenant_guard` are present, matching `ac_bot_startup_script`.
  - [ ] The side-effect import is registered in `core/schema.py` so local `create_all` emits the table.
  - [ ] Protocol and implementation are split under `core/repository/…/bot/`, protocol declared as a base class (§8).
  - [ ] **No column holds a container path** — the engine owns placement, the row identifies a tool by `name`.
  - [ ] The repository covers: upsert by `(bot_id, name)`, delete by name, list by bot, delete-all by bot.
- **Depends on:** —

## Task 2: Define the engine port and build the ARCA implementation
- **Goal:** A name-addressed protocol, and the ARCA side of it.
- **Files:** `core/bot_config_manifest/cli_tools/engine_port.py` (new), `.../arca_port.py` (new)
- **Done when:**
  - [ ] `CliToolEnginePort` declares `install` / `delete` / `list` / `get` / `replace_all`, and **every signature takes a name, never a path** — a test asserts it.
  - [ ] `get` returns a tool's bytes by name; it exists for the teclaw promotion gather.
  - [ ] `replace_all` has a default implementation that loops, so an engine that cannot batch still works.
  - [ ] `ArcaCliToolPort.install` writes through the existing device file chain, then `chmod 0755` via `execute_baas_shell_command`.
  - [ ] A non-zero `chmod` exit **raises** with the command's stderr, so the tool is never recorded as installed.
  - [ ] The directory is a constant private to the ARCA port — proposed `/home/admin/.openclaw/cli` — appearing in no signature, table column or API response.
  - [ ] The tool name is `shlex.quote`d into the command, and a test passes a hostile name.
- **Depends on:** —

## Task 3: Build the teclaw port
- **Goal:** The same name-addressed protocol against the teclaw engine's CLI endpoints.
- **Files:** `core/bot_config_manifest/cli_tools/teclaw_port.py` (new)
- **Done when:**
  - [ ] `install` / `delete` / `list` / `get` call the teclaw engine's CLI endpoints by name.
  - [ ] Nothing in the module composes or parses a container path — teclaw's layout is not ours to know.
  - [ ] `get` is exercised by a test, since promotion depends on it.
- **Depends on:** Task 2

## Task 4: Build `CliToolService`
- **Goal:** The one component that installs, removes, lists and replaces.
- **Files:** `core/bot_config_manifest/cli_tools/service.py` (new), `.../verify.py` (new), `.../README.md` (new, with a Context Boundary block per §8)
- **Done when:**
  - [ ] `install` runs fetch → `sha256` → unpack → `select_subpath` → `verify_amd64_elf` → `md5` → `engine.install` → record, in that order, recording nothing for a step that failed.
  - [ ] `select_subpath` refuses an absent member, a non-regular file, or one escaping the tree after symlink resolution.
  - [ ] `verify_amd64_elf` refuses a non-ELF file and a wrong `e_machine`, naming what was found.
  - [ ] `replace_all` makes the installed set equal the declaration, computing removals **from the table**, and returns a per-tool outcome list.
  - [ ] `remove`, `list` and `drift` behave as `plan.md` describes; `drift` compares the table against `engine.list`.
  - [ ] Fetching goes through `EntryFetcher` under the `cli_tools` category and its existing 200 MiB width.
  - [ ] Nothing in the service branches on engine type, and nothing in it composes a filesystem path.
- **Depends on:** Tasks 2, 3

## Task 5: Service tests
- **Goal:** Pin the pipeline and the failure modes that would otherwise be silent.
- **Files:** `tests/community/core/bot_config_manifest/cli_tools/test_service.py` (new)
- **Done when:**
  - [ ] The eleven cases named in `plan.md`'s service test strategy pass.
  - [ ] `nothing_is_recorded_when_placement_fails` and `chmod_failure_fails_the_entry_with_stderr` both hold.
  - [ ] `replace_all_computes_removals_from_the_table_not_the_engine` holds.
- **Depends on:** Task 4

## Task 6: The management API
- **Goal:** A surface that delegates to the service and implements nothing itself.
- **Files:** `api/bot_cli_tool_service.py` (new), `adapters/http/openapi_v1/bots/cli_tools.py` (new), `adapters/http/openapi_v1/bots/schemas_cli_tools.py` (new)
- **Done when:**
  - [ ] `POST` / `GET` / `DELETE /openapi/v1/bots/{bot_id}/cli-tools` exist, each with its own `ADMISSION` line, mounted before the `{bot_id}` wildcard group.
  - [ ] Collaborator-scoped like the config-manifest group: MEMBER to read, ADMIN to write.
  - [ ] The `api/` contract is registered in the consistency `_PAIRS`; `core` never imports that layer.
  - [ ] A declaration without `digest` is refused; a duplicate name answers 409.
  - [ ] The routes contain no fetch, verification or placement logic, and no response exposes a container path.
- **Depends on:** Task 4

## Task 7: API endpoint tests
- **Goal:** Prove the surface and its authorization.
- **Files:** `tests/community/endpoints/test_openapi_cli_tools.py` (new)
- **Done when:**
  - [ ] The five cases named in `plan.md`'s endpoint test strategy pass.
  - [ ] `every_route_has_an_admission_line` holds.
- **Depends on:** Task 6

## Task 8: Assert CLI tools are absent from the resources surface
- **Goal:** Pin the property, given that nothing was built to make it true.
- **Files:** `tests/community/core/resources/test_cli_tools_absent_from_listings.py` (new)
- **Done when:**
  - [ ] A test asserts an installed tool never appears in a resources listing for that bot.
  - [ ] A test asserts **no resources file was modified** by this feature — no filter, no hidden-name entry, no namespace change.
  - [ ] `core/config_compose/teclaw_paths.py` is untouched (rev 4 added a namespace; rev 5 does not).
- **Depends on:** Tasks 2, 6

## Task 9: The materialiser, registration and the capability unlock
- **Goal:** Make manifest apply a caller of the service, and let the category be accepted.
- **Files:** `core/bot_config_manifest/apply/materialisers/cli_tools.py` (new), `.../apply/{registry,delivery}.py`, `.../capabilities.py`, `.../apply/materialisers/__init__.py`
- **Done when:**
  - [ ] The materialiser's `write` is one `CliToolService.replace_all` call; it adds no fetch, verification or placement of its own.
  - [ ] `plan` reads the table: matching `(digest, subpath)` plans `unchanged`, rows the declaration no longer names plan removals; `version` never affects convergence.
  - [ ] `MaterialiserPorts` gains `cli_tool_service`, bound per strategy with the family's engine port already inside it.
  - [ ] `ManifestCategory.CLI_TOOLS` maps to `None` in `blocked` and `_REASON_CLI_TOOLS` is deleted; desktop and unknown-engine refusals still win.
  - [ ] **`order.py` is not modified** — `cli_tools` stays `ON_CONTAINER`, and delivery is a live engine call on both families.
  - [ ] `cli_tools` is **always platform-managed and does not consult `teclaw_platform_managed`**, exactly as `mcp` does not; a test pins that the switch changes nothing for this category.
  - [ ] The stale "`cli_tools` arrives with W9" comments in `registry.py:218` and `materialisers/__init__.py:10` are removed.
  - [ ] W13 creation provisions tools through the same service call; a failed creation removes the rows and the installed tools.
  - [ ] The existing "orchestrator stays generic" and "no materialiser names an engine" tests pass unedited.
- **Depends on:** Task 4

## Task 10: Materialiser and apply tests
- **Goal:** Pin delegation, convergence and the two-caller equivalence.
- **Files:** `tests/community/core/bot_config_manifest/apply/test_cli_tools_materialiser.py` (new), `tests/.../test_iteration1_ordering.py`, `tests/.../test_capabilities.py`
- **Done when:**
  - [ ] The six cases named in `plan.md`'s materialiser test strategy pass, including `api_and_apply_refuse_the_same_hostile_declaration`.
  - [ ] A test pins `cli_tools` as `ON_CONTAINER` on both families regardless of the switch, added as a new row rather than by editing an existing assertion.
  - [ ] Capabilities report `cli_tools` supported on ARCA and teclaw, unsupported on desktop and unknown engines.
- **Depends on:** Task 9

## Task 11: Record how the agent finds a tool in v1
- **Goal:** Write down the v1 answer and its cost, rather than leaving a false promise in the schema.
- **Files:** `docs/bot-config-manifest/manifest-schema.zh-CN.md`, `docs/bot-config-manifest/engine-requirements.zh-CN.md`
- **Done when:**
  - [ ] Schema §3.7's claim that the platform guarantees the tool is on the agent's `PATH` and that the user never sees a physical path is **corrected**: in v1 the agent is told where tools live by a default-skillset skill, and invokes them by absolute path.
  - [ ] The cost is stated plainly: `mycli --help` does not work, every invocation depends on the skill being read, and a script shelling out to a sibling tool will not find it.
  - [ ] It is recorded that adding the directory to `PATH` later is an **engine-side** change requiring no change to the schema, the API, the table or the artifact contract — which is what makes deferring it safe.
  - [ ] A2 is updated to what shipped: placement and exposure are the engine's, not a platform-side answer negotiated per image.
  - [ ] The proposed ARCA directory (`/home/admin/.openclaw/cli`) is recorded as the engine's constant, with the note that each ARCA engine has its own and the default skill set is already per-engine.
- **Depends on:** Task 2

## Task 12: Gather tools at promotion and carry them in the artifact
- **Goal:** Make service-bot promotion (draft→verify, verify→online) bring a bot's tools with it.
- **Files:** `core/service_bot/services/deploy/teclaw_file_promotion.py`, `core/config_compose/{protocols,models}.py`, `services/config_composer.py`
- **Done when:**
  - [ ] At a promotion boundary the backend iterates the **metadata table**, calls `engine.get(name=…)` per tool, and writes each to a **stage-scoped OSS key** under the layout `TeclawFilePromotion` already builds.
  - [ ] Draft and verify snapshots do not share objects.
  - [ ] A tool the engine no longer has fails that entry by name, rather than producing an artifact referencing an object never written.
  - [ ] `CollectedCliTool` exists and the composed artifact carries `{name, store, path, md5, version}` per `cliToolRef`, with `md5` and `version` read from the table rather than re-hashed.
  - [ ] `ownership.cli_tools` is `platform` on **every** compose, as `mcp` is — not conditional on the switch.
  - [ ] `BotConfigArtifact` is built with `cli_tools=cli_tools or None`, so a bot with no tools omits the key and composes byte-identical output to today's.
  - [ ] `SCHEMA_VERSION` stays 4 and the existing drift test passes untouched.
  - [ ] The composer's `_ownership` docstring loses its "nothing composes a `cli_tools` list yet" paragraph.
- **Depends on:** Tasks 3, 4

## Task 13: Promotion and compose-side tests
- **Goal:** Prove the gather, the stage isolation and the no-tools invariant.
- **Files:** `tests/community/core/service_bot/test_teclaw_cli_tool_promotion.py` (new), `tests/community/core/config_compose/test_cli_tools_refs.py` (new)
- **Done when:**
  - [ ] The six promotion cases named in `plan.md` pass, including `md5_and_version_come_from_the_table_not_a_rehash`.
  - [ ] The artifact carries refs with the platform-computed `md5`.
  - [ ] A bot with no tools omits `cli_tools` and is byte-identical to today's artifact.
  - [ ] `ownership.cli_tools` is `platform` on every compose.
- **Depends on:** Task 12

## Task 14: Documentation and work-item reconciliation
- **Goal:** State the limits and the new surface where they are read, and correct what is stale.
- **Files:** `docs/bot-config-manifest/{manifest-schema,user-manual,teclaw-cli-contract}.zh-CN.md`, `docs/bot-config-manifest/work-items{,.zh-CN}.md`
- **Done when:**
  - [ ] Schema §3.7 states that a delivered tool is one self-contained executable file, and that a tool needing an in-package helper must be built as a static binary.
  - [ ] The user manual gains a `cli_tools` section: the two source forms, the mandatory `digest`, the management API, that a `PUT` takes effect immediately on both families (**no §2.6 exception**), and **how the agent finds a tool in v1** (the skill, and the absolute-path cost).
  - [ ] The manual states that CLI tools are platform-managed and are not workspace files — they do not appear in the file/resources surface, and are managed only through the manifest or the CLI-tools API.
  - [ ] The `cli_tools` rows are removed from the gate tables in schema §7 and work-items §5 W1.
  - [ ] `teclaw-cli-contract.zh-CN.md` is reconciled: the `schema_version` 4 → 5 claim and the `entrypoints` / "unpacked directory" language are corrected to the shipped one-entry-one-file shape.
  - [ ] The W9 entry in both work-items files reflects what shipped, and the stale rows are fixed (`${BOT_ARCH}` landed with W1; the artifact field landed with #1734).
- **Depends on:** Tasks 11, 13

## Task 15: Tests & Verification
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** the whole feature
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off.
  - [ ] `uv run pytest tests/community/core/bot_config_manifest tests/community/core/config_compose tests/community/core/resources tests/community/endpoints tests/community/kernel/test_bot_config_artifact.py` is green.
  - [ ] No existing manifest, artifact, resources, creation or deploy test has an edited assertion.
  - [ ] No deploy-path file beyond `teclaw_file_promotion.py`, no `core/skill_center/*` file, no `teclaw_paths.py` change and no resources file is modified.
  - [ ] `engine_config` is still unsupported with its existing reason.
- **Depends on:** Task 14

---

## Groups

- **Group A — The record:** Task 1
  - Theme: The platform gets its own table for what a bot has installed. Nothing user-reachable yet.
- **Group B — The name-addressed protocol:** Tasks 2, 3
  - Theme: Both engine ports behind one protocol whose every operation takes a tool name and never a path — device write plus `chmod` on ARCA, the engine's CLI endpoints on teclaw.
- **Group C — The service:** Tasks 4, 5
  - Theme: The one component that fetches, verifies, records, delegates and replaces, with its failure modes pinned.
- **Group D — The API:** Tasks 6, 7, 8
  - Theme: A management surface that delegates, plus the assertion that tools stay out of the resources surface — a property, since nothing was built to enforce it.
- **Group E — Manifest apply:** Tasks 9, 10
  - Theme: Apply becomes a second caller of the same service, with full-override semantics, always platform-managed regardless of the switch.
- **Group F — Promotion and the artifact:** Tasks 12, 13
  - Theme: Service-bot promotion gathers tools from the engine into stage-scoped OSS, and the artifact's refs point at them.
- **Group G — Docs and verification:** Tasks 11, 14, 15
  - Theme: Record how the agent finds a tool in v1 and correct §3.7's `PATH` promise, write down the limits, and check off the spec.
