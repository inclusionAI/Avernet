# Plan: Lifecycle Apply Points (W8)

Spec: `spec.md` in this directory. Work item W8, issue #1476.

> **Revision 4 (2026-09-03).** The managed-files index table is dropped in review: the object key layout is the record. Otherwise revision 3 — The delivery-strategy seam, the ownership map,
> the teclaw platform-managed path behind a switch, and the deferred-provision
> creation sequence. Revision history at the end.

## What already exists, and what that leaves

| Already there | Where | What W8 gets from it |
| --- | --- | --- |
| `start_apply(..., trigger=, phases=, bot=…)` — lock, re-validate, `RUNNING` row, enqueue; `run_apply_task` rebuilds the context and runs the orchestrator | `services/config_manifest_apply_service.py` | The one entry point; the place the strategy is selected |
| `build_materialisers(script_service, activation_service, …, identity_service, upload_service, …, resource_service)` — materialisers take **ports**, not services | `apply/registry.py` | A strategy is a different set of ports handed to the same registry |
| `ManifestIdentityPort` (`list_bot_files`, `read_identity_file`, `update_bot_file`), `ManifestResourcePort` (`upload_file`, `delete`, `exists`), the upload port (`upload_local_skill`, `installed_package_digest`) | `apply/identity_port.py`, `apply/resource_port.py`, `materialisers/skills.py` | The exact surface a store-backed port must implement |
| `MutationProjectionFlow.apply(runtime_required=False)` — records desired state, skips readiness and projection | `skill_center/services/_mutation_flow.py` | Record-only activation is a parameter away |
| `TeclawFilePromotion.stage_files` — writes files to OSS under `teclaw/{env}/bolt_data/{entity_type}_{entity_id}/<segment>/teclaw/{ns}/{rel}` and returns `{name, store, path}` refs; `ObjectStoragePlugin` (`put_object`, `delete_object`, `list_objects`) | `deploy/teclaw_file_promotion.py`, `plugin_api/object_storage.py` | The key layout and the client the managed-files store reuses |
| `ConfigComposer.compose` builds skills / mcp / resources / identity from `ConfigComposerInputCollector`; the teclaw branches return `[]` for resources and identity | `core/config_compose/services/{config_composer,collector}.py` | The one place the ownership map and the managed refs enter |
| `TeclawDeviceSyncService._compose_and_deliver` — recompose through the composer, POST to the container, record the draft | `devices/services/teclaw_device_sync.py` | The closing redeliver, reached through `DeviceSyncDispatcher.dispatch(ctx).sync_symlinks([])` |
| `TeclawProvisionService.provision(bot, owner_id)` — compose via the producer router, `create_teclaw_bot`, approve, insert binding, enqueue poll | `bot_management/services/teclaw_provision_service.py` | Called *after* the single phase; the composer already reads the store by then |
| `BotService.create_bot` — step 1 record, step 2 device / teclaw provision, shared tail | `bot_management/services/bot_service.py` | Split at the step-2 boundary by a `provision` option |
| `BotCreateWithManifestHandler` step machine; `_creation_state` in the poll | `bot_config_manifest/create_job.py`, `adapters/.../create_with_manifest.py` | Both ask the strategy for the sequence |
| `BotConfigArtifact.to_dict` omitting `cli_tools` when `None`; schema `additionalProperties: false` | `kernel/bot_config/artifact.py`, `artifact.schema.json` | The pattern the ownership map follows |
| `BotConfigManifestConfig.from_block(user_config.bot_config_manifest)` | `manifest_fetch_module.py` | Where the switch is read |

## Architecture

```text
                       ┌──────────────── DeliveryStrategy (per family) ────────────────┐
                       │ phase_of(step)  ports()  creation_sequence()  finish(ctx, rpt) │
                       └───────────────────────────────────────────────────────────────┘
   ArcaDelivery                               TeclawDelivery(switch=on)          TeclawDelivery(switch=off)
   script=PRE, rest=ON                        script=n/a, all=PRE                script=n/a, all=ON
   device ports, projecting activation        store ports, record-only act.      device ports, projecting act.
   finish: nothing                            finish: one redeliver if bound     finish: nothing
   creation: [PRE] create wait [ON]           creation: record [PRE] provision   creation: [PRE] create wait [ON]

apply service ── strategy_for(bot) ──► build_materialisers(**strategy.ports()) ──► orchestrator(steps_for(strategy))
                                                                                └─► strategy.finish(ctx, report)

teclaw store path (switch on)
  materialiser.write ──► ManagedFilesStore.put(bot, category, rel, bytes) ──► OSS object under the bot's `_manifest` prefix
  composer(teclaw)   ──► collector.identity_files/resources/skills ──► ManagedFilesReader.refs(bot, category) when the platform owns the compose
                     ──► artifact.ownership = all platform (MANIFEST_APPLY; PROVISION with a manifest) | all engine but mcp (RUNTIME)

creation (teclaw, switch on)   record ─► apply(PRE, all) ─► provision(composes from the store's listing) ─► wait ACTIVE ─► READY
creation (ARCA)                apply(PRE=script) ─► create+provision ─► wait ACTIVE ─► apply(ON) ─► READY     (unchanged)

PUT ─► manifest_service.put ─► start_apply(trigger=put, ALL_PHASES) ─► response.apply
```

## Key decisions

### K-1 The strategy is a small object built by a factory that reads the switch

`apply/delivery.py`:

```python
class DeliveryStrategy(Protocol):
    family: str                                   # "arca" | "teclaw"
    def phase_of(self, step: ApplyStep) -> ApplyPhase: ...
    def steps_for(self, phases) -> tuple[ApplyStep, ...]: ...
    def ports(self) -> MaterialiserPorts: ...     # the kwargs build_materialisers takes
    def creation_sequence(self) -> CreationSequence: ...
    async def finish(self, ctx: ApplyContext, report: ApplyReport) -> None: ...
```

`CreationSequence` is an enum with two members: `CREATE_BETWEEN_PHASES` (ARCA and
teclaw-off) and `RECORD_APPLY_PROVISION` (teclaw-on). The job and the poll branch
on it; nothing else does.

`DeliveryStrategyFactory.for_bot(bot | engine_type)` picks by `is_teclaw` and
reads `BotConfigManifestConfig.teclaw_platform_managed` once per apply. Ports
are lazy providers, as every other collaborator in this graph is.

`order.py` keeps `APPLY_ORDER` as the position table; its `phase` column
becomes ARCA's default. `steps_for` moves onto the strategy; the module-level
function stays as ARCA's for existing callers and tests.

### K-2 Ports vary, materialisers do not

The teclaw-on strategy hands `build_materialisers` these ports:

| Port | Implementation | Backing |
| --- | --- | --- |
| identity | `StoreIdentityPort` | objects under `identity/`; `update_bot_file` puts the object; empty content deletes it |
| resources | `StoreResourcePort` | objects under `workspace/`; `upload_file` / `delete` / `exists` over the store's listing |
| upload | `StoreSkillPackagePort` | `upload_local_skill` unpacks the validated package into the store under the local-skills layout, stores every file under `workspace/skills-local/<name>/`, creates the skill row with a `local://` locator, and returns the record; `installed_package_digest` recomputes the package digest from the stored members |
| activation | `RecordOnlyActivation` wrapper | delegates to `DirectActivationService` with `project=False` |
| script | unchanged (never reached: unsupported) | |

All three store ports share one `ManagedFilesStore` (`bot_config_manifest/managed_files/`):
`put(scope, category, name, rel_path, content) -> ManagedFile`,
`delete(scope, category, rel_path)`, `list(scope, category)`, `purge(scope)`.
It writes the object first, then the row; a row without an object is
impossible by construction, an object without a row is garbage the purge
collects. The object key is
`teclaw/{env}/bolt_data/{entity_type}_{entity_id}/{bot_id}_manifest/teclaw/{ns}/{rel}`
— the promotion layout with a `_manifest` segment instead of a publish stage,
so the `bot-data` store's base resolves it unchanged.

No index table (rev 4). The key layout is the record: `list_objects` over the
bot's `_manifest` prefix recovers the file set, and each key's path says its
category (`identity/<type>`, `workspace/skills-local/<name>/…`, other
`workspace/…`) and name. A write whose path would read back as another
category is refused. Digests are computed from bytes in hand (`put`, `get`)
and never stored; `unchanged` planning reads content, as it already did.

### K-3 Record-only activation is a parameter, not a bypass

`DirectActivationService.activate_mcp / deactivate_mcp / activate_skill /
deactivate_skill` gain `project: bool = True`. `False` passes
`runtime_required=False` to `MutationProjectionFlow.apply`, which already
records without readiness or projection. The audit row is still written. The
protocol in `api/` gains the parameter with the default, so every existing
caller is unchanged.

### K-4 The ownership map

`BotConfigArtifact.ownership: dict[str, str] | None = None`; omitted by
`to_dict` when `None`; `from_dict` reads it when present. Schema: optional
`ownership` object whose values are `platform` or `engine`, with the semantics
in its description.

`ComposeRequest` gains `occasion: ComposeOccasion` (`RUNTIME` by default,
`PROVISION` from eager provisioning, `MANIFEST_APPLY` from the closing
redeliver). The collector asks one injected `PlatformOwnershipReader`
(protocol in `core/config_compose/protocols.py`, implemented in the manifest
package) whether the platform owns the compose: yes for `MANIFEST_APPLY` and
for `PROVISION` on a bot with a manifest, when the switch is on; no otherwise.
The composer writes every category `platform` or every category `engine`
from that one answer (`mcp` always `platform`). Revision 5; revision 3 keyed
the map on the categories the stored manifest declared.
The composer writes the map for teclaw requests only; the collector's teclaw
branches for identity, resources and skills read the store through a
`ManagedFilesReader` when the category is `platform`, and return today's
answer otherwise.

### K-5 The closing redeliver

`TeclawDelivery.finish` runs after the orchestrator has written every
category: if the bot has a live binding, resolve the device context and call
`dispatch(ctx).sync_symlinks([])` once; otherwise nothing (provisioning will
compose). A redeliver failure is recorded on the report as a category-less
note, never raised — §2.7. With the switch off `finish` is a no-op, because the
per-mutation projections already delivered.

### K-6 Creation: record, apply, provision

`BotService.create_bot(..., provision: bool = True)`. With `False` it runs
step 1 and returns the record with status `PENDING` and no binding; the tail
that depends on a binding (publish record, draft-artifact recording, BCN
registration) moves into `provision_bot(bot_id, user_id, nick_name)`, which
runs step 2 and the tail for a record created that way. The default path
calls both back to back and is byte-for-byte today's behaviour.

`complete_manifest_creation` gains `provision: bool`; the job passes
`False` when the strategy's sequence is `RECORD_APPLY_PROVISION`, then, once the
`create:pre_container` record is terminal, calls `bot_service.provision_bot`.
The job's steps stay questions about durable state: "record exists but no
binding and no terminal pre-container record → start the phase"; "record
exists, phase terminal, no binding → provision"; "binding present → wait for
ACTIVE → Complete". Phase B is never started under this sequence.

The poll's `_creation_state` takes the sequence: under `RECORD_APPLY_PROVISION`,
`READY` is bot `ACTIVE` with the pre-container record `SUCCEEDED`,
`APPLY_FAILED` with it `PARTIAL` / `FAILED`, `APPLYING` while it is `RUNNING`,
`CREATING` between phase and `ACTIVE`.

### K-7 The switch

`BotConfigManifestConfig.teclaw_platform_managed: bool = False`, read from
`user_config.bot_config_manifest.teclaw_platform_managed`. Documented in the
config reference and the module README with the condition for flipping it.

### K-8 `PUT` and the warnings

As in revision 2 (`ConfigManifestApplyStarted`, `declares_script`, the
not-ACTIVE warning — now conditional on the strategy having `ON_CONTAINER`
constructs). The write-through alias (`write_through_script`, `script_body`,
the splice helper) is withdrawn in revision 5: the legacy `/startup-script`
routes stay as they were.

### K-9 What is deliberately not touched

- `BotService.restart_bot`, `BaasService.upgrade_bot`, `_build_create_bot_payload`,
  `PublishFlowService`, `DeviceService`, the teclaw publish poll, the publish
  gather.
- The orchestrator and the five materialiser classes (beyond taking the
  ports they already take).
- `SkillSymlinkListener`, `CronAutoSetupListener`.

## Files

### New

| File | What |
| --- | --- |
| `core/bot_config_manifest/apply/delivery.py` | `DeliveryStrategy`, `ArcaDelivery`, `TeclawDelivery`, `CreationSequence`, `DeliveryStrategyFactory` |
| `core/bot_config_manifest/apply/triggers.py` | Trigger constants |
| `core/bot_config_manifest/managed_files/{store.py,ports.py,reader.py}` | `ManagedFilesStore`; `StoreIdentityPort`, `StoreResourcePort`, `StoreSkillPackagePort`; the composer-facing reader |
| `core/bot_config_manifest/managed_files/README.md` | Context boundary |
| ~~the index table, its repository, protocol and DDL~~ | Dropped in rev 4 |
| ~~`core/bot_config_manifest/schema/splice.py`~~ | Withdrawn in rev 5 |
| `core/config_compose/protocols.py` additions | `PlatformOwnershipReader`, `ManagedFilesReader` |
| `tests/…` | Listed per task |

### Changed

| File | Change |
| --- | --- |
| `kernel/bot_config/artifact.py`, `artifact.schema.json` | `ownership`; omission; schema |
| `core/config_compose/models.py`, `services/config_composer.py`, `services/collector.py` | `ComposeOccasion` on the request; the map by operation; store-backed teclaw branches |
| `core/service_bot/services/deploy/external_compose_producer.py`, `devices/services/teclaw_device_sync.py`, `bot_management/services/teclaw_provision_service.py` | Name the occasion: `PROVISION` from provisioning, `MANIFEST_APPLY` from `deliver_manifest_apply` |
| `core/skill_center/services/direct_activation_service.py`, its protocol in `api/` | `project` parameter |
| `core/bot_management/services/bot_service.py`, `create_flow.py` | `provision` option; `provision_bot`; `complete_manifest_creation(provision=)` |
| `core/bot_config_manifest/services/config_manifest_apply_service.py` | Strategy selection; ports from the strategy; `finish` |
| `core/bot_config_manifest/apply/order.py` | `steps_for` delegates; ARCA default phases |
| `core/bot_config_manifest/create_job.py`, `creation.py`, `adapters/.../create_with_manifest.py` | Sequence-aware job and poll; refusal removed |
| `core/bot_config_manifest/services/config_manifest_service.py`, protocol, `api/` | `declares_script` |
| `adapters/http/openapi_v1/bots/{config_manifest.py,config_manifest_support.py,schemas.py}` | `PUT` apply + `apply` field + warnings |
| `di/modules/{bot_management_module,manifest_fetch_module,service_bot_module,skill_center_module}.py` | Strategy factory; store; reader bindings; switch |
| `docs/bot-config-manifest/engine-convergence-contract.zh-CN.md` | The addendum (§9) |
| `docs/bot-config-manifest/{user-manual,work-items,work-items.zh-CN}.md`, `core/bot_config_manifest/README.md`, `docs/arch` config reference | Docs |

## Risks

1. **The engine has not shipped the map.** Mitigated by the switch, off by
   default; with it off the teclaw bytes on the wire differ only by the map,
   which A5 says is ignored.
2. **`create_bot` split.** The tail runs for every creation path today;
   moving part of it behind `provision_bot` must keep the default path
   identical. Pinned by running the whole creation suite unedited plus a
   golden test that `create_bot()` and `create_bot(provision=False)` followed
   by `provision_bot()` produce the same record and the same collaborator
   calls in the same order.
3. **Record-only activation on a bot with no binding.** `_bot()` reads the
   record, which exists after the deferred create; `_require_mcp_permission`
   and the audit row behave as today. Pinned by a test that the projector is
   never called and the desired-state repository is.
4. **Two readers of the store (materialisers and composer) racing an
   apply.** The apply lock serialises applies; a compose during an apply may
   see a half-written category. Under §3.2 that is the same window a device
   write has today, and the closing redeliver after the apply converges it.
5. **Store key layout must match the `bot-data` store base.** Pinned by a
   test that composes an artifact from the store's listing and resolves each ref
   against the configured store the way the promotion test does.
6. **`router.py` size.** Write-through logic goes into the support module.

## Testing strategy

- **Strategy** — phase tables for the three configurations; `creation_sequence`;
  `ports()` shape; factory picks by `is_teclaw` and the switch; no materialiser
  module names an engine (a grep test beside the orchestrator one).
- **Managed-files store and ports** — put/delete/list/purge against a fake
  object store and in-memory SQLite; object-before-row ordering; identity and
  resource ports drive the real materialisers to `created` / `updated` /
  `unchanged` / removal purely from the store; the skill port unpacks, stores
  under the local-skills layout, creates the skill row, answers the digest.
- **Record-only activation** — `project=False` records and never projects;
  `project=True` unchanged.
- **Ownership map and composer** — `to_dict` omits when unset; schema accepts;
  teclaw compose emits all-`platform` for a manifest apply's redeliver and a
  manifest bot's first artifact, and all-`engine` (but `mcp`) for a runtime
  edit; only the former reads the store; ARCA compose carries no map.
- **Closing redeliver** — one dispatch call after a teclaw-on apply with a
  binding; none without; none with the switch off; a failing redeliver is a
  report note.
- **Creation** — `create_bot(provision=False)` + `provision_bot()` equals
  `create_bot()`; the job under `RECORD_APPLY_PROVISION`: record, phase, provision
  only after terminal, no phase B, re-entrant at every step; the provisioner
  hands an artifact with refs and the map; the poll's states under both
  sequences; ARCA job tests unedited.
- **`PUT`, §2.12, regression** — as in revision 2, plus the teclaw-on
  variant of the ordering pin.
- **Architecture gates** — module boundaries (new dependency rows), service
  API conformance (new protocol members), HTTP-only adapter, authorization
  inventory, oversized modules, no module-level service instances.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | Four apply points on the ARCA shape. |
| **rev 2** | Restart and republish deferred; teclaw creation as "the same job". |
| **rev 3** | The delivery-strategy seam; teclaw as store + index + artifact behind a switch; the ownership map; record, apply, provision. |
| **rev 4** | Review: the index table is dropped; the object key layout is the record, listed by prefix. |
| **rev 5** | Review: ownership follows the operation (`ComposeOccasion`), not the declared categories; the `/startup-script` write-through alias is withdrawn. |
