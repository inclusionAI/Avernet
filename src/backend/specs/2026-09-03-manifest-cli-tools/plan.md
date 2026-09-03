# Plan: `cli_tools` — Platform-Managed Command-Line Tools (W9)

Spec: `spec.md` in this directory. Work item W9, issue #1477.

## Approach

A table, a service, an engine port, and three thin callers. `ac_bot_cli_tool`
holds the platform's record of what a bot has installed. `CliToolService` is the
only code that fetches, verifies, places and records — the HTTP routes and the
manifest materialiser both delegate to it. Placement goes through a narrow
`CliToolEnginePort` with four operations (upload / delete / list / replace-all),
bound per delivery strategy: device write plus `chmod +x` on ARCA, the
managed-files store plus artifact refs on teclaw. Tools live in their own
`tools/` namespace, outside the `workspace/` namespace the resources API is
confined to, so that surface cannot address them.

> **Revision 4** (owner decision after the PR #1870 review rounds). Rev 1 used a
> start-command prologue, rev 2 the runtime-projection seam, rev 3 the resources
> write chain with no platform record. Rev 4 keeps rev 3's "a tool is a file plus
> an executable bit" mechanics but adds what rev 3 lacked: platform-owned
> metadata, a management API, and an engine protocol with a batch operation.

## Affected Components

- `core/bot_config_manifest/cli_tools/` — **new package**: the service, the
  engine port, the record and the two port implementations.
- `core/repository/{protocols,implementations}/bot/cli_tool.py` — **new**, the
  repository split §8 requires.
- `core/schema.py` — register the ORM model's side-effect import.
- `core/config_compose/teclaw_paths.py` — the `tools/` namespace.
- `api/bot_cli_tool_service.py` — **new**, the service API contract, registered
  in the consistency `_PAIRS`.
- `adapters/http/openapi_v1/bots/cli_tools.py` — **new**, the management routes.
- `core/bot_config_manifest/apply/materialisers/cli_tools.py` — **new**, a thin
  materialiser delegating to the service.
- `core/bot_config_manifest/apply/{registry,delivery}.py` — register it, bind the
  port per family. `order.py` and the orchestrator are **not** touched.
- `core/bot_config_manifest/capabilities.py` — unlock `cli_tools`.
- `core/config_compose/{protocols,models}.py`, `services/config_composer.py` —
  the teclaw arm's `cli_tools` refs.
- `docs/bot-config-manifest/*` — schema, user manual, teclaw contract, A2.

**Not touched:** `service_bot/services/baas_service.py` and `services/deploy/*`
(rev 1 changed them); `core/skill_center/*` and the runtime projection (rev 2);
and every resources endpoint (D-5 needs no filter).

## Data Model Changes

```python
# core/bot_config_manifest/cli_tools/models.py (new) — ORM + record
class BotCliToolORM(Base):
    __tablename__ = "ac_bot_cli_tool"
    __table_args__ = (
        # A command name is unique per bot: a duplicate is unwritable, not
        # merely invalid. Mirrors ac_bot_startup_script's env-scoped uniqueness.
        UniqueConstraint("env", "bot_id", "name", name="uk_env_bot_tool"),
    )
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    env = Column(String(32), nullable=False, default=get_current_env)
    entity_id = Column(String(64), nullable=False)
    bot_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)      # the command
    source = Column(Text, nullable=False)           # declared URL / named source
    digest = Column(String(80), nullable=False)     # pinned sha256:…
    subpath = Column(String(512), nullable=True)    # selected archive member
    md5 = Column(String(32), nullable=False)        # platform-computed
    size_bytes = Column(BigInteger, nullable=False)
    version = Column(String(64), nullable=True)     # metadata only
    installed_by = Column(String(64), nullable=False)  # "manifest" | user id
    modifier = Column(String(64), nullable=False)
    gmt_create = Column(DateTime, server_default=func.now())
    gmt_modified = Column(DateTime, server_default=func.now(), onupdate=func.now())

register_avernet_tenant_guard(BotCliToolORM)
```

```diff
# core/schema.py:52 — the side-effect import that emits the table locally
     import agentclaw.community.core.bot_config_manifest.repository.models  # noqa: F401  ac_bot_config_manifest
+    import agentclaw.community.core.bot_config_manifest.cli_tools.models  # noqa: F401  ac_bot_cli_tool
```

`installed_by` is the column that makes a full override honest: the report can
say a manifest apply replaced an API-installed tool, instead of silently
overwriting it.

## The namespace

```diff
# core/config_compose/teclaw_paths.py:26
 WORKSPACE_NS = "workspace"
 IDENTITY_NS = "identity"
+#: CLI tools. A sibling of ``workspace`` on purpose: the resources API is
+#: confined to the workspace namespace (``build_workspace_mapper`` raises on
+#: anything else), so a tool is unreachable from that surface by construction
+#: rather than by a filter someone has to remember (spec D-5).
+TOOLS_NS = "tools"
-_NAMESPACES = (WORKSPACE_NS, IDENTITY_NS, CONFIG_NS)
+_NAMESPACES = (WORKSPACE_NS, IDENTITY_NS, CONFIG_NS, TOOLS_NS)
```

## The core service

```python
# core/bot_config_manifest/cli_tools/service.py (new)
class CliToolService:
    """Install, remove and list a bot's CLI tools. The only code that does it.

    The HTTP adapter and the manifest's ``cli_tools`` materialiser are two
    callers; neither reimplements any step. Backend code never calls the
    platform's own HTTP endpoints (spec D-2).
    """

    def __init__(
        self, *, repo: CliToolRepositoryProtocol, engine: CliToolEnginePort,
        entry_fetcher: EntryFetcher,
    ) -> None: ...

    async def install(self, ctx: CliToolContext, decl: CliToolDecl) -> CliToolOutcome:
        """fetch → enforce sha256 → unpack → select subpath → verify ELF →
        md5 → engine.upload → record. Nothing is recorded for a step that
        failed, so the table never claims a tool the container lacks."""

    async def remove(self, ctx: CliToolContext, name: str) -> CliToolOutcome: ...

    async def list(self, ctx: CliToolContext) -> list[CliToolRecord]:
        """The platform's record — the answer to "what does this bot have"."""

    async def replace_all(
        self, ctx: CliToolContext, decls: Sequence[CliToolDecl]
    ) -> list[CliToolOutcome]:
        """Full override: the declared set becomes the installed set.

        What the manifest calls. Removals come from the table, not from the
        engine's listing, so a tool the platform installed is removed even if
        the engine's view has drifted. One call, so a partial failure is
        reported per tool rather than left for the caller to reconcile.
        """

    async def drift(self, ctx: CliToolContext) -> CliToolDrift:
        """Table versus ``engine.list`` — observable rather than assumed away."""
```

```python
# core/bot_config_manifest/cli_tools/verify.py (new)
ELF_MAGIC = b"\x7fELF"
EM_X86_64 = 0x3E  # e_machine at offset 18, little-endian

def verify_amd64_elf(data: bytes, *, name: str) -> None:
    """Refuse a non-ELF file or one built for another architecture.

    ``digest`` answers "are these the bytes you asked for"; this answers "can
    this machine run them", and a *wrong* binary still has a valid digest.
    """

def select_subpath(tree: UnpackedTree, subpath: str, *, location: str) -> Path:
    """The one declared file inside an unpacked archive: must exist, must be a
    regular file, must still resolve inside the tree after symlinks."""
```

## The engine port

```python
# core/bot_config_manifest/cli_tools/engine_port.py (new)
@runtime_checkable
class CliToolEnginePort(Protocol):
    """What the service asks of an engine. Four operations, per spec D-4."""

    async def upload(self, ctx: CliToolContext, *, name: str, data: bytes) -> None:
        """Place the file and make it executable. Raises on either failure."""

    async def delete(self, ctx: CliToolContext, *, name: str) -> None: ...

    async def list(self, ctx: CliToolContext) -> list[str]:
        """The engine's own view — the input to the drift read."""

    async def replace_all(
        self, ctx: CliToolContext, *, uploads: Sequence[tuple[str, bytes]],
        remove: Sequence[str],
    ) -> None:
        """Make the installed set equal ``uploads``. One round trip where the
        engine supports it; the default implementation loops."""
```

### ARCA implementation — write, then chmod

```python
# core/bot_config_manifest/cli_tools/arca_port.py (new)
class ArcaCliToolPort(CliToolEnginePort):
    """Device write through the existing file chain, then the executable bit.

    The chmod uses ``execute_baas_shell_command`` — the channel
    ``baas_container_init`` already uses for bootstrap, engine install and
    service start. A non-zero exit fails the entry with the command's stderr,
    so a file the model cannot run is never recorded as installed.
    """

    async def upload(self, ctx, *, name: str, data: bytes) -> None: ...
        # device_fs.write_file(f"{TOOLS_NS}/{name}", data)
        # execute_baas_shell_command(..., shell_cmd=f"chmod +x {shlex.quote(abs_path)}")
```

```python
# the chmod, sketched — quoting matters because `name` is user-supplied
result = execute_baas_shell_command(
    baas_service=self._baas, device=device,
    shell_cmd=f"chmod 0755 {shlex.quote(tool_abs_path)}",
    timeout_seconds=30,
)
if result.exit_code != 0:
    raise CliToolPlacementError(f"chmod failed for {name!r}: {result.stderr}")
```

`name` is validated by W1 (no path separators, unique per bot) *and* quoted
here: the schema rule is the contract, the quoting is the defence.

### teclaw implementation — the store plus artifact refs

```python
# core/bot_config_manifest/cli_tools/teclaw_port.py (new)
class TeclawCliToolPort(CliToolEnginePort):
    """Bytes into the managed-files store; the artifact is the delivery.

    ``upload`` puts the object under the bot's tools prefix; the composer reads
    the metadata table for ``md5``/``version`` and emits ``cliToolRef``s. The
    engine places them on receipt, per teclaw-cli-contract §3.4.
    """
```

## The management API

```python
# adapters/http/openapi_v1/bots/cli_tools.py (new)
@router.post("", response_model=Envelope[CliToolView])       # ADMISSION: ADMIN
async def install_cli_tool(bot_id: BotIdPath, body: CliToolInstall, ...): ...

@router.get("", response_model=Envelope[Page[CliToolView]])  # ADMISSION: MEMBER
async def list_cli_tools(bot_id: BotIdPath, ...): ...

@router.delete("/{name}", response_model=Deleted)            # ADMISSION: ADMIN
async def delete_cli_tool(bot_id: BotIdPath, name: str, ...): ...
```

```jsonc
// POST /openapi/v1/bots/{bot_id}/cli-tools → 200
{ "name": "mycli", "version": "1.4.2", "digest": "sha256:3e7a…",
  "subpath": null, "md5": "9f2c…", "size_bytes": 8123456,
  "installed_by": "u1", "gmt_modified": "2026-09-03T12:00:00Z" }
// 400 when digest is absent — the platform will not distribute an unpinned
// executable. 409 when the name is already installed.
```

Each route is collaborator-scoped like the config-manifest group (MEMBER read /
ADMIN write) and carries its own `ADMISSION` line; the contract lives in
`api/bot_cli_tool_service.py` and is registered in the consistency `_PAIRS`.

## The materialiser

```python
# core/bot_config_manifest/apply/materialisers/cli_tools.py (new)
class CliToolsMaterialiser(Materialiser):
    construct = ManifestCategory.CLI_TOOLS

    def __init__(self, tools: CliToolService) -> None: ...

    async def resolve(self, ctx, entries) -> ResolveResult:
        """Placeholder substitution and the syntactic checks. No fetch here —
        the service owns fetching, and doing it twice is how the two callers
        would drift."""

    async def plan(self, ctx, intents) -> CategoryPlan:
        """Read the table: an intent whose ``(digest, subpath)`` matches an
        existing row plans ``unchanged``; rows the declaration no longer names
        plan a removal."""

    async def write(self, ctx, plan) -> Sequence[EntryResult]:
        """One call: ``CliToolService.replace_all``. The outcomes become the
        report's entries."""
```

```diff
# core/bot_config_manifest/apply/delivery.py:92 — MaterialiserPorts
     entry_fetcher: EntryFetcher
     resource_service: ManifestResourcePort
+    cli_tool_service: CliToolService
```

The service is what the strategy binds, already holding the family's engine
port — so the materialiser takes one dependency and the family difference stays
where W6 put it.

## Isolation from the resources API — how it is ensured

Nothing is added to any resources endpoint. The guarantee comes from three
facts already in the tree:

```python
# core/services/resource_file_service.py:334 — every resources call
def _logical(path: str) -> str:
    return f"{WORKSPACE_NS}/{path}" if path else WORKSPACE_NS

# core/services/resource_addressing.py:55 — and the mapper refuses the rest
def _map(logical: str) -> str:
    if logical == WORKSPACE_NS: rel = ""
    elif logical.startswith(prefix): rel = logical[len(prefix):]
    else: raise ValueError(...)      # ← a tools path cannot get through

# core/services/resource_file_service.py:141 — and `..` never escapes
if any(s == ".." for s in segments):
    raise InvalidResourcePathError(...)
```

So a tool under `tools/` is unreachable: the only namespace a resources request
can name is `workspace/`, and no `path` value rewrites that prefix. The test
asserts the property rather than enumerating endpoints, which is what keeps it
true for endpoints added later.

**Why not `_HIDDEN_DIRNAMES`.** It hides a directory from the *root listing*
only — `resource_file_service.py:391` guards on `not path` — so `?path=state`
still lists today. It stays available as a fallback if a placement ever has to
live under `workspace/`, and the plan does not rely on it.

## Dependencies

None. `hashlib` for the md5, `shlex` for the chmod quoting.

## Risks & Mitigations

- **Risk:** the table says a tool is installed and the container disagrees
  (a hand-deleted binary, a rebuilt container without NAS persistence).
  **Mitigation:** `drift()` compares the table against `engine.list`, and
  `replace_all` computes removals from the table so the platform's own tools are
  always re-asserted. Drift is observable, not assumed away.
- **Risk:** a `chmod` succeeds but the directory is not on the agent's `PATH`, so
  the tool is installed and uninvokable.
  **Mitigation:** the open question, and Task 12 confirms it per runtime before
  the feature is announced. The apply report says "installed", which is true; the
  `PATH` guarantee is what Task 12 establishes.
- **Risk:** `name` reaches a shell.
  **Mitigation:** W1 forbids path separators and enforces per-bot uniqueness,
  and the command is `shlex.quote`d regardless. A test passes a hostile name.
- **Risk:** a partial `replace_all` leaves some tools new and some old.
  **Mitigation:** per-tool outcomes in one report, and the table records only
  what landed — so the next apply converges the remainder rather than believing
  it is done.
- **Risk:** two callers drift — the API validating differently from apply.
  **Mitigation:** validation lives in the service, and a test calls both entry
  points against the same hostile declaration and asserts the same refusal.

## Alternatives Considered

- **A start-command prologue** (rev 1). Rejected in review: an arrangement in
  platform code, not a protocol; effective only at the next provisioning.
- **A `cli_tools` domain on `EngineRuntimeProjection`** (rev 2). Rejected: that
  seam carries platform state a runtime must be told about and reconciled
  against; a tool has none of that, and it would have charged five engine teams
  an endpoint for machinery nobody needed.
- **The resources write chain with no platform record** (rev 3). Rejected by the
  owner: a tool has metadata the platform must keep, and once it does, the
  record must be the only way in.
- **Filtering CLI tools out of the resources endpoints.** Rejected as the
  primary mechanism (D-5): a filter is a rule every future endpoint must
  remember, and `_HIDDEN_DIRNAMES` already demonstrates the decay.
- **Reusing `ac_bot_config_manifest` for the metadata.** Rejected: tools are
  installable without a manifest, so their record cannot live inside one.

## Rollout

No flag of its own. The teclaw arm rides W8's existing
`user_config.bot_config_manifest.teclaw_platform_managed`, still default off.
The table is additive and the API is new, so nothing existing changes shape.

```bash
# local bootstrap emits the new table via create_all; no migration file
uv run pytest tests/community/core/bot_config_manifest \
              tests/community/core/config_compose \
              tests/community/core/resources \
              tests/community/endpoints \
              tests/community/kernel/test_bot_config_artifact.py
```

## Test Strategy

```python
# tests/community/core/bot_config_manifest/cli_tools/test_service.py (new)
def test_install_enforces_declared_sha256(): ...
def test_archive_selects_only_the_declared_subpath(): ...
def test_subpath_must_be_a_regular_file_inside_the_tree_after_symlinks(): ...
def test_non_amd64_elf_fails_with_the_architecture_found(): ...
def test_nothing_is_recorded_when_placement_fails(): ...
def test_chmod_failure_fails_the_entry_with_stderr(): ...
def test_hostile_tool_name_is_quoted_into_the_chmod(): ...
def test_replace_all_removes_tools_absent_from_the_declaration(): ...
def test_replace_all_computes_removals_from_the_table_not_the_engine(): ...
def test_replace_all_reports_per_tool_on_partial_failure(): ...
def test_drift_reports_a_table_row_the_engine_does_not_have(): ...
```

```python
# tests/community/core/resources/test_cli_tools_are_unreachable.py (new)
def test_no_resources_path_can_address_the_tools_namespace(): ...
def test_workspace_mapper_raises_on_a_tools_logical_path(): ...
def test_installed_tool_never_appears_in_a_resources_listing(): ...
```

```python
# tests/community/endpoints/test_openapi_cli_tools.py (new)
def test_install_list_delete_round_trip(): ...
def test_install_without_digest_is_refused(): ...
def test_duplicate_name_is_409(): ...
def test_member_can_read_admin_can_write(): ...
def test_every_route_has_an_admission_line(): ...
```

```python
# tests/community/core/bot_config_manifest/apply/test_cli_tools_materialiser.py (new)
def test_materialiser_delegates_to_replace_all(): ...
def test_materialiser_adds_no_fetch_of_its_own(): ...
def test_unchanged_digest_and_subpath_plans_unchanged(): ...
def test_same_digest_different_subpath_is_a_change(): ...
def test_empty_declared_list_removes_every_tool(): ...
def test_api_and_apply_refuse_the_same_hostile_declaration(): ...
```

```python
# tests/community/core/bot_config_manifest/test_iteration1_ordering.py (extend)
def test_cli_tools_is_on_container_on_arca_and_pre_container_on_teclaw(): ...
```

Also extended: a test pinning that no deploy-path file and no resources endpoint
changed.
