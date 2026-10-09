//! Real bootstrap -> HTTP -> application -> core -> stores registration path.
mod helpers;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
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
    // This binary has one test; initialize its dedicated test-only secret before
    // any server starts threads or snapshots the environment.
    unsafe {
        std::env::set_var(
            "BCS_SECRET_PROVIDER_REGISTRATION_TEST",
            "test-only-gateway-principal-signing-key",
        );
    }
    for source in [DownlinkDetectionSource::Binding, DownlinkDetectionSource::BotConnectionMode] {
        for auth_mode in ["static_bearer", "provider_admin", "agentpass"] {
            tokio::time::timeout(std::time::Duration::from_secs(45), exercise_registration(source, auth_mode))
                .await
                .expect("registration integration exceeded its deadline");
        }
    }
}

async fn exercise_registration(source: bcs_domain::bot_provider::DownlinkDetectionSource, auth_mode: &str) {
    let dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&dir.path().into());
    config.provider_http.downlink_detection_source = source;
    let db_path = dir.path().join("registration.sqlite");
    config.database.sqlite.path = db_path.to_str().unwrap().into();
    config.auth.allow_mock_headers = true;
    config.secret.provider = "env".into();
    config.gateway_principal.signing_key_secret = Some("provider-registration-test".into());
    config.group_session_ws.signing_key_secret = "provider-registration-test".into();
    config.session_files.backend.insert(
        "data_dir".into(), toml::Value::String(dir.path().join("files").to_str().unwrap().into()),
    );
    let (addr, task) = bcs::BcsServer::new_with_storage(config).await.unwrap()
        .run_on_random_port().await.unwrap();
    let db = LocalSqliteDbPlugin::new_file(db_path.to_str().unwrap()).unwrap();
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
            "auth": {"mode": auth_mode}}),
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
    assert_agent_code(&db, &upstream, "cc-upstream", auth_mode).await;
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
    assert_agent_code(&db, &gateway, "codex-gateway", auth_mode).await;
    exercise_legacy_registration(&client, &base, &db, id, auth_mode, token_value).await;

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
    let from_openapi_v1 = legacy_data(client.post(format!("{base}/register")).query(&[
        ("token", legacy_token["token"].as_str().unwrap()),
        ("bot-name", "OpenAPI v1 legacy POST"),
    ])).await;
    assert_eq!(from_openapi_v1.as_object().unwrap().len(), 3);
}

async fn legacy_data(request: reqwest::RequestBuilder) -> Value {
    let response = request.send().await
        .unwrap_or_else(|error| panic!("legacy request failed: {}", error.without_url()));
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers().get("cache-control").and_then(|value| value.to_str().ok()), Some("no-store"));
    let body = response.json::<Value>().await.unwrap();
    assert!(body.get("data").is_none(), "legacy responses must remain bare JSON");
    body
}

async fn exercise_legacy_registration(
    client: &reqwest::Client,
    base: &str,
    db: &LocalSqliteDbPlugin,
    provider_id: &str,
    auth_mode: &str,
    openapi_token: &str,
) {
    let api = format!("{base}/openapi/v1/collaboration/register");
    let legacy = format!("{base}/register");
    let token = legacy_data(client.get(format!("{legacy}/token"))
        .header("X-Mock-User-Id", "11111111")
        .query(&[("provider_id", provider_id)])).await;
    assert_eq!(token["registration"]["token_version"], 2);
    assert_eq!(token["registration"]["provider_id"], provider_id);
    assert_eq!(token["registration"]["allowed_modes"], json!(["plugin", "gateway"]));
    let token_value = token["token"].as_str().unwrap();

    let anonymous = client.get(format!("{legacy}/token"))
        .query(&[("provider_id", provider_id)]).send().await.unwrap();
    assert_eq!(anonymous.status(), StatusCode::UNAUTHORIZED);

    for (requested_provider, expected) in [
        (provider_id, StatusCode::FORBIDDEN),
        ("unknown-provider", StatusCode::NOT_FOUND),
        ("", StatusCode::BAD_REQUEST),
    ] {
        let denied = client.get(format!("{legacy}/token"))
            .header("X-Mock-User-Id", "another-human")
            .query(&[("provider_id", requested_provider)])
            .send().await.unwrap();
        assert_eq!(denied.status(), expected);
        let body = denied.json::<Value>().await.unwrap();
        assert!(body["error"].is_string());
        assert!(body["message"].is_string());
    }

    // Legacy-issued v2 token redeemed by legacy POST; caller-supplied owner/Provider are ignored.
    let plugin_query = [
        ("token", token_value), ("bot-name", "Legacy upstream"),
        ("provider_bot_ref", "legacy-upstream"),
        ("owner", "attacker"), ("provider_id", "other-provider"),
    ];
    let upstream = legacy_data(client.post(&legacy).query(&plugin_query)).await;
    assert_eq!(upstream["registration"]["mode"], "plugin");
    assert_eq!(upstream["registration"]["provider_id"], provider_id);
    assert_agent_code(db, &upstream, "legacy-upstream", auth_mode).await;
    let rows = db.query(DbStatement::with_params(
        "SELECT created_by FROM bcs_bots WHERE bot_uuid = ?",
        vec![upstream["bot_uuid"].as_str().unwrap().into()],
    )).await.unwrap();
    assert_eq!(rows[0].get_string("created_by").unwrap().as_deref(), Some("11111111"));
    let duplicate = client.post(&legacy).query(&plugin_query).send().await.unwrap();
    assert_eq!(duplicate.status(), StatusCode::CONFLICT);

    let gateway_query = [
        ("token", token_value), ("bot_name", "Legacy gateway"),
        ("provider_bot_ref", "legacy-gateway"), ("mode", "gateway"),
    ];
    let missing_endpoint = client.post(&legacy).query(&gateway_query).send().await.unwrap();
    assert_eq!(missing_endpoint.status(), StatusCode::BAD_REQUEST);
    let gateway = legacy_data(client.post(&legacy).query(&gateway_query)
        .query(&[("webhook_url", "https://bridge.example.com/legacy-hook")])).await;
    assert_eq!(gateway["registration"]["mode"], "gateway");
    assert_eq!(gateway["registration"]["webhook_url"], "https://bridge.example.com/legacy-hook");
    assert!(gateway.get("provider_admin_token").is_none());
    assert!(gateway.get("bcs_to_provider_token").is_none());
    assert_agent_code(db, &gateway, "legacy-gateway", auth_mode).await;

    // Both HTTP surfaces accept the same signed v2 capability.
    let from_openapi = legacy_data(client.post(&legacy).query(&[
        ("token", openapi_token), ("bot_name", "OpenAPI token legacy POST"),
        ("provider_bot_ref", "openapi-to-legacy"),
    ])).await;
    assert_eq!(from_openapi["registration"]["provider_id"], provider_id);
    let from_legacy = data(client.post(&api).query(&[
        ("token", token_value), ("bot_name", "Legacy token OpenAPI POST"),
        ("provider_bot_ref", "legacy-to-openapi"),
    ]), StatusCode::CREATED).await;
    assert_eq!(from_legacy["registration"]["provider_id"], provider_id);

    for mode in ["unknown", ""] {
        let invalid = client.post(&legacy).query(&[
            ("token", token_value), ("bot_name", "Invalid mode"),
            ("provider_bot_ref", "invalid-mode"), ("mode", mode),
        ]).send().await.unwrap();
        assert_eq!(invalid.status(), StatusCode::BAD_REQUEST);
    }
    let missing_ref = client.post(&legacy).query(&[
        ("token", token_value), ("bot-name", "Missing reference"),
    ]).send().await.unwrap();
    assert_eq!(missing_ref.status(), StatusCode::BAD_REQUEST);

    // Signed mode scope cannot be widened, and invalid v2 capabilities never fall back to v1.
    let mut claims = bcs_domain::provider_registration_token::decode_and_verify(
        token_value, b"test-invite-secret-32-bytes!!!!",
    ).unwrap();
    claims.allowed_modes = vec![bcs_domain::provider_registration_token::ProviderRegistrationMode::Plugin];
    let restricted = bcs_domain::provider_registration_token::encode(
        &claims, b"test-invite-secret-32-bytes!!!!",
    );
    let denied_mode = client.post(&legacy).query(&[
        ("token", restricted.as_str()), ("bot_name", "Restricted gateway"),
        ("provider_bot_ref", "restricted"), ("mode", "gateway"),
        ("webhook_url", "https://bridge.example.com/hook"),
    ]).send().await.unwrap();
    assert_eq!(denied_mode.status(), StatusCode::FORBIDDEN);
    let wrong_signature = bcs_domain::provider_registration_token::encode(&claims, b"wrong-test-secret");
    claims.exp = 0;
    let expired = bcs_domain::provider_registration_token::encode(&claims, b"test-invite-secret-32-bytes!!!!");
    for invalid_token in [wrong_signature.as_str(), expired.as_str(), "invalid-token"] {
        let rejected = client.post(&legacy).query(&[
            ("token", invalid_token), ("bot_name", "Invalid token"),
            ("provider_bot_ref", "invalid-token"),
        ]).send().await.unwrap();
        assert_eq!(rejected.status(), StatusCode::UNAUTHORIZED);
        let body = rejected.json::<Value>().await.unwrap();
        assert_eq!(body["error"], "unauthorized");
    }

    // Ordinary legacy issuance still yields exactly the original three fields and six-hour TTL.
    let v1 = legacy_data(client.get(format!("{legacy}/token"))
        .header("X-Mock-User-Id", "11111111")).await;
    assert_eq!(v1.as_object().unwrap().len(), 3);
    assert_eq!(v1["note"], "Use this token for bot registration within 6 hours");
    let claims = bcs_domain::register_token_decode_and_verify(
        v1["token"].as_str().unwrap(), b"test-invite-secret-32-bytes!!!!",
    ).unwrap();
    assert_eq!(claims.v, 1);
    assert_eq!(claims.id, "human_11111111");
    assert_eq!(v1["expires_at"], claims.exp * 1000);
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    assert!((now + 21595..=now + 21600).contains(&claims.exp));
    let unsupported = bcs_domain::register_token_encode(
        &bcs_domain::RegisterTokenPayload { v: 3, id: "human_11111111".into(), exp: now + 21600 },
        b"test-invite-secret-32-bytes!!!!",
    );
    let rejected = client.post(&legacy).query(&[
        ("token", unsupported.as_str()), ("bot-name", "Unsupported token"),
    ]).send().await.unwrap();
    assert_eq!(rejected.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(rejected.json::<Value>().await.unwrap(), json!({
        "error": "unauthorized", "message": "unsupported register token version",
    }));
    // V1 keeps ignoring previously unknown Provider options, including invalid mode strings.
    for name_key in ["bot-name", "bot_name"] {
        let ordinary = legacy_data(client.post(&legacy).query(&[
            ("token", v1["token"].as_str().unwrap()), (name_key, "Legacy ordinary Bot"),
            ("mode", "ignored"), ("provider_bot_ref", "ignored"),
        ])).await;
        assert_eq!(ordinary.as_object().unwrap().len(), 3);
        assert_eq!(ordinary["bot_name"], "Legacy ordinary Bot");
        assert!(ordinary["bot_uuid"].is_string());
        assert!(ordinary["bot_token"].is_string());
    }
    let ordinary = data(client.post(&api).query(&[
        ("token", v1["token"].as_str().unwrap()), ("bot-name", "Legacy v1 OpenAPI POST"),
    ]), StatusCode::CREATED).await;
    assert_eq!(ordinary.as_object().unwrap().len(), 3);
}

async fn assert_agent_code(db: &dyn DbPlugin, result: &Value, bot_ref: &str, auth_mode: &str) {
    let rows = db.query(DbStatement::with_params(
        "SELECT agent_code, bot_info FROM bcs_bots WHERE bot_uuid = ?",
        vec![result["bot_uuid"].as_str().unwrap().into()],
    )).await.unwrap();
    assert_eq!(rows.len(), 1);
    let expected = (auth_mode == "agentpass").then_some(bot_ref);
    assert_eq!(rows[0].get_string("agent_code").unwrap().as_deref(), expected);
    let capabilities: Value = serde_json::from_str(&rows[0].get_string("bot_info").unwrap().unwrap()).unwrap();
    // The routing identifier belongs in the dedicated column, not the
    // serialized/public BotCapabilities view (which intentionally omits it).
    assert!(capabilities.get("agent_code").is_none());
}
