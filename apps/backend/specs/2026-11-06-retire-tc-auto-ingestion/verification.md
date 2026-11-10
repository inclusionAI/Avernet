# Local verification

Target: dev. Latest refresh/rebase: **2026-11-09**, baseline
`6e8ef82c43c27b9dd751ad8584a34d65438513ae`.
Initial 2026-11-06 verification used baseline
`1c989e0ca51949e7d0f2d09112d833f40f336cc5` (391 tests passed).

## Regression evidence
Original code: six negative regressions failed (legacy complete/status/callback
still needed the observer, formal OpenAPI complete/status still notified it).
Final changed code: all affected module/protocol/architecture tests pass.

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run pytest \
  tests/community/adapters/http/session_resources \
  tests/community/adapters/http/openapi_v1/engine_runtime/test_session_files.py \
  tests/community/core/session_resources \
  tests/community/core/tc_file_upload_integrations \
  tests/community/adapters/http/tc_file_upload_integrations \
  tests/community/di/test_tc_file_upload_integration_wiring.py \
  tests/community/endpoints/test_tc_resource_context.py \
  tests/community/contracts/test_tc_resource_ready.py \
  tests/community/architecture/test_module_boundaries.py \
  tests/community/architecture/test_service_api_conformance.py \
  tests/community/architecture/test_protocol_contracts.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py -q
```

Rebased result: **481 passed**. Changed Python files pass Ruff check and git diff
--check. Source modules remain below 1,000 lines. Full-file formatter differences
already exist on the baseline for the two production files and the formal
OpenAPI test file; unchanged formatting debt was not expanded into this patch.
New/changed test regions were formatted.

A separate final diff review found no blocking scope or behavior regression.
Search confirms no remaining production call to notify_in_background outside
its compatibility contract/coordinator definitions. Shared context/token wiring
and its conformance tests remain unchanged and passing.

## Paired delivery and limits
ECB (target 0731_ecb_dev) rejects old ready notices with an authenticated,
validated 410 and no inbox/worker construction. Its local affected tests pass
608 cases; its full-repository governance failures reproduce on the unchanged
baseline, with zero new incremental violations.

Avernet-only PR delivery was approved on 2026-11-09; ECB and OCB gitlink
changes remain outside this PR. Rebase retained the new Engine file APIs and
upload actor metadata behavior; its direct adapter-construction fixture was
updated to no longer pass the retired observer.

No live Singlebox, real upload network, production/prepub E2E or deployment
was performed. Unit/in-process HTTP tests use controlled external fakes;
they are not deployment acceptance. OCB dev consumes this code via its Avernet
submodule; do not update that gitlink until a real merged commit is reachable
in the internal mirror. Roll out upstream notification removal before ECB's
explicit retirement response. Preserve historical records and generic Jobs.
