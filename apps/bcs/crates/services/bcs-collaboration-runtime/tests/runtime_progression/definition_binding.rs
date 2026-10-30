use super::{
    ActorKind,
    Arc,
    BTreeMap,
    CollaborationDefinition,
    CollaborationDefinitionRef,
    CollaborationRuntime,
    CollaborationRuntimeError,
    CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand,
    CountingDefinitionRepo,
    DefinitionYamlSource,
    GroupCoreService,
    GroupRuntimeBindingRepoPort,
    GroupStore,
    MemoryCollaborationStore,
    MemoryGroupRepo,
    MemoryMessageRepo,
    MemorySessionRepo,
    MessageOwnerFilter,
    MessageQuery,
    MessageRepoPort,
    MessageViewScope,
    Participant,
    ParticipantMode,
    ParticipantRole,
    PatchGroupCollaborationDefinitionCommand,
    RecordingDelivery,
    RecordingSessionChannelOutbound,
    RuntimeParticipantBinding,
    STATE_MACHINE_PANEL_MESSAGE_TYPE,
    SessionManagementServiceImpl,
    StartStateMachineRunCommand,
    StateMachineDefinitionRepoPort,
    ValidateCollaborationDefinitionYamlCommand,
    Value,
    assert_inferred_default_requires,
    human_input_yaml,
    json,
    noop_judge,
    single_node_authoring_yaml,
    single_node_template_yaml,
    single_node_yaml,
    state_machine_test_group,
    test_group,
    test_sessions
};

#[tokio::test]
async fn fixed_loop_preview_honors_server_limits_and_cannot_start_or_configure_v2() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.unwrap();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(), store.clone(), store.clone(), store.clone(), group,
        test_sessions(), delivery.clone(), noop_judge(),
    ).with_fixed_loop_limits(bcs_config_api::FixedLoopLimits {
        max_fixed_loop_iterations: 2,
        ..Default::default()
    });
    let yaml = include_str!("../fixtures/fixed_loop.yaml");
    let command = || ValidateCollaborationDefinitionYamlCommand {
        definition_yaml: yaml.into(), judge_available: true,
    };
    assert!(!runtime.validate_definition_yaml(command()).await.unwrap().valid);
    let runtime = runtime.with_fixed_loop_limits(Default::default());
    assert!(runtime.validate_definition_yaml(command()).await.unwrap().valid);
    let configured = runtime.configure_group_runtime(ConfigureGroupRuntimeCommand {
        group_id: "group-1".into(), definition_yaml: Some(yaml.into()), definition: None,
        definition_ref: None, participant_bindings: Default::default(), auto_start_on_service_invocation: true,
    }).await;
    assert!(matches!(configured, Err(CollaborationRuntimeError::InvalidDefinition(_))));
    assert!(GroupRuntimeBindingRepoPort::get(store.as_ref(), "group-1").await.unwrap().is_none());
    let started = runtime.start_state_machine_run(StartStateMachineRunCommand {
        group_id: "group-1".into(), session_id: None, definition_yaml: Some(yaml.into()),
        definition: None, definition_ref: None, participant_bindings: None, opening_message_override: None,
        input: json!({}), caller_id: None, authenticated_human: None,
        caller_association: None,
    }).await;
    assert!(matches!(started, Err(CollaborationRuntimeError::InvalidDefinition(_))));
    assert!(delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn start_run_uses_group_default_definition_binding() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let configured = runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");

    assert_eq!(
        configured.default_definition.expect("definition").id,
        "single_node"
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "use binding"}),
            caller_id: None,
            authenticated_human: None, caller_association: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.run.definition_id, "single_node");
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn configure_im_definition_defers_channel_validation_until_run_start() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let session_repo = Arc::new(MemorySessionRepo::new());
    let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), Arc::new(MemoryGroupRepo::new())));
    let store = Arc::new(MemoryCollaborationStore::new().with_session_repo(session_repo));
    let channel_outbound = Arc::new(RecordingSessionChannelOutbound::default());
    *channel_outbound.validation_error.lock().await =
        Some("no active dingtalk ChannelBinding exists".to_string());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_session_channel_outbound(channel_outbound.clone());

    let configured = runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configuration must not require a binding that needs the group id");
    assert!(configured.requires_human_input_channel);
    assert!(channel_outbound.validation_calls.lock().await.is_empty());

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: Value::Null,
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect_err("run start must still enforce the active binding");
    assert!(
        error
            .to_string()
            .contains("no active dingtalk ChannelBinding")
    );
    assert_eq!(
        channel_outbound.validation_calls.lock().await.as_slice(),
        &[("group-1".to_string(), "dingtalk".to_string())]
    );
}

#[tokio::test]
async fn group_collaboration_definition_get_and_patch_preserve_source_yaml() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let no_definition = runtime
        .get_group_collaboration_definition("group-1")
        .await
        .expect("get no definition");
    assert_eq!(
        no_definition.yaml_source,
        DefinitionYamlSource::NoDefinition
    );
    assert!(no_definition.default_definition.is_none());

    let source_yaml = single_node_authoring_yaml("Source One");
    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(source_yaml.clone()),
            definition: None,
            definition_ref: None,
            participant_bindings: BTreeMap::new(),
            auto_start_on_service_invocation: false,
        })
        .await
        .expect("configure group runtime");
    let current = runtime
        .get_group_collaboration_definition("group-1")
        .await
        .expect("get source definition");
    assert_eq!(current.yaml_source, DefinitionYamlSource::Original);
    assert_eq!(
        current.definition_yaml.as_deref(),
        Some(source_yaml.as_str())
    );
    let base = current
        .default_definition
        .clone()
        .expect("default definition");

    let patched_yaml = single_node_authoring_yaml("Source Two");
    let patched = runtime
        .patch_group_collaboration_definition(PatchGroupCollaborationDefinitionCommand {
            group_id: "group-1".to_string(),
            base_definition: base.clone(),
            definition_yaml: patched_yaml.clone(),
            participant_bindings: None,
        })
        .await
        .expect("patch definition");
    let next = patched.default_definition.expect("patched definition ref");
    assert_eq!(next.id, base.id);
    assert_eq!(next.version, base.version + 1);
    assert_eq!(patched.yaml_source, DefinitionYamlSource::Original);
    assert_eq!(
        patched.definition_yaml.as_deref(),
        Some(patched_yaml.as_str())
    );
}

#[tokio::test]
async fn group_collaboration_definition_get_generates_legacy_authoring_yaml_without_identity() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let definition: CollaborationDefinition =
        serde_yaml::from_str(&single_node_yaml()).expect("legacy definition yaml");
    runtime
        .upsert_definition(definition)
        .await
        .expect("upsert legacy normalized definition");
    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: None,
            definition: None,
            definition_ref: Some(CollaborationDefinitionRef {
                id: "single_node".to_string(),
                version: 1,
            }),
            participant_bindings: BTreeMap::new(),
            auto_start_on_service_invocation: false,
        })
        .await
        .expect("bind legacy definition");

    let current = runtime
        .get_group_collaboration_definition("group-1")
        .await
        .expect("get legacy definition");
    assert_eq!(
        current.yaml_source,
        DefinitionYamlSource::GeneratedNormalized
    );
    assert_eq!(
        current.definition.as_ref().expect("definition").id,
        "single_node"
    );
    let base = current.default_definition.clone().expect("definition ref");
    assert_eq!(
        base,
        CollaborationDefinitionRef {
            id: "single_node".to_string(),
            version: 1,
        }
    );
    let generated_yaml = current.definition_yaml.expect("generated yaml");
    let generated_value: serde_yaml::Value =
        serde_yaml::from_str(&generated_yaml).expect("generated yaml should parse");
    let keys: Vec<&str> = generated_value
        .as_mapping()
        .expect("generated yaml root mapping")
        .keys()
        .filter_map(|key| key.as_str())
        .collect();
    assert!(!keys.contains(&"id"));
    assert!(!keys.contains(&"version"));
    assert!(!keys.contains(&"api_version"));
    assert!(!keys.contains(&"requires"));
    assert!(!keys.contains(&"metadata"));
    assert!(!keys.contains(&"extensions"));
    let root = generated_value
        .as_mapping()
        .expect("generated yaml root mapping");
    let state_machine = root
        .get("runtime")
        .and_then(serde_yaml::Value::as_mapping)
        .and_then(|runtime| runtime.get("state_machine"))
        .and_then(serde_yaml::Value::as_mapping)
        .expect("state machine mapping");
    assert!(!state_machine.contains_key("version"));
    assert!(!state_machine.contains_key("graph_mode"));
    assert!(!state_machine.contains_key("projection"));
    assert!(!state_machine.contains_key("variables"));
    assert!(!state_machine.contains_key("events"));
    assert!(!state_machine.contains_key("extensions"));
    let defaults = state_machine
        .get("defaults")
        .and_then(serde_yaml::Value::as_mapping)
        .expect("non-default defaults should remain");
    assert_eq!(
        defaults
            .get("max_attempts")
            .and_then(serde_yaml::Value::as_i64),
        Some(2)
    );
    assert_eq!(
        defaults
            .get("node_timeout_ms")
            .and_then(serde_yaml::Value::as_i64),
        Some(120000)
    );
    let driver = root
        .get("participants")
        .and_then(serde_yaml::Value::as_mapping)
        .and_then(|participants| participants.get("driver"))
        .and_then(serde_yaml::Value::as_mapping)
        .expect("driver participant mapping");
    assert!(!driver.contains_key("extensions"));
    assert_eq!(
        driver.get("bot_id").and_then(serde_yaml::Value::as_str),
        Some("driver-bot")
    );
    assert_eq!(
        driver.get("required").and_then(serde_yaml::Value::as_bool),
        Some(true)
    );

    let patched = runtime
        .patch_group_collaboration_definition(PatchGroupCollaborationDefinitionCommand {
            group_id: "group-1".to_string(),
            base_definition: base,
            definition_yaml: generated_yaml,
            participant_bindings: None,
        })
        .await
        .expect("patch generated legacy authoring yaml");
    assert_eq!(
        patched
            .default_definition
            .expect("patched definition")
            .version,
        2
    );
}

#[tokio::test]
async fn start_run_from_group_binding_does_not_upsert_persisted_definition() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let backing_store = Arc::new(MemoryCollaborationStore::new());
    let definitions = Arc::new(CountingDefinitionRepo::new(backing_store.clone()));
    let delivery = Arc::new(RecordingDelivery::default());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let runtime = test_runtime!(
        definitions.clone(),
        backing_store.clone(),
        backing_store.clone(),
        backing_store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    )
    .with_message_repo(message_repo.clone());

    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: Default::default(),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");
    assert_eq!(definitions.upsert_calls().await, 1);

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "use binding without rewriting definition"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.run.definition_id, "single_node");
    assert_eq!(
        definitions.upsert_calls().await,
        1,
        "group binding runs must not rewrite persisted definition rows"
    );
    let snapshot =
        StateMachineDefinitionRepoPort::get_run_snapshot(&*definitions, &started.view.run.run_id)
            .await
            .expect("get run snapshot")
            .expect("run snapshot");
    assert_inferred_default_requires(&snapshot.definition);
    assert_eq!(delivery.commands.lock().await.len(), 1);
    let panels = message_repo
        .query_messages(MessageQuery {
            group_id: "group-1".to_string(),
            session_id: started.view.run.session_id.clone(),
            cursor: None,
            limit: 10,
            keyword: None,
            sender_id: None,
            message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
            owner_filter: MessageOwnerFilter::Any,
            time_range: None,
            visible_from_seq: None,
            human_view: None,
        })
        .await
        .expect("query generic run panel anchors");
    assert_eq!(panels.messages.len(), 1);
    assert_eq!(
        panels.messages[0].client_msg_id.as_deref(),
        Some(format!("{}:000-panel", started.view.run.run_id).as_str())
    );
}

#[tokio::test]
async fn start_run_uses_group_participant_bindings_for_template_definition() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_template_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: BTreeMap::from([(
                "driver".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["driver-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "use participant binding"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(
        started.view.nodes[0].assignee_bot_id.as_deref(),
        Some("driver-bot")
    );
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn start_run_rejects_multi_bot_slot_with_current_single_assignee_runtime() {
    let group = Arc::new(GroupStore::new());
    let mut seeded_group = test_group();
    seeded_group.participants.push(Participant {
        bot_uuid: "reviewer-bot".to_string(),
        bot_name: Some("Reviewer".to_string()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    });
    group.upsert(seeded_group).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery,
        noop_judge(),
    );

    runtime
        .configure_group_runtime(ConfigureGroupRuntimeCommand {
            group_id: "group-1".to_string(),
            definition_yaml: Some(single_node_template_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: BTreeMap::from([(
                "driver".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["driver-bot".to_string(), "reviewer-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            auto_start_on_service_invocation: true,
        })
        .await
        .expect("configure group runtime");

    let error = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "multi"}),
            caller_id: None,
            authenticated_human: None,
            caller_association: None,
        })
        .await
        .expect_err("multi bot slot is not supported by the current single-assignee runtime");

    assert!(error.to_string().contains("exactly one bot"));
}
