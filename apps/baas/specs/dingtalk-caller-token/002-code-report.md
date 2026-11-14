# DingTalk caller credential propagation

BCN accepts the explicit `dingtalk-token` execution credential type. The existing in-memory caller metadata and binding resolver preserve it without decoding or inspecting the opaque token. Once caller provisioning completes, BaaS makes one GET `/api/v1/token/iam` request with `token_type=dingtalk`, `bot_id`, `user_id`, `owner_id`, `is_test_exchange=false`, the source token in the existing `IAM_TOKEN` cookie, and a newly minted app principal. An owner identifier is never inferred to be a stable resource entity identifier.

The Backend verifies the authenticated application assertion and the exchanged subject before runtime mutation. BaaS does not retry the exchange or fall back to another credential type. Existing `iam-token` cookie behavior and `agentpass` outbound-rule behavior remain unchanged.

Diagnostics record safe request parameters, request ID, HTTP status, outcome and elapsed time. Source credentials, principals, response bodies and exception messages are excluded from the new refresh logs and errors.

Validation:

- Affected bot-service, bot-run, BCN and BCN router unit suites: **1391 passed, 10 xpassed** (existing non-strict expected-failure cases).
- Latest focused bot-service plugin suite: **95 passed**.
- Changed executable source lines: **32/32 (100%)**; affected source files total: **954/1002 (95.21%)**, measured with coverage XML and zero-context Git diff added-line intersection.
- Ruff passed for all changed BaaS Python files; Git whitespace check passed.
- Tests verify opaque DeviceToken forwarding, fresh principal minting, actor/owner fields, unsupported credential rejection before provisioning, no refresh retry, and secret-free success/failure diagnostics for HTTP, malformed body, rejection and transport failure.

Run from `apps/baas`:

```sh
.venv/bin/python -m pytest --confcutdir=tests/unit \
  tests/unit/plugins/bot_service tests/unit/core/service/bot_run \
  tests/unit/core/service/bcn tests/unit/adapters/web/open_api/test_bcn_router.py \
  --cov=secbaas.community --cov-report=xml:/tmp/dingtalk-baas-cov.xml -q
```

`--confcutdir` isolates these unit suites from integration bootstrap import side effects during coverage instrumentation. No live token exchange or deployment was performed. Remote CI/ACI remains pending until PR creation.
