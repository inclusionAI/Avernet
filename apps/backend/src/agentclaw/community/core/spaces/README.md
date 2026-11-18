# `agentclaw.community.core.spaces`

Owns Space lifecycle, membership, roles, and the centralized authorization rules
used by every Space-scoped feature. Personal-space initialization and team-space
creation persist the Space and its creator ADMIN membership in one transaction.

`ADMIN` is the only canonical role. `OWNER` and `ADMINISTRATOR` are retained
only as compatibility aliases for old clients and historical rows; they must not
be used for new writes.

Application-only OpenAPI access is deliberately not decided here. The HTTP
adapter inventories every operation as `AdmissionMode.REFUSED` until a
Space-specific delegation model is approved.

## Context Boundary

```yaml
purpose: "Own Space lifecycle, membership, roles, and reusable Space authorization decisions."
provides:
  - SpaceService
  - SpaceManagementService
  - SpaceManagementServiceProtocol
  - SpaceMemberService
  - SpaceAccessService
  - SpaceAccessServiceProtocol
  - SpaceModel
  - SpaceMemberModel
  - SpaceRecord
  - SpaceMemberRecord
consumes:
  - "SpaceRepositoryProtocol (core.repository) — transactional persistence for spaces and members"
  - "SkillCenterClient (plugin_api) — mirrors a pending team-space creation to SC before local commit"
  - "StaffDeptPlugin (plugin_api) — resolves trusted creator and member nickname snapshots"
  - "WorkOrderServiceProtocol (api) — creates member lifecycle notifications"
  - "BotManagerSyncProtocol (api) — best-effort manager-set refresh hint after membership writes and whole-Space deletes"
consumed_by:
  - "adapters/http/openapi_v1/spaces — public Space and member operations"
  - "core/market_favorites — Space membership authorization"
  - "core/bot_collaborator — Team Space membership guard for public Bot editors"
internal_dependencies:
  - agentclaw.community.core.bot_collaborator.manager_sync.notifications
  - agentclaw.community.api.bot_manager_sync
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.kernel.bot_manager_sync
  - agentclaw.community.plugin_api.models
  - agentclaw.community.log
  - agentclaw.community.plugin_api.skill_center_client
  - agentclaw.community.plugin_api.staff_dept
  - agentclaw.community.utils.avernet_tenant
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
  - agentclaw.community.utils.work_no
```

### Change impact

Team-space creation keeps its OB transaction open until required Skill Center
team creation succeeds; a Skill Center failure rolls the local rows back.
Role and creator invariants are authorization rules. Changes affect every
Space-scoped feature, not only member management. The tenant column, tenant
guard registration, and tenant-leading unique keys are one isolation mechanism
and must change together.

## Team-space deletion

`DELETE /openapi/v1/bots/spaces/{space_id}` soft-deletes a Team Space only
when it is empty.
`SpaceDeletionService` rejects a personal space before touching storage, then
`SpaceRepositoryProtocol.delete_team_space` performs the final-state checks in
one transaction: lock the Space row, require a `TEAM` space, require the actor's
current effective membership to be canonical `ADMIN`, and reject the delete
while the Space still owns any non-deleted Bot or any Skill binding. It then
writes `deleted_at`/`deleted_by` and disables the member rows; it does not
delete Bot, Skill, knowledge or remote Skill Center state. After commit the
service sends a best-effort `BotManagerSync` Space-change hint; a delivery
failure is logged and never changes the committed result. Callers must treat
success as a local soft delete, not as cleanup of dependent resources.

## Script maintenance exception

The ordinary HTTP `/api/spaces` surface uses `SpaceManagementServiceProtocol`.
Its explicit maintenance policy permits removing or demoting the creator and
removing the last ADMIN; the normal page/OpenAPI governance remains unchanged.
The HTTP maintenance adapter requires an authenticated super-admin using the
existing configured staff whitelist; core remains transport-agnostic.
Existing creator-based authorization on other entrypoints is not revoked by
removing that creator's membership here.

Both creation commands accept only `TEAM` (default when omitted), designate the
initial ADMIN by employee work number, and call Skill Center inside the local
transaction. The caller is not auto-added. All operations resolve the server
environment with existing `get_current_env` wiring; requests cannot select it.
Names retain the existing creator/environment/active-TEAM comparison behavior.
SC rename/delete, member synchronization, skill handoff and distributed creation
compensation are deliberately outside this maintenance contract. A failed local
commit after SC success can leave an external team and must not be blindly retried.

The new HTTP SQLite suites cover all eleven entrypoints, environment boundaries,
real persistence, atomic rollback and idempotent membership/role operations.
SQLite does not establish production row-lock concurrency guarantees.
