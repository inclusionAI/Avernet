# CI fix iteration 2: synchronize the real-handler drain test

## Actual remote failure

- PR: https://github.com/inclusionAI/Avernet/pull/3619
- Failed source head: `e0ee690cf8bcb12903d1e03d78aa992bc4984945`.
- Job: https://github.com/inclusionAI/Avernet/actions/runs/41846422282/job/126631010547
- Downloaded `backend-testresult` artifact reports 29,613 collected cases, 1 failure, 40 skips, 0 errors. The sole failure was `TestRealHandlerDrain.test_within_grace_real_slice_completes`: the run was still RUNNING instead of COMPLETED after shutdown; this case took 5.356 seconds in CI.
- Coverage artifact: 144,048 / 158,388 executable lines (90.95%). Coverage was not the failed gate. New draft-restart feature tests did not fail.

## Diagnosis

This is an existing timing-sensitive test outside the draft-restart business code. The test waited for the worker's in-flight marker, but `_run_dispatch` sets that marker before `handler.handle_slice` initializes and performs its offloaded database reads. On a loaded runner that preparation can consume the three-second drain budget before the fake subagent has even reached the controlled wait. An empty fixed delay after that marker does not prove that the intended within-grace scenario exists.

The unmodified target test passes locally (1 case, 0.27 seconds). A diagnostic-only pytest plugin delayed the first real `run_repo.get` by 3.2 seconds inside its existing offloaded thread, reproducing the same RUNNING-versus-COMPLETED failure in 3.51 seconds. This demonstrates the synchronization flaw; it does not claim to reconstruct every scheduler condition on the remote runner.

## Minimal test-only fix

Only `tests/community/core/agentswarm/runtime/worker/test_drain_breaker.py` changes:

- A local `ReadyGate` signals when the real fake subagent has actually entered its gate wait.
- The test waits at most ten seconds for this preparation barrier before starting shutdown. The configured drain grace remains **3.0 seconds**.
- The gate is released after shutdown stops claiming work, rather than after a fixed 100ms sleep.
- The final run COMPLETED, final answer `drained`, dispatch DONE, and empty last-error assertions are retained. The separate over-grace reclaim test is unchanged and still runs.
- A `finally` block releases/cancels the test gate task and shuts down the worker on setup or assertion failures.

No runtime logic, database behavior, CI threshold, test exclusion, or coverage rule changes. This is test synchronization only, so no production diagnostic logging is appropriate.

## Validation

- Same 3.2-second first-read delay probe after the fix: **1 PASS**, 3.40 seconds; the delay is outside the real three-second drain interval after the new barrier.
- Entire drain/breaker file plus the two draft-restart suites: **123 / 123 PASS**, zero failures or skips (17 existing deprecation warnings).
- Ruff `--preview --select F,E203,E211,E265` for the modified test: PASS.
- `git diff --check`: PASS.

Command from `apps/backend`:

```bash
DEPLOY_PROFILE=test PYTHONPATH=src .venv/bin/python -m pytest tests/community/core/agentswarm/runtime/worker/test_drain_breaker.py tests/community/core/bot_management/services/test_service_draft_restart_recovery.py tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py -q --disable-warnings
```

The diagnostic delay plugin and before/after logs are kept outside the public source checkout. Production files are unchanged from the original feature head, so this follow-up introduces no additional executable production coverage denominator. Remote checks must run again for the new head; no remote PASS is claimed here. The original full-local PASS reports remain historical and are not overwritten.
