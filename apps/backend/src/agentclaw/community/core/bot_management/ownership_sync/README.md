# Bot ownership sync

Best-effort delivery of the current committed Backend Owner to BCS. This is a
read-only projection and explicit post-commit notification boundary, not an
Owner-change business API or a durable recovery system.

## Context Boundary

```yaml
purpose: Read current committed Bot ownership and submit bounded best-effort delivery and manual repair through the shared sync runtime.
provides:
  - BotOwnershipSyncService
  - OwnershipSyncRepairService
  - OwnershipSyncOperations
  - notify_bot_ownership_sync
  - DisabledBotOwnershipSync
  - DisabledOwnershipSyncRepair
  - build_snapshot
consumes:
  - BotOwnershipSyncProtocol
  - OwnershipSyncReadRepository
  - BcsOwnershipPlugin
internal_dependencies:
  - agentclaw.community.core.bot_management.bcn_identity
  - agentclaw.community.core.repository.protocols.bot
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.api.bot_ownership_sync
  - agentclaw.community.core.bot_collaborator.manager_sync.diagnostics
  - agentclaw.community.core.bot_collaborator.manager_sync.repair
  - agentclaw.community.core.bot_collaborator.manager_sync.retry
  - agentclaw.community.core.repository.protocols.bot.ownership_sync
  - agentclaw.community.kernel.bot_manager_sync
  - agentclaw.community.kernel.bot_ownership_sync
  - agentclaw.community.plugin_api.bcs_ownership
  - agentclaw.community.log
```

### Change impact

Under architecture Rule 3, handoff, the notification helper, and manual repair
consume the same transport-free `BotOwnershipSyncProtocol` Service API. The
architecture gate declares only those exact file/module edges, including
`ownership_sync/repair.py` to `agentclaw.community.api.bot_ownership_sync`.
These declarations neither permit arbitrary Core-to-API imports nor exempt
any Protocol from conformance coverage; HTTP adapters remain outside Core.

Owner handoff consumes Service API v1 through the exception-isolating
`notify_bot_ownership_sync` helper after `finish()` commits. The composition root
binds the notifier to the existing managers dispatcher and lifecycle; admission
is memory-only, never a delivery receipt. Owner delivery consumes Plugin API v1
and the independent v1 ownership reader; no manager roster or Space query is a
prerequisite. Each attempt reads fresh facts using tenant/env/PK, addresses BCS
with the established BCN address (service entity or retained conversion reference), and sends current `owner_id` as personnel.
Invalid/missing identity fails closed; service addressing never substitutes the mutable Owner for entity.

`BotOwnershipSyncService.sync_once(key)` returns `CONFIRMED`,
`BOT_MISSING_NOOP`, `DRIFT`, or `FAILED`. Only matching, confirmed Owner delivery
causes the dispatcher to arrange a subsequent fresh managers read. Retry policy
is at most three sends within 15 seconds per work kind; it provides no durable
or cross-replica ordering guarantee. A failed round never freezes later work.

`OwnershipSyncRepairService.request_bot(key: SyncKey) -> SubmissionResult` and
`request_page(*, avernet_tenant: str, env: str, after_pk: int, limit: int) -> RepairPage`
submit current facts through
ordinary admission without an arbitrary Owner override. They remain internal. The administrator-only operations adapter admits a single
service Bot by PK; it cannot override the Owner and never claims delivery confirmation.
The exact signatures, lifecycle requirements, risk, and deployment steps are in
[the operations guide](../../../../../../docs/bot-ownership-sync-operations.md).
Consumer conformance is `tests/community/contracts/test_bcs_ownership.py`, using
`world` to exercise the real service and assert recording-plugin calls. TEST and
CORP_TEST recordings do not prove real BCS or AAC behavior.
