# `agentclaw.community.core.service_bot`

Service-bot domain — bot publication facade, BAAS service binding, sub-resource management.

## Context Boundary

```yaml
purpose: "Service-bot domain — bot publication facade, BAAS service binding, sub-resource management."
provides:
  - "BotPublishService"
  - "BaasService"
  - "ServiceBot SQLAlchemy models"
consumes:
  - "BotManagement"
  - "DeviceService + repo"
  - "SkillsPool layout state"
  - "SystemConfig"
  - "PassportPlugin"
internal_dependencies:
  - agentclaw.community.core.common_config
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.channel    # per-stage engine_overrides (DingTalk channels) reader at verify/online promotion
  - agentclaw.community.core.config_compose
  - agentclaw.community.core.caller_identity.credential  # CallerToken used by BaaS outbound-rule update
  - agentclaw.community.core.devices
  - agentclaw.community.core.quality.services
  - agentclaw.community.core.skills_pool
  - agentclaw.community.core.system_config
  - agentclaw.community.core.task_queue    # durable publish stage tasks (enqueue + handlers + worker)
  - agentclaw.community.core.workspace
  - agentclaw.community.di
  - agentclaw.community.kernel.bot_config
  - agentclaw.community.kernel.device_dto    # neutral OutBound / ResourceSpecification DTOs (B6)
  - agentclaw.community.kernel.lifecycle    # PublishTaskLifecycle registers durable task handlers
  - agentclaw.community.log
  - agentclaw.community.plugin_api.approval_workflow           # antprocess approval workflow for publish approval
  - agentclaw.community.core.devices.services.device_filesystem    # teclaw build-time file promotion (TeclawFilePromotion)
  - agentclaw.community.plugin_api.engine_ext_client
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
```

### Change impact

Publication path is the user-visible go-live for bots — bugs here block customers from publishing.
Service artifacts freeze the persisted Skills Pool layout state at build time;
changes to the Skills Pool contract or repository therefore affect publication
compatibility and must be reviewed together with this module.

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
