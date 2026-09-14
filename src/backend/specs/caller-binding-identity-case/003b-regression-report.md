# Independent backend regression report

## Scope and environment

- Task: caller-binding-identity-case; backend identity comparison fix only.
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/fix-caller-binding-identity-case-rel20260910`.
- Python: local backend `.venv`, Python 3.12.13; synthetic fixtures, no production requests.
- Base: `227613a5e30667177109978ec73a2bf8ebcd8abd` (`github/REL20260910`).
- Tested head: `31c69e8f826b611ee983d1ab1347baa03cdc5acf`.
- Existing flow: read Bot/Caller repositories, validate readiness and scope, return existing binding. Allowed change: preserve identity values and log rejected scope field. No transport, provisioning, deployment, engine, or relay changes.

## Executed regression

From `src/backend`:

```bash
COVERAGE_FILE=/tmp/caller-binding-regression.coverage .venv/bin/python -m pytest tests/community/core/runtime_binding tests/community/core/caller_identity/test_iam_token_service.py tests/community/api/test_caller_iam_token.py tests/community/core/token_exchange tests/community/adapters/http/openapi_v1/engine_runtime/test_session_files.py tests/community/endpoints/test_openapi_session_files.py --cov=agentclaw.community.core.runtime_binding.service --cov-report=term-missing --cov-report=xml:/tmp/caller-binding-regression-coverage.xml --junitxml=/tmp/caller-binding-regression-junit.xml -q
```

| Suite | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Runtime binding service | 48 | 0 | 0 |
| Caller IAM service | 15 | 0 | 0 |
| Caller IAM API | 6 | 0 | 0 |
| Token exchange pipeline | 12 | 0 | 0 |
| Session files adapter | 15 | 0 | 0 |
| Total | 96 | 0 | 0 |

Result: 96 passed in 1.77 seconds; 21 existing dependency deprecation warnings. The declarative endpoint file registered framework cases but contributed no direct pytest cases to this focused invocation; no live endpoint coverage is claimed.

Assertions cover matching uppercase/mixed-case identities, original Caller repository arguments, different and case-only different identities, missing metadata, inactive bindings, initialization allowlist, enum case normalization, shared stage selection, IAM refresh target success/failure, and token pipeline consumers. IAM consumer tests mock the resolver; they complement real resolver unit tests rather than establish a live IAM-to-runtime end-to-end result.

## Diagnostic logging

- Event: `caller_binding_scope_invalid`; fields: `binding_id`, `field`.
- Every scope mismatch preserves `RuntimeBindingNotFoundError("Caller binding scope is invalid")` and records the relevant field.
- Tests assert the event and fields for invalid owner, actor, reason, environment, and provider.
- Synthetic credential sentinel values attached to the binding and nested instance metadata are absent from logs on success and failure. Complete objects and credential values are not logged.
- No external I/O boundary was added or changed; request/response boundary logging is not applicable to this internal validation edit.

## Static checks

```bash
.venv/bin/python -m ruff check src/agentclaw/community/core/runtime_binding/service.py tests/community/core/runtime_binding/test_service.py --select F,E203,E211,E265 --preview
git diff --check
```

Both passed.

## ACI-compatible focused preflight

From the repository root:

```bash
src/backend/.venv/bin/python scripts/ci/report_check.py --junit /tmp/caller-binding-regression-junit.xml --coverage /tmp/caller-binding-regression-coverage.xml --source-root src/backend/src --base 227613a5e30667177109978ec73a2bf8ebcd8abd --head 31c69e8f826b611ee983d1ab1347baa03cdc5acf --min-case-pass-rate 100 --min-line-coverage 70 --min-change-line-coverage 90
```

| Metric | Evidence | Result |
| --- | --- | --- |
| Focused case pass rate | 96/96 (100%), failed=0, skipped=0; threshold 100% | PASS |
| Affected service line coverage | 97/97 (100%); threshold >90% | PASS |
| Changed executable line coverage | 7/7 (100%); threshold >=90% | PASS |
| Whole-backend line coverage | Not measured by this focused invocation; owned by main workflow | NOT RUN here |

The repository checker passed on actual base/head commits. Its total coverage input here contains only the affected service; this is not a claim that the whole-backend >=70% gate has passed. Independent outputs: `/tmp/caller-binding-regression-output.log`, `/tmp/caller-binding-regression-junit.xml`, `/tmp/caller-binding-regression-coverage.xml`.

## Execution limits and conclusion

**PASS: independent backend regression and affected-file preflight.**

Engine startup and registered model/relay regressions were not run: this change affects backend validation only, and the legacy configured `ocb_worktrees/local-claude-code-engine/src/engine` path is absent. No engine PASS is claimed. No new engine feature or global regression definition was introduced.

Remote ACI/CI: not observed by this regression agent; pending main workflow verification against actual PR jobs. Local checks do not establish remote CI success, deployment verification, or production resolution.
