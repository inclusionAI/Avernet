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

## Task 3: Register the materialiser and move `cli_tools` to `PRE_CONTAINER`
- **Goal:** Wire the materialiser into both delivery strategies and re-phase the step so it needs no container.
- **Files:** `.../apply/order.py`, `.../apply/registry.py`, `.../apply/delivery.py`, `.../apply/materialisers/__init__.py`, the DI module that builds the strategies
- **Done when:**
  - [ ] `APPLY_ORDER`'s `CLI_TOOLS` row is `PRE_CONTAINER`, keeping position 6.
  - [ ] `MaterialiserPorts` gains `cli_tool_store`, both strategies bind it to the same store, and `as_kwargs` carries it.
  - [ ] `build_materialisers` constructs `CliToolsMaterialiser`; the registry test pinning every key to an `APPLY_ORDER` row still passes.
  - [ ] The stale "`cli_tools` arrives with W9" comments in `registry.py:218` and `materialisers/__init__.py:10` are removed.
  - [ ] The orchestrator, the order table and the materialisers still name no engine — the existing "orchestrator stays generic" and "no materialiser names an engine" tests pass unedited.
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
  - [ ] A test pins `cli_tools` as `PRE_CONTAINER` on both families, added as a new row rather than by editing an existing assertion.
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

## Task 8: Render the ARCA boot-chain prologue
- **Goal:** Produce the shell that stages tools, sets the executable bit and exports `PATH`.
- **Files:** `src/agentclaw/community/core/bot_config_manifest/cli_tools_prologue.py` (new)
- **Done when:**
  - [ ] `render_prologue([])` returns `""`.
  - [ ] The rendered shell implements the same four contract behaviours as teclaw: placement, `md5` change test (a match skips the download), executable bit, `PATH` — plus full replacement of the tools directory.
  - [ ] Each tool is fetched to a `.part` file, `md5`-verified, `chmod +x`'d and moved into place atomically.
  - [ ] A failed fetch or `md5` mismatch prints the tool's name to stderr and does not abort the rest.
  - [ ] The rendered string is asserted directly; no container is needed to test it.
- **Depends on:** Task 3

## Task 9: Wire the prologue into the ARCA deploy path
- **Goal:** Resolve the prologue per payload and prepend it to the boot chain.
- **Files:** `core/service_bot/services/deploy/deploy_models.py`, `core/service_bot/services/baas_service.py`
- **Done when:**
  - [ ] `BotDeployContext` gains `cli_tools_prologue: str = ""` with the comment stating the byte-identical guarantee.
  - [ ] `_resolve_cli_tools_prologue` reads the tool index and signs one short-lived GET per tool, mirroring `_resolve_startup_script`; it is re-read on every payload, so create, restart and republish all pick up the current set.
  - [ ] `_compose_start_command` prepends the prologue **before** the chain, so the engine and everything it spawns inherit `PATH`.
  - [ ] `__OCB_RC` still comes from the chain: a tool that failed to stage never fails the boot.
  - [ ] The signed-URL expiry is sized to the boot window, not left at the plugin default.
- **Depends on:** Task 8

## Task 10: ARCA deploy tests
- **Goal:** Guard the invariants that a regression here would break silently.
- **Files:** `tests/community/core/service_bot/test_cli_tools_prologue.py` (new)
- **Done when:**
  - [ ] A bot with no tools composes a **byte-identical** start command; #935's existing assertion is kept and not edited.
  - [ ] The prologue precedes the chain.
  - [ ] A matching `md5` skips the download; an undeclared file in the tools directory is removed.
  - [ ] A prologue failure does not change the boot exit status.
  - [ ] The hook carrying signed URLs stays elided in BaaS logs.
- **Depends on:** Task 9

## Task 11: Verify the tools directory per deploy runtime (A2)
- **Goal:** Confirm the chosen `TOOLS_DIR` is writable and readable by the user the chain starts the engine as, on each ARCA runtime.
- **Files:** `docs/bot-config-manifest/engine-requirements.zh-CN.md`
- **Done when:**
  - [ ] The directory is confirmed against the ACK and managed runtimes' mounts (`managed_composer.py:428`, `deploy_models.py:167`).
  - [ ] A2 is updated from an open confirmation to what shipped: one verification per image, not one design per engine.
  - [ ] Any runtime that does not fit is recorded with what it would need, rather than silently assumed to work.
- **Depends on:** Task 9

## Task 12: Documentation and work-item reconciliation
- **Goal:** State the limits and the timing rule in the places a user and the teclaw owner read, and correct what is now stale.
- **Files:** `docs/bot-config-manifest/manifest-schema.zh-CN.md`, `.../user-manual.zh-CN.md`, `.../teclaw-cli-contract.zh-CN.md`, `.../work-items.md`, `.../work-items.zh-CN.md`
- **Done when:**
  - [ ] Schema §3.7 states plainly that a delivered tool is one self-contained executable file, and that a tool needing an in-package helper or a sibling `lib/` must be built as a static binary.
  - [ ] The user manual gains a `cli_tools` section: the two source forms, the mandatory `digest`, the per-family timing rule, and that teaching the model to *use* a tool is the owner's job.
  - [ ] The §2.6 exception is written down: on ARCA a `PUT` is delivered now and effective at the next provisioning; on teclaw it is immediate.
  - [ ] The `cli_tools` rows are removed from the "not yet open" gate tables in schema §7 and work-items §5 W1.
  - [ ] `teclaw-cli-contract.zh-CN.md` is reconciled: the `schema_version` 4 → 5 claim and the `entrypoints` / "engine receives an unpacked directory" language are corrected to the shipped one-entry-one-file shape.
  - [ ] The W9 entry in both work-items files reflects what shipped, and the two stale rows are fixed (`${BOT_ARCH}` already landed with W1; the artifact field already landed with #1734).
- **Depends on:** Task 11

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
  - Theme: One desired state, one convergence rule. The `cli_tools` category exists in the store, the materialiser converges it, and it is registered as a pre-container step on both families. Nothing is user-reachable yet.
- **Group B — Admission:** Tasks 4, 5
  - Theme: The category becomes acceptable at `PUT`, with the pipeline's behaviour pinned — including the two silent-wrong-file failure modes.
- **Group C — teclaw arm:** Tasks 6, 7
  - Theme: The artifact carries `cli_tools` refs, and a bot without tools still composes byte-identical output.
- **Group D — ARCA arm:** Tasks 8, 9, 10, 11
  - Theme: The boot-chain prologue implements the same contract the platform wrote for teclaw, wired into the deploy path and verified per runtime.
- **Group E — Docs and verification:** Tasks 12, 13
  - Theme: The limits and the timing exception are written down where they are read, the stale docs are reconciled, and the spec's criteria are checked off.
