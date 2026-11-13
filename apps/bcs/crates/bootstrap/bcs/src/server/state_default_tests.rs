use super::*;
use tower::ServiceExt;

#[test]
fn coverage_memory_workbench_composes_group_and_session_lifecycle_without_external_services() {
    const NAME: &str = "server::state_default_tests::coverage_memory_workbench_composes_group_and_session_lifecycle_without_external_services";
    const CHILD: &str = "BCS_WORKBENCH_REGRESSION_CHILD";
    if std::env::var_os(CHILD).is_none() {
        let dir = tempfile::tempdir().unwrap();
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", NAME, "--nocapture"]).current_dir(dir.path()).env(CHILD, "1")
            .env("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", "test-default-principal-signing-key")
            .env("BCS_SECRET_BCN_GROUP_SESSION_WS_JWT", "test-default-session-signing-key-32bytes")
            .output().unwrap();
        assert!(output.status.success(), "isolated workbench test failed: {}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        return;
    }
    tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        use bcs_service_api::{BotCapabilities, GroupCreateCommand, HumanMentionNotifyMode, NewSessionParams, SessionKind, CreateOrReactivateCommand, SessionStatus};
        let data = tempfile::tempdir().unwrap();
        let config = BcsConfig { bots_base_dir: data.path().join("bots"), ..BcsConfig::default() };
        let state = Arc::new(BcsServerState::memory_for_config(config));
        let debug = format!("{state:?}");
        assert!(debug.contains("<redacted>"));
        for bot in ["driver", "worker"] {
            state.services.registry.register(bot.into(), BotCapabilities { name: Some(bot.into()), visibility: "public".into(), ..Default::default() }).await.unwrap();
        }
        let group = state.services.group_management.create_group(GroupCreateCommand {
            group_id: Some("workbench-regression".into()), caller_actor_id: Some("driver".into()), driver_bot_id: "driver".into(),
            label: Some("Regression".into()), topic: None, context: None, opening_message: None, create_initial_session: false,
            human_mention_notify_mode: HumanMentionNotifyMode::None, routing_policy: None, participants: Vec::new(),
            member_bot_ids: vec!["worker".into()], group_kind: None, group_strategy: None, originator: None, visibility: None, provisioning: false,
        }).await.unwrap();
        assert_eq!(group.driver_bot_id, "driver");
        let created = state.services.session_management.create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.group_id.clone(), session_id: None,
            params: NewSessionParams { session_kind: SessionKind::ServiceInvocation, ..Default::default() },
        }).await.unwrap();
        assert!(created.created);
        let completed = state.services.session_management.complete_if_running(&created.session.id, Some(serde_json::json!("result")), None).await.unwrap().unwrap();
        assert_eq!(completed.status, SessionStatus::Completed);
        assert!(state.services.session_management.complete_if_running(&created.session.id, None, None).await.unwrap().is_none());
        let resumed = state.services.session_management.create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.group_id, session_id: Some(created.session.id), params: NewSessionParams::default(),
        }).await.unwrap();
        assert!(!resumed.created); assert_eq!(resumed.session.activation_count, 2);
        assert_eq!(resumed.session.status, SessionStatus::Running);
        let http = crate::http_adapter::build_http_app_state(state).await;
        let app = bcs_http::router::build_router(http);
        for (uri, expected) in [("/health", StatusCode::OK), ("/collaboration/templates/missing", StatusCode::NOT_FOUND)] {
            let response = app.clone().oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status(), expected, "{uri}");
        }
    });
}

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
