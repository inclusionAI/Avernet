//! OAuth cookie-source dispatch and mixed-chain precedence tests.

use super::*;

// ---------------------------------------------------------------------------
// OAuth source dispatch (spec §7.2) over the real engine + memory store
// ---------------------------------------------------------------------------

#[tokio::test]
async fn oauth_source_matches_with_provenance_and_human_projection() {
    let (identities, engine) = oauth_harness().await;
    let token = install_session(&engine, identities.as_ref(), "github", "ext-gh-1", "张三").await;
    let auth = auth_from_toml(
        r#"chain = ["github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gh-client-secret", "gh-secret-value"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("github chain builds");

    let verified = built
        .verifier
        .verify(&headers_with_cookie(&format!("bcs_session={token}")))
        .await
        .expect("valid github cookie must verify");
    assert_eq!(verified.authentication_context.source, "github");
    assert!(matches!(
        verified.authentication_context.credential_kind,
        bcs_api_http::CredentialKind::OAuthSessionCookie
    ));
    let user = verified.caller.user.expect("Human projection");
    assert_eq!(user.username, "张三");
    assert!(!user.id.is_empty(), "user.id is the internal identity id");
    assert!(verified.caller.bot.is_none());
    assert!(verified.caller.app.is_none());
    assert!(verified.caller.access_key.is_none());
    assert!(verified.caller.tenant.is_none(), "tenant must NOT be fabricated");
}

#[tokio::test]
async fn cross_source_cookie_verifies_once_and_only_matches_its_source() {
    let (identities, engine) = oauth_harness().await;
    let token = install_session(&engine, identities.as_ref(), "github", "ext-gh-2", "李四").await;
    // Chain order: google first, github second. The github cookie must be
    // Missing at the google position (NOT Invalid) and match at the github
    // position — with exactly ONE strict verification across both positions.
    let auth = auth_from_toml(
        r#"chain = ["google", "github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[google]
client_id = "gg-id"
client_secret_secret = "gg-client-secret"

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gg-client-secret", "gg-secret-value"),
        ("gh-client-secret", "gh-secret-value"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("google+github chain builds");

    let verified = built
        .verifier
        .verify(&headers_with_cookie(&format!("bcs_session={token}")))
        .await
        .expect("github position must resolve the identity");
    assert_eq!(verified.authentication_context.source, "github");
    assert_eq!(
        identities.get_calls(),
        1,
        "the strict engine verify must run exactly ONCE per request (attempt cache shared)"
    );
    assert_eq!(
        secrets.reads(),
        2,
        "only in-chain providers' client secrets resolved (signing material supplied directly)"
    );
}

#[tokio::test]
async fn provider_outside_allowlist_is_forbidden() {
    let (identities, engine) = oauth_harness().await;
    let token = install_session(&engine, identities.as_ref(), "wechat", "ext-wx-1", "王五").await;
    // Chain only github → allowlist is [github]; the wechat-issued cookie is
    // a VALID session for a DISABLED provider → 403 Forbidden, chain stops.
    let auth = auth_from_toml(
        r#"chain = ["github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gh-client-secret", "gh-secret-value"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("github chain builds");

    let outcome = built
        .verifier
        .verify(&headers_with_cookie(&format!("bcs_session={token}")))
        .await;
    assert!(
        matches!(outcome, Err(PrincipalVerificationError::Forbidden)),
        "valid session of an unenabled provider must be Forbidden, got {outcome:?}"
    );
}

#[tokio::test]
async fn invalid_cookie_is_invalid_and_stops_the_chain() {
    let (identities, engine) = oauth_harness().await;
    // A token that is not a valid OAuth session JWT at all.
    let token = "not-a-real-jwt.sig.value";
    let auth = auth_from_toml(
        r#"chain = ["google", "github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[google]
client_id = "gg-id"
client_secret_secret = "gg-client-secret"

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gg-client-secret", "gg-secret-value"),
        ("gh-client-secret", "gh-secret-value"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("chain builds");

    let outcome = built
        .verifier
        .verify(&headers_with_cookie(&format!("bcs_session={token}")))
        .await;
    assert!(
        matches!(outcome, Err(PrincipalVerificationError::Invalid)),
        "invalid cookie must be Invalid at the first OAuth position, got {outcome:?}"
    );
    // NOTE: a signature-invalid JWT never reaches the store (the engine
    // rejects during JWT verification), so `get_calls` stays 0 here. The
    // chain-stop is guaranteed by the composite's terminal-outcome contract
    // (Invalid is never followed by a fallback — covered by the bcs-api-http
    // composite tests), and by the cached-attempt proof in the
    // cross-source test.
}

#[tokio::test]
async fn store_fault_is_unavailable() {
    let (identities, engine) = oauth_harness().await;
    let token = install_session(&engine, identities.as_ref(), "github", "ext-gh-3", "赵六").await;
    identities.fail_gets.store(true, Ordering::SeqCst);
    let auth = auth_from_toml(
        r#"chain = ["github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gh-client-secret", "gh-secret-value"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("chain builds");

    let outcome = built
        .verifier
        .verify(&headers_with_cookie(&format!("bcs_session={token}")))
        .await;
    assert!(
        matches!(outcome, Err(PrincipalVerificationError::Unavailable)),
        "store fault must surface as Unavailable (503), got {outcome:?}"
    );
}

#[tokio::test]
async fn missing_and_duplicate_cookies_are_never_treated_as_credentials() {
    let (identities, engine) = oauth_harness().await;
    let _token = install_session(&engine, identities.as_ref(), "github", "ext-gh-4", "孙七").await;
    let auth = auth_from_toml(
        r#"chain = ["github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gh-client-secret", "gh-secret-value"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("chain builds");

    // No cookie at all → Missing.
    let outcome = built.verifier.verify(&HeaderMap::new()).await;
    assert!(matches!(outcome, Err(PrincipalVerificationError::Missing)));

    // Empty value → Invalid: the carrier is PRESENT but malformed; it must
    // not be classified as an absent credential (contract §4: enabled
    // source blank/malformed credentials reject without fallback).
    let outcome = built
        .verifier
        .verify(&headers_with_cookie("bcs_session="))
        .await;
    assert!(
        matches!(outcome, Err(PrincipalVerificationError::Invalid)),
        "blank carrier must be Invalid (terminal), got {outcome:?}"
    );

    // Whitespace-only value → Invalid, same rule.
    let outcome = built
        .verifier
        .verify(&headers_with_cookie("bcs_session=   "))
        .await;
    assert!(matches!(outcome, Err(PrincipalVerificationError::Invalid)));

    // Value-less `bcs_session` (no `=`) → Invalid, same rule.
    let outcome = built
        .verifier
        .verify(&headers_with_cookie("bcs_session"))
        .await;
    assert!(matches!(outcome, Err(PrincipalVerificationError::Invalid)));

    // Duplicate cookie carriers → Invalid (ambiguous credential carrier).
    let mut headers = HeaderMap::new();
    headers.insert("cookie", HeaderValue::from_static("bcs_session=one"));
    headers.append("cookie", HeaderValue::from_static("bcs_session=two"));
    let outcome = built.verifier.verify(&headers).await;
    assert!(matches!(outcome, Err(PrincipalVerificationError::Invalid)));
    assert_eq!(
        identities.get_calls(),
        0,
        "ambiguous carriers must not reach the strict engine"
    );
}

// ---------------------------------------------------------------------------
// Review 2026-09-20 #1 regression: a blank/malformed OAuth cookie carrier is
// a TERMINAL Invalid for the chain — a later (or any) Gateway source must
// not rescue the request. Without this classification, `Cookie: bcs_session=`
// degraded to Missing and `chain = ["github", "gateway"]` accepted the
// request on the Gateway credential.
// ---------------------------------------------------------------------------

/// Mint a valid Gateway-signed Human principal token (same claim shape as
/// api_auth_registration_gateway.rs / the shared api-contract fixture).
fn mint_gateway_human_token_for_chain_test(signing_material: &str) -> String {
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string()); // default api.auth.gateway.key_id
    encode(
        &header,
        &serde_json::json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now - 1,
            "exp": now + 60,
            "principals": [{
                "type": "user",
                "tenant": null,
                "subject": {
                    "id": "user-chain",
                    "username": "alice",
                    "display_name": "Alice",
                    "full_name": null,
                    "tenant_id": null
                }
            }]
        }),
        &EncodingKey::from_secret(signing_material.as_bytes()),
    )
    .expect("gateway test token signs")
}

#[tokio::test]
async fn malformed_oauth_cookie_is_terminal_no_gateway_fallback() {
    let (identities, engine) = oauth_harness().await;
    let auth = auth_from_toml(
        r#"chain = ["github", "gateway"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["https://workbench.example.com"]

[github]
client_id = "gh-id"
client_secret_secret = "gh-client-secret"

[gateway]
signing_key_secret = "gw-signing-key"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gh-client-secret", "gh-secret-value"),
        ("gw-signing-key", "chain-test-gateway-signing-material"),
    ]));
    let built = build_api_auth(oauth_inputs(auth, secrets.clone(), engine.clone(), identities.clone()))
        .await
        .expect("mixed chain builds");
    assert_eq!(
        built.sources,
        vec!["github".to_string(), "gateway".to_string()],
        "mixed chain order is config order"
    );

    let gateway_token =
        mint_gateway_human_token_for_chain_test("chain-test-gateway-signing-material");
    // Sanity: WITHOUT the blank cookie the Gateway credential alone passes.
    let mut clean = HeaderMap::new();
    clean.insert(
        "x-avernet-principal",
        HeaderValue::from_str(&gateway_token).expect("header value"),
    );
    let outcome = built.verifier.verify(&clean).await;
    assert!(
        matches!(&outcome, Ok(identity) if identity.caller.user.is_some()),
        "gateway-only credential must authenticate through the mixed chain, got {outcome:?}"
    );

    // A present malformed cookie cannot be rescued by a valid Gateway token,
    // even when the Cookie header cannot be converted to an ASCII string.
    for raw in [
        b"bcs_session=".as_slice(),
        b"bcs_session=   ".as_slice(),
        b"bcs_session".as_slice(),
        b"bcs_session=\x80".as_slice(),
        b"other=\xff; bcs_session=invalid".as_slice(),
    ] {
        let mut headers = clean.clone();
        headers.insert("cookie", HeaderValue::from_bytes(raw).expect("header value"));
        let outcome = built.verifier.verify(&headers).await;
        assert!(
            matches!(outcome, Err(PrincipalVerificationError::Invalid)),
            "malformed cookie must terminate the chain as Invalid, got {outcome:?}"
        );
    }
    assert_eq!(
        identities.get_calls(),
        0,
        "the malformed carrier must never reach the strict engine"
    );
}
