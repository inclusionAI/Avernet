//! Group listing, pagination, and counting queries for the MySQL-backed Group store.

use super::*;

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
                            gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                            gs.created_ts, gs.updated_ts, \
                            gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
                     FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                                  service_group_uuid, service_mode, service_spec, version, record_status, \
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
                            gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                            gs.created_ts, gs.updated_ts, \
                            gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
                     FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                                  service_group_uuid, service_mode, service_spec, version, record_status, \
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
        let mut groups_map: HashMap<String, Group> = HashMap::new();
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
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id.clone(),
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    human_mention_notify_mode: Self::human_mention_notify_mode_from_row(row, &group_id),
                    context,
                    opening_message,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: bot_uuid.clone(),
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                        tags: Self::deserialize_participant_tags(
                            db_get_column_opt(row, "tags_json").ok().flatten(),
                        ),
                        message_view_scope: Self::message_view_scope_from_row(
                            row,
                            "message_view_scope",
                            &entry.id,
                            &bot_uuid,
                            actor_kind,
                        ),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
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
                              service_group_uuid, service_mode, service_spec, version, record_status, \
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
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
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

        let mut groups_map: HashMap<String, Group> = HashMap::new();
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
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id.clone(),
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    human_mention_notify_mode: Self::human_mention_notify_mode_from_row(row, &group_id),
                    context,
                    opening_message,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: bot_uuid.clone(),
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                        tags: Self::deserialize_participant_tags(
                            db_get_column_opt(row, "tags_json").ok().flatten(),
                        ),
                        message_view_scope: Self::message_view_scope_from_row(
                            row,
                            "message_view_scope",
                            &entry.id,
                            &bot_uuid,
                            actor_kind,
                        ),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
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
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp.actor_kind, gp.mode, gp.tags_json, gp.message_view_scope, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM (SELECT group_id, label, status, driver_bot, originator, routing_policy_json, context, opening_message_json, \
                          service_group_uuid, service_mode, service_spec, version, record_status, \
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

        let mut groups_map: HashMap<String, Group> = HashMap::new();
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
            let entry = groups_map.entry(group_id.clone()).or_insert_with(|| {
                let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
                let status_str: String = db_get_column(row, "status").unwrap_or_default();
                let driver_bot: String = db_get_column(row, "driver_bot").unwrap_or_default();
                let originator: Option<String> =
                    db_get_column_opt(row, "originator").ok().flatten();
                let routing_policy_json: Option<String> =
                    db_get_column_opt(row, "routing_policy_json").ok().flatten();
                let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
                let service_group_uuid: Option<String> =
                    db_get_column_opt(row, "service_group_uuid").ok().flatten();
                let service_mode: Option<String> =
                    db_get_column_opt(row, "service_mode").ok().flatten();
                let service_spec_json: Option<String> =
                    db_get_column_opt(row, "service_spec").ok().flatten();
                let service_spec: Option<bcs_service_api::ServiceSpec> =
                    match service_spec_json.as_deref() {
                        Some(s) if !s.is_empty() => serde_json::from_str(s).ok(),
                        _ => None,
                    };
                let version: i32 = db_get_column_opt::<i64>(row, "version")
                    .ok()
                    .flatten()
                    .unwrap_or(1) as i32;
                let record_status: String = db_get_column_opt(row, "record_status")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "active".to_string());
                let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
                let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
                let group_kind_str: Option<String> =
                    db_get_column_opt(row, "group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "dm_pair_key").ok().flatten();
                let group_strategy_str: Option<String> =
                    db_get_column_opt(row, "group_strategy").ok().flatten();
                let group_strategy = Self::parse_group_strategy(group_strategy_str.as_deref());
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

                Group {
                    id: group_id.clone(),
                    label,
                    status: Self::str_to_status(&status_str),
                    driver_bot,
                    originator,
                    routing_policy: Self::deserialize_routing_policy(routing_policy_json),
                    human_mention_notify_mode: Self::human_mention_notify_mode_from_row(row, &group_id),
                    context,
                    opening_message,
                    participants: Vec::new(),
                    messages: Vec::new(),
                    workspace: Workspace::default(),
                    service_group_uuid,
                    service_mode,
                    created_at: Self::seconds_to_millis(created_ts),
                    updated_at: Self::seconds_to_millis(updated_ts),
                    group_kind: Self::parse_group_kind(group_kind_str.as_deref()),
                    dm_pair_key,
                    group_strategy,
                    service_spec,
                    version,
                    record_status,
                    visibility,
                }
            });
            if let (Ok(bot_uuid), Ok(role_str)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: bot_uuid.clone(),
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&role_str),
                        actor_kind,
                        mode: Some(mode),
                        tags: Self::deserialize_participant_tags(
                            db_get_column_opt(row, "tags_json").ok().flatten(),
                        ),
                        message_view_scope: Self::message_view_scope_from_row(
                            row,
                            "message_view_scope",
                            &entry.id,
                            &bot_uuid,
                            actor_kind,
                        ),
                    });
                }
            }
        }
        let mut groups = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
    }
}
