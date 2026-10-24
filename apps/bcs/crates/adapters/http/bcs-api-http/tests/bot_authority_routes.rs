//! Task 5 route suite: the unified `/api/v1/collaboration/`
//! authority surface (spec §5.1/§5.2/§6.1, §6.4) through the
//! REAL `bcs_api_http` router with REAL Gateway signature
//! verification (`GatewayPrincipalTokenVerifier`).
//!
//! Covers: every route requires a verified signature (401
//! expired/tampered/wrong-audience), dual admission on the
//! single transfer GET/accept/reject handlers, the `sync`
//! user-id trap (a legal user id `sync` is a manager edit,
//! never a collection replace), owner-target 409,
//! invite-code regression on Human paths without App
//! exemption on normal routes, pagination bounds, transfer
//! conflict mappings, and byte-exact user-id casing at the
//! HTTP boundary.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, HeaderValue, Request, StatusCode};
use axum::response::Response;
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::{Value, json};
use tower::ServiceExt;

use bcs_api_http::{
    ApiState, PrincipalVerificationError, PrincipalVerifier, router,
};
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, RegisterService,
};
// The Noop service impls live in register_support; the
// trait items they implement are referenced there.

const ENV: &str = "local";
const APP_ID: i64 = 7;
const TENANT: &str = "tenant-a";

// The exact header the gateway principal verifier reads.
const PRINCIPAL_HEADER: &str = "x-avernet-principal";

// ---------------------------------------------------------------------------
// Real gateway signature material (HS256 JWTs on the shared contract shape)
// ---------------------------------------------------------------------------

const TEST_KEY: &[u8] = b"TEST-ONLY-bcs-principal-contract-key-32-bytes";

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs()
}

fn mint_with(key: &[u8], claims: Value) -> String {
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    encode(&header, &claims, &EncodingKey::from_secret(key)).expect("token signs")
}

fn claims_for(principals: Value) -> Value {
    let now = now_secs();
    json!({
        "iss": "gateway",
        "aud": "bcs",
        "iat": now,
        "exp": now + 300,
        "principals": principals,
    })
}

fn user_principal(id: &str, tenant: Option<&str>) -> Value {
    json!({
        "type": "user",
        "tenant": tenant,
        "subject": {
            "id": id,
            "username": id,
            "display_name": Value::Null,
            "full_name": Value::Null,
            "tenant_id": Value::Null,
        },
    })
}

fn app_principal(app_id: i64, tenant: Option<&str>) -> Value {
    json!({
        "type": "app",
        "tenant": tenant,
        "app": {
            "app_id": app_id,
            "app_name": bcs_test_support::AUTHORITY_FIXTURE_APP_NAME,
            "owners": "owner",
            "tenant": tenant,
            "app_type": "THIRD_PARTY",
        },
    })
}

struct GatewaySignatureVerifier {
    verifier: bcs_api_http::v1::gateway_principal::GatewayPrincipalTokenVerifier,
}

#[async_trait]
impl PrincipalVerifier for GatewaySignatureVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        let token = headers
            .get(PRINCIPAL_HEADER)
            .and_then(|value| value.to_str().ok())
            .ok_or(PrincipalVerificationError::Missing)?;
        self.verifier
            .verify(token)
            .map_err(|error| PrincipalVerificationError::Invalid(error.to_string()))
    }
}

fn gateway_verifier(
) -> Arc<GatewaySignatureVerifier> {
    let trust = bcs_api_http::v1::gateway_principal::GatewayPrincipalTrust::new(
        vec!["gateway".to_string()],
        "bcs".to_string(),
        "bare".to_string(),
    )
    .expect("trust config");
    Arc::new(GatewaySignatureVerifier {
        verifier: bcs_api_http::v1::gateway_principal::GatewayPrincipalTokenVerifier::new(
            TEST_KEY,
            trust,
        )
        .expect("verifier"),
    })
}

// ---------------------------------------------------------------------------
// conversation payloads for the verifier
// ---------------------------------------------------------------------------

fn human_token(user_id: &str) -> String {
    mint_with(
        TEST_KEY,
        claims_for(json!([user_principal(user_id, Some(TENANT))])),
    )
}

fn app_token(app_id: i64, tenant: Option<&str>) -> String {
    mint_with(
        TEST_KEY,
        claims_for(json!([app_principal(app_id, tenant)])),
    )
}

fn expired_human_token(user_id: &str) -> String {
    let now = now_secs();
    mint_with(
        TEST_KEY,
        json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now - 7200,
            "exp": now - 3600,
            "principals": [user_principal(user_id, Some(TENANT))],
        }),
    )
}

fn tampered_human_token(user_id: &str) -> String {
    let token = human_token(user_id);
    let mut parts: Vec<String> = token
        .split('.')
        .map(str::to_string)
        .collect();
    // Flip one payload char while keeping the JWT shape so
    // the signature no longer matches.
    let payload = parts.get_mut(1).expect("payload part");
    let first = payload
        .chars()
        .next()
        .expect("non-empty payload");
    let flipped = if first == 'e' {
        format!("f{}", &payload[1..])
    } else {
        format!("e{}", &payload[1..])
    };
    *payload = flipped;
    parts.join(".")
}

fn wrong_audience_human_token(user_id: &str) -> String {
    let now = now_secs();
    mint_with(
        TEST_KEY,
        json!({
            "iss": "gateway",
            "aud": "not-bcs",
            "iat": now,
            "exp": now + 300,
            "principals": [user_principal(user_id, Some(TENANT))],
        }),
    )
}

// ---------------------------------------------------------------------------
// request helpers
const OWNER: &str = "alice";
const MANAGER: &str = "bob";
const STRANGER: &str = "carol";
const TARGET_HUMAN: &str = "bob";
const TARGET_ACTOR: &str = "human_bob";
const ANOTHER_ACTOR: &str = "human_carol";

const BOT_SYNC: &str = "bot_sync";
const BOT_TRANSFER: &str = "bot_transfer";

// ---------------------------------------------------------------------------
// shared authority fixture (bcs-test-support keeps the
// concrete-crate wiring so this adapter test keeps
// only bcs-service-api types)
// ---------------------------------------------------------------------------

#[allow(dead_code)]
mod register_support;
use register_support::{
    NoopFriendshipService, NoopGroupService, NoopInvitationService,
    NoopSessionMessageService, NoopSessionService,
};
use bcs_test_support::bot_authority::{
    InviteGateStub, authority_service_fixture, seed_bot, seed_humans,
    seed_transfer_row,
};

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: bcs_service_api::application::v1::IssueRegisterToken,
    ) -> Result<bcs_service_api::application::v1::RegisterTokenView, ApplicationError>
    {
        Err(ApplicationError::internal("register not configured"))
    }

    async fn register_bot(
        &self,
        _command: bcs_service_api::application::v1::RegisterBot,
    ) -> Result<bcs_service_api::application::v1::BotRegistration, ApplicationError>
    {
        Err(ApplicationError::internal("register not configured"))
    }
}


// ---------------------------------------------------------------------------
// conversation payloads + request helpers
// ---------------------------------------------------------------------------




fn signed_request(method: &str, uri: &str, token: Option<&str>) -> Request<Body> {
    let mut builder = Request::builder()
        .method(method)
        .uri(uri)
        .header("x-request-id", "request-123");
    if let Some(token) = token {
        builder = builder.header(
            PRINCIPAL_HEADER,
            HeaderValue::from_str(token).expect("header value"),
        );
    }
    builder.body(Body::empty()).expect("request")
}

fn signed_json_request(method: &str, uri: &str, token: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-request-id", "request-123")
        .header(PRINCIPAL_HEADER, token)
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    serde_json::from_slice(&bytes).expect("response json")
}

fn error_code(body: &Value) -> String {
    body["data"]["error_code"]
        .as_str()
        .unwrap_or_default()
        .to_string()
}
fn dual_token(user_id: &str, app_id: i64) -> String {
    // Both the Human and the App principal in one
    // verified principal set (the gateway may project
    // both identities on one credential).
    mint_with(
        TEST_KEY,
        claims_for(json!([
            user_principal(user_id, Some(TENANT)),
            app_principal(app_id, Some(TENANT)),
        ])),
    )
}

struct AuthFixture {
    router: axum::Router,
    invite: Arc<InviteGateStub>,
}

async fn build_state(invite_allow: bool) -> (AuthFixture, Arc<MemoryBotRepo>) {
    let fixture = authority_service_fixture(invite_allow).await;
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        gateway_verifier(),
    )
    .with_bot_authority_service(fixture.service)
    .with_invite_code_service(fixture.invite.clone())
    .with_invite_code_gate_enabled(true);
    (
        AuthFixture {
            router: router(state),
            invite: fixture.invite,
        },
        fixture.bot_repo,
    )
}



// ---------------------------------------------------------------------------
// 5.5/5.6 route cases
// ---------------------------------------------------------------------------

#[tokio::test]
async fn every_authority_route_requires_a_verified_signature() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;
    let token = human_token(OWNER);

    // Without a principal header: 401 on a sampling of the
    // new method/path combos.
    let cases = [
        ("GET", "/api/v1/collaboration/bots/bot_sync/managers", None),
        (
            "PUT",
            "/api/v1/collaboration/bots/bot_sync/managers",
            None,
        ),
        (
            "PUT",
            "/api/v1/collaboration/bots/bot_sync/managers/bob",
            None,
        ),
        (
            "DELETE",
            "/api/v1/collaboration/bots/bot_sync/managers/bob",
            None,
        ),
        (
            "GET",
            "/api/v1/collaboration/bots/bot_sync/ownership",
            None,
        ),
        (
            "PUT",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}"),
            None,
        ),
        (
            "GET",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}"),
            None,
        ),
        (
            "POST",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}/accept"),
            None,
        ),
        (
            "POST",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}/reject"),
            None,
        ),
        (
            "POST",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}/cancel"),
            None,
        ),
        (
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=received",
            None,
        ),
        // With a valid signature the same requests pass the
        // verification boundary (identity decisions then apply).
        ("GET", "/api/v1/collaboration/bots/bot_sync/managers", Some(token.as_str())),
    ];
    for (method, uri, token) in cases {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_request(method, uri, token))
            .await
            .expect("response");
        let status = response.status();
        if token.is_none() {
            assert_eq!(
                status,
                StatusCode::UNAUTHORIZED,
                "{method} {uri} without a principal must be 401"
            );
        } else if !status.is_success() {
            panic!("{method} {uri} with a valid signature reached {status}");
        }
    }
}

#[tokio::test]
async fn expired_tampered_and_wrong_audience_signatures_are_unauthenticated() {
    let (fixture, _) = build_state(true).await;
    let cases = [
        (
            "expired",
            expired_human_token(OWNER),
        ),
        (
            "tampered",
            tampered_human_token(OWNER),
        ),
        (
            "wrong-audience",
            wrong_audience_human_token(OWNER),
        ),
    ];
    for (label, token) in cases {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_request(
                "GET",
                "/api/v1/collaboration/bots/bot_sync/managers",
                Some(&token),
            ))
            .await
            .expect("response");
        assert_eq!(
            response.status(),
            StatusCode::UNAUTHORIZED,
            "{label} signature must stay unauthenticated"
        );
        let body = response_json(response).await;
        assert_eq!(error_code(&body), "unauthenticated");
    }
}

#[tokio::test]
async fn sync_body_requires_the_field_and_rejects_null() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[MANAGER]).await;
    let token = app_token(APP_ID, Some(TENANT));

    for body in [json!({}), json!({"manager_user_ids": null})] {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_json_request(
                "PUT",
                &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
                &token,
                body,
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(error_code(&response_json(response).await), "invalid_request");
    }

    // The manager collection is untouched.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(
        response_json(response).await["data"]["items"],
        Value::Array(vec![Value::String(MANAGER.to_string())])
    );
}

#[tokio::test]
async fn human_owner_lists_managers_with_the_owner_summary() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[MANAGER]).await;
    let token = human_token(OWNER);

    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["owner"]["owner_user_id"], OWNER);
    assert_eq!(
        body["data"]["owner"]["owner_actor_id"],
        format!("human_{OWNER}")
    );
    assert_eq!(body["data"]["items"], Value::Array(vec![MANAGER.into()]));
    assert_eq!(body["data"]["total"], 1);

    // A Human with no relation is denied (403), never leaked the page.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(STRANGER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn app_sync_replaces_the_manager_collection() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &["legacy_mgr"]).await;
    seed_humans(&repo, &[MANAGER, "dave"]).await;
    let token = app_token(APP_ID, Some(TENANT));

    let response = fixture
        .router
        .clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &token,
            json!({"manager_user_ids": [MANAGER, MANAGER, "dave".to_string()]}),
        ))
        .await
        .expect("response");
    assert_eq!(
        response.status(),
        StatusCode::OK,
        "sync through the collection PUT"
    );
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 2);
    assert_eq!(body["data"]["removed"], 1);
    assert_eq!(body["data"]["total"], 2);

    // sync never removes the owner, only managers: the owner
    // still reads the final page.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["owner"]["owner_user_id"], OWNER);
    let items = body["data"]["items"].as_array().expect("items");
    assert_eq!(items.len(), 2);

    // Re-submitting the same snapshot reports a zero diff.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &token,
            json!({"manager_user_ids": [MANAGER, "dave".to_string()]}),
        ))
        .await
        .expect("response");
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 0);
    assert_eq!(body["data"]["removed"], 0);
}

#[tokio::test]
async fn human_caller_is_rejected_from_the_collection_replace() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;

    let response = fixture
        .router
        .clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &human_token(OWNER),
            json!({"manager_user_ids": [MANAGER]}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(
        error_code(&body),
        "bot_authority_operator_forbidden"
    );
}

#[tokio::test]
async fn put_single_user_id_sync_adds_that_user_only() {
    // The plan-pinned trap: a legal user id `sync` must be a
    // single-item add, never a collection replace, because the
    // surface deliberately has no /managers/sync sub-path.
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &["existing_mgr"]).await;
    seed_humans(&repo, &["sync"]).await;
    let token = human_token(OWNER);

    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/sync"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 1);
    assert_eq!(body["data"]["removed"], 0);

    // The pre-existing manager survived; the user id `sync` is
    // now a manager; the owner is untouched.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&token),
        ))
        .await
        .expect("response");
    let body = response_json(response).await;
    let mut items = body["data"]["items"]
        .as_array()
        .expect("items")
        .iter()
        .map(|v| v.as_str().expect("string").to_string())
        .collect::<Vec<_>>();
    items.sort();
    assert_eq!(items, vec!["existing_mgr", "sync"]);
    assert_eq!(body["data"]["owner"]["owner_user_id"], OWNER);
}

#[tokio::test]
async fn manager_single_add_remove_round_trip_and_owner_target_409() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[MANAGER]).await;
    let token = human_token(OWNER);

    // Adding the owner through the manager endpoint: 409
    // owner_not_manager (spec §5.1).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{OWNER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(error_code(&body), "owner_not_manager");

    // The manager can manage managers too; grant and revoke.
    // Unknown Human targets fail the whole request (400, no
    // partial write).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(
        response.status(),
        StatusCode::OK,
        "re-adding an existing manager succeeds"
    );

    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "DELETE",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // Removing an absent manager is a success (spec §5.1).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "DELETE",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn single_item_edits_are_human_only() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;
    let app = app_token(APP_ID, Some(TENANT));

    for method in ["PUT", "DELETE"] {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_request(
                method,
                &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
                Some(&app),
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }
}

#[tokio::test]
async fn owner_checks_and_unauthorized_humans_on_dual_read() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, TARGET_HUMAN).await;

    // The current-round initiator may read (dual admission,
    // Human path).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "pending");
    assert_eq!(body["data"]["target_owner_actor_id"], TARGET_ACTOR);
    assert_eq!(body["data"]["from_owner_actor_id"], format!("human_{OWNER}"));

    // The target Human may read.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&human_token(TARGET_HUMAN)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // An unrelated Human is denied.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&human_token("someone-else")),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // A scoped app reads via the SAME handler.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // No configured target set anymore: a scoped App reaches
    // the handler for any pending row — only existence divides
    // read access (missing pair = 404, spec §6.1/§6.4).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{ANOTHER_ACTOR}"),
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);

    // Unknown (bot, target) pairs are 404 with the pinned code.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{ANOTHER_ACTOR}"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(error_code(&body), "bot_transfer_not_found");
}

#[tokio::test]
async fn initiation_and_decision_flows_through_http() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[TARGET_HUMAN, STRANGER]).await;
    let owner = human_token(OWNER);
    let target = human_token(TARGET_HUMAN);
    let app = app_token(APP_ID, Some(TENANT));
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");

    // The current owner initiates: 201.
    let response = fixture.router.clone()
        .oneshot(signed_request("PUT", &base, Some(&owner)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CREATED, "new row");
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "pending");

    // Repeat from the same owner: refresh/reuse returns 200.
    let response = fixture.router.clone()
        .oneshot(signed_request("PUT", &base, Some(&owner)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK, "reuse/refresh returns 200");

    // A different target's pending blocks a new initiation: 409
    // transfer_pending (seed a second row is NOT possible; the
    // unique-pending rule enforces it — attempt another target).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{ANOTHER_ACTOR}"),
            Some(&owner),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(error_code(&body), "transfer_pending");

    // Non-target Human cannot accept.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&human_token(STRANGER))))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Initiation is Human-only: the App scope has no initiate
    // action even for scoped targets.
    let response = fixture.router.clone()
        .oneshot(signed_request("PUT", &base, Some(&app)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Target Human accepts: 200; the row shows accepted and the
    // owner flipped.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&target)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "accepted");
    assert_eq!(body["data"]["decided_by_kind"], "human");

    // Repeat accept on the same terminal row: 200 unchanged.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&target)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // A different decision on the terminal row: 409.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/reject"), Some(&target)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    assert_eq!(error_code(&response_json(response).await), "transfer_state_conflict");

    // Ownership flipped to the target; the old owner became a
    // manager (visible through the manager list).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership"),
            Some(&human_token(TARGET_HUMAN)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["owner_user_id"], TARGET_HUMAN);
}

#[tokio::test]
async fn scoped_app_accepts_and_the_decision_is_recorded_as_service() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    seed_humans(&repo, &[TARGET_HUMAN]).await;
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, TARGET_HUMAN).await;
    let app = app_token(APP_ID, Some(TENANT));
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");

    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&app)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "accepted");
    assert_eq!(body["data"]["decided_by_kind"], "service");

    // The app then reads the row it decided through the same
    // scoped GET.
    let response = fixture.router.clone()
        .oneshot(signed_request("GET", &base, Some(&app)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["status"], "accepted");
}

#[tokio::test]
async fn inbox_is_human_only_and_pagination_bounds_are_enforced() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    let app = app_token(APP_ID, Some(TENANT));

    // Inbox/outbox is a Human rule: the App scope has no action
    // for it.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=received",
            Some(&app),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // limit 0 and over-the-max are bad requests.
    for limit in ["0", "101"] {
        let response = fixture.router.clone()
            .oneshot(signed_request(
                "GET",
                &format!("/api/v1/collaboration/ownership-transfers?direction=received&limit={limit}"),
                Some(&human_token(OWNER)),
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "limit {limit}");
    }
    // An unknown status filter is a bad request.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=received&status=vibes",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    // An unknown direction is a bad request.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=sideways",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    // Wait — direction=sideways is caught by dto; the empty
    // inbox page still works for a Human.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=sent",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["items"], Value::Array(vec![]));
    assert_eq!(body["data"]["total"], 0);
}

/// The `status` filter grammar (bot-authority.yaml: an
/// OpenAPI array on `GET /ownership-transfers`) accepts
/// the documented REPEATED form plus a single value and
/// a comma-delimited list — a native `Query` extractor
/// rejected every legal form with "expected a
/// sequence", so the route parses the raw query.
#[tokio::test]
async fn transfer_status_filter_accepts_single_repeated_and_csv_forms() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    // One PENDING transfer row (expires now + 7d, so
    // the effective status is still `pending`).
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, "bob").await;

    let uri_for = |query: &str| {
        format!(
            "/api/v1/collaboration/ownership-transfers?direction=sent&{query}"
        )
    };

    // Single value: the pending row matches.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("status=pending"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("single status response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["items"][0]["status"], "pending");

    // A single VALID non-matching status narrows the
    // page to empty (and proves the filter applies).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("status=accepted"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("non-matching response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 0);
    assert_eq!(body["data"]["items"], Value::Array(vec![]));

    // Repeated form (`status=pending&status=accepted`,
    // the documented OpenAPI array) — previously a
    // HARD 400 "expected a sequence".
    for query in [
        "status=pending&status=cancelled",
        "status=accepted,pending",
        "status=pending,%20accepted",
        "status=accepted&status=pending,cancelled",
    ] {
        let response = fixture.router.clone()
            .oneshot(signed_request(
                "GET",
                &uri_for(query),
                Some(&human_token(OWNER)),
            ))
            .await
            .expect("repeated status response");
        assert_eq!(response.status(), StatusCode::OK, "{query}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["total"], 1, "{query}");
        assert_eq!(body["data"]["items"][0]["status"], "pending", "{query}");
    }

    // Every status value without an inviting row in
    // it filters to the empty page again (repeated).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("status=rejected&status=expired"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("no default");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 0);

    // No status → the unfiltered (default) page.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for(""),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("default response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 1);

    // Unknown status spelling stays a 400 with the
    // pinned `invalid_request` code.
    for query in [
        "status=vibes",
        "status=pending&status=vibes",
        "status=pending,",
    ] {
        let response = fixture.router.clone()
            .oneshot(signed_request(
                "GET",
                &uri_for(query),
                Some(&human_token(OWNER)),
            ))
            .await
            .expect("invalid status response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{query}");
        let body = response_json(response).await;
        assert_eq!(error_code(&body), "invalid_request", "{query}");
    }

    // Unknown query parameters keep the historical
    // deny (the former `deny_unknown_fields` shape).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("bogus=1"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("unknown parameter response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn invite_code_gate_stays_on_human_paths_without_app_exemption() {
    let (fixture, repo) = build_state(false).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;

    // An un-invite-coded Human on authority routes is rejected
    // by the Application-level gate (403 invite_code_required).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "invite_code_required"
    );
    assert_eq!(fixture.invite.human_checks.lock().expect("gate").len(), 1);

    // A trusted App on the SAME dual/sync routes is never
    // treated as an un-invite-coded Human.
    let response = fixture.router.clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &app_token(APP_ID, Some(TENANT)),
            json!({"manager_user_ids": []}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(fixture.invite.human_checks.lock().expect("gate").len(), 1);
}

#[tokio::test]
async fn existing_routes_gain_no_app_exemption() {
    let (fixture, _) = build_state(false).await;

    // A normal protected collaboration route stays Human-rule:
    // an App-only credential must not gain access through the
    // new protected-only layering.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/templates",
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    // The App-only credential does NOT gain the template
    // route: the normal protected router keeps its
    // always-Human gate and the route fails it closed.
    assert_eq!(
        response.status(),
        StatusCode::FORBIDDEN,
        "App callers must not gain the template route"
    );

    // The invite-gate middleware still runs on normal protected
    // routes: signature-verified Humans without invite codes are
    // rejected there (the middleware's own denial).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/templates",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "invite_code_required"
    );
}

#[tokio::test]
async fn user_id_casing_is_byte_exact_at_the_boundary() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;
    seed_humans(&repo, &[MANAGER]).await;
    let token = human_token(OWNER);

    // Only the exact-case user id grants/ungrants; the
    // wrong-case variant never touches "Bob".
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/Bob"),
            Some(&token),
        ))
        .await
        .expect("response");
    // "Bob" is unknown in the memory Human registry → the whole
    // request fails (400), never silently creating an identity.
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        error_code(&response_json(response).await),
        "bot_authority_human_unknown"
    );

    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&token),
        ))
        .await
        .expect("response");
    let items = response_json(response).await["data"]["items"].clone();
    assert_eq!(items, Value::Array(vec![Value::String(MANAGER.to_string())]));
    // The wrong-case subject was never added.
    assert!(!items.to_string().contains("Bob"), "{items}");
}


// ---------------------------------------------------------------------------
// plan Review Focus #1 pins (User+App simultaneous credentials)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn dual_credential_human_rejection_never_falls_back_to_app() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    seed_humans(&repo, &[TARGET_HUMAN]).await;
    let dual = dual_token(OWNER, APP_ID);

    // The current owner carries BOTH identities. On the
    // dual-admission accept route the Human is processed
    // FIRST: alice is not the target, the Human rule
    // rejects, and the App scope MUST NOT be consumed as a
    // fallback (the scope would accept).
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &base,
            Some(&dual),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&dual)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "bot_authority_operator_forbidden"
    );

    // The row is untouched: no App-side decision was
    // recorded anywhere.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &base,
            Some(&human_token(TARGET_HUMAN)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["status"], "pending");
    // A Human-only alice credential on the same accept
    // rejects identically (the dual projection never
    // matters to the outcome).
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&human_token(OWNER))))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // The same dual credential on the collection PUT IS
    // processed as the App identity explicitly (spec §6.1:
    // sync 始终显式选择 App 身份): the empty snapshot
    // commits as the App mutation, and a repeated
    // snapshot reports the zero diff.
    let response = fixture.router.clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/managers"),
            &dual,
            json!({"manager_user_ids": []}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["removed"], 0);
    assert_eq!(body["data"]["total"], 0);
    let response = fixture.router.clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/managers"),
            &dual,
            json!({"manager_user_ids": [TARGET_HUMAN]}),
        ))
        .await
        .expect("response");
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 1);
    assert_eq!(body["data"]["total"], 1);
}

#[tokio::test]
async fn un_invite_coded_dual_caller_cannot_accept() {
    let (fixture, repo) = build_state(false).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[TARGET_HUMAN]).await;
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, TARGET_HUMAN).await;
    let dual = dual_token(TARGET_HUMAN, APP_ID);
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");

    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&dual)))
        .await
        .expect("response");
    // The Human is processed first: the invite gate
    // rejects and there is no App fallback.
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "invite_code_required"
    );
    assert_eq!(fixture.invite.human_checks.lock().expect("gate").len(), 1);

    // Nothing was decided: the scoped App reads the
    // same row (the App branch is unaffected by the
    // Human invite gate).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &base,
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["status"], "pending");
}
