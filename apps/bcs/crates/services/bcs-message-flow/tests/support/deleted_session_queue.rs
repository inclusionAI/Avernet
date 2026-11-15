//! A Session deleted while its task deliveries are pending must never pin
//! the queue: transitions converge, notices for the deleted Session are
//! dropped, and Session deletion settles the remaining lane rows.
use super::*;
use bcs_db_api::{DbPlugin, DbStatement};

struct SqliteFixture { f: Fixture, db: Arc<bcs_db_local::LocalSqliteDbPlugin> }

async fn sqlite_fixture() -> SqliteFixture {
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:task', 'group-1', 'dev', '[]')")).await.unwrap();
    let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "dev".into()));
    SqliteFixture { f: Fixture::with_repos(repo.clone(), repo).await, db }
}

impl SqliteFixture {
    async fn delete_session(&self) {
        self.db.execute(DbStatement::new("DELETE FROM bcs_group_sessions WHERE session_id = 'group-1:task'")).await.unwrap();
    }
    async fn assign(&self, intent: &str) -> PersistedMessageDelivery {
        let mut cmd = dispatch_command();
        cmd.payload["assignment_intent_id"] = json!(format!("bcs_intent_{intent}"));
        let task = self.f.flow.handle_task_dispatch(cmd).await.unwrap();
        self.f.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["task_id"] == task.task_id).unwrap()
    }
    async fn row(&self, id: &str) -> PersistedMessageDelivery {
        self.f.rows().await.into_iter().find(|r| r.delivery_id == id).unwrap()
    }
    async fn result_legs(&self) -> usize {
        self.f.rows().await.iter().filter(|r| r.semantic_projection_json["task"]["leg"] == "result").count()
    }
}

/// Bounded: before the fix the scheduler retried this forever.
async fn bounded<T>(work: impl Future<Output = T>) -> T {
    tokio::time::timeout(std::time::Duration::from_secs(5), work).await.expect("must not hang")
}

#[tokio::test]
async fn cancelled_task_in_deleted_session_converges_without_its_notice() {
    let s = sqlite_fixture().await;
    let row = s.assign("aaaaaaaa000000000000000000000000").await;
    s.delete_session().await;
    let cancelled = bounded(s.f.service.transition(DeliveryTransitionCommand { caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None,
        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version, event: Event::CancelRequested,
        now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
        transport_context_json: Some(json!({"cancel_reason": "expired"})), deadline_at_ms: None })).await
        .expect("a deleted Session is not a storage outage");
    assert_eq!(cancelled.state.status, Status::Cancelled);
    assert_eq!(s.result_legs().await, 0, "no notice is enqueued into the deleted Session");
}

#[tokio::test]
async fn takeover_recovery_in_deleted_session_converges_without_its_notice() {
    let s = sqlite_fixture().await;
    let row = s.assign("bbbbbbbb000000000000000000000000").await;
    let started = s.f.start(&row).await;
    s.delete_session().await;
    bounded(s.f.service.recover(chrono::Utc::now().timestamp_millis())).await
        .expect("recovery must not stall on a deleted Session");
    assert_eq!(s.row(&started.delivery_id).await.state.status, Status::Unknown);
    assert_eq!(s.f.rows().await.len(), 1, "the uncertainty notice is dropped, not enqueued");
}

#[tokio::test]
async fn deleting_a_session_settles_its_lane() {
    let s = sqlite_fixture().await;
    let first = s.assign("cccccccc000000000000000000000000").await;
    let started = s.f.start(&first).await;
    let queued = s.assign("dddddddd000000000000000000000000").await;
    // Cleanup runs before the Session row is removed; the marker alone must
    // keep the cancelled task's notice out of the still-present Session.
    bounded(s.f.flow.cancel_session_deliveries(SESSION, "session_deleted")).await
        .expect("cleanup succeeds");
    assert_eq!(s.row(&queued.delivery_id).await.state.status, Status::Cancelled, "never-sent rows are cancelled");
    let cancelling = s.row(&started.delivery_id).await;
    assert_eq!(cancelling.state.status, Status::Cancelling, "maybe-sent rows get a cancel intent for the scheduler");
    assert_eq!(cancelling.cancel_reason.as_deref(), Some("session_deleted"));
    assert_eq!(s.result_legs().await, 0);
    // Idempotent: a second sweep finds nothing left to change.
    bounded(s.f.flow.cancel_session_deliveries(SESSION, "session_deleted")).await.unwrap();
}
