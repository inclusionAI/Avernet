//! Integration test: drive `bcs_cli::BcsClient` against a live in-process
//! axum server backed by mock services.
//!
//! Purpose: lock in the wire shape that `bcs-cli`'s session subcommands
//! depend on. The CLI parses responses with `result.get("session_id").or_else(|| result.get("id"))`
//! and `result.get("items").and_then(|v| v.as_array())` — this test fails
//! loudly if the server's JSON layout shifts.
//!
//! What is NOT tested here: real bot delivery (no WebSocket bots are spun up),
//! ServiceInvocation flows, callback dispatch, auth/ownership branches. Those
//! live in `session_create_contract.rs` etc.

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_cli::BcsClient;
use bcs_group::GroupStore;
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CreateOrReactivateCommand, CreateOrReactivateOutcome,
    Group, GroupCoreService, GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupUseCaseError, Participant, ParticipantMode, ParticipantRole,
    Session, SessionHistoryCommand, SessionHistoryResult, SessionManagementService, SessionStatus,
    SessionUseCaseError,
};
use bcs_services_container::Services;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;

// ---------------------------------------------------------------------------
// mocks
// ---------------------------------------------------------------------------

#[derive(Default)]
struct MockSessions {
    /// Records the last canned session so `get` returns the same row created.
    last_created: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for MockSessions {
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
            session_kind: cmd.params.session_kind,
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
        *self.last_created.lock().await = Some(session.clone());
        Ok(CreateOrReactivateOutcome { session, created: true })
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        let last = self.last_created.lock().await.clone();
        Ok(last.filter(|s| s.id == sid))
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        let last = self.last_created.lock().await.clone();
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
        let last = self.last_created.lock().await.clone();
        Ok(last.into_iter().collect())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
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

/// Returns an empty message list for any session — exercises the
/// `GET /sessions/{sid}/messages` happy path without needing real
/// chat history infrastructure.
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
    token: String,
    /// Tempdir owns the bot registry's persistent files; keep alive for the
    /// duration of the test.
    _tmp: TempDir,
    _shutdown: tokio::task::JoinHandle<()>,
}

async fn start_server() -> ServerFixture {
    // Register a real bot so the route's bearer-token auth resolves to it.
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
    let token = registry
        .register_http_connection("driver-bot".to_string(), "test-token".to_string())
        .await;

    // Real GroupStore so the route's `state.services.group.get(&gid)` succeeds,
    // with driver-bot as the sole participant.
    let group_store = Arc::new(GroupStore::new());
    let group = Group::new(
        "g-1",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group_store.upsert(group).await.unwrap();

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = Arc::new(MockSessions::default());
    services.group_message_history = Arc::new(EmptyHistory);

    let app = build_router(HttpAppState::new(services));

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });

    ServerFixture {
        base_url: format!("http://127.0.0.1:{}", port),
        token,
        _tmp: tmp,
        _shutdown: handle,
    }
}

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

#[tokio::test]
async fn cli_create_session_emits_id_and_session_id_keys() {
    // The CLI's print code looks for both `session_id` (legacy alias) and `id`.
    // sessions.rs:30-39 is the contract — this test fails the moment that
    // serializer drops the alias.
    let server = start_server().await;
    let client = BcsClient::with_token(&server.base_url, &server.token);

    let body = client
        .create_session("g-1", Some("hello"), None, None, None)
        .await
        .expect("create_session should succeed against the in-process server");

    let id = body.get("id").and_then(|v| v.as_str());
    let sid = body.get("session_id").and_then(|v| v.as_str());

    assert_eq!(id, Some("g-1:abcdef12"), "id field expected from server");
    assert_eq!(
        sid, id,
        "session_id alias must equal id (CLI prints either via or_else)"
    );
    assert_eq!(
        body.get("session_title").and_then(|v| v.as_str()),
        Some("hello")
    );
    assert_eq!(
        body.get("status").and_then(|v| v.as_str()),
        Some("running")
    );
}

#[tokio::test]
async fn cli_list_sessions_returns_items_array_and_group_id() {
    // CLI parses `result.get("items").and_then(|v| v.as_array())`. If the
    // server ever switches to a top-level array, the loop in main.rs is dead.
    let server = start_server().await;
    let client = BcsClient::with_token(&server.base_url, &server.token);

    // Create one session so the list isn't empty.
    let _ = client
        .create_session("g-1", Some("title"), None, None, None)
        .await
        .unwrap();

    let body = client
        .list_sessions("g-1", None, None, None, None, None)
        .await
        .unwrap();

    let items = body
        .get("items")
        .and_then(|v| v.as_array())
        .expect("response must have an `items` array");
    assert!(!items.is_empty(), "list should include the session we just created");
    assert_eq!(
        body.get("group_id").and_then(|v| v.as_str()),
        Some("g-1"),
        "response must include group_id (used in CLI header line)"
    );

    let first = &items[0];
    assert!(
        first.get("id").is_some() || first.get("session_id").is_some(),
        "each item must expose id or session_id"
    );
    assert!(
        first.get("status").is_some(),
        "each item must expose status (CLI prints `[{{status}}]`)"
    );
}

#[tokio::test]
async fn cli_get_session_returns_full_row() {
    let server = start_server().await;
    let client = BcsClient::with_token(&server.base_url, &server.token);

    let created = client
        .create_session("g-1", None, None, None, None)
        .await
        .unwrap();
    let sid = created.get("id").and_then(|v| v.as_str()).unwrap();

    let body = client.get_session(sid).await.unwrap();
    assert_eq!(body.get("id").and_then(|v| v.as_str()), Some(sid));
    assert_eq!(body.get("group_id").and_then(|v| v.as_str()), Some("g-1"));
    assert_eq!(
        body.get("status").and_then(|v| v.as_str()),
        Some("running")
    );
}

#[tokio::test]
async fn cli_session_messages_returns_array() {
    // CLI calls `result.as_array()` — confirm a top-level array even when empty.
    let server = start_server().await;
    let client = BcsClient::with_token(&server.base_url, &server.token);

    let created = client
        .create_session("g-1", None, None, None, None)
        .await
        .unwrap();
    let sid = created.get("id").and_then(|v| v.as_str()).unwrap();

    let body = client
        .session_messages(sid, None, Some(50), None)
        .await
        .unwrap();
    assert!(
        body.is_array(),
        "messages endpoint must return a JSON array; got {:?}",
        body
    );
    assert_eq!(body.as_array().unwrap().len(), 0);
}
