//! Persistent (DB plugin-backed) bot repository: `PersistentBotRepo`.
//!
//! Moved verbatim from `lib.rs` during the crate split; see the
//! crate docs for the two-layer (process memory + DB) architecture.

use std::collections::HashMap;
use std::sync::Arc;
#[cfg(test)]
use std::time::{Duration, Instant};

use async_trait::async_trait;
use tokio::sync::{RwLock, oneshot};
use tracing::{info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{
    DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue as Value, db_get_column, db_get_column_opt,
};
#[cfg(test)]
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{
    BotCapabilities,
    BotMetricCount,
    BotMetricsSnapshotPort, RegisteredBot, ServiceError, ServiceResult,
};
#[cfg(test)]
use bcs_service_api::Skill;
#[cfg(test)]
use super::BOT_EXPIRY;

/// SQL parameter chunk bound for batched registry loads.
const REGISTRY_LOAD_CHUNK: usize = 100;

use super::{BotInfo, RegisteredBotInner, bot_info_json_set};

mod agent_registration;
mod bot_repo_port;
mod caller_config;
mod control_plane;
mod registration_create;
mod agent_bots;
mod registration_ownership;
mod retirement;

#[cfg(test)]
#[path = "../../tests/unit/heartbeat.rs"]
mod heartbeat_tests;

/// Mine-union candidate read (bot-authority plan
/// Task 4.5, spec §8.0 point 1): the SQL companions
/// of the `list_controllable` implementation in
/// `control_plane.rs`.
#[path = "../controllable.rs"]
mod controllable;

/// Persistent bot repository backed by a DB plugin.
///
/// Process memory holds WebSocket connections and heartbeat timestamps;
/// the database holds persistent capabilities and tokens. Heartbeat payloads
/// are neither retained nor written to an external cache.
pub struct PersistentBotRepo {
    // Layer 1: Process Memory
    /// Bot connections and state. Visible crate-wide: the
    /// heartbeat unit tests drive live/expiry boundaries through
    /// this field directly (same-shaped discipline as the
    /// persistent-repo siblings' internal access).
    pub(crate) bots: RwLock<HashMap<String, RegisteredBotInner>>,
    /// Token to bot_uuid mapping (hot cache for auth).
    token_to_bot: RwLock<HashMap<String, String>>,
    /// Channel binding index: (channel, binding_key) -> bot_uuid.
    binding_channel_index: Arc<RwLock<HashMap<(String, String), String>>>,
    /// Process-local runtime info, including the active negotiated client kind
    /// and server-owned coordination profile.
    bot_info_overrides: RwLock<HashMap<(String, String), String>>,

    // Layer 2: Database
    /// DB plugin for persistent storage.
    db: Arc<dyn DbPlugin>,

    /// SQL dialect flavor. Visible crate-wide for the
    /// AgentPass sibling reader (`agent_bots.rs`): the
    /// flavor reads are pure dialect chooses, never writes.
    pub(crate) flavor: DbSqlFlavor,

    /// Read-through cache for per-Bot caller configuration snapshots
    /// (§15.96): the sync write path invalidates the key on success, so
    /// same-process PATCH→read stays immediate; cross-process staleness is
    /// bounded by the per-class TTLs.
    pub(crate) caller_config_cache: crate::caller_config_cache::CallerConfigCache,

    /// Pending request-response channels for send_request.
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
}

impl PersistentBotRepo {
    /// Create a DB-backed repository using the MySQL dialect.
    pub fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self::with_sql_flavor(db, DbSqlFlavor::Mysql)
    }

    /// Create a DB-backed repository with the selected SQL flavor.
    pub fn with_sql_flavor(
        db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor,
    ) -> Self {
        Self {
            bots: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            binding_channel_index: Arc::new(RwLock::new(HashMap::new())),
            bot_info_overrides: RwLock::new(HashMap::new()),
            db,
            flavor,
            caller_config_cache: crate::caller_config_cache::CallerConfigCache::new(
                crate::caller_config_cache::POSITIVE_TTL,
                crate::caller_config_cache::NEGATIVE_TTL,
            ),
            pending_requests: RwLock::new(HashMap::new()),
        }
    }

    async fn db_query(&self, sql: &str, params: Vec<Value>) -> bcs_db_api::DbResult<Vec<DbRow>> {
        self.db.query(DbStatement::with_params(sql, params)).await
    }

    async fn db_execute_affected(
        &self,
        sql: &str,
        params: Vec<Value>,
    ) -> bcs_db_api::DbResult<u64> {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .map(|result| result.affected_rows)
    }

    /// Save bot capabilities to the configured database.
    /// First checks if the record exists, then uses UPDATE if it exists, otherwise INSERT.
    async fn save_to_db(
        &self,
        bot_uuid: &str,
        caps: &BotCapabilities,
        session_token: Option<&str>,
        created_by_override: Option<&str>,
    ) -> ServiceResult<()> {
        let env = resolve_env();

        let (hidden, existing_created_by) = {
            let bots = self.bots.read().await;
            bots.get(bot_uuid)
                .map(|b| (b.hidden, b.created_by.clone()))
                .unwrap_or((false, None))
        };
        let created_by = created_by_override
            .map(|created_by| created_by.to_string())
            .or(existing_created_by);

        let bot_info = BotInfo {
            summary: caps.summary.clone(),
            domains: caps.domains.clone(),
            skills: caps.skills.clone(),
            scopes: caps.scopes.clone(),
            binding_channels: caps.binding_channels.clone(),
            hidden,
            agent_code: caps.agent_code.clone(),
            agent_token: caps.agent_token.clone(),
            ..Default::default()
        };
        let bot_info_json = serde_json::to_string(&bot_info)
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;

        let name = caps.name.as_deref().unwrap_or(bot_uuid);

        // Dedicated `agent_code` column (transition: dual-written alongside the
        // `bot_info` JSON above). None maps to SQL NULL.
        let agent_code_value = match caps.agent_code.as_deref() {
            Some(code) => Value::from(code),
            None => Value::Null,
        };

        // Check if record exists
        let exists = self.exists_in_db(bot_uuid).await;

        // Normalize: empty visibility defaults to "private" before persisting
        let visibility = if caps.visibility.is_empty() {
            "private"
        } else {
            &caps.visibility
        };

        let affected = if exists {
            // UPDATE existing record — created_by is NOT updated here;
            // use update_created_by_in_db() / save_created_by() for that.
            // Only overwrite session_token when we have a value; None means
            // the caller doesn't know the token — preserve whatever is in DB.
            // Update only lifecycle-owned JSON fields so private control-plane
            // attributes and forward-compatible keys remain authoritative.
            let (bot_info_assignment, bot_info_params) = bot_info_json_set(vec![
                ("$.summary", serde_json::to_value(&bot_info.summary)?),
                ("$.domains", serde_json::to_value(&bot_info.domains)?),
                ("$.skills", serde_json::to_value(&bot_info.skills)?),
                ("$.scopes", serde_json::to_value(&bot_info.scopes)?),
                (
                    "$.binding_channels",
                    serde_json::to_value(&bot_info.binding_channels)?,
                ),
                ("$.agent_code", serde_json::to_value(&bot_info.agent_code)?),
                ("$.agent_token", serde_json::to_value(&bot_info.agent_token)?),
            ])?;
            if let Some(token) = session_token {
                let sql = format!(
                    "UPDATE bcs_bots SET name = ?, {bot_info_assignment}, session_token = ?, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?"
                );
                let mut params = vec![Value::from(name)];
                params.extend(bot_info_params);
                params.extend([
                    Value::from(token),
                    Value::from(visibility),
                    agent_code_value.clone(),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ]);
                self.db_execute_affected(&sql, params).await
            } else {
                let sql = format!(
                    "UPDATE bcs_bots SET name = ?, {bot_info_assignment}, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?"
                );
                let mut params = vec![Value::from(name)];
                params.extend(bot_info_params);
                params.extend([
                    Value::from(visibility),
                    agent_code_value.clone(),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ]);
                self.db_execute_affected(&sql, params).await
            }
        } else {
            // INSERT new record — None maps to SQL NULL.
            // P.6: explicitly write status='online' on first INSERT (do NOT rely
            // on the column default). UPSERT-style updates above intentionally
            // leave `status` untouched so a hidden actor stays hidden across
            // re-onboards (Requirement 3.16#7).
            let sql = "INSERT INTO bcs_bots (bot_uuid, name, bot_info, session_token, created_by, visibility, status, actor_kind, agent_code, is_deleted, env, registered_at, updated_at, connection_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)";
            self.db_execute_affected(sql, vec![
                Value::from(bot_uuid),
                Value::from(name),
                Value::from(bot_info_json.as_str()),
                match session_token {
                    Some(t) => Value::from(t),
                    None => Value::Null,
                },
                match created_by.as_deref() {
                    Some(cb) => Value::from(cb),
                    None => Value::Null,
                },
                Value::from(visibility),
                Value::from("online"),
                Value::from("bot"),
                agent_code_value.clone(),
                Value::from(0_i64),
                Value::from(env.as_str()),
                Value::from(bcs_service_api::bot_provider::BotConnectionMode::Plugin.as_str()),
            ]).await
        }.map_err(|e| {
            warn!(request_id = %bcs_observability::CurrentRequestId, bot_uuid = %bot_uuid, error = %e, "save_to_db: failed");
            ServiceError::InternalError(e.to_string())
        })?;

        info!(bot_uuid = %bot_uuid, affected_rows = affected, exists = exists, "save_to_db: {} completed", if exists { "update" } else { "insert" });

        Ok(())
    }

    /// Load bot capabilities, env, hidden flag, and created_by from the configured database.
    ///
    /// H.2: `hidden` is now derived from the new `bcs_bots.status` column
    /// (`status == 'hidden'`). The legacy `bot_info.hidden` field is no longer
    /// authoritative — if the row was migrated but `bot_info.hidden=true` is
    /// still on disk, it is ignored in favor of `status`.
    async fn try_load_from_db(
        &self,
        bot_uuid: &str,
        include_deleted: bool,
    ) -> ServiceResult<
        Option<(
            BotCapabilities,
            Option<String>,
            bool,
            Option<String>,
            bcs_service_api::ActorKind,
            bcs_service_api::ActorStatus,
        )>,
    > {
        let env = resolve_env();
        // Code-Review fix #1: include `actor_kind` so the registry read path can
        // propagate it back to callers (O.5 / P.3 / F.3 all gate on actor_kind).
        //
        // `include_deleted` skips the soft-delete filter so callers that only
        // need display metadata (e.g. group participant names of removed bots)
        // can still read the `name` snapshot from the retained row.
        let sql = if include_deleted {
            "SELECT name, bot_info, visibility, status, actor_kind, env, created_by, agent_code FROM bcs_bots WHERE bot_uuid = ? AND env = ?".to_string()
        } else {
            "SELECT name, bot_info, visibility, status, actor_kind, env, created_by, agent_code FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0".to_string()
        };

        let rows = self
            .db_query(&sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "load Bot '{bot_uuid}' from registry database: {error}"
                ))
            })?;

        match rows.first() {
            Some(row) => Ok(Some(Self::decode_registry_row(row, bot_uuid)?)),
            None => Ok(None),
        }
    }

    /// Shared row decode for the registry loads (single-row and
    /// batched): one column list and one set of derivations — a
    /// batched read must never drift from the single-row one.
    fn decode_registry_row(
        row: &DbRow,
        bot_uuid: &str,
    ) -> ServiceResult<(
        BotCapabilities,
        Option<String>,
        bool,
        Option<String>,
        bcs_service_api::ActorKind,
        bcs_service_api::ActorStatus,
    )> {
        let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
        let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
        let visibility: String = db_get_column_opt(row, "visibility")
            .ok()
            .flatten()
            .filter(|v: &String| !v.is_empty())
            .unwrap_or_else(|| "private".to_string());
        // H.2: derive hidden from bcs_bots.status, not bot_info.hidden.
        // Status column may be absent on un-migrated rows → default to "online".
        let status_str: String = db_get_column_opt(row, "status")
            .ok()
            .flatten()
            .filter(|v: &String| !v.is_empty())
            .unwrap_or_else(|| "online".to_string());
        // Code-Review fix #1: read actor_kind from the database; default to bot for
        // un-migrated rows (legacy data has no actor_kind column populated).
        let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
            .ok()
            .flatten()
            .filter(|v: &String| !v.is_empty())
            .unwrap_or_else(|| "bot".to_string());
        let created_by: Option<String> = db_get_column_opt(row, "created_by").ok().flatten();
        let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
            .ok()
            .flatten()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();

        let hidden = status_str == "hidden";
        let actor_status = match status_str.as_str() {
            "hidden" => bcs_service_api::ActorStatus::Hidden,
            _ => bcs_service_api::ActorStatus::Online,
        };
        let actor_kind = match actor_kind_str.as_str() {
            "human" => bcs_service_api::ActorKind::Human,
            _ => bcs_service_api::ActorKind::Bot,
        };

        // agent_code transition: prefer the dedicated `agent_code` column;
        // fall back to the legacy `bot_info.agent_code` JSON for rows written
        // before the column existed (historical data not yet backfilled).
        let col_agent_code: Option<String> = db_get_column_opt(row, "agent_code").ok().flatten();
        let agent_code = match (col_agent_code, bot_info.agent_code) {
            (Some(code), _) => {
                info!(
                    bot_uuid = %bot_uuid,
                    source = "column",
                    "load_from_mysql: agent_code resolved from dedicated column"
                );
                Some(code)
            }
            (None, Some(code)) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_uuid = %bot_uuid,
                    source = "bot_info_fallback",
                    "load_from_mysql: agent_code missing in column, fell back to bot_info JSON"
                );
                Some(code)
            }
            (None, None) => {
                info!(
                    bot_uuid = %bot_uuid,
                    "load_from_mysql: agent_code absent in both column and bot_info"
                );
                None
            }
        };

            return Ok((
                BotCapabilities {
                    name,
                    summary: bot_info.summary,
                    domains: bot_info.domains,
                    skills: bot_info.skills,
                    scopes: bot_info.scopes,
                    binding_channels: bot_info.binding_channels,
                    hidden,
                    visibility,
                    agent_code,
                    agent_token: bot_info.agent_token,
                },
                env,
                hidden,
                created_by,
                actor_kind,
                actor_status,
            ));
    }

    /// Batched strict registry load (legacy-mine ruling: ONE
    /// chunked statement set instead of a per-id round trip).
    /// The row decode is shared with the single-row path so a
    /// batched read can never drift from it.
    async fn try_load_many_from_db(
        &self,
        bot_uuids: &[String],
        include_deleted: bool,
    ) -> ServiceResult<
        Vec<(
            String,
            (
                BotCapabilities,
                Option<String>,
                bool,
                Option<String>,
                bcs_service_api::ActorKind,
                bcs_service_api::ActorStatus,
            ),
        )>,
    > {
        let env = resolve_env();
        let mut loaded = Vec::new();
        for chunk in bot_uuids.chunks(REGISTRY_LOAD_CHUNK) {
            let placeholders = std::iter::repeat("?")
                .take(chunk.len())
                .collect::<Vec<_>>()
                .join(", ");
            let filter = if include_deleted {
                ""
            } else {
                " AND COALESCE(is_deleted, 0) = 0"
            };
            let sql = format!(
                "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, \
                 env, created_by, agent_code FROM bcs_bots \
                 WHERE bot_uuid IN ({placeholders}) AND env = ?{filter}"
            );
            let params: Vec<Value> = chunk
                .iter()
                .map(|id| Value::from(id.as_str()))
                .chain(std::iter::once(Value::from(env.as_str())))
                .collect();
            let rows = self.db_query(&sql, params).await.map_err(|error| {
                ServiceError::InternalError(format!(
                    "batch load Bots from registry database: {error}"
                ))
            })?;
            for row in &rows {
                let bot_uuid: String = db_get_column(row, "bot_uuid")
                    .map_err(|error| {
                        ServiceError::InternalError(format!(
                            "batch load Bots bot_uuid column: {error}"
                        ))
                    })?;
                let decoded = Self::decode_registry_row(row, &bot_uuid)?;
                loaded.push((bot_uuid, decoded));
            }
        }
        Ok(loaded)
    }

    async fn load_from_db(
        &self,
        bot_uuid: &str,
        include_deleted: bool,
    ) -> Option<(
        BotCapabilities,
        Option<String>,
        bool,
        Option<String>,
        bcs_service_api::ActorKind,
        bcs_service_api::ActorStatus,
    )> {
        self.try_load_from_db(bot_uuid, include_deleted)
            .await
            .ok()
            .flatten()
    }

    /// Read the `bot_info` JSON column for a given bot, parsed as a JSON object.
    /// Returns `None` if the row is missing, the column is NULL, or JSON parsing fails.
    async fn read_bot_info_json(&self, bot_uuid: &str) -> Option<serde_json::Value> {
        let env = resolve_env();
        let sql = "SELECT bot_info FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 LIMIT 1";
        let rows = self
            .db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .ok()?;
        let row = rows.first()?;
        let bot_info_str: String = db_get_column_opt(row, "bot_info").ok().flatten()?;
        serde_json::from_str(&bot_info_str).ok()
    }

    /// Check if bot exists in the configured database.
    async fn exists_in_db(&self, bot_uuid: &str) -> bool {
        let env = resolve_env();
        let sql = "SELECT 1 FROM bcs_bots WHERE bot_uuid = ? AND env = ? LIMIT 1";
        self.db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map(|r| !r.is_empty())
            .unwrap_or(false)
    }

    /// Save session token to the configured database.
    async fn save_token_to_db(&self, bot_uuid: &str, token: &str) -> ServiceResult<()> {
        let env = resolve_env();

        let sql = "UPDATE bcs_bots SET session_token = ? WHERE bot_uuid = ? AND env = ?";

        info!(bot_uuid = %bot_uuid, env = %env, "save_token_to_db: executing update");

        let affected = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(token),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_uuid = %bot_uuid, error = %e, "save_token_to_db: failed");
                ServiceError::InternalError(e.to_string())
            })?;

        info!(bot_uuid = %bot_uuid, affected_rows = affected, "save_token_to_db: update completed");

        Ok(())
    }

    /// Update created_by in the configured database.
    /// - `overwrite=false`: only if currently NULL (first writer wins, original behavior)
    /// - `overwrite=true`: unconditional update (last writer wins)
    async fn update_created_by_in_db(
        &self,
        bot_uuid: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        let env = resolve_env();
        let sql = if overwrite {
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ?"
        } else {
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ? AND created_by IS NULL"
        };

        let affected = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(created_by),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_uuid = %bot_uuid, error = %e, "update_created_by_in_db: failed");
                ServiceError::InternalError(e.to_string())
            })?;

        info!(bot_uuid = %bot_uuid, created_by = %created_by, affected_rows = affected, "update_created_by_in_db: completed");

        Ok(())
    }

    /// Query bots by creator from the configured database.
    ///
    /// H.3: SELECT now includes `status` and `actor_kind`; `caps.hidden` is
    /// derived from `status == 'hidden'` (legacy `bot_info.hidden` ignored).
    async fn list_bots_by_creator_from_db(
        &self,
        created_by: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        // Byte-exact creator predicate, dialect-agnostic: the plain
        // `=` prefilter uses the created_by index on MySQL (where a
        // case-insensitive column collation admits a wider prefix set),
        // and the HEX equality refines it byte-for-byte on every
        // engine (a CAST-predicate is vendor syntax and breaks on the
        // SQLite-backed MysqlStore fixtures). A NULL creator never matches.
        let sql = "SELECT bot_uuid, name, bot_info, visibility, status, \
             actor_kind, env, created_by FROM bcs_bots \
             WHERE created_by = ? AND HEX(created_by) = HEX(?) \
               AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = self
            .db_query(
                sql,
                vec![
                    Value::from(created_by),
                    Value::from(created_by),
                    Value::from(env),
                ],
            )
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;

        Ok(rows
            .iter()
            .filter_map(Self::creator_listing_row_to_registered_bot)
            .collect())
    }

    /// Shared row → RegisteredBot projection for the creator-scoped
    /// listings (plain creator list and the mine sweep's
    /// uninitialized-candidate query — one mapping, no drift).
    fn creator_listing_row_to_registered_bot(row: &DbRow) -> Option<RegisteredBot> {
                let bot_uuid: String = db_get_column_opt(row, "bot_uuid").ok().flatten()?;
                let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
                let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "protected".to_string());
                let created_by: Option<String> =
                    db_get_column_opt(row, "created_by").ok().flatten();
                let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                    .ok()
                    .flatten()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();

                // H.3: derive hidden from bcs_bots.status (column may be absent
                // on un-migrated rows → default to "online" → hidden=false).
                let status_str: String = db_get_column_opt(row, "status")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "online".to_string());
                let hidden = status_str == "hidden";
                let status = match status_str.as_str() {
                    "hidden" => bcs_service_api::ActorStatus::Hidden,
                    _ => bcs_service_api::ActorStatus::Online,
                };

                let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "bot".to_string());
                let actor_kind = match actor_kind_str.as_str() {
                    "human" => bcs_service_api::ActorKind::Human,
                    _ => bcs_service_api::ActorKind::Bot,
                };

                Some(RegisteredBot {
                    bot_uuid,
                    capabilities: BotCapabilities {
                        name,
                        summary: bot_info.summary,
                        domains: bot_info.domains,
                        skills: bot_info.skills,
                        scopes: bot_info.scopes,
                        binding_channels: bot_info.binding_channels,
                        hidden,
                        visibility,
                        // SECURITY: 敏感字段置空，防止通过常规接口泄露
                        agent_code: None,
                        agent_token: None,
                    },
                    env,
                    created_by,
                    actor_kind,
                    status,
                })
    }

    /// The mine sweep's store-side prefilter: same byte-exact creator
    /// predicate as the plain creator listing (HEX refine), narrowed to
    /// live physical Bots whose ownership migration is still pending
    /// (`ownership_version = 0`). Sorted + capped for deterministic
    /// progression across capped sweeps.
    async fn list_uninitialized_bots_by_creator_from_db(
        &self,
        created_by: &str,
        env: &str,
        limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        // Same byte-exact creator contract as the plain creator
        // listing (HEX refine), narrowed to live physical Bots whose
        // ownership migration is still pending — and with the
        // bcs-admin classify conflict prefilter encoded too: a Bot
        // carrying an approved owner edge OTHER than this caller's
        // would block the bounded window forever, so it never enters
        // the candidate page at all (the single agreeing edge still
        // passes, mirroring classify's migratable carve).
        let owner_actor = format!("human_{created_by}");
        let sql = "SELECT bot_uuid, name, bot_info, visibility, status, \
             actor_kind, env, created_by FROM bcs_bots \
             WHERE created_by = ? AND HEX(created_by) = HEX(?) \
               AND env = ? AND COALESCE(is_deleted, 0) = 0 \
               AND actor_kind = 'bot' AND ownership_version = 0 \
               AND NOT EXISTS ( \
                 SELECT 1 FROM edge_grants g \
                 WHERE g.env = bcs_bots.env AND g.to_id = bcs_bots.bot_uuid \
                   AND g.grant_kind = 'owner' AND g.status = 'approved' \
                   AND g.from_id <> ? \
               ) \
             ORDER BY bot_uuid LIMIT ?";

        let rows = self
            .db_query(
                sql,
                vec![
                    Value::from(created_by),
                    Value::from(created_by),
                    Value::from(env),
                    Value::from(owner_actor.as_str()),
                    Value::from(limit as i64),
                ],
            )
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;

        Ok(rows
            .iter()
            .filter_map(Self::creator_listing_row_to_registered_bot)
            .collect())
    }

    /// Load session token from the configured database.
    async fn load_token_from_db(&self, bot_uuid: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT session_token FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = self
            .db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .ok()?;

        if let Some(row) = rows.first() {
            return db_get_column_opt(row, "session_token").ok().flatten();
        }

        None
    }

    /// Find bot by token in the configured database (indexed lookup).
    async fn find_bot_by_token_in_db(&self, token: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid FROM bcs_bots WHERE session_token = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        info!(env = %env, "find_bot_by_token_in_db: executing query");

        let rows = match self
            .db_query(sql, vec![Value::from(token), Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(_) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, env = %env, "find_bot_by_token_in_db: database query failed");
                return None;
            }
        };

        info!(rows_count = rows.len(), "find_bot_by_token_in_db: query completed");

        if let Some(row) = rows.first() {
            match db_get_column::<String>(row, "bot_uuid") {
                Ok(bot_uuid) => {
                    info!(bot_uuid = %bot_uuid, "find_bot_by_token_in_db: found bot");
                    return Some(bot_uuid);
                }
                Err(_) => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, "find_bot_by_token_in_db: failed to get bot_uuid");
                    return None;
                }
            }
        }

        warn!(request_id = %bcs_observability::CurrentRequestId, "find_bot_by_token_in_db: no bot found");
        None
    }

    /// Resolve a bot by its dedicated `agent_code` column via the indexed
    /// lookup (`idx_agent_code`). Returns the first matching `bot_uuid`.
    async fn find_bot_by_agent_code_in_db(&self, agent_code: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid FROM bcs_bots WHERE agent_code = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = match self
            .db_query(sql, vec![Value::from(agent_code), Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, agent_code = %agent_code, env = %env, error = %e, "find_bot_by_agent_code_in_db: query failed");
                return None;
            }
        };

        if let Some(row) = rows.first() {
            match db_get_column::<String>(row, "bot_uuid") {
                Ok(bot_uuid) => {
                    info!(agent_code = %agent_code, bot_uuid = %bot_uuid, "find_bot_by_agent_code_in_db: found bot");
                    return Some(bot_uuid);
                }
                Err(e) => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, agent_code = %agent_code, error = %e, "find_bot_by_agent_code_in_db: failed to get bot_uuid");
                    return None;
                }
            }
        }

        warn!(request_id = %bcs_observability::CurrentRequestId, agent_code = %agent_code, "find_bot_by_agent_code_in_db: no bot found");
        None
    }

    /// Soft-delete bot from the configured database.
    async fn soft_delete_in_db(&self, bot_uuid: &str) -> bool {
        let env = resolve_env();
        let sql = "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        self.db_execute_affected(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map(|n| n > 0)
            .unwrap_or(false)
    }

    /// Sync binding channel index for a bot.
    /// Removes old bindings and adds new ones.
    async fn sync_binding_channel_index(&self, bot_id: &str, capabilities: &BotCapabilities) {
        let mut index = self.binding_channel_index.write().await;

        // Remove old bindings for this bot
        index.retain(|_, v| v != bot_id);

        // Add new bindings
        if let Some(ref binding_channels) = capabilities.binding_channels {
            for (channel, binding) in binding_channels {
                index.insert(
                    (channel.clone(), binding.binding_key.clone()),
                    bot_id.to_string(),
                );
            }
        }
    }

    /// Query bots from the configured database by name with pagination.
    /// Uses a CTE to resolve friendships and applies cooperatability filtering in SQL.
    ///
    /// Returns `(Vec<(RegisteredBot, bool)>, usize)` where bool = is_friend.
    async fn list_bots_by_name_and_cooperatable_with_impl(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let env = resolve_env();
        let like = if name.is_empty() {
            "%".to_string()
        } else {
            format!("%{}%", name.replace('%', r"\%").replace('_', r"\_"))
        };

        let coop_flag: i64 = if cooperatable_only { 1 } else { 0 };

        let visibility_filter = self.flavor.iif(
            "?",
            "b.visibility = 'public' OR f.bot_uuid IS NOT NULL",
            "b.visibility IN ('public', 'protected')",
        );

        let page_sql = format!(
            "WITH friend_uuids AS (\
                SELECT right_bot AS bot_uuid FROM bcs_friendships \
                WHERE left_bot = ? AND env = ? \
                UNION \
                SELECT left_bot AS bot_uuid FROM bcs_friendships \
                WHERE right_bot = ? AND env = ? \
            ) \
            SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.env, b.created_by, \
                   b.actor_kind, b.status, \
                   f.bot_uuid IS NOT NULL as is_friend \
            FROM bcs_bots b \
            LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
            WHERE b.env = ? \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.name LIKE ? \
              AND b.bot_uuid != ? \
              AND (b.actor_kind IS NULL OR b.actor_kind != 'human') \
              AND {} \
            ORDER BY b.id DESC \
            LIMIT ? OFFSET ?",
            visibility_filter
        );

        let page_rows = match self
            .db_query(
                &page_sql,
                vec![
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(env.as_str()),
                    Value::from(like.as_str()),
                    Value::from(bot_uuid),
                    Value::from(coop_flag),
                    Value::from(limit as i64),
                    Value::from(offset as i64),
                ],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "list_bots_by_name_and_cooperatable_with_impl (page): failed");
                return (Vec::new(), 0);
            }
        };

        let count_sql = format!(
            "WITH friend_uuids AS (\
                SELECT right_bot AS bot_uuid FROM bcs_friendships \
                WHERE left_bot = ? AND env = ? \
                UNION \
                SELECT left_bot AS bot_uuid FROM bcs_friendships \
                WHERE right_bot = ? AND env = ? \
            ) \
            SELECT count(*) AS total \
            FROM bcs_bots b \
            LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
            WHERE b.env = ? \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.name LIKE ? \
              AND b.bot_uuid != ? \
              AND (b.actor_kind IS NULL OR b.actor_kind != 'human') \
              AND {}",
            visibility_filter
        );

        let total: usize = match self
            .db_query(
                &count_sql,
                vec![
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(env.as_str()),
                    Value::from(like.as_str()),
                    Value::from(bot_uuid),
                    Value::from(coop_flag),
                ],
            )
            .await
        {
            Ok(rows) => rows
                .first()
                .and_then(|row| db_get_column_opt::<i64>(row, "total").ok().flatten())
                .map(|v| v as usize)
                .unwrap_or(0),
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, error = %e, "list_bots_by_name_and_cooperatable_with_impl (count): failed");
                0
            }
        };

        let mut results = Vec::new();
        for row in &page_rows {
            let bot_uuid: String = match db_get_column_opt::<String>(row, "bot_uuid").ok().flatten()
            {
                Some(v) => v,
                None => continue,
            };
            let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
            let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
            let visibility: String = db_get_column_opt(row, "visibility")
                .ok()
                .flatten()
                .unwrap_or_else(|| "protected".to_string());
            let created_by: Option<String> = db_get_column_opt(row, "created_by").ok().flatten();
            let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                .ok()
                .flatten()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default();
            let is_friend: bool = db_get_column_opt::<i64>(row, "is_friend")
                .ok()
                .flatten()
                .map(|v| v != 0)
                .unwrap_or(false);

            // Human Actor V1: propagate persisted actor_kind / status so this
            // batch-by-ids path matches the single-row load (`load_bot_capabilities`
            // / `to_registered_bot`); otherwise downstream `O.5` / `P.3` / `F.3`
            // checks misclassify every actor as `Bot` / `Online`.
            let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "bot".to_string());
            let actor_kind = match actor_kind_str.as_str() {
                "human" => bcs_service_api::ActorKind::Human,
                _ => bcs_service_api::ActorKind::Bot,
            };
            let status_str: String = db_get_column_opt(row, "status")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "online".to_string());
            let status = match status_str.as_str() {
                "hidden" => bcs_service_api::ActorStatus::Hidden,
                _ => bcs_service_api::ActorStatus::Online,
            };

            let bot = RegisteredBot {
                bot_uuid,
                capabilities: BotCapabilities {
                    name,
                    summary: bot_info.summary,
                    domains: bot_info.domains,
                    skills: bot_info.skills,
                    scopes: bot_info.scopes,
                    binding_channels: bot_info.binding_channels,
                    hidden: bot_info.hidden,
                    visibility,
                    // SECURITY: 敏感字段置空，防止通过常规接口泄露
                    agent_code: None,
                    agent_token: None,
                },
                env,
                created_by,
                actor_kind,
                status,
            };
            results.push((bot, is_friend));
        }

        (results, total)
    }
}

fn sql_metric_actor_kind(raw: &str) -> bcs_service_api::ActorKind {
    match raw {
        "human" => bcs_service_api::ActorKind::Human,
        _ => bcs_service_api::ActorKind::Bot,
    }
}

fn sql_metric_actor_status(raw: &str) -> bcs_service_api::ActorStatus {
    match raw {
        "hidden" => bcs_service_api::ActorStatus::Hidden,
        _ => bcs_service_api::ActorStatus::Online,
    }
}

#[async_trait]
impl BotMetricsSnapshotPort for PersistentBotRepo {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
        let env = resolve_env();
        let sql = "SELECT actor_kind, status, visibility, COUNT(*) AS bot_count \
                   FROM ( \
                       SELECT \
                           CASE WHEN actor_kind = 'human' THEN 'human' ELSE 'bot' END AS actor_kind, \
                           CASE WHEN status = 'hidden' THEN 'hidden' ELSE 'online' END AS status, \
                           CASE \
                               WHEN visibility IS NULL OR TRIM(visibility) = '' THEN 'private' \
                               WHEN visibility IN ('public', 'protected', 'private') THEN visibility \
                               ELSE 'other' \
                           END AS visibility \
                       FROM bcs_bots \
                       WHERE env = ? \
                         AND COALESCE(is_deleted, 0) = 0 \
                   ) metric_bots \
                   GROUP BY actor_kind, status, visibility";
        let rows = self
            .db_query(sql, vec![Value::from(env.as_str())])
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, env = %env, error = %e, "bot metrics snapshot query failed");
                ServiceError::InternalError(format!("bot metrics snapshot query failed: {}", e))
            })?;

        let mut counts = Vec::with_capacity(rows.len());
        for row in rows {
            let actor_kind_raw: String = db_get_column(&row, "actor_kind").map_err(|e| {
                ServiceError::InternalError(format!(
                    "bot metrics actor_kind conversion failed: {}",
                    e
                ))
            })?;
            let status_raw: String = db_get_column(&row, "status").map_err(|e| {
                ServiceError::InternalError(format!("bot metrics status conversion failed: {}", e))
            })?;
            let visibility: String = db_get_column(&row, "visibility").map_err(|e| {
                ServiceError::InternalError(format!(
                    "bot metrics visibility conversion failed: {}",
                    e
                ))
            })?;
            let bot_count: i64 = db_get_column(&row, "bot_count").map_err(|e| {
                ServiceError::InternalError(format!("bot metrics count conversion failed: {}", e))
            })?;
            let count = u64::try_from(bot_count).map_err(|e| {
                ServiceError::InternalError(format!("bot metrics count is invalid: {}", e))
            })?;
            if count == 0 {
                continue;
            }

            counts.push(BotMetricCount {
                actor_kind: sql_metric_actor_kind(&actor_kind_raw),
                status: sql_metric_actor_status(&status_raw),
                visibility: Some(visibility),
                count,
            });
        }
        Ok(counts)
    }
}
