# `agentclaw.community.core.service_bot`

Publication resource lookups pass the record's `source_bot_pk` to the common
Bot address resolver. This covers build, verify/online/eval release, scale,
restart, restore, rollback, approval and deletion: a legacy publish Owner can
differ from both the current Owner and entity. Config coordinates and engine
selection use the same source-PK rule. Historical publish fields and per-record
stage bindings are not rewritten. Only orphaned default metadata retains the
legacy owner/entity lookup; service-source mismatches fail closed.

Service-bot domain — bot publication facade, BAAS service binding, sub-resource management.

## Context Boundary

```yaml
purpose: "Service-bot domain — bot publication facade, BAAS service binding, sub-resource management."
provides:
  - "FileCountService"
  - "BuildIgnoreService"
  - "PublishIgnoreService"
  - "BotProcess"
  - "PersonalBotProcess"
  - "ServiceBotProcess"
  - "EmptyBotProcess"
  - "BotProcessRegistry"
  - "BotPublishService"
  - "BaasService"
  - "BaasServiceError"
  - "BaasNoActiveDevicesError"
  - "BaasTransientServiceError"
  - "ServiceSkillsManifestBuilder"
  - "ResolvedSharedCorpusDelivery"
  - "ArtifactBuildRequest"
  - "ServiceArtifactLayoutObservation"
  - "ServiceArtifactResolvedLayout"
  - "ServiceArtifactBuildError"
  - "ServiceArtifactBuildErrorCode"
  - "ServiceArtifactLineageReader"
  - "ServiceEditLockService"
  - "ServiceBot SQLAlchemy models"
consumes:
  - "BotManagement"
  - "DeviceService + repo"
  - "SkillsPool layout state"
  - "RuntimeLayoutProbeServiceProtocol"
  - "SystemConfig"
  - "PassportPlugin"
  - "UserListRepositoryProtocol"
internal_dependencies:
  - agentclaw.community.core.bot_knowledge.protocols    # optional build observation Service API
  - agentclaw.community.kernel.file_count
  - agentclaw.community.plugin_api.file_count_runtime
  - agentclaw.community.core.base
  - agentclaw.community.kernel.build_ignore
  - agentclaw.community.core.repository.protocols.identity
  - agentclaw.community.core.user_list
  - agentclaw.community.core.repository.protocols.build_ignore
  - agentclaw.community.core.runtime_binding
  - agentclaw.community.core.engine_runtime.errors
  - agentclaw.community.kernel.publish_ignore
  - agentclaw.community.plugin_api.publish_ignore_runtime
  - agentclaw.community.core.digital_employee
  - agentclaw.community.core.apply_engine.cli_tools.store    # W9: a teclaw promotion copies the bot's CLI-tool objects into the new stage's prefix from the platform's own copy, rather than reading them back out of the engine
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.skills_pool    # repository contracts consumed by this module
  - agentclaw.community.core.skill_center    # full Draft projection and exact Center facts frozen into a service artifact
  - agentclaw.community.core.common_config
  - agentclaw.community.core.bot_collaborator    # collaborator permission and edit-lock enforcement for public lifecycle operations
  - agentclaw.community.core.bot_inventory.policies    # shared engine/deploy-mode combination policy for service upgrades
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_startup_script.protocols  # StartupScriptReaderProtocol — the read side of the per-bot startup script, consumed while composing the container start command
  - agentclaw.community.core.channel    # per-stage engine_overrides (DingTalk channels) reader at verify/online promotion
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.caller_identity.credential  # CallerToken used by BaaS outbound-rule update
  - agentclaw.community.core.devices
  - agentclaw.community.core.engine_runtime.stage    # canonical live-stage publish binding resolution for Caller identity updates
  - agentclaw.community.core.quality.services
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.system_config
  - agentclaw.community.core.task_queue    # durable publish stage tasks (enqueue + handlers + worker)
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.kernel.bot_config
  - agentclaw.community.kernel.device_dto    # neutral OutBound / ResourceSpecification DTOs (B6)
  - agentclaw.community.kernel.deploy_runtime    # DeployRuntime — names which composer shapes the create-bot payload
  - agentclaw.community.kernel.lifecycle    # PublishTaskLifecycle registers durable task handlers
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow           # antprocess approval workflow for publish approval
  - agentclaw.community.core.devices.services.device_filesystem    # teclaw build-time file promotion (TeclawFilePromotion)
  - agentclaw.community.plugin_api.engine_ext_client
  - agentclaw.community.plugin_api.eval_env             # DYNAMIC_ENV_TAG_KEY injected into eval container extra_envs
  - agentclaw.community.plugin_api.http_client
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.object_storage           # teclaw promotion stages files to OSS
  - agentclaw.community.plugin_api.outbound_rules           # BaasService delegates egress-rule construction (B6)
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.secret_resolver       # BaasService 透传给 outbound rule 构造,singlebox/prod 双实现
  - agentclaw.community.plugins.prod.baas_service    # Back-compat ``BaasService`` alias (PEP 562 lazy import); see arch test exception
  - agentclaw.community.api.publish_approval        # API Protocol for publish approval service
  - agentclaw.community.utils
  - agentclaw.community.utils.env_utils
  - agentclaw.community.core.channel.channel_service_protocol  # Service API Protocol consumed by this module
```

### Change impact

Publication path is the user-visible go-live for bots — bugs here block customers from publishing.
Service artifacts freeze the persisted Skills Pool policy state plus fresh
Engine-observed filesystem evidence at build time. OCB remains the sole owner
of Repo/Center mounts; ``shared_corpora`` is an artifact exclusion and audit
contract, never a BaaS mount instruction. Changes to the Skills Pool probe,
mapping contract or historical manifest reader therefore affect publication
compatibility and must be reviewed together with this module.

Publish history is associated with its source Bot through immutable
`ac_bot_publish.source_bot_pk`. Version duplicate checks and stage-record reads
use this key for transferable, non-`default` service Bots. Personal Bots,
`default`, and their legacy orphan lineages retain the old owner-shaped lookup.
An orphan default lineage keeps its historical PK and is never rebound to a
newly created default Bot merely because owner and bot id happen to match.
The existing `owner_id` column remains a compatibility resource locator: new
rows store the source Bot's immutable `entity_id` while the field name and
response shape stay unchanged. This stage does not change SecBaaS code.

Legacy publish requests may contain only `publish_id`. The shared permission
interceptor resolves the source Bot from the extracted resource locator for both
reads and writes, then checks permissions and edit locks against its current Owner.
Historical publish `owner_id` values do not grant Owner privileges after a
handoff. Request payloads and immutable publication resource addresses remain
unchanged; the handoff maintenance fence still applies to writes before authorization.

Publishing resolves internal Bot reads through `BotService.get_bot_by_address`:
legacy entity/current-Owner locators select the Bot, then its current Owner is
used for the enriched detail read. `get_bot` itself keeps strict ownership
semantics. Build/release/restart/retry/scale/rollback/restore retain the publish
record's stable entity for resource paths. New approval auditors and approval
Owner snapshots use the current Owner; existing approval history is not rewritten.
Deletion resolves and validates the current Bot before destroying version
resources. The Bot row is deleted under current ownership while Passport, App
Grant and capability/asset cleanup retain the stable entity. Personal/default
cleanup and earliest-Bot/employee deletion protections retain existing behavior.
BaaS binding lookup and outbound Bot-type selection accept legacy/current-Owner
locators and query service publication resources by entity. Managed read-only
rules also resolve the stable Bot before reading its policy. Passport hot-update
checks current ownership first, then locates draft/verify/online/Caller targets
by entity, including repeated A→B→C handoff.

The existing oversized central Bot/Publish services remain under the repository's
pre-existing architecture allowlist; this compatibility fix does not extend it.
Their remaining decomposition is a separate lifecycle/service refactor so this
fix can be reviewed without moving unrelated creation and restart behavior.

## Publish operation ledger (#197)

Every BaaS mutation in the publish pipeline (verify/online release, upgrade,
restart, scale, offline/destroy, rollback deploy, eval publish/teardown,
approval create) is recorded in the **`ac_publish_operation`** ledger so a crash
can resume at the first incomplete step instead of re-issuing. The intent row is
persisted **before** the BaaS call; the returned workflow id is persisted after.

`PublishOperationRunner` drives each mutation as
`open_operation → acquire_workflow → complete/fail/abandon` (see
`services/publish_flow/operation_runner.py` and
`specs/2026-07-15-publish-service-idempotency/`).

**States** (`PublishOperationState`):

| State | Meaning |
|-------|---------|
| `PENDING` | Intent persisted; the BaaS workflow id is not yet recorded. On resume the runner adopts an in-doubt workflow (existing bot → adopt-by-query) or re-issues (a creation's bounded, observable orphan). |
| `ID_RECORDED` | The BaaS workflow id (or, for approval-create, the puid) is recorded; follow-up steps (binding/ext) may still be pending. |
| `COMPLETED` | Terminal — the operation and its follow-up steps finished. Also reachable directly from `PENDING` for non-BaaS ops (e.g. `approval_create`) whose outcome lives in `result` rather than `baas_publish_id`. |
| `FAILED` | Terminal — a step failed unrecoverably (error in `last_error`). |
| `ABANDONED` | Terminal — superseded (a rebuild changed the artifact, or an upgrade fell back to a create), so the row is retired and a fresh attempt opens a new op. |

**Adopt-by-query.** For mutations on an *existing* bot, a crash after the BaaS
call but before the id is recorded is resolved on resume by listing the bot's
BaaS workflows and adopting the single one that is ours (matching publish type,
unclaimed by any ledger row, and past a monotonic workflow-id high-water mark
snapshotted at first acquire). Creations (no bot to query) instead accept a
bounded orphan that the in-flight `PENDING` op makes observable.

**All-auto approval.** Every mutation payload sets `auto_approve_publish=True`;
there is no client-side approve call in this pipeline. Approval workflows for
publish/unpublish (owner `should_approval`) are a separate concern handled by
`PublishApprovalService`, whose AGREED callback enqueues a durable trigger task.


## Runtime CLI artifact rollout

Home `.agentclaw` and `.local/bin` snapshots are disabled by default. A build
resolves the source Bot owner's `entity_id` against `ac_entity_user_list` using
`user_list_type='service_bot_runtime_artifact'` and the backend runtime `env`.
An exact owner entry enables that user; `entity_id='*'` enables all owners in
that environment. Missing owners, missing entries, and read failures disable
both snapshots. This decision is fixed for the build, independently of the
publishing actor. Engine files, historical artifact restoration, and runtime
ownership are unchanged. When disabled, `.agentclaw` returns without inspecting
or changing either source or target; any existing versioned snapshot is retained.
Disabled `.local/bin` retries still clear existing versioned bin snapshots without
reading draft CLI directories or creating missing snapshots.

The `PUT /api/v1/user-lists/correct` API requires `admin.super_admin` authorization for all
list types. Operators can add or remove this rollout type for an owner or `*`,
with `env` selecting the environment. Ordinary authenticated users receive
HTTP 403 without changing membership. Membership checks still require only login.
The database remains an alternative for operations.
The following examples are documentation only and must not run as a migration.
Replace `pre` and `OWNER_ENTITY_ID` deliberately for the intended environment/user.

```sql
-- Inspect before changing rollout state.
SELECT entity_id, user_list_type, env FROM ac_entity_user_list
WHERE user_list_type = 'service_bot_runtime_artifact' AND env = 'pre';

-- Enable one Bot owner (run instead of the global example).
INSERT INTO ac_entity_user_list (entity_id, user_list_type, env)
VALUES ('OWNER_ENTITY_ID', 'service_bot_runtime_artifact', 'pre');

-- Enable all Bot owners in one environment only when rollout is intended.
INSERT INTO ac_entity_user_list (entity_id, user_list_type, env)
VALUES ('*', 'service_bot_runtime_artifact', 'pre');

-- Disable one owner; a global row still takes precedence while present.
DELETE FROM ac_entity_user_list
WHERE entity_id = 'OWNER_ENTITY_ID'
  AND user_list_type = 'service_bot_runtime_artifact' AND env = 'pre';

-- Disable the global opt-in; individually listed owners remain enabled.
DELETE FROM ac_entity_user_list
WHERE entity_id = '*'
  AND user_list_type = 'service_bot_runtime_artifact' AND env = 'pre';

-- Fully stop new CLI snapshots for the selected environment.
DELETE FROM ac_entity_user_list
WHERE user_list_type = 'service_bot_runtime_artifact' AND env = 'pre';
```

Turning the list off affects new builds; it does not remove CLI files from
already deployed containers or rewrite historical versions.

## BaaS Bot POST responses

The public `post_bots_api` method requires an object envelope and object `data` (missing `data`
retains the existing empty-object default). JSON decoding failures and invalid
envelope/data shapes raise `BaasServiceError`, so pool-layout restart failures
use the same upstream error contract as transport and business-code failures.
Other private POST callers retain their existing success-data behavior.
Response parsing lives in `services/baas_api_response.py`; the existing oversized
`BaasService` keeps its current size-gate exemption and delegates this parsing.
The broader service split remains a separate refactor, as recorded by the
existing architecture allowlist; this fix reduces the service file size.
## Fastdisk artifact transfer

`BotBuildService` receives the neutral `ArtifactTransferRegistry` and resolves
an `ArtifactTransferStrategy` using each build plan's engine, at both `publish`
and `restore` hooks. DI registers `AicodingArtifactTransferStrategy` only for
`aicoding` and `claude_code`. Every other engine resolves to
`DefaultArtifactTransferStrategy`, which returns False / None without runtime
access, preserving the caller's existing NAS build/restore flow. A no-op never
means transfer success. Coding errors propagate without NAS fallback.

The interface, default implementation and registry live in the neutral
`core/bot_management/engines/artifact_transfer.py`; that module imports no
coding implementation or configuration. Detection, draft-device/version checks,
runtime path resolution, restore-result assembly, command execution and the
pure configuration parser remain under `engines/aicoding/`. The caller neither
checks `supports` nor assembles a container-script request. Path resolution is
passed lazily, and the restore hook receives the existing migration-error type.

The existing `ServiceBotModule` has a separate registry provider that assembles
the coding implementation. Its `bot_build_service` provider only injects that
registry. API, version paths, configuration keys, schema 6 and timeouts are
unchanged by this selection refactor.

The AICoding strategy consumes the existing build plan and a command executor. It
embeds only read-only mount detection so legacy containers need no detector
installation. Non-fastdisk bots retain host-side NAS transfer. Detector failure
is not a non-fastdisk result. Fastdisk bots require schema-6
`/home/admin/bin/service_publish.py`; missing/incompatible scripts fail with a
logged error, without uploading a fallback implementation.

Publication passes the existing version prefix and per-bot dynamic exclusions
for direct OSS upload. Fixed file selection and extra-root mappings are owned by
the installed container policy, not expanded into Backend requests. Draft restore passes the identity/version-derived OSS prefix, logical artifact
path, Mist credential name (not keys), ownership and remaining operation budget.
The container downloads directly into mapped runtime paths, without whole-tree
staging or a second local rsync. Exclusions and CLI merge behavior are retained;
engine/Claude stale-file cleanup starts only after required downloads succeed.
Completed file replacements remain on failure; there is no transactional rollback. The startup tar merge is
not substituted for restore's delete semantics.

Command transport stages only invocation parameters and execution control in
bounded chunks, then waits for an atomic exit-status result. The container scripts
repository owns transfer implementation. Consumers retain publication-state and
error handling; no independent Backend post-upload object validation is added.


Draft device resolution is AICoding-owned: after engine eligibility is established,
the strategy queries the existing binding repository by `bot.binding_id` and
requires an ACTIVE binding with a device ID. It does not trust `bot.device_id`,
mutate the Bot context, or fall back on lookup failure. Other engines never make
this strategy-specific query; the shared draft flow retains its original context.
Optional command-timeout forwarding remains infrastructure-only: omitted timeout
arguments retain the existing provider call/default behavior.

## Optional knowledge observation

The existing verify-flow task attempts a best-effort knowledge-selection capture
only at BUILDING entry. It is a lazy ordinary Service API call, not a publication
transaction participant or success condition. Observer/provider errors are logged
and isolated; actual native build/release failures retain their Fail results.
Online success, restart, rollback, draft restore and publication ext/CAS remain
unchanged. The record is last-observed display metadata, not runtime authority.
