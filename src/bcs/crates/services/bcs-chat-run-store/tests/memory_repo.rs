use bcs_chat_run_store::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, MemoryChatRunRepo,
};
use bcs_service_api::ChatResponseMode;

fn record(run_id: &str, version: u64) -> ChatRunRecord {
    let mut record = ChatRunRecord::new(
        run_id.to_string(),
        "bot".to_string(),
        "from".to_string(),
        "sk".to_string(),
        0,
        100_000,
        Some("http-chat-async".to_string()),
        ChatResponseMode::Full,
        ChatRunCompletionPolicy::WaitForFinal,
    );
    record.version = version;
    record
}

#[tokio::test]
async fn create_duplicate_is_rejected() {
    let repo = MemoryChatRunRepo::new();
    repo.create(record("r1", 1)).await.unwrap();
    match repo.create(record("r1", 1)).await {
        Err(ChatRunRepoError::DuplicateRunId(id)) => assert_eq!(id, "r1"),
        other => panic!("expected DuplicateRunId, got {other:?}"),
    }
}

#[tokio::test]
async fn capacity_is_enforced_as_capacity_error() {
    let repo = MemoryChatRunRepo::with_capacity(1);
    repo.create(record("a", 1)).await.unwrap();
    match repo.create(record("b", 1)).await {
        Err(ChatRunRepoError::Capacity { max_entries: 1 }) => {}
        other => panic!("expected Capacity, got {other:?}"),
    }
}

#[tokio::test]
async fn cas_state_applies_and_bumps_version() {
    let repo = MemoryChatRunRepo::new();
    repo.create(record("r", 1)).await.unwrap();
    let mut next = record("r", 1);
    next.state = ChatRunState::Running;
    match repo.compare_and_set_state("r", 1, next).await.unwrap() {
        CasOutcome::Applied(applied) => {
            assert_eq!(applied.version, 2);
            assert_eq!(applied.state, ChatRunState::Running);
        }
        other => panic!("expected Applied, got {other:?}"),
    }
    // Wrong expected version -> Conflict.
    match repo
        .compare_and_set_state("r", 1, record("r", 1))
        .await
        .unwrap()
    {
        CasOutcome::Conflict(_) => {}
        other => panic!("expected Conflict, got {other:?}"),
    }
}

#[tokio::test]
async fn terminal_is_immutable() {
    let repo = MemoryChatRunRepo::new();
    repo.create(record("t", 1)).await.unwrap();
    let mut terminal = record("t", 1);
    terminal.state = ChatRunState::Completed;
    terminal.completed_at_ms = Some(5);
    repo.compare_and_set_terminal("t", 1, terminal)
        .await
        .unwrap();
    match repo
        .compare_and_set_state("t", 2, record("t", 2))
        .await
        .unwrap()
    {
        CasOutcome::Terminal(_) => {}
        other => panic!("expected Terminal, got {other:?}"),
    }
    let stored = repo.get("t").await.unwrap().unwrap();
    assert_eq!(stored.state, ChatRunState::Completed);
    assert_eq!(stored.completed_at_ms, Some(5));
}

#[tokio::test]
async fn append_streaming_content_updates_and_bumps_version() {
    let repo = MemoryChatRunRepo::new();
    repo.create(record("s", 1)).await.unwrap();
    assert!(
        repo.append_streaming_content("s", 1, "hello".to_string(), false)
            .await
            .unwrap()
    );
    let stored = repo.get("s").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "hello");
    assert_eq!(stored.version, 2);
    // Stale expected version no longer writes.
    assert!(
        !repo
            .append_streaming_content("s", 1, "x".to_string(), false)
            .await
            .unwrap()
    );
    let stored = repo.get("s").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "hello");
}

#[tokio::test]
async fn list_active_returns_only_overdue_non_terminal() {
    let repo = MemoryChatRunRepo::new();
    let mut overdue = record("overdue", 1);
    overdue.expires_at_ms = 5;
    let mut future = record("future", 1);
    future.expires_at_ms = 1_000_000;
    let mut terminal = record("terminal", 1);
    terminal.state = ChatRunState::Completed;
    terminal.completed_at_ms = Some(1);
    repo.create(overdue).await.unwrap();
    repo.create(future).await.unwrap();
    repo.create(terminal).await.unwrap();
    let active = repo.list_active(10).await.unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].run_id, "overdue");
}

#[tokio::test]
async fn delete_expired_terminal_only_removes_past_retention() {
    let repo = MemoryChatRunRepo::new();
    let mut old = record("old", 1);
    old.state = ChatRunState::Failed;
    old.completed_at_ms = Some(0);
    let mut fresh = record("fresh", 1);
    fresh.state = ChatRunState::Completed;
    fresh.completed_at_ms = Some(90);
    repo.create(old).await.unwrap();
    repo.create(fresh).await.unwrap();
    let removed = repo.delete_expired_terminal(100, 50).await.unwrap();
    assert_eq!(removed.len(), 1);
    assert_eq!(removed[0].run_id, "old");
    assert!(repo.get("old").await.unwrap().is_none());
    assert!(repo.get("fresh").await.unwrap().is_some());
}

#[tokio::test]
async fn metric_counts_aggregates_by_state_and_client() {
    let repo = MemoryChatRunRepo::new();
    repo.create(record("a", 1)).await.unwrap();
    repo.create(record("b", 1)).await.unwrap();
    let mut running = record("c", 1);
    running.state = ChatRunState::Running;
    repo.create(running).await.unwrap();
    let counts = repo.metric_counts().await.unwrap();
    let pending_count = counts
        .iter()
        .find(|c| c.state == bcs_service_api::DirectChatRunState::Pending)
        .map(|c| c.count)
        .unwrap_or(0);
    assert_eq!(pending_count, 2);
}