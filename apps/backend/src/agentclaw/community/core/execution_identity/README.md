# Execution Identity

## Context Boundary

```yaml
purpose: Persist and activate the principal identity used by a Bot's AgentPass credentials.
provides:
  - ExecutionIdentityServiceProtocol
  - RuntimePassportTokenUpdaterProtocol
  - ExecutionIdentityBinding
  - OwnerHandoffExecutionPlan
  - BotExecutionIdentityBindingModel
consumes:
  - BotRepository
  - ExecutionIdentityRepositoryProtocol
  - PassportPlugin
  - RuntimePassportTokenUpdaterProtocol
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.bot_management
  - agentclaw.community.log
  - agentclaw.community.plugin_api
  - agentclaw.community.utils
```

### Change impact

This module owns only AgentPass execution identity. It does not own Bot
management authorization, `ac_bots.owner_id`, or any filesystem, container,
skill, MCP, BaaS, BCN, or Proxy resource address. A missing ACTIVE binding is
defined as the legacy state and resolves to the Bot's unchanged owner ID.

B5 adds a read-only `plan_owner_handoff(bot_pk, new_owner_id)` service contract.
Without an ACTIVE independent binding, the future handoff must reissue for the
new Owner. An ACTIVE binding (including STAFF) remains independent. PENDING
identity changes block the plan; personal/default Bots are refused. No public
handoff route, Owner update, or automatic reissue is introduced. The future
orchestrator must revalidate the plan under its concurrency guard and reconcile
partial remote outcomes. Activation requires ISSUED status and, when known,
the same pending credential ID before runtime injection.
