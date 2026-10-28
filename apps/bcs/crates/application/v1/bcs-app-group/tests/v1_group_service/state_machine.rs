use super::*;

#[tokio::test]
async fn state_machine_create_without_runtime_fails_before_persisting_group() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["worker".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(result, Err(ApplicationError::Internal(_))));
    assert_eq!(fixture.groups.count().await, 0);
}
#[tokio::test]
async fn state_machine_create_rejects_duplicate_participant_binding_names() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["worker".into()],
                            },
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["driver".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. })
            if code == "invalid_participant_binding"
    ));
    assert_eq!(fixture.groups.count().await, 0);
    assert!(runtime.configured.lock().expect("runtime lock").is_none());
}
#[tokio::test]
async fn state_machine_runtime_failure_rolls_back_created_group() {
    let runtime = Arc::new(RecordingRuntime::default());
    *runtime.configure_error.lock().expect("runtime lock") =
        Some("invalid runtime configuration".to_string());
    let fixture = Fixture::new_with_runtime(runtime).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. }) if code == "invalid_request"
    ));
    assert_eq!(fixture.groups.count().await, 0);
}
#[tokio::test]
async fn state_machine_create_configures_runtime_and_returns_typed_detail() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: Some("Execute the workflow".into()),
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 3,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["worker".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine Group");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    let CollaborationConfiguration::StateMachine(collaboration) = detail.collaboration else {
        panic!("expected state-machine collaboration");
    };
    match &collaboration.definition {
        bcs_service_api::application::v1::StateMachineDefinition::Reference(reference) => {
            assert_eq!(reference.definition_id, "definition-1");
            assert_eq!(reference.version, 3);
        }
        other => panic!("expected definition reference, got {other:?}"),
    }
    assert_eq!(collaboration.participant_bindings[0].binding, "worker");
    assert_eq!(
        collaboration.participant_bindings[0].actor_ids,
        vec!["worker"]
    );

    {
        let configured = runtime.configured.lock().expect("runtime lock");
        let configured = configured.as_ref().expect("configured runtime");
        assert!(configured.auto_start_on_service_invocation);
        assert_eq!(
            configured
                .definition_ref
                .as_ref()
                .expect("definition ref")
                .id,
            "definition-1"
        );
        assert_eq!(
            configured
                .participant_bindings
                .get("worker")
                .expect("worker binding")
                .bot_ids,
            vec!["worker"]
        );
        assert_eq!(
            configured
                .participant_bindings
                .get("worker")
                .expect("worker binding")
                .source,
            "manual"
        );
    }

    let started = runtime.started.lock().expect("runtime lock");
    assert_eq!(started.len(), 1);
    assert_eq!(started[0].group_id, detail.group_id);
    assert!(started[0].session_id.is_some());
    assert_eq!(started[0].caller_id.as_deref(), Some("human_driver"));
}
#[tokio::test]
async fn state_machine_create_with_inline_yaml_returns_persisted_definition_ref() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Content(
                                bcs_service_api::application::v1::StateMachineDefinitionContent {
                                    content_yaml: "version: 1\n".into(),
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine Group from inline YAML");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    let CollaborationConfiguration::StateMachine(collaboration) = detail.collaboration else {
        panic!("expected state-machine collaboration");
    };
    match collaboration.definition {
        bcs_service_api::application::v1::StateMachineDefinition::Reference(reference) => {
            assert_eq!(reference.definition_id, "generated-definition");
            assert_eq!(reference.version, 1);
        }
        other => panic!("expected persisted definition reference, got {other:?}"),
    }

    let configured = runtime.configured.lock().expect("runtime lock");
    let configured = configured.as_ref().expect("configured runtime");
    assert_eq!(configured.definition_yaml.as_deref(), Some("version: 1\n"));
    assert!(configured.definition_ref.is_none());
}
#[tokio::test]
async fn state_machine_create_defers_initial_run_until_required_channel_is_bound() {
    let runtime = Arc::new(RecordingRuntime {
        requires_human_input_channel: true,
        ..Default::default()
    });
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    for bot in ["driver", "worker"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("Human review".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "bot-human-bot-review".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create channel-backed StateMachine Group");

    assert!(matches!(detail, GroupDetail::Collaboration(_)));
    assert!(runtime.started.lock().expect("runtime lock").is_empty());
    assert_eq!(fixture.groups.count().await, 1);
}
#[tokio::test]
async fn state_machine_create_rejects_human_actors_in_bot_bindings() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture
        .bots
        .ensure_human_actor("staff-1", "Alice")
        .await
        .expect("register human actor");

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "human_staff-1".into(),
                    role: ParticipantRole::Observer,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: vec![
                            bcs_service_api::application::v1::StateMachineParticipantBinding {
                                binding: "worker".into(),
                                actor_ids: vec!["human_staff-1".into()],
                            },
                        ],
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(
        result,
        Err(ApplicationError::InvalidInput { code, .. })
            if code == "invalid_participant_binding"
    ));
    assert_eq!(fixture.groups.count().await, 0);
    assert!(runtime.configured.lock().expect("runtime lock").is_none());
}
#[tokio::test]
async fn state_machine_create_preserves_authenticated_human_in_audit_and_start() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: human_principal_with_profile("staff-1", "alice", Some("Alice"), None),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: Some("Review the release".into()),
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine group");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.originator_actor_id, "human_staff-1");

    let sessions = fixture
        .sessions
        .list_by_group(&detail.group_id, None, 0, 10, None, None)
        .await
        .expect("list initial sessions");
    assert_eq!(sessions.len(), 1);
    assert_eq!(
        sessions[0].caller_principal.as_deref(),
        Some("human_staff-1")
    );

    let started = runtime.started.lock().expect("runtime lock");
    assert_eq!(started.len(), 1);
    assert_eq!(started[0].caller_id.as_deref(), Some("human_staff-1"));
    assert_eq!(
        started[0]
            .authenticated_human
            .as_ref()
            .map(|human| (human.actor_id.as_str(), human.display_name.as_deref())),
        Some(("human_staff-1", Some("Alice")))
    );
}
#[tokio::test]
async fn state_machine_create_does_not_reread_runtime_for_its_response() {
    let runtime = Arc::new(RecordingRuntime::default());
    *runtime
        .projection_error
        .lock()
        .expect("projection error lock") = Some("transient projection failure".into());
    let fixture = Fixture::new_with_runtime(runtime).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(result, Ok(GroupDetail::Collaboration(_))));
}
#[tokio::test]
async fn state_machine_start_failure_removes_runtime_session_and_group() {
    let runtime = Arc::new(RecordingRuntime::default());
    *runtime.start_error.lock().expect("runtime lock") = Some("dispatch failed".into());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;

    let result = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await;

    assert!(matches!(result, Err(ApplicationError::InvalidInput { .. })));
    assert_eq!(fixture.groups.count().await, 0);
    let group_id = runtime
        .configured
        .lock()
        .expect("runtime lock")
        .as_ref()
        .expect("configured runtime")
        .group_id
        .clone();
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list sessions")
            .is_empty()
    );
    assert_eq!(
        runtime.cancelled_groups.lock().expect("runtime lock").len(),
        1
    );
    assert_eq!(
        runtime
            .deleted_group_state
            .lock()
            .expect("runtime lock")
            .len(),
        1
    );
}
#[tokio::test]
async fn deleting_state_machine_group_cancels_runs_and_removes_runtime_state() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture.add_public_bot("worker").await;
    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("driver"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("State machine".into()),
                context: None,
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "worker".into(),
                    role: ParticipantRole::Consultant,
                    tags: Vec::new(),
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::StateMachine(
                    bcs_service_api::application::v1::StateMachineConfiguration {
                        definition:
                            bcs_service_api::application::v1::StateMachineDefinition::Reference(
                                bcs_service_api::application::v1::StateMachineDefinitionReference {
                                    definition_id: "definition-1".into(),
                                    version: 1,
                                },
                            ),
                        participant_bindings: Vec::new(),
                    },
                ),
            }),
        })
        .await
        .expect("create state-machine group");
    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };

    let deleted = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: detail.group_id.clone(),
            acting_bot_id: None,
        })
        .await
        .expect("delete state-machine group");

    assert!(deleted.deleted);
    assert_eq!(
        runtime
            .cancelled_groups
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &[detail.group_id.clone()]
    );
    assert_eq!(
        runtime
            .deleted_group_state
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &[detail.group_id]
    );
}
#[tokio::test]
async fn failed_binding_cleanup_keeps_deletion_and_cleans_state_machine_runtime() {
    let runtime = Arc::new(RecordingRuntime::default());
    let fixture = Fixture::new_with_runtime_and_failing_channel_cleanup(runtime.clone()).await;
    fixture.add_public_bot("driver").await;
    fixture
        .groups
        .upsert(normal_group(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
            GroupStrategy::StateMachine,
            1,
        ))
        .await
        .expect("store StateMachine Group");

    let error = fixture
        .service
        .delete(DeleteGroup {
            caller: bot_principal("driver"),
            group_id: "group-1".into(),
            acting_bot_id: None,
        })
        .await
        .expect_err("binding cleanup failure must fail deletion");

    assert!(matches!(error, ApplicationError::Internal(_)));
    assert!(fixture.groups.get("group-1").await.is_none());
    assert_eq!(
        runtime
            .cancelled_groups
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &["group-1".to_string()]
    );
    assert_eq!(
        runtime
            .deleted_group_state
            .lock()
            .expect("runtime lock")
            .as_slice(),
        &["group-1".to_string()]
    );
}
