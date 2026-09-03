# Tasks: `cli_tools` — Declarative Command-Line Tools for Both Engine Families (W9)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Spec: `spec.md` · Plan: `plan.md` · Work item W9, issue #1477.

## Task 1: Add the `cli_tools` category and tool index to the managed-files store
- **Goal:** Give the store a `tools/` namespace, a `cli_tools` category, and the per-bot index object the other categories do not need.
- **Files:** `src/agentclaw/community/core/bot_config_manifest/managed_files/store.py`, `.../managed_files/__init__.py`
- **Done when:**
  - [ ] `TOOLS_NS`, `TOOLS_INDEX`, `CATEGORY_CLI_TOOLS` and the `ToolRecord` dataclass exist and are exported.
  - [ ] `category_of` returns `cli_tools` for `tools/<name>`, `None` for the index and for anything nested below `tools/`, and is unchanged for the other three categories.
  - [ ] `store.list(scope, category=CATEGORY_CLI_TOOLS)` returns tools only, never the index.
  - [ ] `read_tool_index` / `write_tool_index` round-trip a list of `ToolRecord`; a missing index reads as `[]`.
  - [ ] `put` still refuses a path that would read back as another category.
  - [ ] The module docstring gains the paragraph arguing why `cli_tools` gets an index when the layout is otherwise the record, and `ENGINE_LAYOUT_SEGMENT`'s "ARCA holds no platform copy" comment is corrected.
  - [ ] `purge` removes the index along with the tools.
- **Depends on:** —

## Task 2: Build the `cli_tools` materialiser
- **Goal:** Fetch, enforce `sha256`, unpack, select `subpath`, verify the ELF header, compute `md5`, and converge the store.
- **Files:** `src/agentclaw/community/core/bot_config_manifest/apply/materialisers/cli_tools.py` (new)
- **Done when:**
  - [ ] `resolve` fetches through `EntryFetcher` under the `cli_tools` category and its existing 200 MiB width, and enforces the declared `sha256` over the fetched source object (the binary, or the whole archive).
  - [ ] For an archive entry, the tree is unpacked under W2's guards and `subpath` selects exactly one member; no other member is delivered.
  - [ ] `select_subpath` refuses a `subpath` that is absent, is not a regular file, or escapes the tree after symlink resolution.
  - [ ] `verify_amd64_elf` refuses a non-ELF file and one whose `e_machine` is not x86-64, failing that entry with the architecture found and the one expected.
  - [ ] `md5` is computed over the finally selected file, never read from a store ETag.
  - [ ] `plan` is read-only and classifies `unchanged` on `(digest, subpath)` equality — so the same archive with a changed `subpath` is a change, not a no-op.
  - [ ] `plan` computes removals for names in the index that the declaration no longer carries; an empty declared list removes every tool.
  - [ ] `write` puts changed tools, deletes removals, and rewrites the index **last**; an `is_noop` plan performs no write.
  - [ ] `version` reaches the report and the index and never affects convergence.
- **Depends on:** Task 1

## Task 3: Register the materialiser and add its two ports
- **Goal:** Wire the materialiser into both delivery strategies, with a store port and a projection port.
- **Files:** `.../apply/registry.py`, `.../apply/delivery.py`, `.../apply/cli_tools_port.py` (new), `.../apply/materialisers/__init__.py`, the DI module that builds the strategies
- **Done when:**
  - [ ] `CliToolProjectionPort` exists, named the way `ActivationPort` names activation's methods.
  - [ ] `MaterialiserPorts` gains `cli_tool_store` and `cli_tool_projection`; `as_kwargs` carries both.
  - [ ] Both strategies bind the same store; ARCA binds the real projector and the platform-managed teclaw path binds the record-only stand-in, because the artifact is teclaw's projection.
  - [ ] `build_materialisers` constructs `CliToolsMaterialiser`; the registry test pinning every key to an `APPLY_ORDER` row still passes.
  - [ ] **`order.py` is not modified** — `cli_tools` is already `ON_CONTAINER` at position 6, which is where a live projection belongs.
  - [ ] The stale "`cli_tools` arrives with W9" comments in `registry.py:218` and `materialisers/__init__.py:10` are removed.
  - [ ] The existing "orchestrator stays generic" and "no materialiser names an engine" tests pass unedited.
- **Depends on:** Task 2

## Task 4: Unlock the capability
- **Goal:** Let a document declaring `cli_tools` be accepted, on ARCA and teclaw.
- **Files:** `.../capabilities.py`, `tests/community/core/bot_config_manifest/test_capabilities.py`
- **Done when:**
  - [ ] `ManifestCategory.CLI_TOOLS` maps to `None` in `blocked`, and `_REASON_CLI_TOOLS` is deleted.
  - [ ] `GET …/config-manifest/capabilities` reports `cli_tools` supported on ARCA and teclaw, and still unsupported on desktop and unknown engines with the existing reasons.
  - [ ] A `PUT` declaring `cli_tools` is accepted; one omitting `digest` on a non-git form is still refused.
- **Depends on:** Task 3

## Task 5: Materialiser and admission tests
- **Goal:** Pin the pipeline's behaviour, including the two failure modes that are easy to get silently wrong.
- **Files:** `tests/community/core/bot_config_manifest/apply/test_cli_tools_materialiser.py` (new), `tests/.../test_iteration1_ordering.py`
- **Done when:**
  - [ ] The nine cases named in `plan.md`'s test strategy for the materialiser pass — notably `same digest + different subpath is a change` and `index is written after the bytes`.
  - [ ] A test pins `cli_tools` as `ON_CONTAINER` on both families, added as a new row rather than by editing an existing assertion.
  - [ ] Non-ELF and wrong-architecture inputs each fail their entry with a message naming what was found.
- **Depends on:** Task 4

## Task 6: Carry `cli_tools` in the teclaw artifact
- **Goal:** Populate the artifact's `cli_tools` list from the index; `ownership.cli_tools` already exists.
- **Files:** `core/config_compose/protocols.py`, `core/config_compose/models.py`, `core/config_compose/services/config_composer.py`, `.../managed_files/reader.py`
- **Done when:**
  - [ ] `CollectedCliTool` exists and `ManagedFilesReader` declares `cli_tools(req)`.
  - [ ] `ManagedFilesComposeReader.cli_tools` reads the **index** (not a listing, which has no `md5`).
  - [ ] `BotConfigArtifact` is built with `cli_tools=cli_tools or None`, so a bot with no tools omits the key and composes byte-identical output to today's.
  - [ ] Each entry is `{name, store, path, md5, version}` naming the one executable file, per `cliToolRef`.
  - [ ] `SCHEMA_VERSION` stays 4 and the existing drift test passes untouched.
  - [ ] The composer's `_ownership` docstring loses its "nothing composes a `cli_tools` list yet" paragraph.
- **Depends on:** Task 5

## Task 7: Compose-side tests
- **Goal:** Prove the teclaw arm and the no-tools invariant.
- **Files:** `tests/community/core/config_compose/test_cli_tools_refs.py` (new)
- **Done when:**
  - [ ] The artifact carries refs with the platform-computed `md5`.
  - [ ] A bot with no tools omits `cli_tools` and is byte-identical to today's artifact.
  - [ ] `ownership.cli_tools` follows the operation, as W8 established.
- **Depends on:** Task 6

## Task 8: Add the `cli_tools` domain to the runtime projection
- **Goal:** Make tools a third projection domain beside skills and MCP, on the seam that already spans the ARCA family.
- **Files:** `core/skill_center/runtime_projection_contract.py`, `core/skill_center/services/runtime_projections/per_domain.py`
- **Done when:**
  - [ ] `ProjectionScope` gains its tools half, declared by the mutation rather than inferred.
  - [ ] `PerDomainRuntimeProjection` calls the engine's tools endpoint with the declared set, sending the **`cliToolRef` shape verbatim** — the same refs teclaw gets from the artifact.
  - [ ] `validate_plan` refuses a `cli_tools` plan an engine has no contract for **before any runtime request is emitted**.
  - [ ] A tools-only scope does not force a skills or MCP rewrite.
  - [ ] An empty declared list projects removal of every platform-delivered tool.
  - [ ] `WholeArtifactRuntimeProjection` needs no `cli_tools` branch; a test says so.
- **Depends on:** Task 3

## Task 9: Report honestly for an engine without the endpoint
- **Goal:** Make "this engine cannot do tools yet" a first-class, visible outcome rather than silence.
- **Files:** `core/skill_center/services/runtime_projections/per_domain.py`, `.../apply/materialisers/cli_tools.py`
- **Done when:**
  - [ ] An engine with no tools endpoint yields `SKIPPED` — or `DEGRADED` when other domains converged — with a `RuntimeProjectionIssue` naming the engine and the missing endpoint, and `retryable` set honestly.
  - [ ] The materialiser surfaces that outcome in the apply report per entry, using the existing status vocabulary; no new report shape.
  - [ ] A `cli_tools` apply on such an engine **never reports success**.
  - [ ] The store is still converged in that case, so the tools are delivered the moment the engine gains the endpoint.
- **Depends on:** Task 8

## Task 10: Projection tests
- **Goal:** Pin the protocol's behaviour without needing a real engine.
- **Files:** `tests/community/core/skill_center/test_cli_tools_projection.py` (new)
- **Done when:**
  - [ ] The five cases named in `plan.md`'s test strategy for the projection pass.
  - [ ] A test pins that **no deploy-path file changed**: the composed start command is byte-identical for every bot and #935's assertion is unedited.
  - [ ] The `SKIPPED` path is asserted end to end, from the projection through to the apply report entry.
- **Depends on:** Task 9

## Task 11: Write the ARCA-facing CLI protocol document
- **Goal:** Give the ARCA engine owners the spec they implement against, as `teclaw-cli-contract.zh-CN.md` does for teclaw.
- **Files:** `docs/bot-config-manifest/arca-cli-contract.zh-CN.md` (new), `docs/bot-config-manifest/engine-requirements.zh-CN.md`
- **Done when:**
  - [ ] The document states the ref shape, the four behaviours (placement, `md5` change test, executable bit, `PATH`), full-replacement semantics, and the projection result an engine returns.
  - [ ] It states what the platform guarantees: one self-contained executable per entry, `sha256` already enforced, architecture already verified.
  - [ ] It includes the removal and empty-list cases, and the idempotent redelivery case.
  - [ ] `engine-requirements.zh-CN.md` is updated: the "zero changes" line now carries the `cli_tools` exception, and **A2 is closed** — the PATH injection point is the engine's internal decision under this protocol, not a platform-side answer.
- **Depends on:** Task 8

## Task 12: Documentation and work-item reconciliation
- **Goal:** State the limits and the timing rule in the places a user and the teclaw owner read, and correct what is now stale.
- **Files:** `docs/bot-config-manifest/manifest-schema.zh-CN.md`, `.../user-manual.zh-CN.md`, `.../teclaw-cli-contract.zh-CN.md`, `.../work-items.md`, `.../work-items.zh-CN.md`
- **Done when:**
  - [ ] Schema §3.7 states plainly that a delivered tool is one self-contained executable file, and that a tool needing an in-package helper or a sibling `lib/` must be built as a static binary.
  - [ ] The user manual gains a `cli_tools` section: the two source forms, the mandatory `digest`, the per-family timing rule, and that teaching the model to *use* a tool is the owner's job.
  - [ ] The user manual states that `cli_tools` takes effect immediately on both families, like every other category — **no §2.6 exception**.
  - [ ] The `cli_tools` rows are removed from the "not yet open" gate tables in schema §7 and work-items §5 W1.
  - [ ] `teclaw-cli-contract.zh-CN.md` is reconciled: the `schema_version` 4 → 5 claim and the `entrypoints` / "engine receives an unpacked directory" language are corrected to the shipped one-entry-one-file shape.
  - [ ] The W9 entry in both work-items files reflects what shipped, and the two stale rows are fixed (`${BOT_ARCH}` already landed with W1; the artifact field already landed with #1734).
- **Depends on:** Tasks 10, 11

## Task 13: Tests & Verification
- **Goal:** Ensure the feature meets the spec's acceptance criteria.
- **Files:** the whole feature
- **Done when:**
  - [ ] Every acceptance criterion in `spec.md` checks off.
  - [ ] `uv run pytest tests/community/core/bot_config_manifest tests/community/core/config_compose tests/community/core/service_bot tests/community/kernel/test_bot_config_artifact.py` is green.
  - [ ] No existing manifest, artifact, creation or deploy test has an edited assertion.
  - [ ] `engine_config` is still unsupported with its existing reason, and no other category changed behaviour.
- **Depends on:** Task 12

---

## Groups

- **Group A — Store and materialiser:** Tasks 1, 2, 3
  - Theme: One desired state, one convergence rule. The `cli_tools` category exists in the store, the materialiser converges it, and it is registered with its two ports. Nothing is user-reachable yet.
- **Group B — Admission:** Tasks 4, 5
  - Theme: The category becomes acceptable at `PUT`, with the pipeline's behaviour pinned — including the two silent-wrong-file failure modes.
- **Group C — teclaw arm:** Tasks 6, 7
  - Theme: The artifact carries `cli_tools` refs, and a bot without tools still composes byte-identical output.
- **Group D — The engine protocol:** Tasks 8, 9, 10, 11
  - Theme: Tools become a third projection domain on the seam that already spans the engines, an engine without the endpoint reports `SKIPPED` rather than silence, and the ARCA engine owners get the contract they implement against.
- **Group E — Docs and verification:** Tasks 12, 13
  - Theme: The limits are written down where they are read, the stale docs are reconciled, and the spec's criteria are checked off.
