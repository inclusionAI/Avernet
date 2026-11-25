use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult, DbValue,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AddSessionParticipantWithEvent, AppendEventRecord, NewSessionParams,
    RemoveSessionParticipantWithEvent, SessionRepoPort,
    UpdateSessionParticipantMessageViewScopeWithEvent,
};
use bcs_service_api::types::{
    EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject, MessageViewScope,
};
use bcs_service_api::{Participant, ParticipantMode, ParticipantRole, ServiceError, SessionKind};
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};

use bcs_db_migrations as bootstrap_migrations;

#[path = "support/session_completion_recovery.rs"]
mod session_completion_recovery;

#[tokio::test]
async fn memory_session_repo_passes_session_repo_contract() {
    let repo = MemorySessionRepo::new();
    bcs_test_support::contract::repo::session_repo_port_contract_tests(&repo).await;
}

/// Exercises the `MySqlSessionStore` through its SQLite flavor (same code path
/// as a MySQL/OceanBase deployment, just a different dialect). This covers the
/// SQL-backed create/collect/uncollect/list_collected_by_group/collected_at_map
/// implementations that the memory-only contract test cannot reach.
#[tokio::test]
async fn sqlite_session_repo_passes_session_repo_contract() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db, "dev".to_string());
    bcs_test_support::contract::repo::session_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn memory_session_repo_persists_channel_source_in_session_id() {
    let repo = MemorySessionRepo::new();
    assert_channel_session_id(&repo).await;
}

#[tokio::test]
async fn sqlite_session_repo_persists_channel_source_in_session_id() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db, "dev".to_string());
    assert_channel_session_id(&repo).await;
}

async fn assert_channel_session_id(repo: &dyn SessionRepoPort) {
    let session = repo
        .create_channel(
            "bcs_grp_dingtalk_dm_1234567890abcdef",
            "dingtalk",
            NewSessionParams::default(),
        )
        .await
        .expect("create channel session");

    assert!(
        session
            .id
            .starts_with("bcs_grp_dingtalk_dm_1234567890abcdef:channel_dingtalk_")
    );
    assert_eq!(session.id.matches(':').count(), 1);
    let stored = repo.get(&session.id).await.expect("stored channel session");
    assert_eq!(stored.id, session.id);
    assert_eq!(stored.group_id, session.group_id);
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run sqlite migrations");
    db
}

struct AlwaysFailDb;

#[async_trait]
impl DbPlugin for AlwaysFailDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Err(DbError::Backend("forced session query failure".to_string()))
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        unreachable!("fallible list test does not execute statements")
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        unreachable!("fallible list test does not execute transactions")
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

struct MalformedMembershipRowDb;

#[async_trait]
impl DbPlugin for MalformedMembershipRowDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Ok(vec![DbRow::new(BTreeMap::new())])
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        unreachable!("membership row test does not execute statements")
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        unreachable!("membership row test does not execute transactions")
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

struct FailingExecuteDb {
    fail_on_call: usize,
    call_count: AtomicUsize,
}

impl FailingExecuteDb {
    fn new(fail_on_call: usize) -> Self {
        Self {
            fail_on_call,
            call_count: AtomicUsize::new(0),
        }
    }
}

#[async_trait]
impl DbPlugin for FailingExecuteDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        unreachable!("session delete test does not query")
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        let call = self.call_count.fetch_add(1, Ordering::SeqCst);
        if call == self.fail_on_call {
            Err(DbError::Backend(format!(
                "forced session delete failure at call {call}"
            )))
        } else {
            Ok(DbExecuteResult {
                affected_rows: 1,
                last_insert_id: None,
            })
        }
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        unreachable!("session delete test does not execute transactions")
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

#[tokio::test]
async fn mysql_session_list_propagates_query_failure() {
    let repo = MySqlSessionStore::new(Arc::new(AlwaysFailDb), "dev".to_string());

    let error = repo
        .try_list_by_group("group-1", None, 0, 10, None, None)
        .await
        .expect_err("session query failure must propagate");

    assert!(error.to_string().contains("forced session query failure"));
}

#[tokio::test]
async fn mysql_session_participant_group_list_propagates_query_failure() {
    let repo = MySqlSessionStore::new(Arc::new(AlwaysFailDb), "dev".to_string());

    let error = repo
        .try_list_group_ids_by_session_participant("bot-1")
        .await
        .expect_err("session participant query failure must propagate");

    assert!(error.to_string().contains("forced session query failure"));
}

#[tokio::test]
async fn mysql_session_participant_group_list_propagates_row_failure() {
    let repo = MySqlSessionStore::new(Arc::new(MalformedMembershipRowDb), "dev".to_string());

    let error = repo
        .try_list_group_ids_by_session_participant("bot-1")
        .await
        .expect_err("malformed session participant row must propagate");

    assert!(error.to_string().contains("group_id"));
}

#[tokio::test]
async fn mysql_session_delete_propagates_participant_delete_failure() {
    let repo = MySqlSessionStore::new(Arc::new(FailingExecuteDb::new(0)), "dev".to_string());

    let error = repo
        .delete("group-1:00000001")
        .await
        .expect_err("participant delete failure must propagate");

    assert!(
        error
            .to_string()
            .contains("forced session delete failure at call 0")
    );
}

#[tokio::test]
async fn mysql_session_delete_propagates_session_delete_failure() {
    let repo = MySqlSessionStore::new(Arc::new(FailingExecuteDb::new(1)), "dev".to_string());

    let error = repo
        .delete("group-1:00000001")
        .await
        .expect_err("session row delete failure must propagate");

    assert!(
        error
            .to_string()
            .contains("forced session delete failure at call 1")
    );
}

#[tokio::test]
async fn memory_session_metrics_snapshot_port_contract() {
    let repo = MemorySessionRepo::new();
    repo.create(
        "metrics-group",
        NewSessionParams {
            session_kind: SessionKind::ServiceInvocation,
            participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
            ..Default::default()
        },
    )
    .await
    .expect("create session");

    bcs_test_support::contract::port::group_session_metrics_snapshot_port_contract_tests(&repo)
        .await;
}

#[tokio::test]
async fn fallible_session_lookup_distinguishes_missing_rows_and_failures() {
    let db = sqlite_db().await;
    let sql_repo = MySqlSessionStore::sqlite(db, "dev".to_string());
    let memory_repo = MemorySessionRepo::new();
    for repo in [&sql_repo as &dyn SessionRepoPort, &memory_repo] {
        assert!(repo.try_get("missing").await.expect("missing lookup").is_none());
        let session = repo.create("group_1", NewSessionParams::default())
            .await.expect("create session");
        let stored = repo.try_get(&session.id).await.expect("successful lookup")
            .expect("stored session");
        assert_eq!(stored.id, session.id);
    }
    let failing = MySqlSessionStore::sqlite(Arc::new(AlwaysFailDb), "dev".to_string());
    let error = failing.try_get("group_1:session").await.expect_err("query failure");
    assert!(error.to_string().contains("forced session query failure"));
    let malformed = MySqlSessionStore::sqlite(Arc::new(MalformedMembershipRowDb), "dev".to_string());
    assert!(malformed.try_get("group_1:session").await.is_err());
}

fn scope_change_event(session_id: &str, group_id: &str) -> AppendEventRecord {
    AppendEventRecord {
        event: NewEvent {
            event_id: "evt-scope-change-1".to_string(),
            event_type: "session.participant.message_view_scope_changed".to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "session-store-test".to_string(),
            producer_key: "evt-scope-change-1".to_string(),
            occurred_at: "2026-09-10T00:00:00.000Z".to_string(),
            subject: EventSubject {
                subject_type: "session.participant".to_string(),
                id: "human-1".to_string(),
            },
            scope: EventScope {
                group_id: Some(group_id.to_string()),
                session_id: Some(session_id.to_string()),
                ..EventScope::default()
            },
            stream_key: format!("session:{session_id}"),
            actor: None,
            correlation_id: None,
            causation_event_id: None,
            trace_id: None,
            data: BTreeMap::new(),
        },
        recorded_at: "2026-09-10T00:00:00.001Z".to_string(),
        retention_until_ms: 2_000_000_000_000,
        env: "dev".to_string(),
    }
}

/// Rewrite the stored `participants` TEXT into pre-`message_view_scope` legacy
/// bytes: structurally identical after parsing (the field defaults to `full`),
/// but byte-different from what the current serializer produces.
async fn rewrite_participants_as_legacy_bytes(db: &Arc<dyn DbPlugin>, session_id: &str) {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT participants FROM bcs_group_sessions WHERE env = ? AND session_id = ?",
            vec![DbValue::from("dev"), DbValue::from(session_id)],
        ))
        .await
        .expect("read participants");
    let raw = rows[0]
        .get_string("participants")
        .expect("participants column")
        .expect("participants not null");
    let mut parsed: Vec<serde_json::Value> = serde_json::from_str(&raw).expect("participants json");
    for participant in parsed.iter_mut() {
        participant
            .as_object_mut()
            .expect("participant object")
            .remove("message_view_scope");
    }
    let legacy = serde_json::to_string(&parsed).expect("legacy json");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_group_sessions SET participants = ? WHERE env = ? AND session_id = ?",
        vec![
            DbValue::from(legacy.as_str()),
            DbValue::from("dev"),
            DbValue::from(session_id),
        ],
    ))
    .await
    .expect("rewrite legacy participants");
}

async fn create_legacy_scope_session(
    repo: &MySqlSessionStore,
    db: &Arc<dyn DbPlugin>,
) -> bcs_service_api::Session {
    let session = repo
        .create(
            "group-legacy",
            NewSessionParams {
                participants: vec![
                    Participant::bot("bot-1", ParticipantRole::Driver),
                    Participant::human("human-1", ParticipantRole::Observer),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("create session");
    rewrite_participants_as_legacy_bytes(db, &session.id).await;
    session
}

#[tokio::test]
async fn sqlite_scope_update_with_event_succeeds_on_legacy_participants_bytes() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());
    let session = create_legacy_scope_session(&repo, &db).await;

    let stored = repo.get(&session.id).await.expect("stored session");
    let updated = repo
        .update_participant_message_view_scope_with_event(
            UpdateSessionParticipantMessageViewScopeWithEvent {
                session_id: session.id.clone(),
                expected_participants: stored.participants.clone(),
                actor_id: "human-1".to_string(),
                message_view_scope: MessageViewScope::Participant,
                mode: None,
                event: scope_change_event(&session.id, &session.group_id),
            },
        )
        .await
        .expect("legacy participants bytes must not break the scope CAS");

    let human = updated
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human-1")
        .expect("human participant");
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);

    let reloaded = repo.get(&session.id).await.expect("reload session");
    let stored_human = reloaded
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human-1")
        .expect("human participant");
    assert_eq!(
        stored_human.message_view_scope,
        MessageViewScope::Participant
    );
}

#[tokio::test]
async fn sqlite_mode_and_scope_update_succeeds_on_legacy_participants_bytes() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());
    let session = create_legacy_scope_session(&repo, &db).await;

    let updated = repo
        .update_participant_mode_and_message_view_scope(
            &session.id,
            "human-1",
            Some(ParticipantMode::Present),
            MessageViewScope::Participant,
        )
        .await
        .expect("legacy participants bytes must not break the scope CAS");

    let human = updated
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human-1")
        .expect("human participant");
    assert_eq!(human.message_view_scope, MessageViewScope::Participant);
    assert_eq!(human.mode, Some(ParticipantMode::Present));
}

/// Wraps a real DbPlugin and, when armed, mutates the session participants
/// immediately before delegating a transaction — simulating a concurrent
/// writer landing between the store's pre-check read and its lock step.
struct ScopeRaceDb {
    inner: Arc<dyn DbPlugin>,
    sabotage: AtomicBool,
    fail_transaction: AtomicBool,
}

#[async_trait]
impl DbPlugin for ScopeRaceDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.fail_transaction.swap(false, Ordering::SeqCst) {
            return Err(DbError::Backend("forced transaction failure".to_string()));
        }
        if self.sabotage.swap(false, Ordering::SeqCst) {
            self.inner
                .execute(DbStatement::with_params(
                    "UPDATE bcs_group_sessions SET participants = ? WHERE env = ?",
                    vec![DbValue::from("[]"), DbValue::from("dev")],
                ))
                .await?;
        }
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

#[tokio::test]
async fn sqlite_scope_update_race_reports_clean_conflict() {
    let inner = sqlite_db().await;
    let db = Arc::new(ScopeRaceDb {
        inner,
        sabotage: AtomicBool::new(false),
        fail_transaction: AtomicBool::new(false),
    });
    let repo = MySqlSessionStore::sqlite(db.clone() as Arc<dyn DbPlugin>, "dev".to_string());
    let session = repo
        .create(
            "group-race",
            NewSessionParams {
                participants: vec![Participant::human("human-1", ParticipantRole::Observer)],
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let stored = repo.get(&session.id).await.expect("stored session");
    db.sabotage.store(true, Ordering::SeqCst);

    let error = repo
        .update_participant_message_view_scope_with_event(
            UpdateSessionParticipantMessageViewScopeWithEvent {
                session_id: session.id.clone(),
                expected_participants: stored.participants.clone(),
                actor_id: "human-1".to_string(),
                message_view_scope: MessageViewScope::Participant,
                mode: None,
                event: scope_change_event(&session.id, &session.group_id),
            },
        )
        .await
        .expect_err("concurrent writer must produce a conflict");

    match &error {
        ServiceError::Conflict(message) => {
            assert!(
                !message.contains("references missing row"),
                "conflict must not leak transaction binding internals: {message}"
            );
            assert!(
                !message.contains("transaction parameter"),
                "conflict must not leak transaction binding internals: {message}"
            );
            assert!(
                message.contains(&session.id),
                "conflict should identify the session: {message}"
            );
        }
        other => panic!("expected Conflict, got {other:?}"),
    }
}

#[tokio::test]
async fn sqlite_add_participant_with_event_succeeds_on_legacy_participants_bytes() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());
    let session = create_legacy_scope_session(&repo, &db).await;

    let stored = repo.get(&session.id).await.expect("stored session");
    let updated = repo
        .add_participant_with_event(AddSessionParticipantWithEvent {
            session_id: session.id.clone(),
            expected_participants: stored.participants.clone(),
            participant: Participant::bot("bot-2", ParticipantRole::Consultant),
            event: scope_change_event(&session.id, &session.group_id),
        })
        .await
        .expect("legacy participants bytes must not break the addition CAS");

    assert!(
        updated
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "bot-2")
    );
    let reloaded = repo.get(&session.id).await.expect("reload session");
    assert!(
        reloaded
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "bot-2")
    );
}

#[tokio::test]
async fn sqlite_remove_participant_with_event_succeeds_on_legacy_participants_bytes() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());
    let session = create_legacy_scope_session(&repo, &db).await;

    let stored = repo.get(&session.id).await.expect("stored session");
    let updated = repo
        .remove_participant_with_event(RemoveSessionParticipantWithEvent {
            session_id: session.id.clone(),
            expected_participants: stored.participants.clone(),
            bot_uuid: "human-1".to_string(),
            event: scope_change_event(&session.id, &session.group_id),
        })
        .await
        .expect("legacy participants bytes must not break the removal CAS");

    assert!(
        !updated
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "human-1")
    );
    let reloaded = repo.get(&session.id).await.expect("reload session");
    assert!(
        !reloaded
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "human-1")
    );
}

#[tokio::test]
async fn sqlite_add_participant_with_event_race_reports_clean_conflict() {
    let inner = sqlite_db().await;
    let db = Arc::new(ScopeRaceDb {
        inner,
        sabotage: AtomicBool::new(false),
        fail_transaction: AtomicBool::new(false),
    });
    let repo = MySqlSessionStore::sqlite(db.clone() as Arc<dyn DbPlugin>, "dev".to_string());
    let session = repo
        .create(
            "group-race",
            NewSessionParams {
                participants: vec![Participant::bot("bot-1", ParticipantRole::Driver)],
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let stored = repo.get(&session.id).await.expect("stored session");
    db.sabotage.store(true, Ordering::SeqCst);

    let error = repo
        .add_participant_with_event(AddSessionParticipantWithEvent {
            session_id: session.id.clone(),
            expected_participants: stored.participants.clone(),
            participant: Participant::bot("bot-2", ParticipantRole::Consultant),
            event: scope_change_event(&session.id, &session.group_id),
        })
        .await
        .expect_err("concurrent writer must produce a conflict");

    match &error {
        ServiceError::Conflict(message) => {
            assert!(
                !message.contains("references missing row"),
                "conflict must not leak transaction binding internals: {message}"
            );
            assert!(
                !message.contains("transaction parameter"),
                "conflict must not leak transaction binding internals: {message}"
            );
            assert!(
                message.contains(&session.id),
                "conflict should identify the session: {message}"
            );
        }
        other => panic!("expected Conflict, got {other:?}"),
    }
}

#[tokio::test]
async fn sqlite_remove_participant_with_event_race_reports_clean_conflict() {
    let inner = sqlite_db().await;
    let db = Arc::new(ScopeRaceDb {
        inner,
        sabotage: AtomicBool::new(false),
        fail_transaction: AtomicBool::new(false),
    });
    let repo = MySqlSessionStore::sqlite(db.clone() as Arc<dyn DbPlugin>, "dev".to_string());
    let session = repo
        .create(
            "group-race",
            NewSessionParams {
                participants: vec![
                    Participant::bot("bot-1", ParticipantRole::Driver),
                    Participant::human("human-1", ParticipantRole::Observer),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let stored = repo.get(&session.id).await.expect("stored session");
    db.sabotage.store(true, Ordering::SeqCst);

    let error = repo
        .remove_participant_with_event(RemoveSessionParticipantWithEvent {
            session_id: session.id.clone(),
            expected_participants: stored.participants.clone(),
            bot_uuid: "human-1".to_string(),
            event: scope_change_event(&session.id, &session.group_id),
        })
        .await
        .expect_err("concurrent writer must produce a conflict");

    match &error {
        ServiceError::Conflict(message) => {
            assert!(
                !message.contains("references missing row"),
                "conflict must not leak transaction binding internals: {message}"
            );
            assert!(
                !message.contains("transaction parameter"),
                "conflict must not leak transaction binding internals: {message}"
            );
            assert!(
                message.contains(&session.id),
                "conflict should identify the session: {message}"
            );
        }
        other => panic!("expected Conflict, got {other:?}"),
    }
}

#[tokio::test]
async fn sqlite_add_participant_with_event_db_failure_maps_to_internal_error() {
    let inner = sqlite_db().await;
    let db = Arc::new(ScopeRaceDb {
        inner,
        sabotage: AtomicBool::new(false),
        fail_transaction: AtomicBool::new(false),
    });
    let repo = MySqlSessionStore::sqlite(db.clone() as Arc<dyn DbPlugin>, "dev".to_string());
    let session = repo
        .create("group-db-fail", NewSessionParams::default())
        .await
        .expect("create session");

    let stored = repo.get(&session.id).await.expect("stored session");
    db.fail_transaction.store(true, Ordering::SeqCst);

    let error = repo
        .add_participant_with_event(AddSessionParticipantWithEvent {
            session_id: session.id.clone(),
            expected_participants: stored.participants.clone(),
            participant: Participant::bot("bot-2", ParticipantRole::Consultant),
            event: scope_change_event(&session.id, &session.group_id),
        })
        .await
        .expect_err("db failure must propagate");

    match &error {
        ServiceError::InternalError(message) => {
            assert!(
                message.contains("forced transaction failure"),
                "internal error should carry the db failure: {message}"
            );
        }
        other => panic!("expected InternalError, got {other:?}"),
    }
}

#[tokio::test]
async fn sqlite_remove_participant_with_event_db_failure_maps_to_internal_error() {
    let inner = sqlite_db().await;
    let db = Arc::new(ScopeRaceDb {
        inner,
        sabotage: AtomicBool::new(false),
        fail_transaction: AtomicBool::new(false),
    });
    let repo = MySqlSessionStore::sqlite(db.clone() as Arc<dyn DbPlugin>, "dev".to_string());
    let session = repo
        .create(
            "group-db-fail",
            NewSessionParams {
                participants: vec![Participant::human("human-1", ParticipantRole::Observer)],
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let stored = repo.get(&session.id).await.expect("stored session");
    db.fail_transaction.store(true, Ordering::SeqCst);

    let error = repo
        .remove_participant_with_event(RemoveSessionParticipantWithEvent {
            session_id: session.id.clone(),
            expected_participants: stored.participants.clone(),
            bot_uuid: "human-1".to_string(),
            event: scope_change_event(&session.id, &session.group_id),
        })
        .await
        .expect_err("db failure must propagate");

    match &error {
        ServiceError::InternalError(message) => {
            assert!(
                message.contains("forced transaction failure"),
                "internal error should carry the db failure: {message}"
            );
        }
        other => panic!("expected InternalError, got {other:?}"),
    }
}

/// Delegates to a real backend while counting `query` round trips, so the
/// chunked activity aggregate's round-trip count is asserted instead of
/// assumed (the message-store activity tests use the same recording shape).
struct CountingQueryDb {
    inner: Arc<dyn DbPlugin>,
    queries: AtomicUsize,
}

/// SQLite-backed recorder for the D5-revised read-shape assertions: every
/// `query` the store issues is captured with its parameter count so the
/// tests can prove the candidate read carries no LIMIT and the page-row
/// fetch chunks at the bind bound.
struct RecordingQueryDb {
    inner: Arc<dyn DbPlugin>,
    statements: std::sync::Mutex<Vec<String>>,
    param_counts: std::sync::Mutex<Vec<usize>>,
}

impl RecordingQueryDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            statements: std::sync::Mutex::new(Vec::new()),
            param_counts: std::sync::Mutex::new(Vec::new()),
        }
    }

    fn recordings(&self) -> (Vec<String>, Vec<usize>) {
        (
            self.statements.lock().expect("recording lock").clone(),
            self.param_counts.lock().expect("recording lock").clone(),
        )
    }
}

#[async_trait]
impl DbPlugin for RecordingQueryDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let sql = statement.sql().to_string();
        let params = statement.params().len();
        self.statements.lock().expect("recording lock").push(sql);
        self.param_counts.lock().expect("recording lock").push(params);
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}


impl CountingQueryDb {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            queries: AtomicUsize::new(0),
        }
    }
}

#[async_trait]
impl DbPlugin for CountingQueryDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.queries.fetch_add(1, Ordering::SeqCst);
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// Pin a session row's stored `gmt_create` (epoch seconds) so the activity
/// aggregate can be asserted on exact values instead of the second-granularity
/// insert timestamp.
async fn set_session_gmt_create(
    db: &Arc<dyn DbPlugin>,
    env: &str,
    session_id: &str,
    epoch_secs: i64,
) {
    let result = db
        .execute(DbStatement::with_params(
            "UPDATE bcs_group_sessions SET gmt_create = datetime(?, 'unixepoch') \
             WHERE env = ? AND session_id = ?",
            vec![
                DbValue::I64(epoch_secs),
                DbValue::from(env),
                DbValue::from(session_id),
            ],
        ))
        .await
        .expect("set session gmt_create");
    assert_eq!(
        result.affected_rows, 1,
        "gmt_create update must match the seeded session row"
    );
}

/// Empty candidate set: the group list can paginate to nothing, so an empty id
/// list must answer without issuing an (invalid) `IN ()` query.
#[tokio::test]
async fn mysql_max_created_at_by_group_ids_is_empty_without_querying() {
    let repo = MySqlSessionStore::sqlite(Arc::new(AlwaysFailDb), "dev".to_string());

    assert!(
        repo.max_created_at_by_group_ids(&[])
            .await
            .expect("empty id list must not query")
            .is_empty()
    );
}

/// Per-group activity is `MAX(gmt_create)` in epoch ms, matching the epoch-ms
/// `Session.created_at` the same store projects for the newest session.
#[tokio::test]
async fn sqlite_max_created_at_by_group_ids_returns_per_group_maximum() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());

    let mut newest = String::new();
    for (group_id, epoch_secs) in [
        ("act-g-a", 1_700_000_001_i64),
        ("act-g-a", 1_700_000_005),
        ("act-g-a", 1_700_000_003),
        ("act-g-b", 1_700_000_002),
        ("act-g-b", 1_700_000_007),
    ] {
        let session = repo
            .create(group_id, NewSessionParams::default())
            .await
            .expect("create session");
        set_session_gmt_create(&db, "dev", &session.id, epoch_secs).await;
        if epoch_secs == 1_700_000_005 {
            newest = session.id;
        }
    }

    let created = repo
        .max_created_at_by_group_ids(&[
            "act-g-a".to_string(),
            "act-g-b".to_string(),
            "act-g-missing".to_string(),
        ])
        .await
        .expect("group activity key");

    assert_eq!(created.len(), 2, "groups without sessions stay absent");
    assert_eq!(created.get("act-g-a"), Some(&1_700_000_005_000));
    assert_eq!(created.get("act-g-b"), Some(&1_700_000_007_000));

    let projected = repo.get(&newest).await.expect("stored session");
    assert_eq!(
        created.get("act-g-a"),
        Some(&projected.created_at),
        "activity key must use the Session projection's epoch-ms conversion"
    );
}

/// A hard-deleted session stops contributing (design D1, Review Focus #7 store
/// half): the group key drops to the remaining session, then the group leaves
/// the map entirely once its last session is deleted through the store.
#[tokio::test]
async fn sqlite_max_created_at_by_group_ids_drops_hard_deleted_sessions() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());

    let older = repo
        .create("act-del-g", NewSessionParams::default())
        .await
        .expect("create older session");
    set_session_gmt_create(&db, "dev", &older.id, 1_700_000_100).await;
    let newest = repo
        .create("act-del-g", NewSessionParams::default())
        .await
        .expect("create newest session");
    set_session_gmt_create(&db, "dev", &newest.id, 1_700_000_900).await;

    let before = repo
        .max_created_at_by_group_ids(&["act-del-g".to_string()])
        .await
        .expect("group activity key");
    assert_eq!(before.get("act-del-g"), Some(&1_700_000_900_000));

    assert!(repo.delete(&newest.id).await.expect("delete newest session"));
    let after_newest = repo
        .max_created_at_by_group_ids(&["act-del-g".to_string()])
        .await
        .expect("group activity key");
    assert_eq!(after_newest.get("act-del-g"), Some(&1_700_000_100_000));

    assert!(repo.delete(&older.id).await.expect("delete remaining session"));
    assert!(
        repo.max_created_at_by_group_ids(&["act-del-g".to_string()])
            .await
            .expect("group activity key")
            .is_empty(),
        "a group with no persisted sessions is absent from the map"
    );
}

/// More candidate groups than one bind chunk (200): every id is answered and
/// the lookup costs exactly two aggregate round trips.
#[tokio::test]
async fn sqlite_max_created_at_by_group_ids_batches_past_one_chunk() {
    let db = sqlite_db().await;
    let counting = Arc::new(CountingQueryDb::new(db.clone()));
    let repo = MySqlSessionStore::sqlite(counting.clone(), "dev".to_string());

    let group_ids: Vec<String> = (0..250).map(|index| format!("act-chunk-{index:03}")).collect();
    for (index, group_id) in group_ids.iter().enumerate() {
        let session = repo
            .create(group_id, NewSessionParams::default())
            .await
            .expect("create session");
        set_session_gmt_create(&db, "dev", &session.id, 1_700_000_000 + index as i64).await;
    }

    counting.queries.store(0, Ordering::SeqCst);
    let created = repo
        .max_created_at_by_group_ids(&group_ids)
        .await
        .expect("group activity key");

    assert_eq!(
        counting.queries.load(Ordering::SeqCst),
        2,
        "250 ids are two 200-id chunk round trips"
    );
    assert_eq!(created.len(), 250);
    assert_eq!(created.get("act-chunk-000"), Some(&1_700_000_000_000));
    assert_eq!(created.get("act-chunk-249"), Some(&1_700_000_249_000));
}

/// D5 revision: the candidate read returns the FULL matching set in ONE
/// round trip whose SQL carries no LIMIT / OFFSET / ORDER BY and selects
/// only the two sort columns — a stale COUNT result can never truncate the
/// recent-activity sort set, and no full Session row is hydrated for it.
#[tokio::test]
async fn sqlite_activity_candidates_full_set_in_one_unlimited_query() {
    let db = sqlite_db().await;
    let recording = Arc::new(RecordingQueryDb::new(db));
    let repo = MySqlSessionStore::sqlite(recording.clone(), "dev".to_string());

    let mut ids = Vec::new();
    for _ in 0..205_u32 {
        let session = repo
            .create("act-cand-g", NewSessionParams::default())
            .await
            .expect("create session");
        ids.push(session.id);
    }

    assert_eq!(
        recording.recordings().0.len(),
        0,
        "create() writes must not show up as queries"
    );

    let candidates = repo
        .list_activity_candidates_by_group("act-cand-g", None, None, None)
        .await
        .expect("activity candidates");

    assert_eq!(candidates.len(), 205, "the whole set, no COUNT-derived truncation");
    let candidate_ids: std::collections::HashSet<&str> =
        candidates.iter().map(|candidate| candidate.session_id.as_str()).collect();
    assert_eq!(candidate_ids.len(), ids.len());
    for id in &ids {
        assert!(candidate_ids.contains(id.as_str()), "missing candidate {id}");
    }

    let (statements, _) = recording.recordings();
    assert_eq!(statements.len(), 1, "exactly one candidate round trip");
    let sql = statements[0].to_uppercase();
    assert!(!sql.contains("LIMIT"), "candidate query must carry no LIMIT: {sql}");
    assert!(!sql.contains("OFFSET"), "candidate query must carry no OFFSET: {sql}");
    assert!(!sql.contains("ORDER BY"), "no ORDER BY for the caller to redo: {sql}");
    assert!(
        !sql.contains("INPUT") && !sql.contains("OUTPUT") && !sql.contains("META"),
        "the lightweight projection must not select full-row columns: {sql}"
    );
}

/// The batched page-row fetch chunks at the store's 200-bind bound and returns
/// only the requested rows; a vanished id is absent, never an error.
#[tokio::test]
async fn sqlite_try_get_many_chunks_beyond_the_bind_bound() {
    let db = sqlite_db().await;
    let recording = Arc::new(RecordingQueryDb::new(db));
    let repo = MySqlSessionStore::sqlite(recording.clone(), "dev".to_string());

    let mut ids = Vec::new();
    for _ in 0..205_u32 {
        let session = repo
            .create("act-batch-g", NewSessionParams::default())
            .await
            .expect("create session");
        ids.push(session.id);
    }
    ids.push("act-batch-g:ffffffff".to_string());

    let sessions = repo.try_get_many(&ids).await.expect("batched page rows");

    assert_eq!(sessions.len(), 205, "missing ids are absent, never an error");
    let (statements, param_counts) = recording.recordings();
    assert_eq!(statements.len(), 2, "ceil(206 / 200) round trips");
    assert_eq!(param_counts, vec![201, 7], "env + at most 200 ids per chunk");
    for sql in &statements {
        assert!(sql.contains("session_id IN"), "chunked id fetch: {sql}");
    }
}

/// Store-owned env isolation (design D1/D5): the same group id in another env
/// never contributes, even when its session is much newer.
#[tokio::test]
async fn sqlite_max_created_at_by_group_ids_never_counts_another_environment() {
    let db = sqlite_db().await;
    let dev = MySqlSessionStore::sqlite(db.clone(), "dev".to_string());
    let prod = MySqlSessionStore::sqlite(db.clone(), "prod".to_string());

    let dev_session = dev
        .create("act-shared-g", NewSessionParams::default())
        .await
        .expect("create dev session");
    set_session_gmt_create(&db, "dev", &dev_session.id, 1_700_000_100).await;
    let prod_session = prod
        .create("act-shared-g", NewSessionParams::default())
        .await
        .expect("create prod session");
    set_session_gmt_create(&db, "prod", &prod_session.id, 1_700_000_900).await;
    let prod_only = prod
        .create("act-prod-only-g", NewSessionParams::default())
        .await
        .expect("create prod-only session");
    set_session_gmt_create(&db, "prod", &prod_only.id, 1_700_000_900).await;

    let created = dev
        .max_created_at_by_group_ids(&[
            "act-shared-g".to_string(),
            "act-prod-only-g".to_string(),
        ])
        .await
        .expect("group activity key");

    assert_eq!(created.len(), 1);
    assert_eq!(created.get("act-shared-g"), Some(&1_700_000_100_000));
}
