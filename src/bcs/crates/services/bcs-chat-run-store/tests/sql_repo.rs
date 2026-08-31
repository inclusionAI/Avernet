use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_cache_api::{CacheError, CachePlugin, CacheResult, CacheSetMode, CacheTtl};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_chat_run_store::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, SqlChatRunRepo,
};
use bcs_db_api::{
    db_get_column, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbSqlFlavor, DbStatement,
    DbTransactionStep, DbTransactionStepResult, DbValue,
};
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
    // at the create-time version 1. The overlay value is the whole-record
    // shape (env-gated); this replica's env is "test".
    let stale = serde_json::to_vec(&serde_json::json!({
        "env": "test",
        "record": {
            "run_id": "a", "bot_uuid": "bot", "from_bot_id": "from", "session_key": "sk",
            "state": "running", "accumulated_content": "ABCDE", "error_message": null,
            "created_at_ms": 0u64, "updated_at_ms": 0u64, "completed_at_ms": null,
            "expires_at_ms": 9_000_000_000_000u64, "version": 5u64, "content_truncated": false,
            "client": "http-chat-async", "response_mode": "full"
        },
        "completion_policy": "WaitForFinal", "delivery_ack_at_ms": null,
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
// ---------------------------------------------------------------------------
// Read-side-follows-authority (overlay-first get) coverage
// ---------------------------------------------------------------------------

/// DbPlugin double that counts SELECT-like `query` calls so tests can pin the
/// "zero DB reads on the normal streaming path" invariant.
struct CountingDb {
    inner: Arc<LocalSqliteDbPlugin>,
    selects: AtomicUsize,
}

impl CountingDb {
    fn new() -> Self {
        Self {
            inner: Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db")),
            selects: AtomicUsize::new(0),
        }
    }

    fn selects(&self) -> usize {
        self.selects.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl DbPlugin for CountingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.selects.fetch_add(1, Ordering::SeqCst);
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

fn repo_counting(db: &Arc<CountingDb>) -> SqlChatRunRepo {
    let cache = Arc::new(InMemoryCachePlugin::new());
    SqlChatRunRepo::new(
        db.clone(),
        DbSqlFlavor::Sqlite,
        cache,
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    )
}

/// Cache double: writes succeed, deletes always fail — models a terminal CAS
/// whose overlay delete is rejected while the tombstone write still lands.
struct NoDeleteCache {
    inner: InMemoryCachePlugin,
}

#[async_trait]
impl CachePlugin for NoDeleteCache {
    async fn get_value(&self, key: &str) -> CacheResult<Option<Vec<u8>>> {
        self.inner.get_value(key).await
    }

    async fn set_value(
        &self,
        key: &str,
        value: Vec<u8>,
        ttl: Option<Duration>,
        mode: CacheSetMode,
    ) -> CacheResult<bool> {
        self.inner.set_value(key, value, ttl, mode).await
    }

    async fn delete(&self, _key: &str) -> CacheResult<bool> {
        Err(CacheError::Backend("injected overlay delete failure".to_string()))
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

#[tokio::test]
async fn overlay_roundtrips_every_record_field() {
    // The overlay value must round-trip the WHOLE record — including the two
    // fields ChatRunRecord's own serde skips (completion_policy /
    // delivery_ack_at_ms). Losing either would silently degrade the detach
    // classification in `record_run_event` on every cache hit.
    let repo = repo();
    let mut full = record("full", 1);
    full.state = ChatRunState::Running;
    full.accumulated_content = "text".to_string();
    full.error_message = Some("err".to_string());
    full.completed_at_ms = Some(0);
    full.content_truncated = true;
    full.response_mode = ChatResponseMode::AfterLastToolCall;
    full.completion_policy = ChatRunCompletionPolicy::DetachDeliveryAck;
    full.delivery_ack_at_ms = Some(0);
    repo.create(full.clone()).await.unwrap();
    // Create seeds the overlay, so this `get` is served cache-first.
    let stored = repo.get("full").await.unwrap().unwrap();
    assert_eq!(stored, full);
}

#[tokio::test]
async fn streaming_and_polling_read_path_skips_db_entirely() {
    // Read side follows the authority: while the run streams, the overlay is
    // the freshest fact, so appends AND long-poll reads answer from the cache
    // with zero MySQL SELECTs.
    let db = Arc::new(CountingDb::new());
    let repo = repo_counting(&db);
    repo.create(record("z", 1)).await.unwrap();
    assert_eq!(db.selects(), 0, "create must not SELECT");

    assert!(
        repo.append_streaming_content("z", 1, "he".to_string(), false)
            .await
            .unwrap()
    );
    assert!(
        repo.append_streaming_content("z", 2, "hell".to_string(), false)
            .await
            .unwrap()
    );
    assert!(
        repo.append_streaming_content("z", 3, "hello".to_string(), false)
            .await
            .unwrap()
    );

    for _ in 0..5 {
        let stored = repo.get("z").await.unwrap().unwrap();
        assert_eq!(stored.accumulated_content, "hello");
        assert_eq!(stored.version, 4);
        assert_eq!(stored.state, ChatRunState::Running);
    }
    assert_eq!(
        db.selects(),
        0,
        "appends + polls on the fast path must not SELECT (overlay-first get)"
    );
}

#[tokio::test]
async fn state_cas_midstream_preserves_streamed_overlay_content() {
    // Step-1 landmine: the state-CAS overlay refresh must compose the DB row's
    // authority fields with the overlay's streaming content. Taking the DB row
    // wholesale would clobber the text back to the create-time value whenever
    // a state CAS lands mid-stream (e.g. a detach ack on the first
    // content-bearing event).
    let repo = repo();
    repo.create(record("s", 1)).await.unwrap();
    assert!(
        repo.append_streaming_content("s", 1, "hello".to_string(), false)
            .await
            .unwrap()
    );

    let mut acked = record("s", 2);
    acked.state = ChatRunState::Running;
    acked.delivery_ack_at_ms = Some(0);
    match repo.compare_and_set_state("s", 2, acked).await.unwrap() {
        // The DB row is still at its create-time version 1 (streaming only
        // advanced the overlay to 2), so the CAS bumps it 1 -> 2.
        CasOutcome::Applied(r) => assert_eq!(r.version, 2),
        other => panic!("expected Applied, got {other:?}"),
    }

    // Cache-first read: version/state/ack from the DB row's CAS outcome,
    // content from the streamed overlay.
    let stored = repo.get("s").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "hello");
    assert_eq!(stored.version, 2);
    assert_eq!(stored.state, ChatRunState::Running);
    assert_eq!(stored.delivery_ack_at_ms, Some(0));
}

#[tokio::test]
async fn unparseable_overlay_value_degrades_to_db_read() {
    // The cache holds a single shape. Any value that is not the whole-record
    // overlay (a stale blob left over from a different writer, a corrupt
    // entry, or a foreign-env entry) must be treated as a miss and fall
    // straight to the authoritative DB row — never served and never merged.
    let db = Arc::new(CountingDb::new());
    let cache = Arc::new(InMemoryCachePlugin::new());
    let repo = SqlChatRunRepo::new(
        db.clone(),
        DbSqlFlavor::Sqlite,
        cache.clone(),
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    );
    repo.create(record("lg", 1)).await.unwrap();
    let selects_after_create = db.selects();

    // Replace the seed with a whole-record overlay carrying a FOREIGN env.
    // The key is env-less, so the same run_id may hold another environment's
    // value in a shared Redis; this replica must NOT serve it.
    let foreign = serde_json::to_vec(&serde_json::json!({
        "env": "other",
        "record": {
            "run_id": "lg", "bot_uuid": "bot", "from_bot_id": "from", "session_key": "sk",
            "state": "running", "accumulated_content": "LEAK", "error_message": null,
            "created_at_ms": 0u64, "updated_at_ms": 0u64, "completed_at_ms": null,
            "expires_at_ms": 9_000_000_000_000u64, "version": 9u64, "content_truncated": false,
            "client": "x", "response_mode": "full"
        },
        "completion_policy": "WaitForFinal", "delivery_ack_at_ms": null,
    }))
    .unwrap();
    cache
        .set_value("bcs:chat_run:lg", foreign, None, CacheSetMode::Upsert)
        .await
        .unwrap();

    // The foreign overlay is not served; the DB row defines the truth.
    let stored = repo.get("lg").await.unwrap().unwrap();
    assert_eq!(stored.accumulated_content, "", "foreign-env overlay must NOT leak");
    assert_eq!(stored.version, 1);
    assert_eq!(stored.state, ChatRunState::Pending);
    assert_eq!(
        db.selects(),
        selects_after_create + 1,
        "a non-serving overlay falls back to exactly one DB read"
    );
}

#[tokio::test]
async fn terminal_tombstone_covers_failed_overlay_delete() {
    // Terminal CAS tombstones the overlay (terminal record) before
    // deleting it. When the delete is rejected but the write landed, the
    // cache-first read serves the TERMINAL record — never a stale
    // pre-terminal running snapshot.
    let db = Arc::new(CountingDb::new());
    let cache = Arc::new(NoDeleteCache {
        inner: InMemoryCachePlugin::new(),
    });
    let repo = SqlChatRunRepo::new(
        db.clone(),
        DbSqlFlavor::Sqlite,
        cache,
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    );
    repo.create(record("t", 1)).await.unwrap();
    assert!(
        repo.append_streaming_content("t", 1, "hello".to_string(), false)
            .await
            .unwrap()
    );

    let mut completed = record("t", 2);
    completed.state = ChatRunState::Completed;
    completed.accumulated_content = "hello final".to_string();
    repo.compare_and_set_terminal("t", 2, completed)
        .await
        .unwrap();

    // The delete failed, so the tombstone V2 record is what a cache-first
    // read must serve.
    let stored = repo.get("t").await.unwrap().unwrap();
    assert_eq!(stored.state, ChatRunState::Completed);
    assert_eq!(stored.accumulated_content, "hello final");
    // The terminal CAS bumped the DB row (still at v1) to v2; the tombstone
    // carries the same version.
    assert_eq!(stored.version, 2);
}

// ---------------------------------------------------------------------------
// original_request audit column (write-once, outside the app read surface)
// ---------------------------------------------------------------------------

/// Read the `original_request` column straight from the DB. The repo's `get`
/// path never SELECTs this column (it is a write-once audit field), so the
/// only way to inspect it is a raw column query scoped to `env`.
async fn select_original_request(db: &dyn DbPlugin, run_id: &str, env: &str) -> String {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT original_request FROM bcs_chat_runs WHERE run_id = ? AND env = ?",
            vec![
                DbValue::from(run_id.to_string()),
                DbValue::from(env.to_string()),
            ],
        ))
        .await
        .expect("select original_request");
    db_get_column(&rows[0], "original_request").expect("original_request column")
}

#[tokio::test]
async fn original_request_persisted_but_not_read_back() {
    // `original_request` is a write-once-at-create audit column deliberately
    // kept OUT of the repo's read surface: every SELECT omits it and the
    // overlay serde skips it, so `get` returns the record with the field empty.
    // Inspecting it requires a direct query of that column (what an operator
    // does against the run table later).
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let cache = Arc::new(InMemoryCachePlugin::new());
    let repo = SqlChatRunRepo::new(
        db.clone(),
        DbSqlFlavor::Sqlite,
        cache,
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    );

    let payload = r#"{"method":"chat.send","params":{"message":{"content":"hi"}}}"#;
    let mut rec = record("audit", 1);
    rec.original_request = payload.to_string();
    repo.create(rec).await.unwrap();

    // The port deliberately does NOT surface the audit column.
    let stored = repo.get("audit").await.unwrap().unwrap();
    assert_eq!(stored.original_request, "");

    // Only a direct column query reads it back; the audit value landed verbatim.
    assert_eq!(
        select_original_request(db.as_ref(), "audit", "test").await,
        payload
    );
}

#[tokio::test]
async fn original_request_is_write_once_across_state_streaming_and_terminal() {
    // No UPDATE path (state CAS, DB-fail-over streaming append, terminal CAS)
    // touches `original_request`; the audit column keeps its create-time value
    // through every lifecycle mutation.
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    let dyn_cache: Arc<dyn CachePlugin> = Arc::new(FailingWritesCache::new());
    let repo = SqlChatRunRepo::new(
        db.clone(),
        DbSqlFlavor::Sqlite,
        dyn_cache,
        "bcs:".to_string(),
        120_000,
        "test".to_string(),
    );

    let payload = r#"{"method":"chat.send","params":{"message":"q"}}"#;
    let mut rec = record("w", 1);
    rec.original_request = payload.to_string();
    repo.create(rec).await.unwrap();

    // state CAS UPDATE
    let mut running = record("w", 1);
    running.state = ChatRunState::Running;
    repo.compare_and_set_state("w", 1, running)
        .await
        .unwrap();

    // streaming append via the DB fail-over UPDATE (overlay write rejected).
    assert!(
        repo.append_streaming_content("w", 2, "delta".to_string(), false)
            .await
            .unwrap()
    );

    // terminal CAS UPDATE
    let v = repo.get("w").await.unwrap().unwrap().version;
    let mut done = record("w", v);
    done.state = ChatRunState::Completed;
    done.accumulated_content = "delta".to_string();
    repo.compare_and_set_terminal("w", v, done)
        .await
        .unwrap();

    // Audit column untouched by any UPDATE.
    assert_eq!(
        select_original_request(db.as_ref(), "w", "test").await,
        payload
    );
}
