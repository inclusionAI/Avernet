# Caller instance status regression report

## Scope and outcome

**Local feature regression: PASS. Remote ACI: PENDING.**

Backend-only additive status query. No Engine, Relay, container, production URL, provisioning, restart or deployment was started or invoked. Engine live scenarios and their registration are not applicable to this Backend read-only feature; existing explicit domain and HTTP test cases are its executable regression contract.

## Independent verification

Executed from this worktree's `apps/backend` with its source ahead of the shared interpreter environment:

```bash
DEPLOY_PROFILE=test PYTHONPATH=src:. /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-cron-sessions-20261112/apps/backend/.venv/bin/python -m pytest tests/community/core/expert_chat/services/test_caller_instance_status.py tests/community/adapters/http/openapi_v1/test_caller_instance_status.py -q --disable-warnings --maxfail=1
```

Result: **43 passed**, failed=0, skipped=0, 18 warnings, 4.63 seconds. `git diff --check` passed. Existing broader successful runs are preserved in 002/003 rather than repeated without a changed requirement.

| Behavior | Result | Evidence |
|---|---|---|
| Missing/malformed allocation | PASS | Eight variants return exactly false/false without BaaS query. |
| Actual liveness and stale health | PASS | Eleven status/device combinations including RELEASED with stale ACTIVE/true, empty devices, pending/updating/unknown and mixed healthy devices. |
| No writes or lifecycle calls | PASS | Tests assert no instance upsert/update, binding status update, create, upgrade, publication polling or identity exchange. |
| Exact caller identity | PASS | Real owner lookup, authenticated subject, source missing denies before ledger observation. |
| Existing chat entitlement | PASS | Public noncollaborator admitted; private unrelated subject masked 404; user and mixed-principal mismatch 403; required principal/user enforced. |
| Application grant | PASS | Addressed owner/Bot/user grant required; denial before service observation. |
| Provider failures | PASS | Failure propagates; HTTP maps safe 502, never success with false status. |
| Public contract | PASS | Published operation schema has exactly required boolean `exists` and `alive` fields. |
| Boundary logs | PASS | Inbound request/success/failure and outbound provider request/success/failure assertions; projected non-sensitive identity/status/timing. |
| Real provider credential protection | PASS | Six real BaasService + httpx.MockTransport scenarios: healthy, 404/released, 503, application error, malformed JSON, transport error; raw credential sentinel absent from logs and HTTP output. |

## Coverage evidence and limits

This independent targeted run measures case pass **43/43 = 100%**, skipped=0, failed=0. Existing final coding artifact `/tmp/caller-instance-status-coverage.json` reports focused changed executable lines **60/61 = 98.36%** and focused five-module total **790/902 = 87.58%**; these are scoped local measurements, not the complete Backend or remote denominator. Generic router unexpected-exception re-raise is the one measured uncovered added line. Existing BaaS class coverage exclusion predates this task; its exercised safe logging paths are not included in measured changed lines. No exclusion or threshold was added or lowered.

No committed PR head existed during this run. Therefore head-specific repository-wide ACI-compatible coverage enforcement and actual remote checks remain **PENDING**, not PASS. Required remote evidence remains passed/total/skipped/failed, repository covered/total and changed-covered/changed-lines, along with the actual thresholds and job URL.

## Conclusion

Local behavior and boundary regression PASS with no remaining failure. Proceed to commit, rebase onto verified GitHub dev, PR and actual remote gate observation.
