# Skill AUTO approval handler prerequisite

Relates to [Avernet #2452](https://github.com/inclusionAI/Avernet/issues/2452),
WorkOrder AUTO PR #2463, and the follow-on Skill feature PR #2450.

Owning module: `agentclaw.community.core.skill_center`, with the atomic
repository command in `community.core.repository`. Relevant accepted decisions:
[Skill Space Ownership](../../../docs/adr/0003-skill-space-ownership-is-relational.md)
and [Space governance vs Skill Grant](../../../docs/adr/0007-separate-space-governance-from-skill-grants.md).
This slice changes no public HTTP API or response schema. Local validation:
Skill/WorkOrder/repository/DI tests and the architecture/HTTP fast gates;
OCB integration, deployment, and live behavior remain unverified.

## Scope and rollout boundary

`ac_skill_space_binding.auto_approve_editor_requests` is a per-Skill boolean
with an ORM and database default of `false`. Apply
`2026_10_08_skill_editor_auto_approval.sql` to existing databases before
running code that reads the column. This prerequisite exposes no public
configuration API and adds no second feature flag. The normal Skill editor
request still creates a manual order when the field is false. If it is true
before trusted WorkOrder AUTO integration, the Skill request rejects before
creating an order or notifying the Owner. This exception is a temporary
integration guard, not a user-visible product mode to deploy separately.

## Internal contract for WorkOrder

`SkillCollaboratorApprovalHandlerProtocol.process_auto(work_order_id)` is the
Skill-owned internal completion seam. WorkOrder must verify the persisted
`approval_mode=AUTO` through a trusted caller boundary, create **no** human
approver row, and claim the Skill order as `PROCESSING` before invoking it.
The handler does not accept a user-supplied reviewer or trust caller-supplied
Skill identity: it locks the persisted order, checks `SKILL_COLLABORATOR` and
`biz_id`/`biz_data`, and rechecks the Team Space binding, enabled setting,
non-offline Skill, active Owner, and locked active applicant membership.

The Skill repository commits the `MANAGER` Grant, `APPROVED` order with
reviewer `SYSTEM`, and one applicant `SKILL_COLLABORATOR_REVIEWED` notice in
one transaction. An existing active Manager Grant keeps its original audit;
a revoked Manager Grant is reactivated. An active Owner is never downgraded.
Retrying an already SYSTEM-approved order returns its original result without
regranting or renotifying. Any validation or database failure rolls back
the Grant, terminal state, and success notice together.

`PROCESSING` is intentionally a local string until #2463 lands its WorkOrder
status contract. This PR does **not** add generic WorkOrder AUTO creation,
caller authentication, claim/failure recovery, persisted approval mode, or
the final Skill request routing. When integrating #2463, call
`process_auto(work_order_id=...)` instead of the human `process(...)`; for
Skill, do not run generic finalization or send success notification before
the handler succeeds. #2450 will replace the temporary request exception
with the trusted AUTO call after that contract lands. The public generic
events admission policy is unchanged by this prerequisite.
