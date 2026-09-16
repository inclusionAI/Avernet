//! Group-only creation through the real Group and Session application services.

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_group::{GroupConfig, GroupManagement, GroupStore, MemoryGroupRepo};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, GroupCoreService, Participant, ServiceResult,
    SessionManagementService, SystemMessageEvent, SystemMessageService,
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
        json!({"group_strategy": "state_machine"}),
        json!({"group_kind": "dm", "target_actor_id": "member-bot"}),
        json!({"collaboration_definition_yaml": "name: Invalid definition"}),
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
