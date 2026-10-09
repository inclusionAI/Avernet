# Team Space Skill editor auto-approval integration

Issue: [Avernet #2452](https://github.com/inclusionAI/Avernet/issues/2452).
This spec supersedes the staged fail-closed request behavior described in
`2026-10-08-skill-auto-handler-prerequisite.md`. The existing binding column
and migration from #2533 remain authoritative; this change adds no second flag.

## Public contract

`GET/PUT /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-approval-policy`
read or replace `{"auto_approve_editor_requests": boolean}`. Only the active
OWNER Grant of a live Team Space Skill may read or write. Other users and
Personal Space Skills cannot manage this policy. PUT requires a strict boolean
and rejects unknown fields. New and existing bindings default to false after
the binding migration is applied.

`POST /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests`
remains the Skill product's application entry. Its response fields are
unchanged; status is PENDING for manual review or APPROVED after AUTO succeeds.
The Skill module checks the applicant, Team binding, live Skill, Owner, active
membership, existing Grant, and pending request before selecting the mode.
The MANUAL path retains the Owner's approval task and notification. AUTO calls
the WorkOrder Service in-process with no human approver and the applicant as
the sole result recipient; both user-facing generic events routes reject AUTO.
The applicant cannot choose the approval mode. An AUTO failure is an error, not
a successful editor-request response.

## Transaction and failure contract

WorkOrder persists an AUTO Skill order as PROCESSING in the creation transaction. Its repository
opens one database transaction, verifies the persisted AUTO Skill identity,
calls `SkillEditorRequestRepositoryProtocol.apply_auto_skill_editor_request`
with the same SQLAlchemy Session, then writes APPROVED/SYSTEM and one applicant
result notice before commit. The Skill step rechecks the live Team binding,
enabled policy, active Owner, and applicant membership, and writes only the
MANAGER Grant. A rejected qualification, Grant write, terminal-state write, or
notice write rolls back the completion transaction. An active Manager keeps
its original audit, a revoked Manager is reactivated, and an active Owner is
never downgraded. Existing PENDING manual orders are not rewritten when the
policy changes.

The PROCESSING creation is committed before completion; it cannot be rolled back
by that later transaction. For callback-backed non-Skill AUTO types, external
callback success cannot participate in the local database transaction. If
local persistence then fails, the work order stays PROCESSING for explicit
reconciliation rather than being marked FAILED despite an external success.
Automatic recovery of that uncertain external window is not provided here.

## Enterprise integration boundary

OCB corp request models, WorkOrder forwarding, and the `ocb-public` gitlink
must be updated and verified separately. An Avernet PR, green local test, or
merge into dev does not prove enterprise availability or deployment.
