use std::time::{SystemTime, UNIX_EPOCH};

use bcs::BcsServer;
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::json;

mod helpers;

const TEST_GATEWAY_PRINCIPAL_SIGNING_KEY: &[u8] = b"test-only-gateway-principal-signing-key";

fn user_principal_token(signing_key: &[u8]) -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("current time is after the Unix epoch")
        .as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    encode(
        &header,
        &json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now.saturating_sub(1),
            "exp": now + 60,
            "principals": [{
                "type": "user",
                "tenant": "tenant-a",
                "subject": {
                    "id": "user-1",
                    "username": "user-1",
                    "tenant_id": "tenant-a"
                }
            }]
        }),
        &EncodingKey::from_secret(signing_key),
    )
    .expect("test principal token signs")
}

#[tokio::test]
async fn mounted_openapi_v1_routes_require_and_verify_gateway_principal() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let url = format!("http://{addr}/openapi/v1/collaboration/bots/mine");

    let missing = client
        .get(&url)
        .send()
        .await
        .expect("missing-principal request");
    assert_eq!(missing.status(), reqwest::StatusCode::UNAUTHORIZED);

    let invalid = client
        .get(&url)
        .header(
            "x-avernet-principal",
            user_principal_token(b"wrong-test-key"),
        )
        .send()
        .await
        .expect("invalid-principal request");
    assert_eq!(invalid.status(), reqwest::StatusCode::UNAUTHORIZED);

    let valid = client
        .get(&url)
        .header(
            "x-avernet-principal",
            user_principal_token(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY),
        )
        .send()
        .await
        .expect("valid-principal request");
    assert_eq!(valid.status(), reqwest::StatusCode::OK);
    let envelope: serde_json::Value = valid.json().await.expect("JSON envelope");
    assert_eq!(envelope["code"], 20_000);
    assert!(envelope["data"].is_object());

    let legacy_health = client
        .get(format!("http://{addr}/health"))
        .send()
        .await
        .expect("legacy health request");
    assert_eq!(legacy_health.status(), reqwest::StatusCode::OK);

    handle.abort();
}

#[tokio::test]
async fn mounted_session_file_routes_use_the_real_facade_and_public_token_boundary() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let protected_url =
        format!("http://{addr}/api/v1/collaboration/sessions/missing-session/files");

    let missing = client
        .get(&protected_url)
        .send()
        .await
        .expect("missing-principal request");
    assert_eq!(missing.status(), reqwest::StatusCode::UNAUTHORIZED);

    let valid = client
        .get(&protected_url)
        .header(
            "x-avernet-principal",
            user_principal_token(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY),
        )
        .send()
        .await
        .expect("valid-principal request");
    assert_eq!(valid.status(), reqwest::StatusCode::NOT_FOUND);
    let envelope: serde_json::Value = valid.json().await.expect("JSON envelope");
    assert_eq!(envelope["data"]["error_code"], "session_not_found");

    let public = client
        .get(format!(
            "http://{addr}/api/v1/collaboration/sessions/shared-file/content?token=invalid"
        ))
        .send()
        .await
        .expect("public shared-content request");
    assert_eq!(public.status(), reqwest::StatusCode::NOT_FOUND);
    let envelope: serde_json::Value = public.json().await.expect("JSON envelope");
    assert_eq!(envelope["data"]["error_code"], "shared_file_not_found");

    handle.abort();
}

#[tokio::test]
async fn mounted_session_token_route_authenticates_before_reaching_the_application() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let url = format!("http://{addr}/openapi/v1/collaboration/sessions/missing-session/token");

    let missing = client
        .post(&url)
        .send()
        .await
        .expect("missing-principal request");
    assert_eq!(missing.status(), reqwest::StatusCode::UNAUTHORIZED);

    let invalid = client
        .post(&url)
        .header(
            "x-avernet-principal",
            user_principal_token(b"wrong-test-key"),
        )
        .send()
        .await
        .expect("invalid-principal request");
    assert_eq!(invalid.status(), reqwest::StatusCode::UNAUTHORIZED);

    let valid = client
        .post(&url)
        .header(
            "x-avernet-principal",
            user_principal_token(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY),
        )
        .send()
        .await
        .expect("valid-principal request");
    assert_eq!(valid.status(), reqwest::StatusCode::NOT_FOUND);
    let envelope: serde_json::Value = valid.json().await.expect("JSON envelope");
    assert_eq!(envelope["data"]["error_code"], "session_not_found");

    handle.abort();
}

#[tokio::test]
async fn mounted_session_collection_routes_require_a_gateway_principal() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let url = format!("http://{addr}/openapi/v1/collaboration/sessions/missing-session/collect");

    let collect = client
        .post(&url)
        .json(&json!({"participant": "bot-1"}))
        .send()
        .await
        .expect("missing-principal collect request");
    assert_eq!(collect.status(), reqwest::StatusCode::UNAUTHORIZED);

    let uncollect = client
        .delete(format!("{url}?participant=bot-1"))
        .send()
        .await
        .expect("missing-principal uncollect request");
    assert_eq!(uncollect.status(), reqwest::StatusCode::UNAUTHORIZED);

    handle.abort();
}

#[tokio::test]
async fn mounted_message_websocket_route_verifies_token_and_preserves_legacy_websocket() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");

    let error = tokio_tungstenite::connect_async(format!(
        "ws://{addr}/openapi/v1/collaboration/messages/ws?token=invalid"
    ))
    .await
    .expect_err("invalid BCN token must fail before Upgrade");
    let tokio_tungstenite::tungstenite::Error::Http(response) = error else {
        panic!("expected HTTP rejection, got {error}")
    };
    assert_eq!(response.status(), reqwest::StatusCode::UNAUTHORIZED);

    let old_path_error = tokio_tungstenite::connect_async(format!(
        "ws://{addr}/openapi/v1/collaboration/group/ws?token=invalid"
    ))
    .await
    .expect_err("the old group WebSocket path must not remain mounted");
    let tokio_tungstenite::tungstenite::Error::Http(old_path_response) = old_path_error else {
        panic!("expected HTTP rejection for old path, got {old_path_error}")
    };
    assert_eq!(old_path_response.status(), reqwest::StatusCode::NOT_FOUND);

    let (_legacy, response) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws"))
        .await
        .expect("legacy Workbench WebSocket remains mounted");
    assert_eq!(response.status(), reqwest::StatusCode::SWITCHING_PROTOCOLS);

    handle.abort();
}

#[tokio::test]
async fn mounted_event_subscription_routes_return_explicitly_disabled_capability() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let response = reqwest::Client::new()
        .get(format!(
            "http://{addr}/openapi/v1/collaboration/event-subscriptions"
        ))
        .header(
            "x-avernet-principal",
            user_principal_token(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY),
        )
        .send()
        .await
        .expect("list subscriptions");

    assert_eq!(response.status(), reqwest::StatusCode::CONFLICT);
    let envelope: serde_json::Value = response.json().await.expect("JSON envelope");
    assert_eq!(envelope["data"]["error_code"], "eventing_disabled");

    handle.abort();
}

#[tokio::test]
async fn mounted_eventing_record_only_profile_uses_the_real_application_service() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    config.eventing.enabled = true;
    config.eventing.dispatcher_enabled = false;
    config.eventing.webhook.allow_http_loopback = true;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let principal = user_principal_token(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY);

    let list = client
        .get(format!(
            "http://{addr}/openapi/v1/collaboration/event-subscriptions"
        ))
        .header("x-avernet-principal", &principal)
        .send()
        .await
        .expect("list subscriptions");
    assert_eq!(list.status(), reqwest::StatusCode::OK);
    let envelope: serde_json::Value = list.json().await.expect("JSON envelope");
    assert_eq!(envelope["data"]["items"], json!([]));

    let create = client
        .post(format!(
            "http://{addr}/openapi/v1/collaboration/event-subscriptions"
        ))
        .header("x-avernet-principal", principal)
        .json(&json!({
            "name": "missing-group-test",
            "scope": {"type": "group", "id": "missing-group"},
            "event_filters": ["group.created"],
            "sink": {
                "type": "webhook",
                "url": "http://127.0.0.1:9/events"
            }
        }))
        .send()
        .await
        .expect("create subscription for missing scope");
    assert_eq!(create.status(), reqwest::StatusCode::NOT_FOUND);
    let envelope: serde_json::Value = create.json().await.expect("JSON envelope");
    assert_eq!(
        envelope["data"]["error_code"],
        "event_subscription_not_found"
    );

    handle.abort();
}

#[tokio::test]
async fn register_routes_have_the_expected_security_boundary() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();

    // GET /register/token sits on the protected router: missing or invalid
    // principal is rejected by the boundary before the handler runs.
    let missing = client
        .get(format!("http://{addr}/openapi/v1/collaboration/register/token"))
        .send()
        .await
        .expect("missing-principal request");
    assert_eq!(missing.status(), reqwest::StatusCode::UNAUTHORIZED);

    let valid = client
        .get(format!("http://{addr}/openapi/v1/collaboration/register/token"))
        .header(
            "x-avernet-principal",
            user_principal_token(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY),
        )
        .send()
        .await
        .expect("valid-principal request");
    assert_eq!(valid.status(), reqwest::StatusCode::OK);
    let body: serde_json::Value = valid.json().await.expect("token body");
    assert_eq!(body["code"], 20_000);
    assert!(body["data"]["token"].as_str().expect("token data").len() > 16);

    // POST /register sits on the public router: it must NOT be gated by the
    // principal boundary — an anonymous POST reaches the handler and gets a
    // 400 (missing params) instead of the boundary's 401.
    let anonymous = client
        .post(format!("http://{addr}/register"))
        .send()
        .await
        .expect("anonymous register request");
    assert_eq!(anonymous.status(), reqwest::StatusCode::BAD_REQUEST);
    let body: serde_json::Value = anonymous.json().await.expect("error body");
    assert_eq!(body["code"], 40_000);
    assert_eq!(
        body["data"]["error_code"], "invalid_request",
        "missing token/bot-name validation must produce invalid_request"
    );

    handle.abort();
}
