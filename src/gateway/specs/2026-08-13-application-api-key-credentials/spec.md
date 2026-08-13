# Application API-key credentials (secbaas-compatible)

- Date: 2026-08-13
- Status: implemented
- Related: `specs/2026-07-30-application-tenant-accesskey-schema/spec.md` (the
  `avernet_application` table this changes), `specs/2026-07-30-credential-issuance/spec.md`
  (the JWT app-token issuance this replaces), secbaas API-gateway key service
  (`src/baas/src/secbaas/community/core/service/api_gateway/`, the scheme being adopted)

## Summary

Third-party apps authenticate to the gateway with API keys instead of signed JWT
app tokens. The gateway adopts the secbaas API-gateway key scheme **exactly** —
same key format, same one-way hashed storage, same prefix-based lookup, same
verification behavior — because the existing secbaas key records will be migrated
into the gateway's application registry and must keep authenticating unchanged,
with no key rotation or re-issue forced on their holders.

Existing gateway-issued JWT app tokens keep working through a **transition
window**: they cannot be converted into the new scheme (see Motivation), so they
are served by a second, deprecated lookup path that is removed once their holders
have rotated onto API keys. No credential holder — migrated or existing — is
broken by this change.

## Motivation

Today the gateway registers an app by minting a signed JWT and storing it in
plaintext as the app's lookup key; a presented token is resolved by exact string
match. Separately, secbaas operates an API-key service whose keys are random
32-character strings, stored only as salted one-way hashes and resolved by an
8-character prefix. The two credential populations are converging: secbaas's
existing key data will be migrated into the gateway's application registry, and
after migration those keys — whose plaintext only the holders have — must keep
working. That is only possible if the gateway generates, stores, and verifies
credentials with byte-for-byte the same scheme, so migrated records and
gateway-issued records are indistinguishable.

This also removes plaintext credentials from the application registry: a DB read
can no longer reveal a usable credential.

**Why existing JWTs cannot simply be re-hashed into the new columns.** Hashing
them would work — the hash function accepts any input — but the *lookup* would
not. The scheme locates a record by the credential's first 8 characters, which
assumes those characters are random. A JWT's leading characters encode its
header, which is identical for every token this gateway issues: all of them
start with `eyJhbGci` (verified empirically against the real signer). They would
collide as a single prefix, violating its uniqueness constraint and making
records indistinguishable at lookup time. Existing JWT holders therefore need a
separate, temporary path rather than a data conversion.

## User Stories

- As an existing secbaas API-key holder, I want my current key to keep
  authenticating after my record is migrated into the gateway registry, so that
  migration is invisible to me and I never have to rotate.
- As an existing gateway app-token (JWT) holder, I want my token to keep
  authenticating after this change ships, so that I can rotate onto an API key
  on my own schedule instead of being cut off on deploy day.
- As a platform operator, I want to see which apps are still presenting legacy
  JWTs, so that I know who to chase and when it is safe to delete the
  deprecated path.
- As a platform operator, I want newly registered apps to receive credentials in
  the same format as migrated ones, so that one verification path serves both
  populations and there is no legacy branch to maintain.
- As a platform operator, I want app credentials stored only as one-way hashes,
  so that a database leak does not disclose usable credentials.
- As an app developer, I want to receive my plaintext API key exactly once at
  registration, so that I know it cannot be recovered later and must be stored
  safely on my side.
- As a platform operator, I want a deactivated or revoked key to stop
  authenticating, so that migrated key lifecycle states are honored.

## Acceptance Criteria

- [x] Registering an app returns a plaintext API key once; the key is a
      32-character alphanumeric (base62) string, and no plaintext credential is
      persisted anywhere.
- [x] A key hashed by the secbaas key service (its exact salted-hash format and
      parameters), inserted into the gateway registry as a migrated record,
      authenticates successfully with its original plaintext key.
- [x] A key freshly issued by the gateway verifies under the secbaas
      verification routine, and vice versa (round-trip compatibility in both
      directions).
- [x] Verification resolves the record by the key's 8-character prefix and
      compares hashes in constant time; a wrong key with a valid prefix is
      rejected.
- [x] Only records in `ACTIVE` status authenticate — on **both** paths;
      presenting a credential whose record is `INACTIVE` or `REVOKED` is
      rejected the same way as an unknown one (soft miss — other credential
      types may still claim the request). Note this is a behavior change for the
      legacy path, which ignored `status` before this change
      (Confirmed Decisions 1: the non-`ACTIVE` population is zero).
- [x] Two registered apps never share a key prefix; registration retries on
      prefix collision and fails cleanly (no partial write) if a unique prefix
      cannot be found.
- [x] A malformed presented credential (wrong length/alphabet) is rejected
      without a database lookup.
- [x] Previously issued JWT app tokens **continue to authenticate** unchanged,
      via a deprecated exact-match path, and resolve to exactly the same app
      identity as before.
- [x] Registration never issues a JWT again: every newly registered app gets an
      API key, and the registration response returns `api_key`, not `token`.
- [x] The two credential populations are told apart deterministically by format
      (a 32-character base62 key can never be a JWT and vice versa) — no
      guess-and-fallback, no double lookup on the hot path.
- [x] Every authentication served by the deprecated JWT path emits a warning
      log identifying the app, so remaining legacy usage is observable and the
      path can be deleted once it goes quiet.
- [x] Successful authentication still yields the same app identity as today
      (id, name, owners, type, tenant) — downstream principal contracts are
      unchanged.

## In Scope

- App credential generation, storage format, lookup, and verification in the
  gateway: registration switches to API keys, and API keys become the primary
  authentication path.
- Schema change to the application registry needed to hold the hashed key and
  its prefix in migration-compatible form, alongside the retained legacy
  credential column.
- A deprecated, format-dispatched legacy path that keeps existing JWT holders
  authenticating, with warning-level logging of every legacy resolution.
- Honoring record status (`ACTIVE`/`INACTIVE`/`REVOKED`) at verification time.

## Out of Scope

- The data migration itself (moving secbaas rows into `avernet_application` is a
  separate workstream; this feature only guarantees the target is compatible).
- **Deleting the legacy path.** Removing the `token` column, the exact-match
  lookup, and its tests is a deliberate follow-up, gated on the warning logs
  going quiet — not part of this change.
- Rotating existing holders onto API keys (outreach and re-issue are an
  operational task; this change only makes both credentials work meanwhile).
- Access-key (`avernet_access_key_token`) and bot credentials — their JWT
  schemes are untouched.
- Key lifecycle management APIs (list / deactivate / revoke / rotate) — status
  is honored at verification, but no gateway API mutates it this iteration.
- Rate limiting and policy enforcement (secbaas rows carry `rate_limit_*` /
  `policy`; enforcing them at the gateway is not part of this feature).
- Admin endpoint authentication (stays not-for-prod, as today).
- Environment-scoped verification (secbaas pins lookups to its current `env`;
  the gateway has no environment concept in its authn path — see Open
  Questions).

## Confirmed Decisions

1. **Gate `status` uniformly on both paths.** Confirmed against the real table:
   the non-`ACTIVE` population is zero, so applying the `ACTIVE` gate to the
   legacy path breaks no live holder. This closes the only way this change could
   have bitten an existing user, and fixes a latent gap where a row explicitly
   registered `INACTIVE` still authenticated.
2. **Do not harden the retained `token` column.** The plaintext JWTs stay as-is
   for the window rather than moving to a `sha256(token)` lookup key: the risk
   is unchanged from today's, not worsened, and the column is scheduled for
   deletion. Not worth a column plus a backfill on a path already on its way out.

## Open Questions

- **Env pinning at verification.** secbaas restricts verification to records in
  its own deployment environment (`env` column). The gateway's authn path has no
  environment concept today. Assumed answer (proceeding with it): the gateway
  does **not** filter by `env`; if migrated data mixes environments in one
  gateway DB, keys from all of them will verify. Flag if that's wrong.
- **Migrated metadata columns.** secbaas rows carry fields with no
  `avernet_application` counterpart (`key_name`, `rate_limit_rpm/rpd`,
  `policy`, `description`, `owner` vs `owners`). Assumed answer: the migration
  workstream owns that mapping; this feature only fixes the credential columns'
  shape and semantics.
- **Transition window length.** Not fixed by this spec. The deletion follow-up
  is gated on legacy-path warnings going quiet rather than on a calendar date.
(Both remaining questions are deferrable — neither changes the design or blocks
implementation.)
