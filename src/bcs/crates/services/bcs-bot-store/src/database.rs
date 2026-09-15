use super::*;

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

        let name = caps.name.as_deref().unwrap_or(bot_uuid);

        // Dedicated `agent_code` column (transition: dual-written alongside the
        // `bot_info` JSON above). None maps to SQL NULL.
        let agent_code_value = match caps.agent_code.as_deref() {
            Some(code) => Value::from(code),
            None => Value::Null,
        };

        // Check if record exists
        let exists = self.exists_in_db(bot_uuid).await;

        // Normalize: empty visibility defaults to "private" before persisting
        let visibility = if caps.visibility.is_empty() {
            "private"
        } else {
            &caps.visibility
        };

        let affected = if exists {
            // UPDATE existing record — created_by is NOT updated here;
            // use update_created_by_in_db() / save_created_by() for that.
            // Only overwrite session_token when we have a value; None means
            // the caller doesn't know the token — preserve whatever is in DB.
            // Update only lifecycle-owned JSON fields so private control-plane
            // attributes and forward-compatible keys remain authoritative.
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
            if let Some(token) = session_token {
                let sql = format!(
                    "UPDATE bcs_bots SET name = ?, {bot_info_assignment}, session_token = ?, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?"
                );
                let mut params = vec![Value::from(name)];
                params.extend(bot_info_params);
                params.extend([
                    Value::from(token),
                    Value::from(visibility),
                    agent_code_value.clone(),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ]);
                self.db_execute_affected(&sql, params).await
            } else {
                let sql = format!(
                    "UPDATE bcs_bots SET name = ?, {bot_info_assignment}, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?"
                );
                let mut params = vec![Value::from(name)];
                params.extend(bot_info_params);
                params.extend([
                    Value::from(visibility),
                    agent_code_value.clone(),
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
            let sql = "INSERT INTO bcs_bots (bot_uuid, name, bot_info, session_token, created_by, visibility, status, actor_kind, agent_code, is_deleted, env, registered_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
            self.db_execute_affected(sql, vec![
                Value::from(bot_uuid),
                Value::from(name),
                Value::from(bot_info_json.as_str()),
                match session_token {
                    Some(t) => Value::from(t),
                    None => Value::Null,
                },
                match created_by.as_deref() {
                    Some(cb) => Value::from(cb),
                    None => Value::Null,
                },
                Value::from(visibility),
                Value::from("online"),
                Value::from("bot"),
                agent_code_value.clone(),
                Value::from(0_i64),
                Value::from(env.as_str()),
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

        if let Some(row) = rows.first() {
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

            return Ok(Some((
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
            )));
        }

        Ok(None)
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

    /// Read the `bot_info` JSON column for a given bot, parsed as a JSON object.
    /// Returns `None` if the row is missing, the column is NULL, or JSON parsing fails.
    pub(super) async fn read_bot_info_json(&self, bot_uuid: &str) -> Option<serde_json::Value> {
        let env = resolve_env();
        let sql = "SELECT bot_info FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 LIMIT 1";
        let rows = self
            .db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .ok()?;
        let row = rows.first()?;
        let bot_info_str: String = db_get_column_opt(row, "bot_info").ok().flatten()?;
        serde_json::from_str(&bot_info_str).ok()
    }

    /// Check if bot exists in the configured database.
    pub(super) async fn exists_in_db(&self, bot_uuid: &str) -> bool {
        let env = resolve_env();
        let sql = "SELECT 1 FROM bcs_bots WHERE bot_uuid = ? AND env = ? LIMIT 1";
        self.db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map(|r| !r.is_empty())
            .unwrap_or(false)
    }

    /// Save session token to the configured database.
    pub(super) async fn save_token_to_db(&self, bot_uuid: &str, token: &str) -> ServiceResult<()> {
        let env = resolve_env();

        let sql = "UPDATE bcs_bots SET session_token = ? WHERE bot_uuid = ? AND env = ?";

        info!(bot_uuid = %bot_uuid, env = %env, "save_token_to_db: executing update");

        let affected = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(token),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_uuid = %bot_uuid, error = %e, "save_token_to_db: failed");
                ServiceError::InternalError(e.to_string())
            })?;

        info!(bot_uuid = %bot_uuid, affected_rows = affected, "save_token_to_db: update completed");

        Ok(())
    }

    /// Update created_by in the configured database.
    /// - `overwrite=false`: only if currently NULL (first writer wins, original behavior)
    /// - `overwrite=true`: unconditional update (last writer wins)
    pub(super) async fn update_created_by_in_db(
        &self,
        bot_uuid: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        let env = resolve_env();
        let sql = if overwrite {
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ?"
        } else {
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ? AND created_by IS NULL"
        };

        let affected = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(created_by),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_uuid = %bot_uuid, error = %e, "update_created_by_in_db: failed");
                ServiceError::InternalError(e.to_string())
            })?;

        info!(bot_uuid = %bot_uuid, created_by = %created_by, affected_rows = affected, "update_created_by_in_db: completed");

        Ok(())
    }

    /// Query bots by creator from the configured database.
    ///
    /// H.3: SELECT now includes `status` and `actor_kind`; `caps.hidden` is
    /// derived from `status == 'hidden'` (legacy `bot_info.hidden` ignored).
    pub(super) async fn list_bots_by_creator_from_db(
        &self,
        created_by: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let sql = "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, created_by FROM bcs_bots WHERE created_by = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = self
            .db_query(sql, vec![Value::from(created_by), Value::from(env)])
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;

        Ok(rows
            .iter()
            .filter_map(|row| {
                let bot_uuid: String = db_get_column_opt(row, "bot_uuid").ok().flatten()?;
                let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
                let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "protected".to_string());
                let created_by: Option<String> =
                    db_get_column_opt(row, "created_by").ok().flatten();
                let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                    .ok()
                    .flatten()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();

                // H.3: derive hidden from bcs_bots.status (column may be absent
                // on un-migrated rows → default to "online" → hidden=false).
                let status_str: String = db_get_column_opt(row, "status")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "online".to_string());
                let hidden = status_str == "hidden";
                let status = match status_str.as_str() {
                    "hidden" => bcs_service_api::ActorStatus::Hidden,
                    _ => bcs_service_api::ActorStatus::Online,
                };

                let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "bot".to_string());
                let actor_kind = match actor_kind_str.as_str() {
                    "human" => bcs_service_api::ActorKind::Human,
                    _ => bcs_service_api::ActorKind::Bot,
                };

                Some(RegisteredBot {
                    bot_uuid,
                    capabilities: BotCapabilities {
                        name,
                        summary: bot_info.summary,
                        domains: bot_info.domains,
                        skills: bot_info.skills,
                        scopes: bot_info.scopes,
                        binding_channels: bot_info.binding_channels,
                        hidden,
                        visibility,
                        // SECURITY: 敏感字段置空，防止通过常规接口泄露
                        agent_code: None,
                        agent_token: None,
                    },
                    env,
                    created_by,
                    actor_kind,
                    status,
                })
            })
            .collect())
    }

    /// Load session token from the configured database.
    pub(super) async fn load_token_from_db(&self, bot_uuid: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT session_token FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = self
            .db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .ok()?;

        if let Some(row) = rows.first() {
            return db_get_column_opt(row, "session_token").ok().flatten();
        }

        None
    }

    /// Find bot by token in the configured database (indexed lookup).
    pub(super) async fn find_bot_by_token_in_db(&self, token: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid FROM bcs_bots WHERE session_token = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        info!(token = %token, env = %env, "find_bot_by_token_in_db: executing query");

        let rows = match self
            .db_query(sql, vec![Value::from(token), Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, token = %token, env = %env, error = %e, "find_bot_by_token_in_db: database query failed");
                return None;
            }
        };

        info!(token = %token, rows_count = rows.len(), "find_bot_by_token_in_db: query completed");

        if let Some(row) = rows.first() {
            match db_get_column::<String>(row, "bot_uuid") {
                Ok(bot_uuid) => {
                    info!(token = %token, bot_uuid = %bot_uuid, "find_bot_by_token_in_db: found bot");
                    return Some(bot_uuid);
                }
                Err(e) => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, token = %token, error = %e, "find_bot_by_token_in_db: failed to get bot_uuid");
                    return None;
                }
            }
        }

        warn!(request_id = %bcs_observability::CurrentRequestId, token = %token, "find_bot_by_token_in_db: no bot found");
        None
    }

    /// Resolve a bot by its dedicated `agent_code` column via the indexed
    /// lookup (`idx_agent_code`). Returns the first matching `bot_uuid`.
    pub(super) async fn find_bot_by_agent_code_in_db(&self, agent_code: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid FROM bcs_bots WHERE agent_code = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = match self
            .db_query(sql, vec![Value::from(agent_code), Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(e) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, agent_code = %agent_code, env = %env, error = %e, "find_bot_by_agent_code_in_db: query failed");
                return None;
            }
        };

        if let Some(row) = rows.first() {
            match db_get_column::<String>(row, "bot_uuid") {
                Ok(bot_uuid) => {
                    info!(agent_code = %agent_code, bot_uuid = %bot_uuid, "find_bot_by_agent_code_in_db: found bot");
                    return Some(bot_uuid);
                }
                Err(e) => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, agent_code = %agent_code, error = %e, "find_bot_by_agent_code_in_db: failed to get bot_uuid");
                    return None;
                }
            }
        }

        warn!(request_id = %bcs_observability::CurrentRequestId, agent_code = %agent_code, "find_bot_by_agent_code_in_db: no bot found");
        None
    }

    /// Soft-delete bot from the configured database.
    pub(super) async fn soft_delete_in_db(&self, bot_uuid: &str) -> bool {
        let env = resolve_env();
        let sql = "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        self.db_execute_affected(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map(|n| n > 0)
            .unwrap_or(false)
    }
}
