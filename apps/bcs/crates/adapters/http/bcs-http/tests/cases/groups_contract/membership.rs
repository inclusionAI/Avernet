use super::*;

#[tokio::test]
async fn post_group_member_delegates_without_exposing_role() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .registry(registry)
        .group_management(recorder.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "bot_uuid": "target-bot" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["added"], true);
    assert_eq!(json["session_id"], "group-1");
    assert_eq!(json["member"]["bot_uuid"], "target-bot");
    assert_eq!(json["member"]["role"], "consultant");

    let calls = recorder.add_member_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.human_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bot_id, "target-bot");
}
#[tokio::test]
async fn participant_mode_route_delegates_to_group_management_and_preserves_envelope() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-1/participants/driver-bot/mode")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "mode": "muted" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["group_id"], "group-1");
    assert_eq!(json["data"]["actor_id"], "driver-bot");
    assert_eq!(json["data"]["mode"], "muted");

    let calls = recorder.participant_mode_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id, "driver-bot");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.actor_id, "driver-bot");
    assert_eq!(cmd.mode, ParticipantMode::Muted);
}
