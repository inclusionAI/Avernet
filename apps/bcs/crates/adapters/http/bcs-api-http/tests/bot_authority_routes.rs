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

#[path = "bot_authority_routes/signatures_and_managers.rs"]
mod signatures_and_managers;

#[path = "bot_authority_routes/transfers_and_scope.rs"]
mod transfers_and_scope;
