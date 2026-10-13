# Provider collaboration scope

## Requirement

Local Bots can execute on the owner's machine. Provider administration must be
able to restrict their collaboration to that Human and Bots with the same
persisted owner, while preserving existing permission behavior for other
Providers. Provider connection mode and collaboration scope are independent.

## Contract

`POST /providers` and `PATCH /providers/{provider_id}` accept an optional
`collaboration_scope`: `standard` or `same_owner`. Provider information returns
the effective value. Omission/null preserves existing PATCH configuration;
missing stored scope resolves to `standard`. Unknown values are rejected.
Existing Provider admin authentication and Human ownership checks apply.

The policy lives in the Provider table's existing config JSON. It is not copied
to Bots, selected by slug, or supplied by the Bot/Bridge. To restrict local Bots,
configure the local Provider with `{"collaboration_scope":"same_owner"}`.
No table, schema migration, connection protocol, or frontend change is required.
Ordinary Provider administration may tighten the policy, but cannot relax
`same_owner` to `standard`; the storage write also rejects stale configuration
that would otherwise overwrite a concurrent tightening.

## Runtime authorization

- Either restricted endpoint activates the boundary; direction does not matter.
- Bot ownership uses authoritative `created_by`, not a client owner claim, an
  ID suffix, shared Provider affiliation, or intersection of owner lists.
- Every participating Bot and authenticated Human must have the same owner.
  Unknown owners are denied. Same-owner private Bots can collaborate without a
  friendship edge. Hidden status and existing actor authentication still apply.
- Apply the boundary before public/friend/grant/organization exemptions for
  A2A reachability, connection/admission, group creation and membership changes.
- Groups containing restricted Bots must remain private. Human invitation,
  workbench subscription/send/abort and history reads check current group and,
  where relevant, session membership while the group boundary is active; a
  stored invitation/grant cannot widen it. Historical session snapshots do not
  retain this group restriction once the last restricted group member leaves.
- Membership mutations check the current proposed member set before existing
  optimistic group version fencing commits it.
- No permanent group owner is stored. When the last restricted Bot leaves the
  current participant set, this additional boundary no longer applies; the
  existing permissions still apply. Rejoining immediately checks all members.
- An unbound legacy Bot retains existing rules. A bound Bot whose Provider is
  missing, malformed policy, or failed DB lookup raises an error, not a fallback.

## Persistence and access cost

Authorization projects actor ID, owner and affiliation/policy in one fresh,
environment-scoped indexed Bot/Provider LEFT JOIN. Each call reads at most 256
actor rows and performs no writes. IDs are deduplicated by the core before the
query. Larger batches fail explicitly. No authorization-policy cache is added.
Group operations may perform both a full-member check and pair reachability
checks; this is an additional bounded query per policy check. Workbench session
checks add one session read when a restricted group and a session ID are
supplied. Provider metadata
PATCH performs one conditional write and the existing read/cache invalidation.

SQLite tests exercise ordinary and maximum batches, tenant filtering, missing
Provider projection, fresh disabled-state reads, DB failures, and atomic stale
policy rejection. MySQL uses JSON_UNQUOTE/JSON_EXTRACT in its conditional write;
real MySQL execution and sustained-load testing remain CI/deployment validation.

## Acceptance evidence

- `bcs-bot/tests/provider_collaboration_scope.rs`: same-owner Bot/Human accepted,
  foreign participants rejected, last restricted member removal clears only the
  additional boundary, rejoining reapplies the current policy.
- `bcs-bot-store/tests/provider_collaboration_scope.rs`: the bounded SQL projection
  and monotonic metadata contract, including rejected-write atomicity.
- `bcs-group/tests/provider_collaboration_scope.rs`: actual Human invitation is
  denied while a restricted Bot participates and resumes legacy behavior after
  it leaves, using the same valid invitation token.
- Group scope tests also exercise fenced public/private membership mutations and
  Workbench access with a stale foreign Session grant, including recovery after
  the last restricted group participant leaves.
- `bcs-http/tests/unit/provider_scope.rs`: omitted/null request compatibility,
  exact scope values, HTTP 400 for invalid values, effective policy responses
  and corrupt Provider configuration rejection.
- `bcs-message/src/tests/provider_scope.rs`: actual group/session history reads
  reject Public/foreign callers even with an owned-Bot view, accept the owner
  and an owned Bot, and reject a mixed-owner Session while the group is restricted.
- Bot/group/message-flow/edge-permission/message/HTTP suites preserve standard
  Provider and unbound legacy behavior.
