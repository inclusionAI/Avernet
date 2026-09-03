# Plan: `cli_tools` — Declarative Command-Line Tools for Both Engine Families (W9)

Spec: `spec.md` in this directory. Work item W9, issue #1477.

## Approach

One materialiser converges a single desired state — the managed-files store —
and each family reads it its own way. The materialiser fetches, enforces the
declared `sha256`, unpacks, selects the one `subpath` file, verifies its ELF
header, computes the `md5`, and writes bytes plus a small per-bot **index**
object. teclaw's composer turns that index into artifact `cli_tools` refs;
ARCA's deploy path turns it into a **boot-chain prologue** that pulls each tool
over a signed URL, sets the executable bit and prepends the tools directory to
`PATH`. Because both arms read the store rather than a container, `cli_tools`
moves to `PRE_CONTAINER` on both families, which is what makes a newly created
bot's *first* container carry its tools.

## Affected Components

- `core/bot_config_manifest/capabilities.py` — unlock `cli_tools`.
- `core/bot_config_manifest/managed_files/` — new `cli_tools` category, `tools/`
  namespace, and the index object.
- `core/bot_config_manifest/apply/materialisers/cli_tools.py` — **new**, the
  fetch → verify → select → store pipeline.
- `core/bot_config_manifest/apply/{order,registry,delivery}.py` — phase move,
  registration, and the new port on `MaterialiserPorts`.
- `core/config_compose/{protocols.py,models.py,services/config_composer.py}` and
  `managed_files/reader.py` — the teclaw arm: `cli_tools` refs in the artifact.
- `core/service_bot/services/{baas_service.py,deploy/*}` — the ARCA arm: the
  prologue, resolved by the service and prepended by the composer.
- `docs/bot-config-manifest/*` and `docs/.../user-manual.zh-CN.md` — the docs.

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
`md5` (the artifact field and the prologue's change test), `version`, and the
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
that under-claims rather than over-claims: the prologue and the composer never
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

## Phase and registration

```diff
# core/bot_config_manifest/apply/order.py:79
-    ApplyStep(ManifestCategory.CLI_TOOLS, ApplyPhase.ON_CONTAINER, 6),
+    ApplyStep(ManifestCategory.CLI_TOOLS, ApplyPhase.PRE_CONTAINER, 6),
```

`PRE_CONTAINER` is defined as "a plain database write, no device involved"
(`order.py:30`); a store write qualifies, and this is the one category where it
is true on ARCA as well. On the ARCA creation path this is load-bearing: the
step must land before `_build_create_bot_payload` composes the start command,
because that is when the prologue is rendered — the same reason `script` is
`PRE_CONTAINER`.

```diff
# core/bot_config_manifest/apply/delivery.py:92 — MaterialiserPorts
     entry_fetcher: EntryFetcher
     resource_service: ManifestResourcePort
+    cli_tool_store: ManifestCliToolStore
```

Both strategies bind `cli_tool_store` to the same `ManagedFilesStore` — this is
the one port that does not vary by family, and the field exists so the
materialiser is constructed the same way as the others rather than reaching for
a singleton.

```diff
# core/bot_config_manifest/apply/registry.py:238 — build_materialisers
         ResourcesMaterialiser(resource_service, entry_fetcher),
+        CliToolsMaterialiser(cli_tool_store, entry_fetcher),
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

## ARCA arm

The prologue is *resolved* by the service (which crosses boundaries already) and
*rendered into the chain* by the composer (which crosses none) — the same split
`startup_script` uses today (`baas_service.py:711`, `:2318`).

```diff
# core/service_bot/services/deploy/deploy_models.py — BotDeployContext
     ext_info: Optional[Dict[str, Any]] = None
+    #: Platform-rendered shell that stages this bot's cli_tools and exports
+    #: PATH. Empty for a bot that declares none — and then the composed start
+    #: command is byte-identical to today's.
+    cli_tools_prologue: str = ""
```

```diff
# core/service_bot/services/baas_service.py:711
             startup_script = self._resolve_startup_script(
                 entity_id=entity_id, bot_id=bot_id
             )
+        cli_tools_prologue = self._resolve_cli_tools_prologue(
+            owner_id=owner_id, bot_id=bot_id
+        )
```

```python
# core/service_bot/services/baas_service.py (new)
def _resolve_cli_tools_prologue(self, *, owner_id: str, bot_id: str) -> str:
    """Render this bot's tool staging, or "" when it declares none.

    Reads the tool index and signs one short-lived GET per tool. Mirrors
    ``_resolve_startup_script``: re-read on every payload the platform
    composes, so create / restart / republish all pick up the current set.
    """
```

```python
# core/bot_config_manifest/cli_tools_prologue.py (new)
TOOLS_DIR = "/home/admin/nfs/bot-data/.platform-tools"

def render_prologue(tools: Sequence[SignedTool], *, tools_dir: str = TOOLS_DIR) -> str:
    """POSIX sh implementing the same four behaviours teclaw implements:
    placement, md5 change test, executable bit, PATH — plus full replacement.
    Returns "" for an empty list."""
```

Rendered shape (one block, prepended to the chain):

```bash
# rendered by cli_tools_prologue.render_prologue
mkdir -p "$TD"
# full replacement: anything not declared goes
for f in "$TD"/*; do case " mycli tk " in *" ${f##*/} "*) ;; *) rm -f "$f";; esac; done
# per tool: skip when the md5 already matches, else fetch, verify, chmod
if [ "$(md5sum "$TD/mycli" 2>/dev/null | cut -d' ' -f1)" != "9f2c…" ]; then
  curl -fsSL -o "$TD/mycli.part" '<signed-url>' \
    && [ "$(md5sum "$TD/mycli.part" | cut -d' ' -f1)" = "9f2c…" ] \
    && chmod +x "$TD/mycli.part" && mv -f "$TD/mycli.part" "$TD/mycli" \
    || echo "[cli_tools] FAILED to stage mycli" >&2
fi
export PATH="$TD:$PATH"
```

```diff
# core/service_bot/services/baas_service.py:2328 — _compose_start_command
-        chain = self._deploy_composer.build_start_command(ctx)
+        chain = self._deploy_composer.build_start_command(ctx)
+        if ctx.cli_tools_prologue:
+            # Before the chain, so the engine and everything it spawns inherit
+            # PATH. Non-fatal by construction: __OCB_RC still comes from the
+            # chain, so a tool that failed to stage never fails the boot.
+            chain = f"{ctx.cli_tools_prologue}\n{chain}"
```

Prepending in `_compose_start_command` rather than in each composer means the
ACK and managed runtimes get it from one place, and no composer's own
`build_start_command` changes.

## API / Interface Changes

No HTTP surface changes. `PUT`/`GET`/`DELETE …/config-manifest` and
`POST …/config-manifest/apply` keep their shapes; the only visible difference is
that a document declaring `cli_tools` is now accepted, and
`GET …/config-manifest/capabilities` reports it supported:

```jsonc
// GET /openapi/v1/bots/{bot_id}/config-manifest/capabilities → 200 (excerpt)
{ "kind": "category", "name": "cli_tools", "supported": true, "reason": "" }
```

The ARCA delivery note rides the apply report's existing warning channel:

```text
delivered now; effective at this bot's next device provisioning
(create, restart or republish)
```

## Dependencies

None. `hashlib` (md5), `curl` and `md5sum` in the container image — both already
relied on by the existing boot chain.

## Risks & Mitigations

- **Risk:** the prologue is rendered from the index at compose time, so a tool
  written *after* the payload was composed is not in that container.
  **Mitigation:** this is D-3, the accepted §2.6 exception; the apply response
  says so in the delivery note, and the next provisioning converges.
- **Risk:** signed URLs expire before a slow first boot finishes.
  **Mitigation:** expiry sized to the boot window with headroom, not the default
  7200s ceiling reasoned about in isolation; a failure is loud in the boot log
  and the next provisioning retries. Named in the spec's open questions.
- **Risk:** a signed URL leaking through logs.
  **Mitigation:** `after_create_cmd_hook` is already elided in BaaS logs
  (`baas_service.py:176-188`); a test pins that the elision still covers the
  hook once the prologue is in it.
- **Risk:** `TOOLS_DIR` is not writable, or the chain's user cannot read it, on
  some runtime.
  **Mitigation:** it sits under the NAS mount every managed bot already has
  (`managed_composer.py:428`, `deploy_models.py:167`); verifying it per runtime
  is the concrete form of A2 and is a task, not an assumption.
- **Risk:** a partially written tool is served to the model.
  **Mitigation:** fetch to `.part`, verify `md5`, `chmod`, then atomic `mv`;
  and the index is written after the bytes so it never over-claims.
- **Risk:** the phase move changes ARCA ordering for an existing bot.
  **Mitigation:** `cli_tools` has never been appliable, so no stored document
  contains it and no existing apply changes shape. The ordering test gains a
  `cli_tools` row rather than editing an existing assertion.

## Alternatives Considered

- **An engine-side *command* protocol** (platform issues `chmod`/`mv`/`PATH`).
  Rejected in spec D-1: contradicts `manifest-schema` §6, makes
  `cliToolRef.md5` meaningless, loses free full-replacement, and opens a
  remote-exec channel into customer containers.
- **Five ARCA images implement the contract now.** Rejected: breaks the
  zero-changes promise (`engine-requirements.zh-CN.md:16`) for no benefit this
  iteration. The prologue is the same contract, implemented once; an image can
  take it over later without a contract change.
- **No index; recompute `md5` at compose time.** Rejected: re-reads every tool's
  bytes (≤200 MiB each) on every artifact compose.
- **`md5` as a field on `ManagedFile`.** Rejected: `store.list` reads keys only
  and would return `None` for it, so the composer would still have to read
  bytes — the index is the same information in one object.
- **Push bytes through `DeviceFileSystem` into the container.** Rejected: there
  is no `chmod` on that protocol, so the tool arrives non-executable, and it
  needs a bound device, which would push `cli_tools` back to `ON_CONTAINER` and
  lose the first-container guarantee.

## Rollout

No flag of its own. The teclaw arm rides W8's existing switch —
`user_config.bot_config_manifest.teclaw_platform_managed`, still default off —
because it composes through the same reader. The ARCA arm needs no switch: the
prologue is empty for every bot that declares no tools, which is every bot until
someone writes one.

```bash
# no migration; the store is the only new state
uv run pytest tests/community/core/bot_config_manifest tests/community/core/config_compose \
              tests/community/core/service_bot tests/community/kernel/test_bot_config_artifact.py
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
# tests/community/core/service_bot/test_cli_tools_prologue.py (new)
def test_no_tools_composes_a_byte_identical_start_command(): ...   # guards #935
def test_prologue_precedes_the_chain_so_path_is_inherited(): ...
def test_matching_md5_skips_the_download(): ...
def test_undeclared_file_in_the_tools_dir_is_removed(): ...
def test_prologue_failure_does_not_change_the_boot_exit_status(): ...
def test_hook_carrying_signed_urls_stays_elided_in_logs(): ...
```

```python
# tests/community/core/bot_config_manifest/test_iteration1_ordering.py (extend)
def test_cli_tools_is_pre_container_on_both_families(): ...
```

Manual: none required — the prologue's rendered shell is asserted as a string,
and A2's per-runtime directory check is a task with a named owner rather than a
test.
