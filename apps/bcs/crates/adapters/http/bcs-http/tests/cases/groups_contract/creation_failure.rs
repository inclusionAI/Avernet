use super::*;

#[tokio::test]
async fn session_state_machine_start_rejects_malformed_opening_message_as_bad_request() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/session-chat/state-machine-runs")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "definition_yaml": ONE_SHOT_DEFINITION_YAML,
                        "participant_bindings": {},
                        "opening_message": {
                            "type": "panel",
                            "component": "partnerPanel.OneShotRunView",
                            "unknown": true
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "invalid_opening_message");
    assert!(
        collaboration_runtime
            .session_start_commands
            .lock()
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn session_state_machine_routes_require_bot_authentication() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/session-chat/state-machine-permission")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(
        collaboration_runtime
            .permission_commands
            .lock()
            .await
            .is_empty()
    );
}
#[tokio::test]
async fn post_groups_with_state_machine_yaml_rejects_judge_when_llm_disabled() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Judge Disabled
participants:
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      write:
        kind: bot_task
        display_name: Write
        assignee:
          type: bot_binding
          binding: writer
        instruction: Write.
        transitions:
          approved:
            targets: []
        judge:
          type: llm
          criteria:
            - The answer is complete.
          outcomes:
            - approved
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-judge-disabled",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "participant_bindings": {
                            "writer": {
                                "source": "manual",
                                "bot_ids": ["driver-bot"]
                            }
                        },
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body = String::from_utf8(body.to_vec()).unwrap();
    assert!(body.contains("state-machine judge requires llm.type to select an LLM provider"));
    assert!(recorder.create_calls.lock().await.is_empty());
    assert!(collaboration_runtime.definitions.lock().await.is_empty());
    assert!(
        collaboration_runtime
            .configure_calls
            .lock()
            .await
            .is_empty()
    );
}
#[tokio::test]
async fn start_state_machine_run_rejects_inline_judge_yaml_when_llm_disabled() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime_and_human_identity().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
id: sm_inline_judge_disabled
version: 1
name: Inline Judge Disabled
participants:
  writer:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      write:
        kind: bot_task
        display_name: Write
        assignee:
          type: bot_binding
          binding: writer
        instruction: Write.
        transitions:
          approved:
            targets: []
        judge:
          type: llm
          criteria:
            - The answer is complete.
          outcomes:
            - approved
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-sm-judge-disabled/state-machine-runs")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "definition_yaml": definition_yaml,
                        "input": {"question": "hello"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body = String::from_utf8(body.to_vec()).unwrap();
    assert!(body.contains("state-machine judge requires llm.type to select an LLM provider"));
    assert!(collaboration_runtime.start_commands.lock().await.is_empty());
}
#[tokio::test]
async fn rerun_state_machine_route_rejects_a_request_body() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime_and_human_identity().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/state-machine-runs/run-1/reruns")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"input":{"replacement":true}}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(collaboration_runtime.rerun_commands.lock().await.is_empty());
}

#[tokio::test]
async fn human_state_machine_routes_reject_requests_without_human_identity() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;

    let pending_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/state-machine-runs/run-1/pending-human-nodes")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(pending_response.status(), StatusCode::UNAUTHORIZED);

    let respond_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/state-machine-runs/run-1/nodes/human_review/respond")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"content":"looks good"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(respond_response.status(), StatusCode::UNAUTHORIZED);
    assert!(
        collaboration_runtime
            .pending_human_commands
            .lock()
            .await
            .is_empty()
    );
    assert!(
        collaboration_runtime
            .respond_human_commands
            .lock()
            .await
            .is_empty()
    );
}
#[tokio::test]
async fn post_groups_with_state_machine_yaml_rejects_multi_bot_assigned_slot_before_create() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Multi Bot Assigned Slot
participants:
  driver:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-multi-bot-assigned-slot",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "participant_bindings": {
                            "driver": {
                                "source": "manual",
                                "bot_ids": ["driver-bot", "target-bot"]
                            }
                        },
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body = String::from_utf8(body.to_vec()).unwrap();
    assert!(body.contains(
        "participant slot driver is assigned to a node and must resolve to exactly one bot in the current runtime"
    ));
    assert!(recorder.create_calls.lock().await.is_empty());
    assert!(collaboration_runtime.definitions.lock().await.is_empty());
    assert!(
        collaboration_runtime
            .configure_calls
            .lock()
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_rejects_bcs_participant_role() {
    let (app, recorder, _collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
id: sm_role_rejected
version: 1
name: Role Rejected
participants:
  driver:
    bot_id: driver-bot
    bcs_participant_role: driver
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-role-rejected",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(recorder.create_calls.lock().await.is_empty());
}
#[tokio::test]
async fn post_groups_with_state_machine_yaml_definition_conflict_returns_409() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    *collaboration_runtime.upsert_error.lock().await = Some(CollaborationRuntimeError::Conflict(
        "CollaborationDefinition 'sm_conflict@1' already exists with different content".to_string(),
    ));
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Conflict Definition
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer.
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-sm-conflict",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    assert!(recorder.create_calls.lock().await.is_empty());
}
#[tokio::test]
async fn post_groups_dm_rejects_ambiguous_legacy_participants() {
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
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
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "group_kind": "dm",
                        "participants": [
                            { "bot_uuid": "target-bot" },
                            { "bot_uuid": "other-bot" }
                        ]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(recorder.create_dm_calls.lock().await.is_empty());
}
