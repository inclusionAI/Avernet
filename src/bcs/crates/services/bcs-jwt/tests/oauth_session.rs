//! Strict `OAuthSessionClaims` JWT behavior.
//!
//! Asserts (per Task 5 brief):
//! - Same `iat`/`exp` but different `revision` produces a different token:
//!   `revision` is part of the signed payload.
//! - `verify_at` rejects an expired token (`exp <= now`).
//! - The strict verifier rejects legacy-style tokens that lack
//!   `session_id`, `revision`, and `env` — those fields are required (no
//!   Optional fallback to the legacy `Claims` shape).
//! - `verify_for_revoke` skips ONLY the `exp` check — signature and claim
//!   completeness still apply.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

use bcs_jwt::oauth_session::{OAuthSessionClaims, OAuthSessionJwt};
use bcs_jwt::{Claims as LegacyClaims, JwtService};

#[test]
fn revision_change_produces_different_token_and_verify_at_rejects_expired() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let a = OAuthSessionClaims {
        sub: "u".into(),
        src: "github".into(),
        env: "local".into(),
        session_id: "sid-a".into(),
        revision: 1,
        iat: 100,
        exp: 200,
        name: None,
    };
    let b = OAuthSessionClaims {
        revision: 2,
        ..a.clone()
    };
    assert_ne!(jwt.sign(&a).expect("sign a"), jwt.sign(&b).expect("sign b"));
    assert!(
        jwt.verify_at(&jwt.sign(&a).expect("sign a"), 200).is_err(),
        "token at exp boundary (exp == now) must be expired"
    );
}

#[test]
fn verify_at_accepts_a_valid_unexpired_token() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims {
        sub: "u".into(),
        src: "github".into(),
        env: "local".into(),
        session_id: "sid-a".into(),
        revision: 1,
        iat: 100,
        exp: 200,
        name: Some("alice".into()),
    };
    let token = jwt.sign(&claims).expect("sign");
    let verified = jwt.verify_at(&token, 199).expect("valid at now=199");
    assert_eq!(verified.sub, "u");
    assert_eq!(verified.src, "github");
    assert_eq!(verified.env, "local");
    assert_eq!(verified.session_id, "sid-a");
    assert_eq!(verified.revision, 1);
    assert_eq!(verified.iat, 100);
    assert_eq!(verified.exp, 200);
    assert_eq!(verified.name.as_deref(), Some("alice"));
}

#[test]
fn verify_for_revoke_skips_only_exp_check() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims {
        sub: "u".into(),
        src: "github".into(),
        env: "local".into(),
        session_id: "sid-a".into(),
        revision: 1,
        iat: 100,
        exp: 200,
        name: None,
    };
    let token = jwt.sign(&claims).expect("sign");
    // Now well past expiry: verify_at errors, verify_for_revoke succeeds.
    assert!(jwt.verify_at(&token, 1_000_000).is_err());
    let verified = jwt
        .verify_for_revoke(&token)
        .expect("verify_for_revoke skips exp but keeps signature");
    assert_eq!(verified.sub, "u");
    assert_eq!(verified.session_id, "sid-a");
    assert_eq!(verified.revision, 1);
    assert_eq!(verified.env, "local");
}

#[test]
fn strict_verifier_rejects_legacy_style_token_missing_required_claims() {
    // Sign a legacy-style token (no env/session_id/revision) using the generic
    // JwtService, then assert the strict OAuthSessionJwt verifier rejects it
    // — both verify_at and verify_for_revoke must surface an error since the
    // missing required fields break claim completeness (no Optional fallback).
    let legacy = JwtService::new("test-signing-material");
    let legacy_claims = LegacyClaims {
        sub: "u".into(),
        src: "github".into(),
        iat: 100,
        exp: 200,
        name: None,
    };
    let legacy_token = legacy.sign(&legacy_claims).expect("legacy sign");
    let jwt = OAuthSessionJwt::new("test-signing-material");
    assert!(
        jwt.verify_at(&legacy_token, 100).is_err(),
        "legacy token missing env/session_id/revision must be rejected by verify_at"
    );
    assert!(
        jwt.verify_for_revoke(&legacy_token).is_err(),
        "legacy token missing env/session_id/revision must be rejected by verify_for_revoke"
    );
}

#[test]
fn verify_at_rejects_tampered_signature() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims {
        sub: "u".into(),
        src: "github".into(),
        env: "local".into(),
        session_id: "sid-a".into(),
        revision: 1,
        iat: 100,
        exp: 200,
        name: None,
    };
    let token = jwt.sign(&claims).expect("sign");
    // Flip the last character of the signature segment so the signature no
    // longer matches the HMAC over the header+claims signing input.
    let mut bytes = token.into_bytes();
    let last = bytes.len().checked_sub(1).expect("non-empty token");
    let replacement = if bytes[last] == b'A' { b'B' } else { b'A' };
    bytes[last] = replacement;
    let tampered = String::from_utf8(bytes).expect("URL-safe-no-pad ASCII subset");
    assert!(
        jwt.verify_at(&tampered, 100).is_err(),
        "tampered signature must be rejected by verify_at"
    );
    assert!(
        jwt.verify_for_revoke(&tampered).is_err(),
        "tampered signature must be rejected by verify_for_revoke"
    );
}

#[test]
fn name_field_roundtrips_with_unicode() {
    // `name` carries an optional Unicode display snapshot; both sign/verify
    // must roundtrip it without corruption.
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims {
        sub: "u".into(),
        src: "google".into(),
        env: "prod".into(),
        session_id: "sid-uni".into(),
        revision: 7,
        iat: 1000,
        exp: 2000,
        name: Some("大苹果".into()),
    };
    let token = jwt.sign(&claims).expect("sign");
    let verified = jwt.verify_at(&token, 1500).expect("verify");
    assert_eq!(verified.name.as_deref(), Some("大苹果"));
}

#[test]
fn encoding_carries_named_fields_in_signed_payload() {
    // The serialized JSON must use exact field names sub/src/env/session_id/
    // revision/iat/exp (with name omitted when None) so external verifiers
    // share a contract. Inspect the base64-decoded claims segment.
    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims {
        sub: "u".into(),
        src: "google".into(),
        env: "prod".into(),
        session_id: "sid-x".into(),
        revision: 4,
        iat: 10,
        exp: 20,
        name: None,
    };
    let token = jwt.sign(&claims).expect("sign");
    let claims_b64 = token.split('.').nth(1).expect("claims segment");
    let bytes = URL_SAFE_NO_PAD.decode(claims_b64).expect("decode");
    let json: serde_json::Value = serde_json::from_slice(&bytes).expect("parse");
    let obj = json.as_object().expect("object");
    assert_eq!(obj.len(), 7, "name omitted leaves 7 named fields");
    assert_eq!(obj["sub"], "u");
    assert_eq!(obj["src"], "google");
    assert_eq!(obj["env"], "prod");
    assert_eq!(obj["session_id"], "sid-x");
    assert_eq!(obj["revision"], 4);
    assert_eq!(obj["iat"], 10);
    assert_eq!(obj["exp"], 20);
    assert!(
        obj.get("name").is_none(),
        "name must be omitted from the payload when None"
    );
}
