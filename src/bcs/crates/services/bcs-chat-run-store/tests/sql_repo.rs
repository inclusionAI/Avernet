use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_cache_api::{CacheError, CachePlugin, CacheResult, CacheSetMode, CacheTtl};
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
    SqlChatRunRepo::new(db, DbSqlFlavor::Sqlite, cache, "bcs:".to_string(), 120_000, "test".to_string())
}

/// Cache double whose `set_value` always rejects, simulating a Redis overlay
/// write outage. Reads/deletes delegate to an in-memory store so `read_overlay`
/// sees the nothing that was written — exactly the C4 fail-over scenario.
struct FailingWritesCache {
    inner: InMemoryCachePlugin,
}

impl FailingWritesCache {
    fn new() -> Self {
        Self {
            inner: InMemoryCachePlugin::new(),
        }
    }
}

#[async_trait]
impl CachePlugin for FailingWritesCache {
    async fn get_value(&self, key: &str) -> CacheResult<Option<Vec<u8>>> {
        self.inner.get_value(key).await
    }

    async fn set_value(
        &self,
        _key: &str,
        _value: Vec<u8>,
        _ttl: Option<Duration>,
        _mode: CacheSetMode,
    ) -> CacheResult<bool> {
        Err(CacheError::Backend(
            "injected overlay write failure".to_string(),
        ))
    }

    async fn delete(&self, key: &str) -> CacheResult<bool> {
        self.inner.delete(key).await
    }

    async fn expire(&self, key: &str, ttl: Duration) -> CacheResult<bool> {
        self.inner.expire(key, ttl).await
    }

    async fn ttl(&self, key: &str) -> CacheResult<CacheTtl> {
        self.inner.ttl(key).await
    }

    async fn hash_get(&self, key: &str, field: &str) -> CacheResult<Option<Vec<u8>>> {
        self.inner.hash_get(key, field).await
    }

    async fn hash_get_all(&self, key: &str) -> CacheResult<BTreeMap<String, Vec<u8>>> {
        self.inner.hash_get_all(key).await
    }

    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> CacheResult<()> {
        self.inner.hash_set(key, field, value).await
    }

    async fn hash_set_many(&self, key: &str, fields: BTreeMap<String, Vec<u8>>) -> CacheResult<()> {
        self.inner.hash_set_many(key, fields).await
    }

    async fn hash_delete(&self, key: &str, field: &str) -> CacheResult<bool> {
        self.inner.hash_delete(key, field).await
    }
}

fn repo_failing_cache() -> SqlChatRunRepo {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let cache: Arc<dyn CachePlugin> = Arc::new(FailingWritesCache::new());
    SqlChatRunRepo::new(
        db,
        DbSqlFlavor::Sqlite,
        cache,
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    )
}

/// Cache double for the P1 fail-over scenario. `set_value` and `delete` always
/// reject (Redis can neither accept the new overlay nor evict the stale one on
/// fail-over), while `get_value` reads an in-memory store that can be pre-seeded
/// with a *stale* overlay — modeling Redis still holding a prior successful
/// overlay that the rejected write cannot erase.
struct StaleOverlayFailingCache {
    inner: InMemoryCachePlugin,
}

impl StaleOverlayFailingCache {
    fn new() -> Self {
        Self {
            inner: InMemoryCachePlugin::new(),
        }
    }

    /// Seed a stale overlay at `key` (a prior successful write Redis still holds).
    /// `set_value`/`delete` remain rejected.
    async fn seed(&self, key: &str, bytes: Vec<u8>) {
        let _ = self
            .inner
            .set_value(key, bytes, None, CacheSetMode::Upsert)
            .await;
    }
}

#[async_trait]
impl CachePlugin for StaleOverlayFailingCache {
    async fn get_value(&self, key: &str) -> CacheResult<Option<Vec<u8>>> {
        self.inner.get_value(key).await
    }

    async fn set_value(
        &self,
        _key: &str,
        _value: Vec<u8>,
        _ttl: Option<Duration>,
        _mode: CacheSetMode,
    ) -> CacheResult<bool> {
        Err(CacheError::Backend(
            "injected overlay write failure".to_string(),
        ))
    }

    async fn delete(&self, _key: &str) -> CacheResult<bool> {
        Err(CacheError::Backend(
            "injected overlay delete failure".to_string(),
        ))
    }

    async fn expire(&self, key: &str, ttl: Duration) -> CacheResult<bool> {
        self.inner.expire(key, ttl).await
    }

    async fn ttl(&self, key: &str) -> CacheResult<CacheTtl> {
        self.inner.ttl(key).await
    }

    async fn hash_get(&self, key: &str, field: &str) -> CacheResult<Option<Vec<u8>>> {
        self.inner.hash_get(key, field).await
    }

    async fn hash_get_all(&self, key: &str) -> CacheResult<BTreeMap<String, Vec<u8>>> {
        self.inner.hash_get_all(key).await
    }

    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> CacheResult<()> {
        self.inner.hash_set(key, field, value).await
    }

    async fn hash_set_many(&self, key: &str, fields: BTreeMap<String, Vec<u8>>) -> CacheResult<()> {
        self.inner.hash_set_many(key, fields).await
    }

    async fn hash_delete(&self, key: &str, field: &str) -> CacheResult<bool> {
        self.inner.hash_delete(key, field).await
    }
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
async fn streaming_append_falls_back_to_db_when_overlay_write_fails() {
    // C4: when Redis rejects the overlay write, `append_streaming_content` must
    // fail the delta over to the authoritative DB row instead of silently
    // returning Ok(true) and dropping it. #1546 forbids a swallowed backend
    // write masquerading as success.
    let repo = repo_failing_cache();
    repo.create(record("a", 1)).await.unwrap();

    // Overlay write is rejected (injected Redis outage), yet the append still
    // reports Ok(true) and lands the content in the DB.
    assert!(
        repo.append_streaming_content("a", 1, "hello".to_string(), false)
            .await
            .unwrap()
    );
    // The overlay held nothing (its write was rejected), so the merged read now
    // falls back to the DB row — which must carry the delta and the bumped
    // version, proving the fail-over wrote through.
    let stored = repo.get("a").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "hello");
    assert_eq!(stored.version, 2);
    assert!(!stored.content_truncated);

    // A second delta keeps accumulating in the DB while the overlay keeps
    // rejecting — recovery picks up the DB advance as the base.
    assert!(
        repo.append_streaming_content("a", 2, "hello world".to_string(), true)
            .await
            .unwrap()
    );
    let stored = repo.get("a").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "hello world");
    assert_eq!(stored.version, 3);
    assert!(stored.content_truncated);
}

#[tokio::test]
async fn streaming_append_fallover_noop_when_run_already_terminal() {
    // C4 fail-over UPDATE is gated on the non-terminal state guard. If the run
    // became terminal concurrently the UPDATE affects 0 rows and the append
    // reports Ok(false) (delta not applied) instead of rewriting the audited
    // terminal row.
    let repo = repo_failing_cache();
    repo.create(record("t", 1)).await.unwrap();

    let mut failed = record("t", 1);
    failed.state = ChatRunState::Failed;
    failed.completed_at_ms = Some(0);
    failed.accumulated_content = "final".to_string();
    repo.compare_and_set_terminal("t", 1, failed)
        .await
        .unwrap();

    // The terminal CAS left the DB row at version 2; passing expected version 2
    // gets past the overlay/version check and reaches the fail-over UPDATE,
    // which the terminal guard rejects (0 rows affected).
    assert!(
        !repo
            .append_streaming_content("t", 2, "late".to_string(), false)
            .await
            .unwrap()
    );

    // The audited terminal content is untouched.
    let stored = repo.get("t").await.unwrap().unwrap();
    assert_eq!(stored.state, ChatRunState::Failed);
    assert_eq!(stored.accumulated_content, "final");
    assert_eq!(stored.version, 2);
}

#[tokio::test]
async fn streaming_append_failover_ignores_stale_overlay_and_rebases_on_db() {
    // P1: Redis still READS a stale overlay but rejects the new write, and the
    // best-effort delete also fails. The fail-over must advance the DB to the
    // *intended* version so the stale overlay (lower version) is never merged
    // over it; the next append then re-bases off the DB and resumes seamlessly.
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let cache = Arc::new(StaleOverlayFailingCache::new());
    let dyn_cache: Arc<dyn CachePlugin> = cache.clone();
    let repo = SqlChatRunRepo::new(
        db,
        DbSqlFlavor::Sqlite,
        dyn_cache,
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    );
    repo.create(record("a", 1)).await.unwrap();

    // Pretend five prior deltas streamed into the overlay while the DB stayed
    // at the create-time version 1.
    let stale = serde_json::to_vec(&serde_json::json!({
        "version": 5u64,
        "state": "running",
        "accumulated_content": "ABCDE",
        "content_truncated": false,
        "expires_at_ms": 9_000_000_000_000u64,
    }))
    .unwrap();
    cache.seed("bcs:chat_run:a", stale).await;

    // Engine read the stale overlay (v5, "ABCDE") and appended "F" → "ABCDEF",
    // expecting v5. The overlay write is rejected → DB fail-over.
    assert!(
        repo.append_streaming_content("a", 5, "ABCDEF".to_string(), false)
            .await
            .unwrap()
    );
    // The fail-over wrote "ABCDEF" to the DB at the intended version 6 (NOT
    // version+1 = v2), so the stale overlay v5 can no longer be merged over the
    // DB row.
    let stored = repo.get("a").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "ABCDEF");
    assert_eq!(stored.version, 6);

    // The stale overlay is still readable (delete failed) but v5 < 6, so the
    // next append bases off the DB (v6), not the stale overlay, and continues.
    assert!(
        repo.append_streaming_content("a", 6, "ABCDEF7".to_string(), false)
            .await
            .unwrap()
    );
    let stored = repo.get("a").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "ABCDEF7");
    assert_eq!(stored.version, 7);
}

#[tokio::test]
async fn list_active_overdue_and_delete_expired_terminal_noop() {
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

    // Mark overdue terminal. SQL repos do not prune — terminal-row deletion is
    // delegated to the platform scheduled task (spec §11.2); the code path is a
    // uniform no-op across DB flavors, so the auditable row stays in the DB.
    //
    // `delete_expired_terminal` returning the dropped records (so the engine can
    // attribute the Dropped lifecycle) is exercised by the memory impl and the
    // memory_repo.rs tests; the SQL impl returns empty and emits nothing here.
    let mut failed = active[0].clone();
    failed.state = ChatRunState::Failed;
    failed.completed_at_ms = Some(0);
    repo.compare_and_set_terminal("overdue", failed.version, failed)
        .await
        .unwrap();
    let dropped = repo.delete_expired_terminal(100, 50).await.unwrap();
    assert!(dropped.is_empty());
    assert!(repo.get("overdue").await.unwrap().is_some());
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

fn repo_mysql() -> SqlChatRunRepo {
    // Mysql flavor over the local in-memory SQLite db. The delete/retire code
    // paths are uniform no-ops across DB flavors (spec §11.2, delegated to the
    // platform), so this db is never actually queried — it just exercises the
    // Mysql-flavor constructor.
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let cache = Arc::new(InMemoryCachePlugin::new());
    SqlChatRunRepo::new(db, DbSqlFlavor::Mysql, cache, "bcs:".to_string(), 120_000, "test".to_string())
}

#[tokio::test]
async fn list_active_excludes_acked_detached_and_drop_is_noop() {
    let repo = repo();
    // Acknowledged detached-delivery run: delivered successfully, overdue, but
    // must NOT be failed on timeout — list_active skips it.
    let mut detached = record("detached", 1);
    detached.state = ChatRunState::Running;
    detached.completion_policy = ChatRunCompletionPolicy::DetachDeliveryAck;
    detached.delivery_ack_at_ms = Some(0);
    detached.expires_at_ms = 5;
    // A plain overdue run: should appear in list_active (eligible for force_fail).
    let mut overdue = record("overdue", 1);
    overdue.expires_at_ms = 5;
    repo.create(detached).await.unwrap();
    repo.create(overdue).await.unwrap();

    let active = repo.list_active(10).await.unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].run_id, "overdue");

    // SQL repos do not retire detached rows here — acknowledged detached runs
    // are pruned by the platform scheduled task (spec §11.2 detach branch). The
    // code path is a uniform no-op; the auditable row stays, and list_active
    // keeps excluding it so force_fail won't mark a delivered run as failed.
    let dropped = repo.drop_detached_expired(100, 50).await.unwrap();
    assert!(dropped.is_empty());
    assert!(repo.get("detached").await.unwrap().is_some());
    assert!(repo.get("overdue").await.unwrap().is_some());
}

#[tokio::test]
async fn deletes_are_noops_delegated_to_platform_across_flavors() {
    // Terminal-row and detached-row pruning are delegated to the platform
    // scheduled task (spec §11.2); the code paths no-op (return empty) without
    // touching the DB, uniformly across DB flavors. list_active exclusion is
    // covered above; here we confirm both delete ports no-op for both flavors.
    for repo in [repo(), repo_mysql()] {
        assert!(repo.delete_expired_terminal(100, 50).await.unwrap().is_empty());
        assert!(repo.drop_detached_expired(100, 50).await.unwrap().is_empty());
    }
}

#[tokio::test]
async fn env_scoping_isolates_runs_across_environments() {
    // Two repos over the SAME db/cache but different `env` must not see each
    // other's runs (shared-DB multi-env isolation).
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let cache = Arc::new(InMemoryCachePlugin::new());
    let env_a = SqlChatRunRepo::new(
        db.clone(),
        DbSqlFlavor::Sqlite,
        cache.clone(),
        "bcs:".to_string(),
        120_000,
        "a".to_string(),
    );
    let env_b = SqlChatRunRepo::new(
        db,
        DbSqlFlavor::Sqlite,
        cache,
        "bcs:".to_string(),
        120_000,
        "b".to_string(),
    );

    let mut overdue = record("overdue", 1);
    overdue.expires_at_ms = 5;
    env_a.create(overdue).await.unwrap();

    // get / list_active / metric_counts are env-scoped.
    assert!(env_a.get("overdue").await.unwrap().is_some());
    assert!(env_b.get("overdue").await.unwrap().is_none());
    let active_a = env_a.list_active(10).await.unwrap();
    let active_b = env_b.list_active(10).await.unwrap();
    assert_eq!(active_a.iter().filter(|r| r.run_id == "overdue").count(), 1);
    assert!(active_b.is_empty());
    let total = |counts: Vec<bcs_service_api::ChatRunMetricCount>| {
        counts.into_iter().map(|c| c.count).sum::<u64>()
    };
    assert!(total(env_b.metric_counts().await.unwrap()) == 0);
    assert!(total(env_a.metric_counts().await.unwrap()) >= 1);
}