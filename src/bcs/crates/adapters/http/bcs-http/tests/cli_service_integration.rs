//! Integration test: drive `bcs_cli::BcsClient`'s service-invocation methods
//! against an in-process axum server backed by mock services.
//!
//! Locks two contracts the CLI depends on:
//! - `POST /services/{group_id}/sessions` returns the session JSON with
//!   `session_id` (not `id`), `status: "running"`, `session_kind:
//!   "service_invocation"`, `reused: false` on a fresh invocation.
//! - `GET /services/{group_id}/sessions/{sid}` returns the same shape;
//!   `status` flips to `"completed"` when the session reaches a terminal
//!   state, and `output` / `callback_status` are non-null on completion.
//!
//! What is NOT tested here: svc-key auth (covered by service_auth.rs unit
//! tests), real callback dispatch, ServiceInvocationRunner. The fixture uses
//! bot-token auth, matching `bcs-cli service` behavior.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use bcs_bot::BotCore;
use bcs_cli::BcsClient;
use bcs_group::GroupStore;
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CreateOrReactivateCommand, CreateOrReactivateOutcome,
    Group, GroupCoreService, GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupUseCaseError, Participant, ParticipantMode, ParticipantRole,
    Session, SessionHistoryCommand, SessionHistoryResult, SessionKind, SessionManagementService,
    SessionStatus, SessionUseCaseError, ServiceSpec,
};
use bcs_services_container::Services;
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::sync::Mutex;

// ---------------------------------------------------------------------------
// mocks
// ---------------------------------------------------------------------------

/// Service-flavoured mock. Records the canned session and lets a test
/// "advance" it to Completed by bumping `complete_after_n_gets`.
struct MockServiceSessions {
    last: Mutex<Option<Session>>,
    /// Number of `get` calls before `status` flips to Completed. 0 = always
    /// completed; u64::MAX = never (default — stays Running).
    complete_after_n_gets: AtomicU64,
    /// Counts how many `get` calls have landed.
    get_calls: AtomicU64,
    /// Output JSON to inject when transitioning to Completed.
    completion_output: Mutex<Value>,
}

impl Default for MockServiceSessions {
    fn default() -> Self {
        Self {
            last: Mutex::new(None),
            complete_after_n_gets: AtomicU64::new(u64::MAX),
            get_calls: AtomicU64::new(0),
            completion_output: Mutex::new(json!({"result": "ok"})),
        }
    }
}

impl MockServiceSessions {
    /// After this many `get` calls, the next `get` returns Completed.
    fn flip_to_completed_after(&self, n: u64) {
        self.complete_after_n_gets.store(n, Ordering::SeqCst);
    }
}

#[async_trait::async_trait]
impl SessionManagementService for MockServiceSessions {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let session = Session {
            id: format!("{}:abcdef12", cmd.group_id),
            group_id: cmd.group_id.clone(),
            session_title: cmd.params.session_title.clone(),
            env: None,
            status: SessionStatus::Running,
            // The route writes the kind based on the path, but the mock
            // records ServiceInvocation explicitly so the tests can assert it.
            session_kind: SessionKind::ServiceInvocation,
            participants: cmd.params.participants.clone(),
            group_version: cmd.params.group_version,
            caller_id: cmd.params.caller_id.clone(),
            input: cmd.params.input.clone(),
            output: None,
            error_message: None,
            callback_status: None,
            activation_count: 1,
            caller_principal: cmd.params.caller_principal.clone(),
            created_by: cmd.params.created_by.clone(),
            current_msg_seq: 0,
            participant_join_seq: None,
            created_at: 1_700_000_000_000,
            updated_at: 1_700_000_000_000,
            completed_at: None,
            meta: cmd.params.meta.clone(),
        };
        *self.last.lock().await = Some(session.clone());
        Ok(CreateOrReactivateOutcome { session, created: true })
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        let calls = self.get_calls.fetch_add(1, Ordering::SeqCst) + 1;
        let mut last = self.last.lock().await.clone();
        if let Some(ref mut s) = last {
            if s.id != sid {
                return Ok(None);
            }
            let threshold = self.complete_after_n_gets.load(Ordering::SeqCst);
            if calls > threshold && s.status == SessionStatus::Running {
                s.status = SessionStatus::Completed;
                s.output = Some(self.completion_output.lock().await.clone());
                s.callback_status = Some("success".to_string());
                s.completed_at = Some(1_700_000_001_000);
                // Persist the flipped state so subsequent `get` calls keep it.
                *self.last.lock().await = Some(s.clone());
            }
        }
        Ok(last)
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        let last = self.last.lock().await.clone();
        Ok(last
            .map(|s| s.id == session_id && s.group_id == group_id)
            .unwrap_or(false))
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        // No active concurrent invocations — keeps max_concurrency check passing.
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

struct EmptyHistory;

#[async_trait::async_trait]
impl GroupMessageHistoryService for EmptyHistory {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        Ok(GroupHistoryResult {
            group_id: cmd.group_id,
            messages: Vec::new(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: Vec::<GroupMessage>::new(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}

// ---------------------------------------------------------------------------
// fixture
// ---------------------------------------------------------------------------

struct ServerFixture {
    base_url: String,
    sessions: Arc<MockServiceSessions>,
    _tmp: TempDir,
    _shutdown: tokio::task::JoinHandle<()>,
}

/// Builds an in-process server. `with_service_spec=false` produces a group
/// without `service_spec` so the 400 path can be exercised.
async fn start_server(with_service_spec: bool) -> ServerFixture {
    let tmp = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(tmp.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                summary: Some("driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "g-1",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    if with_service_spec {
        group.service_spec = Some(ServiceSpec {
            callback_config: None,
            timeout_seconds: None,
            max_concurrency: None,
        });
    }
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(MockServiceSessions::default());

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions.clone();
    services.group_message_history = Arc::new(EmptyHistory);

    let app = build_router(HttpAppState::new(services));

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });

    ServerFixture {
        base_url: format!("http://127.0.0.1:{}", port),
        sessions,
        _tmp: tmp,
        _shutdown: handle,
    }
}

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

#[tokio::test]
async fn cli_service_invoke_returns_session_id_and_running() {
    let server = start_server(true).await;
    let client = BcsClient::with_token(&server.base_url, "driver-token");

    let body = client
        .service_invoke(
            "g-1",
            Some(&json!({"q": "ping"})),
            None,
            Some("client-a"),
            Some("demo"),
            None,
        )
        .await
        .expect("service_invoke should succeed against in-process server");

    assert_eq!(
        body.get("session_id").and_then(|v| v.as_str()),
        Some("g-1:abcdef12"),
        "POST response must expose session_id (CLI parses this exact key)"
    );
    assert_eq!(body.get("group_id").and_then(|v| v.as_str()), Some("g-1"));
    assert_eq!(
        body.get("status").and_then(|v| v.as_str()),
        Some("running"),
        "fresh invocation must be running"
    );
    assert_eq!(
        body.get("session_kind").and_then(|v| v.as_str()),
        Some("service_invocation")
    );
    assert_eq!(
        body.get("reused").and_then(|v| v.as_bool()),
        Some(false),
        "first invoke should not be a reuse"
    );
    // `output` field is always present per service_session_to_json — null at
    // this stage. The CLI's `print_service_session_summary` skips printing
    // when null, so don't assert is_null vs missing — just don't crash.
    assert!(body.get("output").is_some(), "output key always present");
}

#[tokio::test]
async fn cli_service_status_returns_running_then_completed() {
    let server = start_server(true).await;
    let client = BcsClient::with_token(&server.base_url, "driver-token");

    // Seed a session via invoke first.
    let invoke = client
        .service_invoke("g-1", Some(&json!({"q": "ping"})), None, None, None, None)
        .await
        .unwrap();
    let sid = invoke.get("session_id").and_then(|v| v.as_str()).unwrap();

    // 1st status: still running.
    let first = client.service_session_status("g-1", sid).await.unwrap();
    assert_eq!(first.get("status").and_then(|v| v.as_str()), Some("running"));

    // Advance the mock so the next `get` returns Completed.
    server.sessions.flip_to_completed_after(1);

    let second = client.service_session_status("g-1", sid).await.unwrap();
    assert_eq!(
        second.get("status").and_then(|v| v.as_str()),
        Some("completed")
    );
    assert_eq!(
        second
            .get("output")
            .and_then(|v| v.get("result"))
            .and_then(|v| v.as_str()),
        Some("ok"),
        "completed session must surface output"
    );
    assert_eq!(
        second.get("callback_status").and_then(|v| v.as_str()),
        Some("success"),
        "callback_status key is exposed (CLI prints it)"
    );
    assert!(second.get("completed_at").is_some());
}

#[tokio::test]
async fn cli_service_invoke_rejects_when_group_has_no_service_spec() {
    let server = start_server(false).await;
    let client = BcsClient::with_token(&server.base_url, "driver-token");

    let err = client
        .service_invoke("g-1", Some(&json!({"q": "ping"})), None, None, None, None)
        .await
        .expect_err("invoke without service_spec should fail");

    let msg = err.to_string();
    assert!(
        msg.contains("400"),
        "expected 400 status in error message, got: {}",
        msg
    );
    assert!(
        msg.contains("invalid_params"),
        "expected invalid_params error code, got: {}",
        msg
    );
}
