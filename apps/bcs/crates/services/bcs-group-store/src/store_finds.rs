//! Participant-scoped Group lookup queries for the MySQL-backed Group store.

use super::*;

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
                gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
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
                    db_get_column_opt(row, "g_group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "g_dm_pair_key").ok().flatten();
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

            // Add participant (deduplicate by bot_uuid)
            if let (Ok(p_bot_uuid), Ok(p_role)) = (
                db_get_column::<String>(row, "p_bot_uuid"),
                db_get_column::<String>(row, "p_role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == p_bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "p_actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "p_mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &p_bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: p_bot_uuid.clone(),
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&p_role),
                        actor_kind,
                        mode: Some(mode),
                        tags: Self::deserialize_participant_tags(
                            db_get_column_opt(row, "p_tags_json").ok().flatten(),
                        ),
                        message_view_scope: Self::message_view_scope_from_row(
                            row,
                            "p_message_view_scope",
                            &entry.id,
                            &p_bot_uuid,
                            actor_kind,
                        ),
                    });
                }
            }
        }

        let mut result = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut result);

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
                gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
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
                    db_get_column_opt(row, "g_group_kind").ok().flatten();
                let dm_pair_key: Option<String> =
                    db_get_column_opt(row, "g_dm_pair_key").ok().flatten();
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

            if let (Ok(p_bot_uuid), Ok(p_role)) = (
                db_get_column::<String>(row, "p_bot_uuid"),
                db_get_column::<String>(row, "p_role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == p_bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "p_actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "p_mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &p_bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: p_bot_uuid.clone(),
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&p_role),
                        actor_kind,
                        mode: Some(mode),
                        tags: Self::deserialize_participant_tags(
                            db_get_column_opt(row, "p_tags_json").ok().flatten(),
                        ),
                        message_view_scope: Self::message_view_scope_from_row(
                            row,
                            "p_message_view_scope",
                            &entry.id,
                            &p_bot_uuid,
                            actor_kind,
                        ),
                    });
                }
            }
        }

        let mut result = groups_map.into_values().collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut result);

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
                    gs.service_group_uuid, gs.service_mode, gs.service_spec, gs.version, gs.record_status, \
                    gs.created_ts, gs.updated_ts, \
                    gp2.actor_kind, gp2.mode, gp2.tags_json, gs.group_kind, gs.dm_pair_key, gs.group_strategy, gs.visibility, gs.human_mention_notify_mode \
             FROM (SELECT DISTINCT g.group_id, g.label, g.status, g.driver_bot, g.originator, g.routing_policy_json, g.context, g.opening_message_json, \
                          g.service_group_uuid, g.service_mode, g.service_spec, g.version, g.record_status, \
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
        let mut groups_map: HashMap<String, Group> = HashMap::new();
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
            if let (Ok(p_bot_uuid), Ok(p_role)) = (
                db_get_column::<String>(row, "bot_uuid"),
                db_get_column::<String>(row, "role"),
            ) {
                if !entry.participants.iter().any(|p| p.bot_uuid == p_bot_uuid) {
                    let actor_kind_str: Option<String> =
                        db_get_column_opt(row, "actor_kind").ok().flatten();
                    let mode_str: Option<String> = db_get_column_opt(row, "mode").ok().flatten();
                    let (actor_kind, mode) = Self::normalize_kind_mode(
                        &entry.id,
                        &p_bot_uuid,
                        self.env.as_str(),
                        actor_kind_str.as_deref(),
                        mode_str.as_deref(),
                    );
                    entry.participants.push(Participant {
                        bot_uuid: p_bot_uuid.clone(),
                        bot_name: None,
                        kind: Some(ParticipantKind::Bot),
                        role: Self::str_to_role(&p_role),
                        actor_kind,
                        mode: Some(mode),
                        tags: Self::deserialize_participant_tags(
                            db_get_column_opt(row, "tags_json").ok().flatten(),
                        ),
                        message_view_scope: Self::message_view_scope_from_row(
                            row,
                            "message_view_scope",
                            &entry.id,
                            &p_bot_uuid,
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
