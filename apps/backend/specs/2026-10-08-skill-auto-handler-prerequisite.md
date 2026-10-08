# Skill AUTO Grant prerequisite

Relates to [Avernet #2452](https://github.com/inclusionAI/Avernet/issues/2452),
WorkOrder AUTO PR #2463, and the follow-on Skill feature PR #2450.

Owning module: `agentclaw.community.core.skill_center`, with the Grant-only
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

`SkillEditorRequestRepositoryProtocol.apply_auto_skill_editor_request(*,
session, work_order_id, env)` is the Skill-owned **step 3 only** seam. The
trusted WorkOrder caller must verify persisted `approval_mode=AUTO`, create
**no** human approver row, and claim the Skill order as `PROCESSING` first.
It then calls this method with its **existing SQLAlchemy Session**, before
finalizing the order. The Skill method neither opens nor commits a transaction.
It does not write WorkOrder terminal state or result notification.

The method locks the persisted order, checks `SKILL_COLLABORATOR` and
`biz_id`/`biz_data`, and rechecks the Team Space binding, enabled setting,
non-offline Skill, active Owner, and active applicant membership before
writing the `MANAGER` Grant. An existing active Manager keeps its audit;
a revoked Manager is reactivated. An active Owner is never downgraded.
Only `PROCESSING` orders are accepted; WorkOrder owns completed-order retries.

WorkOrder owns its transaction, `APPROVED`/`SYSTEM` terminal state, applicant
result notice, and commit/rollback. The Skill Grant, terminal state, and
notice must share the **same transaction**. If WorkOrder has already committed
the `PROCESSING` claim, that earlier claim is outside this atomic unit and its
recovery must be handled by WorkOrder; the Skill method does not solve it.

`PROCESSING` remains a local string until #2463 lands its WorkOrder status
contract. The merged prerequisite #2533 provided the binding column and
temporary fail-closed request guard; this follow-up replaces its AUTO
completion contract. It does **not** add generic WorkOrder AUTO creation,
caller authentication, claim/failure recovery, persisted approval mode, or
the final Skill request routing. #2450 will replace the temporary request
exception with the trusted AUTO call after #2463 lands. The public generic
events admission policy is unchanged by this follow-up.
