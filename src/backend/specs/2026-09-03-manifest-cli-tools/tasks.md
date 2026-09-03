# Tasks: `cli_tools` — Platform-Managed Command-Line Tools (W9)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Spec: `spec.md` · Plan: `plan.md` · Work item W9, issue #1477. Revision 7.

## Task 1: Add the `ac_bot_cli_tool` table, record and repository  `[x]`
- **Goal:** Give the platform its own record of what a bot has installed.
- **Files:** `core/bot_config_manifest/cli_tools/models.py` (new), `core/repository/{protocols,implementations}/bot/cli_tool.py` (new), `core/schema.py`
- **Done when:**
  - [x] The ORM model and the pydantic record exist with the columns `plan.md` names, including `installed_by` and `md5`.
  - [x] `UniqueConstraint(env, bot_id, name)` makes a duplicate command name unwritable, not merely invalid.
  - [x] The `env` column and `register_avernet_tenant_guard` are present, matching `ac_bot_startup_script`.
  - [x] The side-effect import is registered in `core/schema.py` so local `create_all` emits the table.
  - [x] Protocol and implementation are split under `core/repository/…/bot/`, protocol declared as a base class (§8).
  - [x] `oss_key` records where the platform kept the bytes.
  - [x] **No column holds a container path** — the engine owns placement, the row identifies a tool by `name`.
  - [x] The repository covers: upsert by `(bot_id, name)`, delete by name, list by bot, delete-all by bot.
- **Depends on:** —

## Task 2: Build the OSS tool store  `[x]`
- **Goal:** Keep the platform's own copy of every tool's bytes.
- **Files:** `core/bot_config_manifest/cli_tools/store.py` (new), `plugin_api/object_storage.py`, `plugins/{community/object_storage,local/oss_storage}.py`
- **Done when:**
  - [x] `put` writes the bytes under a per-bot key and returns the key recorded on the row.
  - [x] `copy_to_stage` performs a **server-side copy** to a stage-scoped prefix, the layout `TeclawFilePromotion` already builds — through the new optional `ObjectCopyCapability`, read-through where a store lacks it, since an overlay that has not shipped `copy_object` must still promote a bot.
  - [x] `delete` removes an object, and is called whenever a row is removed. It and `copy_to_stage` address the **recorded** `oss_key`, so a row written under an earlier store base is still removable and promotable.
  - [x] The module docstring states why the copy exists: a teclaw artifact composed for a live update or a manifest apply has to reference the tool now, and gathering from the engine then would be circular.

- **Depends on:** —

## Task 3: Define the delivery port and build the ARCA implementation  `[x]`
- **Goal:** A name-addressed delivery protocol, and the ARCA side of it.
- **Files:** `core/bot_config_manifest/cli_tools/{context,delivery_port,arca_port}.py` (new)
- **Done when:**
  - [x] `CliToolDeliveryPort` declares `install` / `delete` / `list` / `replace_all`, and **every signature takes a name, never a path** — a test asserts it.
  - [x] There is **no `get`** — the platform holds the bytes, so nothing reads them back out of a container; a test asserts its absence.
  - [x] `replace_all` has a default implementation that loops — removals first, so a name in both lists is a replacement rather than a deletion of what was just placed.
  - [x] `ArcaCliToolPort.install` is **one call to the engine's install endpoint**, which owns placement, the executable bit and exposure to the agent.
  - [x] A non-2xx **raises** with the engine's error, so a tool the engine could not install is never recorded — and so does a 200 whose envelope reports failure.
  - [x] **The platform issues no `chmod` and runs no shell command** — a test asserts the port's *code* (comments and strings stripped) invokes no shell channel.
  - [x] The tools directory appears nowhere in platform code; it is the engine's, recorded only in `engine-requirements.zh-CN.md`.
- **Depends on:** —

## Task 4: Build the teclaw delivery port  `[x]`
- **Goal:** The artifact is the delivery; no engine upload call.
- **Files:** `core/bot_config_manifest/cli_tools/teclaw_port.py` (new)
- **Done when:**
  - [x] `install` / `delete` do not call the engine: the row-and-store write stands, and the next compose carries the new set, the way `mcp` is delivered.
  - [x] A test asserts **no engine upload call is made** on teclaw — the port takes no collaborator at all.
  - [x] `list` is present and **refuses**, with `CliToolDriftUnobservableError`: the artifact is composed from the table, so returning the table back would be a tautology and returning `[]` would read as "no tools" — which is what a removal set is computed from.
  - [x] Nothing in the module composes or parses a container path.
- **Depends on:** Task 3

## Task 5: Build `CliToolService`  `[x]`
- **Goal:** The one component that installs, removes, lists and replaces.
- **Files:** `core/bot_config_manifest/cli_tools/{service,verify,declarations}.py` (new), `.../README.md` (new), `apply/entry_fetch.py` (the `FetchContext` seam)
- **Done when:**
  - [x] `install` runs fetch → `sha256` → unpack → `select_subpath` → `verify_amd64_elf` → `md5` → **OSS store** → deliver → record, in that order, recording nothing for a step that failed — and discarding the object it stored when delivery then failed, since that key is derived rather than recorded.
  - [x] `select_subpath` refuses an absent member, a non-regular file, or one escaping the tree after symlink resolution.
  - [x] `verify_amd64_elf` refuses a non-ELF file and a wrong `e_machine`, naming what was found, reading `e_machine` in the endianness `EI_DATA` declares.
  - [x] `replace_all` makes the installed set equal the declaration, computing removals **from the table**, and returns a per-tool outcome list.
  - [x] `remove` deletes the OSS object with the row.
  - [x] `list` and `drift` behave as `plan.md` describes; `drift` compares the table against the family's `list`, and reports `observable=False` rather than "converged" on a family that cannot be asked.
  - [x] Fetching goes through `EntryFetcher` under the `cli_tools` category and its existing 200 MiB width. The funnel's ctx parameter is now the declared `FetchContext` protocol, which `CliToolContext` satisfies — so an HTTP-driven install and a manifest apply fetch through the same code rather than two copies.
  - [x] Nothing in the service branches on engine type, and nothing in it composes a filesystem path.
- **Depends on:** Tasks 2, 3, 4

## Task 6: Service tests  `[x]`
- **Goal:** Pin the pipeline and the failure modes that would otherwise be silent.
- **Files:** `tests/community/core/bot_config_manifest/cli_tools/{test_service,test_verify}.py` (new)
- **Done when:**
  - [x] Every case named in `plan.md`'s service test strategy passes.
  - [x] `nothing_is_recorded_when_placement_fails` and the engine-error case both hold.
  - [x] `replace_all_computes_removals_from_the_table_not_the_engine` holds — and asserts the engine's listing was never even consulted.
- **Depends on:** Task 5

## Task 7: The management API  `[x]`
- **Goal:** A surface that delegates to the service and implements nothing itself.
- **Files:** `api/bot_cli_tool_service.py` (new), `core/bot_config_manifest/cli_tools/{service_protocol,bot_service}.py` (new), `adapters/http/openapi_v1/bots/{cli_tools,schemas_cli_tools}.py` (new), `admission.py`, `authorization.py`, `contracts.py`, `responses.py`
- **Done when:**
  - [x] `POST` / `GET` / `DELETE /openapi/v1/bots/{bot_id}/cli-tools` exist, each with its own `ADMISSION` line, mounted before the `{bot_id}` wildcard group.
  - [x] Collaborator-scoped like the config-manifest group: MEMBER to read, ADMIN to write.
  - [x] The `api/` contract is registered in the consistency `_PAIRS`; `core` never imports that layer.
  - [x] A declaration without `digest` is refused; a duplicate name answers 409.
  - [x] The routes contain no fetch, verification or placement logic, and no response exposes a container path.
  - [x] A `BotCliToolService` resolves the bot (which is also the ownership guard), re-asks the capability answer, picks the family and delegates — so the route resolves no storage coordinates itself.
- **Depends on:** Task 5

## Task 8: API endpoint tests  `[x]`
- **Goal:** Prove the surface and its authorization.
- **Files:** `tests/community/endpoints/test_openapi_cli_tools.py` (new)
- **Done when:**
  - [x] The cases named in `plan.md`'s endpoint test strategy pass, plus the two engine refusals (desktop, and a bot with no tools listing empty rather than 404).
  - [x] `every_route_has_an_admission_line` holds, along with the surface's pinned operation counts and its schema-documentation gate.
- **Depends on:** Task 7

## Task 9: Assert CLI tools are absent from the resources surface  `[x]`
- **Goal:** Pin the property, given that nothing was built to make it true.
- **Files:** `tests/community/core/resources/test_cli_tools_absent_from_listings.py` (new)
- **Done when:**
  - [x] Tests assert the mechanism the isolation rests on: the resources surface only ever addresses `workspace/`, and no CLI tool is addressed by a path at all.
  - [x] A test asserts **no resources file was modified** by this feature — no filter, no hidden-name entry, no namespace change — by asserting those files mention CLI tools nowhere.
  - [x] `core/config_compose/teclaw_paths.py` is untouched, and the test says so.
  - [x] A test records why `_HIDDEN_DIRNAMES` was *not* used: it guards the root listing only, so it would have been a filter with a hole rather than a property.
- **Depends on:** Tasks 3, 7

## Task 10: The materialiser, registration and the capability unlock  `[x]`
- **Goal:** Make manifest apply a caller of the service, and let the category be accepted.
- **Files:** `core/bot_config_manifest/apply/materialisers/cli_tools.py` (new), `.../apply/{registry,delivery}.py`, `.../capabilities.py`, `.../apply/materialisers/__init__.py`
- **Done when:**
  - [x] The materialiser's `write` is one `CliToolService.replace_all` call; it adds no fetch, verification or placement of its own.
  - [x] `plan` reads the table: matching `(digest, subpath)` plans `unchanged`, rows the declaration no longer names plan removals; `version` never affects convergence.
  - [x] `MaterialiserPorts` gains `cli_tool_service`, bound per strategy with the family's engine port already inside it.
  - [x] `ManifestCategory.CLI_TOOLS` maps to `None` in `blocked` and `_REASON_CLI_TOOLS` is deleted; desktop and unknown-engine refusals still win.
  - [x] **`order.py` is not modified** — it carries the ARCA reading, `ON_CONTAINER`.
  - [x] `TeclawDelivery.phase_of` gains a `cli_tools` branch returning `PRE_CONTAINER` **under either switch position**, because a teclaw creation has no phase B and the artifact is composed before provisioning.
  - [x] `cli_tools` is **always platform-managed and never consults `teclaw_platform_managed`** for ownership, exactly as `mcp` does not.
  - [x] The stale "`cli_tools` arrives with W9" comments in `registry.py:218` and `materialisers/__init__.py:10` are removed.
  - [x] W13 creation provisions tools through the same service call — it runs the same materialiser through the same registry — and `CliToolService.remove_all` is the cleanup entry point a failed creation calls.
  - [x] The existing "orchestrator stays generic" and "no materialiser names an engine" tests pass unedited.
- **Depends on:** Task 5

## Task 11: Materialiser and apply tests  `[x]`
- **Goal:** Pin delegation, convergence and the two-caller equivalence.
- **Files:** `tests/community/core/bot_config_manifest/apply/test_cli_tools_materialiser.py` (new), `tests/.../test_iteration1_ordering.py`, `tests/.../test_capabilities.py`
- **Done when:**
  - [x] The cases named in `plan.md`'s materialiser test strategy pass, including `the_api_and_apply_refuse_the_same_hostile_declaration`.
  - [x] Tests pin `cli_tools` as `ON_CONTAINER` on ARCA and `PRE_CONTAINER` on teclaw under **both** switch positions, added as new rows; the one existing assertion that had to change is `teclaw_off_is_the_pre_w8_shape`, because that shape genuinely changed.
  - [x] A test pins that `order.py` still carries the ARCA reading, so the per-family rule cannot silently re-phase ARCA.
  - [x] Capabilities report `cli_tools` supported on ARCA and teclaw, unsupported on desktop and unknown engines.
  - [x] A test pins that a teclaw creation installs its declared tools **before** it composes — under either switch position, which is what makes them present in the first artifact.
- **Depends on:** Task 10

## Task 12: Record how the agent finds a tool in v1  `[x]`
- **Goal:** Write down the v1 answer and its cost, rather than leaving a false promise in the schema.
- **Files:** `docs/bot-config-manifest/manifest-schema.zh-CN.md`, `docs/bot-config-manifest/engine-requirements.zh-CN.md`
- **Done when:**
  - [x] Schema §3.7's claim that the platform guarantees the tool is on the agent's `PATH` and that the user never sees a physical path is **corrected**: in v1 the agent is told where tools live by a default-skillset skill, and invokes them by absolute path.
  - [x] The cost is stated plainly: `mycli --help` does not work, every invocation depends on the skill being read, and a script shelling out to a sibling tool will not find it.
  - [x] It is recorded that adding the directory to `PATH` later is an **engine-side** change requiring no change to the schema, the API, the table or the artifact contract — which is what makes deferring it safe.
  - [x] A2 is updated to what shipped: placement and exposure are the engine's, not a platform-side answer negotiated per image.
  - [x] The proposed ARCA directory (`/home/admin/.openclaw/cli`) is recorded as the engine's constant, with the note that each ARCA engine has its own and the default skill set is already per-engine.
- **Depends on:** Task 3

## Task 13: Promote tool objects and carry them in the artifact  `[x]`
- **Goal:** Make service-bot promotion (draft→verify, verify→online) bring a bot's tools with it, without touching the engine.
- **Files:** `core/service_bot/services/deploy/teclaw_file_promotion.py`, `core/config_compose/{protocols,models}.py`, `services/config_composer.py`
- **Done when:**
  - [x] At a promotion boundary the backend iterates the **metadata table** and performs a **server-side copy** of each tool object to the new stage-scoped prefix.
  - [x] **Nothing is downloaded from the engine** — a test asserts the engine is never called during promotion.
  - [x] Draft and verify snapshots do not share objects.
  - [x] `CollectedCliTool` exists and the composed artifact carries `{name, store, path, md5, version}` per `cliToolRef`, with `md5` and `version` read from the table rather than re-hashed.
  - [x] `ownership.cli_tools` is `platform` on **every** compose, as `mcp` is.
  - [x] `BotConfigArtifact` is built with `cli_tools=cli_tools or None`, so a bot with no tools omits the key and composes byte-identical output to today's.
  - [x] `SCHEMA_VERSION` stays 4 and the existing drift test passes untouched.
  - [x] The composer's `_ownership` docstring loses its "nothing composes a `cli_tools` list yet" paragraph.
- **Depends on:** Tasks 2, 5

## Task 14: Promotion and compose-side tests  `[x]`
- **Goal:** Prove the gather, the stage isolation and the no-tools invariant.
- **Files:** `tests/community/core/service_bot/test_teclaw_cli_tool_promotion.py` (new), `tests/community/core/config_compose/test_cli_tools_refs.py` (new)
- **Done when:**
  - [x] The promotion cases named in `plan.md` pass, including `promotion_never_calls_the_engine_for_a_tool`.
  - [x] The artifact carries refs with the platform-computed `md5`, and a test proves it is the *table's* md5 rather than a re-hash of the object.
  - [x] A bot with no tools omits `cli_tools` and is byte-identical to today's artifact.
  - [x] `ownership.cli_tools` is `platform` on every compose.
- **Depends on:** Task 13

## Task 15: Documentation and work-item reconciliation  `[x]`
- **Goal:** State the limits and the new surface where they are read, and correct what is stale.
- **Files:** `docs/bot-config-manifest/{manifest-schema,user-manual,teclaw-cli-contract}.zh-CN.md`, `docs/bot-config-manifest/work-items{,.zh-CN}.md`
- **Done when:**
  - [x] Schema §3.7 states that a delivered tool is one self-contained executable file, and that a tool needing an in-package helper must be built as a static binary.
  - [x] The user manual gains a `cli_tools` section (§5.6, promoted out of "not yet open"): the two source forms, the mandatory `digest`, the management API, that a `PUT` takes effect immediately on both families (**no §2.6 exception**), and **how the agent finds a tool in v1** (the skill, and the absolute-path cost).
  - [x] The manual states that CLI tools are platform-managed and are not workspace files — they do not appear in the file/resources surface, and are managed only through the manifest or the CLI-tools API.
  - [x] The `cli_tools` rows are removed from the gate tables in schema §7 and work-items §5 W1.
  - [x] `teclaw-cli-contract.zh-CN.md` needed **no** reconciliation: it already states `schema_version` stays 4 and already carries the flattened one-entry-one-file shape with no `entrypoints`. The stale `4 → 5` claims were in `manifest-schema.zh-CN.md`, and are corrected there.
  - [x] The W9 entry in both work-items files reflects what shipped, including the one part that is **not** the platform's to finish: the ARCA engine's three endpoints.
- **Depends on:** Tasks 12, 14

## Task 16: Tests & Verification  `[x]`
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** the whole feature
- **Done when:**
  - [x] Every acceptance criterion in `spec.md` checks off, with two annotated as `[~]` rather than silently ticked: the deploy-path scope (one file more than predicted) and the edited assertions below.
  - [x] `pytest tests/community/core/bot_config_manifest tests/community/core/config_compose tests/community/core/resources tests/community/endpoints tests/community/kernel tests/community/architecture tests/community/adapters tests/community/contracts` is green.
  - [!] Two pre-existing failures in this sandbox are environmental, not this change: `test_bot_build_service_skill_artifact.py`'s two pool-build cases fail on `exec: rsync: not found`.
  - [~] Test edits, and why each was unavoidable: every `build_materialisers` / `MaterialiserPorts` / apply-service construction site gains the new port (a required dependency); `test_build_materialisers_registers_five` becomes `…_six`; `materialised_constructs` gains `cli_tools`; the surface's pinned operation counts gain three; `test_teclaw_off_is_the_pre_w8_shape` excludes `cli_tools` from ON_CONTAINER, because that shape genuinely changed; and the capability/schema cases that asserted `cli_tools` is refused now assert it is accepted — which is the gate flip this work item *is*. The one test that moved subject rather than changing, `a_category_with_no_materialiser_fails_and_writes_nothing`, moved from `cli_tools` to `engine_config`, as it moved from `skills` at W5 and `resources` at W6.
  - [x] No `core/skill_center/*` file, no `teclaw_paths.py` change and no resources file is modified — a test asserts the last of those by content.
  - [x] **One deploy-path file beyond `teclaw_file_promotion.py`**: `publish_flow/provider_behavior.py`, which is where the promotion's refs are merged into the artifact. The criterion said "no file beyond", and that was wrong rather than violated — the promotion produces refs and something has to merge them, and this is the same seventeen lines away where the resources and identity merge already lives.
  - [x] `engine_config` is still unsupported with its existing reason.
- **Depends on:** Task 15

---

## Groups

- **Group A — The record:** Task 1
  - Theme: The platform gets its own table for what a bot has installed. Nothing user-reachable yet.
- **Group B — Bytes and delivery:** Tasks 2, 3, 4
  - Theme: The platform's own copy of the bytes, plus both delivery ports behind one name-addressed protocol — one engine `install` call on ARCA, the artifact itself on teclaw.
- **Group C — The service:** Tasks 5, 6
  - Theme: The one component that fetches, verifies, records, delegates and replaces, with its failure modes pinned.
- **Group D — The API:** Tasks 7, 8, 9
  - Theme: A management surface that delegates, plus the assertion that tools stay out of the resources surface — a property, since nothing was built to enforce it.
- **Group E — Manifest apply:** Tasks 10, 11
  - Theme: Apply becomes a second caller of the same service, with full-override semantics, always platform-managed regardless of the switch.
- **Group F — Promotion and the artifact:** Tasks 13, 14
  - Theme: Service-bot promotion gathers tools from the engine into stage-scoped OSS, and the artifact's refs point at them.
- **Group G — Docs and verification:** Tasks 12, 15, 16
  - Theme: Record how the agent finds a tool in v1 and correct §3.7's `PATH` promise, write down the limits, and check off the spec.
