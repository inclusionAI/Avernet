#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::*;
use bcs_service_api::{
    CollaborationRuntimeError, CollaborationRuntimeService,
    ListPendingHumanNodesCommand, PendingHumanNodeView, StateMachineNodeRunView,
    RerunStateMachineCommand, RerunStateMachineOutcome, StateMachineRunAccessCommand,
    StateMachineRunGraphView, StateMachineRunView,
};
use bcs_service_api::{HumanResponseSource, RespondHumanNodeCommand, RespondHumanNodeOutcome};
use serde_json::{Value, json};
use tower::ServiceExt;

const RUN_ID: &str = "run-1";

struct HeaderVerifier;

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(AuthenticatedCaller {
                tenant: Some("tenant-1".into()),
                user: Some(AuthenticatedUserIdentity {
                    id: "staff-1".to_string(),
                    username: "staff-1".to_string(),
                    display_name: Some("Staff One".to_string()),
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            })
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct FakeRuntimeService {
    next_run: Mutex<Option<StateMachineRunView>>,
    next_graph: Mutex<Option<StateMachineRunGraphView>>,
    next_node: Mutex<Option<StateMachineNodeRunView>>,
    next_pending: Mutex<Vec<PendingHumanNodeView>>,
    last_access: Mutex<Option<StateMachineRunAccessCommand>>,
    last_pending_cmd: Mutex<Option<ListPendingHumanNodesCommand>>,
    next_respond: Mutex<Option<RespondHumanNodeOutcome>>,
    last_respond: Mutex<Option<RespondHumanNodeCommand>>,
    next_cancel: Mutex<Option<StateMachineRunView>>,
    last_cancel_reason: Mutex<Option<String>>,
    next_rerun: Mutex<Option<RerunStateMachineOutcome>>,
    last_rerun: Mutex<Option<RerunStateMachineCommand>>,
}

#[async_trait]
impl CollaborationRuntimeService for FakeRuntimeService {
    async fn start_state_machine_run(
        &self,
        _cmd: bcs_service_api::StartStateMachineRunCommand,
    ) -> Result<bcs_service_api::StartStateMachineRunOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used".to_string()))
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(self.next_run.lock().expect("run lock").clone())
    }

    async fn get_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        Ok(self.next_run.lock().expect("run lock").clone())
    }

    async fn rerun_state_machine_run(
        &self,
        cmd: RerunStateMachineCommand,
    ) -> Result<RerunStateMachineOutcome, CollaborationRuntimeError> {
        *self.last_rerun.lock().expect("rerun lock") = Some(cmd);
        self.next_rerun
            .lock()
            .expect("rerun lock")
            .clone()
            .ok_or_else(|| {
                CollaborationRuntimeError::Internal(bcs_service_api::ServiceError::InternalError(
                    "no canned rerun".to_string(),
                ))
            })
    }

    async fn get_state_machine_run_graph_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        Ok(self.next_graph.lock().expect("graph lock").clone())
    }

    async fn get_state_machine_node_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        assert_eq!(node_id, "node-1");
        Ok(self.next_node.lock().expect("node lock").clone())
    }

    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        *self.last_pending_cmd.lock().expect("pending lock") = Some(cmd.clone());
        Ok(self.next_pending.lock().expect("pending lock").clone())
    }

    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        *self.last_respond.lock().expect("respond lock") = Some(cmd.clone());
        self.next_respond
            .lock()
            .expect("respond lock")
            .clone()
            .ok_or_else(|| {
                CollaborationRuntimeError::Internal(bcs_service_api::ServiceError::InternalError(
                    "no canned respond".to_string(),
                ))
            })
    }

    async fn cancel_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        *self.last_access.lock().expect("access lock") = Some(cmd.clone());
        *self.last_cancel_reason.lock().expect("reason lock") = reason;
        self.next_cancel
            .lock()
            .expect("cancel lock")
            .clone()
            .ok_or_else(|| {
                CollaborationRuntimeError::Internal(bcs_service_api::ServiceError::InternalError(
                    "no canned cancel".to_string(),
                ))
            })
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<bcs_service_api::SessionHistoryResult>, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: bcs_service_api::CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<bcs_service_api::StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: bcs_service_api::HandleBotTerminalEventCommand,
    ) -> Result<bcs_service_api::HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }

    async fn upsert_definition(
        &self,
        _definition: bcs_service_api::CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }

    async fn configure_group_runtime(
        &self,
        _cmd: bcs_service_api::ConfigureGroupRuntimeCommand,
    ) -> Result<bcs_service_api::ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest("not used in fake".to_string()))
    }
}

fn sample_run() -> StateMachineRunView {
    serde_json::from_value(json!({
        "run": {
            "run_id": RUN_ID,
            "definition_id": "def-1",
            "definition_version": 1,
            "group_id": "group-1",
            "session_id": "session-1",
            "status": "running",
            "input": {"query": "example"},
            "created_at": 0,
            "updated_at": 0
        },
        "nodes": [],
        "judge_outputs": []
    }))
    .expect("sample StateMachineRunView")
}

fn test_router(service: Arc<FakeRuntimeService>) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_collaboration_runtime_service(service),
    )
}

async fn response_json(response: axum::http::Response<Body>) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX).await.expect("body");
    serde_json::from_slice(&bytes).expect("json body")
}

#[tokio::test]
async fn get_run_returns_enveloped_view_and_records_access() {
    let service = Arc::new(FakeRuntimeService {
        next_run: Mutex::new(Some(sample_run())),
        ..Default::default()
    });
    let app = test_router(service.clone());

    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1")
        .header("x-test-auth", "yes")
        .header("x-request-id", "req-get-run")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);
    assert_eq!(body["request_id"], "req-get-run");

    let recorded = service
        .last_access
        .lock()
        .expect("access lock")
        .clone()
        .expect("access command recorded");
    assert_eq!(recorded.run_id, RUN_ID);
    let human = recorded.authenticated_human.expect("human mapped");
    assert_eq!(human.actor_id, "human_staff-1");
    assert_eq!(human.display_name.as_deref(), Some("Staff One"));
}

#[tokio::test]
async fn rerun_returns_created_child_without_a_request_body_and_records_source_access() {
    let mut child = sample_run();
    child.run.run_id = "run-2".to_string();
    child.run.root_run_id = Some(RUN_ID.to_string());
    child.run.rerun_of = Some(RUN_ID.to_string());
    let service = Arc::new(FakeRuntimeService {
        next_rerun: Mutex::new(Some(RerunStateMachineOutcome {
            view: child,
            created: true,
        })),
        ..Default::default()
    });
    let app = test_router(service.clone());

    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/reruns")
        .header("x-test-auth", "yes")
        .header("x-request-id", "req-rerun")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");

    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["data"]["run"]["run_id"], "run-2");
    assert_eq!(body["data"]["run"]["rerun_of"], RUN_ID);
    assert_eq!(body["data"]["idempotent_replay"], false);
    let recorded = service
        .last_rerun
        .lock()
        .expect("rerun lock")
        .clone()
        .expect("rerun command");
    assert_eq!(recorded.source_run_id, RUN_ID);
    assert_eq!(
        recorded
            .authenticated_human
            .expect("human mapped")
            .actor_id,
        "human_staff-1"
    );
}

#[tokio::test]
async fn rerun_returns_ok_when_source_already_has_a_direct_child() {
    let mut child = sample_run();
    child.run.run_id = "run-2".to_string();
    child.run.root_run_id = Some(RUN_ID.to_string());
    child.run.rerun_of = Some(RUN_ID.to_string());
    let service = Arc::new(FakeRuntimeService {
        next_rerun: Mutex::new(Some(RerunStateMachineOutcome {
            view: child,
            created: false,
        })),
        ..Default::default()
    });
    let app = test_router(service);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/collaboration/state-machine-runs/run-1/reruns")
                .header("x-test-auth", "yes")
                .header("x-request-id", "req-rerun-replay")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["run"]["run_id"], "run-2");
    assert_eq!(body["data"]["idempotent_replay"], true);
}

#[tokio::test]
async fn rerun_rejects_request_body_instead_of_ignoring_unsupported_fields() {
    let service = Arc::new(FakeRuntimeService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/v1/collaboration/state-machine-runs/run-1/reruns")
                .header("x-test-auth", "yes")
                .header("x-request-id", "req-rerun-invalid-body")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"input":{"override":true}}"#))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(service.last_rerun.lock().expect("rerun lock").is_none());
}

#[tokio::test]
async fn get_run_returns_enveloped_404_when_missing() {
    let service = Arc::new(FakeRuntimeService::default());
    let app = test_router(service);

    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(body["code"], 40400);
    assert_eq!(body["data"]["error_code"], "not_found");
}

#[tokio::test]
async fn get_graph_returns_enveloped_view() {
    let service = Arc::new(FakeRuntimeService {
        next_graph: Mutex::new(Some(serde_json::from_value(json!({
            "run": { "run_id": RUN_ID, "definition_id": "def-1", "definition_version": 1,
                     "group_id": "group-1", "session_id": "session-1", "status": "running",
                     "input": {"query": "example"}, "created_at": 0, "updated_at": 0 },
            "definition": { "id": "def-1", "version": 1, "name": "d",
                            "graph_mode": "acyclic", "initial_nodes": [] },
            "nodes": [],
            "edges": []
        })).expect("graph"))),
        ..Default::default()
    });
    let app = test_router(service);
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/graph")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);
    assert_eq!(body["data"]["definition"]["name"], "d");
}

#[tokio::test]
async fn get_node_returns_enveloped_view() {
    let service = Arc::new(FakeRuntimeService {
        next_node: Mutex::new(Some(serde_json::from_value(json!({
            // StateMachineNodeRun requires run_id, node_id, status, attempt.
            "node": { "run_id": RUN_ID, "node_id": "node-1", "status": "running", "attempt": 1 },
        })).expect("node"))),
        ..Default::default()
    });
    let app = test_router(service);
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/nodes/node-1")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["node"]["node_id"], "node-1");
}

#[tokio::test]
async fn pending_human_nodes_returns_enveloped_array() {
    let pending = vec![PendingHumanNodeView {
        node_id: "node-1".to_string(),
        display_name: "Review".to_string(),
        instruction: "please review".to_string(),
        response_ref: "ref-1".to_string(),
        judge_outcomes: vec![],
        timeout_deadline_ms: None,
        upstream_artifacts: vec![],
    }];
    let service = Arc::new(FakeRuntimeService {
        next_pending: Mutex::new(pending),
        ..Default::default()
    });
    let app = test_router(service.clone());
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/pending-human-nodes")
        .header("x-test-auth", "yes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"][0]["node_id"], "node-1");

    let cmd = service.last_pending_cmd.lock().expect("pending lock").clone().expect("cmd");
    assert_eq!(cmd.caller_actor_id, "human_staff-1");
}

#[tokio::test]
async fn pending_human_nodes_requires_user_principal() {
    let app = test_router(Arc::new(FakeRuntimeService::default()));
    // No x-test-auth header -> HeaderVerifier returns Missing -> 401 from the
    // verify_principal boundary before the handler runs.
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/pending-human-nodes")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn respond_requires_user_and_returns_enveloped_outcome() {
    // RespondHumanNodeOutcome = { node: StateMachineNodeRun, run: StateMachineRun }.
    // StateMachineNodeRun requires run_id, node_id, status, attempt.
    // StateMachineNodeStatus variants (snake_case): pending/ready/running/completed/
    // failed/retry_scheduled/skipped — "succeeded" is NOT valid, use "completed".
    let service = Arc::new(FakeRuntimeService {
        next_respond: Mutex::new(Some(serde_json::from_value(json!({
            "node": { "run_id": RUN_ID, "node_id": "node-1", "status": "completed", "attempt": 1 },
            "run": { "run_id": RUN_ID, "definition_id": "def-1", "definition_version": 1,
                     "group_id": "group-1", "session_id": "session-1", "status": "running",
                     "input": {"query": "example"}, "created_at": 0, "updated_at": 0 },
        })).expect("respond outcome"))),
        ..Default::default()
    });
    let app = test_router(service.clone());

    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/nodes/node-1/respond")
        .header("x-test-auth", "yes")
        .header("content-type", "application/json")
        .body(Body::from(json!({"content": "approved"}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);

    let cmd = service.last_respond.lock().expect("lock").clone().expect("cmd");
    assert_eq!(cmd.run_id, "run-1");
    assert_eq!(cmd.node_id, "node-1");
    assert_eq!(cmd.caller_actor_id, "human_staff-1");
    assert_eq!(cmd.content, "approved");
    assert!(matches!(cmd.source, HumanResponseSource::Http));
}

#[tokio::test]
async fn respond_rejects_missing_user_principal() {
    // No x-test-auth header -> HeaderVerifier returns Missing -> 401 at the
    // verify_principal boundary before the handler runs.
    let app = test_router(Arc::new(FakeRuntimeService::default()));
    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/nodes/node-1/respond")
        .header("content-type", "application/json")
        .body(Body::from(json!({"content": "approved"}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn cancel_returns_enveloped_view_with_optional_human() {
    // cancel returns a StateMachineRunView = { run, nodes, judge_outputs };
    // the run_id lives at data.run.run_id, not data.run_id.
    let service = Arc::new(FakeRuntimeService {
        next_cancel: Mutex::new(Some(sample_run())),
        ..Default::default()
    });
    let app = test_router(service.clone());
    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/state-machine-runs/run-1/cancel")
        .header("x-test-auth", "yes")
        .header("content-type", "application/json")
        .body(Body::from(json!({"reason": "done"}).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["run"]["run_id"], RUN_ID);
    assert_eq!(*service.last_cancel_reason.lock().unwrap(), Some("done".to_string()));
}

// --- Noop dependencies required by ApiState::new ---------------------------

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_groups(&self, _: ListGroups) -> Result<Page<GroupSummary>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create(&self, _: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn get(&self, _: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update(&self, _: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete(&self, _: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn add_participant(
        &self,
        _: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update_participant(
        &self,
        _: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_participant(
        &self,
        _: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(&self, _: CreateSession) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn list(&self, _: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn get(&self, _: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update(&self, _: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete(&self, _: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn complete(&self, _: CompleteSession) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn collect(&self, _: CollectSession) -> Result<SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn uncollect(
        &self,
        _: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn add_participant(
        &self,
        _: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update_participant(
        &self,
        _: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_participant(
        &self,
        _: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopMessageService;

#[async_trait]
impl SessionMessageService for NoopMessageService {
    async fn list(
        &self,
        _: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create_session_invitation(
        &self,
        _: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn accept_invitation(
        &self,
        _: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_bot_friendship(
        &self,
        _: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create_bot_friend_request(
        &self,
        _: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn list_bot_friend_requests(
        &self,
        _: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn accept_friend_request(
        &self,
        _: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn reject_friend_request(
        &self,
        _: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }
}
