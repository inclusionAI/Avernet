//! Bot / invite-code route behavioral tests: write-side gate and invite-code
//! flows plus unknown-field rejections. Split from the former top-level
//! target (Task 7).

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_api_http::{ApiState, router};
use serde_json::{Value, json};
use tower::ServiceExt;

use super::fixtures::*;

#[tokio::test]
async fn invite_code_routes_forward_through_gate_and_validate_payloads() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService { allow_access: true, ..Default::default() });
    let app = invite_code_test_router(bot_service.clone(), invite_code_service.clone(), true);

    let claim = app
        .clone()
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("public claim response");
    assert_eq!(claim.status(), StatusCode::CREATED);
    assert_eq!(claim.headers()["cache-control"], "no-store");
    assert_eq!(response_json(claim).await["data"], json!({"invite_code": "ABC123"}));

    let bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "ABC123"}),
        ))
        .await
        .expect("bind response");
    assert_eq!(bind.status(), StatusCode::OK);
    assert_eq!(response_json(bind).await["data"], json!({"bound": true, "bound_at": 123}));

    let me = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/invite-codes/me",
            Value::Null,
        ))
        .await
        .expect("my binding response");
    assert_eq!(me.status(), StatusCode::OK);
    assert_eq!(response_json(me).await["data"], json!({"bound": true, "bound_at": 456}));

    let init = app
        .clone()
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 3}),
        ))
        .await
        .expect("init response");
    assert_eq!(init.status(), StatusCode::OK);
    assert_eq!(response_json(init).await["data"], json!({"codes": ["ABC123"]}));

    let gated_candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("gated candidates response");
    assert_eq!(gated_candidates.status(), StatusCode::OK);
    assert_eq!(response_json(gated_candidates).await["data"]["items"][0]["bot"]["bot_id"], "bot-1");

    let empty_bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "   "}),
        ))
        .await
        .expect("empty bind response");
    assert_eq!(empty_bind.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response_json(empty_bind).await["data"]["error_code"], "invalid_request");

    let zero_init = app
        .clone()
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 0}),
        ))
        .await
        .expect("zero init response");
    assert_eq!(zero_init.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response_json(zero_init).await["data"]["error_code"], "invalid_request");

    assert_eq!(
        invite_code_service
            .ensure_access_calls
            .lock()
            .expect("ensure access lock")
            .len(),
        1,
        "protected routes should run the invite-code gate exactly once"
    );
    assert_eq!(
        invite_code_service
            .claim_public_invite_code_calls
            .lock()
            .expect("claim public invite code lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .bind_invite_code_calls
            .lock()
            .expect("bind invite code lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .get_my_invite_code_binding_calls
            .lock()
            .expect("get my invite code binding lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .init_invite_codes_calls
            .lock()
            .expect("init invite codes lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service.init_invite_codes_calls.lock().unwrap()[0]
            .caller.user.as_ref().unwrap().id,
        "staff-1",
        "issuance must forward the verified caller to application authorization",
    );
    assert!(
        bot_service
            .candidates
            .lock()
            .expect("candidates lock")
            .is_some(),
        "protected routes should flow through the invite-code gate when access is granted"
    );
}

#[tokio::test]
async fn public_invite_code_claim_returns_too_many_requests_at_max_count() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService {
        claim_limit_reached: true,
        ..Default::default()
    });
    let response = invite_code_test_router(bot_service, invite_code_service, false)
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("claim limit response");

    assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invite_code_claim_limit_reached");
    assert_eq!(
        body["message"],
        "maximum public invite-code claim count has been reached"
    );
}

#[tokio::test]
async fn public_invite_code_claim_returns_not_found_when_disabled() {
    let invite_code_service = Arc::new(FakeInviteCodeService::default());
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_invite_code_service(invite_code_service.clone());
    let response = router(state)
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("disabled public claim response");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(invite_code_service
        .claim_public_invite_code_calls
        .lock()
        .expect("claim public invite code lock")
        .is_empty());
}

#[tokio::test]
async fn invite_code_routes_return_internal_when_the_service_is_missing() {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_invite_code_gate_enabled(true);

    let app = router(state);

    let bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "ABC123"}),
        ))
        .await
        .expect("bind without invite-code service");
    assert_eq!(bind.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(bind).await["data"]["error_code"], "internal_error");

    let me = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/invite-codes/me",
            Value::Null,
        ))
        .await
        .expect("get binding without invite-code service");
    assert_eq!(me.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(me).await["data"]["error_code"], "internal_error");

    let init = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 3}),
        ))
        .await
        .expect("init without invite-code service");
    assert_eq!(init.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(init).await["data"]["error_code"], "internal_error");
}

#[tokio::test]
async fn bot_routes_reject_unknown_request_fields_and_missing_principal() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());
    let unknown = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/collaboration/bots/bot-1",
            json!({"name": "Bot", "created_by": "forged"}),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(unknown.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown).await["data"]["error_code"],
        "invalid_request"
    );

    let unknown_search_query = app
        .clone()
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/bot-1/candidates/search?q=planning&limit=10",
            Value::Null,
        ))
        .await
        .expect("unknown search query response");
    assert_eq!(unknown_search_query.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown_search_query).await["data"]["error_code"],
        "invalid_request"
    );
    assert!(
        service
            .candidate_searches
            .lock()
            .expect("candidate searches lock")
            .is_empty()
    );

    let missing = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/collaboration/bots/bot-1")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing principal response");
    assert_eq!(missing.status(), StatusCode::UNAUTHORIZED);
}
