use super::*;

#[tokio::test]
async fn post_collaboration_definition_validate_delegates_to_runtime_service() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/definitions/validate")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "definition_yaml": "name: test"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["valid"], true);
    assert_eq!(json["summary"]["nodes"], 1);
    assert_eq!(json["participants"][0]["binding"], "writer");
    assert_eq!(json["graph"]["graph_mode"], "acyclic");
    assert_eq!(json["graph"]["nodes"][0]["node_id"], "answer");
    assert_eq!(json["graph"]["edges"], serde_json::json!([]));
    assert!(json.get("definition").is_none());

    let calls = collaboration_runtime.validation_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].definition_yaml, "name: test");
    assert!(!calls[0].judge_available);
}

#[tokio::test]
async fn session_state_machine_permission_uses_authenticated_bot_identity() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/session-chat/state-machine-permission")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["allowed"], true);
    assert_eq!(json["caller_bot_id"], "driver-bot");
    assert_eq!(json["policy_version"], "session_state_machine_v1");

    let commands = collaboration_runtime.permission_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].session_id, "session-chat");
    assert_eq!(commands[0].caller_bot_id, "driver-bot");
}
#[tokio::test]
async fn session_state_machine_start_forwards_yaml_and_transient_role_bindings() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = ONE_SHOT_DEFINITION_YAML;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/session-chat/state-machine-runs")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "definition_yaml": definition_yaml,
                        "participant_bindings": {
                            "writer": {
                                "source": "manual",
                                "bot_ids": ["target-bot"]
                            }
                        },
                        "opening_message": {
                            "type": "panel",
                            "component": "partnerPanel.OneShotRunView",
                            "params": {
                                "runId": "{{bcs.run_id}}",
                                "scene": "release"
                            },
                            "tab": {
                                "title": "One Shot",
                                "closable": true
                            }
                        },
                        "input": {"question": "draft it"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["run"]["run_id"], "run-one-shot");
    assert_eq!(json["run"]["session_id"], "session-chat");
    assert_eq!(json["run"]["created_by"], "driver-bot");
    assert!(json["run"].get("opening_message_override").is_none());

    let commands = collaboration_runtime.session_start_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].session_id, "session-chat");
    assert_eq!(commands[0].caller_bot_id, "driver-bot");
    assert_eq!(commands[0].definition_yaml, definition_yaml);
    assert_eq!(commands[0].input, serde_json::json!({"question": "draft it"}));
    assert_eq!(
        serde_json::to_value(commands[0].opening_message.as_ref().unwrap()).unwrap(),
        serde_json::json!({
            "type": "panel",
            "component": "partnerPanel.OneShotRunView",
            "params": {
                "runId": "{{bcs.run_id}}",
                "scene": "release"
            },
            "tab": {
                "title": "One Shot",
                "closable": true
            }
        })
    );
    assert_eq!(
        commands[0]
            .participant_bindings
            .get("writer")
            .map(|binding| (binding.source.as_str(), binding.bot_ids.as_slice())),
        Some(("manual", &["target-bot".to_string()][..]))
    );
    assert!(!commands[0].judge_available);
}

#[tokio::test]
async fn session_state_machine_start_accepts_compatible_opening_message_shapes() {
    let (app, _, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let opening_messages = [
        None,
        Some(Value::Null),
        Some(serde_json::json!("Run {{bcs.run_id}}")),
        Some(serde_json::json!({
            "type": "card",
            "component": "partnerCard.OneShotSummary",
            "params": {"runId": "{{bcs.run_id}}"}
        })),
    ];

    for opening_message in opening_messages {
        let mut payload = serde_json::json!({
            "definition_yaml": ONE_SHOT_DEFINITION_YAML,
            "participant_bindings": {},
            "input": null
        });
        if let Some(opening_message) = opening_message {
            payload["opening_message"] = opening_message;
        }
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/sessions/session-chat/state-machine-runs")
                    .header("authorization", "Bearer driver-token")
                    .header("content-type", "application/json")
                    .body(Body::from(payload.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::ACCEPTED);
    }

    let commands = collaboration_runtime.session_start_commands.lock().await;
    assert_eq!(commands.len(), 4);
    assert!(commands[0].opening_message.is_none());
    assert!(commands[1].opening_message.is_none());
    assert_eq!(
        serde_json::to_value(commands[2].opening_message.as_ref().unwrap()).unwrap(),
        serde_json::json!("Run {{bcs.run_id}}")
    );
    assert_eq!(
        serde_json::to_value(commands[3].opening_message.as_ref().unwrap()).unwrap(),
        serde_json::json!({
            "type": "card",
            "component": "partnerCard.OneShotSummary",
            "params": {"runId": "{{bcs.run_id}}"}
        })
    );
}
#[tokio::test]
async fn post_groups_delegates_to_group_management_create_and_preserves_response_shape() {
    let (app, recorder, _temp_dir) = test_app().await;
    *recorder.latest_running_session_id.lock().await = Some("session-initial".to_string());
    *recorder.initial_run.lock().await = Some(InitialGroupRun {
        run_id: "run-initial".to_string(),
        bot_uuid: "driver-bot".to_string(),
        activity_kind: InitialGroupRunActivityKind::GroupBootstrap,
        state: InitialGroupRunState::Running,
        started_at: "2026-09-04T10:00:00Z".to_string(),
    });

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-1",
                        "driver_bot": "driver-bot",
                        "label": "Launch room",
                        "topic": "Launch readiness",
                        "context": "Coordinate the release",
                        "routing_policy": {
                            "mode": "structured",
                            "default_bot_final_delivery": "inject_observers",
                            "sender_routes": {
                                "driver-bot": ["target-bot"]
                            }
                        },
                        "participants": [
                            { "bot_uuid": "driver-bot", "role": "driver" },
                            { "bot_uuid": "target-bot", "role": "consultant" }
                        ]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(
        json["participants"],
        serde_json::json!(["driver-bot", "target-bot"])
    );
    assert_eq!(json["context"], "Coordinate the release");
    assert_eq!(json["created"], true);
    assert_eq!(json["session_id"], "session-initial");
    assert_eq!(json["initial_session_id"], "session-initial");
    assert_eq!(json["initial_run"]["run_id"], "run-initial");
    assert_eq!(json["initial_run"]["bot_uuid"], "driver-bot");
    assert_eq!(json["initial_run"]["activity_kind"], "group_bootstrap");
    assert_eq!(json["initial_run"]["state"], "running");
    assert_eq!(json["human_mention_notify_mode"], "all");
    assert!(json.get("opening_message").is_none());
    assert!(json.get("scene_group_id").is_none());
    assert!(json.get("scene_group_name").is_none());

    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.group_id.as_deref(), Some("group-1"));
    assert_eq!(cmd.driver_bot_id, "driver-bot");
    assert_eq!(cmd.label.as_deref(), Some("Launch room"));
    assert_eq!(cmd.topic.as_deref(), Some("Launch readiness"));
    assert_eq!(cmd.context.as_deref(), Some("Coordinate the release"));
    assert_eq!(
        cmd.participants
            .iter()
            .map(|p| (p.bot_id.as_str(), p.role.as_deref()))
            .collect::<Vec<_>>(),
        vec![
            ("driver-bot", Some("driver")),
            ("target-bot", Some("consultant"))
        ]
    );
    assert_eq!(cmd.member_bot_ids, vec!["driver-bot", "target-bot"]);
    let policy = cmd.routing_policy.as_ref().expect("routing policy");
    assert_eq!(policy.mode, RoutingMode::Structured);
    assert_eq!(
        policy.sender_routes.get("driver-bot"),
        Some(&vec!["target-bot".to_string()])
    );
}

#[tokio::test]
async fn post_groups_accepts_session_scoped_opening_message_for_chat() {
    let (app, recorder, _temp_dir) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "driver_bot": "driver-bot",
                        "opening_message": "hello {{bcs.session_id}}",
                        "participants": [
                            { "bot_uuid": "driver-bot", "role": "driver" }
                        ]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(
        calls[0].opening_message,
        Some(OpeningMessage::Text(
            "hello {{bcs.session_id}}".to_string()
        ))
    );
}

#[tokio::test]
async fn post_groups_with_state_machine_yaml_infers_participants_when_omitted() {
    let (app, recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: SM From YAML Participants
participants:
  speaker_a:
    required: true
  speaker_b:
    required: true
  watcher:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      speak_a:
        kind: bot_task
        display_name: Speak A
        assignee:
          type: bot_binding
          binding: speaker_a
        instruction: Say A.
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
                        "id": "group-sm-yaml",
                        "driver_bot": "driver-bot",
                        "opening_message": "Run {{bcs.run_id}}",
                        "auto_start_on_service_invocation": true,
                        "participant_bindings": {
                            "speaker_a": {
                                "source": "manual",
                                "bot_ids": ["driver-bot"]
                            },
                            "speaker_b": {
                                "source": "manual",
                                "bot_ids": ["target-bot"]
                            },
                            "watcher": {
                                "source": "manual",
                                "bot_ids": ["observer-bot"]
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

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let response_json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(response_json["opening_message"], "Run {{bcs.run_id}}");
    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.group_id.as_deref(), Some("group-sm-yaml"));
    assert_eq!(cmd.driver_bot_id, "driver-bot");
    assert_eq!(
        cmd.opening_message,
        Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string()))
    );
    assert_eq!(
        cmd.participants
            .iter()
            .map(|p| (p.bot_id.as_str(), p.role.as_deref()))
            .collect::<Vec<_>>(),
        vec![
            ("driver-bot", Some("driver")),
            ("target-bot", Some("consultant")),
            ("observer-bot", Some("consultant")),
        ]
    );
    assert_eq!(
        cmd.member_bot_ids,
        vec!["driver-bot", "target-bot", "observer-bot"]
    );
    assert_eq!(
        cmd.group_strategy,
        Some(bcs_service_api::GroupStrategy::StateMachine)
    );

    let definitions = collaboration_runtime.definitions.lock().await;
    assert_eq!(definitions.len(), 1);
    // The authoring YAML must not carry a top-level id (rejected by
    // `reject_authoring_yaml_identity`); the server assigns the definition id.
    assert!(!definitions[0].id.is_empty());
    drop(definitions);

    let configure_calls = collaboration_runtime.configure_calls.lock().await;
    assert_eq!(configure_calls.len(), 1);
    assert_eq!(configure_calls[0].group_id, "group-sm-yaml");
    assert_eq!(
        configure_calls[0]
            .definition_ref
            .as_ref()
            .map(|r| (!r.id.is_empty(), r.version)),
        Some((true, 1))
    );
    assert!(configure_calls[0].auto_start_on_service_invocation);
    assert_eq!(
        configure_calls[0]
            .participant_bindings
            .get("speaker_b")
            .map(|binding| (binding.source.as_str(), binding.bot_ids.as_slice())),
        Some(("manual", &["target-bot".to_string()][..]))
    );
}

#[tokio::test]
async fn post_groups_auto_start_forwards_authenticated_human_to_runtime() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    let session_management = Arc::new(SessionManagementServiceImpl::new(
        Arc::new(MemorySessionRepo::new()),
        Arc::new(MemoryGroupRepo::new()),
    ));
    let session = session_management
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "group-human-auto-start".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                ..Default::default()
            },
        })
        .await
        .expect("seed service invocation session")
        .session;
    let recorder = Arc::new(RecordingGroupManagement::default());
    *recorder.latest_running_session_id.lock().await = Some(session.id);
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
    let services = Services::builder()
        .registry(registry)
        .group(Arc::new(GroupStore::new()))
        .group_management(recorder)
        .session_management(session_management)
        .collaboration_runtime(collaboration_runtime.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(static_auth_chain("alice", "Alice")),
    )));
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Human Auto Start
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
      review:
        kind: human_input
        display_name: Review
        instruction: Review the input.
        node_timeout_ms: 60000
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-human-auto-start",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "auto_start_on_service_invocation": true,
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let commands = collaboration_runtime.start_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_id.as_deref(), Some("human_alice"));
    assert_eq!(
        commands[0]
            .authenticated_human
            .as_ref()
            .map(|human| (human.actor_id.as_str(), human.display_name.as_deref())),
        Some(("human_alice", Some("Alice")))
    );
}

#[tokio::test]
async fn post_groups_can_defer_initial_state_machine_run() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Deferred Initial Run
participants:
  writer:
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
          binding: writer
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
                        "id": "group-deferred-initial-run",
                        "driver_bot": "driver-bot",
                        "group_strategy": "state_machine",
                        "start_initial_run": false,
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

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(collaboration_runtime.configure_calls.lock().await.len(), 1);
    assert!(collaboration_runtime.start_commands.lock().await.is_empty());
}
#[tokio::test]
async fn start_bot_only_state_machine_run_without_human_identity_reaches_runtime() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
id: bot_only
version: 1
name: Bot Only
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
        final_output: true
"#;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-bot-only/state-machine-runs")
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
    let commands = collaboration_runtime.start_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert!(commands[0].caller_id.is_none());
    assert!(commands[0].authenticated_human.is_none());
}
#[tokio::test]
async fn human_state_machine_routes_forward_authenticated_actor_and_output() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime_and_human_identity().await;

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
    assert_eq!(pending_response.status(), StatusCode::OK);
    let pending_body = to_bytes(pending_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let pending_json: Value = serde_json::from_slice(&pending_body).unwrap();
    assert_eq!(pending_json[0]["node_id"], "human_review");
    assert!(pending_json[0].get("response_state").is_none());

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
    assert_eq!(respond_response.status(), StatusCode::OK);

    let pending_commands = collaboration_runtime.pending_human_commands.lock().await;
    assert_eq!(pending_commands.len(), 1);
    assert_eq!(pending_commands[0].run_id, "run-1");
    assert_eq!(pending_commands[0].caller_actor_id, "human_alice");
    drop(pending_commands);
    let respond_commands = collaboration_runtime.respond_human_commands.lock().await;
    assert_eq!(respond_commands.len(), 1);
    assert_eq!(respond_commands[0].run_id, "run-1");
    assert_eq!(respond_commands[0].node_id, "human_review");
    assert_eq!(respond_commands[0].caller_actor_id, "human_alice");
    assert_eq!(respond_commands[0].content, "looks good");
    assert_eq!(respond_commands[0].source, HumanResponseSource::Http);
}

#[tokio::test]
async fn rerun_state_machine_route_uses_path_run_as_source_and_returns_created_child() {
    let (app, _recorder, collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime_and_human_identity().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/state-machine-runs/run-1/reruns")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(body["run"]["run_id"], "run-2");
    assert_eq!(body["run"]["rerun_of"], "run-1");
    assert!(body.get("node_execution_metadata").is_none());
    assert_eq!(body["idempotent_replay"], false);
    let commands = collaboration_runtime.rerun_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].source_run_id, "run-1");
    assert_eq!(
        commands[0]
            .authenticated_human
            .as_ref()
            .map(|human| human.actor_id.as_str()),
        Some("human_alice")
    );
}
#[tokio::test]
async fn post_groups_with_state_machine_yaml_keeps_non_empty_body_participants() {
    let (app, recorder, _collaboration_runtime, _temp_dir) =
        test_app_with_collaboration_runtime().await;
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Body Participants Win
participants:
  speaker_a:
    bot_id: driver-bot
    required: true
  speaker_b:
    bot_id: target-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      speak_a:
        kind: bot_task
        display_name: Speak A
        assignee:
          type: bot_binding
          binding: speaker_a
        instruction: Say A.
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
                        "id": "group-sm-body",
                        "driver_bot": "driver-bot",
                        "participants": [
                            { "bot_uuid": "driver-bot" }
                        ],
                        "group_strategy": "state_machine",
                        "collaboration_definition_yaml": definition_yaml
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = recorder.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(
        calls[0]
            .participants
            .iter()
            .map(|p| (p.bot_id.as_str(), p.role.as_deref()))
            .collect::<Vec<_>>(),
        vec![("driver-bot", Some("driver"))]
    );
    assert_eq!(calls[0].member_bot_ids, vec!["driver-bot"]);
}
#[tokio::test]
async fn post_groups_dm_delegates_to_create_dm_with_human_caller() {
    let recorder = Arc::new(RecordingGroupManagement::default());
    let services = Services::builder()
        .group_management(recorder.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
    );

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "dm-1",
                        "group_kind": "dm",
                        "driver_bot": "target-bot",
                        "target_actor_id": "target-bot",
                        "topic": "Need help",
                        "context": "Human-Bot DM context",
                        "participants": []
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "dm-1");
    assert_eq!(json["driver_bot"], "target-bot");
    assert_eq!(json["group_kind"], "dm");
    assert_eq!(json["dm_pair_key"], "human_test|target-bot");
    assert_eq!(json["created"], true);
    assert_eq!(
        json["participants"],
        serde_json::json!(["human_alice", "target-bot"])
    );
    // Step 1: the DM projection surfaces `human_mention_notify_mode` even
    // though Dm human-mention delivery remains disabled — the field is
    // visible-but-inactive for Dm Groups.
    assert_eq!(json["human_mention_notify_mode"], "all");

    let create_calls = recorder.create_calls.lock().await;
    assert!(create_calls.is_empty());
    drop(create_calls);
    let dm_calls = recorder.create_dm_calls.lock().await;
    assert_eq!(dm_calls.len(), 1);
    let cmd = &dm_calls[0];
    assert_eq!(cmd.group_id.as_deref(), Some("dm-1"));
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(cmd.driver_bot.as_deref(), Some("target-bot"));
    assert_eq!(cmd.target_actor_id, "target-bot");
    assert_eq!(cmd.topic.as_deref(), Some("Need help"));
    assert_eq!(cmd.context.as_deref(), Some("Human-Bot DM context"));
}

#[tokio::test]
async fn post_groups_dm_leaves_driver_bot_policy_to_group_management() {
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
                        "id": "dm-1",
                        "group_kind": "dm",
                        "driver_bot": "legacy-driver",
                        "target_actor_id": "target-bot",
                        "participants": []
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let dm_calls = recorder.create_dm_calls.lock().await;
    assert_eq!(dm_calls.len(), 1);
    assert_eq!(dm_calls[0].driver_bot.as_deref(), Some("legacy-driver"));
}
#[path = "../../support/fixed_loop_projection.rs"]
mod fixed_loop_projection;
#[tokio::test]
async fn create_group_accepts_and_discards_backend_legacy_service_policy() {
    let (app, recorder, _temp_dir) = test_app().await;
    for policy in [serde_json::json!({"timeout_seconds":600, "max_concurrency":1, "callback_config":{"channels":[]}}), Value::Null] {
        let response = app.clone().oneshot(Request::builder().method("POST")
            .uri("/groups").header("content-type", "application/json")
            .header("authorization", "Bearer driver-token")
            .body(Body::from(serde_json::json!({"driver_bot":"driver-bot", "participants":[{"bot_uuid":"driver-bot","role":"driver"}], "service_spec":policy}).to_string())).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let body: Value = serde_json::from_slice(&body).unwrap();
        assert!(body.get("service_spec").is_none());
    }
    assert_eq!(recorder.create_calls.lock().await.len(), 2);
}
