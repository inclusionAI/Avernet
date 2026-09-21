# Provider-scoped OpenAPI registration

Phase 1 extends only OpenAPI V1 token issuance and registration. Legacy routes,
Provider-admin registration, CLI and bridge startup remain unchanged.

## Approved contract

- No provider: keep v1 token, six-hour TTL, response shape and ordinary upstream
  registration. Omitted mode means upstream.
- Optional `provider_id` on token issuance selects an existing enabled Provider.
  Authorize its owner/creator or an explicitly configured self-service Provider.
  Issue purpose-bound v2 tokens; legacy v1 verifiers reject them.
- Registration derives owner and Provider from the signed token, requires a
  stable `provider_bot_ref` for v2, accepts plugin/gateway and optional
  `webhook_url`. It never accepts Provider-admin credentials or owner overrides.
- Membership is distinct from delivery: upstream persists membership and a real
  runtime token without an HTTP binding. Gateway adds a delivery binding.
- Gateway resolves Bot override before Provider default. Neither alone is
  mandatory, but a usable effective endpoint is required before writes. An
  absent Bot override stays absent; token issuance requires no endpoint.
- `(env, provider_id, provider_bot_ref)` reserves one immutable registration
  identity. Same request retries resume failed writes using that identity;
  mismatched owner/mode/name/override conflict. Completion is persisted only
  after Bot, owner edges and any gateway binding exist. Completed retries never
  recreate deleted Bots, overwrite capabilities or rotate tokens.
- Provider authorization/enabled state is rechecked on redemption. Gateway
  supports existing static_bearer/provider_admin runtime auth, not agentpass.
  Provider settings/credentials are never modified or disclosed.
- Gateway checks the injected credential repository before reservation: the
  downlink_bcs_to_provider credential must exist, be enabled and have a nonblank
  secret. Not-ready returns 400 invalid_request; credential read failures
  propagate as 500. Issuance and upstream do not read or require that credential.
- Security review refinement: self-service registration cannot send the shared
  downlink bearer to a caller-chosen endpoint. Only Provider creator/owners may
  set an override; self-service gateway uses the Provider default. Bot-scoped
  downlink credentials are deferred with the independent bridge runtime work.

## Execution plan (TDD)

1. Domain: add isolated v2 codec and negative/legacy compatibility tests.
2. Service contracts: add optional wire metadata, scoped core contract and
   registration repository contract. Keep transport policy out of repositories.
3. Persistence: memory/SQLite/MySQL reservation and completion implementations,
   incremental migrations, shared conformance and failure tests.
4. Core: authorization, validation, resumable registration, real owner links,
   webhook precedence and no binding for upstream. Test both modes and failures.
5. OpenAPI facade/adapter: parse additive queries, choose v1/v2 safely, preserve
   old envelopes. Test compatibility, denied scopes and anonymous redemption.
6. Bootstrap: inject typed self-service allowlist, scoped repositories and URL
   guard. Document defaults and schema propagation.
7. Run targeted and affected crate tests, architecture/contract checks, review
   security/retry semantics and check modified source-file sizes.

## Propagation and rollout

Service API consumers rebuild with additive Rust DTO fields. JSON optional fields
are omitted for v1 clients. Database membership ledger is additive and independently
migrated for SQLite/MySQL; deploy migrations before new server. Empty allowlist
is the default (Provider owners only). Legacy Provider lists still describe
delivery bindings; this phase does not redefine those APIs as membership lists.
No new Plugin API is introduced; storage consumes the existing DbPlugin contract.
Roll back application code without deleting ledger rows; older servers ignore
the additive table and reject v2 tokens. Tokens expire in six hours.
