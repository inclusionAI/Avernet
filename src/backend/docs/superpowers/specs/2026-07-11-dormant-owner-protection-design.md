# Dormant Bot Owner-Level Protection Design

## 1. Background

Dormant Bot governance currently supports two kinds of automatic recycle input:

1. the scheduled internal scan, which builds candidates from `ac_bots`, checks
   activity, warns, and eventually recycles;
2. `ac_bot_dormant_external_input`, which follows its own warn/recycle cooldown.

The existing `ac_bot_dormant_whitelist` protects one logical Bot at a time by
the exact `(bot_id, owner_id)` key. The owner-level protection requirement is broader:
all Bots owned by a protected owner must be excluded from automatic dormant
governance, regardless of `bot_id`.

The initial list is maintained by deployment operations outside this public
repository. Source identifiers are treated as staff identifiers and matched
against `ac_bots.owner_id` after conversion to strings.

## 2. Goals

- Protect every Bot owned by a configured owner from automatic dormant recycle.
- Apply the protection to both the internal scan and `external_input`.
- Keep `POST /api/internal/dormant/recycle-one` available as an explicit ops
  override.
- Store the complete owner list in one `ac_common_config` row per environment.
- Load the list once at the beginning of each scan run, not once per Bot.
- Preserve the existing exact Bot whitelist and all current callers.
- Make configuration failures observable and prevent malformed configuration
  from silently disabling protection during an automatic run.

## 3. Non-Goals

- Replacing `ac_bot_dormant_whitelist`.
- Adding owner-level protection to unrelated governance domains.
- Blocking explicit manual recycle operations.
- Adding a new management UI or upload API in this change.
- Dynamically reloading a changed list in the middle of an in-flight scan.

## 4. Configuration

Create one row for each environment where protection is required:

| Column | Value |
| --- | --- |
| `business_code` | `bot_dormant` |
| `business_name` | `Dormant Bot Governance` |
| `param_code` | `protected_owner_ids` |
| `param_name` | `Dormant protected owner IDs` |
| `enable` | `1` |
| `env` | `pre` or `prod` |

`param_value` is a JSON array with no wrapper object:

```json
["100001", "100002", "100003"]
```

Keeping the runtime value as a plain array minimizes configuration size and
parsing ambiguity. Operational metadata belongs in `ext_info`:

```json
{
  "source": "deployment-managed owner list",
  "reason": "dormant recycle owner protection",
  "version": "20260711"
}
```

The rollout creates two rows, one for `pre` and one for `prod`, containing the
same normalized list. It does not introduce an `env=all` fallback because the
existing common-config contract is environment-scoped.

### Configuration Semantics

- Row missing: protection list is empty, preserving legacy behavior.
- Row disabled (`enable=0`): protection list is empty by explicit operator
  choice.
- Enabled row with a valid list: trim values, convert every value to `str`,
  drop blanks, and deduplicate into a `frozenset[str]`.
- Enabled row with a non-list value: raise a configuration error.
- Database/config read exception: propagate the error and abort the automatic
  run before activity checks or destructive actions begin.

## 5. Components

### 5.1 CommonWhiteListService

Extend `CommonWhiteListService` with a bulk-reading method:

```python
def get_owner_ids(
    self,
    *,
    business_code: str,
    param_code: str,
    env: str,
) -> frozenset[str]:
    ...
```

The method uses `CommonConfigService.get_value(..., only_enabled=True)` and
implements the normalization and validation rules above.

The existing `is_bot_feature_enabled()` method remains unchanged. In
particular, its exact `owner_id + bot_id` matching semantics must not gain
wildcards or owner-only behavior, because it is already used by unrelated
features.

### 5.2 DormantBotService

Inject `CommonWhiteListService` into `DormantBotService` and load the protected
owner set once in `_process_run_inner`:

```text
scan starts
  -> load protected_owner_ids once
  -> build existing candidates
  -> remove candidates whose owner_id is protected
  -> process remaining candidates
  -> process external_input using the same immutable set
```

Filtering occurs after the existing `filter_candidates()` call and before any
`/alive` request. This avoids changing the candidate-query contract and keeps
the code change local to dormant orchestration.

### 5.3 Existing Bot-Level Whitelist

`ac_bot_dormant_whitelist` remains the exact `(bot_id, owner_id)` whitelist.
The effective automatic protection is the union of:

```text
exact Bot whitelist OR protected owner list
```

No wildcard sentinel such as `bot_id='*'` is introduced.

## 6. Data Flow

### 6.1 Internal Scheduled Scan

1. Refresh the existing scan policy (`dry_run`, N, M).
2. Load `protected_owner_ids` for the current environment exactly once.
3. Run the existing candidate query and exact Bot whitelist subtraction.
4. Partition candidates by `candidate.owner_id in protected_owner_ids`.
5. Log protected count and a bounded sample.
6. Send `/alive` requests only for unprotected candidates.
7. Protected owners receive no warn notification, no cooldown audit, and no
   recycle action from the internal scan.

`RunSummary.scanned` continues to count actionable candidates after protection
filtering, preserving its current meaning.

### 6.2 External Input

`_process_external_inputs` receives the same immutable owner set loaded for the
run. The owner protection check happens before the existing Bot lookup and
before warn/recycle decisions.

For a protected owner:

- leave `processed=0`, so removing the owner from protection later allows the
  input to resume normal governance;
- write an audit row with:
  - `check_result='whitelisted'`;
  - `action_taken='skipped'`;
  - `source='external_input'`;
- do not enqueue notification or call `_execute_recycle`.

### 6.3 Manual Ops Recycle

`DormantOpsService.recycle_one` does not read or enforce the protected-owner
configuration. An authorized operator may still recycle a protected owner's
specific Bot through the internal endpoint.

This is an explicit override, not an accidental bypass. Existing token
authentication, request logging, and `source='manual_ops'` audit behavior stay
unchanged.

## 7. Observability

At run start, log:

```text
event=protected_owners_loaded env=<env> owner_count=<count>
```

After internal candidate filtering, log:

```text
event=protected_owners_filtered skipped=<count> sample=<bounded bot@owner list>
```

Requirements:

- never print the full owner list;
- samples contain at most five logical Bot identifiers;
- config parsing failures include `business_code`, `param_code`, and `env`, but
  not the raw configuration value;
- the existing `run_id` is included in scan-level logs.

External-input skips remain queryable from `ac_bot_dormant_check_audit` using
`check_result='whitelisted'`, `action_taken='skipped'`, and
`source='external_input'`.

## 8. Failure Handling

The owner list is a safety boundary for destructive automatic operations.

- Missing/disabled config intentionally means no owner protection.
- Malformed enabled config aborts the automatic run.
- Common-config/database read failure aborts the automatic run.
- Failure happens before `/alive`, notification creation, container release,
  Bot status mutation, or passport freeze.
- Manual `recycle-one` remains available even if the automatic scan cannot load
  its owner-protection configuration.

The loaded set is immutable for the duration of one run. A configuration update
takes effect on the next scheduled or manually triggered scan; no service
restart is required.

## 9. Testing

### Unit Tests

`CommonWhiteListService`:

- returns an empty set when the row is missing or disabled;
- normalizes integer and string identifiers;
- trims whitespace, drops blanks, and deduplicates;
- rejects dict/string/scalar `param_value` values;
- propagates repository/config read failures;
- preserves all existing exact Bot whitelist tests.

`DormantBotService` internal scan:

- protected owner is removed before `check_alive`;
- unprotected owner follows the existing decision path;
- config is read once per run even with multiple candidates;
- malformed/read-error config performs no downstream calls;
- `RunSummary.scanned` excludes protected candidates;
- bounded observability logs are emitted.

`external_input`:

- protected owner is audited as whitelisted/skipped;
- row remains unprocessed;
- no notification is created;
- `_execute_recycle` is not called;
- unprotected rows preserve existing behavior.

`DormantOpsService`:

- a protected owner can still be recycled through `recycle_one`;
- the resulting audit source remains `manual_ops`.

### Regression Tests

- Existing exact Bot whitelist behavior remains unchanged.
- Existing scan-policy fallback and N/M tests remain unchanged.
- Existing warn/cooldown/recycle behavior remains unchanged for unprotected
  owners.
- Existing dry-run behavior remains unchanged.

## 10. Rollout and Verification

1. Normalize the deployment-managed owner IDs as strings and generate the
   `pre` and `prod` upsert SQL outside this public repository.
2. Insert the `pre` row first.
3. Deploy the code to pre-production.
4. Trigger a dry-run scan containing at least one protected owner and one
   unprotected owner.
5. Verify protected owners do not reach `/alive`, notification, or recycle
   actions; verify external-input protection through audit rows.
6. Insert the `prod` row before deploying/enabling the production code.
7. Run production in dry-run first and compare the protected-owner set against
   the spreadsheet source.
8. Enable real mode only after the set-difference checks return zero.

Suggested reconciliation checks:

- configured owner count matches the deployment source;
- configured list has no blank or duplicate values;
- spreadsheet minus configuration is empty;
- configuration minus spreadsheet is empty;
- no protected owner appears in automatic `warn_enqueued` or `recycled` audit
  rows after rollout;
- manual ops rows remain possible and are distinguishable by
  `source='manual_ops'`.

## 11. Compatibility

This change is backward compatible:

- no database schema changes;
- no endpoint changes;
- no request/response model changes;
- no behavior change when the new config is absent or disabled;
- existing Bot-level whitelist semantics are unchanged;
- manual ops semantics are unchanged.
