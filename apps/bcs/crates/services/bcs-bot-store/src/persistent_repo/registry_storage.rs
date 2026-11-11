//! Registry storage SQL operations for `PersistentBotRepo`.
//!
//! Moved verbatim from `persistent_repo/mod.rs` (registry persistence
//! helpers, same-type inherent impl; SQL contracts unchanged) during the
//! bot-avatar plan Task 1 file-size split.

use tracing::{info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{DbRow, DbValue as Value, db_get_column, db_get_column_opt};
use bcs_service_api::{BotCapabilities, ServiceError, ServiceResult};

use super::PersistentBotRepo;
use crate::{BotInfo, bot_info_json_set};

/// SQL parameter chunk bound for batched registry loads.
const REGISTRY_LOAD_CHUNK: usize = 100;

/// Capabilities → `bcs_bots` column projection for the registration
/// save path ([`PersistentBotRepo::save_to_db`]), including the
/// registry-owned identity column (bot-avatar plan Task 3).
pub(super) struct CapabilitiesColumns {
    /// `name` column value (falls back to the bot id, like the save path).
    pub(super) name: String,
    /// Normalized `visibility` column value (empty capabilities → private).
    pub(super) visibility: String,
    /// `agent_code` column value (absent capabilities → SQL NULL).
    pub(super) agent_code: Value,
    /// Complete `bot_info` JSON document for whole-row INSERTs.
    pub(super) bot_info_json: String,
    /// UPDATE-row `bot_info` assignment over the lifecycle-owned keys.
    pub(super) bot_info_assignment: String,
    /// Parameters of [`Self::bot_info_assignment`].
    pub(super) bot_info_params: Vec<Value>,
}

/// Project capabilities onto the `bcs_bots` storage columns. This is the
/// single mapping the legacy save path consumes; the existing-only profile
/// write uses the profile-only [`project_profile_columns`] sibling so a
/// profile write can never touch the registry-owned identity fields.
pub(super) fn project_capabilities_columns(
    bot_uuid: &str,
    caps: &BotCapabilities,
    hidden: bool,
) -> ServiceResult<CapabilitiesColumns> {
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
    Ok(CapabilitiesColumns {
        name: caps.name.as_deref().unwrap_or(bot_uuid).to_string(),
        visibility: if caps.visibility.is_empty() {
            "private".to_string()
        } else {
            caps.visibility.clone()
        },
        agent_code: match caps.agent_code.as_deref() {
            Some(code) => Value::from(code),
            None => Value::Null,
        },
        bot_info_json,
        bot_info_assignment,
        bot_info_params,
    })
}

/// Profile-only `bcs_bots` projection for the existing-only profile write
/// (`profile.rs`): the same name/visibility/JSON-key derivation as
/// [`project_capabilities_columns`], minus the registry-owned runtime
/// identity — a profile write never touches the `agent_code` column nor the
/// `$.agent_code`/`$.agent_token` bot_info keys, so a capabilities save
/// that carries no identity cannot wipe an online Bot's AgentPass
/// credentials (and silently disable its outbound interceptor chain).
pub(super) struct ProfileColumns {
    /// `name` column value (falls back to the bot id, like the save path).
    pub(super) name: String,
    /// Normalized `visibility` column value (empty capabilities → private).
    pub(super) visibility: String,
    /// Profile-only UPDATE-row `bot_info` assignment (no identity keys).
    pub(super) bot_info_assignment: String,
    /// Parameters of [`Self::bot_info_assignment`].
    pub(super) bot_info_params: Vec<Value>,
}

/// Derive the profile-only column projection; identity derivation stays in
/// [`project_capabilities_columns`] where the registration/save paths need
/// it, so the two mappings cannot drift on the shared profile keys.
pub(super) fn project_profile_columns(
    bot_uuid: &str,
    caps: &BotCapabilities,
) -> ServiceResult<ProfileColumns> {
    let (bot_info_assignment, bot_info_params) = bot_info_json_set(vec![
        ("$.summary", serde_json::to_value(&caps.summary)?),
        ("$.domains", serde_json::to_value(&caps.domains)?),
        ("$.skills", serde_json::to_value(&caps.skills)?),
        ("$.scopes", serde_json::to_value(&caps.scopes)?),
        (
            "$.binding_channels",
            serde_json::to_value(&caps.binding_channels)?,
        ),
    ])?;
    Ok(ProfileColumns {
        name: caps.name.as_deref().unwrap_or(bot_uuid).to_string(),
        visibility: if caps.visibility.is_empty() {
            "private".to_string()
        } else {
            caps.visibility.clone()
        },
        bot_info_assignment,
        bot_info_params,
    })
}

impl PersistentBotRepo {
    /// Save bot capabilities to the configured database.
    /// First checks if the record exists, then uses UPDATE if it exists, otherwise INSERT.
    pub(super) async fn save_to_db(
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

        let columns = project_capabilities_columns(bot_uuid, caps, hidden)?;

        // Check if record exists
        let exists = self.exists_in_db(bot_uuid).await;

        let affected = if exists {
            // UPDATE existing record — created_by is NOT updated here;
            // use update_created_by_in_db() / save_created_by() for that.
            // Only overwrite session_token when we have a value; None means
            // the caller doesn't know the token — preserve whatever is in DB.
            // Update only lifecycle-owned JSON fields so private control-plane
            // attributes and forward-compatible keys remain authoritative.
            let bot_info_assignment = columns.bot_info_assignment;
            let bot_info_params = columns.bot_info_params;
            if let Some(token) = session_token {
                let sql = format!(
                    "UPDATE bcs_bots SET name = CASE WHEN agent_card IS NULL THEN ? ELSE name END, {bot_info_assignment}, session_token = ?, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?"
                );
                let mut params = vec![Value::from(columns.name.as_str())];
                params.extend(bot_info_params);
                params.extend([
                    Value::from(token),
                    Value::from(columns.visibility.as_str()),
                    columns.agent_code.clone(),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ]);
                self.db_execute_affected(&sql, params).await
            } else {
                let sql = format!(
                    "UPDATE bcs_bots SET name = CASE WHEN agent_card IS NULL THEN ? ELSE name END, {bot_info_assignment}, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?"
                );
                let mut params = vec![Value::from(columns.name.as_str())];
                params.extend(bot_info_params);
                params.extend([
                    Value::from(columns.visibility.as_str()),
                    columns.agent_code.clone(),
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
                Value::from(columns.name.as_str()),
                Value::from(columns.bot_info_json.as_str()),
                match session_token {
                    Some(t) => Value::from(t),
                    None => Value::Null,
                },
                match created_by.as_deref() {
                    Some(cb) => Value::from(cb),
                    None => Value::Null,
                },
                Value::from(columns.visibility.as_str()),
                Value::from("online"),
                Value::from("bot"),
                columns.agent_code.clone(),
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
    pub(super) async fn try_load_from_db(
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
    pub(super) async fn try_load_many_from_db(
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

    pub(super) async fn load_from_db(
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
}

impl PersistentBotRepo {
    /// Legacy discovery keeps healthy cached candidates fast, but never returns
    /// metadata invalidated after a committed privacy change or Card update.
    /// Only invalidated IDs use the existing 100-ID batched registry loader.
    pub(super) async fn refresh_invalidated_metadata(
        &self,
        candidates: Vec<bcs_service_api::RegisteredBot>,
    ) -> Vec<bcs_service_api::RegisteredBot> {
        let env = resolve_env();
        let stale: std::collections::HashSet<String> = {
            let state = self
                .registry_metadata
                .lock()
                .expect("registry metadata state poisoned");
            if state.permanent.is_empty() && state.in_flight.is_empty() { return candidates; }
            candidates.iter()
                .filter(|bot| {
                    let key = (env.clone(), bot.bot_uuid.clone());
                    state.permanent.contains(&key) || state.in_flight.contains(&key)
                })
                .map(|bot| bot.bot_uuid.clone()).collect()
        };
        if stale.is_empty() { return candidates; }
        let ids = stale.iter().cloned().collect::<Vec<_>>();
        let refreshed = match bcs_service_api::BotRepoPort::try_get_many(self, &ids).await {
            Ok(records) => records,
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, %error,
                    "discovery: invalidated metadata unavailable; omitting affected Bots");
                Vec::new()
            }
        };
        let mut refreshed = refreshed.into_iter()
            .map(|bot| (bot.bot_uuid.clone(), bot)).collect::<std::collections::HashMap<_, _>>();
        candidates.into_iter().filter_map(|bot| {
            if stale.contains(&bot.bot_uuid) { refreshed.remove(&bot.bot_uuid) }
            else { Some(bot) }
        }).collect()
    }
}
