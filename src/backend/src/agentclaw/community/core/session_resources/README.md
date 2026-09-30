# Session Resources

## Context Boundary

```yaml
purpose: Own session-scoped uploaded-resource state, authorization, and materialization transitions.
provides:
  - ResourceWithdrawalWorker
  - ResourceWithdrawal
  - SessionResourceService
  - SessionResourceRecord
  - SessionResourceRepositoryProtocol
consumes:
  - ResourceWithdrawalPublisherPlugin
  - ResourceWithdrawalRepositoryProtocol
  - DatabasePlugin
  - HttpClient
  - TaskQueueService
  - DeviceContextResolver
  - DeviceAdapterTransport
  - TokenVault
internal_dependencies:
  - agentclaw.community.api
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.di
  - agentclaw.community.kernel
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.session_resources
  - agentclaw.community.core.task_queue
  - agentclaw.community.log
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect file-upload control-plane APIs, BaaS transfer calls, Engine
materialization dispatch, and the point at which Chat resources become ready.
Every state transition must preserve owner, Bot, and session isolation.

Single-chat deletion persists a withdrawal fact in the same real transaction as
its terminal state transition. The worker delivers at least once, tenant-scoped,
and default-paused; ECB owns tombstones, authorization and shared references.
Group/unknown scopes do not emit withdrawal events. See
`docs/contracts/tc-resource-withdrawal-v1.md` and the feature runbook.
