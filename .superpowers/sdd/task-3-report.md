# Task 3 Report: Internal Owner Protection

## Status

Completed after focused verification; the commit is listed below after it is
created.

## Implementation

- Injected `CommonWhiteListService` into `DormantBotService`.
- Extended `BotDormantModule` to inject that existing bound singleton into the
  manual `DormantBotService` provider; no additional config reader or binding
  was introduced.
- Each `process_run` loads `protected_owner_ids` once, after scan-window
  resolution and before candidate discovery or any downstream action.
- Internal candidates whose normalized owner ID is protected are removed before
  the `/alive` loop; `RunSummary.scanned` counts only the remaining candidates.
- The same immutable `frozenset[str]` is passed to `_process_external_inputs`.
  Task 4 remains responsible for applying an external-input skip decision.
- Owner-protection logs record the loaded owner count and, for filtered
  candidates, only a count plus at most five `bot_id@owner_id` samples.
- Configuration errors propagate before `/alive`, notifications, audit writes,
  container release, status updates, or passport freezes can run.

## TDD Record

### RED

Command:

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_decide.py -k "protected_owner or owner_config_error" -v
```

Result: 2 failed. The service had neither `get_current_env` nor the injected
`common_whitelist_service` constructor dependency.

### GREEN

The same command passed with 2 tests:

- protected owners are filtered before `/alive`, and the owner list is loaded
  exactly once with the expected config keys and environment;
- an owner-config read error raises before downstream calls, audit rows, or
  notification rows are created.

### DI RED/GREEN

Added the nearest existing provider test in `test_token_resolver.py`.
Initially, its direct provider call failed because `_dormant_bot_service` did
not accept `common_whitelist_service`. After adding the existing
`CommonWhiteListService` dependency to that provider and forwarding it, the
six provider/token tests passed and the new test proved the identical object is
held by `DormantBotService`.

## Verification

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_scan_policy.py tests/community/core/bot_dormant/test_decide.py tests/community/core/bot_dormant/test_external_input.py -v
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_token_resolver.py -v
DEPLOY_PROFILE=test uv run pytest tests/community/architecture/test_no_oversized_modules.py -v
uv run ruff check src/agentclaw/community/core/bot_dormant/service.py src/agentclaw/community/di/modules/bot_dormant_module.py tests/community/core/bot_dormant/test_decide.py tests/community/core/bot_dormant/test_external_input.py tests/community/core/bot_dormant/test_scan_policy.py tests/community/core/bot_dormant/test_token_resolver.py
wc -l src/agentclaw/community/core/bot_dormant/service.py
```

Results: 62 focused tests passed; 2 architecture tests passed; Ruff passed;
`service.py` is 994 lines. The test runs emitted 14 pre-existing Pydantic
deprecation warnings and no task-specific warnings or failures.

## Self-Review

- `get_owner_ids` has one production call site in the run path, and the new
  test asserts one call per run.
- The candidate partition is before `_process_one_candidate`, whose first
  downstream action is `/alive`.
- The config read is before candidate filtering, audit writes, external-input
  handling, and all lifecycle side effects.
- External-input behavior is deliberately unchanged in this task; it receives
  the protected set for Task 4.
- `BotDormantModule` forwards the same existing `CommonWhiteListService`
  singleton, and a direct provider test verifies object identity.

## Task 4 Line Risk

`service.py` is 994 lines against the 1000-line architecture ceiling, leaving
only six lines. Task 4 should avoid net growth in this module or first extract
or compact existing code within the established ownership boundary, then rerun
the architecture line-count test.

## Review Fix: Malformed Owner Elements

Enabled owner-list configurations now validate every element before
normalization. Only `str` and non-`bool` `int` values are accepted; `None` and
blank strings are still dropped. Invalid values such as `bool`, `dict`, and
`list` raise `ValueError`. The error log includes only the config coordinates
and `item_type`, never the item value or full configuration list.

The process-run regression uses a real `CommonWhiteListService` configured with
an invalid list element. It proves the `ValueError` escapes before `/alive`,
audit/notify writes, `stop_bot`, `update_status`, or passport freeze calls.

### Review TDD RED/GREEN

RED:

- three parameterized invalid-element cases did not raise `ValueError`;
- the malformed process-run configuration also did not raise and therefore
  demonstrated the unprotected path.

GREEN:

```bash
DEPLOY_PROFILE=test uv run pytest tests/community/core/common_config/test_common_whitelist_service.py -v
DEPLOY_PROFILE=test uv run pytest tests/community/core/bot_dormant/test_decide.py -k "owner_config" -v
uv run ruff check src/agentclaw/community/core/common_config/whitelist_service.py tests/community/core/common_config/test_common_whitelist_service.py tests/community/core/bot_dormant/test_decide.py
```

Results: 17 common-config tests passed; 2 owner-config abort tests passed; Ruff
passed. These focused runs emitted the existing Pydantic deprecation warnings.

### Full CI and Coverage Evidence

```bash
src/backend/scripts/ci_test.sh --base origin/dev --head HEAD
python3 scripts/ci/report_check.py --junit src/backend/pytest_report/TEST-junit.xml --coverage src/backend/pytest_report/TEST-cov.xml --source-root src/backend/src --min-case-pass-rate 100 --min-line-coverage 75 --base origin/dev --head HEAD --min-change-line-coverage 90
```

Results: full community CI passed 7331/7331 tests; overall line coverage was
81.74%; CI's 80% change-line gate reported 96.00% (24/25). The explicit 90%
change-line gate also passed at 96.00% (24/25). The full run emitted 418
pre-existing warnings.
