# Plan: `cli_tools` — Declarative Command-Line Tools for Both Engine Families (W9)

Spec: `spec.md` in this directory. Work item W9, issue #1477.

## Approach

`cli_tools` is `resources` with an executable bit. One materialiser fetches,
enforces the declared `sha256`, unpacks, selects the one `subpath` file,
verifies its ELF header, and writes it through a port that a delivery strategy
binds — the device chain on ARCA, the managed-files store on teclaw, exactly
the split W6 established. On ARCA the write is followed by a `chmod +x` through
`execute_baas_shell_command`, the channel `baas_container_init` already uses.
On teclaw the store keeps a tool index so the composer can emit
`{store, path, md5}` refs. No engine change, no deploy-path change, no new
runtime protocol.

> **Revision 3** (PR #1870 review, second round). Rev 1 delivered the ARCA arm
> as a start-command prologue; rev 2 as a `cli_tools` domain on
> `EngineRuntimeProjection`. Both withdrawn. That projection seam carries
> platform state a runtime must be *told about* — activation rows, allow-lists.
> A tool has none of that. And rev 2's premise was wrong: the platform already
> runs commands in live ARCA containers, so the executable bit costs no new
> channel and no engine endpoint.

## Affected Components

- `core/bot_config_manifest/capabilities.py` — unlock `cli_tools`.
- `core/bot_config_manifest/apply/materialisers/cli_tools.py` — **new**, the
  fetch → verify → select → write pipeline.
- `core/bot_config_manifest/apply/cli_tool_port.py` — **new**, the narrow write
  port, bound per strategy.
- `core/bot_config_manifest/apply/delivery.py` — bind the port per family;
  `order.py` and the orchestrator are **not** touched.
- `core/bot_config_manifest/apply/registry.py` — register the materialiser.
- `core/bot_config_manifest/managed_files/` — the `cli_tools` category and the
  tool index (teclaw side only).
- `core/config_compose/{protocols,models}.py`, `services/config_composer.py`,
  `managed_files/reader.py` — the teclaw arm: `cli_tools` refs.
- `docs/bot-config-manifest/*` — schema, user manual, teclaw contract, A2.

**Not touched:** `service_bot/services/baas_service.py` and
`services/deploy/*` (rev 1 changed them); `core/skill_center/*` and the runtime
projection (rev 2 changed them).

## Data Model Changes

None. No table, no migration. The desired state lives in the object store
alongside the W8 managed files.

## Store layout

Tools take a new namespace under the existing per-bot `rel_root`, plus one
index object that the other categories do not need:

```text
# bytes for one tool
{base}/staff_u1/bot7_manifest/teclaw/tools/mycli
# the index: md5 / version / convergence key for every tool
{base}/staff_u1/bot7_manifest/teclaw/tools/_index.json
```

```jsonc
// tools/_index.json — the whole desired state for one bot
{
  "tools": [
    { "name": "mycli", "rel_path": "tools/mycli", "md5": "9f2c…",
      "version": "1.4.2", "digest": "sha256:3e7a…", "subpath": null },
    { "name": "tk", "rel_path": "tools/tk", "md5": "1a04…",
      "version": "0.9.0", "digest": "sha256:88bd…", "subpath": "bin/tk" }
  ]
}
```

**Store-side only.** The store is teclaw's half; on ARCA the platform holds no
copy and converges by replacing (spec D-4), so nothing below applies there.

**Why an index, when `store.py` says "the key layout *is* the record".** The
other three categories need no per-file metadata beyond the path, so their
layout carries everything. `cli_tools` needs three things a path cannot hold:
`md5` (the artifact field), `version`, and the convergence key
`digest` + `subpath`. The alternative is reading every tool's bytes back on
every compose to recompute `md5` — up to 200 MiB per tool per artifact. The
index is a deliberate, argued exception; `store.py`'s module docstring gets a
paragraph saying so.

```diff
# core/bot_config_manifest/managed_files/store.py:70-79
+#: Where a delivered CLI tool lives under its own namespace.
+TOOLS_NS = "tools"
+#: The per-bot tool index. Not a file the engine ever sees.
+TOOLS_INDEX = f"{TOOLS_NS}/_index.json"
 CATEGORY_IDENTITY = "identity"
 CATEGORY_RESOURCES = "resources"
 CATEGORY_SKILLS = "skills"
+CATEGORY_CLI_TOOLS = "cli_tools"
```

```diff
# core/bot_config_manifest/managed_files/store.py:140 — category_of
+    if rel_path.startswith(_TOOLS_PREFIX) and rel_path != TOOLS_INDEX:
+        return CATEGORY_CLI_TOOLS if "/" not in rel_path[len(_TOOLS_PREFIX):] else None
     if rel_path.startswith(_WORKSPACE_PREFIX) and len(rel_path) > len(_WORKSPACE_PREFIX):
         return CATEGORY_RESOURCES
```

The index is excluded from `category_of`, so `store.list(scope,
category=CATEGORY_CLI_TOOLS)` returns tools only and never the index — the
existing `put` guard ("a path that would read back as another category is
refused") keeps holding.

Two new methods, because the index is read and written as a unit:

```python
# core/bot_config_manifest/managed_files/store.py (new)
def read_tool_index(self, scope: ManagedFileScope) -> list[ToolRecord]: ...
def write_tool_index(self, scope: ManagedFileScope, tools: Sequence[ToolRecord]) -> None: ...
```

```python
# core/bot_config_manifest/managed_files/store.py (new)
@dataclass(frozen=True)
class ToolRecord:
    name: str            # the command, and the file name under tools/
    rel_path: str        # "tools/<name>"
    md5: str             # platform-computed, over the selected file
    version: str | None
    digest: str          # the declared sha256 over the fetched source object
    subpath: str | None  # the archive member selected, or None
```

`ENGINE_LAYOUT_SEGMENT` keeps its value `"teclaw"` — changing it would orphan
every object W8 wrote — but its comment claiming "ARCA writes into a live
container and holds no platform copy" becomes false with this change and is
corrected in place.

## The materialiser

```python
# core/bot_config_manifest/apply/materialisers/cli_tools.py (new)
class CliToolsMaterialiser(Materialiser):
    construct = ManifestCategory.CLI_TOOLS

    def __init__(self, store: ManifestCliToolStore, entry_fetcher: EntryFetcher) -> None: ...

    async def resolve(self, ctx, entries) -> ResolveResult:
        """Fetch → enforce sha256 → unpack → select subpath → verify ELF → md5.

        Everything that can fail before touching the bot fails here, as the
        Materialiser contract requires. Produces one Intent per entry carrying
        the selected bytes, its md5, and the (digest, subpath) convergence key.
        """

    async def plan(self, ctx, intents) -> CategoryPlan:
        """Read-only. Removals from ``list_tools``; classification per family.

        Where the port answers with a record (teclaw), an intent whose
        ``(digest, subpath)`` matches plans ``unchanged``. Where it does not
        (ARCA holds no copy), every declared tool plans a write — replace,
        don't diff, the rule ``resources`` already states — so the category is
        never ``is_noop`` there.
        """

    async def write(self, ctx, plan) -> Sequence[EntryResult]:
        """put_tool for each write, remove_tool for each removal.

        The port decides what "put" means: device write plus chmod, or store
        put plus index record.
        """
```

The index is rewritten **after** the bytes, so a crash mid-write leaves an index
that under-claims rather than over-claims: the composer never
name a tool whose bytes are absent, and the next apply re-converges.

`resolve` is where the two new checks live:

```python
# core/bot_config_manifest/apply/materialisers/cli_tools.py (new)
ELF_MAGIC = b"\x7fELF"
EM_X86_64 = 0x3E  # e_machine at offset 18, little-endian

def verify_amd64_elf(data: bytes, *, name: str) -> None:
    """Refuse a non-ELF file or one built for another architecture.

    ``digest`` answers "are these the bytes you asked for"; this answers "can
    this machine run them", and a *wrong* binary still has a valid digest.
    """
```

```python
# core/bot_config_manifest/apply/materialisers/cli_tools.py (new)
def select_subpath(tree: UnpackedTree, subpath: str, *, location: str) -> Path:
    """The one declared file inside an unpacked archive.

    Content-dependent validation (spec): must exist, must be a regular file,
    and must still resolve inside the tree after symlink resolution. W1 keeps
    the syntactic half — no absolute paths, no ``..``, unique ``name``.
    """
```

Fetch reuses the existing funnel and the width already declared for this
category (`fetch/limits.py:53`, 200 MiB):

```python
fetched = self._fetch.fetch(
    ctx, source_url=url, digest=entry["digest"], category="cli_tools",
    entry_identity=entry["name"],
)
```

## Registration and the port

`order.py` is **not** modified: `APPLY_ORDER` already carries
`ApplyStep(ManifestCategory.CLI_TOOLS, ApplyPhase.ON_CONTAINER, 6)`, and
`TeclawDelivery.phase_of` already re-phases every non-script construct to
`PRE_CONTAINER` under the platform-managed switch — so the per-family phase
falls out of existing generic code.

```python
# core/bot_config_manifest/apply/cli_tool_port.py (new)
@runtime_checkable
class ManifestCliToolPort(Protocol):
    """Where a delivered tool goes, and how it is made executable.

    The ``resource_port.py`` move: the ARCA strategy binds the device-backed
    implementation (write through the resource chain, then chmod), the teclaw
    strategy the store-backed one (put bytes, record the index). The
    materialiser does not know which it was handed.
    """

    async def put_tool(
        self, *, bot_id: str, owner_id: str, engine_type: str,
        name: str, content: bytes, md5: str, version: str | None,
        digest: str, subpath: str | None,
    ) -> None: ...

    async def list_tools(self, *, bot_id: str, owner_id: str, engine_type: str) -> list[ToolRecord]: ...

    async def remove_tool(self, *, bot_id: str, owner_id: str, engine_type: str, name: str) -> None: ...
```

```diff
# core/bot_config_manifest/apply/delivery.py:92 — MaterialiserPorts
     entry_fetcher: EntryFetcher
     resource_service: ManifestResourcePort
+    cli_tool_service: ManifestCliToolPort
```

```diff
# core/bot_config_manifest/apply/registry.py:238 — build_materialisers
         ResourcesMaterialiser(resource_service, entry_fetcher),
+        CliToolsMaterialiser(cli_tool_service, entry_fetcher),
     )
```

## Capability unlock

```diff
# core/bot_config_manifest/capabilities.py:284
-        ManifestCategory.CLI_TOOLS: _REASON_CLI_TOOLS,
+        ManifestCategory.CLI_TOOLS: None,
```

`_REASON_CLI_TOOLS` is deleted with it. Desktop and unknown-engine refusals
still win, unchanged, through `capability()`'s existing precedence.

## teclaw arm

The composer already emits `ownership.cli_tools` (`config_composer.py:198-205`)
— only the list is missing.

```diff
# core/config_compose/protocols.py:50 — ManagedFilesReader
     def skill_files(self, req: ComposeRequest, names: Collection[str]) -> list[CollectedFile]: ...
+    def cli_tools(self, req: ComposeRequest) -> list[CollectedCliTool]: ...
```

```python
# core/config_compose/models.py (new)
@dataclass(frozen=True)
class CollectedCliTool:
    name: str
    store: str
    path: str      # store-relative, naming the one executable file
    md5: str
    version: str | None
```

```python
# core/bot_config_manifest/managed_files/reader.py (new method)
def cli_tools(self, req: ComposeRequest) -> list[CollectedCliTool]:
    """The index, as refs. Read from the index, not from a listing — the
    listing has no md5 and recomputing it would re-read every tool."""
```

```diff
# core/config_compose/services/config_composer.py:167 — BotConfigArtifact(
             ownership=self._ownership(req),
+            cli_tools=cli_tools or None,
```

`or None` preserves the omission rule the artifact contract already states
(`artifact.py:304`): a bot with no tools produces byte-identical output to
today's, and the existing "`to_dict` omits `cli_tools` when unset" test keeps
passing. `SCHEMA_VERSION` is untouched.

## ARCA arm — device write plus `chmod +x`

The device-backed port writes through the chain `resources` uses, then sets the
bit through the helper that already exists.

```python
# core/bot_config_manifest/apply/cli_tool_device_port.py (new)
class DeviceCliToolPort(ManifestCliToolPort):
    """Write into the live container, then make it executable.

    ``put_tool`` is two steps and both must succeed: the file write through
    ``ResourceFileService`` (the one write chain), then ``chmod +x`` through
    ``execute_baas_shell_command`` — the helper ``baas_container_init`` and
    ``baas_codefuse_writer`` already use, which returns a ``CommandResult``
    carrying an exit code and stderr.

    ``list_tools`` returns names only: the platform holds no copy on ARCA, so
    there is no md5 or digest to answer with, and the materialiser converges by
    replacing (spec D-4). The list exists to compute removals.
    """
```

```python
# the chmod, through the existing helper
result = execute_baas_shell_command(
    baas_service=..., device=..., shell_cmd=f"chmod +x {shlex.quote(tool_path)}",
    timeout_seconds=30,
)
if result.exit_code != 0:
    raise CliToolChmodError(result.stderr)   # fails this entry in the report
```

`shlex.quote` is not decoration: `name` reaches a shell here. It is already
constrained by W1 (no path separators, unique per bot), and quoting is the
second line rather than the first.

**The tools directory** is a module-level constant, resolved once, and is the
subject of the spec's one open question — whether it must be a directory the
engine's `PATH` already includes, or whether container-init adds one.

## API / Interface Changes

No HTTP surface change, and **no new engine-facing contract**. The only
visible difference is that a document declaring `cli_tools` is accepted and
capabilities reports it supported:

```jsonc
// GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities → 200 (excerpt)
{ "kind": "category", "name": "cli_tools", "supported": true, "reason": "" }
```

teclaw's artifact gains the list its contract already defines:

```jsonc
// BotConfigArtifact (teclaw, platform-managed) — excerpt
{ "cli_tools": [ { "name": "mycli", "store": "bot-data",
                   "path": "staff_u1/bot7_manifest/teclaw/tools/mycli",
                   "md5": "9f2c…", "version": "1.4.2" } ],
  "ownership": { "cli_tools": "platform", ... } }
```

## Dependencies

None. `hashlib` for the md5. The engine-side endpoint is an external
dependency on each ARCA engine team, tracked as out-of-scope in the spec, not a
package.

## Risks & Mitigations

- **Risk:** the tools directory is not on the agent process's `PATH`, so the
  file is delivered and executable but the model cannot invoke it by name.
  **Mitigation:** the spec's one open question, confirmed per deploy runtime
  before Group D lands. It is a lookup, not a design: either an already-on-PATH
  directory, or one line in container-init.
- **Risk:** `name` reaches a shell in the `chmod`.
  **Mitigation:** `shlex.quote`, plus W1's existing constraint that a tool name
  carries no path separators and is unique per bot. A test passes a name with
  shell metacharacters.
- **Risk:** the file lands but the `chmod` fails, leaving a non-executable
  binary the model hits as "permission denied".
  **Mitigation:** a non-zero exit fails that entry in the apply report with the
  command's stderr. Delivery is the write *and* the bit, never just the write.
- **Risk:** ARCA rewrites every tool on every apply, costing a re-upload of up
  to 200 MiB per tool.
  **Mitigation:** accepted, and inherited rather than invented — `resources`
  made the same trade for the same reason (a drifted container would survive a
  source-side comparison). The first place to look if apply latency outruns the
  lock TTL, and recorded as such.
- **Risk:** the teclaw index departs from the store's "layout is the record"
  rule and becomes a precedent.
  **Mitigation:** the reason is written into `store.py`'s docstring — three
  fields that cannot live in a path — so the next category has to make the same
  argument rather than inherit the exception.

## Alternatives Considered

- **A `cli_tools` domain on `EngineRuntimeProjection`** (rev 2). Withdrawn in
  review: that seam carries platform state a runtime must be told about —
  activation rows, allow-lists, a reconcile — and a tool has none of it. It
  would also have charged every ARCA engine an endpoint for a `chmod` the
  platform can already perform.
- **A platform-composed start-command prologue** (rev 1). Withdrawn: an
  arrangement rather than a protocol, and effective only at the next
  provisioning.
- **Extending `DeviceFileSystem` with `chmod`.** Rejected as unnecessary: the
  exec channel already exists a layer up (`exec_command_on_bot`), so adding a
  mode to the file protocol would mean a second way to do the same thing and a
  change to the BaaS upload endpoint.
- **A mode parameter on `write_file`.** Same objection, and it would touch
  every transport including teclaw's, where the engine sets the bit anyway.
- **No index; recompute `md5` at compose time.** Rejected: re-reads every
  tool's bytes (≤200 MiB each) on every artifact compose.

## Rollout

No flag of its own. The teclaw arm rides W8's existing
`user_config.bot_config_manifest.teclaw_platform_managed`, still default off.
The ARCA arm needs no switch: no bot has a `cli_tools` declaration until
someone writes one, because the category was refused at `PUT` until now.

```bash
# no migration; the teclaw tool index is the only new state
uv run pytest tests/community/core/bot_config_manifest \
              tests/community/core/config_compose \
              tests/community/kernel/test_bot_config_artifact.py
```

## Test Strategy

```python
# tests/community/core/bot_config_manifest/apply/test_cli_tools_materialiser.py (new)
def test_single_binary_enforces_declared_sha256(): ...
def test_archive_selects_only_the_declared_subpath(): ...
def test_subpath_must_be_a_regular_file_inside_the_tree_after_symlinks(): ...
def test_non_amd64_elf_fails_the_entry_with_the_architecture_found(): ...
def test_non_elf_file_fails_the_entry(): ...
def test_same_digest_different_subpath_is_a_change_not_unchanged(): ...
def test_unchanged_tool_writes_nothing(): ...
def test_undeclared_tool_is_removed_and_empty_list_removes_all(): ...
def test_index_is_written_after_the_bytes(): ...
```

```python
# tests/community/core/bot_config_manifest/test_capabilities.py (extend)
def test_cli_tools_supported_on_arca_and_teclaw(): ...
def test_cli_tools_still_unsupported_on_desktop_and_unknown_engine(): ...
```

```python
# tests/community/core/config_compose/test_cli_tools_refs.py (new)
def test_artifact_carries_cli_tools_refs_with_platform_md5(): ...
def test_bot_without_tools_omits_cli_tools_and_is_byte_identical(): ...
def test_schema_version_stays_4(): ...
```

```python
# tests/community/core/bot_config_manifest/apply/test_cli_tool_ports.py (new)
def test_device_port_writes_through_the_resource_chain(): ...
def test_device_port_chmods_after_the_write(): ...
def test_failed_chmod_fails_the_entry_with_stderr(): ...
def test_tool_name_with_shell_metacharacters_is_quoted(): ...
def test_device_port_holds_no_copy_so_every_apply_rewrites(): ...
def test_store_port_records_the_index_and_plans_unchanged(): ...
```

```python
# tests/community/core/bot_config_manifest/test_iteration1_ordering.py (extend)
def test_cli_tools_is_on_container_on_arca_and_pre_container_on_teclaw(): ...
```

Also pinned: no deploy-path file and no `skill_center` file changes — the
composed start command is byte-identical for every bot (#935's assertion stands
unedited) and the runtime projection is untouched.

Manual: one confirmation per deploy runtime that the tools directory is on the
agent's `PATH` — the spec's open question, and the only thing here a test
cannot answer.
