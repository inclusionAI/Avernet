use std::{
    collections::BTreeMap,
    future::Future,
    io::{self, Write},
    sync::{Arc, OnceLock},
    time::Duration,
};

use async_trait::async_trait;
use bcs_collaboration_runtime::CollaborationRuntime;
use bcs_collaboration_store::MemoryCollaborationStore;
use bcs_domain::{
    ActorKind, CollaborationDefinition, CollaborationDefinitionRef, Group, GroupMessageType,
    MessageRole, Participant, ParticipantMode, ParticipantRole, ResolvedParticipantBinding,
    RuntimeParticipantBinding, StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_group::GroupStore;
use bcs_group_store::MemoryGroupRepo;
use bcs_protocol::{BcsFrame, ChatSendParams};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, ChatEventState,
    CollaborationEventRepoPort, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    DefinitionYamlSource, FrontendDeliveryCommand,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget, GroupCoreService,
    CallbackChannelConfig, CallbackConfig, PatchGroupCollaborationDefinitionCommand,
    ServiceResult, ServiceSpec, SessionManagementService, StartStateMachineRunCommand,
    StateMachineDefinitionRepoPort,
    JudgeDecision, JudgeEvaluatorPort, JudgeRequest, ServiceError
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use serde_json::{Value, json};
use tokio::sync::Mutex;

#[derive(Clone, Default)]
struct SharedLogBuffer(Arc<std::sync::Mutex<Vec<u8>>>);

struct SharedLogWriter {
    buffer: Arc<std::sync::Mutex<Vec<u8>>>,
}

impl Write for SharedLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buffer.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter {
            buffer: self.0.clone(),
        }
    }
}

async fn capture_tracing_logs<Fut, T>(future: Fut) -> (T, String)
where
    Fut: Future<Output = T>,
{
    static BUFFER: OnceLock<SharedLogBuffer> = OnceLock::new();
    let buffer = BUFFER
        .get_or_init(|| {
            let buffer = SharedLogBuffer::default();
            let subscriber = tracing_subscriber::fmt()
                .with_ansi(false)
                .with_level(false)
                .with_target(true)
                .with_writer(buffer.clone())
                .finish();
            tracing::subscriber::set_global_default(subscriber)
                .expect("install tracing subscriber");
            buffer
        })
        .clone();
    buffer.0.lock().unwrap().clear();
    let output = future.await;
    let logs = String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap();
    (output, logs)
}

fn test_sessions() -> Arc<SessionManagementServiceImpl> {
    Arc::new(SessionManagementServiceImpl::new(
        Arc::new(MemorySessionRepo::new()),
        Arc::new(MemoryGroupRepo::new()),
    ))
}

fn assert_inferred_default_requires(definition: &CollaborationDefinition) {
    let requires = definition.requires.as_ref().expect("requires should be inferred");
    assert!(requires.server_features.contains(&"state_machine.graph_mode.acyclic".to_string()));
    assert!(requires.server_features.contains(&"state_machine.node.kind.bot_task".to_string()));
    assert!(requires.server_features.contains(&"state_machine.transitions.complete".to_string()));
    assert!(requires.bot_runtime_features.contains(&"delivery.chat_send_task_compat".to_string()));
}

#[tokio::test]
async fn single_node_run_completes_session_with_bot_final_text() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    ).with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "review this"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    assert_eq!(started.view.nodes.len(), 1);
    assert_eq!(started.view.nodes[0].node_timeout_ms, Some(60_000));
    assert_eq!(started.view.nodes[0].max_attempts, 3);
    let persisted_definition = StateMachineDefinitionRepoPort::get(&*store, "single_node", 1)
        .await
        .expect("get persisted definition")
        .expect("persisted definition");
    assert_inferred_default_requires(&persisted_definition);
    let snapshot = StateMachineDefinitionRepoPort::get_run_snapshot(
        &*store,
        &started.view.run.run_id,
    )
    .await
    .expect("get run snapshot")
    .expect("run snapshot");
    assert_inferred_default_requires(&snapshot);
    let command = delivery.commands.lock().await[0].clone();
    let params = chat_send_params(&command);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 1);
    assert!(matches!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session { ref session_id }
            if session_id == &started.view.run.session_id
    ));
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    assert_eq!(panel_event["event"].as_str(), Some("chat"));
    assert_eq!(panel_event["bot_uuid"].as_str(), Some("bcs_state_machine"));
    assert_eq!(
        panel_event["payload"]["run_id"].as_str(),
        Some(started.view.run.run_id.as_str())
    );
    assert_eq!(panel_event["payload"]["role"].as_str(), Some("assistant"));
    assert_eq!(panel_event["payload"]["message_type"].as_str(), Some("bot"));
    assert_eq!(
        panel_event["payload"]["bot_name"].as_str(),
        Some("BCS State Machine")
    );
    assert_eq!(
        panel_event["payload"]["message"]["role"].as_str(),
        Some("assistant")
    );
    assert_eq!(
        panel_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    let panel_text = panel_event["payload"]["message"]["content"][0]["text"]
        .as_str()
        .expect("panel text");
    assert!(panel_text.contains("<AixUI"));
    assert!(panel_text.contains("type=\"panel\""));
    assert!(panel_text.contains("params='"));
    assert!(panel_text.contains("bcsPanel.StateMachineRunView"));
    assert!(panel_text.contains(&format!(
        "state-machine-run-{}",
        started.view.run.run_id
    )));
    assert!(panel_text.contains("State Machine - Single Node"));
    drop(frontend_commands);
    assert_eq!(params.bcs_group_id, started.view.run.session_id);
    assert_eq!(params.bcs_session_id, None);
    assert_eq!(
        params.session_context.session_id,
        started.view.run.session_id
    );
    let correlation = runtime
        .lookup_delivery_correlation(&command.run_id)
        .await
        .expect("lookup")
        .expect("correlation");
    assert_eq!(correlation.node_id, "answer");
    let delivery_run_id = command.run_id.clone();

    let delta = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id.clone(),
                "bcs_group_id": "group-1",
                "state": "delta",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "draft"}]
                }
            }),
            state: ChatEventState::Delta,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle delta");
    assert!(delta.consumed);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 2);
    let delta_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("delta event json");
    assert_eq!(delta_event["event"].as_str(), Some("chat"));
    assert_eq!(delta_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        delta_event["payload"]["state"].as_str(),
        Some("delta")
    );
    assert_eq!(
        delta_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("draft")
    );
    drop(frontend_commands);
    let delta_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "chat.event",
    )
    .await
    .expect("list raw delta events");
    assert!(delta_events.is_empty());
    let bot_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.bot_event",
    )
    .await
    .expect("list compact bot events");
    assert!(bot_events.is_empty());

    let handled = runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.clone(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": "ignored-by-runtime-test",
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "final answer"}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(started.view.run.session_id.clone()),
        })
        .await
        .expect("handle final");

    assert!(handled.consumed);
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 3);
    assert!(matches!(
        frontend_commands[2].target,
        FrontendDeliveryTarget::Session { ref session_id }
            if session_id == &started.view.run.session_id
    ));
    let bot_event: Value =
        serde_json::from_str(&frontend_commands[2].event_json).expect("bot event json");
    assert_eq!(bot_event["event"].as_str(), Some("chat"));
    assert_eq!(bot_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        bot_event["payload"]["bcs_session_id"].as_str(),
        Some(started.view.run.session_id.as_str())
    );
    assert_eq!(
        bot_event["payload"]["run_id"].as_str(),
        Some("ignored-by-runtime-test")
    );
    assert_eq!(
        bot_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("final answer")
    );
    let view = handled.view.expect("run view");
    assert_eq!(view.run.output.as_deref(), Some("final answer"));
    let raw_final_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "chat.event",
    )
    .await
    .expect("list raw final events");
    assert!(raw_final_events.is_empty());
    let bot_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "answer",
        "state_machine.node.bot_event",
    )
    .await
    .expect("list compact bot events");
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].attempt, Some(0));
    assert_eq!(bot_events[0].payload["state"].as_str(), Some("final"));
    assert_eq!(
        bot_events[0].payload["source_event_type"].as_str(),
        Some("chat.event")
    );
    assert_eq!(bot_events[0].payload["text_len"].as_u64(), Some(12));
    assert!(bot_events[0].payload.get("message").is_none());
    let session = sessions
        .get(&started.view.run.session_id)
        .await
        .expect("get session")
        .expect("session");
    assert_eq!(session.output, Some(json!("final answer")));
    let history = runtime
        .get_state_machine_session_history(&started.view.run.session_id, 50, None)
        .await
        .expect("history")
        .expect("state-machine history");
    assert_eq!(history.messages.len(), 2);
    assert_eq!(history.messages[0].sender, "bcs_state_machine");
    assert_eq!(history.messages[0].message_type, GroupMessageType::Bot);
    assert_eq!(history.messages[0].role, MessageRole::Assistant);
    assert_eq!(
        history.messages[0].bot_name.as_deref(),
        Some("BCS State Machine")
    );
    assert!(history.messages[0].content.contains("<AixUI"));
    assert!(history.messages[0].content.contains("type=\"panel\""));
    assert!(history.messages[0].content.contains("params='"));
    assert!(history.messages[0].content.contains(&format!(
        "\"runId\":\"{}\"",
        started.view.run.run_id
    )));
    assert_eq!(history.messages[1].sender, "driver-bot");
    assert_eq!(history.messages[1].bot_name.as_deref(), Some("Driver"));
    assert_eq!(history.messages[1].role, MessageRole::Assistant);
    assert_eq!(history.messages[1].message_type, GroupMessageType::Bot);
    assert_eq!(history.messages[1].content, "final answer");
    assert_eq!(
        history.messages[1]
            .metadata
            .as_ref()
            .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
        Some("output")
    );
}

#[tokio::test]
async fn start_run_fails_and_marks_node_failed_when_delivery_returns_not_delivered() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RejectingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let result = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "review this"}),
            caller_id: None,
        })
        .await;

    let error = result.err().expect("delivery rejection should fail start");
    assert!(error.to_string().contains("state-machine node delivery failed"));
    let commands = delivery.commands.lock().await;
    let delivery_id = commands[0].run_id.clone();
    drop(commands);
    let run_id = delivery_id
        .strip_prefix("smnode-")
        .and_then(|value| value.strip_suffix("-answer-0"))
        .expect("state-machine delivery id should include run id");
    let view = runtime
        .get_state_machine_run(run_id)
        .await
        .expect("get failed run")
        .expect("failed run should be persisted");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(view.nodes[0].status, StateMachineNodeStatus::Failed);
    assert!(
        view.nodes[0]
            .error
            .as_ref()
            .expect("node error")
            .contains("not connected")
    );
}

#[tokio::test]
async fn state_machine_completion_dispatches_service_callback() {
    let group = Arc::new(GroupStore::new());
    let mut seeded_group = test_group();
    seeded_group.service_spec = Some(ServiceSpec {
        callback_config: Some(CallbackConfig {
            channels: vec![CallbackChannelConfig::Baas {
                base_url: "http://127.0.0.1:0".to_string(),
                api_key: "sk-test".to_string(),
                bot_id: "default:callback-test".to_string(),
                metadata: None,
            }],
        }),
        timeout_seconds: None,
        max_concurrency: None,
    });
    group.upsert(seeded_group).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "callback after completion"}),
            caller_id: None,
        })
        .await
        .expect("start run");
    let initial_session = sessions
        .get(&started.view.run.session_id)
        .await
        .expect("get session")
        .expect("session");
    assert_eq!(initial_session.callback_status.as_deref(), Some("pending"));

    let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &delivery_run_id,
        &started.view.run.session_id,
        "callback payload",
    )
    .await;

    wait_for_callback_status(&sessions, &started.view.run.session_id, "failed").await;
}

#[tokio::test(flavor = "current_thread")]
async fn state_machine_runtime_logs_run_node_and_terminal_lifecycle() {
    let ((run_id, session_id, delivery_run_id), logs) = capture_tracing_logs(async {
        let group = Arc::new(GroupStore::new());
        group.upsert(test_group()).await.expect("seed group");
        let sessions = test_sessions();
        let store = Arc::new(MemoryCollaborationStore::new());
        let delivery = Arc::new(RecordingDelivery::default());
        let runtime = CollaborationRuntime::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store.clone(),
            group,
            sessions,
            delivery.clone(),
            noop_judge(),
        );

        let started = runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: "group-1".to_string(),
                session_id: None,
                definition_yaml: Some(single_node_yaml()),
                definition: None,
                definition_ref: None,
                input: json!({"question": "logging"}),
                caller_id: Some("caller-1".to_string()),
            })
            .await
            .expect("start run");
        let delivery_run_id = delivery.commands.lock().await[0].run_id.clone();
        complete_with_text(
            &runtime,
            &delivery_run_id,
            &started.view.run.session_id,
            "final answer",
        )
        .await;

        (
            started.view.run.run_id,
            started.view.run.session_id,
            delivery_run_id,
        )
    })
    .await;

    for expected in [
        "state_machine: run started",
        "state_machine: node dispatch started",
        "state_machine: node dispatch completed",
        "state_machine: bot terminal event received",
        "state_machine: node completed",
        "state_machine: run completed",
        "group-1",
        "single_node",
        "answer",
        "driver-bot",
        "caller-1",
        "complete",
        "completed",
    ] {
        assert!(
            logs.contains(expected),
            "expected logs to contain {expected:?}; logs:\n{logs}"
        );
    }
    assert!(
        logs.contains(&run_id),
        "expected logs to contain run id {run_id}; logs:\n{logs}"
    );
    assert!(
        logs.contains(&session_id),
        "expected logs to contain session id {session_id}; logs:\n{logs}"
    );
    assert!(
        logs.contains(&delivery_run_id),
        "expected logs to contain delivery run id {delivery_run_id}; logs:\n{logs}"
    );
}

#[tokio::test]
async fn start_run_uses_group_default_definition_binding() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
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

    assert_eq!(configured.default_definition.expect("definition").id, "single_node");

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            input: json!({"question": "use binding"}),
            caller_id: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.run.definition_id, "single_node");
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn group_collaboration_definition_get_and_patch_preserve_source_yaml() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
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
    assert_eq!(no_definition.yaml_source, DefinitionYamlSource::NoDefinition);
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
    assert_eq!(current.definition_yaml.as_deref(), Some(source_yaml.as_str()));
    let base = current.default_definition.clone().expect("default definition");

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
    assert_eq!(patched.definition_yaml.as_deref(), Some(patched_yaml.as_str()));
}

#[tokio::test]
async fn group_collaboration_definition_get_generates_legacy_authoring_yaml_without_identity() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = CollaborationRuntime::new(
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
    assert_eq!(current.yaml_source, DefinitionYamlSource::GeneratedNormalized);
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
        defaults.get("max_attempts").and_then(serde_yaml::Value::as_i64),
        Some(2)
    );
    assert_eq!(
        defaults.get("node_timeout_ms").and_then(serde_yaml::Value::as_i64),
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
        patched.default_definition.expect("patched definition").version,
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
    let runtime = CollaborationRuntime::new(
        definitions.clone(),
        backing_store.clone(),
        backing_store.clone(),
        backing_store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

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
            input: json!({"question": "use binding without rewriting definition"}),
            caller_id: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.run.definition_id, "single_node");
    assert_eq!(
        definitions.upsert_calls().await,
        1,
        "group binding runs must not rewrite persisted definition rows"
    );
    let snapshot = StateMachineDefinitionRepoPort::get_run_snapshot(
        &*definitions,
        &started.view.run.run_id,
    )
    .await
    .expect("get run snapshot")
    .expect("run snapshot");
    assert_inferred_default_requires(&snapshot);
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn start_run_uses_group_participant_bindings_for_template_definition() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
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
            input: json!({"question": "use participant binding"}),
            caller_id: None,
        })
        .await
        .expect("start run from binding");

    assert_eq!(started.view.nodes[0].assignee_bot_id, "driver-bot");
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
    });
    group.upsert(seeded_group).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
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
            input: json!({"question": "multi"}),
            caller_id: None,
        })
        .await
        .expect_err("multi bot slot is not supported by the current single-assignee runtime");

    assert!(error.to_string().contains("exactly one bot"));
}

#[tokio::test]
async fn graph_view_returns_snapshot_edges_and_node_status() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery,
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(join_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "graph"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    let graph = runtime
        .get_state_machine_run_graph(&started.view.run.run_id)
        .await
        .expect("get graph")
        .expect("graph");

    assert_eq!(graph.definition.id, "join_graph");
    assert_eq!(graph.definition.initial_nodes, vec!["start"]);
    let mut edges = graph
        .edges
        .iter()
        .map(|edge| {
            (
                edge.source.as_str(),
                edge.outcome.as_str(),
                edge.target.as_str(),
            )
        })
        .collect::<Vec<_>>();
    edges.sort();
    assert_eq!(
        edges,
        vec![
            ("branch_b", "complete", "join"),
            ("branch_c", "complete", "join"),
            ("start", "complete", "branch_b"),
            ("start", "complete", "branch_c"),
        ]
    );
    let by_id = graph
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["start"], Some(StateMachineNodeStatus::Running));
    assert_eq!(by_id["branch_b"], Some(StateMachineNodeStatus::Pending));
    assert_eq!(by_id["join"], Some(StateMachineNodeStatus::Pending));
}

#[tokio::test]
async fn complete_transitions_support_fan_out_and_implicit_all_join() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(join_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "join"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    assert_eq!(delivery.commands.lock().await.len(), 1);
    let first_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(&runtime, &first_run_id, &started.view.run.session_id, "a").await;
    assert_eq!(delivery.commands.lock().await.len(), 3);

    let branch_b_run_id = delivery.commands.lock().await[1].run_id.clone();
    let branch_c_run_id = delivery.commands.lock().await[2].run_id.clone();
    complete_with_text(&runtime, &branch_b_run_id, &started.view.run.session_id, "b").await;
    assert_eq!(
        delivery.commands.lock().await.len(),
        3,
        "join node must wait for the other upstream"
    );

    complete_with_text(&runtime, &branch_c_run_id, &started.view.run.session_id, "c").await;
    assert_eq!(delivery.commands.lock().await.len(), 4);

    let join_run_id = delivery.commands.lock().await[3].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &join_run_id,
        &started.view.run.session_id,
        "joined",
    )
    .await;
    let view = handled.view.expect("completed run");
    assert_eq!(view.run.output.as_deref(), Some("joined"));
}

#[tokio::test]
async fn judged_node_routes_selected_outcome_and_skips_unselected_branch() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_outcome("approved"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge.clone(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    assert_eq!(delivery.commands.lock().await.len(), 1);
    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;
    assert_eq!(delivery.commands.lock().await.len(), 2);
    assert_eq!(judge.requests.lock().await[0].allowed_outcomes, vec!["approved", "rejected"]);
    let judged_view = runtime
        .get_state_machine_run(&started.view.run.run_id)
        .await
        .expect("get judged run")
        .expect("run view");
    assert_eq!(judged_view.judge_outputs.len(), 1);
    assert_eq!(judged_view.judge_outputs[0].node_id, "review");
    assert_eq!(judged_view.judge_outputs[0].attempt, 0);
    assert_eq!(judged_view.judge_outputs[0].decision.outcome, "approved");
    let node_view = runtime
        .get_state_machine_node_run(&started.view.run.run_id, "review")
        .await
        .expect("get node run")
        .expect("node view");
    assert_eq!(node_view.node.node_id, "review");
    assert_eq!(node_view.judge_outputs.len(), 1);
    assert_eq!(node_view.judge_outputs[0].decision.outcome, "approved");

    let publish_run_id = delivery.commands.lock().await[1].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &publish_run_id,
        &started.view.run.session_id,
        "approved final",
    )
    .await;
    let view = handled.view.expect("completed run");

    assert_eq!(view.run.output.as_deref(), Some("approved final"));
    let by_id = view
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["review"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["publish"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["revise"], StateMachineNodeStatus::Skipped);
    assert_eq!(by_id["manual_review"], StateMachineNodeStatus::Skipped);
}

#[tokio::test]
async fn judged_node_publishes_bot_output_but_not_judge_message_to_workbench() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let judge = Arc::new(RecordingJudge::with_outcome("approved"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    ).with_frontend_delivery(frontend_delivery.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let frontend_commands = frontend_delivery.commands.lock().await;
    assert_eq!(frontend_commands.len(), 2);
    let panel_event: Value =
        serde_json::from_str(&frontend_commands[0].event_json).expect("panel event json");
    assert_eq!(
        panel_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("panel")
    );
    let bot_event: Value =
        serde_json::from_str(&frontend_commands[1].event_json).expect("bot event json");
    assert_eq!(bot_event["event"].as_str(), Some("chat"));
    assert_eq!(bot_event["bot_uuid"].as_str(), Some("driver-bot"));
    assert_eq!(
        bot_event["payload"]["message"]["content"][0]["text"].as_str(),
        Some("candidate final answer")
    );
    assert_ne!(
        bot_event["payload"]["metadata"]["state_machine"]["event"].as_str(),
        Some("judge")
    );
}

#[tokio::test]
async fn judged_node_failure_records_runtime_event_and_fails_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_error("judge provider timed out"));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_branch_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let view = handled.view.expect("failed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("judge failed for node review attempt 0: judge provider timed out")
    );
    let review = view
        .nodes
        .iter()
        .find(|node| node.node_id == "review")
        .expect("review node");
    assert_eq!(review.status, StateMachineNodeStatus::Failed);
    assert_eq!(
        review.error.as_deref(),
        Some("judge failed for node review attempt 0: judge provider timed out")
    );
    let failure_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "review",
        "state_machine.judge.failed",
    )
    .await
    .expect("list judge failure events");
    assert_eq!(failure_events.len(), 1);
    assert_eq!(failure_events[0].attempt, Some(0));
    assert_eq!(failure_events[0].payload["error"].as_str(), Some("judge provider timed out"));
    assert_eq!(failure_events[0].payload["reason"].as_str(), Some("judge_failed"));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn judged_node_timeout_records_runtime_event_and_fails_run() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let judge = Arc::new(RecordingJudge::with_delayed_outcome("approved", 25));
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        judge,
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_timeout_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "judge branch"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    let handled = complete_with_text(
        &runtime,
        &review_run_id,
        &started.view.run.session_id,
        "candidate final answer",
    )
    .await;

    let view = handled.view.expect("failed run view");
    assert_eq!(view.run.status, StateMachineRunStatus::Failed);
    assert_eq!(
        view.run.error.as_deref(),
        Some("judge timed out for node review attempt 0 after 1ms")
    );
    let review = view
        .nodes
        .iter()
        .find(|node| node.node_id == "review")
        .expect("review node");
    assert_eq!(review.status, StateMachineNodeStatus::Failed);
    assert_eq!(
        review.error.as_deref(),
        Some("judge timed out for node review attempt 0 after 1ms")
    );
    let failure_events = CollaborationEventRepoPort::list_events_by_run_node_and_type(
        &*store,
        &started.view.run.run_id,
        "review",
        "state_machine.judge.failed",
    )
    .await
    .expect("list judge failure events");
    assert_eq!(failure_events.len(), 1);
    assert_eq!(failure_events[0].attempt, Some(0));
    assert_eq!(failure_events[0].payload["reason"].as_str(), Some("judge_timeout"));
    assert_eq!(failure_events[0].payload["timeout_ms"].as_u64(), Some(1));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn judged_node_keeps_shared_merge_reachable_from_selected_branch() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = CollaborationRuntime::new(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        delivery.clone(),
        Arc::new(RecordingJudge::with_outcome("approved")),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judge_shared_merge_yaml()),
            definition: None,
            definition_ref: None,
            input: json!({"question": "judge shared merge"}),
            caller_id: None,
        })
        .await
        .expect("start run");

    let review_run_id = delivery.commands.lock().await[0].run_id.clone();
    complete_with_text(&runtime, &review_run_id, &started.view.run.session_id, "candidate").await;
    assert_eq!(delivery.commands.lock().await.len(), 2);

    let fast_run_id = delivery.commands.lock().await[1].run_id.clone();
    complete_with_text(&runtime, &fast_run_id, &started.view.run.session_id, "fast").await;
    assert_eq!(delivery.commands.lock().await.len(), 3);

    let merge_run_id = delivery.commands.lock().await[2].run_id.clone();
    let handled = complete_with_text(&runtime, &merge_run_id, &started.view.run.session_id, "merged").await;
    let view = handled.view.expect("completed run");
    assert_eq!(view.run.output.as_deref(), Some("merged"));
    let by_id = view
        .nodes
        .iter()
        .map(|node| (node.node_id.as_str(), node.status))
        .collect::<BTreeMap<_, _>>();
    assert_eq!(by_id["fast"], StateMachineNodeStatus::Completed);
    assert_eq!(by_id["slow"], StateMachineNodeStatus::Skipped);
    assert_eq!(by_id["merge"], StateMachineNodeStatus::Completed);
}

#[derive(Default)]
struct CountingDefinitionRepo {
    inner: Arc<MemoryCollaborationStore>,
    upsert_calls: Mutex<usize>,
}

impl CountingDefinitionRepo {
    fn new(inner: Arc<MemoryCollaborationStore>) -> Self {
        Self {
            inner,
            upsert_calls: Mutex::new(0),
        }
    }

    async fn upsert_calls(&self) -> usize {
        *self.upsert_calls.lock().await
    }
}

#[async_trait]
impl StateMachineDefinitionRepoPort for CountingDefinitionRepo {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()> {
        *self.upsert_calls.lock().await += 1;
        StateMachineDefinitionRepoPort::upsert(&*self.inner, definition).await
    }

    async fn get(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinition>> {
        StateMachineDefinitionRepoPort::get(&*self.inner, id, version).await
    }

    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
    ) -> ServiceResult<()> {
        StateMachineDefinitionRepoPort::save_run_snapshot(
            &*self.inner,
            run,
            group_version,
            definition,
            resolved_participant_bindings,
        )
        .await
    }

    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<CollaborationDefinition>> {
        StateMachineDefinitionRepoPort::get_run_snapshot(&*self.inner, run_id).await
    }
}

#[derive(Default)]
struct RecordingDelivery {
    commands: Mutex<Vec<BotDeliveryCommand>>,
}

#[async_trait]
impl BotDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.commands.lock().await.push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: true,
            error: None,
        })
    }
}

#[derive(Default)]
struct RejectingDelivery {
    commands: Mutex<Vec<BotDeliveryCommand>>,
}

#[async_trait]
impl BotDeliveryPort for RejectingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        self.commands.lock().await.push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: false,
            error: Some(ServiceError::BotNotConnected(cmd.target_bot_id().to_string())),
        })
    }
}

#[derive(Default)]
struct RecordingJudge {
    outcome: String,
    error: Option<String>,
    delay_ms: Option<u64>,
    requests: Mutex<Vec<JudgeRequest>>,
}

impl RecordingJudge {
    fn with_outcome(outcome: &str) -> Self {
        Self {
            outcome: outcome.to_string(),
            error: None,
            delay_ms: None,
            requests: Mutex::new(Vec::new()),
        }
    }

    fn with_error(error: &str) -> Self {
        Self {
            outcome: String::new(),
            error: Some(error.to_string()),
            delay_ms: None,
            requests: Mutex::new(Vec::new()),
        }
    }

    fn with_delayed_outcome(outcome: &str, delay_ms: u64) -> Self {
        Self {
            outcome: outcome.to_string(),
            error: None,
            delay_ms: Some(delay_ms),
            requests: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for RecordingJudge {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        self.requests.lock().await.push(request);
        if let Some(delay_ms) = self.delay_ms {
            tokio::time::sleep(Duration::from_millis(delay_ms)).await;
        }
        if let Some(error) = &self.error {
            return Err(ServiceError::InternalError(error.clone()));
        }
        Ok(JudgeDecision {
            outcome: self.outcome.clone(),
            reason: "mock decision".to_string(),
            confidence: 1.0,
            checked_criteria: Vec::new(),
            retry_instruction: String::new(),
            raw_response: None,
        })
    }
}

fn noop_judge() -> Arc<RecordingJudge> {
    Arc::new(RecordingJudge::with_outcome("complete"))
}

#[derive(Default)]
struct RecordingFrontendDelivery {
    commands: Mutex<Vec<FrontendDeliveryCommand>>,
}

#[async_trait]
impl FrontendDeliveryPort for RecordingFrontendDelivery {
    async fn publish(
        &self,
        cmd: FrontendDeliveryCommand,
    ) -> ServiceResult<FrontendDeliveryResult> {
        let target = cmd.target.clone();
        self.commands.lock().await.push(cmd);
        Ok(FrontendDeliveryResult {
            target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

fn chat_send_params(command: &BotDeliveryCommand) -> ChatSendParams {
    match &command.frame {
        BcsFrame::Request(request) => {
            assert_eq!(request.method, "chat.send");
            serde_json::from_value(request.params.clone().expect("chat.send params"))
                .expect("chat.send params decode")
        }
        _ => panic!("expected chat.send request frame"),
    }
}

fn test_group() -> Group {
    Group::new(
        "group-1",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
    )
}

fn single_node_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: single_node
version: 1
name: Single Node
participants:
  driver:
    bot_id: driver-bot
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    defaults:
      node_timeout_ms: 120000
      max_attempts: 2
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        node_timeout_ms: 60000
        max_attempts: 3
        assignee:
          type: bot_binding
          binding: driver
        instruction: Answer the question.
        final_output: true
"#
    .to_string()
}

fn single_node_authoring_yaml(name: &str) -> String {
    format!(
        r#"
api_version: bcs.collaboration/v1
name: {name}
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
        instruction: Answer the question.
        final_output: true
"#
    )
}

fn single_node_template_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: single_node_template
version: 1
name: Single Node Template
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
        instruction: Answer the question.
        final_output: true
"#
    .to_string()
}

async fn complete_with_text(
    runtime: &CollaborationRuntime,
    delivery_run_id: &str,
    session_id: &str,
    text: &str,
) -> bcs_service_api::HandleBotTerminalEventOutcome {
    runtime
        .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
            bot_id: "driver-bot".to_string(),
            run_id: delivery_run_id.to_string(),
            event_type: "chat.event".to_string(),
            event_payload: json!({
                "run_id": delivery_run_id,
                "bcs_group_id": "group-1",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}]
                }
            }),
            state: ChatEventState::Final,
            bcs_session_id: Some(session_id.to_string()),
        })
        .await
        .expect("handle final")
}

async fn wait_for_callback_status(
    sessions: &Arc<SessionManagementServiceImpl>,
    session_id: &str,
    expected: &str,
) {
    let mut last = None;
    for _ in 0..100 {
        let session = sessions
            .get(session_id)
            .await
            .expect("get session")
            .expect("session");
        last = session.callback_status.clone();
        if session.callback_status.as_deref() == Some(expected) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    panic!(
        "callback_status did not become {expected}; last status was {:?}",
        last
    );
}

fn join_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: join_graph
version: 1
name: Join Graph
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
      start:
        kind: bot_task
        display_name: Start
        assignee:
          type: bot_binding
          binding: driver
        instruction: Start.
        transitions:
          complete:
            targets: [branch_b, branch_c]
      branch_b:
        kind: bot_task
        display_name: Branch B
        assignee:
          type: bot_binding
          binding: driver
        instruction: B.
        transitions:
          complete:
            targets: [join]
      branch_c:
        kind: bot_task
        display_name: Branch C
        assignee:
          type: bot_binding
          binding: driver
        instruction: C.
        transitions:
          complete:
            targets: [join]
      join:
        kind: bot_task
        display_name: Join
        assignee:
          type: bot_binding
          binding: driver
        instruction: Join.
        final_output: true
"#
    .to_string()
}

fn judge_branch_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_branch
version: 1
name: Judge Branch
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
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: [revise]
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: driver
        instruction: Publish final answer.
        final_output: true
      revise:
        kind: bot_task
        display_name: Revise
        assignee:
          type: bot_binding
          binding: driver
        instruction: Revise answer.
        transitions:
          complete:
            targets: [manual_review]
      manual_review:
        kind: bot_task
        display_name: Manual Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Review revised answer.
        transitions:
          complete:
            targets: [publish]
"#
    .to_string()
}

fn judge_timeout_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_timeout
version: 1
name: Judge Timeout
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
        kind: bot_task
        display_name: Review
        node_timeout_ms: 1
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [publish]
          rejected:
            targets: []
      publish:
        kind: bot_task
        display_name: Publish
        assignee:
          type: bot_binding
          binding: driver
        instruction: Publish final answer.
        final_output: true
"#
    .to_string()
}

fn judge_shared_merge_yaml() -> String {
    r#"
api_version: bcs.collaboration/v1
id: judge_shared_merge
version: 1
name: Judge Shared Merge
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
        kind: bot_task
        display_name: Review
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce a candidate artifact.
        judge:
          type: llm
          criteria:
            - Is the answer good enough?
          outcomes: [approved, rejected]
        transitions:
          approved:
            targets: [fast]
          rejected:
            targets: [slow]
      fast:
        kind: bot_task
        display_name: Fast Path
        assignee:
          type: bot_binding
          binding: driver
        instruction: Continue approved answer.
        transitions:
          complete:
            targets: [merge]
      slow:
        kind: bot_task
        display_name: Slow Path
        assignee:
          type: bot_binding
          binding: driver
        instruction: Revise rejected answer.
        transitions:
          complete:
            targets: [merge]
      merge:
        kind: bot_task
        display_name: Merge
        assignee:
          type: bot_binding
          binding: driver
        instruction: Produce final answer.
        final_output: true
"#
    .to_string()
}
