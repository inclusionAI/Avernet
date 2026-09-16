# BaaS Caller Authentication Implementation Plan

> Inline execution using test-driven-development.

**Goal:** Authenticate only the app-caller-connection endpoint as BaaS, preserving existing-instance business rules.
**Architecture:** Endpoint-only config replacement and shared JWT decoding; server default tenant; existing core lifecycle.
**Spec:** src/backend/specs/baas-caller-auth/001-spec-output.md

- [x] Update ASGI tests to mint `iss=baas` without principals; assert ignored extensions, signature/time failures, default DI tenant and credential-safe logs. Run before runtime edits and confirm failures.
- [x] Replace endpoint dependency with `decode_principal_token(token, replace(config, issuer="baas", verify_audience=False))`; discard claims. Delete middleware endpoint tenant branch and obsolete helper.
- [x] Remove app_id/tenant parameters from service/protocol and router; preserve Bot existence and valid existing instance checks. Update core tests including invalid UUID and force-upgrade denial.
- [x] Update framework/live JWT helpers and document existing configured secret reuse.
- [x] Run API/core/ordinary/OpenAPI regression tests, ruff and diff checks; record evidence in 002-code-report.md and commit owned files.
