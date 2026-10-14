# Ordinary HTTP Space maintenance

This delivery adapter consumes the environment-scoped Space maintenance Service
API. It does not replace page/OpenAPI governance. All eleven endpoints use
`get_current_user` and require the authenticated
`staffId` in `user_config.admin.super_admin`, matching existing super-admin
operations. Missing or malformed whitelist configuration denies access.
Request `admin_user_id` is the designated creator, never an authentication credential.
Scripts must supply credentials accepted by the deployed AuthPlugin; an employee
ID header alone is not production authentication.

## Context Boundary

```yaml
purpose: Translate script maintenance HTTP requests and domain results.
provides:
  - router
consumes:
  - SpaceManagementServiceProtocol
internal_dependencies:
  - agentclaw.community.adapters.http.auth
  - agentclaw.community.adapters.http.spaces_management
  - agentclaw.community.api.space_management_service
  - agentclaw.community.core.access.admin_scopes
  - agentclaw.community.core.spaces.management_models
  - agentclaw.community.di
  - agentclaw.community.log
  - agentclaw.community.plugin_api.skill_center_client
```

### Change impact

Additive endpoints for trusted scripts; no schema migration or existing endpoint
change. Creation requests expose `space_type` with only `TEAM` allowed and default
`TEAM`. Identifiers address `ac_space.id`, not Skill Center ids. Server environment
resolution is unchanged. Creation uses employee work numbers and a designated
creator/ADMIN, not the caller. See the Space domain's maintenance policy exception.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/spaces` | Create TEAM with designated ADMIN |
| POST | `/api/spaces/initialize` | Create TEAM and initial MEMBER batch |
| GET | `/api/spaces` | Paginated TEAM search |
| GET | `/api/spaces/{space_id}` | TEAM detail and active membership counts |
| POST | `/api/spaces/{space_id}/update` | Rename TEAM |
| DELETE | `/api/spaces/{space_id}` | Delete only if no membership rows exist |
| POST | `/api/spaces/{space_id}/delete-with-members` | Delete TEAM and all membership rows |
| GET | `/api/spaces/{space_id}/members` | Paginated member search |
| POST | `/api/spaces/{space_id}/members/batch-add` | Add MEMBERs, preserve existing active roles |
| POST | `/api/spaces/{space_id}/members/batch-remove` | Remove memberships, absent users are no-ops |
| PUT | `/api/spaces/{space_id}/members/roles` | Atomically set ADMIN/MEMBER, identical roles are no-ops |

Batch membership commands accept 1–500 employee work numbers and deduplicate
trimmed ids. Role updates reject the entire batch if any active membership is
missing. Re-adding an inactive membership reactivates it as MEMBER. Initialization
accepts zero through 500 optional member ids. Empty-only deletion counts inactive
rows too. Maintenance may remove/demote the final administrator and creator.

Successful responses use `code`, `message`, `data`, `request_id`; creation returns
201, other successes 200. Failures use 422 for invalid inputs, 404 for inaccessible
space ids, 409 for policy conflicts, 502 for SC creation failure and 500 for
unexpected persistence failures. Authentication retains existing AuthPlugin/app
error handling (including login redirects). Empty/anonymous resolved identity
returns 401; non-super-admins return 403. Request ids are also returned in
`X-Request-ID` after route handling.

The tests under `tests/community/adapters/http/spaces_management` exercise real
HTTP, service and repository implementations with SQLite, substituting external
SC/staff capabilities and login identity. Application wiring tests additionally
exercise the shared `get_current_user` path with a test AuthPlugin, the real
super-admin whitelist check, and the production composition root.
