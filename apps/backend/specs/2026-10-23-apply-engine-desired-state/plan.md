# Plan: The apply engine takes a typed desired state

## Approach

Three moves, in dependency order.

1. **Extract the apply engine** into `core/apply_engine/` and give it a
   typed input. Everything that runs *after* a desired state exists moves
   there: the model, the materialiser contract, the orchestrator, the six
   materialisers, their delegates and narrow ports, the delivery strategies,
   the apply run (service, task, lock reaper, termination, report codec,
   repository models) and the construct/capability enums. What runs
   *before* stays in `core/bot_config_manifest/`: storage, schema
   validation, the source grammar, the fetch funnel, and a new **producer**
   that turns a validated document into a `DesiredState`. The run service
   starts from a `StateRef(producer, reference)` and resolves the producer
   from a registry; the Manifest producer is one entry, the genome producer
   another.
2. **Totality.** `script: null` is handled by the producer. Resources
   converge by *clear then write* through an engine-owned upload ledger
   (engine adapter capability + endpoint, backend resource road, port,
   materialiser).
3. **Genome.** The registry's `compile` renders a revision into the same
   model through a genome producer; drift is observed by reading the bot
   and comparing with `active`; `applied_revision`, `by_apply_reference`,
   `apply_reference`, `document_digest` and `document_json` go. The
   applier seam stays bound to `PendingDeliveryApplier`.

Names are kept where a rename would be churn without a behaviour change:
`ManifestCategory`, `ManifestSection`, `ApplyReport`, `ApplyContext`, the
HTTP paths and the table names. The service and protocol are renamed
(`ApplyRunService`, `ApplyRunServiceProtocol`) because "config manifest
apply service" would be a lie once it runs genome states; #3072 renamed the
same way.

## Affected Components

- `core/apply_engine/` (new) — model, contract, orchestrator, materialisers,
  ports/delegates, delivery strategies, run service, task, report codec,
  repository models, constructs and capabilities, delivery mode.
- `core/bot_config_manifest/` — loses the above; gains `producer/` and
  `sources/` (the fetch funnel, moved out of `apply/`); `schema/validator`
  accepts `script: null`; `cli_tools/` stops fetching.
- `core/bot_genome/` — `produce.py` replaces `compile.py`; `drift`;
  removals; DDL; promotion service and applier seam on the model.
- `core/repository/` — apply protocols/impls move with the engine (module
  path only); genome promotion protocol/impl lose `by_apply_reference`,
  `document_*`, `apply_reference`.
- `core/devices/services/` (`DeviceFileSystem` and its four
  implementations), `core/services/resource_file_service.py`,
  `core/ports/resource_file_port.py`, `apply_engine/resource_files.py`,
  `apply_engine/managed_files/ports.py` (`PlatformResource` moves with the
  platform-managed ports).
- `adapters/http/openapi_v1/bots/` — apply routes pass a `StateRef`;
  `schemas_config_manifest_apply` gains `state`; `schemas_genome` and
  `genome.py` lose the promotion's document fields; resource upload routes
  pass an origin.
- `di/modules/` — `manifest_delivery_module` → `apply_engine_module`
  (strategies, ports, producers registry); `bot_management_module` provides
  `ApplyRunService`; `bot_genome_module` registers the genome producer and
  drops the apply-service dependency.
- `engine/adapter/` — `Capability.FILE_UPLOADS_CLEAR`, the upload ledger,
  `upload(origin)`, `list_uploads`, `clear_uploads`, router endpoints, both
  engines' matrices and plugins, tests.
- Docs: listed in the spec; ADR 0022.

## The model (`core/apply_engine/desired_state.py`)

Frozen dataclasses and `StrEnum`s; no pydantic (this is an in-process
contract, never serialised; the apply engine's report types are dataclasses
already).

```python
class StoredContent:            digest: str                      # sha256:<hex64>
class EntryFailure:             identity: str; reason: str       # what the producer could not produce
class Declaration(Generic[E]):  entries: tuple[E, ...]; failures: tuple[EntryFailure, ...] = ()

class DesiredScript:            body: str                        # substituted, inline
class DesiredIdentityFile:      type: str; content: StoredContent; note: str | None = None

class SkillOrigin(StrEnum):     LOCAL, CENTER, REPO
class LocalSkillPackage:        tree: StoredContent              # acm-tree-v1 of the package files, SKILL.md inside
class CenterSkillVersion:       version: str
class RepoSkillPath:            path: str
class DesiredSkill:             name: str; source: LocalSkillPackage | CenterSkillVersion | RepoSkillPath
                                note: str | None = None
                                @property origin -> SkillOrigin

class DesiredResourceFile:      path: str; content: StoredContent; note: str | None = None
class DesiredResourceTree:      path: str  # ends with "/"; tree: StoredContent; note: str | None = None
DesiredResource = DesiredResourceFile | DesiredResourceTree

class McpTransport(StrEnum):    SSE, STREAMABLE_HTTP
class McpEndpointEnv(StrEnum):  PROD, PRE
class McpHeader:                name: str; value: str
class McpOverride:              url: str | None; headers: tuple[McpHeader, ...]
                                endpoint_env: McpEndpointEnv | None; transport: McpTransport | None
                                def as_config(self) -> dict[str, Any]   # the mapping the MCP ports take today
class DesiredMcpServer:         server_code: str; override: McpOverride | None

class UnpackKind(StrEnum):      ZIP = "zip", TAR_GZ = "tar.gz"
class DesiredCliTool:           name: str; content: StoredContent; pin: str | None   # declared digest; None over git
                                subpath: str | None; unpack: UnpackKind | None; version: str | None
                                note: str | None = None
class DesiredEngineConfig:      config: Mapping[str, Any]        # keys undecided (G-4, #2858)

class DesiredState:
    script:        Declaration[DesiredScript] | None             # 0 or 1 entries
    identity:      Declaration[DesiredIdentityFile] | None
    resources:     Declaration[DesiredResource] | None
    skills:        Declaration[DesiredSkill] | None
    mcp:           Declaration[DesiredMcpServer] | None
    engine_config: Declaration[DesiredEngineConfig] | None       # 0 or 1 entries
    cli_tools:     Declaration[DesiredCliTool] | None
    def declaration(self, construct: ApplyConstruct) -> Declaration[Any] | None
    EMPTY: ClassVar  # nothing declared (a bot with no document)
```

`None` = not declared (area untouched, nothing reported);
`Declaration(entries=())` = declared empty (area emptied). `__post_init__`
on each entry checks the invariants that today live in the grammar: digest
form, tree paths end with `/`, file paths do not, identities unique within
a declaration (duplicates are a producer bug, raised not reported).

`ContentReader` (`read(digest) -> bytes`, raises `ContentMissing`) is the
engine's read port; `ContentStoreProtocol` satisfies it structurally. It is
one more field of `MaterialiserPorts`.

## The contract (`core/apply_engine/registry.py`)

```python
class Materialiser(Protocol[E]):
    construct: ApplyConstruct
    async def resolve(self, ctx: ApplyContext, entries: Sequence[E]) -> ResolveResult
    async def plan(self, ctx: ApplyContext, intents: Sequence[Intent]) -> CategoryPlan
    async def write(self, ctx: ApplyContext, plan: CategoryPlan) -> Sequence[EntryResult]
```

`Intent`, `ResolveResult`, `ResolveFailure`, `PlannedEntry`, `CategoryPlan`
are unchanged. The orchestrator's `apply(ctx, state: DesiredState, ...)`
walks the steps, takes `state.declaration(construct)`, skips `None`, and
feeds `declaration.failures` into the same abort path as a `ResolveResult`
with failures (named entries `FAILED`, neighbours `SKIPPED`), before
calling `resolve` with `declaration.entries`.

`ApplyContext` loses `budget` and `source_session` (producer-private now)
and keeps `capability_state`. `entry_identity` (outcomes) takes the typed
entry's identity (`type`, `path`, `name`, `server_code`, `"script"`).

### What each materialiser keeps in `resolve`

| Materialiser | Keeps | Leaves (to the producer) |
|---|---|---|
| script | capability gate | `body` key, placeholder substitution |
| mcp | duplicate, permission (actor), `validate_bot_override` on `override.as_config()` | `server_code`/`config` keys, config normalisation |
| identity | duplicate, reserved names, `legal_identity_types(engine)`, UTF-8 decode of `content.read()` | `type`/`content`/`from`/`source`, fetch, `single()` |
| skills | duplicate, `validate_directory` on the decoded tree, name match, canonical zip; Center/repo → `ResolveFailure("delivered by RSI-04")` | `name`/`source`/`subpath`/`unpack`, fetch, archive kind, subtree selection |
| resources | path re-validation, duplicate, nesting ban, admission per member (bytes from the store, trees decoded) | `path`/`content`/`unpack`/`strip_components`, fetch, `members()` |
| cli_tools | duplicate; builds `CliToolDecl(content=…)` | the digest belt, `from_entry` |

`plan` and `write` are unchanged except resources (§ Totality) and
cli_tools (`CliToolService._acquire` reads `decl.content` from the store;
`CliToolDecl.entry` goes, `CliToolDecl.content: StoredContent | None`
replaces it, `None` on the upload road).

## Producers

```python
class StateRef:                 producer: str; reference: str      # "manifest"/"" ; "genome"/<promotion id>
class Produced:                 state: DesiredState; sources: tuple[SourceResolution, ...] = ()
class DesiredStateProducer(Protocol):
    kind: str
    async def produce(self, ctx: ApplyContext, reference: str) -> Produced
```

`ApplyRunService` takes `producers: Mapping[str, DesiredStateProducer]`
(an injector map, like the delivery strategies). `start_apply(...,
state: StateRef, ...)` validates the kind up front, stores
`state` in the task payload (two more identifier strings), and the worker's
`_rebuild` calls `producers[state.producer].produce(ctx, state.reference)`
in place of `_parsed_or_empty`. `dry_run` does the same. An unknown kind is
a `ValueError` at `start_apply`, like a phase/trigger mismatch.

### Manifest producer (`core/bot_config_manifest/producer/`)

- `declared.py` — `declared_entries(parsed, construct)` moved from the
  orchestrator. Fix: a section key present with `null` is declared-empty
  (`"script" in parsed and parsed["script"] is None` → `[]`); absent stays
  `None`. `schema/validator._validate_script` keeps accepting `None`;
  `schema/README` (or the docstring the docs cite) says what null means.
- `session.py` — `ProduceSession`: the per-apply `ApplyFetchBudget`
  (`APPLY_BUDGET_S`, `APPLY_FETCH_TOTAL_LIMIT`) and `SourceSession`
  (`parsed["sources"]`, baselines from the run service's recent reports,
  git client), created per `produce`, closed before returning. It satisfies
  `FetchContext` (moved to `sources/`), so the fetchers do not change.
- `produce.py` — `ManifestStateProducer(kind="manifest")`: `manifests.get`
  → `{}`/`validate(...).parsed` (today's `_parsed_or_empty`), then one entry
  producer per declared construct, in `APPLY_ORDER`. Not declared → `None`.
- `entries/script.py` — body, `placeholders.resolve(engine_type, env, tenant)`.
- `entries/mcp.py` — `server_code`, `config` normalised into `McpOverride`
  (headers as a tuple, enums for transport/endpoint env).
- `entries/identity.py` — inline `content:` → `store_produced(origin=
  "manifest/inline", category=identity, entry_identity=type)`; fetched →
  resolver + `single()` + `file()`; emits `DesiredIdentityFile(type,
  StoredContent(digest), note=delivery.note())`.
- `entries/skills.py` — fetch; tree vs blob road; subtree selection; package
  validation (`SkillPackageValidator`) for the per-entry refusal; files the
  package as **acm-tree-v1** under the delivery's receipt URL (today the git
  road files a canonical zip; the tree is what the genome stores, and
  `BlobDelivery.members` already decodes trees for `keep_last`); emits
  `DesiredSkill(name, LocalSkillPackage(tree))`.
- `entries/resources.py` — file: inline → produced content; fetched →
  `single()` + `file()`. Directory: `archive_refusal` (OSS), fetch,
  `members(unpack, strip_components)`, `canonical_tree_bytes` filed (today
  a no-op on the blob road; now a real `store_produced` so the tree digest
  is reachable), emits `DesiredResourceTree`. Admission stays in the
  materialiser.
- `entries/cli_tools.py` — the digest belt (`declared_protocol` + git
  exemption), fetch through the same funnel (`FetchCategory.CLI_TOOLS`),
  `single()`, `file()`; pinned fast path: when `content.is_reachable(scope,
  pin)` the entry is emitted without a fetch; emits `DesiredCliTool`.
- `entries/engine_config.py` — `DesiredEngineConfig(config)`.

Each entry producer returns `Declaration[...]` with per-entry `failures`
for what today is a `ResolveFailure` raised by grammar or fetch (fetch
failed with `fail`, archive refusal, invalid package, not a single file,
unknown source name, budget expired, …). A producer exception is a bug and
propagates; the run service records it as a FAILED report with no
categories, as it does today for an orchestrator exception.

The fetch funnel moves `apply/{source_resolver,source_fetchers,
entry_delivery,source_session,budget,fetch_context}.py` →
`bot_config_manifest/sources/` unchanged.

### Genome producer (`core/bot_genome/produce.py`)

- `produce_state(spec: GenomeSpec, content: ContentReader) -> DesiredState`
  (pure; replaces `compile_spec`): persona → identity files; skills by
  origin (`LocalSkill.digest` → `LocalSkillPackage`, `CenterSkill` →
  `CenterSkillVersion`, `RepoSkill` → `RepoSkillPath`); resources by
  trailing slash; `tools.mcp` → `DesiredMcpServer` with `McpOverride` from
  `McpConfig`; script body read from the store; `cli_tools`,
  `engine_config` not declared; `memory` ignored until RSI-05.
- `GenomeStateProducer(kind="genome")`: `produce(ctx, promotion_id)` reads
  the promotion row (must exist and be `applying`), resolves its revision,
  and returns `Produced(produce_state(...))`. Registered in the producer
  map; no production caller until RSI-04.
- `GenomeRegistryProtocol.compile(bot, rev) -> DesiredState`;
  `CompiledManifest` and `compile.py` are deleted.

### Equivalence test

`tests/community/core/bot_config_manifest/producer/test_equivalence.py`:
for each `examples/manifest-v1/*` case with `revision.json`, run the
Manifest producer on `manifest.yaml` with the example `sources` served by
the existing fakes (`FakeObjectStore`, `FakeGitClient`, `BlobDir`), run
`produce_state` on the revision, and assert the states equal after
stripping `note`. The `compiled.yaml` files are deleted and
`manifest-v1-compatibility.md` describes this test instead of M′.

## Totality

### Engine adapter (`engine/adapter/src/engine/community/`)

- `core/engine/capability.py`: `FILE_UPLOADS_CLEAR = "file.uploads.clear"`;
  added to the `_file` domain tuple in `core/engine/base.py` and to both
  engines' matrices.
- `core/file/upload_ledger.py` (new, engine-neutral): `UploadLedger(path)`
  with `record(resolved_path, origin)`, `paths(origin) -> list[str]`,
  `clear(origin) -> list[str]` (unlinks each recorded file that still
  exists, drops the entry; a missing file is dropped silently). One JSON
  object `{path: origin}`, rewritten atomically (temp file + rename).
- `core/file/protocol.py`: `upload(target_path, content, *, origin: str |
  None)`, `list_uploads(origin: str | None) -> list[str]`,
  `clear_uploads(origin: str | None) -> list[str]`; models gain
  `UploadsResult(paths)`.
- Plugins: openclaw `_FilePortMixin` keeps the ledger at
  `workspace_root().parent / ".avernet-uploads.json"` (its `_convert_path`
  result is the recorded key); claude_code keeps it under its active root.
  Adapters and plugin-api protocols forward the three members.
- Router: `POST /api/file/upload` takes an optional `origin` form field
  (recorded only when present — a bare upload is not ledgered);
  `POST /api/file/uploads/list {origin?}` → `{origin, paths}`;
  `POST /api/file/uploads/clear {origin?}` → `{origin, removed}`. Both new
  routes check `FILE_UPLOADS_CLEAR` (`list` is part of the same
  capability).
- Tests: `api/tests/test_file_router.py` (dispatch, 501 without the
  capability), `core/file/tests/test_upload_ledger.py`, plugin tests for
  both engines (record on upload, clear by origin, clear all, a file deleted
  by the bot in between).

### Backend resource road

- `core/devices/services/device_filesystem.py`: `write_file(path, content,
  *, origin: str | None = None)` (None = not ledgered, for callers that
  are not uploads, e.g. `.keep`), `list_uploads(origin) -> tuple[str, ...]
  | None`, `clear_uploads(origin) -> tuple[str, ...] | None`; `None` means
  the transport cannot (the capability is absent: a 501 from the adapter,
  or the teclaw device road). BaaS and local (BaaS mode) send the field and
  call the two routes; local pathlib mode keeps an in-process ledger;
  teclaw answers `None`.
- `UploadOrigin(StrEnum)`: `MANIFEST = "manifest"`, `USER = "user"`
  (`core/services/resource_file_service.py`). `ResourceFileService.
  upload_file(..., origin: UploadOrigin)`, `list_uploads`, `clear_uploads`.
  Callers: console `file_router` and the OpenAPI resources router pass
  `USER`; the legacy console resources router's write path passes `USER`
  where it goes through `DeviceFileSystem.write_file`.
- `ResourceFilePort`: `upload_file(..., origin)`, `list_uploads(origin)`,
  `clear_uploads(origin)`. `DeviceResource` forwards. `PlatformResource`:
  `list_uploads` = the category listing's paths; `clear_uploads` = delete
  each; origin ignored (the store is platform-written only).
- `ResourcesMaterialiser`: `plan` calls `list_uploads(MANIFEST)`; when it
  answers, entries are classified from the listing (no per-member `exists`
  probes) and `removals = sorted(listed − declared member paths) +
  declared trees`; when it answers `None`, today's per-member `exists` and
  per-entry removals, plus a category note `"cross-entry removals are not
  available on this transport"`. `write`: `clear_uploads(MANIFEST)` first
  (when available), then declared tree deletes as today, then uploads with
  `origin=MANIFEST`. A failed clear fails the category before any write.

## Genome

- `registry.drift(bot) -> Drift | None`: `active = refs.get("active")`;
  `None` when there is no `active`; otherwise `captured = await
  capture_spec(self._state_reader(), bot, validator=...)`, `observed =
  revision_id(captured.spec, active_rev.policy)`, `None` when equal to
  `active.revision_id`, else `Drift(active=…, observed=…)`. `CaptureRefused`
  propagates. Nothing is recorded.
- `promotion/service.py`: the drift check becomes `drift = await
  self._registry.drift(bot)` for non-user actors; `Drifted(active,
  observed)`; the row no longer carries `document_*`; `finish` no longer
  takes `apply_reference`. `RevisionApplier.preflight/start` take a
  `DesiredState`; `ApplyResult.reference` goes (`kind`, `detail` stay).
- `errors.py`: `Drifted(active, observed)`; `errors_genome.py` data
  `{active, observed}`.
- Repository: `GenomePromotionRepositoryProtocol` loses
  `by_apply_reference`; `finish(status, apply_detail)`; `GenomePromotionRow`
  / `GenomePromotionModel` lose the three columns and the index; DDL
  `sql/2026_10_19_bot_genome.sql` edited in place (comment block 119–123
  rewritten).
- `models.py`: `Promotion` loses `document_digest`, `apply_reference`,
  `document`; `CompiledManifest` deleted; `ApplyResult.reference` deleted.
- HTTP: `PromotionPayload` loses `document_digest`, `apply_reference`,
  `document`; `genome.py` promote/get/list adjusted; `test_genome_routes`,
  `test_openapi_genome` (the `_seed_deliverable` applier override takes the
  new signature).
- Registry constructor loses `applies`; `bot_genome_module` stops importing
  the Manifest apply protocol and provides `GenomeStateProducer`.

## Data Model Changes

- `ac_genome_promotion`: drop `document_digest`, `document_json`,
  `apply_reference`, `idx_genome_promotion_bot_apply` (DDL edited in place;
  not deployed, per #3072).
- `ac_bot_config_manifest_apply.report` JSON gains `"state": {"producer",
  "reference"}`; the codec defaults missing keys to `manifest`/`""`. No
  column change (`apply_trigger` keeps `String(32)`; a genome run's trigger
  is `"promotion"`, which fits).
- Apply task payload gains `state_producer`, `state_reference`.
- `ac_manifest_content` gains rows for inline identity/resource content
  (`produced://manifest/inline`) and for resource trees
  (`produced://manifest/tree`); no schema change.

## API / Interface Changes

- `api/apply_run_service.py` re-exports `ApplyRunServiceProtocol` (was
  `api/bot_config_manifest_apply_service.py` / `BotConfigManifestApplyServiceProtocol`):
  `start_apply(..., state: StateRef, ...)`, `dry_run(..., state: StateRef)`;
  `get_apply`, `last_apply`, `recent_applies(limit)` (new, for the
  producer's baselines), `materialised_constructs`, `delivery_for_engine`,
  `delivery_for_bot` unchanged.
- HTTP apply payloads (`ConfigManifestApply`) gain `state: {producer,
  reference}`; everything else unchanged. `PromotionPayload` drops three
  fields. `drifted` 409 data is `{active, observed}`.
- Engine adapter HTTP: `/api/file/upload` optional `origin`;
  `/api/file/uploads/list`, `/api/file/uploads/clear`; capability
  `file.uploads.clear`.

## Key Files & Functions

### New: `core/apply_engine/`
`__init__.py`, `README.md` (`## Context Boundary`), `desired_state.py`,
`producer.py` (`DesiredStateProducer`, `StateRef`, `Produced`),
`constructs.py` (from `bot_config_manifest/capabilities.py`:
`ManifestCategory`, `ManifestSection`, `Construct`, `ManifestCapabilities`,
`resolve_capabilities`, `SourceCell`), `delivery_mode.py`, `context.py`,
`order.py`, `outcomes.py` (+ `StateRef` on `ApplyReport`, `as_payload`),
`registry.py`, `orchestrator.py`, `materialisers/{script,mcp,identity,
skills,resources,cli_tools}.py`, `delivery.py` (`MaterialiserPorts` +
`content`), `activation_delegates.py`, `identity_files.py`,
`resource_files.py`, `skill_package_upload.py`, `managed_files/` (store,
reader, ports — moved whole; `PlatformResource` lives here),
`redeliver.py`, `carry_forward.py`, `triggers.py`, `apply_task.py`,
`run/{service,report_codec,lock_reaper,termination}.py`,
`repository/apply_models.py`. The `cli_tools/` package moves too
(`CliToolService` is a delivery component the management API also uses;
`declarations.from_entry` moves to the Manifest producer).

### `core/bot_config_manifest/`
`producer/{__init__,declared,session,produce}.py`,
`producer/entries/{script,mcp,identity,skills,resources,cli_tools,
engine_config}.py`; `sources/` (moved funnel); `schema/validator.py`
(`script: null` doc); `creation.py`, `create_job.py` (import `apply_engine`,
pass `StateRef.manifest()`); `README.md` boundary rewritten.

### `core/bot_genome/`
`produce.py` (new), `compile.py` (deleted), `registry.py` (`compile`,
`drift`, no `applied_revision`, no `applies`), `genome_registry_protocol.py`,
`models.py`, `errors.py`, `promotion/{applier,service}.py`,
`repository/models.py`, `sql/2026_10_19_bot_genome.sql`, `README.md`.

### Repository
`protocols/bot/{config_manifest_apply,genome}.py`,
`implementations/bot/{config_manifest_apply,genome}.py`.

### HTTP
`adapters/http/openapi_v1/bots/{config_manifest_apply,config_manifest_support,
create_with_manifest,schemas_config_manifest_apply,genome,schemas_genome}.py`,
`adapters/http/openapi_v1/errors_genome.py`, resource upload routers
(origin), `api/apply_run_service.py`.

### DI
`di/modules/apply_engine_module.py` (from `manifest_delivery_module.py`:
ports, strategies, producers map), `manifest_fetch_module.py` (the
Manifest producer and its factories), `bot_management_module.py`
(`ApplyRunService`, seam, task lifecycle), `bot_genome_module.py`,
`agentswarm/__init__.py` (type rename).

### Engine adapter
`core/engine/{capability,base}.py`, `core/file/{protocol,models,
upload_ledger}.py`, `api/file/{router,schemas}.py`, `plugin_api/{openclaw,
claude_code}/file.py`, `core/adapters/{openclaw,claude_code}/file.py`,
`plugins/{openclaw,claude_code}/_file.py`, `engines/{openclaw,claude_code}/
engine.py`, tests beside each.

### Tests (moves)
`tests/community/core/bot_config_manifest/apply/*` → split:
`tests/community/core/apply_engine/` (engine, service lifecycle, strategy,
task, materialisers, delivery, teclaw, orchestrator-generic, port
forwarding, capability contract, managed_files, cli_tools) and
`tests/community/core/bot_config_manifest/sources/` (resolver, fetchers,
session, entry delivery). `_fakes.py` (1308 lines) splits into
`apply_engine/_fakes.py` (services, ports, strategies) and
`bot_config_manifest/_fakes.py` (content, object store, credentials, git).
New: `bot_config_manifest/producer/test_*.py` (one per entry producer,
carrying the grammar/fetch refusal cases lifted from the materialiser
tests), `test_equivalence.py`; `apply_engine/test_desired_state.py`;
`bot_genome/test_produce.py` (replaces `test_compile.py`),
`test_registry_service.py` (`drift`), `test_promotion_service.py`;
`di/test_apply_engine_module_wiring.py`.

## Testing Strategy

- **Behaviour lock first.** Before moving anything, run the full Manifest
  suite on `dev` and record the pass count; every task group ends with the
  same count (moved tests included) plus the group's new tests.
- **Producer parity.** Each materialiser test that asserts a grammar or
  fetch refusal today becomes a producer test asserting the same
  `EntryFailure` reason text; the materialiser test keeps the engine-side
  cases with typed entries built directly.
- **Equivalence** across the two producers on the contract examples.
- **Engine adapter**: router contract tests with a fake engine, ledger unit
  tests, per-plugin I/O tests under a temp root; `./scripts/ci_test.sh`.
- **Genome**: route, endpoint, repository (DDL/ORM pin), service and
  conformance tests; `drift` with the `FakeReader`; promotion refusals.
- **Architecture**: module boundaries (`apply_engine` added to the
  significant set; `bot_genome` and `bot_config_manifest` boundaries
  updated), narrow-ports declaration, service-API conformance, HTTP-only
  adapter, no module-level service instances.
- Gates: `ruff check`, `ruff format --line-length 120 --check` on new
  files, line counts, `DEPLOY_PROFILE=test pytest tests/community`, the
  engine adapter suite, `./scripts/ci_test.sh` coverage thresholds.

## Risks & Mitigations

- **Blast radius of the move** (≈40 source files, ≈45 test files). Use
  `git mv` so history follows; one commit per moved unit; no shims, so a
  stale import fails at collection, not at runtime.
- **Behaviour drift in the resolve split.** The producer tests are lifted
  from the materialiser tests case by case; the equivalence test catches
  shape mistakes on both producers at once.
- **Content-store growth**: inline content and resource trees are filed
  now. Both are bounded by the existing per-entry caps; retention is
  unconditional already (RSI-03 §9.1).
- **Skills receipt format change** (canonical zip → acm-tree). `keep_last`
  reads of receipts filed before this change still decode: `BlobDelivery.
  members` falls back to the archive road for a zip.
- **Resources clear on a transport without the capability** degrades to
  today's behaviour with a note; nothing new is destroyed.
- **Clear-then-write window**: a failed upload after a clear leaves fewer
  files than before, reported as `partially_written`; the next apply
  converges. Accepted with the issue owner.
- **Pre-existing uploads** are not ledgered until re-uploaded; stated in
  the spec.
- **Drift by capture is heavier** than a row lookup (reads every identity
  file, skill package, resource and MCP override). It runs at promotion for
  non-user actors only; run start (RSI-08) will reuse it.
- **Contract folder edits** (`compiled.yaml` retired, compatibility doc
  rewritten): flagged for the owner.

## Open Questions

- One PR or three (spec § Open Questions). Groups 1–2, 3 and 4–5 below are
  separable.
- Whether `ManifestCategory`/`ManifestSection` should be renamed to
  `Category`/`Section` now that they live in `apply_engine`. Left as is;
  a rename is a mechanical follow-up.
