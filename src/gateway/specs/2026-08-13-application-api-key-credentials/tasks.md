# Tasks: Application API-key credentials (secbaas-compatible)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1 `[x]`: Copy the secbaas key generator into the gateway app domain
- **Goal:** Land a byte-for-byte copy of `APIKeyGenerator` in the gateway with a
  parity test pinning migration compatibility against a secbaas-produced fixture.
- **Files:** `src/gateway/src/gateway/community/core/app/_key_gen.py` (new, copied
  verbatim from `src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py`),
  `src/gateway/src/gateway/community/core/app/__init__.py` (export),
  `src/gateway/tests/unit/plugins/test_app_key_gen.py` (new)
- **Done when:**
  - [x] `test_secbaas_produced_hash_verifies` passes against the pinned fixture
        pair from `plan.md` (`5X1tk2yC…` / `YIrLEzbZ…`) — the migration guarantee.
  - [x] Generate → hash → verify round-trip passes; two hashes of one key differ
        (salt uniqueness); wrong key and garbage stored-hash are rejected.
  - [x] `generate()` output is 32-char base62 and passes `validate_format`.
  - [x] Copied module is diff-identical to the secbaas source.
  - [x] *(added)* PBKDF2 parameters pinned explicitly by recomputing the derived
        key from the stored salt — the digest and iteration count are not
        recorded in the stored string, so nothing else would catch their drift.
  - [x] *(added)* Round-trip verified in both directions against the real
        secbaas implementation loaded from disk.
- **Depends on:** —

## Task 2 `[x]`: Add the API-key columns, retain the legacy credential column
- **Goal:** Add `api_key_hash` + `api_key_prefix` (nullable, prefix unique) to
  the ORM row and canonical DDL, keeping `token` as a nullable deprecated column
  so existing rows keep authenticating.
- **Files:** `src/gateway/src/gateway/community/core/app/_orm.py`,
  `src/gateway/migrations/mysql/001_init_schema.sql`
- **Done when:**
  - [x] `AppRow` has `api_key_hash: str | None` and `api_key_prefix: str | None`
        (unique); `token` becomes `str | None`, still unique, marked deprecated
        in a comment naming what must happen before removal.
  - [x] DDL matches: both new columns `DEFAULT NULL`,
        `uk_avernet_application_api_key_prefix` added,
        `uk_avernet_application_token` retained, comments updated.
  - [x] The schema change is additive only — no existing column is dropped or
        made stricter, so it can be applied before the code ships.
  - [x] Verified against a live SQLite schema: both uniqueness constraints
        reject duplicates, and an API-key row plus multiple legacy rows (all
        with `NULL` prefixes) coexist — the multi-`NULL` behavior the
        empty-string sentinel would have broken.
- **Depends on:** —

## Task 3 `[x]`: Rework `AppRepository` + the `AppRegistry` port for API-key verification
- **Goal:** Resolution becomes prefix lookup + status gate + constant-time hash
  verify inside the repository, behind a renamed port method with soft-miss
  semantics.
- **Files:** `src/gateway/src/gateway/community/core/app/_repository.py`,
  `src/gateway/src/gateway/community/spi/app/_ports.py`,
  `src/gateway/tests/unit/plugins/test_app_registry_db.py` (rework)
- **Done when:**
  - [x] Port and impl expose
        `find_app_by_credential(credential) -> RegisteredApp | None`;
        `find_app_by_token` is gone.
  - [x] Correct plaintext key against a seeded hashed row resolves the
        `RegisteredApp`; wrong key with a valid prefix returns `None`.
  - [x] Rows in `INACTIVE`/`REVOKED` status return `None` even with the correct key.
  - [x] Empty/short (`len < 8`) credentials return `None` without touching the DB.
  - [x] The stored hash and record are read inside the ORM session and verified
        after it closes (no `DetachedInstanceError`, no session held across
        ~62ms of PBKDF2).
  - [x] `exists_prefix` and the reworked `store(api_key_hash=…, api_key_prefix=…)`
        are covered by DB-backed tests.
  - [x] *(added)* `exists_prefix` ignores `status`, so a new key cannot be
        issued with a prefix already held by a deactivated row.
  - [x] *(added)* A row with a prefix but a `NULL` hash fails closed rather
        than raising.
- **Depends on:** Task 1, Task 2

## Task 4: Add the deprecated legacy-JWT path (the continuity guarantee)
- **Goal:** Existing JWT holders keep authenticating via a format-dispatched
  exact-match path that logs every use, so remaining legacy traffic is
  measurable and the path can be deleted on evidence.
- **Files:** `src/gateway/src/gateway/community/core/app/_repository.py`,
  `src/gateway/tests/unit/plugins/test_app_legacy_token.py` (new)
- **Done when:**
  - [ ] A row seeded the old way (`token=<real JWT>`, `api_key_*` NULL) still
        authenticates and yields a `RegisteredApp` identical to today's.
  - [ ] Dispatch is by format via `validate_format`, not try-and-fallback: a
        32-char base62 key never reaches the legacy branch, and each request
        performs exactly one lookup.
  - [ ] Every legacy resolution emits a WARNING naming the app (asserted with
        `caplog`); the method docstring and column comment state the deletion
        criteria.
  - [ ] API-key rows and legacy rows coexist in one table, each resolving
        through its own path.
  - [ ] An unknown JWT returns `None` (soft miss, US27 preserved); a legacy row
        that is not `ACTIVE` returns `None` (documented behavior change).
- **Depends on:** Task 3

## Task 5: Point the `app_token` strategy at the renamed port
- **Goal:** The authn strategy resolves credentials through
  `find_app_by_credential`; header extraction and soft-miss adjudication (US27)
  are unchanged.
- **Files:** `src/gateway/src/gateway/community/plugins/authn/app_token/_strategy.py`,
  `src/gateway/tests/unit/plugins/test_app_token_strategy.py` (fake registry rename)
- **Done when:**
  - [ ] Strategy calls `find_app_by_credential`; docstrings describe both
        credential forms and the transition window.
  - [ ] All existing strategy behavior tests pass unmodified in substance
        (Bearer precedence, dedicated header fallback, soft miss, principal
        fields) — the resolved `AppPrincipal` shape is untouched.
- **Depends on:** Task 3

## Task 6: Rework `AppRegistrar` to mint API keys
- **Goal:** Registration generates a key via `APIKeyGenerator`, retries up to 3
  times on prefix collision, persists only hash + prefix, and returns the
  plaintext once via `IssuedApp.api_key`; `PrincipalSigner` and `clock` are
  dropped, and no new JWT is ever issued.
- **Files:** `src/gateway/src/gateway/community/core/app/_registrar.py`,
  `src/gateway/src/gateway/community/core/app/__init__.py`,
  `src/gateway/tests/unit/plugins/test_app_registrar.py` (rework — JWT claim
  tests dropped)
- **Done when:**
  - [ ] `register(...)` returns `IssuedApp` with a 32-char base62 `api_key`; the
        stored row holds only `api_key_hash`/`api_key_prefix`, with `token` NULL.
  - [ ] The freshly issued key authenticates through
        `AppRepository.find_app_by_credential` (mint → verify closed loop).
  - [ ] Collision retry: a fake repository forcing `exists_prefix=True` sees 3
        generate attempts, then a clean raise with no partial write.
  - [ ] `creator` is recorded as both creator and modifier (behavior kept).
- **Depends on:** Task 3

## Task 7: Rewire bootstrap and the admin endpoint
- **Goal:** `build_app_registrar` loses its signer; `POST /admin/apps` returns
  `api_key` instead of `token`.
- **Files:** `src/gateway/src/gateway/community/bootstrap/_credential_issuance.py`,
  `src/gateway/src/gateway/community/bootstrap/__init__.py`,
  `src/gateway/src/gateway/community/adapters/web/admin.py`,
  `src/gateway/tests/integration/test_admin_issuance.py` (rework)
- **Done when:**
  - [ ] `build_app_registrar(db)` builds without a `PrincipalSigner`; access-key
        issuance keeps its signer untouched.
  - [ ] `POST /admin/apps` → 201 body carries `api_key` (32-char base62) and no
        `token`; nothing JWT-decodable is returned for apps.
  - [ ] The returned key immediately authenticates via
        `find_app_by_credential` (register → use closed loop over HTTP).
  - [ ] Access-key admin flow (`POST /admin/access-keys`) regression-passes
        unchanged.
- **Depends on:** Task 6

## Task 8: Cover both credential paths in the integration suites
- **Goal:** Identity-pipeline and forward-route suites exercise the real verify
  path for API keys, plus one end-to-end legacy-JWT case through the strategy.
- **Files:** `src/gateway/tests/integration/test_identity_pipeline.py`,
  `src/gateway/tests/integration/test_forward_route.py`
- **Done when:**
  - [ ] Seeds use `AppRow(api_key_hash=hash_key(k), api_key_prefix=k[:8], …)`
        with a 32-char key; headers present the plaintext key.
  - [ ] One case seeds a legacy `token=` row and authenticates with the JWT
        through the real strategy, end to end.
  - [ ] App-identity, mixed-identity (app + user), and US27 adjudication cases
        pass; resolved principal fields (id/name/owners/type/tenant) unchanged
        on both paths.
- **Depends on:** Task 4, Task 5, Task 7

## Task 9: Full-suite verification against spec acceptance criteria
- **Goal:** Ensure the feature meets every `spec.md` acceptance criterion and
  repo gates pass.
- **Files:** — (verification only)
- **Done when:**
  - [ ] Full gateway test suite green.
  - [ ] `scripts/ci/python_sast_local.sh` (lint/SAST gate) green for the gateway
        module.
  - [ ] Every acceptance checkbox in `spec.md` is satisfied and checked off,
        including: no plaintext persisted for new keys, secbaas-hash verifies,
        both-direction round-trip, constant-time prefix verify, status gating,
        prefix uniqueness, malformed-credential cheap reject, **existing JWTs
        still authenticate**, format dispatch is total, legacy warning logged,
        principal contract unchanged.
- **Depends on:** Tasks 1–8

---

## Groups

> Groups bundle tasks into end-to-end units of work. `implement` executes
> one group at a time and runs code review on each group before moving on.

- **Group A — Compatible key generator:** Task 1
  - Theme: Additive, independently green — the verbatim generator plus the
    pinned secbaas parity fixture that guarantees migration compatibility.
- **Group B — Credential swap with continuity:** Tasks 2, 3, 4, 5, 6, 7
  - Theme: API keys become the primary path — schema, repository + SPI port,
    authn strategy, registrar, bootstrap, admin — while the deprecated JWT path
    (Task 4) keeps every existing holder authenticating. Lands as one coherent,
    green slice in which no credential, old or new, stops working.
- **Group C — Integration & verification:** Tasks 8, 9
  - Theme: Both paths exercised end to end, then the final spec acceptance check.
