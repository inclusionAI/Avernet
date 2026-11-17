# Caller expiry correction

Add PATCH /openapi/v1/bots/{bot_id}/caller-instances/{caller_user_id}/token-expiry on the existing bots router. The verified human principal must be a configured super admin; application-only callers and user-id impersonation are rejected. Body requires exact entity_id, binding_id, aware expires_at (past allowed), explicit nullable expected_expires_at and an audit reason. Only existing serving dedicated Caller instances of service Caller Bots are eligible; Teclaw shared instances are excluded. No token signing, runtime calls, provisioning or schema changes.

The repository corrects the exact scoped row under SELECT FOR UPDATE, using the same predicate/lock as IAM and ASC. Verify expected old expiry in UTC under the lock, refuse stale binding/status/expected expiry and atomically update. Normal installation APIs retain future-only monotonic expiry validation. Return old/new expiry, instance and binding coordinates. Log request/success/failure with actor and safe coordinates; never log credential headers.

Tests: schema missing/null/naive handling, human super-admin acceptance and spoof/application denial, exact service/repository delegation, target eligibility, CAS/rollback and shared-row SQL/concurrency behavior, owning framework endpoint cases and architecture gates. Base is GitHub REL20261117. No deployment or remote SQL execution.
