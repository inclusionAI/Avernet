use super::provider_registration::{core_errors, scoped_payload, scoped_service};
use super::*;
use axum::http::StatusCode;
use bcs_domain::provider_registration_token::{self, ProviderRegistrationMode};
use serde_json::json;
use tower::ServiceExt;

// Share only the router fixture, with the real application facade injected below.
#[path = "../../../../../adapters/http/bcs-api-http/tests/register_support/mod.rs"]
mod support;
use support::*;

const REGISTER: &str = "/openapi/v1/collaboration/register";

#[tokio::test]
async fn scoped_http_registration_uses_signed_owner_and_provider_anonymously() {
    for mode in ["", "&mode=plugin", "&mode=gateway"] {
        for webhook in ["", "&webhook_url=https%3A%2F%2Fexample.test%2Foverride"] {
            if mode != "&mode=gateway" && !webhook.is_empty() {
                continue;
            }
            let (svc, core, management, onboarding) = scoped_service();
            let app = test_router(Arc::new(svc));
            let response = app
                .clone()
                .oneshot(authenticated_request(
                    "GET",
                    &format!("{REGISTER}/token?provider_id=provider-a"),
                    json!({}),
                ))
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            assert_eq!(response.headers()["cache-control"], "no-store");
            let issued = response_json(response).await;
            assert_eq!(
                issued["data"]["registration"],
                json!({
                    "token_version": 2, "provider_id": "provider-a", "allowed_modes": ["plugin", "gateway"],
                })
            );
            let token = issued["data"]["token"].as_str().unwrap();
            let response = app.oneshot(bare_request("POST", &format!(
                "{REGISTER}?token={token}&bot_name=Test%20Bot&provider_bot_ref=ref-1{mode}{webhook}&owner=attacker&provider_id=attacker"
            ))).await.unwrap();
            assert_eq!(response.status(), StatusCode::CREATED);
            assert_eq!(response.headers()["cache-control"], "no-store");
            let body = response_json(response).await;
            assert_eq!(body["data"]["bot_token"], "runtime-token");
            assert_eq!(body["data"]["registration"]["provider_id"], "provider-a");
            assert_eq!(
                body["data"]["registration"]["mode"],
                if mode == "&mode=gateway" {
                    "gateway"
                } else {
                    "plugin"
                }
            );
            assert_eq!(
                body["data"]["registration"]["webhook_url"],
                if webhook.is_empty() {
                    json!(null)
                } else {
                    json!("https://example.test/override")
                }
            );
            assert_eq!(
                body["data"]["registration"]["effective_webhook_url"],
                if mode == "&mode=gateway" {
                    if webhook.is_empty() {
                        json!("https://example.test/default")
                    } else {
                        json!("https://example.test/override")
                    }
                } else {
                    json!(null)
                }
            );
            assert_eq!(body["data"].as_object().unwrap().len(), 4);
            assert_eq!(body["data"]["registration"].as_object().unwrap().len(), 5);
            assert!(!body.to_string().contains(token));
            assert!(!body.to_string().contains("attacker"));
            assert!(!body.to_string().contains("completed"));
            let commands = core.registered.lock().unwrap();
            assert_eq!(commands[0].owner, "staff-1");
            assert_eq!(commands[0].provider_id, "provider-a");
            assert!(management.connected.lock().unwrap().is_empty());
            assert!(onboarding.onboarded.lock().unwrap().is_empty());
        }
    }
}

#[tokio::test]
async fn legacy_http_exact_shape_and_aliases_remain_anonymous() {
    for name in ["bot-name", "bot_name"] {
        let (svc, core, management, _) = scoped_service();
        let app = test_router(Arc::new(svc));
        let response = app
            .clone()
            .oneshot(authenticated_request(
                "GET",
                &format!("{REGISTER}/token"),
                json!({}),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let issued = response_json(response).await;
        assert_eq!(issued["data"].as_object().unwrap().len(), 3);
        let token = issued["data"]["token"].as_str().unwrap();
        assert_eq!(
            register_token_decode_and_verify(token, SECRET).unwrap().v,
            1
        );
        let response = app
            .oneshot(bare_request(
                "POST",
                &format!("{REGISTER}?token={token}&{name}=Test%20Bot"),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::CREATED);
        assert_eq!(response.headers()["cache-control"], "no-store");
        assert_eq!(
            response_json(response).await,
            json!({
                "code": 20100, "message": "Created", "request_id": "request-123",
                "data": { "bot_name": "Test Bot", "bot_uuid": "bot-1", "bot_token": "bot-token-1" },
            })
        );
        assert_eq!(management.connected.lock().unwrap().len(), 1);
        assert!(core.authorized.lock().unwrap().is_empty());
        assert!(core.registered.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn core_reauthorization_and_persistence_errors_reach_http_without_details() {
    for redeem in [false, true] {
        for (error, code) in core_errors() {
            let (svc, core, management, _) = scoped_service();
            let app = test_router(Arc::new(svc));
            let response = if redeem {
                // Issue first, then simulate a policy/provider/persistence change at redemption.
                let issued = app
                    .clone()
                    .oneshot(authenticated_request(
                        "GET",
                        &format!("{REGISTER}/token?provider_id=provider-a"),
                        json!({}),
                    ))
                    .await
                    .unwrap();
                let issued = response_json(issued).await;
                let token = issued["data"]["token"].as_str().unwrap();
                *core.register_error.lock().unwrap() = Some(error);
                app.oneshot(bare_request(
                    "POST",
                    &format!("{REGISTER}?token={token}&bot_name=Test&provider_bot_ref=ref-1"),
                ))
                .await
                .unwrap()
            } else {
                *core.issue_error.lock().unwrap() = Some(error);
                app.oneshot(authenticated_request(
                    "GET",
                    &format!("{REGISTER}/token?provider_id=provider-a"),
                    json!({}),
                ))
                .await
                .unwrap()
            };
            assert_eq!(
                response.status(),
                match code {
                    "forbidden" => StatusCode::FORBIDDEN,
                    "provider_not_found" => StatusCode::NOT_FOUND,
                    "registration_conflict" => StatusCode::CONFLICT,
                    "invalid_request" => StatusCode::BAD_REQUEST,
                    "internal_error" => StatusCode::INTERNAL_SERVER_ERROR,
                    _ => panic!("unexpected code"),
                }
            );
            let body = response_json(response).await;
            assert_eq!(body["data"]["error_code"], code);
            assert!(!body.to_string().contains("secret"));
            assert!(body["data"].get("token").is_none());
            assert!(body["data"].get("bot_token").is_none());
            assert!(management.connected.lock().unwrap().is_empty());
        }
    }
}

#[tokio::test]
async fn signed_scope_and_expiry_fail_before_core_in_http() {
    let (svc, core, management, _) = scoped_service();
    let app = test_router(Arc::new(svc));
    let expired = ProviderRegisterTokenPayload {
        exp: 0,
        ..scoped_payload()
    };
    let upstream = ProviderRegisterTokenPayload {
        allowed_modes: vec![ProviderRegistrationMode::Plugin],
        ..scoped_payload()
    };
    for (token, status) in [
        (
            provider_registration_token::encode(&expired, SECRET),
            StatusCode::UNAUTHORIZED,
        ),
        (
            provider_registration_token::encode(&scoped_payload(), b"wrong"),
            StatusCode::UNAUTHORIZED,
        ),
        (
            provider_registration_token::encode(&upstream, SECRET),
            StatusCode::FORBIDDEN,
        ),
    ] {
        let response = app
            .clone()
            .oneshot(bare_request(
                "POST",
                &format!(
                    "{REGISTER}?token={token}&bot_name=Test&provider_bot_ref=ref-1&mode=gateway"
                ),
            ))
            .await
            .unwrap();
        assert_eq!(response.status(), status);
        let body = response_json(response).await;
        assert!(!body.to_string().contains(&token));
    }
    assert!(core.registered.lock().unwrap().is_empty());
    assert!(management.connected.lock().unwrap().is_empty());
}
