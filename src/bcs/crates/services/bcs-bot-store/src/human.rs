use super::*;

impl PersistentBotRepo {
    /// Ensure a Human Actor row exists in `bcs_bots` — Task O.3.
    ///
    /// Idempotent INSERT IGNORE: if `bot_uuid = "human_{staff_no}"` already
    /// exists, nothing is done (in particular, `name` is preserved per
    /// Requirement 3.1#4).
    ///
    /// On first INSERT, writes:
    /// - `bot_uuid = "human_{staff_no}"`
    /// - `actor_kind = 'human'`
    /// - `status = 'online'`
    /// - `visibility = 'protected'`
    /// - `name = nick_name`
    /// - `session_token = UUID v4`
    /// - `created_by = staff_no`
    pub(super) async fn repo_ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        let env = resolve_env();
        let bot_uuid = format!("human_{}", staff_no);
        let default_summary = "写点什么介绍自己";

        // Fast path: already present → backfill missing fields, then return.
        if self.exists_in_db(&bot_uuid).await {
            // Deserialize current bot_info into BotInfo struct (missing fields get defaults).
            let existing_json = self
                .read_bot_info_json(&bot_uuid)
                .await
                .unwrap_or_else(|| serde_json::json!({}));
            let mut bot_info: BotInfo =
                serde_json::from_value(existing_json.clone()).unwrap_or_default();

            // Check if summary needs backfill.
            let needs_summary = bot_info.summary.as_deref().map_or(true, |s| s.is_empty());
            if needs_summary {
                bot_info.summary = Some(default_summary.to_string());
            }

            // Re-serialize and compare to detect if anything actually changed.
            let merged_str = serde_json::to_string(&bot_info).unwrap_or_default();
            let needs_backfill = merged_str != existing_json.to_string();

            if needs_backfill {
                let sql = "UPDATE bcs_bots SET bot_info = ? WHERE bot_uuid = ? AND env = ?";
                self.db_execute_affected(
                    sql,
                    vec![
                        Value::from(merged_str.as_str()),
                        Value::from(bot_uuid.as_str()),
                        Value::from(env.as_str()),
                    ],
                )
                .await
                .map_err(|e| {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        bot_uuid = %bot_uuid,
                        error = %e,
                        "ensure_human_actor: failed to backfill bot_info"
                    );
                    ServiceError::InternalError(format!(
                        "ensure_human_actor: failed to backfill bot_info for {}: {}",
                        bot_uuid, e
                    ))
                })?;

                // Sync in-memory cache so subsequent reads (e.g. /bots/query)
                // reflect the updated fields without waiting for a full reload.
                {
                    let mut bots = self.bots.write().await;
                    if let Some(bot) = bots.get_mut(&bot_uuid) {
                        if needs_summary {
                            bot.capabilities.summary = Some(default_summary.to_string());
                        }
                    }
                }

                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor: backfilled missing bot_info fields"
                );
            } else {
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor: row already exists, preserving existing fields"
                );
            }
            return Ok(bcs_service_api::EnsureHumanResult { created: false });
        }

        let session_token = uuid::Uuid::new_v4().to_string();
        let initial_bot_info = BotInfo {
            summary: Some(default_summary.to_string()),
            ..Default::default()
        };
        let bot_info_str = serde_json::to_string(&initial_bot_info).unwrap_or_default();
        let visibility = "protected";
        let actor_kind = "human";
        let status = "online";

        let sql = format!(
            "{} INTO bcs_bots \
             (bot_uuid, actor_kind, name, bot_info, session_token, created_by, visibility, status, env, registered_at, updated_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            self.flavor.insert_or_ignore()
        );
        let affected = self
            .db_execute_affected(
                &sql,
                vec![
                    Value::from(bot_uuid.as_str()),
                    Value::from(actor_kind),
                    Value::from(nick_name),
                    Value::from(bot_info_str.as_str()),
                    Value::from(session_token.as_str()),
                    Value::from(staff_no),
                    Value::from(visibility),
                    Value::from(status),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_uuid = %bot_uuid,
                    error = %e,
                    "ensure_human_actor: INSERT IGNORE failed"
                );
                ServiceError::InternalError(e.to_string())
            })?;

        let created = affected > 0;
        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            nick_name = %nick_name,
            env = %env,
            created = %created,
            "ensure_human_actor: INSERT IGNORE completed"
        );
        Ok(bcs_service_api::EnsureHumanResult { created })
    }

    pub(super) async fn repo_list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        // Rule (a): bots with `created_by = staff_no`
        // Rule (b): bots with `created_by IS NULL` whose `bot_uuid` ends with `:{staff_no}`
        //           (filtered in Rust by `is_legacy_namespace` whitelist)
        let like_pattern = format!("%:{}", staff_no);
        let sql = "SELECT bot_uuid, actor_kind, name, bot_info, session_token, \
                   visibility, status, created_by \
                   FROM bcs_bots \
                   WHERE env = ? AND actor_kind = 'bot' AND ( \
                       created_by = ? \
                       OR (created_by IS NULL AND bot_uuid LIKE ?) \
                   ) AND COALESCE(is_deleted, 0) = 0 \
                   ORDER BY gmt_create DESC";

        let rows = self
            .db_query(
                sql,
                vec![
                    Value::from(env),
                    Value::from(staff_no),
                    Value::from(like_pattern.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    staff_no = %staff_no,
                    env = %env,
                    error = %e,
                    "list_legacy_bots_for_owner: query failed"
                );
                ServiceError::InternalError(e.to_string())
            })?;

        let results: Vec<RegisteredBot> = rows
            .iter()
            .filter_map(|row| {
                let bot_uuid: String = db_get_column_opt(row, "bot_uuid").ok().flatten()?;
                let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "bot".to_string());
                if actor_kind_str != "bot" {
                    return None;
                }
                let created_by: Option<String> =
                    db_get_column_opt(row, "created_by").ok().flatten();

                // For rule (b): apply whitelist filter
                if created_by.is_none() && !is_legacy_namespace(&bot_uuid, staff_no) {
                    return None;
                }

                let actor_kind = bcs_service_api::ActorKind::Bot;
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
                let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                    .ok()
                    .flatten()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();
                let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

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
                    env: Some(env.to_string()),
                    created_by,
                    actor_kind,
                    status,
                })
            })
            .collect();

        info!(
            staff_no = %staff_no,
            env = %env,
            count = results.len(),
            "list_legacy_bots_for_owner: query completed"
        );
        Ok(results)
    }

    /// Repair a Human actor's `name` column — used by the `/debug/whoami`
    /// debug endpoint to backfill the real `nick_name` after onboard fell
    /// back to writing `staff_no` (because the auth SDK didn't return
    /// `nick_name` at the time).
    pub(super) async fn repo_update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        let env = resolve_env();
        let bot_uuid = format!("human_{}", staff_no);

        let sql = "UPDATE bcs_bots SET name = ? WHERE bot_uuid = ? AND env = ?";
        self.db_execute_affected(
            sql,
            vec![
                Value::from(new_name),
                Value::from(bot_uuid.as_str()),
                Value::from(env.as_str()),
            ],
        )
        .await
        .map_err(|e| {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                bot_uuid = %bot_uuid,
                error = %e,
                "update_human_name: failed to update database"
            );
            ServiceError::InternalError(e.to_string())
        })?;

        // Update in-memory mirror if the row is cached.
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(&bot_uuid) {
                existing.capabilities.name = Some(new_name.to_string());
            }
        }

        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            new_name = %new_name,
            env = %env,
            "update_human_name: human actor name updated"
        );
        Ok(())
    }
}
