use super::*;

fn control_plane_record_from_row(row: &DbRow) -> ServiceResult<BotControlPlaneRecord> {
    let bot_id: String = db_get_column(row, "bot_uuid")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let name: String = db_get_column(row, "name")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let env: String = db_get_column(row, "env")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let visibility = db_get_column_opt::<String>(row, "visibility")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "protected".to_string());
    let kind = match db_get_column_opt::<String>(row, "actor_kind")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .as_deref()
    {
        Some("human") => bcs_service_api::ActorKind::Human,
        _ => bcs_service_api::ActorKind::Bot,
    };
    let status = match db_get_column_opt::<String>(row, "status")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .as_deref()
    {
        Some("hidden") => bcs_service_api::ActorStatus::Hidden,
        _ => bcs_service_api::ActorStatus::Online,
    };
    let created_by = db_get_column_opt(row, "created_by")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let bot_info = db_get_column_opt::<String>(row, "bot_info")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .and_then(|value| serde_json::from_str::<BotInfo>(&value).ok())
        .unwrap_or_default();
    let agent_code = db_get_column_opt::<String>(row, "agent_code")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .or(bot_info.agent_code);
    let user_visibility = db_get_column_opt::<String>(row, "user_visibility")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .map(|value| {
            serde_json::from_value(serde_json::Value::String(value)).map_err(|error| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id,
                    column = "user_visibility",
                    "Invalid persisted Bot attribute"
                );
                ServiceError::InternalError(error.to_string())
            })
        })
        .transpose()?
        .unwrap_or_default();
    let friend_ext = db_get_column_opt::<String>(row, "friend_ext")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .map(|value| {
            serde_json::from_str(&value).map_err(|error| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id,
                    column = "friend_ext",
                    "Invalid persisted Bot attribute"
                );
                ServiceError::InternalError(error.to_string())
            })
        })
        .transpose()?
        .unwrap_or_default();
    let friend_check_in_strategy = db_get_column_opt::<String>(row, "friend_check_in_strategy")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .map(|value| {
            serde_json::from_value(serde_json::Value::String(value)).map_err(|error| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id,
                    column = "friend_check_in_strategy",
                    "Invalid persisted Bot attribute"
                );
                ServiceError::InternalError(error.to_string())
            })
        })
        .transpose()?
        .unwrap_or_default();
    let task_claim_mode = db_get_column_opt::<i64>(row, "task_claim_mode")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .map(|value| value != 0)
        .unwrap_or(false);
    let task_dream_mode = db_get_column_opt::<i64>(row, "task_dream_mode")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .map(|value| value != 0)
        .unwrap_or(false);
    let created_at = db_get_column::<i64>(row, "gmt_create_ms")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .max(0) as u64;
    let updated_at = db_get_column::<i64>(row, "gmt_modified_ms")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .max(0) as u64;

    Ok(BotControlPlaneRecord {
        bot_id,
        kind,
        name,
        visibility,
        status,
        env,
        created_by,
        descriptor: BotControlPlaneDescriptor {
            summary: bot_info.summary.unwrap_or_default(),
            domains: bot_info.domains,
            skills: bot_info.skills,
            scopes: bot_info.scopes,
        },
        agent_code,
        task_claim_mode,
        task_dream_mode,
        created_at,
        updated_at,
        user_visibility,
        friend_ext,
        friend_check_in_strategy,
    })
}

#[async_trait]
impl BotControlPlaneRepoPort for PersistentBotRepo {
    async fn get_control_plane(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        let sql = format!(
            "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, \
                    created_by, agent_code, task_claim_mode, task_dream_mode, user_visibility, \
                    friend_ext, friend_check_in_strategy, ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms \
             FROM bcs_bots \
             WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 \
             LIMIT 1",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        );
        let rows = self
            .db_query(&sql, vec![Value::from(bot_id), Value::from(env)])
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        rows.first().map(control_plane_record_from_row).transpose()
    }

    async fn list_control_plane_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        let name = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let name_filter = name.unwrap_or_default().to_lowercase();
        let visibility_filter = match query.visibility {
            BotCandidateVisibility::Discovery => "b.visibility IN ('public', 'protected')",
            BotCandidateVisibility::Collaboration => {
                "b.visibility = 'public' OR f.bot_uuid IS NOT NULL"
            }
        };
        let mut friend_ids = query.friend_ids.iter().cloned().collect::<Vec<_>>();
        friend_ids.sort_unstable();
        let friend_rows = if friend_ids.is_empty() {
            "SELECT NULL AS bot_uuid WHERE 1 = 0".to_string()
        } else {
            friend_ids
                .iter()
                .enumerate()
                .map(|(index, _)| {
                    if index == 0 {
                        "SELECT ? AS bot_uuid"
                    } else {
                        "SELECT ?"
                    }
                })
                .collect::<Vec<_>>()
                .join(" UNION ALL ")
        };
        let common = format!("WITH friend_uuids AS ({friend_rows}) ");
        let page_sql = format!(
            "{common}\
             SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.status, \
                    b.actor_kind, b.env, b.created_by, b.agent_code, \
                    b.task_claim_mode, b.task_dream_mode, b.user_visibility, b.friend_ext, \
                    b.friend_check_in_strategy, \
                    ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms, \
                    CASE WHEN f.bot_uuid IS NULL THEN 0 ELSE 1 END AS is_friend \
             FROM bcs_bots b \
             LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
             WHERE b.env = ? AND COALESCE(b.is_deleted, 0) = 0 \
               AND COALESCE(b.actor_kind, 'bot') = 'bot' \
               AND b.bot_uuid != ? AND INSTR(LOWER(b.name), ?) > 0 \
               AND ({visibility_filter}) \
             ORDER BY b.gmt_create DESC, b.bot_uuid ASC \
             LIMIT ? OFFSET ?",
            self.flavor.unix_ts("b.gmt_create"),
            self.flavor.unix_ts("b.gmt_modified"),
        );
        let mut base_params = friend_ids
            .iter()
            .map(|friend_id| Value::from(friend_id.as_str()))
            .collect::<Vec<_>>();
        base_params.extend([
            Value::from(query.env.as_str()),
            Value::from(query.acting_bot_id.as_str()),
            Value::from(name_filter.as_str()),
        ]);
        let mut page_params = base_params.clone();
        page_params.push(Value::from(query.limit as i64));
        page_params.push(Value::from(query.offset as i64));
        let rows = self
            .db_query(&page_sql, page_params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let mut records = Vec::with_capacity(rows.len());
        for row in &rows {
            let is_friend = db_get_column::<i64>(row, "is_friend")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                != 0;
            records.push(BotCandidateReadRecord {
                bot: control_plane_record_from_row(row)?,
                is_friend,
            });
        }

        let count_sql = format!(
            "{common}\
             SELECT COUNT(*) AS total \
             FROM bcs_bots b \
             LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
             WHERE b.env = ? AND COALESCE(b.is_deleted, 0) = 0 \
               AND COALESCE(b.actor_kind, 'bot') = 'bot' \
               AND b.bot_uuid != ? AND INSTR(LOWER(b.name), ?) > 0 \
               AND ({visibility_filter})"
        );
        let count_rows = self
            .db_query(&count_sql, base_params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let total = count_rows
            .first()
            .map(|row| db_get_column::<i64>(row, "total"))
            .transpose()
            .map_err(|error| ServiceError::InternalError(error.to_string()))?
            .unwrap_or(0) as u64;
        Ok((records, total))
    }

    async fn search_control_plane_candidates(
        &self,
        query: BotSearchCandidateQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        let search_text = query
            .q
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .or_else(|| {
                query
                    .name
                    .as_deref()
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
            })
            .map(str::to_lowercase);
        let bot_uuid_filter = match query.bot_uuids.as_ref() {
            Some(values) if values.is_empty() => return Ok((Vec::new(), 0)),
            Some(values) => {
                let mut values = values
                    .iter()
                    .map(|value| value.trim().to_string())
                    .collect::<Vec<_>>();
                values.sort_unstable();
                values.dedup();
                Some(values)
            }
            None => None,
        };
        let visibility_filter = match query.visibility_filter.as_ref() {
            Some(values) if values.is_empty() => return Ok((Vec::new(), 0)),
            Some(values) => Some(
                values
                    .iter()
                    .map(|value| value.trim().to_lowercase())
                    .collect::<Vec<_>>(),
            ),
            None => None,
        };
        let user_visibility_filter = match query.user_visibility.as_ref() {
            Some(values) if values.is_empty() => return Ok((Vec::new(), 0)),
            Some(values) => Some(
                values
                    .iter()
                    .map(|value| value.trim().to_lowercase())
                    .collect::<Vec<_>>(),
            ),
            None => None,
        };
        let friendship = query.friendship.unwrap_or_default();
        if matches!(friendship, BotSearchFriendshipFilter::Friends)
            && query.friend_ids.is_empty()
        {
            return Ok((Vec::new(), 0));
        }
        let mut friend_ids = query.friend_ids.iter().cloned().collect::<Vec<_>>();
        friend_ids.sort_unstable();
        friend_ids.dedup();
        let friend_rows = if friend_ids.is_empty() {
            "SELECT NULL AS bot_uuid WHERE 1 = 0".to_string()
        } else {
            friend_ids
                .iter()
                .enumerate()
                .map(|(index, _)| {
                    if index == 0 {
                        "SELECT ? AS bot_uuid"
                    } else {
                        "SELECT ?"
                    }
                })
                .collect::<Vec<_>>()
                .join(" UNION ALL ")
        };
        let common = format!("WITH friend_uuids AS ({friend_rows}) ");
        let mut predicates = vec![
            "b.env = ?".to_string(),
            "COALESCE(b.is_deleted, 0) = 0".to_string(),
            "COALESCE(b.actor_kind, 'bot') = 'bot'".to_string(),
            "b.bot_uuid != ?".to_string(),
        ];
        let mut params = friend_ids
            .iter()
            .map(|friend_id| Value::from(friend_id.as_str()))
            .collect::<Vec<_>>();
        params.push(Value::from(query.env.as_str()));
        params.push(Value::from(query.acting_bot_id.as_str()));
        if let Some(values) = bot_uuid_filter.as_ref() {
            predicates.push(format!(
                "b.bot_uuid IN ({})",
                vec!["?"; values.len()].join(", ")
            ));
            params.extend(values.iter().map(|value| Value::from(value.as_str())));
        }
        if let Some(search_text) = search_text.as_ref() {
            predicates.push(
                "(INSTR(LOWER(COALESCE(b.name, '')), ?) > 0 OR INSTR(LOWER(COALESCE(b.bot_info, '')), ?) > 0 OR INSTR(LOWER(b.bot_uuid), ?) > 0)"
                    .to_string(),
            );
            params.push(Value::from(search_text.as_str()));
            params.push(Value::from(search_text.as_str()));
            params.push(Value::from(search_text.as_str()));
        }
        if let Some(values) = visibility_filter.as_ref() {
            predicates.push(format!(
                "b.visibility IN ({})",
                vec!["?"; values.len()].join(", ")
            ));
            params.extend(values.iter().map(|value| Value::from(value.as_str())));
        } else {
            match query.visibility {
                BotCandidateVisibility::Discovery => {
                    predicates.push("b.visibility IN ('public', 'protected')".to_string());
                }
                BotCandidateVisibility::Collaboration => {
                    predicates.push("(b.visibility = 'public' OR f.bot_uuid IS NOT NULL)".to_string());
                }
            }
        }
        if let Some(values) = user_visibility_filter.as_ref() {
            predicates.push(format!(
                "b.user_visibility IN ({})",
                vec!["?"; values.len()].join(", ")
            ));
            params.extend(values.iter().map(|value| Value::from(value.as_str())));
        }
        match friendship {
            BotSearchFriendshipFilter::All => {}
            BotSearchFriendshipFilter::Friends => {
                predicates.push("f.bot_uuid IS NOT NULL".to_string());
            }
            BotSearchFriendshipFilter::NonFriends => {
                predicates.push("f.bot_uuid IS NULL".to_string());
            }
        }
        if let Some(want_tc) = query.tc_bot {
            if want_tc {
                predicates.push(
                    "b.created_by IS NOT NULL AND INSTR(b.bot_uuid, ':') > 0 AND SUBSTR(b.bot_uuid, INSTR(b.bot_uuid, ':') + 1) = b.created_by"
                        .to_string(),
                );
            } else {
                predicates.push(
                    "(b.created_by IS NULL OR INSTR(b.bot_uuid, ':') = 0 OR SUBSTR(b.bot_uuid, INSTR(b.bot_uuid, ':') + 1) != b.created_by)"
                        .to_string(),
                );
            }
        }
        let where_clause = predicates.join(" AND ");
        let page_sql = format!(
            "{common}\
             SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.status, \
                    b.actor_kind, b.env, b.created_by, b.agent_code, \
                    b.task_claim_mode, b.task_dream_mode, b.user_visibility, b.friend_ext, \
                    b.friend_check_in_strategy, \
                    ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms, \
                    CASE WHEN f.bot_uuid IS NULL THEN 0 ELSE 1 END AS is_friend \
             FROM bcs_bots b \
             LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
             WHERE {where_clause} \
             ORDER BY b.gmt_create DESC, b.bot_uuid ASC \
             LIMIT ? OFFSET ?",
            self.flavor.unix_ts("b.gmt_create"),
            self.flavor.unix_ts("b.gmt_modified"),
        );
        let mut page_params = params.clone();
        page_params.push(Value::from(query.limit as i64));
        page_params.push(Value::from(query.offset as i64));
        let rows = self
            .db_query(&page_sql, page_params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let mut records = Vec::with_capacity(rows.len());
        for row in &rows {
            let is_friend = db_get_column::<i64>(row, "is_friend")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                != 0;
            records.push(BotCandidateReadRecord {
                bot: control_plane_record_from_row(row)?,
                is_friend,
            });
        }

        let count_sql = format!(
            "{common}\
             SELECT COUNT(*) AS total \
             FROM bcs_bots b \
             LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
             WHERE {where_clause}"
        );
        let count_rows = self
            .db_query(&count_sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let total = count_rows
            .first()
            .and_then(|row| row.get("total"))
            .and_then(|value| value.as_i64())
            .unwrap_or_default()
            .max(0) as u64;
        Ok((records, total))
    }

    async fn list_control_plane_by_creator(

        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let mut sql = format!(
            "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, \
                    created_by, agent_code, task_claim_mode, task_dream_mode, user_visibility, \
                    friend_ext, friend_check_in_strategy, ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms \
             FROM bcs_bots \
             WHERE created_by = ? AND env = ? AND COALESCE(is_deleted, 0) = 0",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        );
        let mut params = vec![
            Value::from(query.created_by.as_str()),
            Value::from(query.env.as_str()),
        ];
        if let Some(kind) = query.kind {
            sql.push_str(" AND COALESCE(actor_kind, 'bot') = ?");
            params.push(Value::from(match kind {
                bcs_service_api::ActorKind::Bot => "bot",
                bcs_service_api::ActorKind::Human => "human",
            }));
        }
        if let Some(name) = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            sql.push_str(" AND INSTR(LOWER(name), ?) > 0");
            params.push(Value::from(name.to_lowercase()));
        }
        if let Some(status) = query.status {
            sql.push_str(" AND status = ?");
            params.push(Value::from(match status {
                bcs_service_api::ActorStatus::Online => "online",
                bcs_service_api::ActorStatus::Hidden => "hidden",
            }));
        }
        sql.push_str(" ORDER BY gmt_create DESC, bot_uuid ASC");
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        rows.iter().map(control_plane_record_from_row).collect()
    }

    async fn list_control_plane_by_task_modes(
        &self,
        query: BotTaskModesQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let mut sql = format!(
            "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, \
                    created_by, agent_code, task_claim_mode, task_dream_mode, user_visibility, \
                    friend_ext, friend_check_in_strategy, \
                    ({}) * 1000 AS gmt_create_ms, ({}) * 1000 AS gmt_modified_ms \
             FROM bcs_bots \
             WHERE env = ? AND COALESCE(is_deleted, 0) = 0 \
             AND COALESCE(actor_kind, 'bot') = 'bot'",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        );
        let mut params = vec![Value::from(query.env.as_str())];
        match (query.task_claim_mode, query.task_dream_mode, query.match_mode) {
            (Some(claim), Some(dream), TaskModeMatch::All) => {
                sql.push_str(" AND task_claim_mode = ? AND task_dream_mode = ?");
                params.push(Value::from(if claim { 1 } else { 0 }));
                params.push(Value::from(if dream { 1 } else { 0 }));
            }
            (Some(claim), Some(dream), TaskModeMatch::Any) => {
                sql.push_str(" AND (task_claim_mode = ? OR task_dream_mode = ?)");
                params.push(Value::from(if claim { 1 } else { 0 }));
                params.push(Value::from(if dream { 1 } else { 0 }));
            }
            (Some(claim), None, _) => {
                sql.push_str(" AND task_claim_mode = ?");
                params.push(Value::from(if claim { 1 } else { 0 }));
            }
            (None, Some(dream), _) => {
                sql.push_str(" AND task_dream_mode = ?");
                params.push(Value::from(if dream { 1 } else { 0 }));
            }
            (None, None, _) => {}
        }
        if let Some(visibility) = query.visibility.as_deref().map(str::trim).filter(|v| !v.is_empty()) {
            sql.push_str(" AND visibility = ?");
            params.push(Value::from(visibility));
        }
        if let Some(status) = query.status {
            sql.push_str(" AND status = ?");
            params.push(Value::from(match status {
                bcs_service_api::ActorStatus::Online => "online",
                bcs_service_api::ActorStatus::Hidden => "hidden",
            }));
        }
        if let Some(user_visibility) = query.user_visibility {
            sql.push_str(" AND user_visibility = ?");
            params.push(Value::from(match user_visibility {
                bcs_service_api::application::v1::UserVisibility::Public => "public",
                bcs_service_api::application::v1::UserVisibility::Protected => "protected",
                bcs_service_api::application::v1::UserVisibility::Private => "private",
            }));
        }
        sql.push_str(" ORDER BY gmt_create DESC, bot_uuid ASC");
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        rows.iter().map(control_plane_record_from_row).collect()
    }

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        let _identity = self.identity_locks.lock(bot_id).await;
        if patch.user_visibility.is_some()
            || patch.friend_ext.is_some()
            || patch.friend_check_in_strategy.is_some()
        {
            debug!(
                bot_id = %bot_id,
                has_user_visibility = patch.user_visibility.is_some(),
                has_friend_ext = patch.friend_ext.is_some(),
                has_friend_check_in_strategy = patch.friend_check_in_strategy.is_some(),
                "Patching internal Bot attributes in persistent storage"
            );
        }
        let existing = self.get_control_plane(bot_id, env).await?;
        if existing.is_none() {
            return Ok(None);
        }

        let mut assignments = Vec::new();
        let mut params = Vec::new();
        if let Some(name) = patch.name.as_deref() {
            assignments.push("name = ?".to_string());
            params.push(Value::from(name));
        }
        if let Some(visibility) = patch.visibility.as_deref() {
            assignments.push("visibility = ?".to_string());
            params.push(Value::from(visibility));
        }
        if let Some(status) = patch.status {
            assignments.push("status = ?".to_string());
            params.push(Value::from(match status {
                bcs_service_api::ActorStatus::Online => "online",
                bcs_service_api::ActorStatus::Hidden => "hidden",
            }));
        }
        if let Some(task_claim_mode) = patch.task_claim_mode {
            assignments.push("task_claim_mode = ?".to_string());
            params.push(Value::from(if task_claim_mode { 1 } else { 0 }));
        }
        if let Some(task_dream_mode) = patch.task_dream_mode {
            assignments.push("task_dream_mode = ?".to_string());
            params.push(Value::from(if task_dream_mode { 1 } else { 0 }));
        }
        if let Some(user_visibility) = patch.user_visibility {
            assignments.push("user_visibility = ?".to_string());
            params.push(Value::from(serialized_string_value(&user_visibility)?));
        }
        if let Some(friend_ext) = patch.friend_ext.as_ref() {
            assignments.push("friend_ext = json_extract(?, '$')".to_string());
            params.push(Value::from(serde_json::to_string(friend_ext)?));
        }
        if let Some(friend_check_in_strategy) = patch.friend_check_in_strategy {
            assignments.push("friend_check_in_strategy = ?".to_string());
            params.push(Value::from(serialized_string_value(
                &friend_check_in_strategy,
            )?));
        }
        if patch.descriptor.is_some() {
            let mut json_updates = Vec::new();
            if let Some(descriptor) = patch.descriptor.as_ref() {
                if let Some(summary) = descriptor.summary.as_ref() {
                    json_updates.push(("$.summary", serde_json::to_value(summary)?));
                }
                if let Some(domains) = descriptor.domains.as_ref() {
                    json_updates.push(("$.domains", serde_json::to_value(domains)?));
                }
                if let Some(skills) = descriptor.skills.as_ref() {
                    json_updates.push(("$.skills", serde_json::to_value(skills)?));
                }
                if let Some(scopes) = descriptor.scopes.as_ref() {
                    json_updates.push(("$.scopes", serde_json::to_value(scopes)?));
                }
            }
            if !json_updates.is_empty() {
                let (assignment, json_params) = bot_info_json_set(json_updates)?;
                assignments.push(assignment);
                params.extend(json_params);
            }
        }

        assignments.push("gmt_modified = CURRENT_TIMESTAMP".to_string());
        assignments.push("updated_at = CURRENT_TIMESTAMP".to_string());
        params.push(Value::from(bot_id));
        params.push(Value::from(env));
        let sql = format!(
            "UPDATE bcs_bots SET {} WHERE bot_uuid = ? AND env = ? \
             AND COALESCE(is_deleted, 0) = 0",
            assignments.join(", ")
        );
        let affected = self
            .db_execute_affected(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        if affected == 0 && self.get_control_plane(bot_id, env).await?.is_none() {
            return Ok(None);
        }

        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
            if let Some(name) = patch.name {
                bot.capabilities.name = Some(name);
            }
            if let Some(visibility) = patch.visibility {
                bot.capabilities.visibility = visibility;
            }
            if let Some(status) = patch.status {
                bot.status = status;
            }
            if let Some(descriptor) = patch.descriptor {
                if let Some(summary) = descriptor.summary {
                    bot.capabilities.summary = Some(summary);
                }
                if let Some(domains) = descriptor.domains {
                    bot.capabilities.domains = domains;
                }
                if let Some(skills) = descriptor.skills {
                    bot.capabilities.skills = skills;
                }
                if let Some(scopes) = descriptor.scopes {
                    bot.capabilities.scopes = scopes;
                }
            }
        }

        self.get_control_plane(bot_id, env).await
    }
}
