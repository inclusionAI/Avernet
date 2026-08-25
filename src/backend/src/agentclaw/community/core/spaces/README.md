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
consumed_by:
  - "adapters/http/openapi_v1/spaces — public Space and member operations"
  - "core/market_favorites — Space membership authorization"
  - "core/bot_collaborator — Team Space membership guard for public Bot editors"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.plugin_api.models
  - agentclaw.community.log
  - agentclaw.community.plugin_api.skill_center_client
  - agentclaw.community.plugin_api.staff_dept
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

Team-space creation keeps its OB transaction open until required Skill Center
team creation succeeds; a Skill Center failure rolls the local rows back.
Role and creator invariants are authorization rules. Changes affect every
Space-scoped feature, not only member management. The tenant column, tenant
guard registration, and tenant-leading unique keys are one isolation mechanism
and must change together.
