//! `BotRepoPort` implementation for [`MemoryBotRepo`]
//! (registration, discovery, lifecycle, storage), split out of the
//! former over-limit `memory.rs` (plan Task 3 memory split).
//!
//! Token persistence lives in `memory_token_storage.rs` and the
//! streaming-session bodies in `memory_streaming.rs`; their trait
//! methods here delegate one line each — moved bodies are otherwise
//! verbatim.

use super::*;

#[async_trait]
impl BotRepoPort for MemoryBotRepo {
    async fn try_load_token(&self, bot_id: &str) -> ServiceResult<Option<String>> {
        self.load_registration_token(bot_id).await
    }

    async fn create_registration_if_absent(
        &self, bot_id: String, capabilities: BotCapabilities, created_by: &str, token: &str,
    ) -> ServiceResult<bool> {
        self.create_registration_once(bot_id, capabilities, created_by, token).await
    }

    async fn create_registration_if_absent_with_initialization(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
        initialization: bcs_service_api::types::OwnershipInitialization,
    ) -> ServiceResult<bool> {
        self.create_registration_once_with_initialization(
            bot_id,
            capabilities,
            created_by,
            token,
            &initialization,
        )
        .await
    }

    async fn initialize_existing_ownership(
        &self,
        bot_id: &str,
        initialization: bcs_service_api::types::OwnershipInitialization,
    ) -> ServiceResult<bcs_service_api::types::OwnershipState> {
        self.initialize_existing_ownership_impl(bot_id, &initialization)
            .await
    }

    async fn retire_bot_lifecycle(
        &self,
        bot_id: &str,
        operation: bcs_service_api::types::BotOperationContext,
    ) -> ServiceResult<bool> {
        self.retire_bot_lifecycle_impl(bot_id, &operation).await
    }

    async fn delete_human_actor(
        &self,
        staff_no: &str,
        operation: bcs_service_api::types::BotOperationContext,
    ) -> ServiceResult<bool> {
        self.delete_human_actor_impl(staff_no, &operation).await
    }

    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(&bot_id);

        // Update binding channel index
        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        // Pre-read created_by from disk before acquiring lock
        let persisted = {
            let path = self.bot_info_path(&bot_id);
            if let Ok(content) = fs::read_to_string(&path).await {
                serde_json::from_str::<PersistedCapabilities>(&content).ok()
            } else {
                None
            }
        };
        let persisted_created_by = persisted.as_ref().and_then(|value| value.created_by.clone());
        let persisted_user_visibility = persisted
            .as_ref()
            .map(|value| value.user_visibility)
            .unwrap_or_default();
        let persisted_friend_ext = persisted
            .as_ref()
            .map(|value| value.friend_ext.clone())
            .unwrap_or_default();
        let persisted_friend_check_in_strategy = persisted
            .as_ref()
            .map(|value| value.friend_check_in_strategy)
            .unwrap_or_default();

        let mut bots = self.bots.write().await;

        if let Some(existing) = bots.get_mut(&bot_id) {
            // Update existing registration
            existing.last_heartbeat = Instant::now();
            // Merge capabilities, keeping non-empty values
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
            if capabilities.binding_channels.is_some() {
                existing.capabilities.binding_channels = capabilities.binding_channels;
            }
            if !capabilities.visibility.is_empty() {
                existing.capabilities.visibility = capabilities.visibility.clone();
            }
            // agent_code: 从请求中更新（允许设置或清除）
            if capabilities.agent_code.is_some() {
                existing.capabilities.agent_code = capabilities.agent_code;
            }
            // agent_token: 从请求中更新（允许设置）
            if capabilities.agent_token.is_some() {
                existing.capabilities.agent_token = capabilities.agent_token;
            }
            // Load created_by from disk if not already set in memory
            if existing.created_by.is_none() && persisted_created_by.is_some() {
                existing.created_by = persisted_created_by;
            }
            debug!(bot_id = %bot_id, "Bot registration updated");
        } else {
            // New registration
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities,
                    ws_connection: None,
                    session_token: None,
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: persisted_created_by,
                    protocol_version: 1,
                    user_visibility: persisted_user_visibility,
                    friend_ext: persisted_friend_ext,
                    friend_check_in_strategy: persisted_friend_check_in_strategy,
                },
            );
            info!(bot_id = %bot_id, "Bot registered");
        }

        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id).or_insert((now, now));
        entry.1 = now;

        Ok(())
    }

    async fn update_capabilities(
        &self,
        bot_id: &str,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(bot_id);
        self.sync_binding_channel_index(bot_id, &capabilities)
            .await;
        // Persist to disk verbatim (no empty-array skip), matching the
        // wholesale replacement semantics of the core update path.
        self.save_capabilities_to_disk(bot_id, &capabilities).await?;
        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(bot_id) {
            existing.last_heartbeat = Instant::now();
            existing.capabilities = capabilities;
            let mut audit = self.control_plane_audit.write().await;
            let now = unix_millis();
            let entry = audit.entry(bot_id.to_string()).or_insert((now, now));
            entry.1 = now;
            Ok(())
        } else {
            Err(ServiceError::BotNotFound(bot_id.to_string()))
        }
    }

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(&bot_id);

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        let persisted_created_by = {
            let bots = self.bots.read().await;
            bots.get(&bot_id)
                .and_then(|bot| bot.created_by.clone())
                .unwrap_or_else(|| created_by.to_string())
        };
        let persisted_attributes = {
            let path = self.bot_info_path(&bot_id);
            if let Ok(content) = fs::read_to_string(&path).await {
                serde_json::from_str::<PersistedCapabilities>(&content)
                    .ok()
                    .map(|value| {
                        (
                            value.user_visibility,
                            value.friend_ext,
                            value.friend_check_in_strategy,
                        )
                    })
                    .unwrap_or_default()
            } else {
                Default::default()
            }
        };

        let persisted = PersistedCapabilities {
            bot_id: bot_id.clone(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token: Some(token.to_string()),
            registered_at: now,
            hidden: false,
            created_by: Some(persisted_created_by),
            visibility: if capabilities.visibility.is_empty() {
                None
            } else {
                Some(capabilities.visibility.clone())
            },
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
            user_visibility: persisted_attributes.0,
            friend_ext: persisted_attributes.1.clone(),
            friend_check_in_strategy: persisted_attributes.2,
        };

        let path = self.bot_info_path(&bot_id);
        let dir = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {}", bot_id))
        })?;
        fs::create_dir_all(dir).await?;
        let content = serde_json::to_string_pretty(&persisted)?;
        fs::write(&path, content).await?;

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
                if capabilities.binding_channels.is_some() {
                    existing.capabilities.binding_channels = capabilities.binding_channels.clone();
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
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_id: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities,
                        ws_connection: None,
                        session_token: Some(token.to_string()),
                        env: Some(resolve_env()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: Some(created_by.to_string()),
                        protocol_version: 1,
                        user_visibility: persisted_attributes.0,
                        friend_ext: persisted_attributes.1,
                        friend_check_in_strategy: persisted_attributes.2,
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

        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id.clone()).or_insert((now, now));
        entry.1 = now;

        info!(bot_id = %bot_id, "Bot registered with owner and token");
        Ok(())
    }

    async fn update_status(&self, bot_id: &str) -> bool {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            bot.last_heartbeat = Instant::now();
            debug!(bot_id = %bot_id, "Bot heartbeat renewed");
            true
        } else {
            debug!(bot_id = %bot_id, "Bot not found for status update");
            false
        }
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
    }

    async fn get_agent_credentials(
        &self,
        bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .filter(|b| !b.is_expired())
            .map(|b| bcs_service_api::AgentCredentials {
                agent_code: b.capabilities.agent_code.clone(),
                agent_token: b.capabilities.agent_token.clone(),
            })
    }

    async fn add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        let bots = self.bots.read().await;
        if !bots.contains_key(bot_id) {
            return;
        }
        drop(bots);

        if key == "agent_token" {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.capabilities.agent_token = Some(value);
            }
            return;
        }

        if key == "client_kind" {
            self.bot_info_overrides
                .write()
                .await
                .insert((bot_id.to_string(), key.to_string()), value);
        }
    }

    async fn get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        if key == "agent_token" {
            let bots = self.bots.read().await;
            return bots
                .get(bot_id)
                .and_then(|bot| bot.capabilities.agent_token.clone());
        }

        self.bot_info_overrides
            .read()
            .await
            .get(&(bot_id.to_string(), key.to_string()))
            .cloned()
    }

    async fn set_bot_info(&self, bot_id: &str, key: &str, value: Option<String>) {
        if let Some(value) = value {
            self.add_bot_info(bot_id, key, value).await;
            return;
        }
        if key == "client_kind" {
            self.bot_info_overrides
                .write()
                .await
                .remove(&(bot_id.to_string(), key.to_string()));
        }
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values().map(|b| b.to_registered_bot()).collect()
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        friend_uuids: &HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let bots = self.bots.read().await;
        let name_lower = name.to_lowercase();

        let filtered: Vec<(RegisteredBot, bool)> = bots
            .values()
            .map(|b| b.to_registered_bot())
            .filter(|b| b.bot_uuid != bot_uuid)
            .filter(|b| b.actor_kind != bcs_service_api::ActorKind::Human)
            .filter(|b| {
                if !name.is_empty() {
                    b.capabilities
                        .name
                        .as_ref()
                        .map(|n| n.to_lowercase().contains(&name_lower))
                        .unwrap_or(false)
                } else {
                    true
                }
            })
            .filter_map(|b| {
                let is_friend = friend_uuids.contains(&b.bot_uuid);
                let vis = b.capabilities.visibility.as_str();
                if cooperatable_only {
                    if vis == "public" || is_friend {
                        Some((b, is_friend))
                    } else {
                        None
                    }
                } else {
                    if vis == "public" || vis == "protected" {
                        Some((b, is_friend))
                    } else {
                        None
                    }
                }
            })
            .collect();

        let total = filtered.len();
        let page: Vec<(RegisteredBot, bool)> =
            filtered.into_iter().skip(offset).take(limit).collect();

        (page, total)
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        let current_env = resolve_env();
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                b.created_by.as_deref() == Some(created_by)
                    && b.env.as_deref() == Some(current_env.as_str())
            })
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let mut seen = HashSet::new();
        bot_ids
            .iter()
            .filter(|id| seen.insert(id.as_str()))
            .filter_map(|id| bots.get(id.as_str()))
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let query_lower = query.to_lowercase();

        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                // Check name
                b.capabilities.name.as_ref()
                    .map(|n| n.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check summary
                || b.capabilities.summary.as_ref()
                    .map(|s| s.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check domains
                || b.capabilities.domains.iter()
                    .any(|d| d.to_lowercase().contains(&query_lower))
                // Check skills
                || b.capabilities.skills.iter()
                    .any(|s| s.name.to_lowercase().contains(&query_lower))
                // Check scopes
                || b.capabilities.scopes.iter()
                    .any(|s| s.to_lowercase().contains(&query_lower))
                // Check bot_id
                || b.bot_id.to_lowercase().contains(&query_lower)
            })
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| skills.iter().all(|s| b.has_skill(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| domains.iter().all(|d| b.has_domain(d)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| scopes.iter().all(|s| b.has_scope(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.soft_delete(bot_id).await
    }

    async fn soft_delete(&self, bot_id: &str) -> bool {
        self.deleted_bot_ids.write().await.insert(bot_id.to_string());
        let mut bots = self.bots.write().await;
        let memory_removed = bots.remove(bot_id).is_some();
        drop(bots);

        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.retain(|_, value| value != bot_id);
        drop(token_to_bot);

        let mut binding_index = self.binding_channel_index.write().await;
        binding_index.retain(|_, value| value != bot_id);

        memory_removed || self.bot_info_path(bot_id).exists()
    }

    async fn cleanup_expired(&self) {
        let mut bots = self.bots.write().await;
        let before = bots.len();
        bots.retain(|_, b| !b.is_expired());
        let removed = before - bots.len();
        if removed > 0 {
            warn!(request_id = %bcs_observability::CurrentRequestId, removed, "Removed expired bot registrations");
        }
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return None;
        }
        self.load_capabilities_from_disk(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        // Update binding channel index
        self.sync_binding_channel_index(bot_id, caps).await;

        // Persist to disk
        self.save_capabilities_to_disk(bot_id, caps).await?;
        // Update in-memory cache so that subsequent get() calls reflect the final
        // merged capabilities produced by the application layer.
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.capabilities.name = caps.name.clone();
                existing.capabilities.summary = caps.summary.clone();
                existing.capabilities.domains = caps.domains.clone();
                existing.capabilities.skills = caps.skills.clone();
                existing.capabilities.scopes = caps.scopes.clone();
                existing.capabilities.binding_channels = caps.binding_channels.clone();
                if !caps.visibility.is_empty() {
                    existing.capabilities.visibility = caps.visibility.clone();
                }
                // agent_code: 从请求中更新（允许设置或清除）
                if caps.agent_code.is_some() {
                    existing.capabilities.agent_code = caps.agent_code.clone();
                }
                // agent_token: 从请求中更新（允许设置）
                if caps.agent_token.is_some() {
                    existing.capabilities.agent_token = caps.agent_token.clone();
                }
            }
        }
        Ok(())
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        let visibility_value = if visibility.is_empty() {
            "private"
        } else {
            visibility
        };

        // Update in-memory
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.capabilities.visibility = visibility_value.to_string();
            }
        }

        // Update on disk (only the visibility field)
        let path = self.bot_info_path(bot_id);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(mut persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    persisted.visibility = Some(visibility_value.to_string());
                    let updated = serde_json::to_string_pretty(&persisted)?;
                    fs::write(&path, updated).await?;
                    info!(bot_id = %bot_id, visibility = %visibility_value, "Updated visibility on disk");
                }
            }
        }

        Ok(())
    }

    /// DEPRECATED (Rev-4 / Human Actor V1): Noop + WARN. Replaced by
    /// [`update_actor_status`](Self::update_actor_status). (Task H.1)
    #[allow(deprecated)]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            bot_id = %bot_id,
            hidden = %hidden,
            "set_hidden is DEPRECATED and is now a Noop; use update_actor_status(bot_id, ActorStatus::Hidden) instead (Task H.1)"
        );
        Ok(())
    }

    /// Update the actor-level lifecycle status (`Online` / `Hidden`) — Task P.2.
    ///
    /// In-memory implementation: only updates the in-process registry entry.
    /// File-based persistence is intentionally not extended for `status` in
    /// the local registry (MemoryBotRepo is a dev-only fallback; production runs
    /// PersistentBotRepo which persists to MySQL).
    async fn update_actor_status(
        &self,
        bot_id: &str,
        status: bcs_service_api::ActorStatus,
    ) -> ServiceResult<()> {
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.status = status;
            info!(bot_id = %bot_id, status = ?status, "update_actor_status (in-memory): updated");
        } else {
            debug!(bot_id = %bot_id, "update_actor_status (in-memory): bot not loaded, no-op");
        }
        Ok(())
    }

    /// Ensure a Human Actor entry exists for the given staff_no — Task O.3.
    ///
    /// In-memory implementation: idempotent insert into the in-process registry.
    /// `name` is preserved on subsequent calls (Requirement 3.1#4).
    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        let bot_uuid = format!("human_{}", staff_no);

        let default_summary = "写点什么介绍自己";

        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(&bot_uuid) {
            // Backfill summary if it is missing or empty.
            let needs_summary = existing
                .capabilities
                .summary
                .as_deref()
                .map_or(true, |s| s.is_empty());
            if needs_summary {
                existing.capabilities.summary = Some(default_summary.to_string());
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor (in-memory): backfilled empty summary"
                );
            } else {
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor (in-memory): already exists, preserving existing fields"
                );
            }
            return Ok(bcs_service_api::EnsureHumanResult { created: false });
        }

        let session_token = uuid::Uuid::new_v4().to_string();
        let caps = BotCapabilities {
            name: Some(nick_name.to_string()),
            summary: Some(default_summary.to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        };

        bots.insert(
            bot_uuid.clone(),
            RegisteredBotInner {
                bot_id: bot_uuid.clone(),
                last_heartbeat: Instant::now(),
                capabilities: caps,
                ws_connection: None,
                session_token: Some(session_token),
                env: Some(resolve_env()),
                status: bcs_service_api::ActorStatus::Online,
                actor_kind: bcs_service_api::ActorKind::Human,
                created_by: Some(staff_no.to_string()),
                protocol_version: 1,
                user_visibility: UserVisibility::default(),
                friend_ext: serde_json::Map::new(),
                friend_check_in_strategy: FriendCheckInStrategy::default(),
            },
        );

        let now = unix_millis();
        self.control_plane_audit
            .write()
            .await
            .insert(bot_uuid.clone(), (now, now));

        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            nick_name = %nick_name,
            "ensure_human_actor (in-memory): row inserted"
        );
        Ok(bcs_service_api::EnsureHumanResult { created: true })
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let bots = self.bots.read().await;
        let results: Vec<RegisteredBot> = bots
            .values()
            .filter(|b| {
                // Must be a Bot (not Human) and match env
                if b.actor_kind != bcs_service_api::ActorKind::Bot {
                    return false;
                }
                if b.env.as_deref() != Some(env) {
                    return false;
                }
                // Rule (a): created_by matches
                if b.created_by.as_deref() == Some(staff_no) {
                    return true;
                }
                // Rule (b): created_by is None and namespace is whitelisted
                if b.created_by.is_none() && is_legacy_namespace(&b.bot_id, staff_no) {
                    return true;
                }
                false
            })
            .map(|b| {
                // 清除敏感字段，防止通过常规接口泄露
                let mut capabilities = b.capabilities.clone();
                capabilities.agent_code = None;
                capabilities.agent_token = None;
                RegisteredBot {
                    bot_uuid: b.bot_id.clone(),
                    capabilities,
                    env: b.env.clone(),
                    created_by: b.created_by.clone(),
                    actor_kind: b.actor_kind.clone(),
                    status: b.status.clone(),
                }
            })
            .collect();

        Ok(results)
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return false;
        }
        self.bot_info_path(bot_id).exists()
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        // Update in-memory (respects overwrite flag; early return if not overwriting and already claimed)
        let already_claimed = {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                if overwrite || bot.created_by.is_none() {
                    bot.created_by = Some(created_by.to_string());
                    false
                } else {
                    true
                }
            } else {
                false
            }
        };

        if already_claimed {
            return Ok(());
        }

        // Update on disk (respects overwrite flag)
        let path = self.bot_info_path(bot_id);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(mut persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if overwrite || persisted.created_by.is_none() {
                        persisted.created_by = Some(created_by.to_string());
                        let updated = serde_json::to_string_pretty(&persisted)?;
                        fs::write(&path, updated).await?;
                        info!(bot_id = %bot_id, created_by = %created_by, overwrite = overwrite, "Updated created_by on disk");
                    }
                }
            }
        }

        Ok(())
    }


    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.persist_bot_token(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        self.read_persisted_bot_token(bot_id).await
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.find_bot_id_by_token(token).await
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        let index = self.binding_channel_index.read().await;
        index
            .get(&(channel.to_string(), binding_key.to_string()))
            .cloned()
    }

    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        self.find_bot_id_by_agent_code(agent_code).await
    }

    // ===== Streaming Connection Management =====

    async fn connect_or_promote_streaming(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        self.promote_streaming_connection(bot_id).await
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        self.register_streaming_session(bot_id).await
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.reconnect_streaming_session(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            if let Some(conn) = bot.ws_connection.take() {
                // DO NOT remove token_to_bot mapping - token should persist for reconnection
                info!(
                    bot_id = %bot_id,
                    token = %conn.session_token,
                    duration_ms = conn.connected_at.elapsed().as_millis() as u64,
                    "Bot streaming connection removed (token preserved for reconnection)"
                );
            }
        } else {
            debug!(bot_id = %bot_id, "Bot not found for disconnect");
        }
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .map(|b| b.ws_connection.is_some())
            .unwrap_or(false)
    }

    async fn send_frame(&self, bot_id: &str, _frame: String) -> Result<(), ()> {
        warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot frame delivery is owned by the ws adapter");
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        let bots = self.bots.read().await;
        bots.iter()
            .filter(|(_, b)| b.ws_connection.is_some())
            .map(|(id, _)| id.clone())
            .collect()
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(token.clone(), bot_id.clone());
        debug!(bot_id = %bot_id, token = %token, "Token mapping stored");
    }

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        let bots = self.bots.read().await;
        bots.get(bot_id).map(|b| b.protocol_version).unwrap_or(1)
    }

    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.protocol_version = version;
        }
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        self.register_http_session(bot_id, token).await
    }

    async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.dispatch_streaming_request(bot_id, method, params, timeout_ms).await
    }

    async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        let mut pending = self.pending_requests.write().await;
        if let Some(tx) = pending.remove(request_id) {
            let _ = tx.send(response);
        }
    }
}
