# Bot Inventory

Bot Inventory builds transport-agnostic display models and action policy for the
personal-cloud and local-bot public API surface.

## Context Boundary

```yaml
purpose: Aggregate personal cloud Bots and local desktop Bots into user-scoped inventory view models and action decisions.
provides:
  - BotInventoryItem
  - BotInventoryService
  - BotLifecycleView
  - LocalBotWorkflowService
  - BusinessSpaceContextProtocol
  - ServiceLifecyclePort
consumes:
  - BotInventoryBotPort
  - DesktopBotInventoryPort
  - BusinessSpaceContextProtocol
  - ServiceLifecyclePort
internal_dependencies:
  - agentclaw.community.core.bot_inventory
  - agentclaw.community.core.errors
  - agentclaw.community.core.desktop_bot.errors
  - agentclaw.community.core.workspace
  - agentclaw.community.plugin_api
```

### Change impact

Changes affect the public `/openapi/v1/bots/all` and
`/openapi/v1/bots/local` views that compose existing Bot management and desktop
Bot services.  The module does not own business-space membership, storage, or
CRUD; replacing the fallback space context with the business-space owner's
Service API changes filtering/visibility behavior without changing this core
boundary.

The composition root binds `DesktopInventoryAdapter` to the desktop port.
The delivery adapter translates BaaS restart errors (including HTTP status and
transport failures from the pool update path) into
the port's `DesktopBotServiceError` contract. The local workflow maps that
provider-neutral error, including subclasses, to `BotInventoryUpstreamError`,
which the HTTP adapter renders as 502. Personal-space and ownership checks
still run before restart; unrelated programming errors are not reclassified.

## Inventory status contract

`GET /openapi/v1/bots/all` exposes the stored Bot status for every card kind
(`PENDING`, `PROVISIONING`, `ACTIVE`, etc.), matching `GET /openapi/v1/bots`.
Service cards additionally expose a bounded `publish` object with `id`,
`status` (the original publication status), and `version`. This is the
publication represented by that card, not necessarily the latest publication.
Non-service cards and service cards without a publication return `publish: null`.

This changes the meaning of service-card `status`: clients must migrate away
from checking it for `draft/deploying/prestable/running/offline`. Use
`publish.status` for the publication vocabulary, or the unchanged `display_state`
for normalized card presentation. Keep using `actions` for available operations.
The legacy `/api/bots/search`, `/bots`, and `/lifecycle` contracts are unchanged.
Existing publication identifiers, diagnostic state, edit locks, and action policy
remain intact. Projection reuses the existing batched publication read.

## Explicit collaborator visibility

The default personal inventory combines the caller's own Bots in that personal
space with Bots explicitly shared with the caller, including Bots assigned to
other personal or team spaces. An explicit team-space selection remains a
space filter. Space membership alone does not add other spaces' Bots to the
personal view. Existing effective permission checks still reject revoked team
membership; listing does not grant access to Space metadata or change Bot actions.

Collaborator candidates are resolved using their primary key and owner as well
as Bot ID, under the existing tenant/environment scope. Sources are combined
before service publication expansion and final card pagination. Returned cards
retain the source owner and space. Owner-only creation template snapshots are
not loaded or returned for collaborator cards. Inaccessible Space metadata is represented
only by its identifier. Application grant-filtered inventories remain restricted
to the delegator's own Bots and never expand through collaborator relationships.
