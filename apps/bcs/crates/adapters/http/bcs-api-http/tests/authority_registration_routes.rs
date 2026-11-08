//! Task 4.1a — OpenAPI register surface × token version
//! contract matrix (spec §10.1), each cell over the REAL
//! trusted chain (SQL store) with one corresponding
//! lifecycle fault.
//!
//! The OpenAPI route keeps its OWN strict query parsing
//! (unknown enum → 400, scoped fields rejected for a v1
//! token by the facade contract) and its 201 envelope
//! with `no-store`; responses always come from the same
//! committed facade as the legacy surface.

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use bcs_domain::provider_registration_token::ProviderRegistrationMode;
use bcs_domain::{register_token_encode, RegisterTokenPayload};
use bcs_service_api::port::repo::{BotRepoPort, PermissionProfileRepoPort};
use bcs_test_support::contract::bot_registration_chain::{
    assert_no_half_registration, mint_v1_token, mint_v2_token,
    registration_chain, RegistrationChain, OWNER, REGISTER_SECRET,
};
use tower::ServiceExt;

// The register_support fixture reproduces the production
// ApiState wiring (real router) with the given service.
#[path = "register_support/mod.rs"]
mod support;

const REGISTER: &str = "/openapi/v1/collaboration/register";
const SECRET: &[u8] = REGISTER_SECRET;

fn chain_router(chain: &RegistrationChain) -> axum::Router {
    support::test_router(chain.register_service.clone())
}

async fn body_json(response: axum::response::Response) -> serde_json::Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body");
    serde_json::from_slice(&bytes).expect("json body")
}

fn expired_v1(staff: &str) -> String {
    register_token_encode(
        &RegisterTokenPayload {
            v: 1,
            id: format!("human_{}", staff),
            exp: 1,
        },
        SECRET,
    )
}

fn forged_v2(staff: &str) -> String {
    bcs_domain::provider_registration_token::encode(
        &bcs_domain::provider_registration_token::ProviderRegisterTokenPayload {
            v: 2,
            purpose: "provider_bot_registration".to_string(),
            id: format!("human_{}", staff),
            provider_id: "provider-a".to_string(),
            allowed_modes: vec![ProviderRegistrationMode::Plugin],
            exp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0)
                + 600,
            auth_mode: None,
        },
        b"wrong-secret",
    )
}

/// Cell legacy-v1-equivalent on the OpenAPI surface: a
/// v1 token registers through the real chain (201
/// envelope). incl. name alias & trim & bounds.
#[tokio::test]
async fn openapi_v1_registration_commits_through_the_real_chain() {
    let chain = registration_chain().await;
    let app = chain_router(&chain);
    for name in ["bot-name", "bot_name"] {
        let response = app
            .clone()
            .oneshot(Request::builder()
                .method("POST")
                .uri(format!(
                    "{REGISTER}?token={}&{name}=%20%E6%B5%8B%E8%AF%95",
                    uri_encode(&mint_v1_token(OWNER, SECRET))
                ))
                .body(Body::empty())
                .unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::CREATED);
        assert_eq!(response.headers()["cache-control"], "no-store");
        let body = body_json(response).await;
        assert_eq!(body["code"], 20100);
        assert_eq!(body["data"]["bot_name"], "测试");
        let bot_uuid = body["data"]["bot_uuid"].as_str().unwrap().to_string();
        // The committed state is the real chain's: owner,
        // version, profile, token.
        assert!(
            chain.bot_repo.load_token(&bot_uuid).await.is_some()
        );
        assert!(
            !chain.bot_repo.is_connected(&bot_uuid).await,
            "openapi route must not activate runtime"
        );
        assert!(
            chain
                .profile_store
                .get_active_default(
                    &bot_uuid,
                    &bcs_config::resolve_env_str()
                )
                .await
                .is_some()
        );
    }
}

/// The same params (scoped fields) a v1 token carries are
/// REJECTED by the existing contract (strict query
/// parsing + the no-Provider-fields facade rule); the
/// unknown mode stays a parser 400.
#[tokio::test]
async fn openapi_v1_rejects_scoped_fields_strictly() {
    let chain = registration_chain().await;
    let app = chain_router(&chain);
    let token = uri_encode(&mint_v1_token(OWNER, SECRET));
    // Per the frozen matrix, a v1 token accepts only the
    // default / plugin modes, while ref/webhook and an
    // unparseable mode are strictly rejected.
    let rejected = [
        format!("{REGISTER}?token={token}&bot_name=Ok&provider_bot_ref=ref-1"),
        format!("{REGISTER}?token={token}&bot_name=Ok&webhook_url=https%3A%2F%2Fexample.test"),
        format!("{REGISTER}?token={token}&bot_name=Ok&mode=weird_unknown"),
        format!("{REGISTER}?token={token}&bot_name=Ok&mode=gateway"),
    ];
    for uri in &rejected {
        let response = app
            .clone()
            .oneshot(
                Request::builder().method("POST").uri(uri.as_str()).body(Body::empty()).unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        assert!(
            status == StatusCode::BAD_REQUEST,
            "expected 400 for {uri}, got {status}"
        );
    }
    assert_no_half_registration(&chain, "openapi-v1-reject").await;
    // The default/plugin shape still commits through the
    // real chain after the strict rejections.
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!(
                    "{REGISTER}?token={token}&bot_name=Ok&mode=plugin"
                ))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn legacy_v1_registration_rolls_back_on_profile_owner_audit_failure() {
    for (cell, pattern) in [
        ("profile", "permission_profiles"),
        ("owner", "edge_grants"),
        ("audit", "bot_authority_changes"),
    ] {
        let chain = registration_chain().await;
        let app = chain_router(&chain);
        chain.db.arm(pattern);
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!(
                        "{REGISTER}?token={}&bot_name=Rollback",
                        uri_encode(&mint_v1_token(OWNER, SECRET))
                    ))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(
            response.status(),
            StatusCode::INTERNAL_SERVER_ERROR,
            "cell {cell} must not be a success"
        );
        let body = body_json(response).await;
        // OpenAPI envelope error, no credential content, no
        // SQL text.
        let dumped = format!("{body}");
        assert!(body["data"].get("bot_token").is_none());
        assert!(!dumped.contains("injected"));
        assert_no_half_registration(&chain, "rollback").await;
        assert!(
            chain.bot_repo.list_active().await.is_empty(),
            "no half registration after cell {cell}"
        );
        chain.db.disarm_all();
    }
}

/// Cell legacy-v2: scoped registration succeeds and a
/// Provider/audit failure rolls back; the duplicate
/// Provider/ref conflicts.
#[tokio::test]
async fn openapi_v2_registration_rolls_back_and_conflicts() {
    let chain = registration_chain().await;
    let app = chain_router(&chain);
    // Fault cell: audit write failure.
    chain.db.arm("bot_authority_changes");
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!(
                    "{REGISTER}?token={}&bot_name=Scoped&provider_bot_ref=ref-1&mode=plugin",
                    uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
                ))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let body = body_json(response).await;
    assert!(body["data"].get("bot_token").is_none());
    assert_no_half_registration(&chain, "v2-rollback").await;
    chain.db.disarm_all();

    // Success cell: 201 envelope, provider scope echoed.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!(
                    "{REGISTER}?token={}&bot_name=Scoped&provider_bot_ref=ref-1&mode=plugin",
                    uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
                ))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
    assert_eq!(response.headers()["cache-control"], "no-store");
    let body = body_json(response).await;
    assert_eq!(body["data"]["registration"]["provider_id"], "provider-a");

    // Duplicate Provider/ref (tombstones included).
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!(
                    "{REGISTER}?token={}&bot_name=Scoped&provider_bot_ref=ref-1&mode=plugin",
                    uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
                ))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CONFLICT);
}

#[tokio::test]
async fn openapi_invalid_and_expired_tokens_reject_before_stores() {
    let chain = registration_chain().await;
    let app = chain_router(&chain);
    for token in [expired_v1(OWNER), forged_v2(OWNER)] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!(
                        "{REGISTER}?token={}&bot_name=Whatever",
                        uri_encode(&token)
                    ))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        let body = body_json(response).await;
        assert!(!body.to_string().contains(&token));
    }
    assert_no_half_registration(&chain, "tokens").await;
    assert!(chain.bot_repo.list_active().await.is_empty());
}

fn uri_encode(value: &str) -> String {
    // Register tokens are URL-safe base64 with no padding;
    // they never need percent-encoding, keep it explicit.
    value.replace('+', "%2B").replace('/', "%2F")
}
