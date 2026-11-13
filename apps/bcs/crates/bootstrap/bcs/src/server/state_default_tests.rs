use super::*;
use tower::ServiceExt;

#[tokio::test]
async fn default_memory_composition_serves_health_and_requires_a_bot_caller() {
    // Default composition reads deployment secrets and relative runtime paths.
    // Isolate them in a child instead of mutating the test process environment.
    const CHILD: &str = "BCS_DEFAULT_COMPOSITION_TEST_CHILD";
    if std::env::var_os(CHILD).is_none() {
        let dir = tempfile::tempdir().unwrap();
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "server::state_default_tests::default_memory_composition_serves_health_and_requires_a_bot_caller", "--nocapture"])
            .current_dir(dir.path())
            .env(CHILD, "1")
            .env("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", "test-default-principal-signing-key")
            .env("BCS_SECRET_BCN_GROUP_SESSION_WS_JWT", "test-default-session-signing-key-32bytes")
            .status().unwrap();
        assert!(status.success(), "isolated default composition failed: {status}");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let config = BcsConfig { bots_base_dir: dir.path().join("bots"), ..BcsConfig::default() };
    let state = Arc::new(BcsServerState::memory_for_config(config));
    let http = crate::http_adapter::build_http_app_state(state).await;
    let app = bcs_http::router::build_router(http);
    for (uri, expected) in [
        ("/health", StatusCode::OK),
        ("/bots", StatusCode::UNAUTHORIZED),
        ("/bots/missing", StatusCode::UNAUTHORIZED),
    ] {
        let response = app.clone().oneshot(Request::builder().uri(uri)
            .body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(response.status(), expected, "{uri}");
    }
}
