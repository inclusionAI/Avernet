//! Strict OAuth session-instance JWT tests (V1 API auth plugin chain).
//!
//! Pins the V1 session JWT contract: `env`/`session_id`/`revision` are
//! REQUIRED claims (a revision change under identical `iat`/`exp` must
//! rewrite the token; legacy tokens missing the session claims are rejected
//! by the strict verifier), `verify_at` enforces `exp` at a caller-supplied
//! instant, `verify_for_revoke` skips ONLY `exp` (signature and claim
//! integrity stay enforced so a stale browser can still log out), and the
//! claims never leak values through `Debug` (redaction sentinels for
//! `sub`/`name`/`session_id`).

use bcs_jwt::{Claims, JwtError, JwtService, OAuthSessionClaims, OAuthSessionJwt};

/// The plan's verbatim pinned JWT test: same secret, same `iat`/`exp`, only
/// `revision` differs → the token must differ; and `exp` is enforced
/// inclusively (`exp <= now` rejects at the boundary).
#[test]
fn revision_change_rewrites_the_token_and_exp_boundary_rejects() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let a = OAuthSessionClaims { sub: "u".into(), src: "github".into(), env: "local".into(),
        session_id: "sid-a".into(), revision: 1, iat: 100, exp: 200, name: None };
    let b = OAuthSessionClaims { revision: 2, ..a.clone() };
    assert_ne!(jwt.sign(&a).unwrap(), jwt.sign(&b).unwrap());
    assert!(jwt.verify_at(&jwt.sign(&a).unwrap(), 200).is_err());
}

/// A roundtrip strictly before expiry returns the exact claims, including
/// the optional display-name snapshot.
#[test]
fn verify_at_roundtrips_the_claims_strictly_before_expiry() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims { sub: "u".into(), src: "github".into(), env: "local".into(),
        session_id: "sid-a".into(), revision: 1, iat: 100, exp: 200,
        name: Some("Contract 用户".into()) };
    let token = jwt.sign(&claims).expect("sign session claims");
    let verified = jwt.verify_at(&token, 199).expect("verify strictly before exp");
    assert_eq!(verified, claims);
}

/// Legacy tokens carry no `env`/`session_id`/`revision`; the strict verifier
/// must reject them — required session claims are missing, and no error
/// message may embed claim values. Both the expiry-checking and the
/// revoke-path verifiers refuse them.
#[test]
fn legacy_tokens_missing_session_claims_are_rejected() {
    let legacy = JwtService::new("test-signing-material");
    let token = legacy
        .sign(&Claims { sub: "user-1".into(), src: "google".into(), iat: 1000, exp: 1_900_000_000, name: None })
        .expect("sign legacy token");

    let jwt = OAuthSessionJwt::new("test-signing-material");
    let verified = jwt.verify_at(&token, 1000).expect_err("missing session claims must reject");
    assert!(matches!(verified, JwtError::InvalidToken(_)), "got {verified:?}");
    let revoke = jwt.verify_for_revoke(&token).expect_err("missing session claims must reject on revoke path too");
    assert!(matches!(revoke, JwtError::InvalidToken(_)), "got {revoke:?}");
}

/// `verify_for_revoke` skips ONLY `exp`: an expired token still resolves to
/// its exact claims (the bearer lost access but keeps logout authority),
/// while the expiry-checking verifier rejects the same token.
#[test]
fn verify_for_revoke_skips_only_expiry() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims { sub: "u".into(), src: "github".into(), env: "local".into(),
        session_id: "sid-a".into(), revision: 7, iat: 100, exp: 200, name: None };
    let token = jwt.sign(&claims).expect("sign session claims");

    let revoked = jwt.verify_for_revoke(&token).expect("revoke path skips exp");
    assert_eq!(revoked, claims);
    let expired = jwt.verify_at(&token, 200).expect_err("exp is still enforced for access");
    assert!(matches!(expired, JwtError::Expired), "got {expired:?}");
}

/// `verify_for_revoke` keeps signature and claim integrity: a tampered
/// payload, a tampered signature, and a truncated token are all rejected,
/// never silently accepted as "expired but valid".
#[test]
fn verify_for_revoke_still_enforces_signature_and_claim_integrity() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let claims = OAuthSessionClaims { sub: "u".into(), src: "github".into(), env: "local".into(),
        session_id: "sid-a".into(), revision: 1, iat: 100, exp: 200, name: None };
    let token = jwt.sign(&claims).expect("sign session claims");

    let (header, payload, signature) = {
        let mut parts = token.splitn(3, '.');
        (
            parts.next().expect("header").to_string(),
            parts.next().expect("payload").to_string(),
            parts.next().expect("signature").to_string(),
        )
    };

    // A different secret must not verify the token.
    let other_key = OAuthSessionJwt::new("other-signing-material");
    assert!(other_key.verify_for_revoke(&token).is_err());

    // Tampered signature: flip one base64 character.
    let mut bad_sig: String = signature.clone();
    bad_sig.replace_range(0..1, if signature.starts_with('A') { "B" } else { "A" });
    let tampered_sig = format!("{header}.{payload}.{bad_sig}");
    assert!(jwt.verify_for_revoke(&tampered_sig).is_err());
    assert!(jwt.verify_at(&tampered_sig, 100).is_err());

    // Tampered payload: claim text changed without re-signing.
    let mut bad_payload: String = payload.clone();
    bad_payload.replace_range(0..1, if payload.starts_with('e') { "Z" } else { "e" });
    let tampered_payload = format!("{header}.{bad_payload}.{signature}");
    assert!(jwt.verify_for_revoke(&tampered_payload).is_err());

    // Truncated and multi-dotted tokens never parse.
    assert!(jwt.verify_for_revoke(&token[..token.len() - 2]).is_err());
    assert!(jwt.verify_for_revoke("a.b.c.d").is_err());
}

/// 凭据日志红线（plan 3a）：`OAuthSessionClaims`'s hand-written `Debug`
/// outputs only the type name and an elision marker — the `sub`, `name`, and
/// `session_id` sentinels must not appear in `format!("{claims:?}")`.
#[test]
fn redacted_debug_hides_claim_values() {
    const SENTINEL_SUB: &str = "sentinel-sub-42";
    const SENTINEL_SID: &str = "sentinel-sid-42";
    const SENTINEL_NAME: &str = "sentinel-name-42";

    let claims = OAuthSessionClaims { sub: SENTINEL_SUB.into(), src: "github".into(),
        env: "local".into(), session_id: SENTINEL_SID.into(), revision: 1, iat: 100, exp: 200,
        name: Some(SENTINEL_NAME.into()) };
    let rendered = format!("{claims:?}");
    assert!(!rendered.contains(SENTINEL_SUB), "Debug leaked sub: {rendered}");
    assert!(!rendered.contains(SENTINEL_SID), "Debug leaked session_id: {rendered}");
    assert!(!rendered.contains(SENTINEL_NAME), "Debug leaked name: {rendered}");
    assert!(rendered.contains("OAuthSessionClaims"), "Debug keeps the type name");
    assert!(rendered.contains(".."), "Debug shows an elided-fields marker");
}

/// A malformed token is rejected with the stable `InvalidToken` category —
/// the only error text is the fixed category, never claim material.
#[test]
fn verify_at_rejects_malformed_tokens_with_stable_categories() {
    let jwt = OAuthSessionJwt::new("test-signing-material");
    let malformed = jwt
        .verify_at("not-a-jwt", 100)
        .expect_err("a token without three parts must reject");
    assert!(matches!(malformed, JwtError::InvalidToken(_)), "got {malformed:?}");
}
