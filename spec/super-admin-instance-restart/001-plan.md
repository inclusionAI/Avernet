# Super-admin corrections and collaborator instance restarts

Base: GitHub inclusionAI/Avernet REL20261023. Worktree: fix-super-admin-instance-restart-20261022. Final topic will be rebased onto a fresh remote REL20261023 before PR creation.

## Requirements and authorization

- PUT /api/v1/user-lists/correct requires authenticated membership in admin.super_admin. Being in whitelist.operator_names alone does not authorize it. Membership reads and unrelated operator endpoints retain their existing policies.
- POST /api/v1/devices/{binding_id}/restart allows the existing binding Owner, admin.super_admin, or an explicit Bot collaborator at MEMBER or ADMIN level. Resolve the target Bot from server-side binding.device_props.bolt_id plus binding.entity_id using get_by_id_and_owner (the Bot binding_id is the draft binding, not the runtime binding), never from request-supplied owner/Bot identifiers. Collaborator authorization uses the canonical service with explicit=True (including revocation checks); public visibility or space-derived grants do not authorize restart.
- Preserve binding existence, current environment, ACTIVE status and BaaS provider checks before authorization/downstream mutation. Preserve actual caller identity in BaaS requests. Deny when collaborator service or target Bot is unavailable. Apply the same check to the bot-ID entry that delegates to this service.
- Reuse existing config-driven scopes and lazy collaborator resolver; no employee IDs, credentials or private URLs enter public source.
- Log request, permission result, success/failure and elapsed time without authentication material. Existing correction request/success/failure logs remain.

## Implementation and validation

1. Baseline: closest device + correction E2E tests (50 passed).
2. Add failing tests for super-admin/operator independence, allowed collaborator/admin restarts, unrelated/revoked users, wrong Bot, missing services, invalid binding/environment and no downstream calls on rejection.
3. Implement minimal policy changes; exercise real HTTP correction writes and the real device restart service (mock only external collaborators/BaaS), plus routing integration.
4. Run affected tests, backend test suite, lint/architecture checks and diff hygiene. Record exact failures/coverage and fix task-related or PR gate errors.
5. Commit task files, fetch and rebase onto GitHub REL20261023, publish topic and source PR. Inspect actual reviews/checks; do not merge. After mirror synchronization, open an OCB gitlink PR if available.
