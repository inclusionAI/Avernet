use super::*;
use crate::admission::StreamingStore;
use bcs_service_api::port::repo::{BotIdentity as Identity, BotMemoryIdentity as MemoryIdentity};

fn connection_error(error: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError(error.to_string())
}

impl MemoryBotRepo {
    async fn read_connection_file(&self, id: &str) -> Result<Option<Identity>, ServiceError> {
        if self.deleted_bot_ids.read().await.contains(id) {
            return Ok(Some(Identity {
                id: id.to_owned(), token: None, deleted: true,
                capabilities: BotCapabilities::default(), env: Some(resolve_env()),
                created_by: None, actor_kind: bcs_service_api::ActorKind::Bot,
                status: bcs_service_api::ActorStatus::Online,
            }));
        }
        let content = match fs::read_to_string(self.bot_info_path(id)).await {
            Ok(content) => content,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(err) => return Err(connection_error(err)),
        };
        let saved: PersistedCapabilities =
            serde_json::from_str(&content).map_err(connection_error)?;
        let mut capabilities = BotCapabilities::from(&saved);
        capabilities.agent_token = None;
        let current = self.bots.read().await;
        let current = current.get(id);
        Ok(Some(Identity {
            id: id.to_owned(),
            token: saved.token,
            deleted: false,
            capabilities,
            env: Some(resolve_env()),
            created_by: saved.created_by,
            actor_kind: current
                .map(|bot| bot.actor_kind)
                .unwrap_or(bcs_service_api::ActorKind::Bot),
            status: current
                .map(|bot| bot.status)
                .unwrap_or(bcs_service_api::ActorStatus::Online),
        }))
    }
}

#[async_trait]
impl StreamingStore for MemoryBotRepo {
    fn identity_locks(&self) -> &crate::admission::IdentityLocks {
        &self.identity_locks
    }
    async fn memory_token_owner(&self, token: &str) -> Option<String> {
        self.token_to_bot.read().await.get(token).cloned()
    }
    async fn memory_identity(&self, id: &str) -> Option<MemoryIdentity> {
        let deleted = self.deleted_bot_ids.read().await.contains(id);
        self.bots.read().await.get(id).map(|bot| MemoryIdentity {
            identity: Identity {
                id: id.to_owned(),
                token: bot.session_token.clone(),
                deleted,
                capabilities: bot.capabilities.clone(),
                env: bot.env.clone(),
                created_by: bot.created_by.clone(),
                actor_kind: bot.actor_kind,
                status: bot.status,
            },
            // General local reads keep disconnected token-bearing entries visible.
            // Core receives the timestamp and owns temporary expiry policy.
            last_heartbeat: bot.last_heartbeat,
            connected: bot.ws_connection.is_some(),
        })
    }
    async fn stored_by_id(&self, id: &str) -> Result<Option<Identity>, ServiceError> {
        self.read_connection_file(id).await
    }
    async fn stored_by_token(&self, token: &str) -> Result<Option<Identity>, ServiceError> {
        let mut entries = match fs::read_dir(&self.bots_base_dir).await {
            Ok(entries) => entries,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(err) => return Err(connection_error(err)),
        };
        while let Some(entry) = entries.next_entry().await.map_err(connection_error)? {
            if !entry.file_type().await.map_err(connection_error)?.is_dir() {
                continue;
            }
            let id = entry.file_name().to_string_lossy().to_string();
            if self.deleted_bot_ids.read().await.contains(&id) {
                continue;
            }
            if let Some(identity) = self.read_connection_file(&id).await? {
                if identity.token.as_deref() == Some(token) {
                    return Ok(Some(identity));
                }
            }
        }
        Ok(None)
    }
    async fn replace_token(&self, identity: &Identity, token: &str) -> Result<(), ServiceError> {
        // Called with the same identity lock used by persistence writers.
        self.save_token_under_identity_lock(&identity.id, token)
            .await
            .map_err(connection_error)
    }
    async fn attach(&self, identity: Identity, token: &str) -> Result<(), ServiceError> {
        if self.deleted_bot_ids.read().await.contains(&identity.id) {
            return Err(ServiceError::Conflict(identity.id));
        }
        self.sync_binding_channel_index(&identity.id, &identity.capabilities)
            .await;
        let mut bots = self.bots.write().await;
        let attributes = bots
            .get(&identity.id)
            .map(|bot| {
                (
                    bot.protocol_version,
                    bot.user_visibility,
                    bot.friend_ext.clone(),
                    bot.friend_check_in_strategy,
                )
            })
            .unwrap_or((
                1,
                UserVisibility::default(),
                serde_json::Map::new(),
                FriendCheckInStrategy::default(),
            ));
        let mut tokens = self.token_to_bot.write().await;
        tokens.retain(|_, owner| owner != &identity.id);
        tokens.insert(token.to_owned(), identity.id.clone());
        bots.insert(
            identity.id.clone(),
            RegisteredBotInner {
                bot_id: identity.id,
                last_heartbeat: Instant::now(),
                capabilities: identity.capabilities,
                ws_connection: Some(BotConnection {
                    session_token: token.to_owned(),
                    connected_at: Instant::now(),
                }),
                session_token: Some(token.to_owned()),
                env: identity.env.or_else(|| Some(resolve_env())),
                status: identity.status,
                actor_kind: identity.actor_kind,
                created_by: identity.created_by,
                protocol_version: attributes.0,
                user_visibility: attributes.1,
                friend_ext: attributes.2,
                friend_check_in_strategy: attributes.3,
            },
        );
        Ok(())
    }
}
