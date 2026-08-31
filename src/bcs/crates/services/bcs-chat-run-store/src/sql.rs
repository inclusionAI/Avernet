//! SQL-backed `ChatRunRepoPort` with a Redis hot cache for streaming content.
//!
//! Authority split (see spec):
//! - MySQL/SQLite is authoritative for state, version, ownership, timestamps,
//!   terminal content, and is the auditable record.
//! - The Redis cache holds the streaming overlay as the WHOLE `ChatRunRecord`
//!   so per-token deltas never hit the DB — and neither do reads: while a run
//!   streams, the overlay IS the freshest fact (the DB row's content stays at
//!   its create/fail-over value), so `get` serves an overlay hit directly
//!   ("read side follows the authority") and only falls back to the DB row on
//!   a miss: terminal (terminal CAS deletes the overlay), cache loss, or a
//!   foreign-env value (the key is env-less). The cache holds a single
//!   format; an unparseable value is treated as a miss (no legacy shape).
//!
//! The port's `expected_version` is used by memory-mode CAS; the SQL impl gates
//! transitions on the non-terminal state guard (`state NOT IN (...)`) plus
//! `version = version + 1`, which is robust to the cache/DB version drift
//! inherent in streaming-only cache writes. Concurrent terminals resolve to
//! exactly one winner via the same state guard.
//!
//! Accepted degraded window (mirrors the spec's C6 dual-failure stance): if
//! Redis rejects BOTH the terminal tombstone write and the overlay delete
//! while reads still work, a pre-terminal overlay snapshot stays readable
//! until its TTL lapses; writers are unaffected (append bases are
//! version-fenced at the DB boundary).

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use bcs_cache_api::{CacheError, CachePlugin, CacheSetMode};
use bcs_db_api::{db_get_column, db_get_column_opt, DbPlugin, DbSqlFlavor, DbStatement, DbValue};
use bcs_service_api::port::repo::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState,
};
use bcs_service_api::{ChatResponseMode, ChatRunMetricCount, DirectChatClientKind};

const TERMINAL_STATES: &str = "'completed','failed','cancelled'";

/// SQL-backed direct chat run repository.
pub struct SqlChatRunRepo {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
    cache: Arc<dyn CachePlugin>,
    key_prefix: String,
    /// Retention grace past `expires_at_ms` that the streaming overlay must
    /// survive, so the timeout sweep (`force_fail`, which runs only after the
    /// deadline) can still merge the accumulated text. See spec §11 / C8.
    overlay_retention_ms: u64,
    /// BCS environment the rows belong to. Every query carries `AND env = ?`
    /// so a shared MySQL/Redis across environments cannot read, cancel, or
    /// sweep another environment's runs. Mirrors the convention in
    /// `bcs-session-store` / `bcs-bot-store`.
    env: String,
    schema_ready: AtomicBool,
    /// Runs whose overlay write was rejected (Redis can read but not write,
    /// and its delete may also fail). A suspect run's `get` bypasses the
    /// cache-first fast path and reads the authoritative DB row, so a stale
    /// pre-fail-over overlay left in the cache cannot be served over the
    /// advanced DB content. Cleared on the next successful overlay write
    /// (create seed / append / state CAS refresh), so steady-state reads
    /// stay on the zero-DB-read fast path.
    suspect: Arc<Mutex<HashSet<String>>>,
}

/// Streaming overlay value: the WHOLE `ChatRunRecord` as the in-flight
/// read-through source. While a run streams, per-delta writes only land here,
/// so the overlay is the freshest fact and `get` can serve it directly
/// without a DB read. Terminal CAS deletes the key, so terminal reads always
/// fall back to the audited DB row.
///
/// The cache KEY is env-less (`{prefix}chat_run:{run_id}`) while the DB rows
/// are env-scoped, so the value carries `env` and every fast-path read must
/// match it before serving — otherwise a shared Redis would leak one
/// environment's run into another's cache-first reads.
///
/// `completion_policy` / `delivery_ack_at_ms` are carried at the wrapper
/// level because `ChatRunRecord`'s own serde skips them (the port crate keeps
/// them out of its serialized shape); the cache must round-trip them or the
/// detach classification in `record_run_event` would silently degrade on
/// cache hits.
#[derive(Serialize, Deserialize, Clone)]
struct Overlay {
    env: String,
    record: ChatRunRecord,
    completion_policy: ChatRunCompletionPolicy,
    delivery_ack_at_ms: Option<u64>,
}

impl Overlay {
    fn from_record(record: &ChatRunRecord, env: &str) -> Self {
        Self {
            env: env.to_string(),
            completion_policy: record.completion_policy,
            delivery_ack_at_ms: record.delivery_ack_at_ms,
            record: record.clone(),
        }
    }

    fn into_record(mut self) -> ChatRunRecord {
        self.record.completion_policy = self.completion_policy;
        self.record.delivery_ack_at_ms = self.delivery_ack_at_ms;
        self.record
    }
}

impl SqlChatRunRepo {
    pub fn new(
        db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor,
        cache: Arc<dyn CachePlugin>,
        key_prefix: String,
        overlay_retention_ms: u64,
        env: String,
    ) -> Self {
        Self {
            db,
            flavor,
            cache,
            key_prefix,
            overlay_retention_ms,
            env,
            schema_ready: AtomicBool::new(false),
            suspect: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    fn cache_key(&self, run_id: &str) -> String {
        format!("{}chat_run:{}", self.key_prefix, run_id)
    }

    /// Whether `get` must bypass the cache-first fast path for this run.
    fn is_suspect(&self, run_id: &str) -> bool {
        self.suspect
            .lock()
            .map(|s| s.contains(run_id))
            .unwrap_or(false)
    }

    /// Mark a run cache-suspect after an overlay write was rejected: the read
    /// path must source from the DB until a later successful overlay write
    /// restores the cache as the freshest fact.
    fn mark_suspect(&self, run_id: &str) {
        if let Ok(mut s) = self.suspect.lock() {
            s.insert(run_id.to_string());
        }
    }

    /// Drop the suspect flag once the cache has accepted a fresh overlay
    /// write — the fast path is reliable again.
    fn clear_suspect(&self, run_id: &str) {
        if let Ok(mut s) = self.suspect.lock() {
            s.remove(run_id);
        }
    }

    async fn ensure_schema(&self) -> Result<(), ChatRunRepoError> {
        if self.schema_ready.load(Ordering::Relaxed) {
            return Ok(());
        }
        let create = "CREATE TABLE IF NOT EXISTS bcs_chat_runs (\
            id INTEGER PRIMARY KEY AUTOINCREMENT,\
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\
            env TEXT NOT NULL,\
            run_id TEXT NOT NULL,\
            bot_uuid TEXT NOT NULL,\
            from_bot_id TEXT NOT NULL,\
            session_key TEXT NOT NULL,\
            state TEXT NOT NULL,\
            accumulated_content TEXT,\
            error_message TEXT,\
            original_request TEXT,\
            completed_at_ms INTEGER,\
            expires_at_ms INTEGER NOT NULL,\
            version INTEGER NOT NULL,\
            content_truncated INTEGER NOT NULL DEFAULT 0,\
            client TEXT,\
            response_mode TEXT NOT NULL,\
            completion_policy TEXT NOT NULL,\
            delivery_ack_at_ms INTEGER,\
            CONSTRAINT uk_env_run_id UNIQUE (env, run_id))";
        self.db
            .execute(DbStatement::new(create))
            .await
            .map_err(backend)?;
        if self.flavor == DbSqlFlavor::Sqlite {
            for stmt in [
                "CREATE INDEX IF NOT EXISTS idx_env_expires ON bcs_chat_runs(env, state, expires_at_ms)",
                "CREATE INDEX IF NOT EXISTS idx_env_completed ON bcs_chat_runs(env, state, completed_at_ms)",
                "CREATE INDEX IF NOT EXISTS idx_env_from_bot ON bcs_chat_runs(env, from_bot_id)",
                "CREATE INDEX IF NOT EXISTS idx_env_bot ON bcs_chat_runs(env, bot_uuid)",
            ] {
                let _ = self.db.execute(DbStatement::new(stmt)).await;
            }
        }
        self.schema_ready.store(true, Ordering::Relaxed);
        Ok(())
    }

    async fn read_db(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {} FROM bcs_chat_runs WHERE run_id = ? AND env = ?",
                    select_columns(self.flavor)
                ),
                vec![DbValue::from(run_id), DbValue::from(self.env.as_str())],
            ))
            .await
            .map_err(backend)?;
        Ok(rows
            .into_iter()
            .next()
            .map(|row| row_to_record(&row))
            .transpose()?)
    }

    async fn read_overlay_bytes(&self, run_id: &str) -> Option<Vec<u8>> {
        match self.cache.get_value(&self.cache_key(run_id)).await {
            Ok(Some(bytes)) => Some(bytes),
            _ => None,
        }
    }

    /// Parse the cached overlay value into the whole record, served only when
    /// `env` matches: the cache key is env-less, and a shared Redis may hold
    /// another environment's value for the same run_id, so a foreign value
    /// must not be served (it falls through to the env-scoped DB). `None`
    /// when the key is missing, unreadable, fails to parse, or belongs to
    /// another env.
    async fn read_overlay_record(&self, run_id: &str) -> Option<ChatRunRecord> {
        let bytes = self.read_overlay_bytes(run_id).await?;
        let overlay = serde_json::from_slice::<Overlay>(&bytes).ok()?;
        if overlay.env != self.env {
            return None;
        }
        Some(overlay.into_record())
    }

    /// Write the whole-record streaming overlay. Returns `Ok(())` only
    /// when the store confirmed the write (`set_value` → `Ok(true)`); a
    /// rejection, or the no-write `Ok(false)` for an upsert, is surfaced as
    /// `Err` so the caller that relies on the overlay as the sole delta
    /// record (`append_streaming_content`) can fail over to the DB.
    /// Best-effort callers (`create`, state CAS) ignore the result since the
    /// DB row is authoritative there.
    async fn write_overlay_record(
        &self,
        run_id: &str,
        record: &ChatRunRecord,
    ) -> Result<(), CacheError> {
        let bytes = serde_json::to_vec(&Overlay::from_record(record, &self.env))
            .map_err(|err| CacheError::InvalidInput(err.to_string()))?;
        match self.set_overlay_bytes(run_id, bytes, record.expires_at_ms).await {
            Ok(()) => {
                self.clear_suspect(run_id);
                Ok(())
            }
            Err(err) => Err(err),
        }
    }

    /// Store the overlay value with its deadline-derived TTL. The overlay
    /// must survive the run deadline by the retention grace so the timeout
    /// sweep (which runs only once `expires_at_ms < now`) can still merge the
    /// streamed content instead of reading a stale DB row.
    async fn set_overlay_bytes(
        &self,
        run_id: &str,
        bytes: Vec<u8>,
        expires_at_ms: u64,
    ) -> Result<(), CacheError> {
        let now = now_ms();
        let ttl_ms = expires_at_ms
            .saturating_add(self.overlay_retention_ms)
            .saturating_sub(now)
            .max(1000);
        match self
            .cache
            .set_value(
                &self.cache_key(run_id),
                bytes,
                Some(Duration::from_millis(ttl_ms)),
                CacheSetMode::Upsert,
            )
            .await
        {
            Ok(true) => Ok(()),
            Ok(false) => Err(CacheError::Backend(
                "streaming overlay upsert reported no write".to_string(),
            )),
            Err(err) => Err(err),
        }
    }

    /// Fail a streaming delta over to the authoritative DB row when the Redis
    /// overlay write is unavailable (C4, #1546: never convert a backend write
    /// failure into a lost delta). Writes `accumulated_content` at the
    /// *intended* version (`version = ?`, not `version + 1`) so a stale overlay
    /// left at a lower version can never be merged over the freshly persisted
    /// row (P1): later reads/appends re-base off the DB. The non-terminal guard
    /// still resolves a concurrent terminal to 0 rows. applied → `Ok(true)`,
    /// already-terminal → `Ok(false)`, DB error → propagated `Backend`.
    async fn fall_back_to_db_append(
        &self,
        run_id: &str,
        accumulated: &str,
        truncated: bool,
        intended_version: u64,
    ) -> Result<bool, ChatRunRepoError> {
        let stmt = DbStatement::with_params(
            &format!(
                "UPDATE bcs_chat_runs SET accumulated_content = ?, content_truncated = ?, \
                 version = ?, {gmt_modified} \
                 WHERE run_id = ? AND state NOT IN ({TERMINAL_STATES}) AND env = ?",
                gmt_modified = self.flavor.set_modified_now()
            ),
            vec![
                DbValue::from(accumulated.to_string()),
                DbValue::from(truncated),
                DbValue::from(intended_version as i64),
                DbValue::from(run_id.to_string()),
                DbValue::from(self.env.as_str()),
            ],
        );
        let result = self.db.execute(stmt).await.map_err(backend)?;
        Ok(result.affected_rows > 0)
    }

    /// Resolve the base for a streaming append. Fast path: when the cache
    /// holds an overlay at exactly the caller's expected version it is the
    /// streaming source of truth and is used directly **without a DB read**
    /// (issue spec: per-token deltas never hit the DB on the normal path). An
    /// overlay newer than the caller's expectation means the caller is stale
    /// → no base (`Ok(None)` → append returns `Ok(false)`). A missing or
    /// behind-version overlay (Redis outage / stale-after-fail-over / a
    /// foreign-env value) falls back to the authoritative DB row — the
    /// recovery path that has to read MySQL anyway. Returns `Ok(None)` when
    /// the run is missing or its version does not match.
    async fn resolve_append_base(
        &self,
        run_id: &str,
        expected_version: u64,
    ) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
        match self.read_overlay_record(run_id).await {
            Some(record) if record.version == expected_version => return Ok(Some(record)),
            Some(record) if record.version > expected_version => return Ok(None),
            _ => {}
        }
        let Some(db) = self.read_db(run_id).await? else {
            return Ok(None);
        };
        if db.expires_at_ms == 0 {
            return Ok(None);
        }
        Ok(if db.version == expected_version { Some(db) } else { None })
    }

    async fn delete_overlay(&self, run_id: &str) {
        let _ = self.cache.delete(&self.cache_key(run_id)).await;
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn backend(err: impl std::fmt::Display) -> ChatRunRepoError {
    ChatRunRepoError::Backend(err.to_string())
}

/// Read a `*_ts` Unix-epoch-seconds column as milliseconds (0 if absent),
/// used to derive the record's `created_at_ms`/`updated_at_ms` from the
/// DB-managed `gmt_create`/`gmt_modified` convention columns.
fn row_seconds_to_millis(row: &bcs_db_api::DbRow, column: &'static str) -> u64 {
    match row.get(column) {
        Some(DbValue::I64(value)) if *value >= 0 => (*value as u64).saturating_mul(1000),
        Some(DbValue::U64(value)) => (*value).saturating_mul(1000),
        _ => 0,
    }
}

fn state_str(state: ChatRunState) -> &'static str {
    match state {
        ChatRunState::Pending => "pending",
        ChatRunState::Submitted => "submitted",
        ChatRunState::Running => "running",
        ChatRunState::Completed => "completed",
        ChatRunState::Failed => "failed",
        ChatRunState::Cancelled => "cancelled",
    }
}

fn parse_state(value: &str) -> Option<ChatRunState> {
    match value {
        "pending" => Some(ChatRunState::Pending),
        "submitted" => Some(ChatRunState::Submitted),
        "running" => Some(ChatRunState::Running),
        "completed" => Some(ChatRunState::Completed),
        "failed" => Some(ChatRunState::Failed),
        "cancelled" => Some(ChatRunState::Cancelled),
        _ => None,
    }
}

fn response_mode_str(mode: ChatResponseMode) -> &'static str {
    match mode {
        ChatResponseMode::Full => "full",
        ChatResponseMode::AfterLastToolCall => "after-last-tool-call",
    }
}

fn parse_response_mode(value: &str) -> ChatResponseMode {
    match value {
        "after-last-tool-call" => ChatResponseMode::AfterLastToolCall,
        _ => ChatResponseMode::Full,
    }
}

fn completion_policy_str(policy: ChatRunCompletionPolicy) -> &'static str {
    match policy {
        ChatRunCompletionPolicy::WaitForFinal => "wait_for_final",
        ChatRunCompletionPolicy::DetachDeliveryAck => "detach_delivery_ack",
    }
}

fn parse_completion_policy(value: &str) -> ChatRunCompletionPolicy {
    match value {
        "detach_delivery_ack" => ChatRunCompletionPolicy::DetachDeliveryAck,
        _ => ChatRunCompletionPolicy::WaitForFinal,
    }
}

fn client_kind(client: Option<&str>) -> DirectChatClientKind {
    match client.map(str::trim).filter(|s| !s.is_empty()) {
        None => DirectChatClientKind::None,
        Some("http-chat") => DirectChatClientKind::HttpChat,
        Some("http-chat-async") => DirectChatClientKind::HttpChatAsync,
        Some(raw) if raw.starts_with("bcs-cli") => DirectChatClientKind::BcsCli,
        Some(_) => DirectChatClientKind::Unknown,
    }
}

fn row_to_record(row: &bcs_db_api::DbRow) -> Result<ChatRunRecord, ChatRunRepoError> {
    let run_id: String = db_get_column(row, "run_id").map_err(backend)?;
    let bot_uuid: String = db_get_column(row, "bot_uuid").map_err(backend)?;
    let from_bot_id: String = db_get_column(row, "from_bot_id").map_err(backend)?;
    let session_key: String = db_get_column(row, "session_key").map_err(backend)?;
    let state: String = db_get_column(row, "state").map_err(backend)?;
    let accumulated_content: Option<String> =
        db_get_column_opt::<String>(row, "accumulated_content").map_err(backend)?;
    let error_message: Option<String> =
        db_get_column_opt::<String>(row, "error_message").map_err(backend)?;
    // created_at_ms/updated_at_ms are derived from the DB-managed
    // gmt_create/gmt_modified convention columns (projected as Unix-epoch
    // seconds under gmt_create_ts/gmt_modified_ts by `select_columns`).
    let created_at_ms = row_seconds_to_millis(row, "gmt_create_ts");
    let updated_at_ms = row_seconds_to_millis(row, "gmt_modified_ts");
    let completed_at_ms: Option<u64> =
        db_get_column_opt::<u64>(row, "completed_at_ms").map_err(backend)?;
    let expires_at_ms: u64 = db_get_column(row, "expires_at_ms").map_err(backend)?;
    let version: u64 = db_get_column(row, "version").map_err(backend)?;
    let content_truncated: bool = db_get_column(row, "content_truncated").map_err(backend)?;
    let client: Option<String> = db_get_column_opt::<String>(row, "client").map_err(backend)?;
    let response_mode: String = db_get_column(row, "response_mode").map_err(backend)?;
    let completion_policy: String = db_get_column(row, "completion_policy").map_err(backend)?;
    let delivery_ack_at_ms: Option<u64> =
        db_get_column_opt::<u64>(row, "delivery_ack_at_ms").map_err(backend)?;

    Ok(ChatRunRecord {
        run_id,
        bot_uuid,
        from_bot_id,
        session_key,
        state: parse_state(&state).unwrap_or(ChatRunState::Pending),
        accumulated_content: accumulated_content.unwrap_or_default(),
        error_message,
        // original_request is a write-once audit column never read back by the
        // port (absent from every SELECT), so it stays empty here.
        original_request: String::new(),
        created_at_ms,
        updated_at_ms,
        completed_at_ms,
        expires_at_ms,
        version,
        content_truncated,
        client,
        response_mode: parse_response_mode(&response_mode),
        completion_policy: parse_completion_policy(&completion_policy),
        delivery_ack_at_ms,
    })
}

/// Per-flavor SELECT column list for `bcs_chat_runs`. The DB-managed
/// `gmt_create`/`gmt_modified` convention columns are projected to Unix-epoch
/// seconds under `gmt_create_ts`/`gmt_modified_ts` so `row_to_record` can
/// derive the record's `created_at_ms`/`updated_at_ms` (millis). Other columns
/// are named verbatim; `original_request` is a write-only audit column and is
/// intentionally NOT read back.
fn select_columns(flavor: DbSqlFlavor) -> String {
    format!(
        "run_id, bot_uuid, from_bot_id, session_key, state, accumulated_content, \
         error_message, {gmt_create} AS gmt_create_ts, {gmt_modified} AS gmt_modified_ts, \
         completed_at_ms, expires_at_ms, version, content_truncated, client, response_mode, \
         completion_policy, delivery_ack_at_ms",
        gmt_create = flavor.unix_ts("gmt_create"),
        gmt_modified = flavor.unix_ts("gmt_modified"),
    )
}

/// Classify a CAS update that affected 0 rows by reading the current row.
async fn classify_cas_failure(
    flavor: DbSqlFlavor,
    db: &dyn DbPlugin,
    run_id: &str,
    env: &str,
) -> Result<CasOutcome, ChatRunRepoError> {
    let rows = db
        .query(DbStatement::with_params(
            &format!(
                "SELECT {} FROM bcs_chat_runs WHERE run_id = ? AND env = ?",
                select_columns(flavor)
            ),
            vec![DbValue::from(run_id.to_string()), DbValue::from(env.to_string())],
        ))
        .await
        .map_err(backend)?;
    match rows
        .into_iter()
        .next()
        .map(|r| row_to_record(&r))
        .transpose()?
    {
        None => Ok(CasOutcome::Conflict(None)),
        Some(record) => {
            if record.state.is_terminal() {
                Ok(CasOutcome::Terminal(Some(record)))
            } else {
                Ok(CasOutcome::Conflict(Some(record)))
            }
        }
    }
}

async fn read_full(
    flavor: DbSqlFlavor,
    db: &dyn DbPlugin,
    run_id: &str,
    env: &str,
) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
    let rows = db
        .query(DbStatement::with_params(
            &format!(
                "SELECT {} FROM bcs_chat_runs WHERE run_id = ? AND env = ?",
                select_columns(flavor)
            ),
            vec![DbValue::from(run_id.to_string()), DbValue::from(env.to_string())],
        ))
        .await
        .map_err(backend)?;
    Ok(rows
        .into_iter()
        .next()
        .map(|r| row_to_record(&r))
        .transpose()?)
}

#[async_trait]
impl ChatRunRepoPort for SqlChatRunRepo {
    async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunRepoError> {
        self.ensure_schema().await?;
        let stmt = DbStatement::with_params(
            "INSERT INTO bcs_chat_runs (env, run_id, bot_uuid, from_bot_id, session_key, state, \
             accumulated_content, error_message, original_request, completed_at_ms, expires_at_ms, \
             version, content_truncated, client, response_mode, completion_policy, \
             delivery_ack_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::from(self.env.clone()),
                DbValue::from(record.run_id.clone()),
                DbValue::from(record.bot_uuid.clone()),
                DbValue::from(record.from_bot_id.clone()),
                DbValue::from(record.session_key.clone()),
                DbValue::from(state_str(record.state)),
                DbValue::from(record.accumulated_content.clone()),
                record.error_message.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                DbValue::from(record.original_request.clone()),
                record.completed_at_ms.map(|v| DbValue::from(v as i64)).unwrap_or(DbValue::Null),
                DbValue::from(record.expires_at_ms as i64),
                DbValue::from(record.version as i64),
                DbValue::from(record.content_truncated),
                record.client.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                DbValue::from(response_mode_str(record.response_mode)),
                DbValue::from(completion_policy_str(record.completion_policy)),
                record.delivery_ack_at_ms.map(|v| DbValue::from(v as i64)).unwrap_or(DbValue::Null),
            ],
        );
        match self.db.execute(stmt).await {
            Ok(_) => {
                // Seed the whole-record overlay so `get` can serve this run
                // cache-first from the very first poll; a write blip is
                // benign (the read falls back to the just-inserted DB row).
                let _ = self.write_overlay_record(&record.run_id, &record).await;
                Ok(())
            }
            Err(err) if err.is_duplicate_key() => {
                Err(ChatRunRepoError::DuplicateRunId(record.run_id))
            }
            Err(err) => Err(backend(err)),
        }
    }

    async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
        // Read-through fast path ("read side follows the authority"): while a
        // run streams, the overlay is the freshest fact — per-delta writes
        // only land there — so an overlay hit answers the whole record with
        // ZERO DB reads. A miss falls back to the authoritative DB row:
        // terminal runs (terminal CAS deletes the overlay), cache loss, or a
        // foreign-env value. A cache-SUSPECT run (an overlay write was
        // rejected) bypasses the fast path: a stale pre-fail-over overlay
        // left in the cache must never be served over the advanced DB row.
        // Cleared on the next successful overlay write. `get` stays
        // side-effect-free: no re-seed on a miss; an active run's lost
        // overlay is re-seeded by the next delta write.
        if !self.is_suspect(run_id) {
            if let Some(record) = self.read_overlay_record(run_id).await {
                return Ok(Some(record));
            }
        }
        self.read_db(run_id).await
    }

    async fn compare_and_set_state(
        &self,
        run_id: &str,
        _expected_version: u64,
        new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError> {
        let stmt = DbStatement::with_params(
            &format!(
                "UPDATE bcs_chat_runs SET state = ?, {gmt_modified}, delivery_ack_at_ms = ?, \
                 version = version + 1 WHERE run_id = ? AND state NOT IN ({TERMINAL_STATES}) AND env = ?",
                gmt_modified = self.flavor.set_modified_now()
            ),
            vec![
                DbValue::from(state_str(new.state)),
                new.delivery_ack_at_ms.map(|v| DbValue::from(v as i64)).unwrap_or(DbValue::Null),
                DbValue::from(run_id.to_string()),
                DbValue::from(self.env.as_str()),
            ],
        );
        let result = self.db.execute(stmt).await.map_err(backend)?;
        if result.affected_rows > 0 {
            let updated = read_full(self.flavor, self.db.as_ref(), run_id, &self.env)
                .await?
                .unwrap_or(new.clone());
            // Refresh the read-cache overlay of the just-applied DB state; best
            // effort, since the DB row is authoritative on a read miss.
            // Compose, don't mirror: the DB row is authoritative for version,
            // state, ack, and timestamps, but its `accumulated_content` is the
            // create/fail-over-time value while the run streams — the streamed
            // text lives only in the overlay. Taking the DB row wholesale here
            // would clobber the content back to create-time whenever a state
            // CAS lands mid-stream (e.g. a detach acknowledgement on the
            // first content-bearing event).
            let mut refreshed = updated.clone();
            if let Some(base) = self.read_overlay_record(run_id).await {
                refreshed.accumulated_content = base.accumulated_content;
                refreshed.content_truncated = base.content_truncated;
            }
            let _ = self.write_overlay_record(run_id, &refreshed).await;
            Ok(CasOutcome::Applied(updated))
        } else {
            classify_cas_failure(self.flavor, self.db.as_ref(), run_id, &self.env).await
        }
    }

    async fn compare_and_set_terminal(
        &self,
        run_id: &str,
        _expected_version: u64,
        new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError> {
        let now = now_ms();
        let completed_at = new.completed_at_ms.unwrap_or(now);
        let stmt = DbStatement::with_params(
            &format!(
                "UPDATE bcs_chat_runs SET state = ?, accumulated_content = ?, error_message = ?, \
                 {gmt_modified}, completed_at_ms = ?, content_truncated = ?, \
                 version = version + 1 WHERE run_id = ? AND state NOT IN ({TERMINAL_STATES}) AND env = ?",
                gmt_modified = self.flavor.set_modified_now()
            ),
            vec![
                DbValue::from(state_str(new.state)),
                DbValue::from(new.accumulated_content.clone()),
                new.error_message.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                DbValue::from(completed_at as i64),
                DbValue::from(new.content_truncated),
                DbValue::from(run_id.to_string()),
                DbValue::from(self.env.as_str()),
            ],
        );
        let result = self.db.execute(stmt).await.map_err(backend)?;
        if result.affected_rows > 0 {
            let updated = read_full(self.flavor, self.db.as_ref(), run_id, &self.env)
                .await?
                .unwrap_or(new);
            // Tombstone before delete: best-effort write of the terminal record
            // first, so a failed delete cannot leave a readable pre-terminal
            // overlay snapshot served cache-first. The delete then reclaims the key so
            // terminal reads land on the audited DB row. If BOTH fail, the
            // stale overlay stays readable until its TTL (accepted degraded
            // window, see module docs); writers remain safe — append bases
            // are version-fenced at the DB boundary.
            let _ = self.write_overlay_record(run_id, &updated).await;
            self.delete_overlay(run_id).await;
            Ok(CasOutcome::Applied(updated))
        } else {
            classify_cas_failure(self.flavor, self.db.as_ref(), run_id, &self.env).await
        }
    }

    async fn append_streaming_content(
        &self,
        run_id: &str,
        expected_version: u64,
        accumulated: String,
        truncated: bool,
    ) -> Result<bool, ChatRunRepoError> {
        // Resolve the base. On the normal streaming path this returns straight
        // from the overlay without a DB read (see `resolve_append_base`); the DB
        // is only touched when the overlay is missing or stale (fail-over /
        // recovery), which is the path that has to read MySQL anyway.
        let Some(mut record) = self.resolve_append_base(run_id, expected_version).await? else {
            return Ok(false);
        };
        let intended_version = expected_version + 1;
        let now = now_ms();
        let flipped = record.state == ChatRunState::Pending;
        record.version = intended_version;
        if flipped {
            record.state = ChatRunState::Running;
        }
        record.accumulated_content = accumulated.clone();
        record.content_truncated = truncated;
        record.updated_at_ms = now;
        let cache_write: Result<(), CacheError> =
            self.write_overlay_record(run_id, &record).await;
        match cache_write {
            // Overlay (the sole delta record) confirmed the write.
            Ok(()) => Ok(true),
            // C4/P1: overlay write rejected — fail the delta over to the DB at the
            // *intended* version, then drop the stale overlay best-effort so
            // recovery re-bases off the DB. (#1546 forbids swallowing the write.)
            // Mark the run cache-suspect: if the delete also fails, a stale
            // pre-fail-over overlay must not be served cache-first.
            Err(_) => {
                self.mark_suspect(run_id);
                let applied = self
                    .fall_back_to_db_append(run_id, &accumulated, truncated, intended_version)
                    .await?;
                if applied {
                    self.delete_overlay(run_id).await;
                }
                Ok(applied)
            }
        }
    }

    async fn list_active(&self, now_ms: u64) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        // Exclude acknowledged detached-delivery runs: they are successfully
        // delivered and must not be failed on timeout (drop_detached_expired
        // retires them instead).
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {} FROM bcs_chat_runs \
                     WHERE state NOT IN ({TERMINAL_STATES}) AND expires_at_ms < ? \
                     AND env = ? \
                     AND NOT (completion_policy = 'detach_delivery_ack' \
                              AND delivery_ack_at_ms IS NOT NULL)",
                    select_columns(self.flavor)
                ),
                vec![DbValue::from(now_ms as i64), DbValue::from(self.env.as_str())],
            ))
            .await
            .map_err(backend)?;
        let mut records = Vec::new();
        for row in rows {
            records.push(row_to_record(&row)?);
        }
        Ok(records)
    }

    async fn drop_detached_expired(
        &self,
        _now_ms: u64,
        _retention_ms: u64,
    ) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        // Persistent (SQL) DBs do not retire rows from the 10s cleanup loop.
        // Acknowledged detached runs are pruned by the platform's scheduled task
        // via the spec §11.2 SQL — terminal rows, plus acknowledged detached
        // running rows past a *short* retention (not audit retention), so an
        // orphaned-but-delivered run does not sit on the active-run gauge for
        // 30/90 days. The memory impl performs the actual retirement and emits
        // the `Dropped` lifecycle; this impl keeps the auditable row. Uniform
        // across DB flavors — no per-flavor split.
        Ok(Vec::new())
    }

    async fn delete_expired_terminal(
        &self,
        _now_ms: u64,
        _retention_ms: u64,
    ) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        // Persistent (SQL) DBs delegate terminal-row pruning to the platform's
        // scheduled cleanup task (spec §11.2): keep the auditable rows, do not
        // hard-delete from the 10s cleanup loop. The memory impl still prunes and
        // emits the `Dropped`/`Expired` lifecycle. Uniform across DB flavors —
        // no per-flavor split.
        Ok(Vec::new())
    }

    async fn metric_counts(&self) -> Result<Vec<ChatRunMetricCount>, ChatRunRepoError> {
        // Only active (non-terminal) runs belong on the gauge; terminal totals
        // come from the lifecycle counter. This also keeps the GROUP BY off the
        // long-retention terminal rows in MySQL mode.
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT state, client, COUNT(*) AS c FROM bcs_chat_runs \
                     WHERE state NOT IN ({TERMINAL_STATES}) AND env = ? GROUP BY state, client"
                ),
                vec![DbValue::from(self.env.as_str())],
            ))
            .await
            .map_err(backend)?;
        let mut counts: Vec<ChatRunMetricCount> = Vec::new();
        for row in rows {
            let state_str: String = db_get_column(&row, "state").map_err(backend)?;
            let client: Option<String> = db_get_column_opt::<String>(&row, "client").map_err(backend)?;
            let count: u64 = db_get_column::<i64>(&row, "c").map_err(backend)? as u64;
            let state = match state_str.as_str() {
                "pending" => bcs_service_api::DirectChatRunState::Pending,
                "submitted" => bcs_service_api::DirectChatRunState::Submitted,
                "running" => bcs_service_api::DirectChatRunState::Running,
                "completed" => bcs_service_api::DirectChatRunState::Completed,
                "failed" => bcs_service_api::DirectChatRunState::Failed,
                _ => bcs_service_api::DirectChatRunState::Cancelled,
            };
            let client_kind = client_kind(client.as_deref());
            if let Some(existing) = counts
                .iter_mut()
                .find(|c| c.state == state && c.client_kind == client_kind)
            {
                existing.count = existing.count.saturating_add(count);
            } else {
                counts.push(ChatRunMetricCount {
                    state,
                    client_kind,
                    count,
                });
            }
        }
        Ok(counts)
    }
}