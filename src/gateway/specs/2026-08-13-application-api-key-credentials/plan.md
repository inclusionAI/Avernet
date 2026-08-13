# Plan: Application API-key credentials (secbaas-compatible)

## Approach

Copy secbaas's `APIKeyGenerator` verbatim into the gateway's app domain and swap
the app credential path from "mint JWT, store plaintext, exact-match lookup" to
"generate 32-char base62 key, store PBKDF2 hash + 8-char prefix, resolve by
prefix + constant-time verify". The `AppRegistry` SPI keeps its
opaque-credential shape (`credential in → RegisteredApp | None`): prefix lookup,
status gating, and hash verification all live inside `AppRepository`, so the
authn strategy and downstream principal contracts are untouched except for a
method rename. `AppRegistrar` stops using `PrincipalSigner` entirely.

Existing JWT holders are served by a **deprecated second path** kept for a
transition window: the retained `token` column and its exact-match lookup. The
two paths are chosen by *format*, not by fallback — `APIKeyGenerator.validate_format`
(`^[0-9A-Za-z]{32}$`) is a total discriminator, because a JWT always contains
`.` and can never be 32 base62 chars. So each request does exactly one lookup on
one path, and the hot path stays clean. Every legacy resolution logs at WARNING
with the app's identity, which is what tells us when the path is safe to delete.

Verbatim means byte-for-byte: same base62 alphabet, `pbkdf2_hmac("sha256", key,
salt, 100_000)`, stored as `base64(salt):base64(dk)`, `hmac.compare_digest`
verify, `key[:8]` prefix, 3-attempt prefix-collision retry at creation, and the
`len < 8` cheap reject before any DB touch (mirroring
`DefaultAPIKeyValidator.verify`, `src/baas/src/secbaas/community/core/service/api_gateway/_key_validator.py:32`).

## Affected Components

- `src/gateway/src/gateway/community/core/app/` — the app domain: new
  `_key_gen.py` (copied), reworked `_orm.py` / `_repository.py` /
  `_registrar.py` / `__init__.py`.
- `src/gateway/src/gateway/community/spi/app/_ports.py` — `AppRegistry` port:
  `find_app_by_token` → `find_app_by_credential` (semantics: verify a credential
  of either form, not just look a string up).
- `src/gateway/src/gateway/community/plugins/authn/app_token/_strategy.py` —
  strategy calls the renamed port; header extraction unchanged.
- `src/gateway/src/gateway/community/adapters/web/admin.py` — `POST /admin/apps`
  response returns `api_key` instead of `token` (breaking).
- `src/gateway/src/gateway/community/bootstrap/_credential_issuance.py` and
  `bootstrap/__init__.py:100` — `build_app_registrar` loses its `signer` param.
- `src/gateway/migrations/mysql/001_init_schema.sql` — canonical DDL for
  `avernet_application` swaps `token` for `api_key_hash` + `api_key_prefix`.
- Tests under `src/gateway/tests/` (unit + integration) — see Test Strategy.

Nothing outside `src/gateway` consumes `/admin/apps` or app tokens (checked:
no hits in `src/backend`, `src/bcs`, `scripts/`). `src/backend`'s
`bot_app_grant` references `avernet_application.id` only — unaffected.

## Data Model Changes

Column names deliberately mirror `baas_api_key`
(`src/baas/src/secbaas/community/core/repository/api_gateway/_orm_model.py:16-17`)
so the migration is a straight column map.

A row carries **exactly one** credential form: legacy rows have `token`, new and
migrated rows have `api_key_hash` + `api_key_prefix`. All three become nullable —
`None` is a real, intended state here (which credential form this row uses), not
a shrug, so it satisfies the `AGENTS.md` rule against gratuitous optionals.
Nullable also matters for `api_key_prefix` specifically: a unique index permits
many `NULL`s but only one `''`, so an empty-string sentinel would collide across
every legacy row.

```diff
# src/gateway/src/gateway/community/core/app/_orm.py:31 — AppRow
-    token: Mapped[str] = mapped_column(unique=True)
+    # Legacy JWT credential — deprecated, populated only on pre-migration rows.
+    token: Mapped[str | None] = mapped_column(unique=True, nullable=True)
+    api_key_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
+    api_key_prefix: Mapped[str | None] = mapped_column(
+        String(8), unique=True, nullable=True
+    )
```

```diff
# src/gateway/migrations/mysql/001_init_schema.sql:27 — avernet_application
-  `token` varchar(1024) NOT NULL COMMENT '应用访问令牌(签名 JWT)，opaque 查找键',
+  `token` varchar(1024) DEFAULT NULL COMMENT '[废弃] 旧版应用令牌(签名 JWT)，过渡期精确匹配查找键',
+  `api_key_hash` varchar(256) DEFAULT NULL COMMENT 'API Key 哈希(base64(salt):base64(dk)，PBKDF2-SHA256)',
+  `api_key_prefix` varchar(8) DEFAULT NULL COMMENT 'API Key 前缀(8 位，查找键)',
   ...
   UNIQUE KEY `uk_avernet_application_token` (`token`),
+  UNIQUE KEY `uk_avernet_application_api_key_prefix` (`api_key_prefix`),
```

Community edition creates tables via `DataSourcePlugin.create_all()` (no
Alembic); the SQL file is the canonical schema for real MySQL/OceanBase. The
ALTER for a live DB is additive only (two nullable columns, one index) —
existing rows keep their `token` untouched and keep authenticating, so the
schema step is safe to apply before the code ships. The secbaas data move
belongs to the migration workstream.

## API / Interface Changes

```diff
# BREAKING — src/gateway/src/gateway/community/spi/app/_ports.py:40 — AppRegistry
-    async def find_app_by_token(self, token: str) -> RegisteredApp | None: ...
+    async def find_app_by_credential(self, credential: str) -> RegisteredApp | None: ...
```

Named for what it accepts: either credential form, dispatched by format. (It
stays accurate after the legacy path is deleted, so the follow-up is a deletion
with no second rename.) Port contract: soft miss (`None`) for a malformed
credential (`len < 8`), unknown prefix or token, non-`ACTIVE` row, or hash
mismatch — never raises on a bad credential. `RegisteredApp` itself is unchanged
(`id/app_name/owners/app_type/tenant`), so `AppPrincipal` and forwarded
principal headers are untouched on both paths.

```jsonc
// BREAKING — POST /admin/apps → 201 (admin.py:90): "token" replaced by "api_key"
{ "id": 1, "app_name": "…", "owners": "…", "app_type": "…", "tenant": "…",
  "status": "ACTIVE", "env": "", "api_key": "5X1tk2yC6rxmKhUfWzN2GJ3CYiGGE22F" }
// api_key: 32-char base62, shown exactly once; only its hash is persisted
```

Previously issued JWT app tokens keep authenticating through the deprecated
path; no new JWT is ever issued. `Authorization: Bearer <credential>` and
`x-avernet-app-token: <credential>` both keep working as presentation channels,
for either credential form.

## Key Files & Functions

New module — byte-for-byte copy of
`src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py` (the
gateway does not depend on the baas package, so the class is copied, not
imported; a parity fixture test keeps the copy honest):

```python
# src/gateway/src/gateway/community/core/app/_key_gen.py (new, copied verbatim)
class APIKeyGenerator:
    BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    @classmethod
    def generate(cls) -> str: ...            # 32-char base62
    @classmethod
    def hash_key(cls, api_key: str) -> str: ...   # base64(salt):base64(dk), 100k PBKDF2-SHA256
    @classmethod
    def verify_key(cls, api_key: str, stored_hash: str) -> bool: ...  # constant-time
    @staticmethod
    def validate_format(api_key: str) -> bool: ...  # ^[0-9A-Za-z]{32}$
```

```diff
# src/gateway/src/gateway/community/core/app/_repository.py:38 — AppRepository
-    async def find_app_by_token(self, token: str) -> RegisteredApp | None:
-        with self._db.orm_session() as session:
-            row = session.scalar(select(self.Model).where(self.Model.token == token))
-            return None if row is None else row.to_record()
+    async def find_app_by_credential(self, credential: str) -> RegisteredApp | None:
+        if not credential or len(credential) < 8:
+            return None                      # cheap reject, no DB touch
+        if APIKeyGenerator.validate_format(credential):
+            return self._by_api_key(credential)
+        return self._by_legacy_token(credential)      # DEPRECATED path
```

```python
# src/gateway/src/gateway/community/core/app/_repository.py (new private helpers)
def _by_api_key(self, api_key: str) -> RegisteredApp | None:
    with self._db.orm_session() as session:
        row = session.scalar(select(self.Model).where(
            self.Model.api_key_prefix == api_key[:8],
            self.Model.status == _ACTIVE,
        ))
        if row is None:
            return None
        stored_hash, record = row.api_key_hash, row.to_record()   # read in-session
    # PBKDF2 is ~62ms of CPU — verify after releasing the session.
    return record if APIKeyGenerator.verify_key(api_key, stored_hash or "") else None

def _by_legacy_token(self, token: str) -> RegisteredApp | None:
    """DEPRECATED — delete with the `token` column once the warning goes quiet."""
    ...  # exact match on token + status == _ACTIVE
    logger.warning("app authenticated with deprecated JWT token: id=%s name=%s", ...)

async def exists_prefix(self, api_key_prefix: str) -> bool: ...
```

Both `stored_hash` and `record` are read *inside* the session and verified
outside it — attribute access on an expired row after the block would raise
`DetachedInstanceError`, and holding the session across 62ms of PBKDF2 would pin
a connection for no reason.

`store(...)` swaps `token: str` for `api_key_hash: str, api_key_prefix: str`
(new rows never populate `token`).

```diff
# src/gateway/src/gateway/community/core/app/_registrar.py — AppRegistrar
-    def __init__(self, repository, signer, *, clock=time.time) -> None: ...
+    def __init__(self, repository: AppRepository) -> None: ...

     async def register(self, app_name, owners, app_type, tenant, *, creator,
                        status="ACTIVE", env="", config=None) -> IssuedApp:
-        claims = {...}; token = await self._signer.sign_token(claims)
+        for _ in range(3):                     # prefix-collision retry, as secbaas
+            api_key = APIKeyGenerator.generate()
+            api_key_prefix = api_key[:8]
+            if not await self._repository.exists_prefix(api_key_prefix):
+                break
+        else:
+            raise RuntimeError("could not allocate a unique api key prefix")
+        app_id = await self._repository.store(
+            api_key_hash=APIKeyGenerator.hash_key(api_key),
+            api_key_prefix=api_key_prefix, ...)
```

```diff
# src/gateway/src/gateway/community/core/app/_registrar.py:28 — IssuedApp
 @dataclass(frozen=True)
 class IssuedApp:
-    token: str
+    api_key: str
```

```diff
# src/gateway/src/gateway/community/plugins/authn/app_token/_strategy.py:63
-        record = await self._registry.find_app_by_token(app_token)
+        record = await self._registry.find_app_by_credential(app_token)
```

Strategy header extraction (`Bearer` / `x-avernet-app-token`) is unchanged; an
unrecognized or invalid key stays a soft miss so other Bearer chains (bot_token)
still adjudicate independently (US27).

```diff
# src/gateway/src/gateway/community/bootstrap/_credential_issuance.py:24
-def build_app_registrar(db: DataSourcePlugin, signer: PrincipalSigner) -> AppRegistrar:
-    return AppRegistrar(AppRepository(db), signer)
+def build_app_registrar(db: DataSourcePlugin) -> AppRegistrar:
+    return AppRegistrar(AppRepository(db))
```

Caller: `bootstrap/__init__.py:100` drops the `principal_signer` argument.
`admin.py:100` response field `"token": issued.token` → `"api_key": issued.api_key`.
`core/app/__init__.py` exports `APIKeyGenerator` (tests and the parity check
import it via the public package face, per module import rules).

## Dependencies

None — `_key_gen.py` is stdlib-only (`base64`, `hashlib`, `hmac`, `re`,
`secrets`). `pyjwt` stays (still used by access-key issuance and principal
signing).

## Risks & Mitigations

- **Risk:** The copied generator drifts from secbaas's, silently breaking
  migration compatibility.
  **Mitigation:** A pinned cross-implementation fixture — a real
  `(api_key, stored_hash)` pair produced by the *secbaas* implementation —
  asserted against the gateway's `verify_key` (see Test Strategy). Any change to
  iterations, salt handling, or encoding fails it.
- **Risk:** `uk_avernet_application_api_key_prefix` rejects migrated rows if the
  secbaas data ever produced duplicate prefixes (their uniqueness is enforced
  at creation time, not by a DB constraint).
  **Mitigation:** Creation-time invariant makes duplicates all but impossible;
  the migration workstream should assert prefix uniqueness pre-copy. The unique
  index also backstops the registrar's check-then-insert race (IntegrityError →
  500, fail-closed, no partial write).
- **Risk:** PBKDF2 at 100k iterations adds ~50–100ms CPU per app-token
  authentication (per request, not per registration).
  **Mitigation:** Accepted — identical cost profile to secbaas today, and
  required byte-for-byte. Caching is out of scope (secbaas flags the same
  future extension).
- **Risk:** Seeded demo/test rows using plaintext `token=` stop working and
  break unrelated suites.
  **Mitigation:** Blast radius enumerated in Test Strategy; seeds move to
  `api_key_hash`/`api_key_prefix`, except the ones deliberately kept on `token=`
  to cover the legacy path.
- **Risk:** The deprecated path is never deleted and becomes permanent, leaving
  plaintext credentials in the table indefinitely.
  **Mitigation:** The WARNING log per legacy resolution makes remaining usage
  measurable, so deletion is gated on evidence rather than memory. Spec lists
  the deletion as an explicit follow-up, and both the column comment and the
  method docstring say what has to happen before removal.
- **Risk:** ~~Uniform `ACTIVE` gating breaks a live holder whose row is not
  `ACTIVE`, since the legacy lookup ignores `status` today.~~ **Retired** —
  confirmed against the real table that the non-`ACTIVE` population is zero, so
  no live holder is affected (spec §Confirmed Decisions 1).

## Alternatives Considered

- **Import secbaas's generator instead of copying** — rejected: the gateway
  package (`src/gateway/pyproject.toml`) has no dependency on `baas`, and adding
  a cross-module dependency for one stdlib-only class is worse than a copy
  pinned by a parity test.
- **Mirror secbaas's validator layering (repository returns hash, separate
  validator verifies)** — rejected: the gateway's port pattern is
  "opaque credential in → record | None" (`_ports.py:32-40`); keeping
  verification inside `AppRepository` preserves that contract and keeps the
  authn plugin storage-agnostic. "Exactly the same" binds the credential
  scheme, not secbaas's internal layering.
- **Convert existing JWTs into the new columns** (hash each JWT, store
  `token[:8]` as its prefix) — **rejected: does not work.** Hashing is fine, but
  every JWT this gateway issues begins with the same base64-encoded header, so
  all of them share the prefix `eyJhbGci`. Verified by signing five tokens for
  different apps and tenants with the real signer: 1 distinct prefix out of 5.
  That violates the prefix's uniqueness constraint, and even without the
  constraint a lookup would return every legacy row at once, forcing an O(n)
  scan of 62ms PBKDF2 verifications. This finding is what forced the transition
  window.
- **Use the JWT's last 8 characters as its prefix** (the signature tail *is*
  random — measured distinct across all five samples) — rejected: it works, but
  it needs a permanent branch in the lookup rule and makes the gateway's scheme
  diverge from secbaas's for a population that should shrink to zero. A
  deprecated exact-match path costs less and deletes cleanly.
- **Hard replacement, cutting existing JWT holders off on deploy day** —
  rejected by the product decision that no current holder may break. The
  transition window is the cost of that guarantee.
- **`sha256(token)` as the legacy lookup key**, so no plaintext remains during
  the window — viable (deterministic, so still exact-matchable; unsalted is safe
  for a high-entropy JWT), but **decided against**: it adds a column and a
  backfill for a path already scheduled for deletion, and leaves today's risk
  unchanged rather than worse (spec §Confirmed Decisions 2).
- **Pin verification to `env` like secbaas
  (`get_by_prefix_and_status(..., env=...)`)** — deferred: the gateway authn
  path has no environment concept; spec's Open Question records the assumption
  (no `env` filter). Trivial to add later (one more `where` clause).
- **Unique index on `api_key_hash`** — rejected: the hash embeds a random salt
  (unique by construction), and the lookup key is the prefix; a 256-char unique
  index buys nothing.

## Rollout

No flag needed: the schema step is purely additive and the two credential paths
are format-disjoint, so old and new coexist without a switch. Community edition
rebuilds its schema via `create_all()` on boot. For a real MySQL/OceanBase
deployment:

```bash
# 1. ALTER avernet_application: add api_key_hash + api_key_prefix (both NULL),
#    add uk_avernet_application_api_key_prefix. `token` is retained as-is.
# 2. deploy gateway with this change   → existing JWT holders keep working
# 3. copy baas_api_key rows in (migration workstream; column map + prefix
#    uniqueness check)                 → migrated key holders work on arrival
```

Steps 1 and 2 are independently reversible: rolling the code back leaves two
unused nullable columns behind, and no credential stops working at any point.

**Exit criteria for the window** (the deletion follow-up, not this change):
`legacy JWT token` warnings drop to zero across a full traffic cycle → re-issue
any stragglers via `POST /admin/apps` → drop the `token` column, `_by_legacy_token`,
its dispatch branch, and its tests.

## Test Strategy

```python
# src/gateway/tests/unit/plugins/test_app_key_gen.py (new)
# The load-bearing fixture pair below was produced by RUNNING the secbaas
# implementation (src/baas/.../_key_gen.py) — it pins migration compatibility.
_SECBAAS_KEY = "5X1tk2yC6rxmKhUfWzN2GJ3CYiGGE22F"
_SECBAAS_HASH = "YIrLEzbZybtDzATwCkQ9QERLnn0Q9z09iO+u02jvGGs=:UKS+A02LiRqVNsn0oOs9EiNO63ggsbZ3UHGnND6A08Q="
def test_secbaas_produced_hash_verifies(): ...          # read side: the migration guarantee
def test_secbaas_hash_rejects_a_different_key(): ...
def test_pinned_fixture_is_internally_consistent(): ...  # authenticates the fixture
def test_hash_key_output_uses_documented_pbkdf2_parameters(): ...  # write side
def test_generate_is_32_char_base62(): ...
def test_generate_is_not_deterministic(): ...
def test_hash_roundtrip(): ...
def test_hashing_one_key_twice_yields_different_stored_values(): ...  # salt uniqueness
def test_verify_rejects_wrong_key(): ...
def test_verify_rejects_unusable_stored_hash(): ...
def test_validate_format(): ...
def test_round_trip_against_secbaas_implementation(): ...  # both directions
def test_copy_is_byte_identical_to_secbaas_source(): ...
```

The write-side pin is not redundant with the read-side one: the latter asserts
against the fixture constant, so it cannot catch a weakened salt or iteration
count in *our* `hash_key`, and the internal round-trips stay self-consistent
under any change applied to `hash_key` and `verify_key` together. In a checkout
without `src/baas` the parity tests skip and the write-side pin is the only
guard left. Path resolution keys on the ancestor holding both `src/baas` and
`src/gateway` — not `AGENTS.md`, which this repo already places at module level
— so "split out on its own" (skip) stays distinguishable from "the file moved"
(fail).

```python
# src/gateway/tests/unit/plugins/test_app_registry_db.py (rework)
def test_correct_key_resolves_active_seeded_app(): ...  # seed hash+prefix, present plaintext
def test_wrong_key_with_known_prefix_returns_none(): ...
def test_inactive_and_revoked_rows_return_none(): ...
def test_short_or_empty_key_returns_none(): ...         # no DB lookup path
def test_exists_prefix(): ...
```

```python
# src/gateway/tests/unit/plugins/test_app_legacy_token.py (new — the continuity guarantee)
# Seeds a row the OLD way (token=<real JWT>, api_key_* NULL) and asserts it still
# authenticates, i.e. that shipping this change breaks no existing holder.
def test_legacy_jwt_still_resolves_app(): ...
def test_legacy_jwt_resolves_identical_registeredapp_as_before(): ...
def test_unknown_jwt_returns_none(): ...
def test_legacy_resolution_emits_deprecation_warning(caplog): ...
def test_inactive_legacy_row_returns_none(): ...        # documents the status-gate change
def test_api_key_and_legacy_rows_coexist_in_one_table(): ...
def test_32_char_key_never_takes_the_legacy_path(): ...  # format dispatch is total
```

```python
# src/gateway/tests/unit/plugins/test_app_registrar.py (rework — JWT tests dropped)
def test_register_returns_32_char_key_and_persists_only_hash(): ...
def test_registered_key_authenticates_via_repository(): ...   # mint→verify closed loop
def test_register_retries_on_prefix_collision(): ...          # fake repo, 3 attempts then raise
def test_creator_recorded_as_creator_and_modifier(): ...      # kept from today
```

- `tests/unit/plugins/test_app_token_strategy.py` — `_FakeAppRegistry` renames
  its method; behavior tests unchanged.
- `tests/integration/test_identity_pipeline.py:54-57,111,178` and
  `test_forward_route.py:419-427` — seed `AppRow(api_key_hash=hash_key(k),
  api_key_prefix=k[:8], ...)` with a 32-char key; present the plaintext in
  headers. Add one case seeding a legacy `token=` row and authenticating with
  the JWT, so the deprecated path is covered end to end through the real
  strategy, not just at the repository.
- `tests/integration/test_admin_issuance.py:59-91` — `POST /admin/apps` → 201
  body has `api_key` (32-char base62, format-validated), no `token`; the
  returned key immediately authenticates through
  `AppRepository.find_app_by_credential`; the DB row holds a hash, not the key,
  and its `token` column is NULL.

Gates: `scripts/ci/python_sast_local.sh` (pre-push lint) and the gateway module
test suite; run the full module gate via `OCB_PRE_PUSH_RUN_CI=1` before push.

## Notes on upstream follow-ups

Two defects live in the copied scheme. Neither can be fixed here: byte-identity
with secbaas is the migration guarantee, so a one-sided edit is worse than the
defect. Both need a secbaas change plus a re-copy, and neither is triggered by
anything the gateway does today.

1. **`validate_format` accepts a trailing newline.** `re.match(r"^…{32}$", s)`
   also matches immediately before a trailing `\n`, so a 33-character value
   passes as a 32-char key (`re.fullmatch` or `\Z` would not). Harmless for the
   credential dispatch — a newline-suffixed value authenticates on neither path,
   which Task 4 must test — but wrong wherever this is used to validate input,
   including in the migration tooling.
2. **`verify_key` decodes non-validating.** `base64.b64decode` defaults to
   discarding characters outside the alphabet, so a stored hash corrupted only
   by inserted punctuation decodes to the original bytes and still verifies —
   a fail-open on data corruption. `validate=True` fixes it.

Two further structural gaps, both outside this change's blast radius:

3. **The byte-identity guard is one-directional.** The gateway CI job is
   selected by changed paths under `src/gateway`, so a commit touching only
   secbaas's copy never runs `test_copy_is_byte_identical_to_secbaas_source`.
   The clean fix is a mirror assertion in
   `src/baas/tests/unit/core/service/api_gateway/test_key_gen.py`, which runs on
   exactly the commits the gateway job skips — a baas-module change, hence not
   made here.
4. **A skip reads as a pass at the coverage gate.** `scripts/ci/report_check.py`
   computes `passed = tests - failures - errors`, so if the parity tests ever
   skip (no ancestor holding both module trees — a split-out checkout, or a
   renamed module directory), CI still reports 100%. Hard-failing instead would
   break legitimately standalone checkouts, so the ambiguity is recorded rather
   than resolved; item 3 removes most of its consequence.

