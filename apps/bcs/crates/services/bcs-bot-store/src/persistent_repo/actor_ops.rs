//! Human-actor identity, registration, and credential operations for
//! `PersistentBotRepo`.
//!
//! Moved verbatim from the `BotRepoPort` trait impl during the bot-avatar
//! plan Task 1 file-size split; the trait impl keeps explicit delegations
//! to these `*_impl` inherent methods. The registration/credential cluster
//! mirrors `memory_identity_ops.rs` on the memory store.

use std::time::Instant;

use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{DbValue as Value, db_get_column_opt};
use bcs_service_api::{BotCapabilities, RegisteredBot, ServiceError, ServiceResult};
use bcs_service_api::port::repo::BotRepoPort;

use super::PersistentBotRepo;
use crate::{BotInfo, RegisteredBotInner, is_legacy_namespace};

impl PersistentBotRepo {

    pub(super) async fn register_impl(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        info!(bot_id = %bot_id, name = ?capabilities.name, "register: received registration request");

        // Sync binding channel index
        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        // Get token from memory for DB insert
        let session_token: Option<String> = {
            let bots = self.bots.read().await;
            bots.get(&bot_id).and_then(|b| b.session_token.clone())
        };

        // Save to the configured database (include token if available)
        self.save_to_db(&bot_id, &capabilities, session_token.as_deref(), None)
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, error = %e, "Failed to save bot to database during register");
                e
            })?;

        // Update memory
        let mut bots = self.bots.write().await;

        if let Some(existing) = bots.get_mut(&bot_id) {
            existing.last_heartbeat = Instant::now();
            if capabilities.name.is_some() {
                existing.capabilities.name = capabilities.name;
            }
            if capabilities.summary.is_some() {
                existing.capabilities.summary = capabilities.summary;
            }
            if !capabilities.domains.is_empty() {
                existing.capabilities.domains = capabilities.domains;
            }
            if !capabilities.skills.is_empty() {
                existing.capabilities.skills = capabilities.skills;
            }
            if !capabilities.scopes.is_empty() {
                existing.capabilities.scopes = capabilities.scopes;
            }
            if !capabilities.visibility.is_empty() {
                existing.capabilities.visibility = capabilities.visibility;
            }
            if capabilities.agent_code.is_some() {
                existing.capabilities.agent_code = capabilities.agent_code;
            }
            if capabilities.agent_token.is_some() {
                existing.capabilities.agent_token = capabilities.agent_token;
            }
            info!(bot_id = %bot_id, "register: updated existing bot");
        } else {
            // Normalize: empty visibility defaults to "private" for new bots
            let mut caps = capabilities;
            if caps.visibility.is_empty() {
                caps.visibility = "private".to_string();
            }
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_uuid: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: caps,
                    ws_connection: None,
                    protocol_version: 1,
                    session_token: None,
                    env: Some(resolve_env()),
                    hidden: false,
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                },
            );
            info!(bot_id = %bot_id, "register: inserted new bot");
        }

        Ok(())
    }

    pub(super) async fn register_with_owner_and_token_impl(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        info!(bot_id = %bot_id, name = ?capabilities.name, "register_with_owner_and_token: received registration request");

        self.save_to_db(&bot_id, &capabilities, Some(token), Some(created_by))
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, error = %e, "Failed to save bot to database during register_with_owner_and_token");
                e
            })?;

        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        let previous_token = {
            let mut bots = self.bots.write().await;

            if let Some(existing) = bots.get_mut(&bot_id) {
                existing.last_heartbeat = Instant::now();
                if capabilities.name.is_some() {
                    existing.capabilities.name = capabilities.name.clone();
                }
                if capabilities.summary.is_some() {
                    existing.capabilities.summary = capabilities.summary.clone();
                }
                if !capabilities.domains.is_empty() {
                    existing.capabilities.domains = capabilities.domains.clone();
                }
                if !capabilities.skills.is_empty() {
                    existing.capabilities.skills = capabilities.skills.clone();
                }
                if !capabilities.scopes.is_empty() {
                    existing.capabilities.scopes = capabilities.scopes.clone();
                }
                if !capabilities.visibility.is_empty() {
                    existing.capabilities.visibility = capabilities.visibility.clone();
                }
                if capabilities.agent_code.is_some() {
                    existing.capabilities.agent_code = capabilities.agent_code.clone();
                }
                if capabilities.agent_token.is_some() {
                    existing.capabilities.agent_token = capabilities.agent_token.clone();
                }
                if existing.created_by.is_none() {
                    existing.created_by = Some(created_by.to_string());
                }
                existing.session_token.replace(token.to_string())
            } else {
                let mut caps = capabilities;
                if caps.visibility.is_empty() {
                    caps.visibility = "private".to_string();
                }
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_uuid: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: caps,
                        ws_connection: None,
                        protocol_version: 1,
                        session_token: Some(token.to_string()),
                        env: Some(resolve_env()),
                        hidden: false,
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: Some(created_by.to_string()),
                    },
                );
                None
            }
        };

        let mut token_to_bot = self.token_to_bot.write().await;
        if let Some(previous_token) = previous_token.filter(|previous| previous != token) {
            token_to_bot.remove(&previous_token);
        }
        token_to_bot.insert(token.to_string(), bot_id.clone());

        info!(bot_id = %bot_id, "register_with_owner_and_token: completed");
        Ok(())
    }

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
    pub(super) async fn ensure_human_actor_impl(
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

    pub(super) async fn list_legacy_bots_for_owner_impl(
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
    pub(super) async fn update_human_name_impl(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
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

    pub(super) async fn save_created_by_impl(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        // Update in-memory (respects overwrite flag)
        {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                if overwrite || bot.created_by.is_none() {
                    bot.created_by = Some(created_by.to_string());
                }
            }
        }

        // Update in database (conditional based on overwrite)
        self.update_created_by_in_db(bot_id, created_by, overwrite)
            .await
    }

    pub(super) async fn save_token_impl(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.save_token_to_db(bot_id, token).await
    }

    pub(super) async fn load_token_impl(&self, bot_id: &str) -> Option<String> {
        // Check memory first
        {
            let bots = self.bots.read().await;
            if let Some(bot) = bots.get(bot_id) {
                if let Some(ref token) = bot.session_token {
                    return Some(token.clone());
                }
            }
        }

        // Load from database
        self.load_token_from_db(bot_id).await
    }

    pub(super) async fn find_bot_by_token_impl(&self, token: &str) -> Option<String> {
        // Check memory cache first (fast path)
        {
            let token_to_bot = self.token_to_bot.read().await;
            if let Some(bot_id) = token_to_bot.get(token) {
                let bot_id = bot_id.clone();
                drop(token_to_bot);
                if self.get(&bot_id).await.is_some() {
                    return Some(bot_id);
                }
                return None;
            }
        }

        // Check memory by iterating bots
        let memory_candidate = {
            let bots = self.bots.read().await;
            bots.iter()
                .find(|(_, bot)| bot.session_token.as_deref() == Some(token))
                .map(|(bot_id, _)| bot_id.clone())
        };
        if let Some(bot_id) = memory_candidate {
            if self.get(&bot_id).await.is_some() {
                return Some(bot_id);
            }
            return None;
        }

        // Fall back to database indexed lookup
        let result = self.find_bot_by_token_in_db(token).await;
        if result.is_none() {
            let prefix = &token[..8.min(token.len())];
            warn!(request_id = %bcs_observability::CurrentRequestId, token_prefix = %prefix, "find_bot_by_token: token not found in memory or database");
        }
        result
    }

    pub(super) async fn find_bot_by_agent_code_impl(&self, agent_code: &str) -> Option<String> {
        if agent_code.is_empty() {
            return None;
        }

        // Fast path: scan in-memory bots for a matching agent_code.
        let memory_candidate = {
            let bots = self.bots.read().await;
            bots.iter()
                .find(|(_, bot)| {
                    bot.capabilities.agent_code.as_deref() == Some(agent_code)
                })
                .map(|(bot_id, _)| bot_id.clone())
        };
        if let Some(bot_id) = memory_candidate {
            if self.get(&bot_id).await.is_some() {
                return Some(bot_id);
            }
            return None;
        }

        // Fall back to the database indexed lookup (idx_agent_code).
        self.find_bot_by_agent_code_in_db(agent_code).await
    }

    pub(super) async fn find_bot_by_binding_channel_impl(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        let index = self.binding_channel_index.read().await;
        index
            .get(&(channel.to_string(), binding_key.to_string()))
            .cloned()
    }
}
