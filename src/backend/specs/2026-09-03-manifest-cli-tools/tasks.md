# Tasks: `cli_tools` — Declarative Command-Line Tools for Both Engine Families (W9)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

Spec: `spec.md` · Plan: `plan.md` · Work item W9, issue #1477.

## Task 1: Add the `cli_tools` category and tool index to the managed-files store
- **Goal:** Give the store a `tools/` namespace, a `cli_tools` category, and the per-bot index object the other categories do not need. teclaw side only — ARCA holds no copy.
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
  - [ ] `plan` is read-only. Where the port answers with a record (teclaw), `(digest, subpath)` equality plans `unchanged` — so the same archive with a changed `subpath` is a change, not a no-op. Where it does not (ARCA), every declared tool plans a write and the category is never `is_noop` — replace, don't diff, as `resources` does.
  - [ ] `plan` computes removals from `list_tools`; an empty declared list removes every tool.
  - [ ] `write` calls `put_tool` per write and `remove_tool` per removal, and never branches on engine.
  - [ ] `version` reaches the report and the index and never affects convergence.
- **Depends on:** Task 1

## Task 3: Add the tool port and register the materialiser
- **Goal:** One narrow write port, bound per delivery strategy — the `resources` shape.
- **Files:** `.../apply/cli_tool_port.py` (new), `.../apply/registry.py`, `.../apply/delivery.py`, `.../apply/materialisers/__init__.py`, the DI module that builds the strategies
- **Done when:**
  - [ ] `ManifestCliToolPort` declares `put_tool` / `list_tools` / `remove_tool`, named the way `resource_port.py` names the resource chain's methods.
  - [ ] `MaterialiserPorts` gains `cli_tool_service`; `as_kwargs` carries it.
  - [ ] The ARCA strategy binds the device-backed port, the teclaw platform-managed strategy the store-backed one.
  - [ ] `build_materialisers` constructs `CliToolsMaterialiser`; the registry test pinning every key to an `APPLY_ORDER` row still passes.
  - [ ] **`order.py` is not modified** — `cli_tools` is already `ON_CONTAINER`, and `TeclawDelivery.phase_of` already re-phases non-script constructs to `PRE_CONTAINER` under the switch, so the per-family phase needs no category-specific code.
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
  - [ ] A test pins `cli_tools` as `ON_CONTAINER` on ARCA and `PRE_CONTAINER` on teclaw under the switch, added as a new row rather than by editing an existing assertion.
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

## Task 8: Build the device-backed tool port (ARCA)
- **Goal:** Write the tool into the live container through the existing file chain, then make it executable.
- **Files:** `core/bot_config_manifest/apply/cli_tool_device_port.py` (new)
- **Done when:**
  - [ ] `put_tool` writes through `ResourceFileService`'s chain — the same chain `resources` uses — into the platform tools directory.
  - [ ] It then sets the executable bit via `execute_baas_shell_command`, the helper `baas_container_init` and `baas_codefuse_writer` already use. **No new channel, no engine change, no deploy-path change.**
  - [ ] A non-zero exit raises with the command's stderr, so the entry fails in the apply report rather than leaving a non-executable file.
  - [ ] The tool name is `shlex.quote`d before it reaches the shell, on top of W1's no-separator constraint.
  - [ ] `list_tools` returns names only (no md5/digest — the platform holds no copy on ARCA), which is what makes the materialiser plan every tool as a write.
  - [ ] `remove_tool` deletes a tool the declaration no longer names.
- **Depends on:** Task 3

## Task 9: Build the store-backed tool port (teclaw)
- **Goal:** Put bytes and record the index, so the composer can emit refs.
- **Files:** `core/bot_config_manifest/managed_files/ports.py`
- **Done when:**
  - [ ] `put_tool` writes the bytes under the `cli_tools` category and records `md5`, `version`, `digest` and `subpath` in the tool index, bytes before index.
  - [ ] `list_tools` answers from the index, so `(digest, subpath)` equality can plan `unchanged`.
  - [ ] `remove_tool` deletes the object and its index entry.
  - [ ] It sets no executable bit: on teclaw the engine does that on placement, per its contract.
- **Depends on:** Task 3

## Task 10: Port tests
- **Goal:** Pin both ports, including the failure that would silently ship an unusable tool.
- **Files:** `tests/community/core/bot_config_manifest/apply/test_cli_tool_ports.py` (new)
- **Done when:**
  - [ ] The six cases named in `plan.md`'s test strategy for the ports pass — notably `failed chmod fails the entry with stderr` and `tool name with shell metacharacters is quoted`.
  - [ ] A test pins that no deploy-path file and no `skill_center` file changed.
- **Depends on:** Tasks 8, 9

## Task 11: Confirm the tools directory is on the agent's `PATH` (closes A2)
- **Goal:** Answer the spec's one open question, per deploy runtime.
- **Files:** `docs/bot-config-manifest/engine-requirements.zh-CN.md`, and the tools-directory constant
- **Done when:**
  - [ ] The directory is confirmed against the ACK and managed runtimes: either it is already on the agent process's `PATH`, or container-init adds it.
  - [ ] The constant reflects what was confirmed, not what was assumed.
  - [ ] `engine-requirements.zh-CN.md` records that the ARCA family needs **no change** for `cli_tools`, and A2 is closed by naming the directory.
  - [ ] Any runtime that does not fit is recorded with what it would need, rather than silently assumed to work.
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

- **Group A — Materialiser and port shape:** Tasks 1, 2, 3
  - Theme: The pipeline and the seam. The store gains the `cli_tools` category, the materialiser converges through a port it cannot see the engine behind, and it is registered. Nothing user-reachable yet.
- **Group B — Admission:** Tasks 4, 5
  - Theme: The category becomes acceptable at `PUT`, with the pipeline pinned — including the two silent-wrong-file failure modes.
- **Group C — teclaw arm:** Tasks 6, 7
  - Theme: The artifact carries `cli_tools` refs, and a bot without tools still composes byte-identical output.
- **Group D — ARCA arm:** Tasks 8, 9, 10, 11
  - Theme: Both ports built — device write plus `chmod` on ARCA, store plus index on teclaw — and the tools directory confirmed to be on `PATH`, which closes A2.
- **Group E — Docs and verification:** Tasks 12, 13
  - Theme: The limits are written down where they are read, the stale docs are reconciled, and the spec's criteria are checked off.
