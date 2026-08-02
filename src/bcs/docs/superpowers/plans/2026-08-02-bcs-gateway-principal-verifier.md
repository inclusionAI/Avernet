# BCS Gateway Principal Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and contract-test a production-quality HS256 Gateway Principal token verifier that returns a complete, secret-free `AuthenticatedCaller`, without mounting BCS V1 or selecting a business Actor.

**Architecture:** Add transport-neutral authenticated identity types to `bcs-service-api::application::v1`, then implement the Gateway wire projection and JWT verifier under `bcs-api-http::v1::gateway_principal`. A shared JSON fixture pins Gateway's provider serialization and BCS's consumer projection; the existing single-Principal route seam and production bootstrap stay unchanged.

**Tech Stack:** Rust 1.91, `jsonwebtoken` 11.0.0 with the pure-Rust `rust_crypto` backend, `time` 0.3, Serde, PyJWT, Pydantic 2, Cargo tests, and Pytest.

## Global Constraints

- Work only in `/private/tmp/avernet_yg-bcs-gateway-principal-contract` on branch `codex/bcs-gateway-principal-contract`; do not modify `dev` directly.
- Pin JWT verification to `HS256`; never select an algorithm from untrusted token data.
- Require `typ=JWT`, `kid=bare`, `iss=gateway`, `aud=bcs`, `iat`, `exp`, and a non-empty `principals` array.
- Apply exactly 5 seconds of clock-skew tolerance; the tolerance is not configurable.
- User, Bot, App, and AccessKey may coexist; do not select an Actor in this work.
- Never retain, log, serialize, or expose the raw JWT, HMAC key, `bot.token`, or `access_key_token`.
- Do not add scopes, bootstrap/config wiring, middleware integration, route changes, Application command migrations, or a V1 production mount.
- `bcs-service-api` remains transport-agnostic; `bcs-api-http` must not depend on `bcs-jwt`, bootstrap, legacy `bcs-http`, or concrete Application implementations.
- Follow TDD for every behavior change: add the focused failing test, observe the expected failure, add the minimum implementation, rerun the focused test, then commit.

**Design reference:** `src/bcs/docs/superpowers/specs/2026-08-02-bcs-gateway-principal-verifier-design.md`

**Library reference:** `jsonwebtoken` 11.0.0 supports HS256 and reusable `DecodingKey`; its official metadata declares Rust 1.88, below this workspace's Rust 1.91 floor. Configure it with `default-features = false, features = ["rust_crypto"]` so BCS does not pull PEM support or AWS-LC.

---

## File Structure

- Create `src/bcs/api-contracts/v1/gateway-principal/contract.md` — canonical Gateway-to-BCS V1 authentication wire contract.
- Create `src/bcs/api-contracts/v1/gateway-principal/principal-set.json` — shared all-four-Principal fixture with synthetic credential markers.
- Create `src/gateway/tests/contracts/test_bcs_gateway_principal_contract.py` — Gateway provider-side fixture and signer conformance test.
- Create `src/bcs/crates/service-api/bcs-service-api/src/application/v1/identity.rs` — transport-neutral safe authenticated caller types.
- Create `src/bcs/crates/service-api/bcs-service-api/tests/v1_authenticated_caller_contract.rs` — internal identity contract tests.
- Modify `src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs` — publish the identity module and types.
- Modify `src/bcs/crates/service-api/bcs-service-api/Cargo.toml` — add the workspace `time` dependency.
- Modify `src/bcs/Cargo.toml` — declare `jsonwebtoken` 11.0.0 as a workspace dependency.
- Modify `src/bcs/Cargo.lock` — lock the approved JWT implementation and pure-Rust crypto dependencies.
- Modify `src/bcs/crates/adapters/http/bcs-api-http/Cargo.toml` — consume `jsonwebtoken` and `time` with RFC 3339 parsing.
- Create `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/mod.rs` — public verifier exports and test module declaration.
- Create `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/wire.rs` — private Gateway claims and discriminated-union DTOs.
- Create `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs` — trust construction, JWT verification, validation, and safe projection.
- Create `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/tests.rs` — deterministic success, forgery, shape, tenant, and secret-erasure tests.
- Modify `src/bcs/crates/adapters/http/bcs-api-http/src/v1/mod.rs` — expose the versioned Gateway Principal module without changing the router.
- Modify `src/bcs/crates/adapters/http/bcs-api-http/tests/boundary_contract.rs` — prevent concrete auth-service dependency and accidental production mount.
- Modify `src/bcs/crates/service-api/bcs-service-api/tests/boundary_contracts.rs` — prohibit transport/JWT/credential fields in the internal caller contract.
- Modify `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md` — document the preparatory verifier and non-mount boundary.
- Modify `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md` — document ownership of safe authenticated identity types.

---

### Task 1: Pin the shared Gateway provider contract

**Files:**
- Create: `src/gateway/tests/contracts/test_bcs_gateway_principal_contract.py`
- Create: `src/bcs/api-contracts/v1/gateway-principal/principal-set.json`
- Create: `src/bcs/api-contracts/v1/gateway-principal/contract.md`

**Interfaces:**
- Consumes: Gateway `Principal` union and `BarePrincipalSigner` exactly as production uses them.
- Produces: a stable fixture with keys `issuer`, `audience`, `key_id`, and `principals`, consumed by Task 3's Rust verifier tests.

- [ ] **Step 1: Write the failing Gateway provider contract test**

Create `src/gateway/tests/contracts/test_bcs_gateway_principal_contract.py`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
from pydantic import TypeAdapter

from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)
from gateway.community.spi.authn import Principal

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE_PATH = (
    _REPO_ROOT
    / "src/bcs/api-contracts/v1/gateway-principal/principal-set.json"
)
_TEST_ONLY_KEY = "TEST-ONLY-bcs-principal-contract-key-32-bytes"


async def test_gateway_serialization_matches_bcs_principal_contract() -> None:
    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    principals = TypeAdapter(list[Principal]).validate_python(raw["principals"])
    serialized = [principal.model_dump(mode="json") for principal in principals]
    assert serialized == raw["principals"]

    now = int(time.time())
    signer = BarePrincipalSigner(
        PrincipalSignerConfig(
            signing_key=_TEST_ONLY_KEY,
            kid=raw["key_id"],
            issuer=raw["issuer"],
            ttl_seconds=60,
        ),
        clock=lambda: now,
    )
    token = await signer.sign(
        {principal.type: principal for principal in principals},
        audience=raw["audience"],
    )

    header = jwt.get_unverified_header(token)
    assert header == {"alg": "HS256", "kid": "bare", "typ": "JWT"}
    claims = jwt.decode(
        token,
        _TEST_ONLY_KEY,
        algorithms=["HS256"],
        audience="bcs",
        issuer="gateway",
    )
    assert claims["iat"] == now
    assert claims["exp"] == now + 60
    assert claims["principals"] == raw["principals"]
```

- [ ] **Step 2: Run the provider test and observe the missing-contract failure**

Run:

```bash
cd src/gateway
uv run pytest tests/contracts/test_bcs_gateway_principal_contract.py -q
```

Expected: FAIL with `FileNotFoundError` for `principal-set.json`. This proves the provider test is reading the shared contract rather than a local copy.

- [ ] **Step 3: Add the exact shared fixture**

Create `src/bcs/api-contracts/v1/gateway-principal/principal-set.json`:

```json
{
  "issuer": "gateway",
  "audience": "bcs",
  "key_id": "bare",
  "principals": [
    {
      "type": "user",
      "tenant": "tenant-a",
      "subject": {
        "id": "user-1",
        "username": "alice",
        "display_name": "Alice",
        "full_name": null,
        "tenant_id": "tenant-a"
      }
    },
    {
      "type": "bot",
      "tenant": "tenant-a",
      "bot": {
        "bot_uuid": "bot-1",
        "owner_id": "user-1",
        "token": "TEST_ONLY_BOT_TOKEN_MARKER",
        "app_id": 7,
        "agent_code": "agent-1",
        "tenant": "tenant-a"
      }
    },
    {
      "type": "app",
      "tenant": "tenant-a",
      "app": {
        "app_id": 7,
        "app_name": "Contract App",
        "owners": "contract-owner",
        "tenant": "tenant-a",
        "app_type": "THIRD_PARTY"
      }
    },
    {
      "type": "access_key",
      "tenant": "tenant-a",
      "access_key": {
        "access_key": "ak-test-1",
        "access_key_token": "TEST_ONLY_ACCESS_KEY_TOKEN_MARKER",
        "expire_at": "2030-01-01T00:00:00Z"
      }
    }
  ]
}
```

- [ ] **Step 4: Document the fixture's acceptance rules**

Create `src/bcs/api-contracts/v1/gateway-principal/contract.md` with these normative sections and values:

```markdown
# Gateway Principal Contract for BCS V1

`X-Avernet-Principal` carries one raw compact JWT. The verifier requires
`alg=HS256`, `typ=JWT`, `kid=bare`, `iss=gateway`, `aud=bcs`, integer `iat` and
`exp`, and a non-empty `principals` array. It allows one each of `user`, `bot`,
`app`, and `access_key`; all must agree on one non-blank tenant.

Known Principal types may add fields compatibly. Unknown Principal types,
duplicate types, removed required fields, mixed tenants, invalid time claims,
and invalid signatures fail the whole request. BCS never projects `bot.token`
or `access_key_token` into its internal caller.

This contract is preparatory: BCS V1 is not production-mounted by this change.
```

- [ ] **Step 5: Run the Gateway provider contract test**

Run:

```bash
cd src/gateway
uv run pytest tests/contracts/test_bcs_gateway_principal_contract.py -q
uv run ruff check tests/contracts/test_bcs_gateway_principal_contract.py
```

Expected: both commands PASS.

- [ ] **Step 6: Commit the provider contract**

```bash
git add src/gateway/tests/contracts/test_bcs_gateway_principal_contract.py \
        src/bcs/api-contracts/v1/gateway-principal/contract.md \
        src/bcs/api-contracts/v1/gateway-principal/principal-set.json
git commit -m "test(gateway): pin BCS principal token contract"
```

---

### Task 2: Add the transport-neutral authenticated caller contract

**Files:**
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/identity.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/tests/v1_authenticated_caller_contract.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/Cargo.toml`

**Interfaces:**
- Consumes: no transport or Gateway wire types.
- Produces: `AuthenticatedCaller` and four safe identity structs used as Task 3's verifier output.

- [ ] **Step 1: Write the failing caller-contract test**

Create `src/bcs/crates/service-api/bcs-service-api/tests/v1_authenticated_caller_contract.rs`:

```rust
use bcs_service_api::application::v1::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity,
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

#[test]
fn authenticated_caller_preserves_all_identity_kinds_without_selecting_an_actor() {
    let expire_at = OffsetDateTime::parse("2030-01-01T00:00:00Z", &Rfc3339)
        .expect("valid contract timestamp");
    let caller = AuthenticatedCaller {
        tenant: "tenant-a".into(),
        user: Some(AuthenticatedUserIdentity {
            id: "user-1".into(),
            username: "alice".into(),
            display_name: Some("Alice".into()),
            full_name: None,
        }),
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "bot-1".into(),
            owner_id: "user-1".into(),
            app_id: 7,
            agent_code: "agent-1".into(),
        }),
        app: Some(AuthenticatedAppIdentity {
            app_id: 7,
            app_name: "Contract App".into(),
            owners: "contract-owner".into(),
            app_type: "THIRD_PARTY".into(),
        }),
        access_key: Some(AuthenticatedAccessKeyIdentity {
            access_key: "ak-test-1".into(),
            expire_at,
        }),
    };

    assert_eq!(caller.tenant, "tenant-a");
    assert_eq!(caller.user.as_ref().map(|value| value.id.as_str()), Some("user-1"));
    assert_eq!(caller.bot.as_ref().map(|value| value.bot_uuid.as_str()), Some("bot-1"));
    assert_eq!(caller.app.as_ref().map(|value| value.app_id), Some(7));
    assert_eq!(
        caller.access_key.as_ref().map(|value| value.access_key.as_str()),
        Some("ak-test-1"),
    );
}
```

- [ ] **Step 2: Run the focused test and observe missing types**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-service-api \
  --test v1_authenticated_caller_contract
```

Expected: FAIL because the five authenticated identity types are not exported.

- [ ] **Step 3: Add the safe identity types**

Create `src/bcs/crates/service-api/bcs-service-api/src/application/v1/identity.rs` with `Debug, Clone, PartialEq, Eq` structs matching these exact fields:

```rust
use time::OffsetDateTime;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedCaller {
    pub tenant: String,
    pub user: Option<AuthenticatedUserIdentity>,
    pub bot: Option<AuthenticatedBotIdentity>,
    pub app: Option<AuthenticatedAppIdentity>,
    pub access_key: Option<AuthenticatedAccessKeyIdentity>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedUserIdentity {
    pub id: String,
    pub username: String,
    pub display_name: Option<String>,
    pub full_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedBotIdentity {
    pub bot_uuid: String,
    pub owner_id: String,
    pub app_id: i64,
    pub agent_code: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedAppIdentity {
    pub app_id: i64,
    pub app_name: String,
    pub owners: String,
    pub app_type: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedAccessKeyIdentity {
    pub access_key: String,
    pub expire_at: OffsetDateTime,
}
```

Add `time = { workspace = true, features = ["parsing"] }` to `bcs-service-api` dependencies. In `application/v1/mod.rs`, add `pub mod identity;` and re-export all five types explicitly.

- [ ] **Step 4: Run the caller-contract and boundary tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-service-api \
  --test v1_authenticated_caller_contract \
  --test boundary_contracts
```

Expected: PASS.

- [ ] **Step 5: Commit the internal contract**

```bash
git add src/bcs/crates/service-api/bcs-service-api/Cargo.toml \
        src/bcs/crates/service-api/bcs-service-api/src/application/v1/identity.rs \
        src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs \
        src/bcs/crates/service-api/bcs-service-api/tests/v1_authenticated_caller_contract.rs
git commit -m "feat(bcs): add authenticated caller contract"
```

---

### Task 3: Decode a valid Gateway Principal token into the safe caller

**Files:**
- Modify: `src/bcs/Cargo.toml`
- Modify: `src/bcs/Cargo.lock`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/Cargo.toml`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/wire.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/tests.rs`

**Interfaces:**
- Consumes: Task 1's fixture and Task 2's five authenticated identity types.
- Produces: `GatewayPrincipalTrust::new`, `GatewayPrincipalTokenVerifier::new`, and `GatewayPrincipalTokenVerifier::verify`, re-exported from `bcs_api_http::v1::gateway_principal`.

- [ ] **Step 1: Add a failing all-identities verifier test module**

Create `gateway_principal/mod.rs` with private `wire`, `verifier`, and test modules, and re-export the verifier API. Add `pub mod gateway_principal;` to `v1/mod.rs`. In `gateway_principal/tests.rs`, load the shared fixture and assert the expected projection:

```rust
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde::Deserialize;
use serde_json::{Value, json};

use super::{GatewayPrincipalTokenVerifier, GatewayPrincipalTrust};

const NOW: u64 = 1_785_657_600;
const TEST_KEY: &[u8] = b"TEST-ONLY-bcs-principal-contract-key-32-bytes";

#[derive(Deserialize)]
struct ContractFixture {
    issuer: String,
    audience: String,
    key_id: String,
    principals: Value,
}

fn fixture() -> ContractFixture {
    serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../../api-contracts/v1/gateway-principal/principal-set.json"
    )))
    .expect("valid shared Principal fixture")
}

fn mint(fixture: &ContractFixture, principals: Value) -> String {
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".into());
    header.kid = Some(fixture.key_id.clone());
    encode(
        &header,
        &json!({
            "iss": fixture.issuer,
            "aud": fixture.audience,
            "iat": NOW,
            "exp": NOW + 60,
            "principals": principals,
        }),
        &EncodingKey::from_secret(TEST_KEY),
    )
    .expect("test token signs")
}

fn verifier_from(fixture: &ContractFixture) -> GatewayPrincipalTokenVerifier {
    let trust = GatewayPrincipalTrust::new(
        fixture.issuer.clone(),
        fixture.audience.clone(),
        fixture.key_id.clone(),
    )
    .expect("valid trust");
    GatewayPrincipalTokenVerifier::new(TEST_KEY, trust).expect("valid verifier")
}

#[test]
fn verifies_the_shared_all_identity_fixture_without_projecting_secrets() {
    let fixture = fixture();
    let trust = GatewayPrincipalTrust::new(
        fixture.issuer.clone(),
        fixture.audience.clone(),
        fixture.key_id.clone(),
    )
    .expect("valid trust");
    let verifier = GatewayPrincipalTokenVerifier::new(TEST_KEY, trust)
        .expect("valid verifier");
    let token = mint(&fixture, fixture.principals.clone());

    let caller = verifier.verify_at(&token, NOW).expect("verified caller");

    assert_eq!(caller.tenant, "tenant-a");
    assert_eq!(caller.user.as_ref().map(|value| value.id.as_str()), Some("user-1"));
    assert_eq!(caller.bot.as_ref().map(|value| value.bot_uuid.as_str()), Some("bot-1"));
    assert_eq!(caller.app.as_ref().map(|value| value.app_id), Some(7));
    assert_eq!(
        caller.access_key.as_ref().map(|value| value.access_key.as_str()),
        Some("ak-test-1"),
    );
    let debug = format!("{caller:?}");
    assert!(!debug.contains("TEST_ONLY_BOT_TOKEN_MARKER"));
    assert!(!debug.contains("TEST_ONLY_ACCESS_KEY_TOKEN_MARKER"));
}
```

Add this selector and the focused combination/order tests:

```rust
fn select_principals(principals: &Value, kinds: &[&str]) -> Value {
    Value::Array(
        principals
            .as_array()
            .expect("fixture principals array")
            .iter()
            .filter(|principal| {
                principal["type"]
                    .as_str()
                    .is_some_and(|kind| kinds.contains(&kind))
            })
            .cloned()
            .collect(),
    )
}

#[test]
fn accepts_user_only_bot_only_and_user_plus_bot() {
    let fixture = fixture();
    for (kinds, expect_user, expect_bot) in [
        (&["user"][..], true, false),
        (&["bot"][..], false, true),
        (&["user", "bot"][..], true, true),
    ] {
        let caller = verifier_from(&fixture)
            .verify_at(&mint(&fixture, select_principals(&fixture.principals, kinds)), NOW)
            .expect("valid identity combination");
        assert_eq!(caller.user.is_some(), expect_user);
        assert_eq!(caller.bot.is_some(), expect_bot);
    }
}

#[test]
fn principal_order_does_not_change_the_normalized_caller() {
    let fixture = fixture();
    let forward = verifier_from(&fixture)
        .verify_at(&mint(&fixture, fixture.principals.clone()), NOW)
        .expect("forward order");
    let mut reversed = fixture
        .principals
        .as_array()
        .expect("fixture principals array")
        .clone();
    reversed.reverse();
    let reverse = verifier_from(&fixture)
        .verify_at(&mint(&fixture, Value::Array(reversed)), NOW)
        .expect("reverse order");
    assert_eq!(forward, reverse);
}
```

- [ ] **Step 2: Run the focused crate test and observe missing verifier APIs**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-api-http \
  gateway_principal
```

Expected: FAIL because `jsonwebtoken`, `GatewayPrincipalTrust`, and `GatewayPrincipalTokenVerifier` do not exist.

- [ ] **Step 3: Add the approved JWT dependencies**

In workspace dependencies:

```toml
jsonwebtoken = { version = "11.0.0", default-features = false, features = ["rust_crypto"] }
```

In `bcs-api-http` dependencies:

```toml
jsonwebtoken = { workspace = true }
time = { workspace = true, features = ["parsing"] }
```

Run `cargo check --manifest-path src/bcs/Cargo.toml --package bcs-api-http` once to resolve and update `src/bcs/Cargo.lock`. Confirm the lockfile selects `jsonwebtoken 11.0.0`.

- [ ] **Step 4: Add the private Gateway wire DTOs**

In `wire.rs`, define these claims and the Serde-tagged union:

```rust
use serde::Deserialize;

#[derive(Deserialize)]
pub(super) struct GatewayClaims {
    pub iss: String,
    pub aud: String,
    pub iat: u64,
    pub exp: u64,
    pub principals: Vec<GatewayPrincipal>,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(super) enum GatewayPrincipal {
    User { tenant: String, subject: GatewayUser },
    Bot { tenant: String, bot: GatewayBot },
    App { tenant: String, app: GatewayApp },
    AccessKey { tenant: String, access_key: GatewayAccessKey },
}
```

Define the nested DTOs with these exact fields:

```rust
#[derive(Deserialize)]
pub(super) struct GatewayUser {
    pub id: String,
    pub username: String,
    pub display_name: Option<String>,
    pub full_name: Option<String>,
    pub tenant_id: Option<String>,
}
#[derive(Deserialize)]
pub(super) struct GatewayBot {
    pub bot_uuid: String,
    pub owner_id: String,
    pub app_id: i64,
    pub agent_code: String,
    pub tenant: String,
}
#[derive(Deserialize)]
pub(super) struct GatewayApp {
    pub app_id: i64,
    pub app_name: String,
    pub owners: String,
    pub tenant: String,
    pub app_type: String,
}
#[derive(Deserialize)]
pub(super) struct GatewayAccessKey {
    pub access_key: String,
    pub expire_at: String,
}
```

Derive only `Deserialize` and test-support traits needed by the module. Do not declare Bot `token` or `access_key_token`; default Serde behavior deliberately ignores those known wire extras.

- [ ] **Step 5: Implement trust construction and the happy-path verifier**

In `verifier.rs`, define these exact public interfaces:

```rust
pub struct GatewayPrincipalTrust {
    issuer: String,
    audience: String,
    key_id: String,
}

impl GatewayPrincipalTrust {
    pub fn new(
        issuer: impl Into<String>,
        audience: impl Into<String>,
        key_id: impl Into<String>,
    ) -> Result<Self, GatewayPrincipalVerifierBuildError>;
}

pub struct GatewayPrincipalTokenVerifier {
    decoding_key: jsonwebtoken::DecodingKey,
    trust: GatewayPrincipalTrust,
}

impl GatewayPrincipalTokenVerifier {
    pub fn new(
        signing_key: &[u8],
        trust: GatewayPrincipalTrust,
    ) -> Result<Self, GatewayPrincipalVerifierBuildError>;

    pub fn verify(
        &self,
        token: &str,
    ) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError>;

    pub(super) fn verify_at(
        &self,
        token: &str,
        now: u64,
    ) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError>;
}
```

Introduce the complete closed error vocabulary with the public API so every
subsequent validation step preserves the same signatures:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GatewayPrincipalVerifierBuildError {
    #[error("Gateway Principal signing key is empty")]
    EmptySigningKey,
    #[error("Gateway Principal trust configuration is invalid")]
    InvalidTrustConfiguration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GatewayPrincipalVerificationError {
    #[error("Gateway Principal token is empty")]
    EmptyToken,
    #[error("Gateway Principal token header is invalid")]
    InvalidHeader,
    #[error("Gateway Principal token algorithm is unsupported")]
    UnsupportedAlgorithm,
    #[error("Gateway Principal token type is invalid")]
    InvalidTokenType,
    #[error("Gateway Principal token key id is invalid")]
    InvalidKeyId,
    #[error("Gateway Principal token signature is invalid")]
    InvalidSignature,
    #[error("Gateway Principal token claims are invalid")]
    InvalidClaims,
    #[error("Gateway Principal set is invalid")]
    InvalidPrincipalSet,
}
```

Use `decode_header`, require HS256/JWT/the expected key ID, then call `decode::<GatewayClaims>` with `Validation::new(Algorithm::HS256)`. Set issuer and audience; require `exp`, `iss`, and `aud`; set `validate_exp = false` so `verify_at` owns deterministic time checks. `iat` and `principals` remain required through non-optional Serde fields. Convert the four wire variants into the Task 2 identity structs and parse AccessKey expiry with `OffsetDateTime::parse(..., &Rfc3339)`.

Implement `verify` by obtaining the current non-negative Unix timestamp from
`OffsetDateTime::now_utc()` and delegating to `verify_at`. In
`gateway_principal/mod.rs`, re-export all four public types explicitly:

```rust
pub use verifier::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust,
    GatewayPrincipalVerificationError, GatewayPrincipalVerifierBuildError,
};
```

For this task, implement the successful projection and `iat < exp`; Task 4 adds all fail-closed semantic validation. Never derive `Debug` for the verifier or decoding key holder.

- [ ] **Step 6: Run the happy-path tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-api-http \
  gateway_principal
```

Expected: User-only, Bot-only, User+Bot, all-four identities, order independence, and secret-erasure tests PASS.

- [ ] **Step 7: Commit the working consumer projection**

```bash
git add src/bcs/Cargo.toml src/bcs/Cargo.lock \
        src/bcs/crates/adapters/http/bcs-api-http/Cargo.toml \
        src/bcs/crates/adapters/http/bcs-api-http/src/v1/mod.rs \
        src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal
git commit -m "feat(bcs): parse gateway principal tokens"
```

---

### Task 4: Enforce fail-closed JWT and Principal-set validation

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/tests.rs`

**Interfaces:**
- Consumes: Task 3's verifier interface; no public signature changes.
- Produces: deterministic build/request error enums and the complete validation contract from the design.

- [ ] **Step 1: Add failing trust, header, signature, and time tests**

Extend the test helpers so tests can mint with an arbitrary `Header`, signing key, and JSON claims. Add these exact assertions:

```rust
use bcs_service_api::application::v1::AuthenticatedCaller;

use super::{
    GatewayPrincipalTokenVerifier, GatewayPrincipalTrust,
    GatewayPrincipalVerificationError, GatewayPrincipalVerifierBuildError,
};

fn header(typ: &str, kid: &str) -> Header {
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some(typ.into());
    header.kid = Some(kid.into());
    header
}

fn valid_claims() -> Value {
    let fixture = fixture();
    json!({
        "iss": fixture.issuer,
        "aud": fixture.audience,
        "iat": NOW,
        "exp": NOW + 60,
        "principals": fixture.principals,
    })
}

fn mint_with(header: Header, claims: Value, signing_key: &[u8]) -> String {
    encode(&header, &claims, &EncodingKey::from_secret(signing_key))
        .expect("test token signs")
}

fn verifier() -> GatewayPrincipalTokenVerifier {
    let fixture = fixture();
    verifier_from(&fixture)
}

fn token_with_times(iat: u64, exp: u64) -> String {
    let mut claims = valid_claims();
    claims["iat"] = json!(iat);
    claims["exp"] = json!(exp);
    mint_with(header("JWT", "bare"), claims, TEST_KEY)
}
```

Keep the existing imports of `Algorithm`, `EncodingKey`, `Header`, `encode`,
`Value`, and `json`; replace the narrower `super` import from Task 3 with the
one above.

```rust
#[test]
fn rejects_wrong_algorithm_before_claims_are_trusted() {
    let mut header = Header::new(Algorithm::HS512);
    header.typ = Some("JWT".into());
    header.kid = Some("bare".into());
    let token = mint_with(header, valid_claims(), TEST_KEY);
    assert_eq!(
        verifier().verify_at(&token, NOW),
        Err(GatewayPrincipalVerificationError::UnsupportedAlgorithm),
    );
}

#[test]
fn rejects_wrong_token_type_and_key_id() {
    let wrong_type = mint_with(header("NOT-JWT", "bare"), valid_claims(), TEST_KEY);
    let wrong_kid = mint_with(header("JWT", "rotated"), valid_claims(), TEST_KEY);
    assert_eq!(
        verifier().verify_at(&wrong_type, NOW),
        Err(GatewayPrincipalVerificationError::InvalidTokenType),
    );
    assert_eq!(
        verifier().verify_at(&wrong_kid, NOW),
        Err(GatewayPrincipalVerificationError::InvalidKeyId),
    );
}

#[test]
fn rejects_wrong_signature_issuer_and_audience() {
    let wrong_key = mint_with(header("JWT", "bare"), valid_claims(), b"different-test-key");
    assert_eq!(
        verifier().verify_at(&wrong_key, NOW),
        Err(GatewayPrincipalVerificationError::InvalidSignature),
    );
    for (claim, value) in [("iss", "other-gateway"), ("aud", "backend")] {
        let mut claims = valid_claims();
        claims[claim] = json!(value);
        let token = mint_with(header("JWT", "bare"), claims, TEST_KEY);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
        );
    }
}

#[test]
fn enforces_exact_five_second_clock_skew() {
    let accepted_future = token_with_times(NOW + 5, NOW + 65);
    let rejected_future = token_with_times(NOW + 6, NOW + 66);
    let accepted_expired = token_with_times(NOW - 65, NOW - 4);
    let rejected_expired = token_with_times(NOW - 66, NOW - 5);
    assert!(verifier().verify_at(&accepted_future, NOW).is_ok());
    assert_eq!(
        verifier().verify_at(&rejected_future, NOW),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );
    assert!(verifier().verify_at(&accepted_expired, NOW).is_ok());
    assert_eq!(
        verifier().verify_at(&rejected_expired, NOW),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );
}
```

Add the remaining trust/shape tests explicitly:

```rust
#[test]
fn rejects_empty_trust_material() {
    let valid = GatewayPrincipalTrust::new("gateway", "bcs", "bare")
        .expect("valid trust");
    assert_eq!(
        GatewayPrincipalTokenVerifier::new(b"", valid).err(),
        Some(GatewayPrincipalVerifierBuildError::EmptySigningKey),
    );
    for values in [("", "bcs", "bare"), ("gateway", "", "bare"), ("gateway", "bcs", "")] {
        assert!(matches!(
            GatewayPrincipalTrust::new(values.0, values.1, values.2),
            Err(GatewayPrincipalVerifierBuildError::InvalidTrustConfiguration),
        ));
    }
}

#[test]
fn rejects_empty_malformed_and_unsigned_tokens() {
    let verifier = verifier();
    assert_eq!(
        verifier.verify_at("", NOW),
        Err(GatewayPrincipalVerificationError::EmptyToken),
    );
    for token in [
        "not-a-jwt",
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.e30.",
    ] {
        assert_eq!(
            verifier.verify_at(token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidHeader),
        );
    }
}

#[test]
fn rejects_missing_required_claims() {
    for claim in ["iss", "aud", "iat", "exp", "principals"] {
        let mut claims = valid_claims();
        claims.as_object_mut().expect("claims object").remove(claim);
        let token = mint_with(header("JWT", "bare"), claims, TEST_KEY);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
            "missing {claim}",
        );
    }
}

#[test]
fn rejects_invalid_claim_shapes() {
    for (claim, value) in [
        ("iat", json!("1785657600")),
        ("exp", json!(null)),
        ("principals", json!({})),
    ] {
        let mut claims = valid_claims();
        claims[claim] = value;
        let token = mint_with(header("JWT", "bare"), claims, TEST_KEY);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
            "invalid shape for {claim}",
        );
    }
}

#[test]
fn rejects_non_positive_token_lifetime() {
    for (iat, exp) in [(NOW, NOW), (NOW + 1, NOW)] {
        let token = token_with_times(iat, exp);
        assert_eq!(
            verifier().verify_at(&token, NOW),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
        );
    }
}
```

- [ ] **Step 2: Add failing Principal-set validation tests**

Using `valid_claims()` and JSON mutation, add this helper and the semantic
rejection tests. Fixture ordering is intentionally part of the fixture only,
not production behavior: User=0, Bot=1, App=2, AccessKey=3.

```rust
fn verify_principals(principals: Value) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
    let mut claims = valid_claims();
    claims["principals"] = principals;
    let token = mint_with(header("JWT", "bare"), claims, TEST_KEY);
    verifier().verify_at(&token, NOW)
}

#[test]
fn rejects_empty_unknown_and_duplicate_principal_types() {
    assert_eq!(
        verify_principals(json!([])),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
    );

    let mut unknown = fixture().principals;
    unknown[0]["type"] = json!("future_identity");
    assert_eq!(
        verify_principals(unknown),
        Err(GatewayPrincipalVerificationError::InvalidClaims),
    );

    let mut duplicate = fixture().principals;
    let repeated_user = duplicate[0].clone();
    duplicate.as_array_mut().expect("principals array").push(repeated_user);
    assert_eq!(
        verify_principals(duplicate),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
    );
}

#[test]
fn rejects_missing_required_known_principal_fields() {
    for (index, field) in [
        (0, "subject"),
        (1, "bot"),
        (2, "app"),
        (3, "access_key"),
    ] {
        let mut principals = fixture().principals;
        principals[index]
            .as_object_mut()
            .expect("principal object")
            .remove(field);
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidClaims),
            "missing {field}",
        );
    }
}

#[test]
fn rejects_mixed_and_contradictory_tenants() {
    for pointer in [
        "/1/tenant",
        "/1/bot/tenant",
        "/2/app/tenant",
        "/0/subject/tenant_id",
    ] {
        let mut principals = fixture().principals;
        *principals.pointer_mut(pointer).expect("fixture pointer") = json!("tenant-b");
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
            "tenant mutation at {pointer}",
        );
    }

    for value in ["", "   "] {
        let mut principals = fixture().principals;
        principals[0]["subject"]["tenant_id"] = json!(value);
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
        );
    }
}

#[test]
fn rejects_blank_stable_identities_and_invalid_access_key_time() {
    for pointer in [
        "/0/tenant",
        "/0/subject/id",
        "/0/subject/username",
        "/1/bot/bot_uuid",
        "/1/bot/owner_id",
        "/1/bot/agent_code",
        "/3/access_key/access_key",
    ] {
        let mut principals = fixture().principals;
        *principals.pointer_mut(pointer).expect("fixture pointer") = json!("   ");
        assert_eq!(
            verify_principals(principals),
            Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
            "blank identity at {pointer}",
        );
    }

    let mut principals = fixture().principals;
    principals[3]["access_key"]["expire_at"] = json!("not-rfc3339");
    assert_eq!(
        verify_principals(principals),
        Err(GatewayPrincipalVerificationError::InvalidPrincipalSet),
    );
}

#[test]
fn ignores_future_fields_within_known_principal_types() {
    let mut principals = fixture().principals;
    principals[0]["future_principal_field"] = json!(true);
    principals[0]["subject"]["future_user_field"] = json!(1);
    principals[1]["bot"]["future_bot_field"] = json!(2);
    principals[2]["app"]["future_app_field"] = json!(3);
    principals[3]["access_key"]["future_access_key_field"] = json!(4);
    assert!(verify_principals(principals).is_ok());
}
```

- [ ] **Step 3: Run the new tests and verify they fail for missing validation**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-api-http \
  gateway_principal
```

Expected: the new forgery/time/duplicate/tenant/blank-identity tests FAIL against Task 3's permissive successful projection.

- [ ] **Step 4: Complete the closed build and request error mapping**

Retain the exact error enums introduced in Task 3 without adding raw library
messages or secret-bearing fields:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GatewayPrincipalVerifierBuildError {
    #[error("Gateway Principal signing key is empty")]
    EmptySigningKey,
    #[error("Gateway Principal trust configuration is invalid")]
    InvalidTrustConfiguration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GatewayPrincipalVerificationError {
    #[error("Gateway Principal token is empty")]
    EmptyToken,
    #[error("Gateway Principal token header is invalid")]
    InvalidHeader,
    #[error("Gateway Principal token algorithm is unsupported")]
    UnsupportedAlgorithm,
    #[error("Gateway Principal token type is invalid")]
    InvalidTokenType,
    #[error("Gateway Principal token key id is invalid")]
    InvalidKeyId,
    #[error("Gateway Principal token signature is invalid")]
    InvalidSignature,
    #[error("Gateway Principal token claims are invalid")]
    InvalidClaims,
    #[error("Gateway Principal set is invalid")]
    InvalidPrincipalSet,
}
```

Map `jsonwebtoken::errors::ErrorKind::InvalidSignature` only to `InvalidSignature`; map every other decode/validation error to `InvalidClaims`. Do not attach the source error.

- [ ] **Step 5: Implement header, time, and Principal-set validation**

Add focused helpers in `verifier.rs`:

```rust
const CLOCK_SKEW_SECONDS: u64 = 5;

fn validate_times(iat: u64, exp: u64, now: u64) -> Result<(), GatewayPrincipalVerificationError> {
    let latest_allowed_iat = now.saturating_add(CLOCK_SKEW_SECONDS);
    let expiration_with_skew = exp.saturating_add(CLOCK_SKEW_SECONDS);
    if iat >= exp || iat > latest_allowed_iat || now >= expiration_with_skew {
        return Err(GatewayPrincipalVerificationError::InvalidClaims);
    }
    Ok(())
}

fn is_non_blank(value: &str) -> bool {
    !value.trim().is_empty()
}
```

Normalize Principals into four initially-empty `Option` slots. Reject before assignment when a slot is already occupied. Set the normalized tenant from the first Principal, require every later outer tenant to equal it, and validate each nested tenant before constructing the safe identity. Require the stable identifiers listed in Step 2 to pass `is_non_blank`. Parse AccessKey expiry with RFC 3339; preserve the original identity strings rather than trimming or rewriting them.

Keep unknown-field compatibility by leaving `deny_unknown_fields` off the wire structs. Keep unknown-type rejection through the Serde-tagged enum.

- [ ] **Step 6: Prove errors do not expose credentials**

Add this exact regression test; it signs the shared fixture after making the
outer tenant invalid, so both synthetic credential markers are present in the
verified payload but absent from the closed error:

```rust
#[test]
fn verification_errors_do_not_expose_tokens_or_keys() {
    let mut principals = fixture().principals;
    principals[0]["tenant"] = json!("   ");
    let mut claims = valid_claims();
    claims["principals"] = principals;
    let token = mint_with(header("JWT", "bare"), claims, TEST_KEY);

    let error = verifier()
        .verify_at(&token, NOW)
        .expect_err("blank tenant must fail");
    let message = error.to_string();
    for forbidden in [
        "TEST_ONLY_BOT_TOKEN_MARKER",
        "TEST_ONLY_ACCESS_KEY_TOKEN_MARKER",
        token.as_str(),
        std::str::from_utf8(TEST_KEY).expect("ASCII test key"),
    ] {
        assert!(!message.contains(forbidden));
    }
}
```

- [ ] **Step 7: Run the complete verifier suite**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-api-http \
  gateway_principal
```

Expected: all success, forgery, time, Principal-shape, tenant, compatibility, and secret-erasure tests PASS.

- [ ] **Step 8: Commit fail-closed validation**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs \
        src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/tests.rs
git commit -m "feat(bcs): enforce gateway principal trust validation"
```

---

### Task 5: Enforce boundaries, document staging, and run final verification

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/boundary_contract.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/boundary_contracts.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`
- Modify: `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: executable architecture guards and an explicitly production-unreachable completed slice.

- [ ] **Step 1: Extend the HTTP adapter boundary guards**

In `bcs-api-http/tests/boundary_contract.rs`, add `"bcs-jwt"` to the forbidden manifest dependencies. Add a staging guard that resolves
`../../../bootstrap/bcs/Cargo.toml` from `CARGO_MANIFEST_DIR` and asserts that no dependency line begins with `bcs-api-http`; use a line-prefix check rather than a broad substring so comments cannot fail it.

- [ ] **Step 2: Extend the Application identity boundary guard**

In `bcs-service-api/tests/boundary_contracts.rs`, read `src/application/v1/identity.rs` and assert that it contains none of these production transport/credential tokens:

```rust
for forbidden in [
    "jsonwebtoken",
    "axum",
    "HeaderMap",
    "X-Avernet-Principal",
    "access_key_token",
    "bot_token",
] {
    assert!(
        !source.contains(forbidden),
        "authenticated identity contract must not contain {forbidden}",
    );
}
```

Run both boundary test binaries; they should PASS immediately and then guard future integration work.

- [ ] **Step 3: Update crate context documents**

Update `bcs-api-http/CONTEXT.md` to state that V1 owns the Gateway wire projection and injectable verifier implementation, while HTTP header extraction, production trust selection, and router mounting remain deferred. Update `bcs-service-api/CONTEXT.md` to state that Application V1 owns secret-free `AuthenticatedCaller` contract types but no JWT or HTTP semantics.

- [ ] **Step 4: Format and run focused Rust verification**

Run:

```bash
cargo fmt --manifest-path src/bcs/Cargo.toml --all
cargo fmt --manifest-path src/bcs/Cargo.toml --all -- --check
cargo test --manifest-path src/bcs/Cargo.toml \
  --package bcs-service-api \
  --package bcs-api-http
cargo check --manifest-path src/bcs/Cargo.toml \
  --package bcs-service-api \
  --package bcs-api-http \
  --all-targets
cargo clippy --manifest-path src/bcs/Cargo.toml \
  --package bcs-service-api \
  --package bcs-api-http \
  --all-targets -- -D warnings
```

Expected: every command exits 0; existing 27-route tests remain green.

- [ ] **Step 5: Run Gateway provider verification**

Run:

```bash
cd src/gateway
uv run pytest tests/contracts/test_bcs_gateway_principal_contract.py -q
uv run ruff check tests/contracts/test_bcs_gateway_principal_contract.py
```

Expected: both commands exit 0.

- [ ] **Step 6: Verify staging and secret hygiene**

Run from the repository root:

```bash
git diff --check
git status --short
rg -n 'bcs-api-http' src/bcs/crates/bootstrap/bcs/Cargo.toml
rg -n 'TEST_ONLY_(BOT|ACCESS_KEY)_TOKEN_MARKER' \
  src/bcs/api-contracts/v1/gateway-principal \
  src/bcs/crates/adapters/http/bcs-api-http
```

Expected: no whitespace errors; bootstrap search has no dependency match; marker values appear only in the contract fixture and assertions, never in production Rust sources.

- [ ] **Step 7: Commit boundary and documentation updates**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md \
        src/bcs/crates/adapters/http/bcs-api-http/tests/boundary_contract.rs \
        src/bcs/crates/service-api/bcs-service-api/CONTEXT.md \
        src/bcs/crates/service-api/bcs-service-api/tests/boundary_contracts.rs
git commit -m "test(bcs): enforce gateway principal boundaries"
```

- [ ] **Step 8: Confirm the implementation branch is clean and scoped**

Run:

```bash
git status --short
git log --oneline --decorate dev..HEAD
git diff --stat dev...HEAD
```

Expected: clean worktree; only the approved design/plan, shared contract, Gateway provider test, BCS identity/verifier code, focused dependency changes, tests, and context docs differ from `dev`.
