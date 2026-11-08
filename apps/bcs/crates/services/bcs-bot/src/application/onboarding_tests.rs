//! Shared doubles and fixtures for the onboarding use-case tests.
//!
//! Moved verbatim from the inline `#[cfg(test)] mod tests` in
//! `onboarding.rs` to keep that file within the 1,000-physical-line
//! ceiling. The two test groups live in `onboarding_tests/names.rs`
//! (name/summary/binding merge and admin onboarding) and
//! `onboarding_tests/ownership.rs` (owner binding and ensure-bot).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, ActorStatus, BindingChannel, BindingChannels,
    BotRegistryCoreService, EnsureHumanResult, EnsureOwnerEdgesResult, OnboardActorIdentity,
    RegisteredBot, RelationCoreService, RelationEdge, ServiceResult, Skill,
};
use tempfile::TempDir;
use tokio::sync::Mutex;

use super::*;
use crate::BotCore;

#[path = "onboarding_tests/names.rs"]
mod names;
#[path = "onboarding_tests/ownership.rs"]
mod ownership;
#[path = "onboarding_tests/profile_avatar.rs"]
mod profile_avatar;

#[derive(Default)]
struct RecordingRelationCoreService {
    owner_edges: Mutex<Vec<(String, String, String)>>,
}

#[async_trait]
impl RelationCoreService for RecordingRelationCoreService {
    async fn upsert_edge(&self, _edge: RelationEdge) -> ServiceResult<()> {
        Ok(())
    }

    async fn delete_edge(&self, _from_id: &str, _to_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_edge(
        &self,
        _from_id: &str,
        _to_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(None)
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.owner_edges.lock().await.push((
            human_id.to_string(),
            bot_id.to_string(),
            env.to_string(),
        ));
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        self.ensure_owner_edges(human_id, bot_id, env).await?;
        Ok(EnsureOwnerEdgesResult::default())
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(
        &self,
        _caller: &str,
        _target: &str,
        _env: &str,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

struct StaticRegistry {
    bots: Mutex<HashMap<String, RegisteredBot>>,
    binding_index: Mutex<HashMap<(String, String), String>>,
    onboarded: Mutex<HashSet<String>>,
    saved_created_by: Mutex<Vec<(String, String, bool)>>,
    /// Bot avatar test state (bot-avatar plan Task 5): the stored avatar
    /// per bot, mirroring the record's dedicated avatar field. `Keep` never
    /// touches a value here.
    avatars: Mutex<HashMap<String, Option<String>>>,
    /// Every `update_profile_existing` request seen by the double.
    profile_writes: Mutex<Vec<(String, bcs_service_api::types::bot_profile::ExistingProfileUpdate)>>,
    /// Row-creation surface: every call to `register` (the double's only
    /// INSERT path) records the bot id.
    register_calls: Mutex<Vec<String>>,
    /// Simulates the race where the precheck `get` still sees the bot but
    /// the existing-only profile write classifies it `Missing`.
    miss_profile_writes: bool,
}

impl StaticRegistry {
    fn new(bots: Vec<RegisteredBot>) -> Self {
        Self::with_onboarded(bots, [])
    }

    fn with_onboarded<const N: usize>(bots: Vec<RegisteredBot>, onboarded: [&str; N]) -> Self {
        let mut bot_map = HashMap::new();
        let mut binding_index = HashMap::new();
        for bot in bots {
            if let Some(bindings) = bot.capabilities.binding_channels.as_ref() {
                for (channel, binding) in bindings {
                    binding_index.insert(
                        (channel.clone(), binding.binding_key.clone()),
                        bot.bot_uuid.clone(),
                    );
                }
            }
            bot_map.insert(bot.bot_uuid.clone(), bot);
        }

        Self {
            bots: Mutex::new(bot_map),
            binding_index: Mutex::new(binding_index),
            onboarded: Mutex::new(
                onboarded
                    .into_iter()
                    .map(|bot_id| bot_id.to_string())
                    .collect(),
            ),
            saved_created_by: Mutex::new(Vec::new()),
            avatars: Mutex::new(HashMap::new()),
            profile_writes: Mutex::new(Vec::new()),
            register_calls: Mutex::new(Vec::new()),
            miss_profile_writes: false,
        }
    }

    /// Simulates a bot missing from the precheck-to-write window: `get`
    /// still returns the bot, but `update_profile_existing` classifies the
    /// target as `ProfileWrite::Missing`.
    fn with_missing_profile_writes(mut self) -> Self {
        self.miss_profile_writes = true;
        self
    }

    /// Seed the stored avatar of one bot (the record's dedicated avatar
    /// field) for `Keep`/`Clear` assertions.
    async fn store_avatar(&self, bot_id: &str, url: Option<&str>) {
        self.avatars
            .lock()
            .await
            .insert(bot_id.to_string(), url.map(str::to_string));
    }

    /// The stored avatar of one bot, or `None` when never written.
    async fn stored_avatar(&self, bot_id: &str) -> Option<Option<String>> {
        self.avatars.lock().await.get(bot_id).cloned()
    }

    /// The `update_profile_existing` requests received so far.
    async fn profile_writes(
        &self,
    ) -> Vec<(String, bcs_service_api::types::bot_profile::ExistingProfileUpdate)> {
        self.profile_writes.lock().await.clone()
    }

    /// Bot rows currently held by the double (its DB row count).
    async fn row_count(&self) -> usize {
        self.bots.lock().await.len()
    }

    /// Every bot id passed to `register` (the double's only INSERT path).
    async fn register_calls(&self) -> Vec<String> {
        self.register_calls.lock().await.clone()
    }

    async fn created_by(&self, bot_id: &str) -> Option<String> {
        self.bots
            .lock()
            .await
            .get(bot_id)
            .and_then(|bot| bot.created_by.clone())
    }

    async fn saved_created_by_calls(&self) -> Vec<(String, String, bool)> {
        self.saved_created_by.lock().await.clone()
    }
}

#[async_trait]
impl BotRegistryCoreService for StaticRegistry {
    async fn register(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        self.register_calls.lock().await.push(bot_id.clone());
        self.bots
            .lock()
            .await
            .insert(bot_id.clone(), bot(bot_id, capabilities));
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.lock().await.get(bot_id).cloned()
    }

    async fn get_agent_credentials(
        &self,
        _bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.lock().await.values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        _created_by: &str,
        _limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        Ok(Vec::new())
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        if let Some(bot) = self.bots.lock().await.get_mut(bot_id) {
            bot.capabilities = caps.clone();
        }
        self.onboarded.lock().await.insert(bot_id.to_string());
        let mut index = self.binding_index.lock().await;
        index.retain(|_, indexed_bot_id| indexed_bot_id != bot_id);
        if let Some(bindings) = caps.binding_channels.as_ref() {
            for (channel, binding) in bindings {
                index.insert(
                    (channel.clone(), binding.binding_key.clone()),
                    bot_id.to_string(),
                );
            }
        }
        Ok(())
    }

    async fn update_profile_existing(
        &self,
        bot_id: &str,
        update: bcs_service_api::types::bot_profile::ExistingProfileUpdate,
    ) -> ServiceResult<bcs_service_api::types::bot_profile::ProfileWrite> {
        self.profile_writes
            .lock()
            .await
            .push((bot_id.to_string(), update.clone()));
        if self.miss_profile_writes || !self.bots.lock().await.contains_key(bot_id) {
            return Ok(bcs_service_api::types::bot_profile::ProfileWrite::Missing);
        }
        // Same publication shape as `save_to_storage`, plus the avatar
        // transition on the record's dedicated avatar field; `Keep` never
        // touches the stored avatar.
        self.save_to_storage(bot_id, &update.capabilities).await?;
        let mut avatars = self.avatars.lock().await;
        match &update.avatar {
            bcs_service_api::types::bot_profile::AvatarChange::Keep => {}
            bcs_service_api::types::bot_profile::AvatarChange::Clear => {
                avatars.insert(bot_id.to_string(), None);
            }
            bcs_service_api::types::bot_profile::AvatarChange::Set(url) => {
                avatars.insert(bot_id.to_string(), Some(url.clone()));
            }
        }
        Ok(bcs_service_api::types::bot_profile::ProfileWrite::Updated)
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        let human_id = format!("human_{}", staff_no);
        self.bots
            .lock()
            .await
            .entry(human_id.clone())
            .or_insert_with(|| RegisteredBot {
                bot_uuid: human_id,
                capabilities: BotCapabilities {
                    name: Some(nick_name.to_string()),
                    visibility: "protected".to_string(),
                    ..Default::default()
                },
                env: None,
                created_by: Some(staff_no.to_string()),
                actor_kind: ActorKind::Human,
                status: ActorStatus::Online,
            });
        Ok(EnsureHumanResult { created: true })
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.onboarded.lock().await.contains(bot_id)
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        self.saved_created_by.lock().await.push((
            bot_id.to_string(),
            created_by.to_string(),
            overwrite,
        ));
        if let Some(bot) = self.bots.lock().await.get_mut(bot_id) {
            if overwrite || bot.created_by.is_none() {
                bot.created_by = Some(created_by.to_string());
            }
        }
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(
        &self,
        _existing_token: String,
    ) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        self.binding_index
            .lock()
            .await
            .get(&(channel.to_string(), binding_key.to_string()))
            .cloned()
    }
}

fn bot(bot_uuid: impl Into<String>, capabilities: BotCapabilities) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: bot_uuid.into(),
        capabilities,
        env: None,
        created_by: None,
        actor_kind: ActorKind::Bot,
        status: ActorStatus::Online,
    }
}

fn binding(key: &str) -> BindingChannels {
    let mut bindings = BindingChannels::new();
    bindings.insert(
        "antding".to_string(),
        BindingChannel {
            binding_key: key.to_string(),
        },
    );
    bindings
}

fn command(bot_uuid: &str) -> BotOnboardCommand {
    BotOnboardCommand {
        bot_uuid: bot_uuid.to_string(),
        name: "Bot".to_string(),
        summary: Some("summary".to_string()),
        domains: Vec::new(),
        skills: Vec::new(),
        scopes: Vec::new(),
        binding_channels: None,
        agent_code: None,
        agent_token: None,
        actor_identity: None,
    }
}
