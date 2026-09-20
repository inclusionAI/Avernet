//! Group-only creation through the real Group and Session application services.

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_collaboration_runtime::CollaborationRuntime;
use bcs_collaboration_store::MemoryCollaborationStore;
use bcs_group::{GroupConfig, GroupManagement, GroupStore, MemoryGroupRepo};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{
    BotCapabilities, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget,
    BotRegistryCoreService, GroupCoreService, GroupRuntimeBindingRepoPort, JudgeDecision,
    JudgeEvaluatorPort, JudgeRequest, Participant, ServiceResult, SessionManagementService,
    StateMachineDefinitionRepoPort, StateMachineRunRepoPort, SystemMessageEvent,
    SystemMessageService,
};
use bcs_services_container::Services;
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[derive(Default)]
struct RecordingSystemMessages(Mutex<Vec<(String, SystemMessageEvent)>>);

#[derive(Default)]
struct RecordingDelivery(Mutex<Vec<BotDeliveryCommand>>);

#[async_trait::async_trait]
impl BotDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, command: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let result = BotDeliveryResult {
            target_bot_id: command.target_bot_id().to_string(),
            delivered: true,
            error: None,
        };
        self.0.lock().await.push(command);
        Ok(result)
    }
}

struct UnusedJudge;

#[async_trait::async_trait]
impl JudgeEvaluatorPort for UnusedJudge {
    async fn judge(&self, _: JudgeRequest) -> ServiceResult<JudgeDecision> {
        panic!("this workflow does not use a judge")
    }
}

#[async_trait::async_trait]
impl SystemMessageService for RecordingSystemMessages {
    async fn notify(
        &self,
        _group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        _participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.0.lock().await.push((session_id.to_string(), event));
        Ok(1)
    }
}

struct Fixture {
    app: axum::Router,
    groups: Arc<GroupStore>,
    sessions: Arc<SessionManagementServiceImpl>,
    notifications: Arc<RecordingSystemMessages>,
    collaboration: Arc<MemoryCollaborationStore>,
    delivery: Arc<RecordingDelivery>,
    _temp: TempDir,
}

impl Fixture {
    async fn new() -> Self {
        let temp = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp.path().to_path_buf()));
        for id in ["lead-bot", "member-bot"] {
            registry
                .register(
                    id.to_string(),
                    BotCapabilities {
                        name: Some(id.to_string()),
                        visibility: "public".to_string(),
                        ..Default::default()
                    },
                )
                .await
                .unwrap();
        }
        registry
            .store_token_mapping("lead-token".to_string(), "lead-bot".to_string())
            .await;
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupStore::with_repo(group_repo.clone()));
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()),
            group_repo,
        ));
        let notifications = Arc::new(RecordingSystemMessages::default());
        let mut services = Services::noop();
        services.registry = registry.clone();
        services.group = groups.clone();
        services.session_management = sessions.clone();
        services.system_message = notifications.clone();
        let collaboration = Arc::new(MemoryCollaborationStore::new());
        let delivery = Arc::new(RecordingDelivery::default());
        services.collaboration_runtime = Arc::new(
            CollaborationRuntime::new(
                collaboration.clone(),
                collaboration.clone(),
                collaboration.clone(),
                collaboration.clone(),
                groups.clone(),
                sessions.clone(),
                delivery.clone(),
                Arc::new(UnusedJudge),
            )
            .with_bot_registry(registry.clone())
            .with_message_repo(Arc::new(MemoryMessageRepo::new())),
        );
        services.group_management = Arc::new(GroupManagement::new(
            groups.clone(),
            registry.clone(),
            services.friend.clone(),
            services.relation.clone(),
            GroupConfig::default(),
            sessions.clone(),
            notifications.clone(),
        ));
        services.session_launch = Arc::new(SessionLaunchApplication::new(
            registry,
            groups.clone(),
            sessions.clone(),
            services.collaboration_runtime.clone(),
            notifications.clone(),
        ));
        let mut state = HttpAppState::new(services);
        state.botchat_url = Some("https://example.com".to_string());
        Self {
            app: build_router(state),
            groups,
            sessions,
            notifications,
            collaboration,
            delivery,
            _temp: temp,
        }
    }

    async fn request(&self, method: &str, uri: &str, body: Value) -> (StatusCode, Value) {
        let response = self
            .app
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(uri)
                    .header("authorization", "Bearer lead-token")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        (status, serde_json::from_slice(&body).unwrap())
    }
}

fn group_request(strategy: &str) -> Value {
    let (lead_role, member_role) = if strategy == "manager_worker" {
        ("manager", "worker")
    } else {
        ("driver", "consultant")
    };
    json!({
        "id": "new-group", "driver_bot": "lead-bot",
        "group_strategy": strategy, "topic": "Review", "context": "Prepare first",
        "participants": [
            {"bot_uuid": "lead-bot", "role": lead_role},
            {"bot_uuid": "member-bot", "role": member_role}
        ],
        "create_initial_session": false
    })
}

async fn assert_group_only_then_explicit_session(strategy: &str) {
    let fixture = Fixture::new().await;
    let (status, created) = fixture
        .request("POST", "/groups", group_request(strategy))
        .await;
    assert_eq!(status, StatusCode::OK, "{created}");
    assert_eq!(created["created"], true);
    for field in ["session_id", "initial_session_id", "initial_run"] {
        assert_eq!(created.get(field), Some(&Value::Null), "{field}");
    }
    assert_eq!(created["context_injected"], 0);
    assert!(!created["chat_url"].as_str().unwrap().contains("&session="));
    let group = fixture
        .groups
        .get("new-group")
        .await
        .expect("group persisted");
    assert_eq!(group.context.as_deref(), Some("Prepare first"));
    assert_eq!(group.label.as_deref(), Some("Group: Review"));
    assert_eq!(group.participants.len(), 2);
    assert!(fixture.notifications.0.lock().await.is_empty());

    for uri in [
        "/groups/new-group/sessions",
        "/groups/new-group/sessions",
        "/groups/new-group/sessions?participant=lead-bot&limit=1",
    ] {
        let (status, listed) = fixture.request("GET", uri, Value::Null).await;
        assert_eq!(status, StatusCode::OK, "{listed}");
        assert_eq!(listed["items"], json!([]));
    }
    assert!(
        fixture
            .sessions
            .list_by_group("new-group", None, 0, 20, None, None)
            .await
            .unwrap()
            .is_empty()
    );
    assert!(fixture.notifications.0.lock().await.is_empty());

    let (status, session) = fixture
        .request(
            "POST",
            "/groups/new-group/sessions",
            json!({
                "session_title": "Ready to start", "session_kind": "chat"
            }),
        )
        .await;
    assert_eq!(status, StatusCode::CREATED, "{session}");
    let sid = session["session_id"].as_str().unwrap();
    assert_eq!(session["group_id"], "new-group");
    assert!(fixture.sessions.get(sid).await.unwrap().is_some());
    let calls = fixture.notifications.0.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, sid);
    assert!(matches!(
        &calls[0].1,
        SystemMessageEvent::SessionContext { .. }
    ));
}

#[tokio::test]
async fn chat_group_can_remain_empty_until_explicit_session_creation() {
    assert_group_only_then_explicit_session("chat").await;
}

#[tokio::test]
async fn manager_worker_group_can_remain_empty_until_explicit_session_creation() {
    assert_group_only_then_explicit_session("manager_worker").await;
}

#[tokio::test]
async fn group_creation_still_initializes_a_session_by_default_or_explicit_opt_in() {
    for strategy in ["chat", "manager_worker"] {
        for choice in [None, Some(true)] {
            let fixture = Fixture::new().await;
            let mut request = group_request(strategy);
            request
                .as_object_mut()
                .unwrap()
                .remove("create_initial_session");
            if let Some(choice) = choice {
                request["create_initial_session"] = json!(choice);
            }
            let (status, created) = fixture.request("POST", "/groups", request).await;
            assert_eq!(status, StatusCode::OK, "{created}");
            assert!(created["session_id"].is_string());
            assert_eq!(created["initial_session_id"], created["session_id"]);
            assert_eq!(fixture.notifications.0.lock().await.len(), 1);
        }
    }
}

#[tokio::test]
async fn unsupported_no_session_combinations_fail_before_group_creation() {
    for extra in [
        json!({"group_kind": "dm", "target_actor_id": "member-bot"}),
        json!({"event_subscriptions": [{
            "name": "events", "event_filters": ["group.*"],
            "payload": {"mode": "metadata_only"},
            "sink": {"type": "webhook", "url": "https://example.com/events"}
        }]}),
    ] {
        let fixture = Fixture::new().await;
        let mut request = group_request("chat");
        request
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        let (status, error) = fixture.request("POST", "/groups", request).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{error}");
        assert!(
            error.to_string().contains("create_initial_session"),
            "{error}"
        );
        assert!(fixture.groups.get("new-group").await.is_none());
        assert!(
            fixture
                .groups
                .find_by_participant("lead-bot")
                .await
                .is_empty()
        );
        assert!(fixture.notifications.0.lock().await.is_empty());
    }
}

#[tokio::test]
async fn no_session_still_enforces_group_membership_validation() {
    let fixture = Fixture::new().await;
    let mut request = group_request("chat");
    request["participants"][1]["bot_uuid"] = json!("missing-bot");
    let (status, _) = fixture.request("POST", "/groups", request).await;
    assert!(!status.is_success());
    assert!(fixture.groups.get("new-group").await.is_none());
    assert!(fixture.notifications.0.lock().await.is_empty());
}

const WORKFLOW_YAML: &str = r#"name: Deferred workflow
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
        instruction: Answer the request.
        final_output: true
"#;

fn state_machine_request() -> Value {
    let mut request = group_request("state_machine");
    request["participants"] = json!([{"bot_uuid": "lead-bot"}, {"bot_uuid": "member-bot"}]);
    request["collaboration_definition_yaml"] = json!(WORKFLOW_YAML);
    request["participant_bindings"] = json!({
        "writer": {"source": "manual", "bot_ids": ["member-bot"]}
    });
    request["auto_start_on_service_invocation"] = json!(true);
    request["opening_message"] = json!("Run {{bcs.run_id}}");
    request
}

#[tokio::test]
async fn state_machine_keeps_configuration_until_explicit_session_launch() {
    for start_initial_run in [None, Some(false), Some(true)] {
        let fixture = Fixture::new().await;
        let mut request = state_machine_request();
        if let Some(start) = start_initial_run {
            request["start_initial_run"] = json!(start);
        } else {
            // YAML also selects StateMachine when no strategy is supplied.
            request.as_object_mut().unwrap().remove("group_strategy");
        }
        let (status, created) = fixture.request("POST", "/groups", request).await;
        assert_eq!(status, StatusCode::OK, "{created}");
        for field in ["session_id", "initial_session_id", "initial_run"] {
            assert_eq!(created.get(field), Some(&Value::Null), "{field}");
        }
        assert_eq!(created["context_injected"], 0);
        assert!(!created["chat_url"].as_str().unwrap().contains("session="));
        let group = fixture.groups.get("new-group").await.unwrap();
        assert_eq!(
            group.group_strategy,
            bcs_service_api::GroupStrategy::StateMachine
        );
        assert_eq!(group.context.as_deref(), Some("Prepare first"));
        let binding = GroupRuntimeBindingRepoPort::get(&*fixture.collaboration, "new-group")
            .await
            .unwrap()
            .expect("persisted runtime binding");
        assert_eq!(
            binding.participant_bindings["writer"].bot_ids,
            ["member-bot"]
        );
        assert!(binding.auto_start_on_service_invocation);
        let definition = binding.default_definition.unwrap();
        let record = StateMachineDefinitionRepoPort::get_record(
            &*fixture.collaboration,
            &definition.id,
            definition.version,
        )
        .await
        .unwrap()
        .expect("persisted definition");
        assert_eq!(record.yaml_text.as_deref(), Some(WORKFLOW_YAML));
        for _ in 0..2 {
            let (status, listed) = fixture
                .request("GET", "/groups/new-group/sessions", Value::Null)
                .await;
            assert_eq!(status, StatusCode::OK, "{listed}");
            assert_eq!(listed["items"], json!([]));
        }
        assert!(fixture.notifications.0.lock().await.is_empty());
        assert!(fixture.delivery.0.lock().await.is_empty());
        assert!(
            fixture
                .sessions
                .list_by_group("new-group", None, 0, 20, None, None)
                .await
                .unwrap()
                .is_empty()
        );

        let (status, session) = fixture
            .request(
                "POST",
                "/groups/new-group/sessions",
                json!({
                    "session_title": "Start workflow", "input": {"task": "Write an answer"}
                }),
            )
            .await;
        assert_eq!(status, StatusCode::CREATED, "{session}");
        assert_eq!(session["session_kind"], "service_invocation");
        let sid = session["session_id"].as_str().unwrap();
        let run = fixture
            .collaboration
            .get_run_by_session_id(sid)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(run.definition_id, definition.id);
        assert_eq!(run.input, json!({"task": "Write an answer"}));
        assert_eq!(run.status, bcs_service_api::StateMachineRunStatus::Running);
        assert_eq!(fixture.delivery.0.lock().await.len(), 1);
        assert!(fixture.notifications.0.lock().await.is_empty());
    }
}

#[tokio::test]
async fn state_machine_default_session_creation_still_respects_start_initial_run() {
    for start_initial_run in [None, Some(false), Some(true)] {
        let fixture = Fixture::new().await;
        let mut request = state_machine_request();
        request
            .as_object_mut()
            .unwrap()
            .remove("create_initial_session");
        if let Some(start) = start_initial_run {
            request["start_initial_run"] = json!(start);
        }
        let (status, created) = fixture.request("POST", "/groups", request).await;
        assert_eq!(status, StatusCode::OK, "{created}");
        let sid = created["session_id"].as_str().unwrap();
        assert!(fixture.sessions.get(sid).await.unwrap().is_some());
        let run = fixture
            .collaboration
            .get_run_by_session_id(sid)
            .await
            .unwrap();
        assert_eq!(run.is_some(), start_initial_run.unwrap_or(true));
        assert_eq!(
            fixture.delivery.0.lock().await.len(),
            usize::from(start_initial_run.unwrap_or(true))
        );
    }
}

#[tokio::test]
async fn inline_event_subscriptions_require_initial_session_for_every_normal_strategy() {
    for strategy in ["chat", "manager_worker", "state_machine"] {
        let fixture = Fixture::new().await;
        let mut request = if strategy == "state_machine" {
            state_machine_request()
        } else {
            group_request(strategy)
        };
        request["event_subscriptions"] = json!([{
            "name": "events", "event_filters": ["group.*"],
            "payload": {"mode": "metadata_only"},
            "sink": {"type": "webhook", "url": "https://example.com/events"}
        }]);
        let (status, error) = fixture.request("POST", "/groups", request).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{error}");
        assert!(error.to_string().contains("create_initial_session"));
        assert!(fixture.groups.get("new-group").await.is_none());
        assert!(
            GroupRuntimeBindingRepoPort::get(&*fixture.collaboration, "new-group")
                .await
                .unwrap()
                .is_none()
        );
        assert!(
            fixture
                .sessions
                .list_by_group("new-group", None, 0, 20, None, None)
                .await
                .unwrap()
                .is_empty()
        );
        assert!(fixture.delivery.0.lock().await.is_empty());
    }
}

#[tokio::test]
async fn sessionless_state_machine_still_validates_yaml_before_persistence() {
    let fixture = Fixture::new().await;
    let mut request = state_machine_request();
    request["collaboration_definition_yaml"] = json!("name: Invalid definition");
    let (status, error) = fixture.request("POST", "/groups", request).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{error}");
    assert!(
        error
            .to_string()
            .contains("invalid collaboration_definition_yaml")
    );
    assert!(fixture.groups.get("new-group").await.is_none());
}
