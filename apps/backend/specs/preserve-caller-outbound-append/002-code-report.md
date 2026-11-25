# Coding report: preserve Caller outbound headers

## Scope and existing chain
Owner Passport refresh and bootstrap build generic outbound headers and call `BaasService.update_device_outbound_rule`. Dedicated Caller/JIT paths independently append headers. The approved change makes generic BaaS writes use append/upsert semantics confirmed by the user, retaining other headers.

Allowed changes: the existing outbound method, its Protocol, necessary Teclaw call-site compatibility, focused behavioral tests. No credentials, deployment, OCB/gitlink, lifecycle refactor, new service or dependency.

## Implementation
- `BaasService.update_device_outbound_rule` adds keyword-only `mode: Literal["append", "replace"] = "append"` and sends a mode query parameter only for append; replacement omits the query entirely, keeping payload serialization, 30-second timeout and return behavior.
- Teclaw global rules select `replace` internally in publish and single-device/batch header update paths; the HTTP request omits mode to retain the upstream default. Its session-scoped append requires a session key and cannot substitute for global updates.
- The service Protocol declares the same keyword contract.
- Success returns True only after HTTP status validation and business code zero. HTTP failures still become BaasServiceError, while transport, JSON and business failures retain their original exception types. Existing exception message semantics remain; raw response bodies are removed from boundary logs.

## Diagnostic events
`baas_outbound_update_requested`: system=baas, operation, PUT route, physical device, mode, mode_query_present, rule descriptors (domains/header/action). Rule values and placeholders are never logged.

`baas_outbound_update_succeeded`: device, mode, HTTP status, business result code, duration_ms.

`baas_outbound_update_failed`: device, mode, available HTTP/business status, exception type, duration_ms. No raw response or exception message is logged because it can echo credentials. Existing logger context retains correlation metadata. No generic redaction/logging framework added.

## Verification
Runtime: existing `ocb/apps/backend/.venv/bin/python`; PYTHONPATH explicitly points first to this worktree's `apps/backend/src` and `apps/backend`, so tests import changed source.

`python -m pytest` against:
- tests/community/core/service_bot/services/test_baas_service_outbound_append.py
- tests/community/core/service_bot/services/test_baas_service_teclaw_outbound_rule.py
- tests/community/core/service_bot/services/test_caller_identity_outbound.py
- tests/community/core/devices/services/test_baas_device_service.py

Result: **112 passed**, 21 existing deprecation warnings, no skips. New transport tests cover request shape, independent Caller preservation, repeated Owner upsert, replacement with no query, HTTP/business/timeout/invalid-JSON failures and credential-free logs. The test transport models the user-confirmed upstream append contract; it is not live BaaS implementation verification.

Ruff on all changed production and test Python files: PASS. `git diff --check`: PASS. Coverage rerun: 112 passed; the existing repository coverage exclusions omit update_device_outbound_rule, so no method coverage percentage can be claimed from this run. Focused aggregate: 155/221 (70.14%); this is not the repository-wide coverage gate. Independent reviewer/regression reports are maintained separately.

The existing large baas_service.py is already allowlisted; the task does not expand into an unrelated split. No live network mutation was executed.
