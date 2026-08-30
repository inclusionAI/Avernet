//! SQL-backed `ChatRunRepoPort` with a Redis hot cache for streaming content.
//!
//! Authority split (see spec):
//! - MySQL/SQLite is authoritative for state, version, ownership, timestamps,
//!   terminal content, and is the auditable record.
//! - The Redis cache holds only the streaming overlay (`{version, state,
//!   accumulated_content, content_truncated}`) so per-token deltas never hit
//!   the DB. Reads merge DB (authoritative structure) with the cache overlay
//!   when the cache version is ahead (i.e. streaming advanced it past the DB).
//!
//! The port's `expected_version` is used by memory-mode CAS; the SQL impl gates
//! transitions on the non-terminal state guard (`state NOT IN (...)`) plus
//! `version = version + 1`, which is robust to the cache/DB version drift
//! inherent in streaming-only cache writes. Concurrent terminals resolve to
//! exactly one winner via the same state guard.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
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
}

#[derive(Serialize, Deserialize, Clone)]
struct StreamingOverlay {
    version: u64,
    state: String,
    accumulated_content: String,
    content_truncated: bool,
    /// Run deadline carried in the overlay so the normal streaming path can
    /// compute its TTL (and detect a missing run) without a per-delta DB read.
    expires_at_ms: u64,
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
        }
    }

    fn cache_key(&self, run_id: &str) -> String {
        format!("{}chat_run:{}", self.key_prefix, run_id)
    }

    async fn ensure_schema(&self) -> Result<(), ChatRunRepoError> {
        if self.schema_ready.load(Ordering::Relaxed) {
            return Ok(());
        }
        let create = "CREATE TABLE IF NOT EXISTS bcs_chat_runs (\
            env TEXT NOT NULL,\
            run_id TEXT NOT NULL,\
            bot_uuid TEXT NOT NULL,\
            from_bot_id TEXT NOT NULL,\
            session_key TEXT NOT NULL,\
            state TEXT NOT NULL,\
            accumulated_content TEXT,\
            error_message TEXT,\
            created_at_ms INTEGER NOT NULL,\
            updated_at_ms INTEGER NOT NULL,\
            completed_at_ms INTEGER,\
            expires_at_ms INTEGER NOT NULL,\
            version INTEGER NOT NULL,\
            content_truncated INTEGER NOT NULL DEFAULT 0,\
            client TEXT,\
            response_mode TEXT NOT NULL,\
            completion_policy TEXT NOT NULL,\
            delivery_ack_at_ms INTEGER,\
            PRIMARY KEY (env, run_id))";
        self.db
            .execute(DbStatement::new(create))
            .await
            .map_err(backend)?;
        if self.flavor == DbSqlFlavor::Sqlite {
            for stmt in [
                "CREATE INDEX IF NOT EXISTS idx_chat_runs_env_expires ON bcs_chat_runs(env, state, expires_at_ms)",
                "CREATE INDEX IF NOT EXISTS idx_chat_runs_env_completed ON bcs_chat_runs(env, state, completed_at_ms)",
                "CREATE INDEX IF NOT EXISTS idx_chat_runs_env_from_bot ON bcs_chat_runs(env, from_bot_id)",
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
                "SELECT run_id, bot_uuid, from_bot_id, session_key, state, accumulated_content, \
                 error_message, created_at_ms, updated_at_ms, completed_at_ms, expires_at_ms, \
                 version, content_truncated, client, response_mode, completion_policy, \
                 delivery_ack_at_ms FROM bcs_chat_runs WHERE run_id = ? AND env = ?",
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

    async fn read_overlay(&self, run_id: &str) -> Option<StreamingOverlay> {
        match self.cache.get_value(&self.cache_key(run_id)).await {
            Ok(Some(bytes)) => serde_json::from_slice(&bytes).ok(),
            _ => None,
        }
    }

    /// Write the streaming overlay. Returns `Ok(())` only when the store
    /// confirmed the write (`set_value` → `Ok(true)`). A rejection, or the
    /// no-write `Ok(false)` for an upsert (which means the delta was NOT
    /// stored), is surfaced as `Err` so the caller that relies on the overlay
    /// as the sole delta record (`append_streaming_content`) can fail over to
    /// the DB. Best-effort callers (`create`, state CAS) ignore the result
    /// since the DB row is authoritative there.
    async fn write_overlay(&self, run_id: &str, overlay: &StreamingOverlay) -> Result<(), CacheError> {
        let now = now_ms();
        // Overlay must survive the run deadline by the retention grace so the
        // timeout sweep (which runs only once `expires_at_ms < now`) can still
        // merge the streamed content instead of reading a stale DB row.
        let ttl_ms = overlay
            .expires_at_ms
            .saturating_add(self.overlay_retention_ms)
            .saturating_sub(now)
            .max(1000);
        let bytes = serde_json::to_vec(overlay)
            .map_err(|err| CacheError::InvalidInput(err.to_string()))?;
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
        let now = now_ms();
        let stmt = DbStatement::with_params(
            &format!(
                "UPDATE bcs_chat_runs SET accumulated_content = ?, content_truncated = ?, \
                 version = ?, updated_at_ms = ? \
                 WHERE run_id = ? AND state NOT IN ({TERMINAL_STATES}) AND env = ?"
            ),
            vec![
                DbValue::from(accumulated.to_string()),
                DbValue::from(truncated),
                DbValue::from(intended_version as i64),
                DbValue::from(now as i64),
                DbValue::from(run_id.to_string()),
                DbValue::from(self.env.as_str()),
            ],
        );
        let result = self.db.execute(stmt).await.map_err(backend)?;
        Ok(result.affected_rows > 0)
    }

    /// Resolve the base for a streaming append. Fast path: when the overlay is
    /// present at exactly the caller's expected version it is the streaming
    /// source of truth and is used directly **without a DB read** (issue spec:
    /// per-token deltas never hit the DB on the normal path). An overlay newer
    /// than the caller's expectation means the caller is stale → no base
    /// (`Ok(None)` → append returns `Ok(false)`). A missing or behind-version
    /// overlay (Redis outage / stale-after-fail-over) falls back to the
    /// authoritative DB row — the recovery path that has to read MySQL anyway.
    /// Returns `Ok(None)` when the run is missing or its version does not match.
    async fn resolve_append_base(
        &self,
        run_id: &str,
        expected_version: u64,
    ) -> Result<Option<StreamingOverlay>, ChatRunRepoError> {
        match self.read_overlay(run_id).await {
            Some(existing) if existing.version == expected_version => return Ok(Some(existing)),
            Some(existing) if existing.version > expected_version => return Ok(None),
            _ => {}
        }
        let Some(db) = self.read_db(run_id).await? else {
            return Ok(None);
        };
        if db.expires_at_ms == 0 {
            return Ok(None);
        }
        let overlay = StreamingOverlay {
            version: db.version,
            state: state_str(db.state).to_string(),
            accumulated_content: db.accumulated_content.clone(),
            content_truncated: db.content_truncated,
            expires_at_ms: db.expires_at_ms,
        };
        Ok(if overlay.version == expected_version {
            Some(overlay)
        } else {
            None
        })
    }

    async fn delete_overlay(&self, run_id: &str) {
        let _ = self.cache.delete(&self.cache_key(run_id)).await;
    }

    /// Merge the authoritative DB record with the streaming overlay when the
    /// overlay version is ahead (streaming advanced it past the DB) and the run
    /// is still non-terminal.
    fn merge(record: ChatRunRecord, overlay: Option<StreamingOverlay>) -> ChatRunRecord {
        let Some(overlay) = overlay else {
            return record;
        };
        if record.state.is_terminal() || overlay.version <= record.version {
            return record;
        }
        let mut merged = record;
        merged.version = overlay.version;
        merged.state = parse_state(&overlay.state).unwrap_or(merged.state);
        merged.accumulated_content = overlay.accumulated_content;
        merged.content_truncated = overlay.content_truncated;
        merged
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
    let created_at_ms: u64 = db_get_column(row, "created_at_ms").map_err(backend)?;
    let updated_at_ms: u64 = db_get_column(row, "updated_at_ms").map_err(backend)?;
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

/// Classify a CAS update that affected 0 rows by reading the current row.
async fn classify_cas_failure(
    db: &dyn DbPlugin,
    run_id: &str,
    env: &str,
) -> Result<CasOutcome, ChatRunRepoError> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT run_id, bot_uuid, from_bot_id, session_key, state, accumulated_content, \
             error_message, created_at_ms, updated_at_ms, completed_at_ms, expires_at_ms, \
             version, content_truncated, client, response_mode, completion_policy, \
             delivery_ack_at_ms FROM bcs_chat_runs WHERE run_id = ? AND env = ?",
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

const SELECT_COLS: &str = "run_id, bot_uuid, from_bot_id, session_key, state, accumulated_content, \
     error_message, created_at_ms, updated_at_ms, completed_at_ms, expires_at_ms, \
     version, content_truncated, client, response_mode, completion_policy, delivery_ack_at_ms";

async fn read_full(db: &dyn DbPlugin, run_id: &str, env: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
    let rows = db
        .query(DbStatement::with_params(
            &format!("SELECT {SELECT_COLS} FROM bcs_chat_runs WHERE run_id = ? AND env = ?"),
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
             accumulated_content, error_message, created_at_ms, updated_at_ms, completed_at_ms, \
             expires_at_ms, version, content_truncated, client, response_mode, completion_policy, \
             delivery_ack_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::from(self.env.clone()),
                DbValue::from(record.run_id.clone()),
                DbValue::from(record.bot_uuid.clone()),
                DbValue::from(record.from_bot_id.clone()),
                DbValue::from(record.session_key.clone()),
                DbValue::from(state_str(record.state)),
                DbValue::from(record.accumulated_content.clone()),
                record.error_message.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                DbValue::from(record.created_at_ms as i64),
                DbValue::from(record.updated_at_ms as i64),
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
                // Overlay here is only a read cache of the just-inserted DB row;
                // a write blip is benign (a later read falls back to the DB).
                let _ = self
                    .write_overlay(
                        &record.run_id,
                        &StreamingOverlay {
                            version: record.version,
                            state: state_str(record.state).to_string(),
                            accumulated_content: record.accumulated_content.clone(),
                            content_truncated: record.content_truncated,
                            expires_at_ms: record.expires_at_ms,
                        },
                    )
                    .await;
                Ok(())
            }
            Err(err) if err.is_duplicate_key() => {
                Err(ChatRunRepoError::DuplicateRunId(record.run_id))
            }
            Err(err) => Err(backend(err)),
        }
    }

    async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
        let Some(record) = self.read_db(run_id).await? else {
            // No DB row; not even created here.
            return Ok(None);
        };
        let overlay = self.read_overlay(run_id).await;
        Ok(Some(Self::merge(record, overlay)))
    }

    async fn compare_and_set_state(
        &self,
        run_id: &str,
        _expected_version: u64,
        new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError> {
        let now = now_ms();
        let stmt = DbStatement::with_params(
            &format!(
                "UPDATE bcs_chat_runs SET state = ?, updated_at_ms = ?, delivery_ack_at_ms = ?, \
                 version = version + 1 WHERE run_id = ? AND state NOT IN ({TERMINAL_STATES}) AND env = ?"
            ),
            vec![
                DbValue::from(state_str(new.state)),
                DbValue::from(now as i64),
                new.delivery_ack_at_ms.map(|v| DbValue::from(v as i64)).unwrap_or(DbValue::Null),
                DbValue::from(run_id.to_string()),
                DbValue::from(self.env.as_str()),
            ],
        );
        let result = self.db.execute(stmt).await.map_err(backend)?;
        if result.affected_rows > 0 {
            let updated = read_full(self.db.as_ref(), run_id, &self.env).await?.unwrap_or(new.clone());
            // Refresh the read-cache overlay of the just-applied DB state; best
            // effort, since the DB row is authoritative on a read miss.
            let _ = self
                .write_overlay(
                    run_id,
                    &StreamingOverlay {
                        version: updated.version,
                        state: state_str(updated.state).to_string(),
                        accumulated_content: updated.accumulated_content.clone(),
                        content_truncated: updated.content_truncated,
                        expires_at_ms: updated.expires_at_ms,
                    },
                )
                .await;
            Ok(CasOutcome::Applied(updated))
        } else {
            classify_cas_failure(self.db.as_ref(), run_id, &self.env).await
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
                 updated_at_ms = ?, completed_at_ms = ?, content_truncated = ?, \
                 version = version + 1 WHERE run_id = ? AND state NOT IN ({TERMINAL_STATES}) AND env = ?"
            ),
            vec![
                DbValue::from(state_str(new.state)),
                DbValue::from(new.accumulated_content.clone()),
                new.error_message.clone().map(DbValue::from).unwrap_or(DbValue::Null),
                DbValue::from(now as i64),
                DbValue::from(completed_at as i64),
                DbValue::from(new.content_truncated),
                DbValue::from(run_id.to_string()),
                DbValue::from(self.env.as_str()),
            ],
        );
        let result = self.db.execute(stmt).await.map_err(backend)?;
        if result.affected_rows > 0 {
            self.delete_overlay(run_id).await;
            let updated = read_full(self.db.as_ref(), run_id, &self.env).await?.unwrap_or(new);
            Ok(CasOutcome::Applied(updated))
        } else {
            classify_cas_failure(self.db.as_ref(), run_id, &self.env).await
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
        let Some(mut overlay) = self.resolve_append_base(run_id, expected_version).await? else {
            return Ok(false);
        };
        let flipped = overlay.state == "pending";
        overlay.version += 1;
        if flipped {
            overlay.state = "running".to_string();
        }
        overlay.accumulated_content = accumulated;
        overlay.content_truncated = truncated;
        let intended_version = overlay.version;
        match self.write_overlay(run_id, &overlay).await {
            // Overlay (the sole delta record) confirmed the write.
            Ok(()) => Ok(true),
            // C4/P1: overlay write rejected — fail the delta over to the DB at the
            // *intended* version, then drop the stale overlay best-effort so
            // recovery re-bases off the DB. (#1546 forbids swallowing the write.)
            Err(_) => {
                let applied = self
                    .fall_back_to_db_append(
                        run_id,
                        &overlay.accumulated_content,
                        truncated,
                        intended_version,
                    )
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
                    "SELECT {SELECT_COLS} FROM bcs_chat_runs \
                     WHERE state NOT IN ({TERMINAL_STATES}) AND expires_at_ms < ? \
                     AND env = ? \
                     AND NOT (completion_policy = 'detach_delivery_ack' \
                              AND delivery_ack_at_ms IS NOT NULL)"
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
        now_ms: u64,
        retention_ms: u64,
    ) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        // MySQL delegates (terminal-row pruning is platform-managed; detached
        // retirement is analogous — keep the auditable row). SQLite (dev/test)
        // self-prunes here so the local table stays bounded and the path is
        // covered by tests.
        if self.flavor != DbSqlFlavor::Sqlite {
            return Ok(Vec::new());
        }
        let cutoff = now_ms.saturating_sub(retention_ms);
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {SELECT_COLS} FROM bcs_chat_runs \
                     WHERE state = 'running' \
                       AND completion_policy = 'detach_delivery_ack' \
                       AND delivery_ack_at_ms IS NOT NULL \
                       AND delivery_ack_at_ms < ? \
                       AND env = ?"
                ),
                vec![DbValue::from(cutoff as i64), DbValue::from(self.env.as_str())],
            ))
            .await
            .map_err(backend)?;
        let mut dropped = Vec::new();
        for row in rows {
            let record = row_to_record(&row)?;
            let _ = self
                .db
                .execute(DbStatement::with_params(
                    &format!(
                        "DELETE FROM bcs_chat_runs \
                         WHERE run_id = ? AND state = 'running' \
                           AND completion_policy = 'detach_delivery_ack' \
                           AND delivery_ack_at_ms < ? \
                           AND env = ?"
                    ),
                    vec![DbValue::from(record.run_id.clone()), DbValue::from(cutoff as i64), DbValue::from(self.env.as_str())],
                ))
                .await;
            self.delete_overlay(&record.run_id).await;
            dropped.push(record);
        }
        Ok(dropped)
    }

    async fn delete_expired_terminal(
        &self,
        now_ms: u64,
        retention_ms: u64,
    ) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        // Production MySQL delegates terminal-row pruning to the platform's
        // scheduled cleanup task (spec §11): keep the auditable rows, do not
        // hard-delete from the 10s loop. SQLite (dev/test, no platform) still
        // self-prunes here so the local table stays bounded and the delete path
        // stays covered by tests.
        if self.flavor != DbSqlFlavor::Sqlite {
            return Ok(Vec::new());
        }
        let cutoff = now_ms.saturating_sub(retention_ms);
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {SELECT_COLS} FROM bcs_chat_runs \
                     WHERE state IN ({TERMINAL_STATES}) AND completed_at_ms < ? AND env = ?"
                ),
                vec![DbValue::from(cutoff as i64), DbValue::from(self.env.as_str())],
            ))
            .await
            .map_err(backend)?;
        let mut dropped = Vec::new();
        for row in rows {
            let record = row_to_record(&row)?;
            // Delete one-by-one reusing the same cutoff guard to stay portable
            // across SQLite/MySQL (no parameterized IN-list with variable arity).
            let _ = self
                .db
                .execute(DbStatement::with_params(
                    &format!(
                        "DELETE FROM bcs_chat_runs \
                         WHERE run_id = ? AND state IN ({TERMINAL_STATES}) AND completed_at_ms < ? \
                         AND env = ?"
                    ),
                    vec![DbValue::from(record.run_id.clone()), DbValue::from(cutoff as i64), DbValue::from(self.env.as_str())],
                ))
                .await;
            self.delete_overlay(&record.run_id).await;
            dropped.push(record);
        }
        Ok(dropped)
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