use super::admission::{Identity, MemoryIdentity, StreamingStore};
use super::*;
use bcs_service_api::{BotConnectParams, ConnectError};

fn connection_error(error: impl std::fmt::Display) -> ConnectError {
    ConnectError::InternalError(error.to_string())
}

fn identity_from_row(row: &DbRow) -> Result<Identity, ConnectError> {
    let get = |key| db_get_column_opt::<String>(row, key).map_err(connection_error);
    let info: BotInfo = match get("bot_info")? {
        Some(json) => serde_json::from_str(&json).map_err(connection_error)?,
        None => BotInfo::default(),
    };
    let status = match get("status")?.as_deref() {
        Some("hidden") => bcs_service_api::ActorStatus::Hidden,
        _ => bcs_service_api::ActorStatus::Online,
    };
    Ok(Identity {
        id: db_get_column(row, "bot_uuid").map_err(connection_error)?,
        token: get("session_token")?,
        deleted: db_get_column_opt::<i64>(row, "is_deleted")
            .map_err(connection_error)?
            .unwrap_or(0)
            != 0,
        capabilities: BotCapabilities {
            name: get("name")?,
            summary: info.summary,
            domains: info.domains,
            skills: info.skills,
            scopes: info.scopes,
            binding_channels: info.binding_channels,
            hidden: status == bcs_service_api::ActorStatus::Hidden,
            visibility: get("visibility")?
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "private".into()),
            agent_code: get("agent_code")?.or(info.agent_code),
            agent_token: None,
        },
        env: get("env")?,
        created_by: get("created_by")?,
        actor_kind: match get("actor_kind")?.as_deref() {
            Some("human") => bcs_service_api::ActorKind::Human,
            _ => bcs_service_api::ActorKind::Bot,
        },
        status,
    })
}

impl PersistentBotRepo {
    async fn read_connection_identity(
        &self,
        key: &str,
        value: &str,
    ) -> Result<Option<Identity>, ConnectError> {
        // `key` is chosen only by the two private entry points below.
        let sql = format!(
            "SELECT bot_uuid, session_token, is_deleted, name, bot_info, visibility, status, actor_kind, env, created_by, agent_code FROM bcs_bots WHERE {key} = ? AND env = ? LIMIT 1"
        );
        let env = resolve_env();
        let rows = self
            .db_query(&sql, vec![Value::from(value), Value::from(env.as_str())])
            .await
            .map_err(connection_error)?;
        rows.first().map(identity_from_row).transpose()
    }

    pub(super) async fn admit_streaming(
        &self,
        params: BotConnectParams,
    ) -> Result<bcs_service_api::BotConnectResult, ConnectError> {
        admission::connect(self, params).await
    }
}

#[async_trait]
impl StreamingStore for PersistentBotRepo {
    fn identity_locks(&self) -> &admission::IdentityLocks {
        &self.identity_locks
    }

    async fn memory_token_owner(&self, token: &str) -> Option<String> {
        if let Some(id) = self.token_to_bot.read().await.get(token).cloned() {
            return Some(id);
        }
        self.bots
            .read()
            .await
            .iter()
            .find(|(_, bot)| bot.session_token.as_deref() == Some(token))
            .map(|(id, _)| id.clone())
    }

    async fn memory_identity(&self, id: &str) -> Option<MemoryIdentity> {
        self.bots.read().await.get(id).map(|bot| MemoryIdentity {
            identity: Identity {
                id: id.to_owned(),
                token: bot.session_token.clone(),
                deleted: false,
                capabilities: bot.capabilities.clone(),
                env: bot.env.clone(),
                created_by: bot.created_by.clone(),
                actor_kind: bot.actor_kind,
                status: bot.status,
            },
            expired: bot.is_expired(),
            connected: bot.ws_connection.is_some(),
        })
    }

    async fn stored_by_id(&self, id: &str) -> Result<Option<Identity>, ConnectError> {
        self.read_connection_identity("bot_uuid", id).await
    }
    async fn stored_by_token(&self, token: &str) -> Result<Option<Identity>, ConnectError> {
        self.read_connection_identity("session_token", token).await
    }

    async fn promote(&self, identity: &Identity, token: &str) -> Result<(), ConnectError> {
        let sql = "UPDATE bcs_bots SET session_token = ? WHERE bot_uuid = ? AND env = ? AND session_token = ? AND COALESCE(is_deleted, 0) = 0";
        let env = resolve_env();
        let count = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(token),
                    Value::from(identity.id.as_str()),
                    Value::from(env.as_str()),
                    Value::from(identity.token.as_deref().unwrap_or_default()),
                ],
            )
            .await
            .map_err(connection_error)?;
        if count != 1 {
            return Err(ConnectError::AlreadyRegistered(identity.id.clone()));
        }
        Ok(())
    }

    async fn attach(&self, identity: Identity, token: &str) -> Result<(), ConnectError> {
        self.sync_binding_channel_index(&identity.id, &identity.capabilities)
            .await;
        let mut bots = self.bots.write().await;
        let mut tokens = self.token_to_bot.write().await;
        tokens.retain(|_, owner| owner != &identity.id);
        tokens.insert(token.to_owned(), identity.id.clone());
        bots.insert(
            identity.id.clone(),
            RegisteredBotInner {
                bot_uuid: identity.id,
                last_heartbeat: Instant::now(),
                capabilities: identity.capabilities,
                ws_connection: Some(BotConnection {
                    session_token: token.to_owned(),
                    connected_at: Instant::now(),
                }),
                session_token: Some(token.to_owned()),
                env: identity.env.or_else(|| Some(resolve_env())),
                hidden: identity.status == bcs_service_api::ActorStatus::Hidden,
                status: identity.status,
                actor_kind: identity.actor_kind,
                created_by: identity.created_by,
            },
        );
        Ok(())
    }
}
