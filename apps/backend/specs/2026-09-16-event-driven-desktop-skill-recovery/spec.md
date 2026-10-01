# Event-driven Desktop Skill Recovery

## Problem

`skill_center.desktop_skill_recovery` currently treats old Desktop Engines that
implement neither logical Mapping API generation as transiently unavailable.
One task retries for thirty minutes, then every Backend worker's periodic
Sweeper recreates work for the same Bot. Successful Bots are also projected
repeatedly because terminal TaskQueue rows release their live idempotency key.

## Contract

- Recovery is triggered by `DeviceActivatedEvent`,
  `RuntimeProjectionRequestedEvent`, desired-state mutation, Local Skill
  mutation, or Track Latest. Elapsed time alone does not create work.
- The periodic `DesktopSkillRecoverySweeper` and all lifecycle wiring are
  removed. No replacement periodic task is introduced.
- `desktop_skill_recovery.task_deadline_seconds` is required by the strict
  configuration schema and defaults to 600 seconds. Removed Sweep keys are
  rejected.
- A standard route-missing `404` or structured
  `501/SKILL_MAPPINGS_APPLY_UNSUPPORTED` from logical Mapping apply authorizes
  one Legacy publish/verify fallback.
- A standard route-missing `404` or capability `501` from the required Legacy
  Mapping operation produces `ENGINE_SKILL_MAPPING_UNSUPPORTED`,
  `DEGRADED`, `retryable=false`.
- Desktop recovery maps that issue to TaskQueue `FAILED`; timeouts, transport
  failures, and `5xx` responses remain retryable until the task deadline.
- Desired state remains committed. The shared cloud Runtime projection becomes
  more accurate (`DEGRADED`, not transient `PENDING`) but gains no recovery
  task or new trigger.
- Expected route fallback is not logged as an error. Confirmed incompatibility
  emits one structured warning; unexpected failures retain error tracebacks.

## Compatibility and rollout

Avernet and OCB are separate review units but one OCB build/deployment unit.
The OCB PR updates the exact `ocb-public` gitlink and changes the Corp transport
log classification. Existing TaskQueue rows are not rewritten: new handlers
terminate known incompatibility on their next execution, while genuine
transient rows retain the deadline frozen at creation. After old rollout
processes exit, no periodic Sweep creates replacement rows.

## Acceptance

- New logical apply succeeds without Legacy calls.
- Apply route `404` and structured unsupported `501` both attempt Legacy.
- Legacy success converges; Legacy route/capability absence is non-retryable.
- Desktop unsupported recovery becomes `FAILED`; offline recovery waits for a
  Runtime Ready event; transient errors retry within 600 seconds.
- DI and lifecycle discovery contain the recovery service and task handler but
  no Sweeper.
- Standard route-missing `404` remains a typed transport exception without an
  error-level Corp transport log.
- No OpenAPI, database schema, general TaskQueue status, or Engine route is
  added.
