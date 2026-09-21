//! Real bootstrap -> HTTP -> application -> core -> stores registration path.
mod helpers;
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use reqwest::StatusCode;
use serde_json::{Value, json};

fn principal(user: &str) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".into());
    header.kid = Some("bare".into());
    encode(
        &header,
        &json!({ "iss": "gateway", "aud": "bcs", "iat": now - 1, "exp": now + 120,
        "principals": [{ "type": "user", "tenant": "tenant-a",
            "subject": { "id": user, "username": user, "tenant_id": "tenant-a" } }] }),
        &EncodingKey::from_secret(b"test-only-gateway-principal-signing-key"),
    )
    .unwrap()
}

async fn data(request: reqwest::RequestBuilder, expected: StatusCode) -> Value {
    let response = request
        .send()
        .await
        .unwrap_or_else(|error| panic!("request failed: {}", error.without_url()));
    assert_eq!(response.status(), expected);
    assert_eq!(response.headers()["cache-control"], "no-store");
    response.json::<Value>().await.unwrap()["data"].clone()
}

#[tokio::test]
async fn scoped_register_mount_preserves_legacy_and_creates_both_transports() {
    use bcs_domain::bot_provider::DownlinkDetectionSource;
    for source in [DownlinkDetectionSource::Binding, DownlinkDetectionSource::BotConnectionMode] {
        tokio::time::timeout(std::time::Duration::from_secs(45), exercise_registration(source))
            .await
            .expect("registration integration exceeded its deadline");
    }
}

async fn exercise_registration(source: bcs_domain::bot_provider::DownlinkDetectionSource) {
    let dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&dir.path().into());
    config.provider_http.downlink_detection_source = source;
    let (addr, task) = helpers::start_test_server_with_config(config).await;
    let abort = task.abort_handle();
    // Ensure a failed assertion cannot leave a server listening in the test process.
    struct Stop(tokio::task::AbortHandle);
    impl Drop for Stop {
        fn drop(&mut self) {
            self.0.abort();
        }
    }
    let _stop = Stop(abort);
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap();
    let base = format!("http://{addr}");
    let api = format!("{base}/openapi/v1/collaboration/register");
    let provider_response = client
        .post(format!("{base}/providers"))
        .header("X-Mock-User-Id", "11111111")
        .json(
            &json!({"name": "Poolab registration test", "protocol_version": "2.0",
            "auth": {"mode": "static_bearer"}}),
        )
        .send()
        .await
        .unwrap();
    assert!(provider_response.status().is_success());
    let provider = provider_response.json::<Value>().await.unwrap();
    let id = provider["provider_id"].as_str().unwrap();

    // No default webhook: issuance and upstream still work.
    let token = data(
        client
            .get(format!("{api}/token"))
            .header("x-avernet-principal", principal("11111111"))
            .query(&[("provider_id", id)]),
        StatusCode::OK,
    )
    .await;
    assert_eq!(token["registration"]["token_version"], 2);
    let token_value = token["token"].as_str().unwrap();
    let denied = client
        .get(format!("{api}/token"))
        .header("x-avernet-principal", principal("another-human"))
        .query(&[("provider_id", id)])
        .send()
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    let upstream_query = [
        ("token", token_value),
        ("bot-name", "Upstream bridge"),
        ("provider_bot_ref", "cc-upstream"),
    ];
    let upstream = data(
        client.post(&api).query(&upstream_query),
        StatusCode::CREATED,
    )
    .await;
    assert_eq!(upstream["registration"]["mode"], "plugin");
    assert_eq!(upstream["registration"]["provider_id"], id);
    // Provider/ref is unique, but the registration token is reusable for
    // distinct refs. Duplicate POSTs no longer replay runtime credentials.
    let retried = client.post(&api).query(&upstream_query).send().await.unwrap();
    assert_eq!(retried.status(), StatusCode::CONFLICT);
    let mut connected =
        helpers::MockBot::reconnect(addr, upstream["bot_token"].as_str().unwrap()).await;
    assert_eq!(connected.bot_id, upstream["bot_uuid"].as_str().unwrap());
    connected.disconnect().await;

    let gateway_query = [
        ("token", token_value),
        ("bot_name", "Gateway bridge"),
        ("provider_bot_ref", "codex-gateway"),
        ("mode", "gateway"),
    ];
    let missing = client
        .post(&api)
        .query(&gateway_query)
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status(), StatusCode::BAD_REQUEST);
    let gateway = data(
        client
            .post(&api)
            .query(&gateway_query)
            .query(&[("webhook_url", "https://bridge.example.com/hook")]),
        StatusCode::CREATED,
    )
    .await;
    assert_eq!(
        gateway["registration"]["webhook_url"],
        "https://bridge.example.com/hook"
    );
    assert!(gateway.get("provider_admin_token").is_none());
    assert!(gateway.get("bcs_to_provider_token").is_none());
    assert_ne!(gateway["bot_token"], provider["provider_admin_token"]);
    assert_ne!(gateway["bot_token"], provider["bcs_to_provider_token"]);
    // The unchanged legacy handler must reject scoped tokens, not create an ordinary Bot.
    let legacy_reject = client
        .post(format!("{base}/register"))
        .query(&upstream_query)
        .send()
        .await
        .unwrap();
    assert_eq!(legacy_reject.status(), StatusCode::UNAUTHORIZED);

    let legacy_token = data(
        client
            .get(format!("{api}/token"))
            .header("x-avernet-principal", principal("11111111")),
        StatusCode::OK,
    )
    .await;
    assert_eq!(legacy_token.as_object().unwrap().len(), 3);
    let legacy = data(
        client.post(&api).query(&[
            ("token", legacy_token["token"].as_str().unwrap()),
            ("bot-name", "Old client"),
        ]),
        StatusCode::CREATED,
    )
    .await;
    assert_eq!(legacy.as_object().unwrap().len(), 3);
}
