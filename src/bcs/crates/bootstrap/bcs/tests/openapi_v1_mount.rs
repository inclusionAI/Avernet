use std::time::{SystemTime, UNIX_EPOCH};

use bcs::BcsServer;
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::json;

mod helpers;

const DEVELOPMENT_SIGNING_KEY: &[u8] = b"avernet-dev-signing-key-NOT-FOR-PROD";

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

    let missing = client.get(&url).send().await.expect("missing-principal request");
    assert_eq!(missing.status(), reqwest::StatusCode::UNAUTHORIZED);

    let invalid = client
        .get(&url)
        .header("x-avernet-principal", user_principal_token(b"wrong-test-key"))
        .send()
        .await
        .expect("invalid-principal request");
    assert_eq!(invalid.status(), reqwest::StatusCode::UNAUTHORIZED);

    let valid = client
        .get(&url)
        .header(
            "x-avernet-principal",
            user_principal_token(DEVELOPMENT_SIGNING_KEY),
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
async fn mounted_session_token_route_authenticates_before_reaching_the_application() {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    let (addr, handle) = server.run_on_random_port().await.expect("start server");
    let client = reqwest::Client::new();
    let url = format!(
        "http://{addr}/openapi/v1/collaboration/sessions/missing-session/token"
    );

    let missing = client
        .post(&url)
        .send()
        .await
        .expect("missing-principal request");
    assert_eq!(missing.status(), reqwest::StatusCode::UNAUTHORIZED);

    let invalid = client
        .post(&url)
        .header("x-avernet-principal", user_principal_token(b"wrong-test-key"))
        .send()
        .await
        .expect("invalid-principal request");
    assert_eq!(invalid.status(), reqwest::StatusCode::UNAUTHORIZED);

    let valid = client
        .post(&url)
        .header(
            "x-avernet-principal",
            user_principal_token(DEVELOPMENT_SIGNING_KEY),
        )
        .send()
        .await
        .expect("valid-principal request");
    assert_eq!(valid.status(), reqwest::StatusCode::NOT_FOUND);
    let envelope: serde_json::Value = valid.json().await.expect("JSON envelope");
    assert_eq!(envelope["data"]["error_code"], "session_not_found");

    handle.abort();
}
