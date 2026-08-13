# Tasks: Application API-key credentials (secbaas-compatible)

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Copy the secbaas key generator into the gateway app domain
- **Goal:** Land a byte-for-byte copy of `APIKeyGenerator` in the gateway with a
  parity test pinning migration compatibility against a secbaas-produced fixture.
- **Files:** `src/gateway/src/gateway/community/core/app/_key_gen.py` (new, copied
  verbatim from `src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py`),
  `src/gateway/src/gateway/community/core/app/__init__.py` (export),
  `src/gateway/tests/unit/plugins/test_app_key_gen.py` (new)
- **Done when:**
  - [ ] `test_secbaas_produced_hash_verifies` passes against the pinned fixture
        pair from `plan.md` (`5X1tk2yC…` / `YIrLEzbZ…`) — the migration guarantee.
  - [ ] Generate → hash → verify round-trip passes; two hashes of one key differ
        (salt uniqueness); wrong key and garbage stored-hash are rejected.
  - [ ] `generate()` output is 32-char base62 and passes `validate_format`.
  - [ ] Copied module is diff-identical to the secbaas source.
- **Depends on:** —

## Task 2: Swap `avernet_application` credential columns
- **Goal:** Replace the plaintext `token` column with `api_key_hash` +
  `api_key_prefix` (unique) in the ORM row and the canonical MySQL DDL, using
  `baas_api_key`'s column names for a straight migration map.
- **Files:** `src/gateway/src/gateway/community/core/app/_orm.py`,
  `src/gateway/migrations/mysql/001_init_schema.sql`
- **Done when:**
  - [ ] `AppRow` has `api_key_hash: String(256)` and
        `api_key_prefix: String(8), unique=True`; `token` is gone.
  - [ ] DDL mirrors the ORM: `uk_avernet_application_api_key_prefix` replaces
        `uk_avernet_application_token`; column comments updated.
  - [ ] `to_record()` and module docstrings no longer mention JWT/opaque-token
        lookup.
- **Depends on:** —

## Task 3: Rework `AppRepository` and the `AppRegistry` port to verify API keys
- **Goal:** Resolution becomes prefix lookup + status gate + constant-time hash
  verify inside the repository, behind a renamed SPI port method with soft-miss
  semantics.
- **Files:** `src/gateway/src/gateway/community/core/app/_repository.py`,
  `src/gateway/src/gateway/community/spi/app/_ports.py`,
  `src/gateway/tests/unit/plugins/test_app_registry_db.py` (rework)
- **Done when:**
  - [ ] Port and impl expose `find_app_by_api_key(api_key) -> RegisteredApp | None`;
        `find_app_by_token` is gone.
  - [ ] Correct plaintext key against a seeded hashed row resolves the
        `RegisteredApp`; wrong key with a valid prefix returns `None`.
  - [ ] Rows in `INACTIVE`/`REVOKED` status return `None` even with the correct key.
  - [ ] Empty/short (`len < 8`) keys return `None` without touching the DB.
  - [ ] `exists_prefix` and the reworked `store(api_key_hash=…, api_key_prefix=…)`
        are covered by the DB-backed tests.
- **Depends on:** Task 1, Task 2

## Task 4: Point the `app_token` strategy at the renamed port
- **Goal:** The authn strategy resolves API keys through
  `find_app_by_api_key`; header extraction and soft-miss adjudication (US27)
  are unchanged.
- **Files:** `src/gateway/src/gateway/community/plugins/authn/app_token/_strategy.py`,
  `src/gateway/tests/unit/plugins/test_app_token_strategy.py` (fake registry rename)
- **Done when:**
  - [ ] Strategy calls `find_app_by_api_key`; docstrings describe API keys, not
        registry-recognised JWTs.
  - [ ] All existing strategy behavior tests pass unmodified in substance
        (Bearer precedence, dedicated header fallback, soft miss, principal
        fields) — the resolved `AppPrincipal` shape is untouched.
- **Depends on:** Task 3

## Task 5: Rework `AppRegistrar` to mint API keys
- **Goal:** Registration generates a key via `APIKeyGenerator`, retries up to 3
  times on prefix collision, persists only hash + prefix, and returns the
  plaintext once via `IssuedApp.api_key`; `PrincipalSigner` and `clock` are
  dropped.
- **Files:** `src/gateway/src/gateway/community/core/app/_registrar.py`,
  `src/gateway/src/gateway/community/core/app/__init__.py`,
  `src/gateway/tests/unit/plugins/test_app_registrar.py` (rework — JWT claim
  tests dropped)
- **Done when:**
  - [ ] `register(...)` returns `IssuedApp` with a 32-char base62 `api_key`; the
        stored row holds only `api_key_hash`/`api_key_prefix` (no plaintext
        anywhere in the DB).
  - [ ] The freshly issued key authenticates through
        `AppRepository.find_app_by_api_key` (mint → verify closed loop).
  - [ ] Collision retry: a fake repository forcing `exists_prefix=True` sees 3
        generate attempts, then a clean raise with no partial write.
  - [ ] `creator` is recorded as both creator and modifier (behavior kept).
- **Depends on:** Task 3

## Task 6: Rewire bootstrap and the admin endpoint
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
        `AppRepository.find_app_by_api_key` (register → use closed loop over HTTP).
  - [ ] Access-key admin flow (`POST /admin/access-keys`) regression-passes
        unchanged.
- **Depends on:** Task 5

## Task 7: Migrate integration-test seeds off plaintext tokens
- **Goal:** Identity-pipeline and forward-route suites seed hashed keys and
  present the plaintext, exercising the real verify path end to end.
- **Files:** `src/gateway/tests/integration/test_identity_pipeline.py`,
  `src/gateway/tests/integration/test_forward_route.py`
- **Done when:**
  - [ ] Seeds use `AppRow(api_key_hash=hash_key(k), api_key_prefix=k[:8], …)`
        with a 32-char key; headers present the plaintext key.
  - [ ] App-identity, mixed-identity (app + user), and US27 adjudication cases
        pass; resolved principal fields (id/name/owners/type/tenant) unchanged.
- **Depends on:** Task 4, Task 6

## Task 8: Full-suite verification against spec acceptance criteria
- **Goal:** Ensure the feature meets every `spec.md` acceptance criterion and
  repo gates pass.
- **Files:** — (verification only)
- **Done when:**
  - [ ] Full gateway test suite green.
  - [ ] `scripts/ci/python_sast_local.sh` (lint/SAST gate) green for the gateway
        module.
  - [ ] Every acceptance checkbox in `spec.md` is satisfied and checked off,
        including: no plaintext persisted, secbaas-hash verifies, both-direction
        round-trip, constant-time prefix verify, status gating, prefix
        uniqueness, malformed-key cheap reject, JWT app tokens dead, principal
        contract unchanged.
- **Depends on:** Tasks 1–7

---

## Groups

> Groups bundle tasks into end-to-end units of work. `implement` executes
> one group at a time and runs code review on each group before moving on.

- **Group A — Compatible key generator:** Task 1
  - Theme: Additive, independently green — the verbatim generator plus the
    pinned secbaas parity fixture that guarantees migration compatibility.
- **Group B — Credential swap:** Tasks 2, 3, 4, 5, 6
  - Theme: The end-to-end replacement of JWT app tokens with hashed API keys —
    schema, repository + SPI port, authn strategy, registrar, bootstrap, admin —
    landing as one coherent, green slice.
- **Group C — Integration & verification:** Tasks 7, 8
  - Theme: Real-path integration seeds and the final spec acceptance check.
