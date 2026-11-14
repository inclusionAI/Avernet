---
agent: tc-code
status: completed
created: 2026-11-14
iteration: 1
---

# DeviceToken Caller credential implementation

The existing GET `/api/v1/token/iam` now accepts the finite `token_type=iam|dingtalk` query, defaulting to IAM. The DeviceToken branch first verifies the existing BaaS signed application principal. Its explicit caller and owner assertions are required and must match the resolved Bot owner and upstream verified credential subject. DeviceToken validation never falls back to browser SSO.

A separate optional `DingTalkCallerTokenProvider.exchange_dingtalk` capability preserves the existing IAM provider signature. The community provider explicitly reports unavailable. The service resolves only the exact Caller instance, validates its session, checks the issued credential subject and expiration, then resolves the binding again before reusing the existing runtime installation. Owner drafts, shared service targets and other caller instances are excluded.

The HTTP adapter and application service log safe request metadata, success and stable failure outcomes. No source credential, issued token, Cookie or signed principal is logged. Source tokens remain opaque and in memory.

Validation:

- Final DeviceToken core/API tests: **28 passed**, including real signature verification with absent, invalid, expired and wrong-issuer principals; unknown source; exact target; subject mismatch; expired result; replaced/wrong target; context/provider/updater failure; missing fields; no SSO fallback; secret-free request/success/failure diagnostics.
- Architecture, Caller identity, existing IAM API and OpenAPI regression: **680 passed**.
- Ruff on all changed Backend Python files and `git diff --check`: passed.
- Full Backend suite: **31,255 passed, 40 skipped, 6 subtests passed**; total line coverage **149,253/163,700 (91.17%)**.
- Final independent DeviceToken tests: **28 passed**, with **59/59 changed executable source lines covered (100%)**. The full suite started before the final test additions; its standalone changed-line result is 51/59 (86.44%). The final focused run independently verifies those eight remaining changed lines.
- Branch: `feat/dingtalk-caller-token-20261114`; freshly fetched base `origin/dev` remains `7711855bebd028c2231db58cefe045e2b330a7e2`.

BaaS propagation and its independent verification are described in `apps/baas/specs/dingtalk-caller-token/002-code-report.md`.

No live upstream exchange or deployment was performed. Production adapters consume the optional provider capability through dependency injection. Remote PR checks remain a separate gate.
