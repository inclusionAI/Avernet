//! `read_authorization_snapshot` — strict Session authorization-fact
//! reads (bot-authority plan Task 6.4, spec §8.3 权威资源读取).
//!
//! Semantics shared by both stores:
//!
//! - SQL reads the MASTER data source directly (`MySqlSessionStore`
//!   keeps no display cache; the projection still queries the
//!   instance-bound `env` row set), and any storage or decode
//!   failure is `Err` — never a silent `Ok(None)`.
//! - Memory projects from the current in-memory state.
//! - 历史 mode/scope/join-seq 缺省按既有持久化解码规则投影
//!   (`Participant` serde defaults + `effective_mode()`).
//! - Snapshots contain no message bodies, credentials, or full
//!   configurations (spec §8.3).
//!
//! Env is bound at instance level: SQL filters `env = ?`; Memory
//! resolves the process env per call (the same
//! `bcs_config::resolve_env_str` pattern the authority Memory
//! projection in `bcs-bot-store` uses).

use bcs_db_api::{db_get_column, db_get_column_opt, DbRow, DbStatement, DbValue};
use bcs_service_api::types::authorization_snapshot::{
    ParticipantAuthorizationSnapshot, SessionAuthorizationSnapshot,
};
use bcs_service_api::ServiceResult;
use bcs_service_api::Participant;

use super::mysql::{parse_session_kind, parse_status, participants_from_json};

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

fn decode_participants(
    session_id: &str,
    raw: Option<String>,
) -> ServiceResult<Vec<ParticipantAuthorizationSnapshot>> {
    let raw = raw.filter(|raw| !raw.is_empty());
    let Some(raw) = raw else {
        return Ok(Vec::new());
    };
    participants_from_json(&raw)
        .map(|participants| {
            participants
                .iter()
                .map(participant_snapshot)
                .collect()
        })
        .map_err(|error| {
            bcs_service_api::ServiceError::InternalError(format!(
                "decode Session '{session_id}' authorization snapshot \
                 participants: {error}"
            ))
        })
}

pub(super) fn memory_snapshot(
    participant_env: String,
    session: &bcs_service_api::Session,
) -> SessionAuthorizationSnapshot {
    SessionAuthorizationSnapshot {
        env: participant_env,
        id: session.id.clone(),
        live: true,
        group_id: session.group_id.clone(),
        status: session.status,
        session_kind: session.session_kind,
        created_by: session.created_by.clone(),
        message_visibility_version: session.message_visibility_version,
        participant_join_seq: session.participant_join_seq.clone(),
        participants: session
            .participants
            .iter()
            .map(participant_snapshot)
            .collect(),
    }
}

/// Decode one master-source Session row into the snapshot.
fn decode_snapshot(
    env: &str,
    row: &DbRow,
) -> ServiceResult<SessionAuthorizationSnapshot> {
    let session_id: String = db_get_column(row, "session_id")
        .map_err(|error| {
            bcs_service_api::ServiceError::InternalError(format!(
                "decode Session authorization snapshot session_id: {error}"
            ))
        })?;
    let id = session_id.as_str();
    let group_id: String = db_get_column(row, "group_id").map_err(|error| {
        bcs_service_api::ServiceError::InternalError(format!(
            "decode Session '{id}' authorization snapshot group_id: {error}"
        ))
    })?;
    let status_raw: String = db_get_column_opt(row, "status")
        .ok()
        .flatten()
        .unwrap_or_else(|| "running".to_string());
    let status = parse_status(&status_raw).map_err(|error| {
        bcs_service_api::ServiceError::InternalError(format!(
            "decode Session '{id}' authorization snapshot status: {error}"
        ))
    })?;
    let kind_raw: String = db_get_column_opt(row, "session_kind")
        .ok()
        .flatten()
        .unwrap_or_else(|| "chat".to_string());
    let session_kind = parse_session_kind(&kind_raw).map_err(|error| {
        bcs_service_api::ServiceError::InternalError(format!(
            "decode Session '{id}' authorization snapshot session_kind: {error}"
        ))
    })?;
    let created_by = db_get_column_opt(row, "created_by").ok().flatten();
    // `0` is the legacy visibility; a NULL column decodes to 0
    // (the existing persistence decode rule).
    let version_value: i64 = db_get_column_opt(row, "message_visibility_version")
        .ok()
        .flatten()
        .unwrap_or(0);
    let message_visibility_version = u8::try_from(version_value)
        .ok()
        .filter(|version| *version <= 1)
        .ok_or_else(|| {
            bcs_service_api::ServiceError::InternalError(format!(
                "decode Session '{id}' authorization snapshot \
                 message_visibility_version: invalid"
            ))
        })?;
    let participant_join_seq = match db_get_column_opt::<String>(row, "participant_join_seq")
        .map_err(|error| {
            bcs_service_api::ServiceError::InternalError(format!(
                "decode Session '{id}' authorization snapshot \
                 participant_join_seq: {error}"
            ))
        })? {
        None => None,
        Some(raw) if raw.is_empty() => None,
        Some(raw) => {
            match serde_json::from_str::<serde_json::Value>(&raw) {
                Ok(value) => Some(value).filter(|value| !value.is_null()),
                Err(error) => {
                    return Err(bcs_service_api::ServiceError::InternalError(
                        format!(
                            "decode Session '{id}' authorization snapshot \
                             participant_join_seq: {error}"
                        ),
                    ))
                }
            }
        }
    };
    let participants_raw = db_get_column_opt(row, "participants").ok().flatten();

    Ok(SessionAuthorizationSnapshot {
        env: env.to_string(),
        id: session_id.clone(),
        live: true,
        group_id,
        status,
        session_kind,
        created_by,
        message_visibility_version,
        participant_join_seq,
        participants: decode_participants(id, participants_raw)?,
    })
}

impl super::MySqlSessionStore {
    /// Strict master-source projection of one Session's
    /// authorization facts. `Ok(None)` only when the Session row
    /// (in this instance env) does not exist.
    pub(crate) async fn read_authorization_snapshot_sql(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<SessionAuthorizationSnapshot>> {
        let sql = "SELECT session_id, group_id, status, session_kind, created_by, \
                   message_visibility_version, participant_join_seq, participants \
                   FROM bcs_group_sessions WHERE env = ? AND session_id = ? LIMIT 1";
        let rows = self
            .db
            .query(DbStatement::with_params(
                sql,
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|error| {
                bcs_service_api::ServiceError::InternalError(format!(
                    "read Session '{session_id}' authorization snapshot: {error}"
                ))
            })?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        decode_snapshot(&self.env, &row).map(Some)
    }
}
