//! Single-group and bulk load paths for the MySQL-backed Group store.

use super::*;
use std::collections::HashSet;

impl MySqlGroupStore {

    /// Load session from MySQL.
    pub(crate) async fn load_group_from_mysql(&self, group_id: &str) -> ServiceResult<Option<Group>> {
        // Task G.2 / migration 005: read group_kind + dm_pair_key from DB so
        // dm groups round-trip through `get()` without losing their identity.
        let sql = format!(
            "SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
             service_group_uuid, service_mode, version, record_status, \
             {} AS created_ts, {} AS updated_ts, \
             group_kind, dm_pair_key, group_strategy, visibility, human_mention_notify_mode \
             FROM bcs_groups WHERE group_id = ? AND env = ?",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        );

        let rows = self
            .db
            .query_with(
                &self.logical_db,
                &sql,
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!("load Group '{group_id}': {error}"))
            })?;

        if let Some(row) = rows.first() {
            let id: String = db_get_column(row, "group_id").map_err(|error| {
                ServiceError::InternalError(format!("load Group group_id: {error}"))
            })?;
            let driver_bot: String = db_get_column(row, "driver_bot").map_err(|error| {
                ServiceError::InternalError(format!("load Group driver_bot: {error}"))
            })?;
            let opening_message = Self::opening_message_from_row(row, group_id)?;
            let participants = self.load_participants_from_mysql(group_id).await?;

            let notify_mode_raw: Option<String> =
                db_get_column_opt(row, "human_mention_notify_mode").map_err(|error| {
                    ServiceError::InternalError(format!(
                        "load Group '{group_id}' human_mention_notify_mode: {error}"
                    ))
                })?;
            let human_mention_notify_mode =
                Self::parse_human_mention_notify_mode(notify_mode_raw.as_deref())?;

            return Ok(Some(store_projection::group_from_row(
                row,
                store_projection::GROUP_COLUMNS,
                id,
                driver_bot,
                opening_message,
                human_mention_notify_mode,
                participants,
            )));
        }

        Ok(None)
    }

    /// Load participants from MySQL.
    pub(crate) async fn load_participants_from_mysql(
        &self,
        group_id: &str,
    ) -> ServiceResult<Vec<Participant>> {
        let sql = "SELECT bot_uuid, role, actor_kind, mode, tags_json, message_view_scope FROM bcs_group_participants \
             WHERE group_id = ? AND env = ?";

        let rows = self
            .db
            .query_with(
                &self.logical_db,
                sql,
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "load participants for Group '{group_id}': {error}"
                ))
            })?;

        rows.iter()
            .map(|row| {
                let decode = |column: &str, error: DbError| {
                    ServiceError::InternalError(format!(
                        "decode participant column '{column}' for Group '{group_id}': {error}"
                    ))
                };
                let bot_uuid: String =
                    db_get_column(row, "bot_uuid").map_err(|error| decode("bot_uuid", error))?;
                let role_str: String =
                    db_get_column(row, "role").map_err(|error| decode("role", error))?;
                let actor_kind_str: Option<String> = db_get_column_opt(row, "actor_kind")
                    .map_err(|error| decode("actor_kind", error))?;
                let mode_str: Option<String> =
                    db_get_column_opt(row, "mode").map_err(|error| decode("mode", error))?;
                let tags_json: Option<String> = db_get_column_opt(row, "tags_json")
                    .map_err(|error| decode("tags_json", error))?;
                let scope_raw: Option<String> = db_get_column_opt(row, "message_view_scope")
                    .map_err(|error| decode("message_view_scope", error))?;
                let (actor_kind, mode) = Self::normalize_kind_mode(
                    group_id,
                    &bot_uuid,
                    self.env.as_str(),
                    actor_kind_str.as_deref(),
                    mode_str.as_deref(),
                );
                let message_view_scope = Self::parse_message_view_scope(scope_raw.as_deref())?;
                if !message_view_scope.is_valid_for(actor_kind) {
                    return Err(ServiceError::InternalError(format!(
                        "Bot participant '{bot_uuid}' has invalid participant message scope"
                    )));
                }

                Ok(Participant {
                    bot_uuid,
                    bot_name: None,
                    kind: Some(ParticipantKind::Bot),
                    role: Self::str_to_role(&role_str),
                    actor_kind,
                    mode: Some(mode),
                    tags: Self::deserialize_participant_tags(tags_json),
                    message_view_scope,
                })
            })
            .collect()
    }

    /// Load all sessions from MySQL.
    pub(crate) async fn load_all_groups_from_mysql(&self) -> Vec<Group> {
        // Fixed-shape JOIN: one prepared statement regardless of data volume.
        // Task G.2: also project gs.group_kind / gs.dm_pair_key so the `list()`
        // result reflects the persisted dm identity (otherwise dm groups would
        // collapse to GroupKind::Normal in memory after a server restart).
        let _start = std::time::Instant::now();
        let sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                    gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                    {} AS created_ts, {} AS updated_ts, \
                    gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM bcs_groups gs \
             LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ? \
             WHERE gs.env = ?",
            self.flavor.unix_ts("gs.gmt_create"),
            self.flavor.unix_ts("gs.gmt_modified"),
        );
        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                &sql,
                vec![
                    Value::from(self.env.as_str()),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
        {
            Ok(r) => {
                let elapsed = _start.elapsed();
                if elapsed.as_millis() > 100 {
                    warn!(duration_ms = %elapsed.as_millis(), rows = r.len(), "slow load_all_groups_from_mysql");
                } else {
                    info!(duration_ms = %elapsed.as_millis(), rows = r.len(), "load_all_groups_from_mysql");
                }
                r
            }
            Err(e) => {
                warn!(duration_ms = %_start.elapsed().as_millis(), error = %e, "load_all_groups_from_mysql failed");
                return Vec::new();
            }
        };

        let mut groups_map: HashMap<String, (Group, HashSet<String>)> = HashMap::new();
        for row in &rows {
            let group_id: String = match db_get_column(row, "group_id") {
                Ok(v) => v,
                Err(_) => continue,
            };
            let opening_message = match Self::opening_message_from_row(row, &group_id) {
                Ok(value) => value,
                Err(error) => {
                    error!(%group_id, %error, "Failed to load Group opening_message_json");
                    return Vec::new();
                }
            };
            store_projection::insert_join_row(
                &mut groups_map,
                row,
                store_projection::GROUP_COLUMNS,
                self.env.as_str(),
                group_id,
                opening_message,
            );
        }
        store_projection::into_sorted_groups(groups_map)
    }

    /// Read the Group human-mention notify policy with one scoped SELECT on
    /// the primary datasource. This never consults the ordinary Group cache,
    /// never loads participants, and never reads a replica or stale snapshot,
    /// so a second Store's just-committed row is always observed here.
    pub(crate) async fn read_human_notify_policy_sql(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<GroupHumanNotifyPolicy>> {
        let sql = "SELECT human_mention_notify_mode, driver_bot \
                   FROM bcs_groups \
                   WHERE group_id = ? AND env = ?";
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                sql,
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "read Group '{group_id}' human-notify policy: {error}"
                ))
            })?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let raw: Option<String> = db_get_column_opt(row, "human_mention_notify_mode").map_err(
            |error| {
                ServiceError::InternalError(format!(
                    "decode Group '{group_id}' human_mention_notify_mode: {error}"
                ))
            },
        )?;
        let mode = Self::parse_human_mention_notify_mode(raw.as_deref())?;
        let driver_bot: String = db_get_column(row, "driver_bot").map_err(|error| {
            ServiceError::InternalError(format!(
                "decode Group '{group_id}' driver_bot: {error}"
            ))
        })?;
        Ok(Some(GroupHumanNotifyPolicy {
            mode,
            driver_bot_id: driver_bot,
        }))
    }
}
