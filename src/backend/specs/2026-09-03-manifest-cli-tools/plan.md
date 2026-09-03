# Plan: `cli_tools` — Platform-Managed Command-Line Tools (W9)

Spec: `spec.md` in this directory. Work item W9, issue #1477.

## Approach

A table, an OSS tool store, a service, and per-family delivery.
`ac_bot_cli_tool` records what a bot has; the OSS store holds the bytes.
`CliToolService` is the only code that fetches, verifies, stores, records and
delivers — the HTTP routes and the manifest materialiser both call it. ARCA
installs into the live container by name (device write plus `chmod`); on teclaw
the composed artifact's `cli_tools` refs point at the OSS objects and *are* the
delivery. Promotion copies those objects to the new stage prefix.

> **Revision 6** (PR #1870 review, fourth round). Rev 5 produced the artifact
> refs by gathering from the engine at promotion time, which left a live CLI
> update or a manifest apply on teclaw with nothing to reference. The platform
> now stores the bytes at install time, which also removes the protocol's `get`,
> turns promotion into a copy, and settles the phase per family.

## Affected Components

- `core/bot_config_manifest/cli_tools/` — **new package**: the service, the OSS
  tool store, the record, the ARCA delivery port and the teclaw ref source.
- `core/repository/{protocols,implementations}/bot/cli_tool.py` — **new**.
- `core/schema.py` — register the ORM model's side-effect import.
- `api/bot_cli_tool_service.py` — **new**, registered in the consistency `_PAIRS`.
- `adapters/http/openapi_v1/bots/cli_tools.py` — **new**, the management routes.
- `core/bot_config_manifest/apply/materialisers/cli_tools.py` — **new**, a thin
  materialiser delegating to the service.
- `core/bot_config_manifest/apply/delivery.py` — bind the service, and the
  `phase_of` rule that makes `cli_tools` `PRE_CONTAINER` on teclaw regardless of
  the switch. `order.py` and the orchestrator are **not** touched.
- `core/bot_config_manifest/apply/registry.py` — register the materialiser.
- `core/bot_config_manifest/capabilities.py` — unlock `cli_tools`.
- `core/service_bot/services/deploy/teclaw_file_promotion.py` — copy tool
  objects to the new stage prefix.
- `core/config_compose/{protocols,models}.py`, `services/config_composer.py` —
  the `cli_tools` refs and always-`platform` ownership.
- `docs/bot-config-manifest/*` — schema (including the §3.7 `PATH` correction),
  user manual, teclaw contract, A2.

**Not touched:** `core/skill_center/*` and the runtime projection;
`core/config_compose/teclaw_paths.py`; and every resources endpoint and
`core/services/resource_file_service.py`.

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
    oss_key = Column(String(512), nullable=False)   # where the platform kept the bytes
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
        self, *, repo: CliToolRepositoryProtocol, store: CliToolStore,
        delivery: CliToolDeliveryPort, entry_fetcher: EntryFetcher,
    ) -> None: ...

    async def install(self, ctx: CliToolContext, decl: CliToolDecl) -> CliToolOutcome:
        """fetch → sha256 → unpack → select subpath → verify ELF → md5 →
        **store in OSS** → deliver → record.

        The OSS write comes before delivery because it is what a teclaw
        artifact references (spec D-4); on ARCA it is also what makes a
        re-delivery possible without re-fetching a source URL that may have
        rotated. Nothing is recorded for a step that failed.
        """

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
        """Table versus ``delivery.list`` — observable, not assumed away."""
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

## The OSS tool store

```python
# core/bot_config_manifest/cli_tools/store.py (new)
class CliToolStore:
    """The platform's copy of a bot's tool bytes.

    Exists because a teclaw artifact composed for a live update or a manifest
    apply has to reference the tool *now* — gathering from the engine at that
    moment would be circular, since the platform is the side that just fetched
    and verified the bytes (spec D-4).
    """

    def put(self, ctx: CliToolContext, *, name: str, data: bytes) -> str:
        """Write the bytes; return the OSS key recorded on the row."""

    def copy_to_stage(self, ctx, *, name: str, stage: str) -> str:
        """Server-side copy to a stage-scoped prefix. What promotion calls."""

    def delete(self, ctx: CliToolContext, *, name: str) -> None: ...
```

## The delivery ports

```python
# core/bot_config_manifest/cli_tools/delivery_port.py (new)
@runtime_checkable
class CliToolDeliveryPort(Protocol):
    """How a family gets a tool into a bot. Name-addressed; no path crosses.

    There is deliberately **no `get`**: the platform holds the bytes, so nothing
    reads them back out of a container (spec D-5). ``list`` remains, for the
    drift read only.
    """

    async def install(self, ctx: CliToolContext, *, name: str, data: bytes) -> None: ...
    async def delete(self, ctx: CliToolContext, *, name: str) -> None: ...
    async def list(self, ctx: CliToolContext) -> list[str]: ...
    async def replace_all(
        self, ctx: CliToolContext, *, install: Sequence[tuple[str, bytes]],
        remove: Sequence[str],
    ) -> None: ...
```

### ARCA delivery — write, then chmod

```python
# core/bot_config_manifest/cli_tools/arca_port.py (new)
class ArcaCliToolPort(CliToolDeliveryPort):
    """Device write through the existing file chain, then the executable bit.

    The chmod uses ``execute_baas_shell_command`` — the channel
    ``baas_container_init`` already uses for bootstrap, engine install and
    service start. A non-zero exit raises with the command's stderr, so a file
    the model cannot run is never recorded as installed.

    The directory is this module's own constant and appears in no signature,
    no table column and no API response (spec D-3). Proposed value:
    ``/home/admin/.openclaw/cli``.
    """
```

```python
# the chmod, sketched — quoting matters because `name` is user-supplied
result = execute_baas_shell_command(
    baas_service=self._baas, device=device,
    shell_cmd=f"chmod 0755 {shlex.quote(self._abs(name))}",
    timeout_seconds=30,
)
if result.exit_code != 0:
    raise CliToolPlacementError(f"chmod failed for {name!r}: {result.stderr}")
```

`name` is validated by W1 (no path separators, unique per bot) *and* quoted
here: the schema rule is the contract, the quoting is the defence.

### teclaw delivery — the artifact is the delivery

```python
# core/bot_config_manifest/cli_tools/teclaw_port.py (new)
class TeclawCliToolPort(CliToolDeliveryPort):
    """No engine upload call. The refs on the composed artifact are the
    delivery, exactly as ``mcp`` is composed and delivered.

    ``install`` and ``delete`` therefore do nothing beyond letting the caller's
    row-and-store write stand: the next compose carries the new set. The
    strategy's existing closing redeliver (W8) is what pushes it.
    """
```

```diff
# core/service_bot/services/deploy/teclaw_file_promotion.py
  # workspace and identity are read from the engine, because the container owns
  # them. Tools are not: the platform holds the bytes, so promotion is a copy.
+ def _promote_cli_tools(self, ...) -> list[CliToolStagedRef]:
+     for record in self._tools.list(ctx):              # the platform's table
+         new_key = self._store.copy_to_stage(ctx, name=record.name, stage=stage)
+     # → {name, store, path, md5, version} refs for the composed artifact
```

Nothing is downloaded from the engine, so promotion costs a server-side copy
rather than a round trip through the container. `md5` and `version` come from
the metadata table.

## The phase rule

```diff
# core/bot_config_manifest/apply/delivery.py — TeclawDelivery.phase_of
     def phase_of(self, step: ApplyStep) -> ApplyPhase:
         if step.construct == ManifestSection.SCRIPT:
             return step.phase
+        if step.construct == ManifestCategory.CLI_TOOLS:
+            # The artifact is teclaw's delivery and it is composed before
+            # provisioning; a teclaw creation has no phase B at all under the
+            # switch-on sequence. So this category is PRE_CONTAINER whatever
+            # the switch says — it is always platform-managed (spec D-6, D-8).
+            return ApplyPhase.PRE_CONTAINER
         if self._platform_managed:
             return ApplyPhase.PRE_CONTAINER
         return ApplyPhase.ON_CONTAINER
```

`order.py` keeps `cli_tools` at `ON_CONTAINER` — that table is the ARCA reading,
per its own comment — and is not modified. Rev 5 claimed the per-family phase
needed no category-specific code; that was wrong, because the generic re-phasing
keys on the switch and this category must not.

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

## Why no isolation work is needed

Rev 4 added a `tools/` namespace so the resources API could not address a tool.
Rev 5 does not need it: the backend never addresses a tool by path at all, and
the engine keeps tools outside the workspace it serves to the file APIs. There
is nothing to hide.

So `core/config_compose/teclaw_paths.py` is untouched, every resources endpoint
is untouched, and `core/services/resource_file_service.py` is untouched. One
test asserts the property that matters — an installed tool never appears in a
resources listing — without any code existing to make it true.

## Dependencies

None. `hashlib` for the md5, `shlex` for the chmod quoting.

## Risks & Mitigations

- **Risk:** the table says a tool is installed and the container disagrees.
  **Mitigation:** `drift()` compares the table against `engine.list`, and
  `replace_all` computes removals from the table so the platform's own tools are
  always re-asserted.
- **Risk:** the model cannot find a tool, because v1 has no `PATH` entry.
  **Mitigation:** the default-skillset skill (spec D-8, out of scope here) is
  the v1 answer, and the cost is written into the user manual rather than left
  to be discovered. Schema §3.7's `PATH` promise is corrected in the same pass.
- **Risk:** `name` reaches a shell.
  **Mitigation:** W1 forbids path separators and enforces per-bot uniqueness,
  and the command is `shlex.quote`d regardless. A test passes a hostile name.
- **Risk:** the OSS copy and the container drift apart — the store has a tool
  the container lost, or vice versa.
  **Mitigation:** `drift()` compares the table against the family's `list`, and
  `replace_all` re-asserts every declared tool from the platform's own bytes, so
  recovery never depends on the user's source URL still serving them.
- **Risk:** an OSS object is orphaned when a tool is removed or a bot is deleted.
  **Mitigation:** `remove` deletes the object with the row, and the creation
  cleanup path removes both; a test covers each.
- **Risk:** the extra OSS write doubles the bytes moved on install.
  **Mitigation:** accepted (spec D-4). On teclaw it is not redundant at all — the
  OSS object *is* the delivery. On ARCA it buys re-delivery without re-fetching a
  source that may have rotated, which is worth one write of a ≤200 MiB file at
  install time.
- **Risk:** a partial `replace_all` leaves some tools new and some old.
  **Mitigation:** per-tool outcomes in one report, and the table records only
  what landed — so the next apply converges the remainder.
- **Risk:** two callers drift — the API validating differently from apply.
  **Mitigation:** validation lives in the service, and a test calls both entry
  points against the same hostile declaration and asserts the same refusal.

## Alternatives Considered

- **A start-command prologue** (rev 1). Rejected: an arrangement in platform
  code, not a protocol; effective only at the next provisioning.
- **A `cli_tools` domain on `EngineRuntimeProjection`** (rev 2). Rejected: that
  seam carries platform state a runtime must be told about and reconciled
  against; a tool has none of that.
- **The resources write chain with no platform record** (rev 3). Rejected: a
  tool has metadata the platform must keep.
- **A backend-owned `tools/` namespace** (rev 4). Rejected: it made the backend
  know a directory it has no use for, and it could not work for teclaw at all,
  whose layout is not ours. Name-addressing removes the namespace, the
  `to_engine_relative` change and every isolation mechanism together.
- **Filtering CLI tools out of the resources endpoints.** Rejected twice over:
  unnecessary once tools are not in the workspace, and `_HIDDEN_DIRNAMES`
  already shows how a filter decays (it guards the root listing only).
- **Composing teclaw's refs by gathering from the engine at promotion time**
  (rev 5). Rejected: it answers only the promotion case. A live CLI update or a
  manifest apply composes an artifact immediately, and there is nothing for it to
  reference unless the platform already holds the bytes.
- **Storing the bytes only for teclaw.** Tempting, since only teclaw's artifact
  needs a ref. Rejected: it splits the install pipeline in two by family for a
  saving of one write, and it gives up ARCA's re-delivery-without-refetch.
- **Putting the tools directory on `PATH` now.** Deferred per D-8; engine-side,
  and reversible without touching the schema, the API, the table or the
  artifact contract.

## Rollout

No flag of its own, and **not** behind `teclaw_platform_managed`: `cli_tools`
is always platform-managed, like `mcp` (spec D-6). The table is additive and the
API is new, so nothing existing changes shape.

```bash
# local bootstrap emits the new table via create_all; no migration file
uv run pytest tests/community/core/bot_config_manifest \
              tests/community/core/config_compose \
              tests/community/core/resources \
              tests/community/core/service_bot \
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
def test_no_delivery_port_signature_takes_a_path(): ...
def test_delivery_port_has_no_get(): ...
def test_bytes_are_written_to_oss_before_delivery(): ...
def test_remove_deletes_the_oss_object_with_the_row(): ...
def test_replace_all_reports_per_tool_on_partial_failure(): ...
def test_drift_reports_a_table_row_the_engine_does_not_have(): ...
```

```python
# tests/community/core/resources/test_cli_tools_absent_from_listings.py (new)
def test_installed_tool_never_appears_in_a_resources_listing(): ...
def test_no_resources_file_was_modified_by_this_feature(): ...
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
```

```python
# tests/community/core/service_bot/test_teclaw_cli_tool_promotion.py (new)
def test_promotion_copies_objects_to_the_new_stage_prefix(): ...
def test_promotion_never_calls_the_engine(): ...
def test_draft_and_verify_snapshots_do_not_share_objects(): ...
def test_md5_and_version_come_from_the_table_not_a_rehash(): ...
def test_promotion_of_a_bot_with_no_tools_is_byte_identical(): ...
```

```python
# tests/community/core/bot_config_manifest/test_iteration1_ordering.py (extend)
def test_cli_tools_is_on_container_on_arca(): ...
def test_cli_tools_is_pre_container_on_teclaw_under_both_switch_positions(): ...
def test_teclaw_creation_carries_tools_in_its_first_artifact(): ...
```

Also extended: a test pinning that no deploy-path file (beyond the promotion
one), no `core/skill_center/*` file, no `teclaw_paths.py` change and no
resources endpoint was modified.
