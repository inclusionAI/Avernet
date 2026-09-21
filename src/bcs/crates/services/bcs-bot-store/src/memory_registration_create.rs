//! Atomic local creation with no replacement of existing memory or files.
//!
//! This memory adapter does not provide durable pending-registration recovery.
//! Cancellation while awaiting blocking persistence can leave a complete file
//! without a published in-memory Bot. A retry refuses that file without hydrating
//! it, so the caller's active-Bot check fails closed. The same applies after a
//! repository restart. Files alone cannot prove a Bot is active: deletion markers
//! are process-local and another writer may have rotated its token or metadata.
//! Use PersistentBotRepo for durable creation; scoped registration does not replay
//! credentials or resume owner-edge writes. Do not recover this
//! window by publishing the retry's candidate or blindly loading the old file.

use std::io::{ErrorKind, Write};
use std::path::Path;

use super::{
    BotCapabilities, Instant, MemoryBotRepo, PersistedCapabilities, RegisteredBotInner,
    ServiceError, ServiceResult, resolve_env, unix_millis,
};

impl MemoryBotRepo {
    pub(super) async fn load_registration_token(
        &self,
        bot_id: &str,
    ) -> ServiceResult<Option<String>> {
        // Match creation's lock order; hold the tombstone guard across the read
        // so deletion cannot be bypassed by the persisted-file fallback.
        let bots = self.bots.read().await;
        let deleted = self.deleted_bot_ids.read().await;
        if deleted.contains(bot_id) {
            return Ok(None);
        }
        if let Some(token) = bots.get(bot_id).and_then(|bot| bot.session_token.clone()) {
            return Ok(Some(token));
        }
        let content = match tokio::fs::read(self.bot_info_path(bot_id)).await {
            Ok(content) => content,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let stored: PersistedCapabilities = serde_json::from_slice(&content).map_err(|_| {
            ServiceError::InternalError("invalid persisted Bot credential record".into())
        })?;
        if stored.bot_id != bot_id {
            return Err(ServiceError::InternalError(
                "persisted Bot credential identity mismatch".into(),
            ));
        }
        Ok(stored.token)
    }

    pub(super) async fn create_registration_once(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<bool> {
        // Existing nested lock order is bots -> tokens -> deleted IDs. Retain
        // these guards across persistence and publication so another create or
        // soft-delete cannot pass the absence check before publication finishes.
        let mut bots = self.bots.write().await;
        if bots.contains_key(&bot_id) {
            return Ok(false);
        }
        let mut tokens = self.token_to_bot.write().await;
        let deleted = self.deleted_bot_ids.read().await;
        if deleted.contains(&bot_id) {
            return Ok(false);
        }
        if tokens
            .get(token)
            .is_some_and(|existing| existing != &bot_id)
        {
            return Err(ServiceError::InternalError(
                "Bot registration token already exists".into(),
            ));
        }
        let mut bindings = self.binding_channel_index.write().await;
        let mut audit = self.control_plane_audit.write().await;
        let now = unix_millis();
        let persisted = PersistedCapabilities {
            bot_id: bot_id.clone(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token: Some(token.into()),
            registered_at: now,
            hidden: false,
            created_by: Some(created_by.into()),
            visibility: if capabilities.visibility.is_empty() {
                None
            } else {
                Some(capabilities.visibility.clone())
            },
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
            user_visibility: Default::default(),
            friend_ext: Default::default(),
            friend_check_in_strategy: Default::default(),
        };
        let path = self.bot_info_path(&bot_id);
        let created = tokio::task::spawn_blocking(move || persist_once(&path, &persisted))
            .await
            .map_err(|_| {
                ServiceError::InternalError("Bot registration persistence task failed".into())
            })??;
        if !created {
            return Ok(false);
        }

        // No awaits after the durable file publication: the complete record and
        // all indexes are installed while the already-acquired guards are held.
        if let Some(channels) = &capabilities.binding_channels {
            for (channel, binding) in channels {
                bindings
                    .entry((channel.clone(), binding.binding_key.clone()))
                    .or_insert_with(|| bot_id.clone());
            }
        }
        tokens.insert(token.into(), bot_id.clone());
        audit.insert(bot_id.clone(), (now, now));
        bots.insert(
            bot_id.clone(),
            RegisteredBotInner {
                bot_id,
                last_heartbeat: Instant::now(),
                capabilities,
                ws_connection: None,
                session_token: Some(token.into()),
                env: Some(resolve_env()),
                status: bcs_service_api::ActorStatus::Online,
                actor_kind: bcs_service_api::ActorKind::Bot,
                created_by: Some(created_by.into()),
                protocol_version: 1,
                user_visibility: Default::default(),
                friend_ext: Default::default(),
                friend_check_in_strategy: Default::default(),
            },
        );
        Ok(true)
    }
}

fn existing_record(path: &Path, bot_id: &str) -> ServiceResult<bool> {
    match std::fs::read(path) {
        Ok(bytes) => {
            let stored: PersistedCapabilities = serde_json::from_slice(&bytes).map_err(|_| {
                ServiceError::InternalError("invalid persisted Bot registration".into())
            })?;
            if stored.bot_id != bot_id {
                return Err(ServiceError::InternalError(
                    "persisted Bot registration identity mismatch".into(),
                ));
            }
            Ok(true)
        }
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

fn persist_once(path: &Path, record: &PersistedCapabilities) -> ServiceResult<bool> {
    if existing_record(path, &record.bot_id)? {
        return Ok(false);
    }
    let directory = path
        .parent()
        .ok_or_else(|| ServiceError::InternalError("invalid Bot registration path".into()))?;
    std::fs::create_dir_all(directory)?;
    let content = serde_json::to_vec_pretty(record)
        .map_err(|_| ServiceError::InternalError("Bot registration serialization failed".into()))?;
    // Write a private temporary file completely before linking it into place.
    // persist_noclobber atomically refuses an existing destination, including
    // one created by a separate repository instance/process after our read.
    let mut pending = tempfile::NamedTempFile::new_in(directory)?;
    pending.write_all(&content)?;
    pending.as_file().sync_all()?;
    match pending.persist_noclobber(path) {
        Ok(_) => Ok(true),
        Err(error) if error.error.kind() == ErrorKind::AlreadyExists => {
            if existing_record(path, &record.bot_id)? {
                Ok(false)
            } else {
                Err(ServiceError::InternalError(
                    "conflicting Bot registration disappeared".into(),
                ))
            }
        }
        Err(error) => Err(error.error.into()),
    }
}
