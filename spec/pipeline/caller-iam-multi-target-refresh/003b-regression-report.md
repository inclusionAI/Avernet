---
agent: tc-engine-regression
status: completed
iteration: 1
---

# Local Regression Report

## Commands

```bash
uv run --project src/backend pytest -q \
  src/backend/tests/community/core/runtime_binding \
  src/backend/tests/community/core/caller_identity \
  src/backend/tests/community/adapters/http/openapi_v1/engine_runtime/test_session_files.py \
  src/backend/tests/community/endpoints/test_openapi_session_files.py \
  src/backend/tests/community/di/test_community_router_bindings_resolve.py

uv run --project src/backend ruff check \
  src/backend/src/agentclaw/community/core/runtime_binding/models.py \
  src/backend/src/agentclaw/community/core/runtime_binding/service.py \
  src/backend/src/agentclaw/community/core/runtime_binding/__init__.py \
  src/backend/src/agentclaw/community/core/caller_identity/iam_token_service.py \
  src/backend/src/agentclaw/community/di/modules/caller_identity_module.py \
  src/backend/tests/community/core/runtime_binding/test_service.py \
  src/backend/tests/community/core/caller_identity/test_iam_token_service.py

uv run --project src/backend python -m compileall -q \
  src/backend/src/agentclaw/community/core/runtime_binding \
  src/backend/src/agentclaw/community/core/caller_identity/iam_token_service.py \
  src/backend/src/agentclaw/community/di/modules/caller_identity_module.py

git diff --check
```

## Results

- Pytest: `79 passed`
- Ruff: passed
- Compileall: passed
- Git diff check: passed

The test run emitted pre-existing Pydantic/Starlette deprecation warnings;
there were no test failures or new lint errors.
