# Caller token expiry coordination

Add nullable UTC caller_token_expires_at to the Caller instance ledger. Serialize installs with a real row-lock transaction; keep the current token when its expiry is later or equal. Persist expiry only after successful installation. Reset on instance replacement/release; preserve progress-only writes. Public runtime-updater DI must apply the policy to interactive Caller installs; shared service targets without an instance ledger keep existing behavior. An enterprise ASC consumer uses the same repository scope.

Tests: shorter/equal/later IAM tokens, install failure rollback, instance replacement, environment and binding fencing, parallel refresh, production-style AUTOCOMMIT transaction handling, real DI, and existing IAM/instance/repository/architecture suites. Schema rollout must precede application rollout. No independent scheduler is introduced by this change.

## Teclaw exclusion (2026-11-15)
Teclaw Caller, draft, pre and production share a runtime and do not yet support scheduled tasks. Exclude Teclaw engine or Teclaw binding provider from this expiry policy. IAM retains its existing runtime update without reading or writing the expiry ledger. ASC refresh exits before runtime/Cron probe, issuance or installation. Resolve engine from the scoped Bot, including legacy baas bindings; keep existing authorization and target selection unchanged. Cover all stages and preserve non-Teclaw behavior.
