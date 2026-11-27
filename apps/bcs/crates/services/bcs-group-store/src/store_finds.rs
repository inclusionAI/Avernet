//! Participant-scoped Group lookup queries for the MySQL-backed Group store.

use super::*;
use std::collections::HashSet;

impl MySqlGroupStore {

    /// Find all groups where the given bot is a participant.
    /// Uses a single JOIN query instead of 3 serial queries to reduce cursor usage.
    pub(crate) async fn find_by_participant_sql(&self, bot_uuid: &str) -> Vec<Group> {
        info!(
            bot_uuid = %bot_uuid,
            env = %self.env,
            logical_db = %self.logical_db,
            "find_by_participant: starting query"
        );

        // Single JOIN query: find groups + details + all participants in one shot.
        // Task G.2: also project group_kind / dm_pair_key for dm group tagging.
        let sql = format!(
            "SELECT \
                gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                gp2.bot_uuid AS p_bot_uuid, gp2.role AS p_role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                {} AS created_ts, {} AS updated_ts, \
                gp2.actor_kind AS p_actor_kind, gp2.mode AS p_mode, gp2.tags_json AS p_tags_json, gp2.message_view_scope AS p_message_view_scope, \
                gs.group_kind AS g_group_kind, gs.dm_pair_key AS g_dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM bcs_group_participants gp \
             JOIN bcs_groups gs ON gp.group_id = gs.group_id AND gs.env = ? \
             JOIN bcs_group_participants gp2 ON gs.group_id = gp2.group_id AND gp2.env = ? \
             WHERE gp.bot_uuid = ? AND gp.env = ?",
            self.flavor.unix_ts("gs.gmt_create"),
            self.flavor.unix_ts("gs.gmt_modified"),
        );

        info!(
            bot_uuid = %bot_uuid,
            "find_by_participant: executing query"
        );

        let env = self.env.as_str();
        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                &sql,
                vec![
                    Value::from(env),
                    Value::from(env),
                    Value::from(bot_uuid),
                    Value::from(env),
                ],
            )
            .await
        {
            Ok(r) => {
                info!(
                    row_count = r.len(),
                    "find_by_participant: query returned rows"
                );
                r
            }
            Err(e) => {
                warn!(error = %e, "find_by_participant: query failed");
                return Vec::new();
            }
        };

        // Aggregate flat rows into Groups by group_id
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
                store_projection::PARTICIPANT_FIND_ALIASED_COLUMNS,
                self.env.as_str(),
                group_id,
                opening_message,
            );
        }

        let result = store_projection::into_sorted_groups(groups_map);

        info!(
            bot_uuid = %bot_uuid,
            result_count = result.len(),
            "find_by_participant: completed"
        );

        result
    }

    pub(crate) async fn try_find_by_participant_sql(&self, bot_uuid: &str) -> ServiceResult<Vec<Group>> {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT DISTINCT group_id FROM bcs_group_participants \
                 WHERE bot_uuid = ? AND env = ?",
                vec![Value::from(bot_uuid), Value::from(self.env.as_str())],
            )
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "find Groups for participant '{bot_uuid}': {error}"
                ))
            })?;
        let mut groups = Vec::with_capacity(rows.len());
        for row in rows {
            let group_id: String = db_get_column(&row, "group_id").map_err(|error| {
                ServiceError::InternalError(format!(
                    "find Groups for participant group_id: {error}"
                ))
            })?;
            if let Some(group) = self.try_get(&group_id).await? {
                groups.push(group);
            }
        }
        Group::sort_by_updated_at_desc(&mut groups);
        Ok(groups)
    }

    pub(crate) async fn find_by_participant_filtered_sql(
        &self,
        bot_uuid: &str,
        kind: Option<bcs_service_api::GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<Group> {
        info!(
            bot_uuid = %bot_uuid,
            env = %self.env,
            has_group_kind = kind.is_some(),
            has_label_query = label_query.map(str::trim).is_some_and(|q| !q.is_empty()),
            "find_by_participant_filtered: starting query"
        );

        let mut sql = format!(
            "SELECT \
                gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                gp2.bot_uuid AS p_bot_uuid, gp2.role AS p_role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                {} AS created_ts, {} AS updated_ts, \
                gp2.actor_kind AS p_actor_kind, gp2.mode AS p_mode, gp2.tags_json AS p_tags_json, gp2.message_view_scope AS p_message_view_scope, \
                gs.group_kind AS g_group_kind, gs.dm_pair_key AS g_dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM bcs_group_participants gp \
             JOIN bcs_groups gs ON gp.group_id = gs.group_id AND gs.env = ? \
             JOIN bcs_group_participants gp2 ON gs.group_id = gp2.group_id AND gp2.env = ? \
             WHERE gp.bot_uuid = ? AND gp.env = ?",
            self.flavor.unix_ts("gs.gmt_create"),
            self.flavor.unix_ts("gs.gmt_modified"),
        );

        let env = self.env.as_str();
        let mut params = vec![
            Value::from(env),
            Value::from(env),
            Value::from(bot_uuid),
            Value::from(env),
        ];
        if let Some(kind) = kind {
            sql.push_str(" AND gs.group_kind = ?");
            params.push(Value::from(Self::group_kind_to_str(kind)));
        }
        if let Some(query) = label_query.map(str::trim).filter(|q| !q.is_empty()) {
            sql.push_str(" AND LOWER(COALESCE(gs.label, '')) LIKE ?");
            params.push(Value::from(format!("%{}%", query.to_lowercase())));
        }

        let rows = match self.db.query_with(&self.logical_db, &sql, params).await {
            Ok(r) => {
                info!(
                    row_count = r.len(),
                    "find_by_participant_filtered: query returned rows"
                );
                r
            }
            Err(e) => {
                warn!(error = %e, "find_by_participant_filtered: query failed");
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
                store_projection::PARTICIPANT_FIND_ALIASED_COLUMNS,
                self.env.as_str(),
                group_id,
                opening_message,
            );
        }

        let result = store_projection::into_sorted_groups(groups_map);

        info!(
            bot_uuid = %bot_uuid,
            result_count = result.len(),
            "find_by_participant_filtered: completed"
        );

        result
    }

    /// Find groups by participant with pagination.
    pub(crate) async fn find_by_participant_paginated_sql(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        debug!(
            "find_by_participant_paginated: bot_uuid={} limit={} offset={}",
            bot_uuid, limit, offset
        );

        // Subquery paginates groups first, then JOIN fetches all participants.
        // LIMIT/OFFSET on the outer JOIN would paginate rows (not groups) due to fan-out.
        // Task G.2: project group_kind / dm_pair_key from both inner DISTINCT
        // and outer SELECT so dm groups remain tagged through pagination.
        let participant_paginated_sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp2.bot_uuid, gp2.role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                    gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp2.actor_kind, gp2.mode, gp2.tags_json, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM (SELECT DISTINCT g.group_id, g.label, g.status, g.driver_bot, g.originator, g.routing_policy_json, g.context, g.opening_message_json, \
                          g.service_group_uuid, g.service_mode, g.version, g.record_status, \
                          {} AS created_ts, {} AS updated_ts, \
                          g.group_kind, g.dm_pair_key, g.group_strategy, g.visibility, g.human_mention_notify_mode \
                   FROM bcs_groups g \
                   JOIN bcs_group_participants gp ON g.group_id = gp.group_id AND gp.env = ? \
                   WHERE gp.bot_uuid = ? AND g.env = ? \
                   ORDER BY updated_ts DESC, g.group_id ASC LIMIT ? OFFSET ?) gs \
             LEFT JOIN bcs_group_participants gp2 ON gs.group_id = gp2.group_id AND gp2.env = ?",
            self.flavor.unix_ts("g.gmt_create"),
            self.flavor.unix_ts("g.gmt_modified"),
        );
        let detail_rows = match self
            .db
            .query_with(
                &self.logical_db,
                &participant_paginated_sql,
                vec![
                    Value::from(self.env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(self.env.as_str()),
                    Value::from(limit as i64),
                    Value::from(offset as i64),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                error!(
                    "find_by_participant_paginated: failed to load group details for bot_uuid={}: {:?}",
                    bot_uuid, e
                );
                return Vec::new();
            }
        };

        // Aggregate flat rows into Groups by group_id
        let mut groups_map: HashMap<String, (Group, HashSet<String>)> = HashMap::new();
        for row in &detail_rows {
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
}
