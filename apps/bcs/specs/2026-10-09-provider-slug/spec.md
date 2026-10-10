# Provider slug and basic information lookup

Status: approved for implementation on 2026-10-09.

## Requirement and scope

Providers need a stable, human-readable lookup key for integration discovery.
Add an optional `slug` to Provider persistence, registration and metadata PATCH,
and expose an unauthenticated lookup of explicitly selected basic information.
Keep the existing Provider file structure, as requested by the user; splitting
the existing oversized Provider files is deferred. Do not expand the file-size
allowlist or weaken CI checks.

## Slug contract

- A supplied slug is 1–64 ASCII characters, containing lowercase letters,
  digits and hyphens, beginning and ending with a letter or digit.
- Slugs are case-sensitive. Reject invalid input; do not silently normalize it.
- A slug is unique within the current environment, including disabled Providers.
- Registration may omit or send null for slug. Historical Providers retain null.
  Multiple Providers may have no slug; no automatic backfill occurs.
- PATCH omission or null preserves the current slug. A valid supplied string
  sets/replaces it. Clearing a slug is outside this change.
- A failed duplicate or invalid update leaves all Provider metadata unchanged.
- Renaming a slug immediately retires its old lookup key.

## HTTP Service API

`POST /providers` and `PATCH /providers/{provider_id}` accept optional `slug`.
Their existing authentication and ownership checks remain applicable. Existing
registration response credentials and Provider-ID behavior remain unchanged.
Authenticated Provider information responses add nullable `slug` and effective
`protocol_version`; legacy records without a version resolve to `1.0`.

`GET /providers/by-slug/{slug}` requires no authentication and returns:

```json
{
  "slug": "coding-provider",
  "provider_id": "provider_example",
  "name": "Coding Provider",
  "auth_mode": "static_bearer",
  "enabled": true,
  "protocol_version": "2.0",
  "created_at": 1791532800000,
  "updated_at": 1791532800000
}
```

`enabled` is the inverse of Provider `disabled`, independent of downlink enabled.
Disabled Providers remain discoverable with `enabled: false`. Auth modes are
`static_bearer`, `agentpass`, and `provider_admin`. Timestamps use epoch milliseconds.
The public response excludes config, credentials, owner identities, callback URLs,
webhook URLs, organization policies and coordination settings.

Errors: invalid slug → 400; missing slug → 404; duplicate registration/PATCH →
409; persistence/read/decoding failures → 500. No failed database write returns
success. Corrupt stored configuration is an internal failure, not absence.

## Architecture and propagation

HTTP adapter → ProviderManagementService → ProviderCoreService → ProviderRepoPort.
The core returns a credential-free basic-info projection, so the unauthenticated
application contract cannot return complete Provider records. Core owns slug
validation and projection semantics; repositories own uniqueness and SQL.
Memory and SQL stores implement the same behavior. Keep existing core registration
and update entry points compatible through additive slug-aware methods.

Affected consumers: Provider management HTTP and application services, repo
implementations, test fixtures constructing Provider records, and Provider clients
that opt into slug. Downlink protocols 1.0/2.0 and Bot affiliation are unchanged.
No new infrastructure Plugin API or configuration setting is introduced.

## Persistence and access cost

Add MySQL migration 032 and SQLite migration 033 after the current committed
maximum. Add nullable `slug` with a unique `(env, slug)` index. Keep every committed
migration unchanged. Fresh installations and upgrades use the same migration chain.
Deploy migrations before binaries that select the new column; rollback binaries
may leave the additive nullable column/index in place.

SQL slug lookup performs one indexed SELECT scoped by environment, returning at
most one row, with no credential read or writes. Use direct reads for this new
discovery path so PATCH/enable/disable need no second cache or invalidation scheme.
Existing by-ID caching and invalidation stay in place. Memory lookup and uniqueness
checks run under the existing Provider map lock.

## Acceptance and verification

1. Registration and PATCH persist a valid slug; omitted legacy inputs still work.
2. Public lookup returns exactly the documented fields for all auth modes/versions.
3. Disabled Providers are visible; enabling/disabling is reflected immediately.
4. Invalid, missing and duplicate slugs return the specified errors.
5. Renaming retires the old slug; duplicate PATCH changes neither slug nor name/config.
6. Memory and SQLite repository conformance cover uniqueness, nulls and failed writes.
7. SQLite upgrade preserves historical rows, is repeatable and creates the index.
8. Real MySQL validates the complete migration chain and Provider repository behavior
   when a disposable server is available; document any unavailable environment.
9. Run relevant Rust tests and architecture/protocol gates. Record source line
   counts; the existing Provider size violation is deferred by user instruction.
