    use super::*;
    use bcs_collaboration_store::MemoryCollaborationStore;
    use bcs_group_store::MemoryGroupRepo;
    use bcs_service_api::JudgeDecision;
    use bcs_session::SessionManagementServiceImpl;
    use bcs_session_store::MemorySessionRepo;
    use bcs_test_support::{NoopBotDeliveryPort, NoopGroupCoreService};

    #[derive(Debug)]
    struct UnusedJudge;

    #[async_trait]
    impl JudgeEvaluatorPort for UnusedJudge {
        async fn judge(&self, _request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
            unreachable!("missing-node retry check must not invoke the Judge")
        }
    }

    #[test]
    fn default_judge_timeout_is_ninety_seconds() {
        assert_eq!(DEFAULT_JUDGE_TIMEOUT_MS, 90_000);
    }

    #[test]
    fn default_opening_message_keeps_the_existing_state_machine_panel() {
        assert_eq!(
            default_state_machine_opening_message("run-1", Some("发布检查")),
            RenderedOpeningMessage {
                content: concat!(
                    "<AixUI\n",
                    "  type=\"panel\"\n",
                    "  component=\"bcsPanel.StateMachineRunView\"\n",
                    "  tab='{\"id\":\"state-machine-run-run-1\",\"title\":\"State Machine - 发布检查\",\"closable\":true}'\n",
                    "  params='{\"runId\":\"run-1\"}'\n",
                    "/>"
                )
                .to_string(),
                component: Some("bcsPanel.StateMachineRunView".to_string()),
            }
        );
    }

    #[test]
    fn one_shot_opening_message_is_run_scoped_for_chat_and_manager_worker_groups() {
        let run = StateMachineRun {
            run_id: "run-1".to_string(),
            root_run_id: Some("run-1".to_string()),
            rerun_of: None,
            definition_id: "one-shot".to_string(),
            definition_version: 1,
            group_id: "group-1".to_string(),
            group_version: 1,
            session_id: "session-1".to_string(),
            session_activation_count: None,
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: Value::Null,
            opening_message_override: None,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
        };
        for strategy in [GroupStrategy::Chat, GroupStrategy::ManagerWorker] {
            let mut group = Group::new("group-1", "driver", Vec::new());
            group.group_strategy = strategy;
            group.opening_message = Some(OpeningMessage::Text(
                "Session opening {{bcs.session_id}}".to_string(),
            ));

            assert_eq!(
                render_state_machine_opening_message(&group, &run, Some("一次性任务"))
                    .expect("render one-shot panel"),
                default_state_machine_opening_message("run-1", Some("一次性任务"))
            );

            let mut overridden_run = run.clone();
            overridden_run.opening_message_override = Some(
                serde_json::from_value(serde_json::json!({
                    "type": "panel",
                    "component": "bcsPanel.UndercoverGamePanel",
                    "params": {
                        "groupId": "{{bcs.group_id}}",
                        "sessionId": "{{bcs.session_id}}",
                        "runId": "{{bcs.run_id}}",
                        "phase": "speaking",
                        "round": 1
                    },
                    "tab": {
                        "id": "undercover-speak-r1-a1-{{bcs.run_id}}",
                        "title": "谁是卧底 · 第 1 轮发言",
                        "closable": true
                    }
                }))
                .expect("valid one-shot panel"),
            );
            let rendered =
                render_state_machine_opening_message(&group, &overridden_run, Some("一次性任务"))
                    .expect("render explicit one-shot panel");
            assert_eq!(rendered.component.as_deref(), Some("bcsPanel.UndercoverGamePanel"));
            assert!(rendered.content.contains("\"groupId\":\"group-1\""));
            assert!(rendered.content.contains("\"sessionId\":\"session-1\""));
            assert!(rendered.content.contains("\"runId\":\"run-1\""));
            assert!(rendered.content.contains("undercover-speak-r1-a1-run-1"));
            assert!(!rendered.content.contains("Session opening"));
        }
    }

    #[test]
    fn authoring_yaml_rejects_top_level_identity() {
        let yaml = r#"
api_version: bcs.collaboration/v1
id: user-owned
name: Bad Definition
runtime:
  kind: chat
"#;
        let err = reject_authoring_yaml_identity(yaml).unwrap_err();
        assert!(matches!(
            err,
            CollaborationRuntimeError::InvalidDefinition(_)
        ));
    }

    #[test]
    fn build_node_runs_treats_zero_timeout_as_disabled() {
        let yaml = r#"
api_version: bcs.collaboration/v1
id: def-timeout
version: 1
name: Timeout Definition
participants:
  worker:
    bot_id: bot-a
runtime:
  kind: state_machine
  state_machine:
    version: 1
    defaults:
      node_timeout_ms: 0
    nodes:
      step:
        kind: bot_task
        display_name: Step
        assignee:
          type: bot_binding
          binding: worker
        instruction: Do the work.
        final_output: true
"#;
        let definition: CollaborationDefinition = serde_yaml::from_str(yaml).unwrap();
        let compiled = validate_definition(definition).unwrap();
        let run = StateMachineRun {
            run_id: "run-timeout-zero".to_string(),
            root_run_id: Some("run-timeout-zero".to_string()),
            rerun_of: None,
            definition_id: compiled.definition.id.clone(),
            definition_version: compiled.definition.version,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            session_activation_count: None,
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: Value::Null,
            opening_message_override: None,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
        };
        let mut resolved = BTreeMap::new();
        resolved.insert(
            "worker".to_string(),
            ResolvedParticipantBinding {
                source: "definition_legacy_bot_id".to_string(),
                binding_source: None,
                bot_ids: vec!["bot-a".to_string()],
                participants: vec![ResolvedParticipant {
                    bot_id: "bot-a".to_string(),
                    bcs_participant_role: ParticipantRole::Driver,
                }],
                extensions: Default::default(),
            },
        );
        let nodes = build_node_runs(&compiled, &run, &resolved).unwrap();
        assert_eq!(nodes.len(), 1);
        assert_eq!(nodes[0].node_timeout_ms, None);
        assert_eq!(nodes[0].attempt, 0);
    }

    #[test]
    fn runtime_cleanup_session_limit_is_sqlite_representable() {
        assert!(RUNTIME_CLEANUP_SESSION_LIMIT <= i64::MAX as u64);
    }

    #[tokio::test]
    async fn retry_check_rejects_a_missing_persisted_node() {
        let store = Arc::new(MemoryCollaborationStore::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            Arc::new(MemoryGroupRepo::new()),
        ));
        let runtime = CollaborationRuntime::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store,
            Arc::new(NoopGroupCoreService),
            sessions,
            Arc::new(NoopBotDeliveryPort),
            Arc::new(UnusedJudge),
        );
        let definition: CollaborationDefinition = serde_yaml::from_str(
            r#"
api_version: bcs.collaboration/v1
id: missing-node
version: 1
name: Missing Node
participants:
  worker:
    bot_id: bot-a
runtime:
  kind: state_machine
  state_machine:
    version: 1
    nodes:
      step:
        kind: bot_task
        display_name: Step
        assignee:
          type: bot_binding
          binding: worker
        instruction: Do the work.
        final_output: true
"#,
        )
        .unwrap();
        let compiled = validate_definition(definition).unwrap();
        let run = StateMachineRun {
            run_id: "run-missing-node".to_string(),
            root_run_id: Some("run-missing-node".to_string()),
            rerun_of: None,
            definition_id: compiled.definition.id.clone(),
            definition_version: compiled.definition.version,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            session_activation_count: None,
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: Value::Null,
            opening_message_override: None,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
        };
        runtime
            .runs
            .create_run(run.clone(), Vec::new())
            .await
            .unwrap();
        let group = Group::new("group", "owner", Vec::new());

        let error = runtime
            .resume_failed_node(
                &compiled,
                &group,
                &run,
                "missing",
                0,
            )
            .await
            .expect_err("missing persisted node must prevent retry scheduling");

        assert!(matches!(
            error,
            CollaborationRuntimeError::NodeNotFound { run_id, node_id }
                if run_id == "run-missing-node" && node_id == "missing"
        ));
    }
