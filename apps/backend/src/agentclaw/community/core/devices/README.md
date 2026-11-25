# `agentclaw.community.core.devices`

Device / engine domain — engine binding, health probing, readiness, sandbox provisioning, OSS↔NAS migration.

Connection authorization resolves the Bot linked to a binding first and uses
its current `owner_id`. A published binding's `device_props.bolt_id` plus its
immutable `entity_id` resolves the same Bot after handoff. Historical bindings
with no linked Bot retain their standalone entity-owner rule. Lookup failures
deny access; the retained entity never grants former-Owner access to a linked
private Bot. Public visibility and existing collaborator checks remain in place.

For BaaS file writes, the device filesystem exposes `DeviceFileSystemReadOnlyError`
only when the Engine upload response contains its explicit read-only marker. Other
403 responses, older Engine 500 responses, and transport failures remain errors
without being classified as read-only. This does not promise atomic writes or
change the permissions of the underlying filesystem.

## Staff template overrides

Staff template overrides (`system/personal_bot_test_template_whitelist`) match
both the owner and the resolved BaaS engine bucket. A mapping contains
`engine`, `staff_ids` and `template_uuid`; omitting `engine` means **OpenClaw
only**, for both legacy single records and entries in `mappings`. Unmatched
engines retain normal template selection, including explicit template UIDs.
Different UUIDs for the same owner and bucket are ambiguous and fall back.
This lookup does not broaden eligibility for ARCA-to-BaaS restart migration.

## Context Boundary

```yaml
purpose: "Device / engine domain — engine binding, health probing, readiness, sandbox provisioning, OSS↔NAS migration."
provides:
  - "DeviceService"
  - "DeviceBindingRepository protocol"
  - "Engine health & readiness services"
  - "Device error types"
  - "Device event types"
  - "LayoutInitializationConfirmationProtocol"
consumes:
  - "BotManagement repo + service"
  - "EventBus"
  - "StoragePath"
  - "SystemConfig"
  - "BaasService"
  - "PassportPlugin"
  - "DeviceAccessor (models only)"
  - "Layout initialization confirmation implementation"
internal_dependencies:
  - agentclaw.community.core.ports.runtime_binding
  - agentclaw.community.core.ports.manifest_delivery  # confirmed whole-artifact MCP receipt
  - agentclaw.community.core.access    # configured super-admin scope for instance restart
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.base    # declarative Base for the devices-owned ORM model
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.config
  - agentclaw.community.core.common_config
  - agentclaw.community.core.config_compose    # teclaw_paths — namespaces/mappers used by the device-fs dispatcher (B6)
  - agentclaw.community.core.events
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.services    # {identity,resource,config}_addressing mapper builders for dispatch_addressed (B6)
  - agentclaw.community.core.storage
  - agentclaw.community.core.system_config
  - agentclaw.community.core.task_queue
  - agentclaw.community.core.utils
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.corp.di    # corp-only device services (ARCA) — relocate to corp/ in B11 T3.4
  - agentclaw.community.kernel.lifecycle    # BaaS publish task lifecycle registers task_queue handlers
  - agentclaw.community.kernel.device_dto    # neutral CommandResult / ResourceSpecification / OutBound DTOs (B6)
  - agentclaw.community.kernel.bot_config    # BotConfigArtifact — the whole-artifact payload teclaw_device_sync delivers
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem    # DeviceFileSystem — return type of the core device-fs dispatcher (B6)
  - agentclaw.community.plugin_api.http_client    # HttpClient used by BaaS transports
  - agentclaw.community.plugin_api.device_sync    # DeviceSyncPlugin — return type of the core device-sync dispatcher (B6)
  - agentclaw.community.plugin_api.devices    # DeviceAccessor — injected into DeviceFilesystemDispatcher (B6)
  - agentclaw.community.plugin_api.drm    # DRM dynamic-config reader plugin (B6)
  - agentclaw.community.plugin_api.models
  - agentclaw.community.plugin_api.outbound_rules    # OutboundRuleProvider (kernel rule) injected into ArcaDeviceService (B6)
  - agentclaw.community.plugin_api.eval_env    # DYNAMIC_ENV_TAG_KEY 常量引用（eval 设备路由）
  - agentclaw.community.plugin_api.passport
  - agentclaw.community.plugin_api.sandbox_runtime    # SandboxRuntimeClient — ARCA SDK I/O seam (B6)
  - agentclaw.community.plugin_api.secret_resolver    # build_outbound_operation_rule 接 SecretResolver, 收口 Mist→layotto 硬依赖
  - agentclaw.community.utils
  - agentclaw.community.utils.env_utils
```

### Change impact

Owns the engine/device binding lifecycle. Schema changes (binding table, engine config dir) require migration. Health-probe contract is consumed by Prom-style monitoring outside the repo.

### Pool-native creation recovery

An ACTIVE BaaS binding can outlive a failed Bot-link write. After the guarded
repository recovery links it to its Bot, an unconfirmed `pool_initializing`
startup re-dispatches only the existing instance-bound watchdog callback. Both
`create_bot` and `provision_bot` retries also delegate pending confirmation to
the binding's provider before returning an already bound Bot, so a transport
failure after committing the recovered link remains retryable. This does not
allocate another device or repeat container initialization. It
uses the retained callback token and current startup identity; it does not repeat
bootstrap, container initialization, publish polling, or migration. Dispatch
errors propagate so the same creation can be retried without failing the ACTIVE
runtime. A matching persisted confirmation makes recovery idempotent. Legacy
and pre-Native Pool bindings retain their old startup wire. The HTTP adapter
catches `LayoutInitializationConfirmationError` through the devices contract,
not through the concrete Skills Pool implementation.

Pool data-init dispatch passes an immutable binding/device/startup/claim token
to the worker. Its `in_progress`, `completed`, `failed` and temporary credential
writes use the same Binding→Bot guarded transaction as the dispatch lease;
an expired worker cannot overwrite its replacement's state. Current checkpoints
renew the lease without changing Bot state. A delayed LLM result must pass a
claim checkpoint before bounded downstream sync; a superseded result is dropped.
Legacy/manual calls without a claim retain the existing BotService write path.

### Single-instance restart authorization

Restart accepts the resolved Bot's current Owner, configured `admin.super_admin`, or an explicit
Bot collaborator (MEMBER or ADMIN). Resolve the Bot by the binding-owned
`device_props.bolt_id` and `entity_id`; the draft Bot binding is not the runtime
binding. Public visibility and space-derived grants do not authorize this
mutation. Missing authorization dependencies fail closed. BaaS receives the
actual caller identity. Binding provider, environment and ACTIVE checks apply
to every role before any restart request.

The binding's entity remains a resource locator after handoff. Bootstrap resolves
the Bot before querying Passport with that immutable entity, including when the
compatibility request names the current Owner. Only unlinked historical bindings
retain standalone entity ownership; a linked Bot's former Owner gains no grant
from the retained binding identity.

## BaaS restart progress telemetry

`BaasRestartPublishPollHandler` emits one allowlisted INFO event for each
observed result or retry of the current ordinary-Bot restart. The event answers
which Bot/engine is still waiting and how long this publish attempt has waited.
It does not change task scheduling, the 1,800-second failure deadline or Bot state.

```text
event=bot_restart_progress phase=publish_poll entry_point=baas.restart.publish_poll bot_id=example-bot binding_id=42 publish_id=1001 request_id=example-request engine_type=claude_code env=pre status=PENDING elapsed_s=601.000 timeout_s=1800
```

The identifiers above are illustrative, not production evidence.

- Correlate `bot_id`, `binding_id`, `request_id` and `publish_id`; `publish_id=-`
  means the current accepted intent is still adopting its provider workflow.
- `engine_type` comes from the current Bot's `active_engine`; absent metadata is
  explicitly `unknown`, never silently classified as OpenClaw. Normal polling
  reuses its existing Bot read. Workflow adoption uses one best-effort metadata
  read; a failure does not interrupt recovery or print the exception body.
- `elapsed_s` uses the task's persisted `started_at_epoch_s` and the existing
  clock, clamped at zero for clock skew. The ordinary coding submission creates
  this timestamp after its pre-restart backup/preparation, so backup is not
  included. It is not the age of the Bot or the 24-hour queue retention period.
- `status` is the publish-poll observation: `ADOPTING`, `PENDING`, `RETRYING`,
  `ACTIVE`, `FAILED` or `TIMED_OUT`. `ACTIVE` here means provider publish success,
  not proof that the engine is ready. Continue to use the readiness contract.
  Superseded requests and uncommitted intents emit no progress event.
- To detect a slow in-flight publish, filter `phase=publish_poll`, group by the
  current Bot/request and select `ADOPTING`, `PENDING` or `RETRYING` with
  `elapsed_s > 600`. Do not add another ten-minute continuous-trigger window.
  Terminal observations are separate; absence of new events is not proof of
  recovery because the poll worker or log delivery could also have stopped.

Correlation values are single-line, field-injection-safe and bounded. The event
never serializes Bot config, credentials, request payloads or provider responses.
The logger emits at the existing task cadence (normally ten seconds for provider
polling); it has no new timer, state table or monitoring side effect. Actual alert
routing and delivery must be verified separately after deployment.
