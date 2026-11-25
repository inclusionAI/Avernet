//! Task 4.1a — legacy `POST /register` surface ×
//! token-version matrix (spec §10.1), each cell over the
//! REAL trusted chain (SQL store) with its lifecycle
//! fault.
//!
//! Legacy responses stay the flat 200 JSON with
//! `no-store`; the v1 branch adapts away the
//! historically ignored fields BEFORE the facade (they
//! are never parsed into the scoped parser); v2 keeps
//! its own scoped validation and error mapping.


use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
    Router,
};
use bcs_domain::{register_token_encode, RegisterTokenPayload};
use bcs_http::state::HttpAppState;
use bcs_services_container::Services;
use bcs_service_api::port::repo::{BotRepoPort, PermissionProfileRepoPort};
use bcs_test_support::contract::bot_registration_chain::{
    assert_no_half_registration,
    mint_v1_token, mint_v2_token, registration_chain,
    RegistrationChain, OWNER, REGISTER_SECRET,
};
use tower::ServiceExt;

const SECRET: &[u8] = REGISTER_SECRET;

fn legacy_router(chain: &RegistrationChain) -> Router {
    // Only the pieces the register routes consume; the
    // real production router else.
    use bcs_http::router::build_router;
    build_router(
        HttpAppState::new(Services::noop())
            .with_register_application(chain.register_service.clone())
            .with_invite_config(
                SECRET.to_vec(),
                6 * 3600,
                None,
                None,
                None,
            ),
    )
}

async fn post(
    app: &Router,
    query: &str,
) -> (StatusCode, serde_json::Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/register?{query}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let cache_control = response
        .headers()
        .get("cache-control")
        .map(|v| v.to_str().unwrap().to_string());
    let bytes =
        to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body: serde_json::Value =
        serde_json::from_slice(&bytes).expect("json body");
    let _ = cache_control;
    (status, body)
}

#[tokio::test]
async fn legacy_v1_registration_rolls_back_on_profile_owner_audit_failure() {
    for (cell, pattern) in [
        ("profile", "permission_profiles"),
        ("owner", "edge_grants"),
        ("audit", "bot_authority_changes"),
    ] {
        let chain = registration_chain().await;
        let app = legacy_router(&chain);
        chain.db.arm(pattern);
        let token = uri_encode(&mint_v1_token(OWNER, SECRET));
        let (status, body) = post(
            &app,
            &format!("token={token}&bot-name=Rollback"),
        )
        .await;
        assert_ne!(
            status,
            StatusCode::OK,
            "cell {cell} must not be a success"
        );
        // No success credential anywhere in the response.
        let dumped = format!("{body}");
        assert!(body.get("bot_token").is_none(), "{dumped}");
        assert!(!dumped.contains("injected"));
        // No half registration anywhere.
        assert_no_half_registration(&chain, "rollback").await;
        assert!(
            chain.bot_repo.list_active().await.is_empty(),
            "no half registration after cell {cell}"
        );
        chain.db.disarm_all();
    }
}

/// The v1 tolerance: mode/provider_bot_ref/webhook_url —
/// including a mode that CANNOT parse as the Provider
/// enum — are ignored; the request adapts to the facade
/// WITHOUT them.
#[tokio::test]
async fn legacy_v1_ignores_scoped_fields() {
    let chain = registration_chain().await;
    let app = legacy_router(&chain);
    let token = uri_encode(&mint_v1_token(OWNER, SECRET));
    let (status, body) = post(
        &app,
        &format!(
            "token={token}&bot-name=Ignored&mode=weird_unknown&\
             provider_bot_ref=ref-1&webhook_url=https%3A%2F%2Fexample.test"
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["bot_name"], "Ignored");
    assert!(body.get("bot_uuid").is_some());
    assert!(body.get("bot_token").is_some());
    // Committed through the real chain.
    let bot_uuid = body["bot_uuid"].as_str().unwrap().to_string();
    assert!(chain.bot_repo.load_token(&bot_uuid).await.is_some());
    assert!(!chain.bot_repo.is_connected(&bot_uuid).await);
    assert!(
        chain
            .profile_store
            .get_active_default(&bot_uuid, &bcs_config::resolve_env_str())
            .await
            .is_some()
    );
}

/// The v2 cell keeps scoped validation errors and
/// performs the same lifecycle fault.
#[tokio::test]
async fn legacy_v2_registration_scoped_and_fault() {
    let chain = registration_chain().await;
    let app = legacy_router(&chain);
    // Audit failure fault cell.
    chain.db.arm("bot_authority_changes");
    let (status, body) = post(
        &app,
        &format!(
            "token={}&bot-name=Scoped&mode=plugin&provider_bot_ref=ref-1",
            uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
        ),
    )
    .await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "{body}");
    assert!(body.get("bot_token").is_none());
    assert_no_half_registration(&chain, "v2-fault").await;
    chain.db.disarm_all();

    // Success: 200 flat JSON (scoped fields are NOT
    // echoed on the legacy surface).
    let (status, body) = post(
        &app,
        &format!(
            "token={}&bot-name=Scoped&mode=plugin&provider_bot_ref=ref-1",
            uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["bot_name"], "Scoped");
    assert!(body["bot_token"].as_str().unwrap().len() > 0);
}

/// Legacy v2 duplicate Provider/ref maps to the scoped
/// 409 without touching the committed winner.
#[tokio::test]
async fn legacy_v2_duplicate_ref_conflicts() {
    let chain = registration_chain().await;
    let app = legacy_router(&chain);
    let payload = |ref_value: &str| {
        format!(
            "token={}&bot-name=Scoped&mode=plugin&provider_bot_ref={ref_value}",
            uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
        )
    };
    let (status, _) = post(&app, &payload("ref-1")).await;
    assert_eq!(status, StatusCode::OK);
    let (status, body) = post(&app, &payload("ref-1")).await;
    assert_eq!(status, StatusCode::CONFLICT, "{body}");
    // Exactly the winner bot row exists (belt-and-braces
    // via the same database).
    let rows = bcs_db_api::DbPlugin::query(
        chain.db.as_ref(),
        bcs_db_api::DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM bcs_bots WHERE actor_kind = 'bot'",
            vec![],
        ),
    )
    .await
    .expect("bots");
    assert_eq!(
        bcs_db_api::db_get_column::<i64>(&rows[0], "c").unwrap(),
        1
    );
}

#[tokio::test]
async fn legacy_v1_name_alias_trim_and_bounds() {
    let chain = registration_chain().await;
    let app = legacy_router(&chain);
    let token = uri_encode(&mint_v1_token(OWNER, SECRET));
    // bot_name alias + trim.
    let (status, body) = post(
        &app,
        &format!("token={token}&bot_name=%20Aliased%20Bot%20"),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["bot_name"], "Aliased Bot");
    // Missing name → 400.
    let (status, _) = post(&app, &format!("token={token}")).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    // Too short.
    let (status, _) = post(&app, &format!("token={token}&bot-name=a")).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    // Too long (65 chars).
    let too_long = "x".repeat(65);
    let (status, _) = post(
        &app,
        &format!("token={token}&bot-name={too_long}"),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn legacy_invalid_and_expired_tokens_reject() {
    let chain = registration_chain().await;
    let app = legacy_router(&chain);
    for token in [
        "not-a-token".to_string(),
        expired_v1(),
        forged_signature(),
    ] {
        let encoded = uri_encode(&token);
        let (status, body) = post(
            &app,
            &format!("token={encoded}&bot-name=Whatever"),
        )
        .await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "{body}");
        assert!(!format!("{body}").contains(&token));
    }
    assert_no_half_registration(&chain, "legacy-tokens").await;
    assert!(chain.bot_repo.list_active().await.is_empty());
}

/// The 200-flat + no-store response protocol is
/// asserted on both token versions' success cells.
#[tokio::test]
async fn legacy_success_keeps_the_flat_no_store_protocol() {
    let chain = registration_chain().await;
    let app = legacy_router(&chain);
    for query in [
        format!(
            "token={}&bot-name=FlatV1",
            uri_encode(&mint_v1_token(OWNER, SECRET))
        ),
        format!(
            "token={}&bot-name=FlatV2&mode=plugin&provider_bot_ref=ref-9",
            uri_encode(&mint_v2_token(OWNER, "provider-a", SECRET))
        ),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/register?{query}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers()["cache-control"],
            "no-store"
        );
        let body: serde_json::Value =
            serde_json::from_slice(
                &to_bytes(response.into_body(), usize::MAX)
                    .await
                    .unwrap(),
            )
            .unwrap();
        assert!(body.get("error").is_none());
        assert!(body.get("code").is_none());
        assert!(body.get("bot_token").is_some());
    }
}

fn expired_v1() -> String {
    register_token_encode(
        &RegisterTokenPayload {
            v: 1,
            id: format!("human_{OWNER}"),
            exp: 1,
        },
        SECRET,
    )
}

fn forged_signature() -> String {
    register_token_encode(
        &RegisterTokenPayload {
            v: 1,
            id: format!("human_{OWNER}"),
            exp: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0)
                + 60,
        },
        b"foreign-secret",
    )
}

fn uri_encode(value: &str) -> String {
    // Register tokens are URL-safe base64 without padding.
    value.replace('+', "%2B").replace('/', "%2F")
}
