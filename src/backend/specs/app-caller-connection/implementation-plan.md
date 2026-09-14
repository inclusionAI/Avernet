# Implementation plan

1. Add failing ASGI tests for the application endpoint using real signed Principal tokens; validate application-only and mixed callers, authentication failures, ordinary audience policy, tenant binding and reset, input validation, response mapping and credential-safe logs.
2. Add failing service tests for exact grant identity, current owner/public/member access, revoked membership, tenant mismatch and existing-instance restrictions. Exercise the existing lifecycle regression tests.
3. Add require_app_caller and ordinary tenant resolution; wire only the new exact path in tenant middleware. Add the flat route and safe response diagnostics.
4. Add the application entry to the owning Service API and concrete service; inject existing grant/collaborator protocols and update the constructor fixture and context boundary.
5. Run focused API/domain tests and architecture/conformance gates; record commands and results in 002-code-report.md. Parent agent owns independent full regression, commit, rebase, push and PR.
