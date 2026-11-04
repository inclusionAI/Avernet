//! The compatibility facade must retain secure refresh/logout without [api.auth].
use super::*;

const TRUSTED: &str = "https://work.example";

#[tokio::test]
async fn refresh_and_logout_accept_trusted_origins_on_both_entries() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth_and_cors(
        &tmp.path().to_path_buf(),
        full_oauth_config(),
        vec!["*".into(), "null".into(), TRUSTED.into()],
    );
    assert!(config.api.auth.is_none());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server");
    let engine = strict_engine_for(&state).await;
    let client = reqwest::Client::new();
    for base in ["/openapi/v1/auth", "/auth"] {
        let (_, token) = seed_strict_session(&state, "Alice", "https://example.com/a.png").await;
        let response = client
            .post(format!("http://{addr}{base}/refresh"))
            .header("cookie", format!("bcs_session={token}"))
            .header("origin", TRUSTED)
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .expect("refresh");
        assert_eq!(response.status(), 200, "{base}: trusted refresh");
        let cookie = challenge_cookie_from(&response).expect("rotated cookie");
        let cookie = cookie.split(';').next().expect("cookie pair");
        let rotated = cookie.strip_prefix("bcs_session=").expect("session cookie");
        assert_ne!(rotated, token);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        assert!(
            engine.verify(&token, now).await.is_err(),
            "old token invalidated"
        );
        assert!(engine.verify(rotated, now).await.is_ok());

        let response = client
            .post(format!("http://{addr}{base}/logout"))
            .header("cookie", cookie)
            .header("origin", TRUSTED)
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .expect("logout");
        assert_eq!(response.status(), 200, "{base}: trusted logout");
        assert!(challenge_cookie_from(&response)
            .unwrap()
            .contains("Max-Age=0"));
        assert!(
            engine.verify(rotated, now).await.is_err(),
            "logout revokes session"
        );
    }
    handle.abort();
}

#[tokio::test]
async fn compat_cookie_writes_reject_missing_untrusted_and_duplicate_origins() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth_and_cors(
        &tmp.path().to_path_buf(),
        full_oauth_config(),
        vec![TRUSTED.into()],
    );
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state()
        .await
        .expect("start server");
    let (_, token) = seed_strict_session(&state, "Alice", "https://example.com/a.png").await;
    let engine = strict_engine_for(&state).await;
    let client = reqwest::Client::new();
    for base in ["/openapi/v1/auth", "/auth"] {
        for action in ["refresh", "logout"] {
            for origins in [vec![], vec!["https://evil.example"], vec![TRUSTED, TRUSTED]] {
                let mut request = client
                    .post(format!("http://{addr}{base}/{action}"))
                    .header("cookie", format!("bcs_session={token}"))
                    .timeout(Duration::from_secs(5));
                for origin in origins {
                    request = request.header("origin", origin);
                }
                let response = request.send().await.expect("untrusted write");
                assert_eq!(response.status(), 403, "{base}/{action}");
                assert!(challenge_cookie_from(&response).is_none());
            }
        }
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    assert!(
        engine.verify(&token, now).await.is_ok(),
        "rejected writes leave session intact"
    );
    handle.abort();
}

#[tokio::test]
async fn compat_cookie_writes_fail_closed_without_exact_origins() {
    for origins in [
        vec![],
        vec!["*".into(), "null".into(), "https://*.example.com".into()],
    ] {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let config =
            config_with_auth_and_cors(&tmp.path().to_path_buf(), full_oauth_config(), origins);
        let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
            .run_on_random_port_with_state()
            .await
            .expect("start server");
        let (_, token) = seed_strict_session(&state, "Alice", "https://example.com/a.png").await;
        for base in ["/openapi/v1/auth", "/auth"] {
            for action in ["refresh", "logout"] {
                let response = reqwest::Client::new()
                    .post(format!("http://{addr}{base}/{action}"))
                    .header("cookie", format!("bcs_session={token}"))
                    .header("origin", TRUSTED)
                    .timeout(Duration::from_secs(5))
                    .send()
                    .await
                    .expect("untrusted write");
                assert_eq!(response.status(), 403, "{base}/{action}");
                assert!(challenge_cookie_from(&response).is_none());
            }
        }
        handle.abort();
    }
}

/// A cookie-less logout is the idempotent boundary: it clears the cookie
/// and reports success even with no trusted origins configured.
#[tokio::test]
async fn legacy_logout_without_cookie_is_idempotent() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port()
        .await
        .expect("start server");

    let resp = reqwest::Client::new()
        .post(format!("http://{addr}/auth/logout"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("request /auth/logout");
    assert_eq!(resp.status(), 200);
    let set_cookie = challenge_cookie_from(&resp).expect("set-cookie header");
    assert!(set_cookie.starts_with("bcs_session=;"));
    assert!(set_cookie.contains("Max-Age=0"));

    handle.abort();
}

#[tokio::test]
async fn compat_origin_configuration_is_validated_before_routes_are_published() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth_and_cors(
        &tmp.path().to_path_buf(),
        full_oauth_config(),
        vec!["https://work.example/not-an-origin".into()],
    );
    let result = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port()
        .await;
    assert!(matches!(result, Err(BcsError::InvalidConfig(_))));
}

#[tokio::test]
async fn compat_current_user_accepts_oauth_without_changing_protected_api_trust() {
    let tmp = tempfile::TempDir::new().expect("temp dir");
    let config = config_with_auth(&tmp.path().to_path_buf(), full_oauth_config());
    let (addr, handle, state) = BcsServer::new_allowing_private_outbound_for_tests(config)
        .run_on_random_port_with_state().await.expect("start server");
    let (user_id, token) = seed_strict_session(&state, "Alice", "/avatar.png").await;
    let client = reqwest::Client::new();
    for family in ["/auth", "/openapi/v1/auth"] {
        let response = client.get(format!("http://{addr}{family}/user"))
            .header("cookie", format!("bcs_session={token}"))
            .send().await.expect("current user");
        assert_eq!(response.status(), 200, "{family}");
        let body: Value = response.json().await.expect("JSON");
        let user = body.get("data").unwrap_or(&body);
        assert_eq!(user["user_id"], user_id);
        assert_eq!(user["provider"], "google");
        assert_eq!(user["name"], "Alice");
        assert_eq!(user["avatar"], "/avatar.png");
    }
    let response = client.get(format!("http://{addr}/openapi/v1/collaboration/groups"))
        .header("cookie", format!("bcs_session={token}"))
        .send().await.expect("protected route");
    assert_eq!(response.status(), 401, "compat must not accept cookies on protected APIs");
    strict_engine_for(&state).await.revoke(&token).await.expect("revoke");
    let response = client.get(format!("http://{addr}/openapi/v1/auth/user"))
        .header("cookie", format!("bcs_session={token}"))
        .send().await.expect("revoked current user");
    assert_eq!(response.status(), 401);
    handle.abort();
}
