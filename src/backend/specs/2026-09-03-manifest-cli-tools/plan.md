# Plan: `cli_tools` — Declarative Command-Line Tools for Both Engine Families (W9)

Spec: `spec.md` in this directory. Work item W9, issue #1477.

## Approach

One materialiser converges a single desired state — the managed-files store —
and each engine family consumes it through the projection seam `skills` and
`mcp` already use. The materialiser fetches, enforces the declared `sha256`,
unpacks, selects the one `subpath` file, verifies its ELF header, computes the
`md5`, and writes bytes plus a per-bot **index** object. teclaw's composer turns
that index into artifact `cli_tools` refs; the ARCA family gets the *same refs*
through a new `cli_tools` domain on `PerDomainRuntimeProjection`, which calls
the engine's tools endpoint live. `cli_tools` therefore stays `ON_CONTAINER`
where `APPLY_ORDER` already has it, and a `PUT` takes effect immediately on both
families. No deploy-path code is touched.

> **Revision 2** (PR #1870 review). Revision 1 delivered the ARCA arm as a
> platform-composed start-command prologue. Withdrawn: an arrangement in
> platform code is not a protocol, so every future engine would have needed a
> bespoke integration, and the category would have been effective only at the
> next provisioning. The projection seam is the protocol, and it already exists.

## Affected Components

- `core/bot_config_manifest/capabilities.py` — unlock `cli_tools`.
- `core/bot_config_manifest/managed_files/` — new `cli_tools` category, `tools/`
  namespace, and the index object.
- `core/bot_config_manifest/apply/materialisers/cli_tools.py` — **new**, the
  fetch → verify → select → store → project pipeline.
- `core/bot_config_manifest/apply/{registry,delivery}.py` — registration and the
  new port on `MaterialiserPorts`. `order.py` is **not** touched.
- `core/bot_config_manifest/apply/cli_tools_port.py` — **new**, the narrow port
  the materialiser projects through, bound per strategy.
- `core/skill_center/runtime_projection_contract.py`,
  `core/skill_center/services/runtime_projections/per_domain.py` — the
  `cli_tools` domain and its `ProjectionScope` half.
- `core/config_compose/{protocols,models}.py`, `services/config_composer.py`,
  `managed_files/reader.py` — the teclaw arm: `cli_tools` refs in the artifact.
- `docs/bot-config-manifest/*` — the ARCA-facing protocol document and the docs.

**Not touched:** `core/service_bot/services/baas_service.py` and everything
under `services/deploy/`. Revision 1 changed them; revision 2 does not.

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

**Why an index, when `store.py` says "the key layout *is* the record".** The
other three categories need no per-file metadata beyond the path, so their
layout carries everything. `cli_tools` needs four things a path cannot hold:
`md5` (the artifact field and the engine's change test), `version`, and the
convergence key `digest` + `subpath`. The alternative is reading every tool's
bytes back on every compose to recompute `md5` — up to 200 MiB per tool per
artifact. The index is a deliberate, argued exception; `store.py`'s module
docstring gets a paragraph saying so.

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
        """Read-only: compare each intent's (digest, subpath) against the index.

        Equal ⇒ ``unchanged``; a name in the index and not in the declaration ⇒
        a removal. Category-level replacement, per §3.2.
        """

    async def write(self, ctx, plan) -> Sequence[EntryResult]:
        """put changed tools, delete removals, then rewrite the index last."""
```

The index is rewritten **after** the bytes, so a crash mid-write leaves an index
that under-claims rather than over-claims: the projection and the composer never
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
`ApplyStep(ManifestCategory.CLI_TOOLS, ApplyPhase.ON_CONTAINER, 6)`, which is
where a live projection belongs.

```diff
# core/bot_config_manifest/apply/delivery.py:92 — MaterialiserPorts
     entry_fetcher: EntryFetcher
     resource_service: ManifestResourcePort
+    cli_tool_store: ManifestCliToolStore
+    cli_tool_projection: CliToolProjectionPort
```

```python
# core/bot_config_manifest/apply/cli_tools_port.py (new)
@runtime_checkable
class CliToolProjectionPort(Protocol):
    """What the cli_tools materialiser asks of the runtime projection.

    The ``ActivationPort`` move, for a third domain: the ARCA strategy binds
    the real projector, the platform-managed teclaw strategy binds a
    record-only stand-in because the artifact is its projection.
    """

    async def project_cli_tools(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        tools: Sequence[CliToolRef],
    ) -> RuntimeProjectionResult: ...
```

```diff
# core/bot_config_manifest/apply/registry.py:238 — build_materialisers
         ResourcesMaterialiser(resource_service, entry_fetcher),
+        CliToolsMaterialiser(cli_tool_store, cli_tool_projection, entry_fetcher),
     )
```

Both strategies bind `cli_tool_store` to the same `ManagedFilesStore`. They
differ on `cli_tool_projection`: ARCA binds the real projector, teclaw binds the
record-only stand-in — the same split `ActivationPort` already makes.

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

## ARCA arm — the `cli_tools` projection domain

A third domain beside skills and MCP on the projection that already spans the
ARCA family. No deploy-path code is involved.

```diff
# core/skill_center/runtime_projection_contract.py — ProjectionScope
   claimed_mcp: frozenset[str] = frozenset()
   released_mcp: frozenset[str] = frozenset()
+  #: The tools half. Declared by the mutation, never inferred — so a
+  #: tools-only apply does not force a skills or MCP rewrite.
+  touched_cli_tools: bool = False
```

```python
# core/skill_center/services/runtime_projections/per_domain.py (new method)
async def _apply_cli_tools(
    self, *, tools: Sequence[CliToolRef], scope: ProjectionScope
) -> RuntimeProjectionResult:
    """Call the engine's tools endpoint with the declared set.

    The engine owns placement, the executable bit, PATH exposure and removal
    of what the set no longer names. The platform sends the same ref shape
    teclaw gets from the artifact — one protocol, not an ARCA dialect.
    """
```

```jsonc
// what the engine receives — the cliToolRef shape, verbatim
{ "cli_tools": [
    { "name": "mycli", "store": "bot-data",
      "path": "staff_u1/bot7_manifest/teclaw/tools/mycli",
      "md5": "9f2c…", "version": "1.4.2" }
] }
```

`validate_plan` gains the refusal so an engine with no tools contract fails
before any request is emitted:

```diff
# core/skill_center/services/runtime_projections/per_domain.py — validate_plan
+  if plan.cli_tools and not self._runtime.supports_cli_tools:
+      raise SkillSetRuntimeReconcileError(...)   # refused before any request
```

An engine without the endpoint yields the existing vocabulary rather than a new
one:

```python
# the SKIPPED result an engine without a tools endpoint produces
RuntimeProjectionResult(
    status=RuntimeProjectionStatus.SKIPPED,
    issues=[RuntimeProjectionIssue(
        resource_type="cli_tool", code="engine_no_cli_endpoint",
        reason="engine '<x>' has no cli_tools runtime endpoint yet",
        status=RuntimeProjectionStatus.SKIPPED, retryable=False,
        suggested_action="...",
    )],
)
```

## API / Interface Changes

No HTTP surface changes on the platform's own API. `PUT`/`GET`/`DELETE
…/config-manifest` and `POST …/config-manifest/apply` keep their shapes; the
only visible difference is that a document declaring `cli_tools` is now
accepted, and capabilities reports it supported:

```jsonc
// GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities → 200 (excerpt)
{ "kind": "category", "name": "cli_tools", "supported": true, "reason": "" }
```

**New engine-facing contract (the ARCA family's half, implemented by engines):**

```jsonc
// engine tools endpoint — request body
{ "cli_tools": [ { "name": "mycli", "store": "bot-data", "path": "…",
                   "md5": "9f2c…", "version": "1.4.2" } ] }
// the engine: fetches by {store, path}, skips when md5 already matches,
// places the file, sets +x, exposes `name` on PATH, and removes any
// platform-delivered tool this list no longer names. An empty list removes all.
```

The apply report carries the projection outcome per entry, using the existing
`RuntimeProjectionStatus` vocabulary — no new report shape.

## Dependencies

None. `hashlib` for the md5. The engine-side endpoint is an external
dependency on each ARCA engine team, tracked as out-of-scope in the spec, not a
package.

## Risks & Mitigations

- **Risk:** no ARCA engine has the tools endpoint on day one, so the feature
  ships inert for that family.
  **Mitigation:** by design (spec D-8) — the projection returns `SKIPPED` with
  an issue naming the engine, so the apply report is honest instead of claiming
  a delivery that did not happen. The platform half, the protocol document and
  teclaw's arm all land and are testable without any engine change.
- **Risk:** a tools-only apply forces a full skills/MCP rewrite on every engine.
  **Mitigation:** `ProjectionScope` gains the tools half, which is exactly what
  the existing halves exist for.
- **Risk:** the engine fetches by `{store, path}` and cannot reach the store.
  **Mitigation:** it is the same `bot-data` store and the same ref shape the
  teclaw engine already resolves for identity, resources and skills — not a new
  content plane. Confirmed per engine as part of the protocol document.
- **Risk:** a partially written index names a tool whose bytes are absent.
  **Mitigation:** bytes are written before the index, so it under-claims rather
  than over-claims, and the next apply re-converges.
- **Risk:** the index departs from the store's "layout is the record" rule and
  becomes a precedent.
  **Mitigation:** the reason is written into `store.py`'s docstring — four
  fields that cannot live in a path — so the next category has to make the same
  argument rather than inherit the exception.

## Alternatives Considered

- **A platform-composed start-command prologue** (revision 1). Rejected in
  review of PR #1870: an arrangement in platform code is not a protocol, so
  every future engine needs a bespoke integration; and it made `cli_tools`
  effective only at the next device provisioning, forcing a §2.6 exception.
- **An engine-side *command* protocol** (platform issues `chmod`/`mv`/`PATH`).
  Rejected in spec D-1: contradicts `manifest-schema` §6, makes
  `cliToolRef.md5` meaningless, loses free full-replacement, and opens a
  remote-exec channel into customer containers.
- **A new projection seam for tools alone.** Rejected: `EngineRuntimeProjection`
  already spans the engines with the right result vocabulary; a parallel seam
  would be a second way to say the same thing.
- **No index; recompute `md5` at compose time.** Rejected: re-reads every tool's
  bytes (≤200 MiB each) on every artifact compose.
- **Push bytes through `DeviceFileSystem` into the container.** Rejected: there
  is no `chmod` on that protocol, so the tool arrives non-executable, and
  placement would become the platform's decision.

## Rollout

No flag of its own. The teclaw arm rides W8's existing
`user_config.bot_config_manifest.teclaw_platform_managed`, still default off.
The ARCA arm needs no switch: an engine without the endpoint reports `SKIPPED`,
which is the honest state until it ships one.

```bash
# no migration; the store is the only new state
uv run pytest tests/community/core/bot_config_manifest \
              tests/community/core/config_compose \
              tests/community/core/skill_center \
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
# tests/community/core/skill_center/test_cli_tools_projection.py (new)
def test_per_domain_projection_sends_the_cli_tool_ref_shape(): ...
def test_engine_without_tools_endpoint_yields_skipped_with_an_issue(): ...
def test_validate_plan_refuses_before_any_runtime_request(): ...
def test_tools_only_scope_does_not_rewrite_skills_or_mcp(): ...
def test_empty_declared_list_projects_removal_of_every_tool(): ...
```

```python
# tests/community/core/bot_config_manifest/test_iteration1_ordering.py (extend)
def test_cli_tools_stays_on_container_on_both_families(): ...
```

Also extended: a test pinning that no deploy-path file changed — the composed
start command is byte-identical for every bot, so #935's assertion stands
unedited.

Manual: none required. The engine half is exercised through the projection
port's fake; a real end-to-end run waits on the first engine to ship the
endpoint.
