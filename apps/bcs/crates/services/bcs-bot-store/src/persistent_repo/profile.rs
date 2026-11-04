//! Existing-only Bot profile (capabilities + `avatar_url`) writes and the
//! batched avatar read for `PersistentBotRepo` (bot-avatar plan Task 3).
//!
//! The writes are strict `UPDATE ... WHERE bot_uuid = ? AND env = ? AND
//! COALESCE(is_deleted, 0) = 0` statements inside ONE short DB transaction
//! that first confirms/locks the target live record — no INSERT and no
//! upsert. The confirm-then-write shape exists because MySQL (no
//! found-rows flag on the connection) reports ZERO affected rows for an
//! UPDATE whose values are already current, so the affected-rows number
//! alone cannot distinguish "already stored" from "row gone". The
//! transaction classification comes from the confirmed live record instead:
//!
//! - confirm sees the live row  → the write is `ProfileWrite::Updated`,
//!   regardless of the (possibly zero) affected rows of the UPDATE;
//! - confirm sees no row       → the write is `ProfileWrite::Missing` and
//!   the (then matching-nothing) UPDATE changes nothing;
//! - read or commit failures   → errors, never a silent success or a
//!   misreported `Missing`.
//!
//! On MySQL the confirm is a `SELECT ... FOR UPDATE`, so a concurrent
//! soft delete cannot slip between the confirm and the write; on SQLite
//! the plugin opens transactions with the write lock already reserved
//! (`BEGIN IMMEDIATE`) before the confirm, giving the same serialization.

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{
    DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult, DbValue as Value,
    db_get_column, db_get_column_opt,
};
use bcs_service_api::types::bot_profile::{
    AvatarChange, BotAvatarMap, ExistingProfileUpdate, ProfileWrite,
};
use bcs_service_api::{BotCapabilities, ServiceError, ServiceResult};
use tracing::warn;

use super::registry_storage::project_profile_columns;
use super::PersistentBotRepo;

/// Maximum number of distinct ids one batched avatar read accepts.
const AVATAR_BATCH_LIMIT: usize = 256;

impl PersistentBotRepo {
    /// Existing-only profile write: replace the live row's profile fields
    /// and apply the avatar transition in the same statement/transaction.
    /// The registry-owned runtime identity (`agent_code` column and the
    /// bot_info identity keys) never appears in the statement, so a
    /// capabilities save that carries no identity preserves the row's
    /// current AgentPass credentials.
    pub(super) async fn update_profile_existing_impl(
        &self,
        bot_id: &str,
        update: ExistingProfileUpdate,
    ) -> ServiceResult<ProfileWrite> {
        let columns = project_profile_columns(bot_id, &update.capabilities)?;
        let mut sql = format!(
            "UPDATE bcs_bots SET name = CASE WHEN agent_card IS NULL THEN ? ELSE name END, {bot_info_assignment}, \
             visibility = ?",
            bot_info_assignment = columns.bot_info_assignment
        );
        let mut params = vec![Value::from(columns.name.as_str())];
        params.extend(columns.bot_info_params);
        params.push(Value::from(columns.visibility.as_str()));
        if let Some(assignment) = avatar_assignment(&update.avatar) {
            sql.push_str(", ");
            sql.push_str(assignment);
            params.push(avatar_value(&update.avatar));
        }
        sql.push_str(
            ", updated_at = CURRENT_TIMESTAMP \
             WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0",
        );
        params.push(Value::from(bot_id));
        params.push(Value::from(resolve_env()));

        let write = self
            .confirm_and_write_existing(
                bot_id,
                Some(DbStatement::with_params(sql, params)),
            )
            .await?;
        if write == ProfileWrite::Updated {
            // Same reverse-index maintenance as every sibling capability save
            // (`save_to_storage`/`register_impl`): drop the bot's old
            // binding entries and install the committed capabilities'
            // bindings. The index is process-local, so this runs inside the
            // same write method right after the committed transaction — a
            // `Missing` target or a failed write leaves it untouched.
            self.sync_binding_channel_index(bot_id, &update.capabilities)
                .await;
            self.publish_profile_capabilities(bot_id, &update.capabilities)
                .await;
        }
        Ok(write)
    }

    /// Existing-only avatar write: one avatar field transition on the live
    /// row; every other column (including the generic audit time) stays
    /// untouched, so an unchanged-value update keeps its deterministic
    /// zero-changed-rows shape on MySQL.
    pub(super) async fn update_avatar_existing_impl(
        &self,
        bot_id: &str,
        avatar: AvatarChange,
    ) -> ServiceResult<ProfileWrite> {
        // `Keep` never appears in a SET clause: it only confirms existence
        // and reports it, without writing the record.
        let Some(assignment) = avatar_assignment(&avatar) else {
            return self.confirm_and_write_existing(bot_id, None).await;
        };
        let sql = format!(
            "UPDATE bcs_bots SET {assignment} \
             WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0"
        );
        let params = vec![
            avatar_value(&avatar),
            Value::from(bot_id),
            Value::from(resolve_env()),
        ];
        self.confirm_and_write_existing(
            bot_id,
            Some(DbStatement::with_params(sql, params)),
        )
        .await
    }

    /// In ONE short transaction, confirm (and on MySQL lock) the target
    /// live record, run the optional guarded UPDATE and classify the write
    /// from the confirmed record. Reads, writes and commit failures stay
    /// errors.
    async fn confirm_and_write_existing(
        &self,
        bot_id: &str,
        statement: Option<DbStatement>,
    ) -> ServiceResult<ProfileWrite> {
        let env = resolve_env();
        let lock = match self.flavor {
            DbSqlFlavor::Mysql => " FOR UPDATE",
            DbSqlFlavor::Sqlite => "",
        };
        let confirm = DbTransactionStep::Query(DbStatement::with_params(
            format!(
                "SELECT 1 AS live FROM bcs_bots \
                 WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0{lock}"
            ),
            vec![Value::from(bot_id), Value::from(env)],
        ));
        let mut steps = Vec::with_capacity(2);
        steps.push(confirm);
        if let Some(statement) = statement {
            steps.push(DbTransactionStep::Execute(statement));
        }
        let results = self.db_transaction(steps).await.map_err(|error| {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                bot_id = %bot_id,
                error = %error,
                "existing-only bot profile write failed"
            );
            ServiceError::InternalError(error.to_string())
        })?;
        // The confirm query is the first step; zero rows means no live
        // record matched (nothing was or will be written — the UPDATE's
        // WHERE cannot match a missing or tombstoned identity).
        let confirmed_live = results
            .first()
            .map(|result| match result {
                DbTransactionStepResult::Rows(rows) => !rows.is_empty(),
                DbTransactionStepResult::Executed(_) => false,
            })
            .unwrap_or(false);
        if confirmed_live {
            Ok(ProfileWrite::Updated)
        } else {
            Ok(ProfileWrite::Missing)
        }
    }

    /// Batched, env-isolated avatar read: live and tombstoned Bot rows are
    /// returned (tombstone display); Human actor rows are excluded. Empty
    /// input queries nothing; more than `AVATAR_BATCH_LIMIT` distinct ids
    /// is an explicit `InvalidOperation`.
    pub(super) async fn read_avatar_batch_impl(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<BotAvatarMap> {
        let mut distinct: Vec<String> = Vec::with_capacity(bot_ids.len());
        let mut seen = std::collections::HashSet::with_capacity(bot_ids.len());
        for bot_id in bot_ids {
            if seen.insert(bot_id.as_str()) {
                distinct.push(bot_id.clone());
            }
        }
        if distinct.is_empty() {
            return Ok(BotAvatarMap::new());
        }
        if distinct.len() > AVATAR_BATCH_LIMIT {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "bot avatar batch read accepts at most {AVATAR_BATCH_LIMIT} distinct bot ids"
                ),
                request_id: None,
            });
        }
        let env = resolve_env();
        let placeholders = vec!["?"; distinct.len()].join(", ");
        let sql = format!(
            "SELECT bot_uuid, avatar_url FROM bcs_bots \
             WHERE bot_uuid IN ({placeholders}) AND env = ? \
               AND COALESCE(actor_kind, 'bot') <> 'human'"
        );
        let params: Vec<Value> = distinct
            .iter()
            .map(|bot_id| Value::from(bot_id.as_str()))
            .chain(std::iter::once(Value::from(env)))
            .collect();
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|error| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    error = %error,
                    "bot avatar batch read failed"
                );
                ServiceError::InternalError(error.to_string())
            })?;
        let mut avatars = BotAvatarMap::with_capacity(rows.len());
        for row in &rows {
            let bot_uuid: String = db_get_column(row, "bot_uuid").map_err(|error| {
                ServiceError::InternalError(format!(
                    "bot avatar batch read bot_uuid column: {error}"
                ))
            })?;
            let avatar_url: Option<String> = db_get_column_opt(row, "avatar_url")
                .map_err(|error| {
                    ServiceError::InternalError(format!(
                        "bot avatar batch read avatar_url column: {error}"
                    ))
                })?;
            avatars.insert(bot_uuid, avatar_url);
        }
        Ok(avatars)
    }

    /// Sync the committed capabilities into an already-loaded registry
    /// entry (cache publication after the durable write; an unloaded entry
    /// is intentionally not hydrated, matching the strict registration
    /// path). Profile fields publish wholesale like the durable write; the
    /// registry-owned runtime identity (`agent_code`/`agent_token`) keeps
    /// its current values — publishing an empty identity would silently
    /// skip the Bot's outbound interceptor chain (PR-3488 review).
    async fn publish_profile_capabilities(
        &self,
        bot_id: &str,
        capabilities: &BotCapabilities,
    ) {
        use std::time::Instant;

        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(bot_id) {
            existing.last_heartbeat = Instant::now();
            existing.capabilities.name = capabilities.name.clone();
            existing.capabilities.summary = capabilities.summary.clone();
            existing.capabilities.domains = capabilities.domains.clone();
            existing.capabilities.skills = capabilities.skills.clone();
            existing.capabilities.scopes = capabilities.scopes.clone();
            existing.capabilities.binding_channels =
                capabilities.binding_channels.clone();
            existing.capabilities.visibility = capabilities.visibility.clone();
        }
    }
}

/// SQL `avatar_url` assignment for a real avatar transition, or `None` for
/// `Keep` (which must never appear in a SET clause).
fn avatar_assignment(avatar: &AvatarChange) -> Option<&'static str> {
    match avatar {
        AvatarChange::Keep => None,
        AvatarChange::Clear | AvatarChange::Set(_) => Some("avatar_url = ?"),
    }
}

/// Bound parameter for the avatar assignment (Clear writes SQL NULL).
fn avatar_value(avatar: &AvatarChange) -> Value {
    match avatar {
        AvatarChange::Keep => Value::Null,
        AvatarChange::Clear => Value::Null,
        AvatarChange::Set(url) => Value::from(url.as_str()),
    }
}
