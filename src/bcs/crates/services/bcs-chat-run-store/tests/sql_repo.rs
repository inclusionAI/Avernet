use std::sync::Arc;

use bcs_cache_local::InMemoryCachePlugin;
use bcs_chat_run_store::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, SqlChatRunRepo,
};
use bcs_db_api::DbSqlFlavor;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::ChatResponseMode;

fn repo() -> SqlChatRunRepo {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let cache = Arc::new(InMemoryCachePlugin::new());
    SqlChatRunRepo::new(db, DbSqlFlavor::Sqlite, cache, "bcs:".to_string(), 120_000)
}

fn record(run_id: &str, version: u64) -> ChatRunRecord {
    let mut record = ChatRunRecord::new(
        run_id.to_string(),
        "bot".to_string(),
        "from".to_string(),
        "sk".to_string(),
        0,
        9_000_000_000_000,
        Some("http-chat-async".to_string()),
        ChatResponseMode::Full,
        ChatRunCompletionPolicy::WaitForFinal,
    );
    record.version = version;
    record
}

#[tokio::test]
async fn create_and_get_roundtrip() {
    let repo = repo();
    repo.create(record("r1", 1)).await.unwrap();
    let stored = repo.get("r1").await.unwrap().unwrap();
    assert_eq!(stored.run_id, "r1");
    assert_eq!(stored.state, ChatRunState::Pending);
    assert_eq!(stored.version, 1);
    assert!(repo.get("missing").await.unwrap().is_none());
}

#[tokio::test]
async fn create_duplicate_rejected() {
    let repo = repo();
    repo.create(record("dup", 1)).await.unwrap();
    match repo.create(record("dup", 1)).await {
        Err(ChatRunRepoError::DuplicateRunId(id)) => assert_eq!(id, "dup"),
        other => panic!("expected DuplicateRunId, got {other:?}"),
    }
}

#[tokio::test]
async fn cas_state_applies_and_terminal_is_immutable() {
    let repo = repo();
    repo.create(record("s", 1)).await.unwrap();
    let mut running = record("s", 1);
    running.state = ChatRunState::Running;
    match repo.compare_and_set_state("s", 1, running).await.unwrap() {
        CasOutcome::Applied(r) => assert_eq!(r.version, 2),
        other => panic!("expected Applied, got {other:?}"),
    }
    // Terminal transition persists content + completed_at and clears cache overlay.
    let mut completed = record("s", 2);
    completed.state = ChatRunState::Completed;
    completed.accumulated_content = "final".to_string();
    repo.compare_and_set_terminal("s", 2, completed)
        .await
        .unwrap();
    let stored = repo.get("s").await.unwrap().unwrap();
    assert_eq!(stored.state, ChatRunState::Completed);
    assert_eq!(stored.accumulated_content, "final");
    assert!(stored.completed_at_ms.is_some());
    // Post-terminal state CAS is rejected.
    match repo
        .compare_and_set_state("s", stored.version, record("s", stored.version))
        .await
        .unwrap()
    {
        CasOutcome::Terminal(_) => {}
        other => panic!("expected Terminal, got {other:?}"),
    }
}

#[tokio::test]
async fn streaming_append_advances_cache_ahead_of_db() {
    let repo = repo();
    repo.create(record("a", 1)).await.unwrap();
    assert!(
        repo.append_streaming_content("a", 1, "hello".to_string(), false)
            .await
            .unwrap()
    );
    let merged = repo.get("a").await.unwrap().unwrap();
    assert_eq!(merged.accumulated_content, "hello");
    assert_eq!(merged.state, ChatRunState::Running);
    assert_eq!(merged.version, 2);
    // Stale expected version refuses the next append.
    assert!(
        !repo
            .append_streaming_content("a", 1, "x".to_string(), false)
            .await
            .unwrap()
    );
}

#[tokio::test]
async fn list_active_and_delete_expired_terminal() {
    let repo = repo();
    let mut overdue = record("overdue", 1);
    overdue.expires_at_ms = 5;
    let mut future = record("future", 1);
    future.expires_at_ms = 9_000_000_000_000;
    repo.create(overdue).await.unwrap();
    repo.create(future).await.unwrap();
    let active = repo.list_active(10).await.unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].run_id, "overdue");

    // Mark overdue terminal then delete past retention.
    let mut failed = active[0].clone();
    failed.state = ChatRunState::Failed;
    failed.completed_at_ms = Some(0);
    repo.compare_and_set_terminal("overdue", failed.version, failed)
        .await
        .unwrap();
    let dropped = repo.delete_expired_terminal(100, 50).await.unwrap();
    assert_eq!(dropped.len(), 1);
    assert_eq!(dropped[0].run_id, "overdue");
    // The deleted record carries `client` so the engine can attribute the
    // Dropped lifecycle event without a separate full-table client scan.
    assert_eq!(dropped[0].client.as_deref(), Some("http-chat-async"));
    assert!(repo.get("overdue").await.unwrap().is_none());
    assert!(repo.get("future").await.unwrap().is_some());
}

#[tokio::test]
async fn metric_counts_counts_active_runs_only() {
    let repo = repo();
    repo.create(record("m1", 1)).await.unwrap();
    repo.create(record("m2", 1)).await.unwrap();
    // A terminal run must NOT appear on the gauge — terminal totals come from
    // the lifecycle counter, and counting retained terminal rows would be a
    // meaningless cumulative.
    let mut done = record("done", 1);
    done.state = ChatRunState::Completed;
    done.completed_at_ms = Some(0);
    repo.create(done).await.unwrap();
    let counts = repo.metric_counts().await.unwrap();
    let pending = counts
        .iter()
        .find(|c| c.state == bcs_service_api::DirectChatRunState::Pending)
        .map(|c| c.count)
        .unwrap_or(0);
    assert_eq!(pending, 2);
    assert!(
        counts
            .iter()
            .all(|c| !matches!(c.state, bcs_service_api::DirectChatRunState::Completed)),
        "terminal runs must be excluded from the gauge"
    );
}