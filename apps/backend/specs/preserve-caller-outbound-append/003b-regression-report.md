# Independent local regression

## Result
PASS for the affected Backend boundary. No engine code changed, so no unrelated Engine startup or production deployment was performed. Production BaaS was not mutated.

## Command and environment
Python 3.12.13: `/Users/helloworld/Desktop/codes/teamclaw/ocb/apps/backend/.venv/bin/python`.
Working directory: this worktree's `apps/backend`; `PYTHONPATH=src:.` ensures new source is imported.

```bash
PYTHONPATH=src:. <python> -m pytest \
 tests/community/architecture/test_api_layer_is_protocols_only.py \
 tests/community/architecture/test_no_oversized_modules.py \
 tests/community/architecture/test_protocol_contracts.py \
 tests/community/architecture/test_protocol_base_ordering.py \
 tests/community/core/service_bot/services/test_baas_service_outbound_append.py \
 tests/community/core/service_bot/services/test_baas_service_teclaw_outbound_rule.py \
 tests/community/core/devices/services/test_baas_device_service.py \
 tests/community/core/service_bot/services/test_caller_identity_outbound.py \
 -q --disable-warnings
```

**126 passed, 23 warnings in 5.55s.** Existing dependency/Pydantic deprecation warnings do not fail tests. New focused append tests separately passed 6/6.

Assertions cover append query and unchanged payload/timeout; repeated Owner token rule replacement preserving Caller; explicit internal replace with no HTTP mode query; all three Teclaw call sites; HTTP/business/transport/invalid JSON error contracts; request/success/failure logs and credential non-disclosure in those logs.

Ruff on modified source/tests and `git diff --check`: PASS. Full Backend suite, live BaaS integration, pre/prod QA and remote three-metric ACI gates were not run by this local regression; remote gates remain PENDING. No coverage exclusions or thresholds changed.

## Final correction rerun
After the user specified replace-by-query-omission, repeated the command above against the updated code: **126 passed, 23 warnings in 5.55s**. New append retains mode=append; internal replace results in no query parameters, verified with real HTTP request capture. Latest three modified files pass Ruff; git diff --check passes.
