# Skills Pool Engine-scoped Admission Policy

Status: implemented against `REL20260917`.

## Goal

Replace Batch-gated Skills Pool rollout with direct, Engine-scoped admission:

```text
global feature disabled → deny
engine admission disabled → deny
exact Bot exclusion → deny
exact Bot allowlist → allow
Owner + Engine rollout → allow
Environment + Engine rollout → allow
otherwise → deny
```

Already claimed Bots bypass first-admission policy and continue their existing
migration/reconcile state. Rollback is separate.

## Canonical configuration

```json
{
  "schema_version": 2,
  "engine_admission": {"openclaw": true},
  "bot_allowlist": [],
  "owner_rollouts": [],
  "environment_rollouts": [],
  "bot_exclusions": []
}
```

The global `enabled` bit remains the existing `ac_common_config.enable` column.
The JSON does not duplicate it.

## Mutation contract

- Every mutation carries `expected_revision` and non-empty `reason`.
- A stale caller receives `409 POLICY_REVISION_CONFLICT`, including idempotent
  requests.
- The application mutates one rule, validates the complete v2 value, and uses
  the existing config/audit transaction for CAS plus append-only audit.
- New audit events have `batch_id=null`; `evidence` records Engine and target.
- Exact Bot rules must match the Bot's current persisted Engine.
- Rules may be prepared while an Engine switch is closed. The switch controls
  runtime admission, not policy authoring.

## HTTP contract

```text
GET    /api/ops/skills-pool/rollout
POST   /api/ops/skills-pool/rollout/feature
PUT    /api/ops/skills-pool/rollout/engines/{engine}/admission
PUT    /api/ops/skills-pool/rollout/bots/{bot_id}/allow
DELETE /api/ops/skills-pool/rollout/bots/{bot_id}/allow
PUT    /api/ops/skills-pool/rollout/bots/{bot_id}/exclude
DELETE /api/ops/skills-pool/rollout/bots/{bot_id}/exclude
PUT    /api/ops/skills-pool/rollout/owners/{owner_id}
DELETE /api/ops/skills-pool/rollout/owners/{owner_id}
PUT    /api/ops/skills-pool/rollout/environments/{engine}
DELETE /api/ops/skills-pool/rollout/environments/{engine}
```

Legacy Batch write routes return `410 ROLLOUT_BATCH_API_RETIRED`; historical
`GET /api/ops/skills-pool/batches/{batch_id}` remains read-only.

## Compatibility and release

1. Deploy the read-compatible Backend without mutating the current v1 config.
2. Verify GET, DI, authorization, v1 Gate decisions, and the new Policy API.
3. Back up v1, then perform the first expected-revision v2 mutation.
4. If rolling Backend back to a v1-only release, restore the v1 backup too.
5. Remove historical Batch GET only after one stable release cycle and one
   release cycle with no observed callers.

## Verification

- v1 and strict v2 schema normalization tests.
- Gate priority, exact/Owner/Environment matching, and cross-Engine isolation.
- v1-to-v2 first-write conversion without Batch evidence.
- stale caller and repository CAS conflict tests.
- HTTP operator authorization, request forwarding, validation, and 410 tests.
- existing historical Batch GET and repository audit regression tests.
