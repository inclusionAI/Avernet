use super::*;

#[tokio::test]
async fn leave_bot_route_rejects_bot_token_without_human_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-leave".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("leave-token".to_string(), "bot-leave".to_string())
        .await;
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-leave")
                .header("authorization", "Bearer leave-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(registry.get("bot-leave").await.is_some());
}
#[tokio::test]
async fn leave_bot_route_rejects_non_owner_human_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-leave".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("bot-leave", "alice", true)
        .await
        .unwrap();
    shared_route_authority().seed_owner("bot-leave", "alice");
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let chain = static_auth_chain("bob", "Bob");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-leave")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(registry.get("bot-leave").await.is_some());
}
#[tokio::test]
async fn get_visibility_route_rejects_invalid_bot_token() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                visibility: "protected".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer invalid-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], false);
    assert_eq!(json["error"], "valid bot token is required");
}
