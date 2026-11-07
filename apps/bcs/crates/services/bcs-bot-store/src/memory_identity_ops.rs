//! Registration and credential identity bodies for `MemoryBotRepo`.
//!
//! Extracted from the `BotRepoPort` trait impl during the bot-avatar split.
//! These operations now share the record write guard and atomic persistence
//! helper with profile writes; the trait impl delegates to these methods.

use std::time::Instant;

use tokio::fs;

use tracing::{debug, info};

use bcs_service_api::{
    BotCapabilities, FriendCheckInStrategy, ServiceResult, UserVisibility,
};

use super::MemoryBotRepo;
use super::{PersistedCapabilities, RegisteredBotInner, resolve_env, unix_millis};

impl MemoryBotRepo {
    pub(super) async fn register_impl(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        let _write_guard = self.record_write_lock.lock().await;
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

    pub(super) async fn register_with_owner_and_token_impl(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        let _write_guard = self.record_write_lock.lock().await;
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
                serde_json::from_str::<super::profile::StoredBotProfile>(&content)
                    .ok()
                    .map(|stored| {
                        (
                            stored.record.user_visibility,
                            stored.record.friend_ext,
                            stored.record.friend_check_in_strategy,
                            // 普通存储写入保留头像字段 (bot-avatar Task 3)
                            stored.avatar_url,
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
        // 普通存储写入保留头像字段 (bot-avatar Task 3): the registration rewrite
        // carries the record's stored avatar through unchanged.
        let stored = super::profile::StoredBotProfile {
            record: persisted,
            avatar_url: persisted_attributes.3,
        };

        self.write_stored_profile(&bot_id, &stored).await?;

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

    pub(super) async fn save_token_impl(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        let _write_guard = self.record_write_lock.lock().await;
        // We need to save token separately since it's not in BotCapabilities
        // Read the persisted file directly, update token, and save back
        // (through the profile record so the avatar sibling field is kept —
        // bot-avatar Task 3).
        let path = self.bot_info_path(bot_id);

        // Try to load existing persisted data
        let mut stored = if path.exists() {
            match fs::read_to_string(&path).await {
                Ok(content) => {
                    serde_json::from_str::<super::profile::StoredBotProfile>(&content)
                        .unwrap_or_else(|_| super::profile::StoredBotProfile {
                            record: PersistedCapabilities {
                                bot_id: bot_id.to_string(),
                                name: None,
                                summary: None,
                                domains: vec![],
                                skills: vec![],
                                scopes: vec![],
                                binding_channels: None,
                                token: Some(token.to_string()),
                                registered_at: 0,
                                hidden: false,
                                created_by: None,
                                visibility: None,
                                agent_code: None,
                                agent_token: None,
                                user_visibility: UserVisibility::default(),
                                friend_ext: serde_json::Map::new(),
                                friend_check_in_strategy: FriendCheckInStrategy::default(),
                            },
                            avatar_url: None,
                        })
                }
                Err(_) => super::profile::StoredBotProfile {
                    record: PersistedCapabilities {
                        bot_id: bot_id.to_string(),
                        name: None,
                        summary: None,
                        domains: vec![],
                        skills: vec![],
                        scopes: vec![],
                        binding_channels: None,
                        token: Some(token.to_string()),
                        registered_at: 0,
                        hidden: false,
                        created_by: None,
                        visibility: None,
                        agent_code: None,
                        agent_token: None,
                        user_visibility: UserVisibility::default(),
                        friend_ext: serde_json::Map::new(),
                        friend_check_in_strategy: FriendCheckInStrategy::default(),
                    },
                    avatar_url: None,
                },
            }
        } else {
            super::profile::StoredBotProfile {
                record: PersistedCapabilities {
                    bot_id: bot_id.to_string(),
                    name: None,
                    summary: None,
                    domains: vec![],
                    skills: vec![],
                    scopes: vec![],
                    binding_channels: None,
                    token: Some(token.to_string()),
                    registered_at: 0,
                    hidden: false,
                    created_by: None,
                    visibility: None,
                    agent_code: None,
                    agent_token: None,
                    user_visibility: UserVisibility::default(),
                    friend_ext: serde_json::Map::new(),
                    friend_check_in_strategy: FriendCheckInStrategy::default(),
                },
                avatar_url: None,
            }
        };

        let previous_token = stored.record.token.clone();

        // Update token
        stored.record.token = Some(token.to_string());

        self.write_stored_profile(bot_id, &stored).await?;

        let previous_token = {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.session_token.replace(token.to_string()).or(previous_token)
            } else {
                previous_token
            }
        };
        let mut token_to_bot = self.token_to_bot.write().await;
        if let Some(previous_token) = previous_token.filter(|previous| previous != token) {
            token_to_bot.remove(&previous_token);
        }
        token_to_bot.insert(token.to_string(), bot_id.to_string());

        info!(bot_id = %bot_id, "Token saved to storage");
        Ok(())
    }

    pub(super) async fn find_bot_by_token_impl(&self, token: &str) -> Option<String> {
        // First check in-memory token mapping (fast path)
        {
            let token_to_bot = self.token_to_bot.read().await;
            if let Some(bot_id) = token_to_bot.get(token) {
                if self.deleted_bot_ids.read().await.contains(bot_id) {
                    return None;
                }
                return Some(bot_id.clone());
            }
        }

        // Fall back to scanning disk (slow path, for BCS server restarts)
        let mut entries = match fs::read_dir(&self.bots_base_dir).await {
            Ok(entries) => entries,
            Err(_) => return None,
        };

        while let Ok(Some(entry)) = entries.next_entry().await {
            if !entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false) {
                continue;
            }

            let bot_id = entry.file_name().to_string_lossy().to_string();
            if self.deleted_bot_ids.read().await.contains(&bot_id) {
                continue;
            }
            let path = self.bot_info_path(&bot_id);

            if !path.exists() {
                continue;
            }

            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if persisted.token.as_deref() == Some(token) {
                        debug!(bot_id = %bot_id, "Found bot by token on disk");
                        return Some(bot_id);
                    }
                }
            }
        }

        None
    }
}
