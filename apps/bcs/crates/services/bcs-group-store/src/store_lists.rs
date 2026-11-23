//! Group listing, pagination, and counting queries for the MySQL-backed Group store.

use super::*;
use std::collections::HashSet;

impl MySqlGroupStore {

    /// Count all groups.
    pub(crate) async fn count_sql(&self) -> u64 {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ?",
                vec![Value::from(self.env.as_str())],
            )
            .await
            .unwrap_or_default();
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    /// CR-4: count groups optionally filtered by `group_kind`.
    ///
    /// Pushes the filter down to a `SELECT COUNT(*)` so callers paging
    /// through `kind=dm` see a `total` consistent with their page contents
    /// (the previous default in-memory filter returned the all-kinds total
    /// which made the X-of-Y display lie for filtered queries).
    pub(crate) async fn count_by_kind_sql(&self, kind: Option<bcs_service_api::GroupKind>) -> u64 {
        let rows =
            match kind {
                None => self
                    .db
                    .query_with(
                        &self.logical_db,
                        "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ?",
                        vec![Value::from(self.env.as_str())],
                    )
                    .await
                    .unwrap_or_default(),
                Some(k) => {
                    let kind_str = Self::group_kind_to_str(k);
                    self.db.query_with(
                    &self.logical_db,
                    "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ? AND group_kind = ?",
                    vec![Value::from(self.env.as_str()), Value::from(kind_str)],
                ).await.unwrap_or_default()
                }
            };
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    /// CR-4: paginate groups optionally filtered by `group_kind`.
    ///
    /// Pushes the filter into the inner subquery (which is what `LIMIT` /
    /// `OFFSET` apply to), so callers paging through `kind=dm` get a stable
    /// page of dm groups regardless of how many normal groups precede them
    /// in scan order. The legacy in-memory post-filter could return a
    /// short or empty page even when more matching rows existed further
    /// in the table.
    pub(crate) async fn list_paginated_by_kind_sql(
        &self,
        kind: Option<bcs_service_api::GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        // We share the same SELECT shape as `list_paginated` (subquery
        // paginates groups → outer JOIN fetches participants) but conditionally
        // append `AND group_kind = ?` to the inner WHERE. This keeps the
        // same row → Group reduction logic below.
        let created_ts_expr = self.flavor.unix_ts("gmt_create");
        let updated_ts_expr = self.flavor.unix_ts("gmt_modified");
        let rows_result = match kind {
            None => {
                let sql = format!(
                    "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                            gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                            gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                            gs.created_ts, gs.updated_ts, \
                            gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
                     FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                                  service_group_uuid, service_mode, version, record_status, \
                                  {} AS created_ts, {} AS updated_ts, \
                                  group_kind, dm_pair_key, group_strategy, visibility, human_mention_notify_mode \
                           FROM bcs_groups WHERE env = ? LIMIT ? OFFSET ?) gs \
                     LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
                    created_ts_expr, updated_ts_expr,
                );
                self.db
                    .query_with(
                        &self.logical_db,
                        &sql,
                        vec![
                            Value::from(self.env.as_str()),
                            Value::from(limit as i64),
                            Value::from(offset as i64),
                            Value::from(self.env.as_str()),
                        ],
                    )
                    .await
            }
            Some(k) => {
                let kind_str = Self::group_kind_to_str(k);
                let sql = format!(
                    "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                            gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                            gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                            gs.created_ts, gs.updated_ts, \
                            gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
                     FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                                  service_group_uuid, service_mode, version, record_status, \
                                  {} AS created_ts, {} AS updated_ts, \
                                  group_kind, dm_pair_key, group_strategy, visibility, human_mention_notify_mode \
                           FROM bcs_groups WHERE env = ? AND group_kind = ? LIMIT ? OFFSET ?) gs \
                     LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
                    created_ts_expr, updated_ts_expr,
                );
                self.db
                    .query_with(
                        &self.logical_db,
                        &sql,
                        vec![
                            Value::from(self.env.as_str()),
                            Value::from(kind_str),
                            Value::from(limit as i64),
                            Value::from(offset as i64),
                            Value::from(self.env.as_str()),
                        ],
                    )
                    .await
            }
        };

        let rows = match rows_result {
            Ok(r) => r,
            Err(e) => {
                warn!(?kind, error = %e, "list_paginated_by_kind: query failed");
                return Vec::new();
            }
        };

        // Identical row → Group aggregation as `list_paginated`.
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

    /// Count groups where the given bot is a participant.
    pub(crate) async fn count_by_participant_sql(&self, bot_uuid: &str) -> u64 {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT COUNT(DISTINCT gs.group_id) as cnt \
             FROM bcs_groups gs \
             JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ? \
             WHERE gp.bot_uuid = ? AND gs.env = ?",
                vec![
                    Value::from(self.env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
            .unwrap_or_default();
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    pub(crate) async fn count_filtered_sql(
        &self,
        kind: Option<bcs_service_api::GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        let mut sql = "SELECT COUNT(*) as cnt FROM bcs_groups WHERE env = ?".to_string();
        let mut params: Vec<Value> = vec![Value::from(self.env.as_str())];

        if let Some(k) = kind {
            sql.push_str(" AND group_kind = ?");
            params.push(Value::from(Self::group_kind_to_str(k)));
        }
        if let Some(v) = visibility {
            sql.push_str(" AND visibility = ?");
            params.push(Value::from(v));
        }
        if let Some(l) = label.map(str::trim).filter(|l| !l.is_empty()) {
            sql.push_str(" AND LOWER(label) LIKE ?");
            let escaped = l
                .to_lowercase()
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_");
            params.push(Value::from(format!("%{}%", escaped)));
        }

        let rows = self
            .db
            .query_with(&self.logical_db, &sql, params)
            .await
            .unwrap_or_default();
        rows.first()
            .and_then(|row| db_get_column::<i64>(row, "cnt").ok())
            .unwrap_or(0) as u64
    }

    pub(crate) async fn list_paginated_filtered_sql(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<bcs_service_api::GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<Group> {
        let mut inner_sql = format!(
            "SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                              service_group_uuid, service_mode, version, record_status, \
                              {} AS created_ts, {} AS updated_ts, \
                              group_kind, dm_pair_key, group_strategy, visibility, human_mention_notify_mode \
                       FROM bcs_groups WHERE env = ?",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        );
        let mut params: Vec<Value> = vec![Value::from(self.env.as_str())];

        if let Some(k) = kind {
            inner_sql.push_str(" AND group_kind = ?");
            params.push(Value::from(Self::group_kind_to_str(k)));
        }
        if let Some(v) = visibility {
            inner_sql.push_str(" AND visibility = ?");
            params.push(Value::from(v));
        }
        if let Some(l) = label.map(str::trim).filter(|l| !l.is_empty()) {
            inner_sql.push_str(" AND LOWER(label) LIKE ?");
            let escaped = l
                .to_lowercase()
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_");
            params.push(Value::from(format!("%{}%", escaped)));
        }

        inner_sql.push_str(" ORDER BY gmt_modified DESC LIMIT ? OFFSET ?");
        params.push(Value::from(limit as i64));
        params.push(Value::from(offset as i64));

        let sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                    gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM ({}) gs \
             LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
            inner_sql
        );
        params.push(Value::from(self.env.as_str()));

        let rows = match self.db.query_with(&self.logical_db, &sql, params).await {
            Ok(r) => r,
            Err(e) => {
                warn!(error = %e, "list_paginated_filtered: query failed");
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

    /// List groups with pagination.
    pub(crate) async fn list_paginated_sql(&self, offset: u64, limit: u64) -> Vec<Group> {
        // Subquery paginates groups first, then JOIN fetches participants.
        // LIMIT/OFFSET on the outer JOIN would paginate rows (not groups) due to fan-out.
        // Task G.2: project group_kind / dm_pair_key from both inner and outer
        // SELECT so dm groups stay tagged after pagination.
        let _start = std::time::Instant::now();
        let paginated_sql = format!(
            "SELECT gs.group_id, gs.label, gs.status, gs.driver_bot, gs.originator, \
                    gp.bot_uuid, gp.role, gs.routing_policy_json, gs.context, gs.opening_message_json, \
                    gs.service_group_uuid, gs.service_mode, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                          service_group_uuid, service_mode, version, record_status, \
                          {} AS created_ts, {} AS updated_ts, \
                          group_kind, dm_pair_key, group_strategy, visibility, human_mention_notify_mode \
                   FROM bcs_groups WHERE env = ? LIMIT ? OFFSET ?) gs \
             LEFT JOIN bcs_group_participants gp ON gs.group_id = gp.group_id AND gp.env = ?",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified"),
        );
        let rows = match self
            .db
            .query_with(
                &self.logical_db,
                &paginated_sql,
                vec![
                    Value::from(self.env.as_str()),
                    Value::from(limit as i64),
                    Value::from(offset as i64),
                    Value::from(self.env.as_str()),
                ],
            )
            .await
        {
            Ok(r) => {
                let elapsed = _start.elapsed();
                if elapsed.as_millis() > 100 {
                    warn!(duration_ms = %elapsed.as_millis(), rows = r.len(), offset = offset, limit = limit, "slow list_paginated");
                } else {
                    info!(duration_ms = %elapsed.as_millis(), rows = r.len(), offset = offset, limit = limit, "list_paginated");
                }
                r
            }
            Err(e) => {
                warn!(duration_ms = %_start.elapsed().as_millis(), error = %e, "list_paginated: query failed");
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
}
