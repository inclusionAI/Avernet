# Self-review

Date: 2026-09-28. Scope: the TC single-chat resource withdrawal change against
`cd36acdfc48eec0597126b2cdb1197f44a45d8f7`.

This is an implementation self-review, not an independent reviewer sign-off.
No additional agent or external code-review service was used.

## Reviewed invariants

- Authorization remains in existing single-chat service/route paths. Only
  explicit personal/friend/openapi single-chat scope values request an outbox
  insert; unknown/group scope does not. No BCS, frontend or group handler edit.
- Repository uses `transactional_orm_session`, not the production autocommit
  session, for the delete/insert pair and outbox writes. First-transition CAS
  prevents duplicate facts; insert failure propagates and rolls back deletion.
- Delivery is outside the user request and outside core transport policy.
  Tenant-scoped eligibility, database time, random claim tokens and unexpired
  lease checks prevent stale completions. Downstream effects still need ECB
  idempotency; this is intentionally at-least-once, never advertised exactly-once.
- HTTP 2xx alone cannot acknowledge. Receipt must match stable event_id with
  accepted exactly true and pending/applied status. TC `accepted` cannot be
  presented as completed business withdrawal.
- No default successful production fake or copied ready-flow dummy token.
  Enabled configuration resolves the named service secret and fails closed;
  startup eagerly validates despite lifecycle-discovery exception handling.
  Logs persist only stable error codes, not responses or credentials.
- Replay is blocked-only, tenant/attempt CAS with required actor/reason and
  confirmation. It preserves age/lifetime attempts/identity. Only latest replay
  metadata is stored; full audit history relies on the operator change record.
- Schema is additive; migration must precede the writer even with delivery OFF.
  Pause retains facts. Rolling back to the old writer needs deletion pause or
  audited compensation, not an undocumented assurance of future replay.
- No schema/content GC, upload redesign, arbitrary downstream file-ID handling,
  public operator route, historical backfill or group support was added.

## Corrections made during implementation

- Replaced generic ORM-session assumptions with explicit real transactions.
- Added lease-expiry fencing, startup fail-closed validation, bounded crash
  recovery and per-tenant selection.
- Fixed module README provides/deps metadata after architecture checks reported
  omissions; checks themselves were not weakened.
- Added full HTTP entrypoint rollback coverage and real-transport redirect test,
  plus active lifecycle and operator command/exit-code coverage.
- Kept new config separate from the pre-existing near-1,000-line config files.

## Remaining risks / disposition

1. **Merge verification open:** unified Singlebox failed; see `validation.md`.
   Foundation targeted tests and combined full suite passing do not waive it.
2. **Release blocked:** ECB contract remains draft and no real ECB/MySQL/UI
   validation exists. OFF is mandatory until the runbook gates are satisfied.
3. **Operational limits documented:** serial delivery, transport (not total
   wall-clock) timeout, drain-on-shutdown, required storage sizing while paused,
   alert routing/SLA unset, and only latest replay metadata.
4. **Submission shape:** prepare two stacked PRs; do not bypass the line budget.
   The preparation step did not authorize publishing. The user subsequently
   authorized commit/push on 2026-09-29 and retained PR creation themselves.
