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
   validation, the source grammar, the fetch funnel, and a new **compiler**
   that turns a validated document into a `DesiredState`. The run service
   starts from a typed `StateRef` and resolves the compiler by its type;
   the Manifest compiler is one entry, the genome compiler another.
2. **Totality.** `script: null` is handled by the compiler. Resources
   converge by *clear then write* of the resources root (`data/`) with the
   device road's existing tree delete; the Manifest validator keeps
   resource paths under the root; the platform's resource rows follow.
3. **Genome.** The registry's `compile` renders a revision into the same
   model; drift is observed by reading the bot and comparing with
   `active`; `applied_revision`, `by_apply_reference`, `apply_reference`,
   `document_digest` and `document_json` go. The applier seam stays bound
   to `PendingDeliveryApplier`.

Names are kept where a rename would be churn without a behaviour change:
`Category`, `Section`, `ApplyReport`, `ApplyContext`, the
HTTP paths and the table names. The service and protocol are renamed
(`ApplyRunService`, `ApplyRunServiceProtocol`) because "config manifest
apply service" would be a lie once it runs genome states; #3072 renamed the
same way.

## Affected Components

- `core/apply_engine/` (new) — model, contract, orchestrator, materialisers,
  ports/delegates, delivery strategies, run service, task, report codec,
  repository models, constructs and capabilities, delivery mode.
- `core/bot_config_manifest/` — loses the above; gains `compiler/` and
  `sources/` (the fetch funnel, moved out of `apply/`); `schema/validator`
  accepts `script: null` and enforces the resources root; `cli_tools/`
  stops fetching.
- `core/bot_genome/` — `compile.py` rewritten onto the model; `drift`;
  removals; DDL; promotion service and applier seam on the model.
- `core/repository/` — apply protocols/impls move with the engine (module
  path only); genome promotion protocol/impl lose `by_apply_reference`,
  `document_*`, `apply_reference`.
- `core/resources/` — `ResourceService` gains a "replace the bot's file rows
  under a prefix" operation the materialiser calls (delete rows under
  `data/`, insert one per written file, source `manifest`).
- `core/ports/resource_file_port.py`, `apply_engine/resource_files.py`,
  `apply_engine/managed_files/ports.py` — `clear_root` and `list_root`.
- `adapters/http/openapi_v1/bots/` — apply routes pass a `StateRef`;
  `schemas_config_manifest_apply` gains `state`; `schemas_genome` and
  `genome.py` lose the promotion's document fields.
- `di/modules/` — `manifest_delivery_module` → `apply_engine_module`
  (strategies, ports, compilers map); `bot_management_module` provides
  `ApplyRunService`; `bot_genome_module` registers the genome compiler and
  drops the apply-service dependency.
- Docs: listed in the spec; ADR 0022; the file-API expectations document
  for the engine teams (Chinese), delivered outside the repo.

## The model (`core/apply_engine/desired_state.py`)

Frozen dataclasses and `StrEnum`s; no pydantic (this is an in-process
contract, never serialised; the apply engine's report types are dataclasses
already).

```python
class ContentRef:            digest: str                      # sha256:<hex64>
class EntryFailure:             identity: str; reason: str       # what the compiler could not compile
class Declaration(Generic[E]):  entries: tuple[E, ...]; failures: tuple[EntryFailure, ...] = ()

class DesiredScript:            body: str                        # substituted, inline
class DesiredIdentityFile:      type: str; content: ContentRef; note: str | None = None

class SkillOrigin(StrEnum):     LOCAL, CENTER, REPO
class LocalSkillPackage:        tree: ContentRef              # acm-tree-v1 of the package files, SKILL.md inside
class CenterSkillVersion:       version: str
class RepoSkillPath:            path: str
class DesiredSkill:             name: str; source: LocalSkillPackage | CenterSkillVersion | RepoSkillPath
                                note: str | None = None
                                @property origin -> SkillOrigin

class DesiredResourceFile:      path: str; content: ContentRef; note: str | None = None
class DesiredResourceTree:      path: str  # ends with "/"; tree: ContentRef; note: str | None = None
DesiredResource = DesiredResourceFile | DesiredResourceTree     # paths are under RESOURCES_ROOT ("data/")

class McpTransport(StrEnum):    SSE, STREAMABLE_HTTP
class McpEndpointEnv(StrEnum):  PROD, PRE
class McpHeader:                name: str; value: str
class McpOverride:              url: str | None; headers: tuple[McpHeader, ...]
                                endpoint_env: McpEndpointEnv | None; transport: McpTransport | None
                                def as_config(self) -> dict[str, Any]   # the mapping the MCP ports take today
class DesiredMcpServer:         server_code: str; override: McpOverride | None

class UnpackKind(StrEnum):      ZIP = "zip", TAR_GZ = "tar.gz"
class DesiredCliTool:           name: str; content: ContentRef; pin: str | None   # declared digest; None over git
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
form, tree paths end with `/`, file paths do not, resource paths under the
root, identities unique within a declaration (duplicates are a compiler
bug, raised not reported).

`RESOURCES_ROOT = "data/"` is a constant of the apply engine (the
materialiser clears it; the Manifest validator and the genome spec checks
import it).

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

`ApplyContext` loses `budget` and `source_session` (compiler-private now)
and keeps `capability_state`. `entry_identity` (outcomes) takes the typed
entry's identity (`type`, `path`, `name`, `server_code`, `"script"`).

### What each materialiser keeps in `resolve`

| Materialiser | Keeps | Leaves (to the compiler) |
|---|---|---|
| script | capability gate | `body` key, placeholder substitution |
| mcp | duplicate, permission (actor), `validate_bot_override` on `override.as_config()` | `server_code`/`config` keys, config normalisation |
| identity | duplicate, reserved names, `legal_identity_types(engine)`, UTF-8 decode of `content.read()` | `type`/`content`/`from`/`source`, fetch, `single()` |
| skills | duplicate, `validate_directory` on the decoded tree, name match, canonical zip; Center/repo → `ResolveFailure("delivered by RSI-04")` | `name`/`source`/`subpath`/`unpack`, fetch, archive kind, subtree selection |
| resources | duplicate, nesting ban, admission per member (bytes from the store, trees decoded) | `path`/`content`/`unpack`/`strip_components`, fetch, `members()`, the root rule |
| cli_tools | duplicate; builds `CliToolDecl(content=…)` | the digest belt, `from_entry` |

`plan` and `write` are unchanged except resources (§ Totality) and
cli_tools (`CliToolService._acquire` reads `decl.content` from the store;
`CliToolDecl.entry` goes, `CliToolDecl.content: ContentRef | None`
replaces it, `None` on the upload road).

## Compilers

```python
class ManifestStateRef:         kind: ClassVar = "manifest"                 # the bot's stored document
class GenomeStateRef:           kind: ClassVar = "genome"; promotion_id: str
StateRef = ManifestStateRef | GenomeStateRef
    # to_payload() / from_payload(): {"kind": "manifest"} | {"kind": "genome", "promotion_id": …}

class Compiled:                 state: DesiredState; sources: tuple[SourceResolution, ...] = ()
class DesiredStateCompiler(Protocol[R]):
    async def compile(self, ctx: ApplyContext, ref: R) -> Compiled
```

`ApplyRunService` takes `compilers: Mapping[type[StateRef],
DesiredStateCompiler]` (an injector map, like the delivery strategies).
`start_apply(..., state: StateRef, ...)` validates the type up front,
stores `state.to_payload()` in the task payload, and the worker's
`_rebuild` calls `compilers[type(ref)].compile(ctx, ref)` in place of
`_parsed_or_empty`. `dry_run` does the same. An unregistered reference
type is a `ValueError` at `start_apply`, like a phase/trigger mismatch.

### Manifest compiler (`core/bot_config_manifest/compiler/`)

- `declared.py` — `declared_entries(parsed, construct)` moved from the
  orchestrator. Fix: a section key present with `null` is declared-empty
  (`"script" in parsed and parsed["script"] is None` → `[]`); absent stays
  `None`. `schema/validator._validate_script` keeps accepting `None`;
  `schema/README` (or the docstring the docs cite) says what null means.
- `session.py` — `CompileSession`: the per-apply `ApplyFetchBudget`
  (`APPLY_BUDGET_S`, `APPLY_FETCH_TOTAL_LIMIT`) and `SourceSession`
  (`parsed["sources"]`, baselines from the run service's recent reports,
  git client), created per `compile`, closed before returning. It satisfies
  `FetchContext` (moved to `sources/`), so the fetchers do not change.
- `compile.py` — `ManifestCompiler`: `manifests.get` → `{}`/
  `validate(...).parsed` (today's `_parsed_or_empty`), then one entry
  compiler per declared construct, in `APPLY_ORDER`. Not declared → `None`.
- `entries/script.py` — body, `placeholders.resolve(engine_type, env, tenant)`.
- `entries/mcp.py` — `server_code`, `config` normalised into `McpOverride`
  (headers as a tuple, enums for transport/endpoint env).
- `entries/identity.py` — inline `content:` → `store_produced(origin=
  "manifest/inline", category=identity, entry_identity=type)`; fetched →
  resolver + `single()` + `file()`; emits `DesiredIdentityFile(type,
  ContentRef(digest), note=delivery.note())`.
- `entries/skills.py` — fetch; tree vs blob road; subtree selection; package
  validation (`SkillPackageValidator`) for the per-entry refusal; files the
  package as **acm-tree-v1** under the delivery's receipt URL (today the git
  road files a canonical zip; the tree is what the genome stores, and
  `BlobDelivery.members` already decodes trees for `keep_last`); emits
  `DesiredSkill(name, LocalSkillPackage(tree))`.
- `entries/resources.py` — root rule (`path` under `RESOURCES_ROOT`, else
  `EntryFailure("resources_outside_root")`), path re-validation. File:
  inline → produced content; fetched → `single()` + `file()`. Directory:
  `archive_refusal` (OSS), fetch, `members(unpack, strip_components)`,
  `canonical_tree_bytes` filed (today a no-op on the blob road; now a real
  `store_produced` so the tree digest is reachable), emits
  `DesiredResourceTree`. Admission stays in the materialiser.
- `entries/cli_tools.py` — the digest belt (`declared_protocol` + git
  exemption), fetch through the same funnel (`FetchCategory.CLI_TOOLS`),
  `single()`, `file()`; pinned fast path: when `content.is_reachable(scope,
  pin)` the entry is emitted without a fetch; emits `DesiredCliTool`.
- `entries/engine_config.py` — `DesiredEngineConfig(config)`.

Each entry compiler returns `Declaration[...]` with per-entry `failures`
for what today is a `ResolveFailure` raised by grammar or fetch (fetch
failed with `fail`, archive refusal, invalid package, not a single file,
unknown source name, budget expired, …). A compiler exception is a bug and
propagates; the run service records it as a FAILED report with no
categories, as it does today for an orchestrator exception.

The fetch funnel moves `apply/{source_resolver,source_fetchers,
entry_delivery,source_session,budget,fetch_context}.py` →
`bot_config_manifest/sources/` unchanged.

`schema/validator._validate_category_list` for `resources` adds the root
rule (`resources_outside_root`, naming `data/`), beside the existing
relative-path check.

### Genome compiler (`core/bot_genome/compile.py`, rewritten)

- `compile_spec(spec: GenomeSpec, content: ContentReader) -> DesiredState`
  (pure): persona → identity files; skills by origin (`LocalSkill.digest`
  → `LocalSkillPackage`, `CenterSkill` → `CenterSkillVersion`, `RepoSkill`
  → `RepoSkillPath`); resources by trailing slash; `tools.mcp` →
  `DesiredMcpServer` with `McpOverride` from `McpConfig`; script body read
  from the store; `cli_tools`, `engine_config` not declared; `memory`
  ignored until RSI-05.
- `GenomeCompiler` (`DesiredStateCompiler[GenomeStateRef]`):
  `compile(ctx, ref)` reads the promotion row (must exist and be
  `applying`), resolves its revision, and returns
  `Compiled(compile_spec(...))`. Registered in the compilers map; no
  production caller until RSI-04.
- `GenomeRegistryProtocol.compile(bot, rev) -> DesiredState`;
  `CompiledManifest` is deleted.

### Equivalence test

`tests/community/core/bot_config_manifest/compiler/test_equivalence.py`:
for each `examples/manifest-v1/*` case with `revision.json`, run the
Manifest compiler on `manifest.yaml` with the example `sources` served by
the existing fakes (`FakeObjectStore`, `FakeGitClient`, `BlobDir`), run
`compile_spec` on the revision, and assert the states equal after
stripping `note`. The `compiled.yaml` files are deleted and
`manifest-v1-compatibility.md` describes this test instead of M′.

## Totality

### Resources root and clear-then-write

- `RESOURCES_ROOT = "data/"` (apply engine). The validator refuses
  `resources[].path` outside it at `PUT`; the compiler refuses the same
  entry at apply for a document stored before the rule; the genome spec
  checks refuse a revision whose resource path is outside it (the contract
  examples already comply).
- `ResourceFilePort` gains `list_root() -> tuple[str, ...]` (every file
  path under the root, relative to the workspace) and `clear_root() ->
  bool` (tree delete of the root; `False` when the root was absent).
  `DeviceResource` forwards to `ResourceFileService.{list_root,clear_root}`,
  which use `DeviceFileSystem.list_dir(recursive=True)` and `delete_tree`
  on `workspace/data`; every transport implements both today (BaaS and
  local through the adapter's `/api/file/list` and `/api/file/rmtree`,
  teclaw through its `/api/v1/file/list` and `/api/v1/file/remove`).
  `PlatformResource` lists and deletes its resources category.
  *Revised (owner review, 2026-10-27): no root-specific methods — the port
  is `list_entries(prefix, recursive, publish_id, device_uuid)` and
  `delete_dir(path, …)`, general and stage-addressable, with the
  protected-entry policy applied by the materialiser, which clears the
  unprotected root entries concurrently.*
- `ResourcesMaterialiser.plan`: one `list_root()`; entries classified
  against it (`updated` when the path is listed, else `created`; trees by
  prefix); `removals = sorted(listed − declared member paths)`; the
  declared-tree marker and its per-member `exists` probes are gone.
- `ResourcesMaterialiser.write`: `clear_root()` (a failure fails the
  category with nothing written), then upload every member in declaration
  order with the existing per-entry outcomes, then
  `ResourceService.replace_rows(bot, written_paths)` (delete the
  bot's file rows under the root, insert one row per written file with
  source `manifest`). A row write failure is a category failure, never
  swallowed.
- The engine teams' document (Chinese) states what the platform relies on:
  recursive listing of `/workspace/data`, tree delete that removes every
  file and subdirectory and is idempotent on an absent root, upload that
  creates parent directories, and the path forms each API takes. It is
  written once the root is confirmed and delivered as an artifact, not in
  the repo.

### `script: null`

In the compiler (`declared.py`); see above.

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
  the Manifest apply protocol and provides `GenomeCompiler`.

## Data Model Changes

- `ac_genome_promotion`: drop `document_digest`, `document_json`,
  `apply_reference`, `idx_genome_promotion_bot_apply` (DDL edited in place;
  not deployed, per #3072).
- `ac_bot_config_manifest_apply.report` JSON gains `"state"`; the codec
  defaults a missing key to `{"kind": "manifest"}`. No column change
  (`apply_trigger` keeps `String(32)`; a genome run's trigger is
  `"promotion"`, which fits).
- Apply task payload gains `state` (the reference's payload dict).
- `ac_resource`: rows with source `manifest` for Manifest-written files; no
  schema change.
- `ac_manifest_content` gains rows for inline identity/resource content
  (`produced://manifest/inline`) and for resource trees
  (`produced://manifest/tree`); no schema change.

## API / Interface Changes

- `api/apply_run_service.py` re-exports `ApplyRunServiceProtocol` (was
  `api/bot_config_manifest_apply_service.py` / `BotConfigManifestApplyServiceProtocol`):
  `start_apply(..., state: StateRef, ...)`, `dry_run(..., state: StateRef)`;
  `get_apply`, `last_apply`, `recent_applies(limit)` (new, for the
  compiler's baselines), `materialised_constructs`, `delivery_for_engine`,
  `delivery_for_bot` unchanged.
- HTTP apply payloads (`ConfigManifestApply`) gain `state`; everything else
  unchanged. `PUT /config-manifest` refuses `resources_outside_root`.
  `PromotionPayload` drops three fields. `drifted` 409 data is
  `{active, observed}`.

## Key Files & Functions

### New: `core/apply_engine/`
`__init__.py`, `README.md` (`## Context Boundary`), `desired_state.py`
(+ `RESOURCES_ROOT`), `compiler.py` (`DesiredStateCompiler`, `StateRef`
union, `Compiled`), `constructs.py` (from
`bot_config_manifest/capabilities.py`: `Category`,
`Section`, `Construct`, `Capabilities`,
`resolve_capabilities`, `SourceCell`), `delivery_mode.py`, `context.py`,
`order.py`, `outcomes.py` (+ `state` on `ApplyReport`, `as_payload`),
`registry.py`, `orchestrator.py`, `materialisers/{script,mcp,identity,
skills,resources,cli_tools}.py`, `delivery.py` (`MaterialiserPorts` +
`content`), `activation_delegates.py`, `identity_files.py`,
`resource_files.py`, `skill_package_upload.py`, `managed_files/` (store,
reader, ports — moved whole; `PlatformResource` lives here),
`redeliver.py`, `carry_forward.py`, `triggers.py`, `apply_task.py`,
`run/{service,report_codec,lock_reaper,termination}.py`,
`repository/apply_models.py`. The `cli_tools/` package moves too
(`CliToolService` is a delivery component the management API also uses;
`declarations.from_entry` moves to the Manifest compiler).

### `core/bot_config_manifest/`
`compiler/{__init__,declared,session,compile}.py`,
`compiler/entries/{script,mcp,identity,skills,resources,cli_tools,
engine_config}.py`; `sources/` (moved funnel); `schema/validator.py`
(`script: null` doc, resources root rule), `schema/entries.py`;
`creation.py`, `create_job.py` (import `apply_engine`, pass
`ManifestStateRef()`); `README.md` boundary rewritten.

### `core/bot_genome/`
`compile.py` (rewritten), `registry.py` (`compile`, `drift`, no
`applied_revision`, no `applies`), `genome_registry_protocol.py`,
`models.py`, `errors.py`, `spec_checks.py` (root rule),
`promotion/{applier,service}.py`, `repository/models.py`,
`sql/2026_10_19_bot_genome.sql`, `README.md`.

### Repository and resources
`protocols/bot/{config_manifest_apply,genome}.py`,
`implementations/bot/{config_manifest_apply,genome}.py`;
`core/resources/service.py` (`replace_rows`),
`core/services/resource_file_service.py` (`list_root`, `clear_root`),
`core/ports/resource_file_port.py`.

### HTTP
`adapters/http/openapi_v1/bots/{config_manifest_apply,config_manifest_support,
create_with_manifest,schemas_config_manifest_apply,genome,schemas_genome}.py`,
`adapters/http/openapi_v1/errors_genome.py`, `api/apply_run_service.py`.

### DI
`di/modules/apply_engine_module.py` (from `manifest_delivery_module.py`:
ports, strategies, compilers map), `manifest_fetch_module.py` (the
Manifest compiler and its factories), `bot_management_module.py`
(`ApplyRunService`, seam, task lifecycle), `bot_genome_module.py`,
`agentswarm/__init__.py` (type rename).

### Tests (moves)
`tests/community/core/bot_config_manifest/apply/*` → split:
`tests/community/core/apply_engine/` (engine, service lifecycle, strategy,
task, materialisers, delivery, teclaw, orchestrator-generic, port
forwarding, capability contract, managed_files, cli_tools) and
`tests/community/core/bot_config_manifest/sources/` (resolver, fetchers,
session, entry delivery). `_fakes.py` (1308 lines) splits into
`apply_engine/_fakes.py` (services, ports, strategies) and
`bot_config_manifest/_fakes.py` (content, object store, credentials, git).
New: `bot_config_manifest/compiler/test_*.py` (one per entry compiler,
carrying the grammar/fetch refusal cases lifted from the materialiser
tests), `test_equivalence.py`; `apply_engine/test_desired_state.py`;
`bot_genome/test_compile.py` (rewritten), `test_registry_service.py`
(`drift`), `test_promotion_service.py`; `core/resources` tests for
`replace_rows`; `di/test_apply_engine_module_wiring.py`.

## Testing Strategy

- **Behaviour lock first.** Before moving anything, run the full Manifest
  suite on `dev` and record the pass count; every task group ends with the
  same count (moved tests included) plus the group's new tests.
- **Compiler parity.** Each materialiser test that asserts a grammar or
  fetch refusal today becomes a compiler test asserting the same
  `EntryFailure` reason text; the materialiser test keeps the engine-side
  cases with typed entries built directly.
- **Equivalence** across the two compilers on the contract examples.
- **Resources**: a path declared by the previous document and omitted now
  is gone; a bot-written file under `data/` is gone and reported in
  `removals`; a file outside `data/` is untouched; rows follow the files;
  a failed clear writes nothing; the platform-store road mirrors each.
- **Genome**: route, endpoint, repository (DDL/ORM pin), service and
  conformance tests; `drift` with the `FakeReader`; promotion refusals.
- **Architecture**: module boundaries (`apply_engine` added to the
  significant set; `bot_genome` and `bot_config_manifest` boundaries
  updated), narrow-ports declaration, service-API conformance, HTTP-only
  adapter, no module-level service instances.
- Gates: `ruff check`, `ruff format --line-length 120 --check` on new
  files, line counts, `DEPLOY_PROFILE=test pytest tests/community`,
  `./scripts/ci_test.sh` coverage thresholds.

## Risks & Mitigations

- **Blast radius of the move** (≈40 source files, ≈45 test files). Use
  `git mv` so history follows; one commit per moved unit; no shims, so a
  stale import fails at collection, not at runtime.
- **Behaviour drift in the resolve split.** The compiler tests are lifted
  from the materialiser tests case by case; the equivalence test catches
  shape mistakes on both compilers at once.
- **Resources root rule on stored documents.** A document with a resource
  outside `data/` fails its next apply with a named per-entry reason; the
  PR description says so, and the OSS deployments are few. The alternative
  (no rule) leaves #2162 open for those paths.
- **Clear-then-write window**: a failed upload after the clear leaves fewer
  files than before, reported as `partially_written`; the next apply
  converges. Accepted with the issue owner.
- **Bot-written files under `data/` are removed** on every apply that
  declares resources. Accepted with the issue owner; the root is what
  bounds it.
- **Content-store growth**: inline content and resource trees are filed
  now. Both are bounded by the existing per-entry caps; retention is
  unconditional already (RSI-03 §9.1).
- **Skills receipt format change** (canonical zip → acm-tree). `keep_last`
  reads of receipts filed before this change still decode: `BlobDelivery.
  members` falls back to the archive road for a zip.
- **Drift by capture is heavier** than a row lookup (reads every identity
  file, skill package, resource and MCP override). It runs at promotion for
  non-user actors only; run start (RSI-08) will reuse it.
- **Contract folder edits** (`compiled.yaml` retired, compatibility doc
  rewritten, root rule stated): flagged for the owner.

## Open Questions

- Resources root confirmed as `data/` (spec § Open Questions).
- Bind the direct applier now or in RSI-04 (spec § Open Questions).
- Whether `Category`/`Section` should be renamed to
  `Category`/`Section` now that they live in `apply_engine`. Left as is;
  a rename is a mechanical follow-up.
