//! `read_authorization_snapshot` — strict Group authorization-fact
//! reads (bot-authority plan Task 6.4, spec §8.3 权威资源读取).
//!
//! Semantics shared by both stores:
//!
//! - SQL reads the MASTER data source directly. It never
//!   consults the ordinary `try_get` display cache and never
//!   falls back to that cache on a read or decode failure.
//! - Memory projects from the current in-memory state.
//! - 历史 mode/scope 缺省按既有持久化解码规则投影（the same
//!   decode helpers `load_group_from_mysql` uses）.
//! - Snapshots contain no message bodies, credentials, or full
//!   configurations (spec §8.3).
//!
//! Env is bound at instance level: SQL filters `env = ?` with the
//! instance env; Memory resolves the process env per call (the same
//! `bcs_config::resolve_env_str` pattern the authority Memory
//! projection in `bcs-bot-store` uses).

use bcs_db_api::{db_get_column, db_get_column_opt, DbValue};
use bcs_service_api::types::authorization_snapshot::{
    GroupAuthorizationSnapshot, ParticipantAuthorizationSnapshot,
};
use bcs_service_api::ServiceResult;
use bcs_service_api::Participant;

use super::MySqlGroupStore;

/// One shared participant projection for both stores.
fn participant_snapshot(participant: &Participant) -> ParticipantAuthorizationSnapshot {
    ParticipantAuthorizationSnapshot {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        role: participant.role,
        effective_mode: participant.effective_mode(),
        message_view_scope: participant.message_view_scope,
    }
}

pub(super) fn memory_snapshot(participant_env: String, group: &bcs_service_api::Group) -> GroupAuthorizationSnapshot {
    GroupAuthorizationSnapshot {
        env: participant_env,
        id: group.id.clone(),
        live: group.record_status == "active",
        status: group.status,
        record_status: group.record_status.clone(),
        visibility: group.visibility.clone(),
        group_kind: group.group_kind,
        group_strategy: group.group_strategy,
        driver_actor_id: group.driver_bot.clone(),
        // Existing default-driver rule (domain `Group::originator`).
        originator_actor_id: group.originator().to_string(),
        participants: group
            .participants
            .iter()
            .map(participant_snapshot)
            .collect(),
    }
}

impl MySqlGroupStore {
    /// Strict master-source projection of one Group's
    /// authorization facts. `Ok(None)` only when the Group row
    /// (in this instance env) does not exist.
    pub(crate) async fn read_authorization_snapshot_sql(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<GroupAuthorizationSnapshot>> {
        let sql = "SELECT group_id, status, driver_bot, originator, record_status, \
                   group_kind, group_strategy, visibility \
                   FROM bcs_groups WHERE group_id = ? AND env = ?";
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                sql,
                vec![
                    DbValue::from(group_id),
                    DbValue::from(self.env.as_str()),
                ],
            )
            .await
            .map_err(|error| {
                bcs_service_api::ServiceError::InternalError(format!(
                    "read Group '{group_id}' authorization snapshot: {error}"
                ))
            })?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };

        let id: String = db_get_column(row, "group_id").map_err(|error| {
            bcs_service_api::ServiceError::InternalError(format!(
                "decode Group '{group_id}' authorization snapshot group_id: {error}"
            ))
        })?;
        let status_str: String =
            db_get_column_opt(row, "status").ok().flatten().unwrap_or_default();
        let driver_bot: String = db_get_column(row, "driver_bot").map_err(|error| {
            bcs_service_api::ServiceError::InternalError(format!(
                "decode Group '{group_id}' authorization snapshot driver_bot: {error}"
            ))
        })?;
        let originator: Option<String> =
            db_get_column_opt(row, "originator").ok().flatten();
        let record_status: String = db_get_column_opt(row, "record_status")
            .ok()
            .flatten()
            .unwrap_or_else(|| "active".to_string());
        let group_kind_str: Option<String> =
            db_get_column_opt(row, "group_kind").ok().flatten();
        let group_strategy_str: Option<String> =
            db_get_column_opt(row, "group_strategy").ok().flatten();
        let visibility: String = db_get_column_opt(row, "visibility")
            .ok()
            .flatten()
            .unwrap_or_else(|| "private".to_string());

        // Same participants decode as `load_group_from_mysql`:
        // legacy missing mode/scope decode with the existing
        // persistence rules.
        let participants = self.load_participants_from_mysql(group_id).await?;

        Ok(Some(GroupAuthorizationSnapshot {
            env: self.env.clone(),
            id,
            // `provisioning` rows are not live resources yet;
            // deleted rows are physically gone and project to None.
            live: record_status == "active",
            status: Self::str_to_status(&status_str),
            record_status,
            visibility,
            group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
            group_strategy: Self::parse_group_strategy(group_strategy_str.as_deref()),
            driver_actor_id: driver_bot.clone(),
            originator_actor_id: originator.unwrap_or(driver_bot),
            participants: participants
                .iter()
                .map(participant_snapshot)
                .collect(),
        }))
    }
}
