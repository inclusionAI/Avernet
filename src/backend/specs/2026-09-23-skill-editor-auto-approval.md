# Team Space Skill editor auto-approval contract

Issue: `inclusionAI/Avernet#2452`

## Persisted policy

`ac_skill_space_binding.auto_approve_editor_requests` is a required boolean
whose database default is `false`. The setting belongs to one Skill binding;
it does not apply to another Skill in the same Space.

Only the active `OWNER` Grant for a live Team Space Skill may use:

- `GET /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-approval-policy`
- `PUT /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-approval-policy`

Both return `{ "auto_approve_editor_requests": boolean }`. Personal Space
Skills and non-Owners are rejected by the domain repository rather than by
transport-only policy.

## Integration gate

The current public WorkOrder model has no `approval_mode`, `SYSTEM` approval
identity, trusted internal AUTO caller contract, or atomic AUTO callback and
notification ordering. OCB/corp sources are not present in this checkout, so
their request forwarding, dependency injection, and public-module gitlink
cannot be verified here.

Consequently this change deliberately stages only the independently safe
policy surface. If the persisted setting is enabled, the existing Skill
editor-request repository fails closed before creating a manual WorkOrder or
notifying the Owner. It does not silently treat enabled AUTO as MANUAL, and it
does not expose an AUTO selector on the public request or generic event API.

Activation requires a later integrated change that proves all of the
following together:

1. persisted `MANUAL`/`AUTO` WorkOrder mode and `SYSTEM` audit identity;
2. only a trusted internal Skill call may select AUTO;
3. the callback rechecks WorkOrder identity, current Team membership, Skill
   binding and policy, and grants exactly one `MANAGER` Grant;
4. a success notification is committed only after the Grant succeeds; and
5. Avernet public and OCB corp contract/DI/gitlink tests pass against the same
   protocol version.
