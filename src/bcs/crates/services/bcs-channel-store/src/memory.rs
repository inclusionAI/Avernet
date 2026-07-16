//! In-memory channel repository implementations.

use std::path::PathBuf;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::info;

use bcs_domain::{
    BindingStatus, BindingTarget, ChannelBinding, ChannelType, ConversationSessionMap,
    ImParticipantMap, SessionScope,
};
use bcs_service_api::ServiceResult;
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, ImParticipantRepoPort,
};

const CHANNEL_BINDINGS_FILE: &str = "channel_bindings.json";
const CHANNEL_CONVERSATIONS_FILE: &str = "channel_conversations.json";
const CHANNEL_IM_PARTICIPANTS_FILE: &str = "channel_im_participants.json";

/// In-memory implementation of [`ChannelBindingRepoPort`].
#[derive(Debug)]
pub struct MemoryChannelBindingRepo {
    bindings: RwLock<Vec<ChannelBinding>>,
    data_dir: Option<PathBuf>,
}

impl MemoryChannelBindingRepo {
    pub fn new() -> Self {
        Self {
            bindings: RwLock::new(Vec::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            bindings: RwLock::new(Vec::new()),
            data_dir: Some(data_dir),
        }
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        let path = dir.join(CHANNEL_BINDINGS_FILE);
        if !path.exists() {
            return Ok(());
        }

        let data = tokio::fs::read_to_string(&path).await?;
        let loaded: Vec<ChannelBinding> = serde_json::from_str(&data)?;
        let count = loaded.len();
        *self.bindings.write().await = loaded;
        info!(count, "Loaded channel bindings from disk");
        Ok(())
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(dir).await?;
        let bindings = self.bindings.read().await;
        let data = serde_json::to_string_pretty(&*bindings)?;
        tokio::fs::write(dir.join(CHANNEL_BINDINGS_FILE), data).await?;
        Ok(())
    }
}

impl Default for MemoryChannelBindingRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ChannelBindingRepoPort for MemoryChannelBindingRepo {
    async fn create(&self, binding: ChannelBinding) -> ServiceResult<()> {
        self.bindings.write().await.push(binding);
        self.save_to_disk().await
    }

    async fn get(&self, id: &str) -> ServiceResult<Option<ChannelBinding>> {
        let bindings = self.bindings.read().await;
        Ok(bindings.iter().find(|binding| binding.id == id).cloned())
    }

    async fn find_active_by_account(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
    ) -> ServiceResult<Option<ChannelBinding>> {
        let bindings = self.bindings.read().await;
        Ok(bindings
            .iter()
            .find(|binding| {
                binding.channel_type == channel_type
                    && binding.account_ref == account_ref
                    && binding.status == BindingStatus::Active
            })
            .cloned())
    }

    async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
        Ok(self.bindings.read().await.clone())
    }

    async fn list_by_target(
        &self,
        target: &BindingTarget,
        channel_type: Option<&str>,
    ) -> ServiceResult<Vec<ChannelBinding>> {
        let bindings = self.bindings.read().await;
        Ok(bindings
            .iter()
            .filter(|binding| {
                binding.target == *target
                    && channel_type
                        .map(|expected| binding.channel_type == expected)
                        .unwrap_or(true)
            })
            .cloned()
            .collect())
    }

    async fn set_status(&self, id: &str, active: bool) -> ServiceResult<()> {
        {
            let mut bindings = self.bindings.write().await;
            if let Some(binding) = bindings.iter_mut().find(|binding| binding.id == id) {
                binding.status = if active {
                    BindingStatus::Active
                } else {
                    BindingStatus::Disabled
                };
            }
        }
        self.save_to_disk().await
    }

    async fn set_config(&self, id: &str, config: serde_json::Value) -> ServiceResult<()> {
        {
            let mut bindings = self.bindings.write().await;
            if let Some(binding) = bindings.iter_mut().find(|binding| binding.id == id) {
                binding.config = config;
            }
        }
        self.save_to_disk().await
    }

    async fn delete(&self, id: &str) -> ServiceResult<()> {
        self.bindings
            .write()
            .await
            .retain(|binding| binding.id != id);
        self.save_to_disk().await
    }
}

/// In-memory implementation of [`ConversationSessionRepoPort`].
#[derive(Debug)]
pub struct MemoryConversationSessionRepo {
    maps: RwLock<Vec<ConversationSessionMap>>,
    data_dir: Option<PathBuf>,
}

impl MemoryConversationSessionRepo {
    pub fn new() -> Self {
        Self {
            maps: RwLock::new(Vec::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            maps: RwLock::new(Vec::new()),
            data_dir: Some(data_dir),
        }
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        let path = dir.join(CHANNEL_CONVERSATIONS_FILE);
        if !path.exists() {
            return Ok(());
        }

        let data = tokio::fs::read_to_string(&path).await?;
        let loaded: Vec<ConversationSessionMap> = serde_json::from_str(&data)?;
        let count = loaded.len();
        *self.maps.write().await = loaded;
        info!(count, "Loaded channel conversation mappings from disk");
        Ok(())
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(dir).await?;
        let maps = self.maps.read().await;
        let data = serde_json::to_string_pretty(&*maps)?;
        tokio::fs::write(dir.join(CHANNEL_CONVERSATIONS_FILE), data).await?;
        Ok(())
    }

    fn key_matches(
        map: &ConversationSessionMap,
        binding_id: &str,
        im_conversation_id: &str,
        session_scope: SessionScope,
        im_user_id: Option<&str>,
    ) -> bool {
        map.binding_id == binding_id
            && map.im_conversation_id == im_conversation_id
            && map.session_scope == session_scope
            && map.im_user_id.as_deref() == im_user_id
    }
}

impl Default for MemoryConversationSessionRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ConversationSessionRepoPort for MemoryConversationSessionRepo {
    async fn get(
        &self,
        binding_id: &str,
        im_conversation_id: &str,
        session_scope: SessionScope,
        im_user_id: Option<&str>,
    ) -> ServiceResult<Option<ConversationSessionMap>> {
        let maps = self.maps.read().await;
        Ok(maps
            .iter()
            .find(|map| {
                Self::key_matches(map, binding_id, im_conversation_id, session_scope, im_user_id)
            })
            .cloned())
    }

    async fn find_by_session(
        &self,
        binding_id: &str,
        bcs_session_id: &str,
    ) -> ServiceResult<Option<ConversationSessionMap>> {
        let maps = self.maps.read().await;
        Ok(maps
            .iter()
            .find(|map| map.binding_id == binding_id && map.bcs_session_id == bcs_session_id)
            .cloned())
    }

    async fn list_by_bcs_session(
        &self,
        bcs_session_id: &str,
    ) -> ServiceResult<Vec<ConversationSessionMap>> {
        let maps = self.maps.read().await;
        Ok(maps
            .iter()
            .filter(|map| map.bcs_session_id == bcs_session_id)
            .cloned()
            .collect())
    }

    async fn upsert(&self, map: ConversationSessionMap) -> ServiceResult<()> {
        {
            let mut maps = self.maps.write().await;
            maps.retain(|existing| {
                !Self::key_matches(
                    existing,
                    &map.binding_id,
                    &map.im_conversation_id,
                    map.session_scope,
                    map.im_user_id.as_deref(),
                )
            });
            maps.push(map);
        }
        self.save_to_disk().await
    }
}

/// In-memory implementation of [`ImParticipantRepoPort`].
#[derive(Debug)]
pub struct MemoryImParticipantRepo {
    maps: RwLock<Vec<ImParticipantMap>>,
    data_dir: Option<PathBuf>,
}

impl MemoryImParticipantRepo {
    pub fn new() -> Self {
        Self {
            maps: RwLock::new(Vec::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            maps: RwLock::new(Vec::new()),
            data_dir: Some(data_dir),
        }
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        let path = dir.join(CHANNEL_IM_PARTICIPANTS_FILE);
        if !path.exists() {
            return Ok(());
        }

        let data = tokio::fs::read_to_string(&path).await?;
        let loaded: Vec<ImParticipantMap> = serde_json::from_str(&data)?;
        let count = loaded.len();
        *self.maps.write().await = loaded;
        info!(count, "Loaded channel IM participant mappings from disk");
        Ok(())
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(dir).await?;
        let maps = self.maps.read().await;
        let data = serde_json::to_string_pretty(&*maps)?;
        tokio::fs::write(dir.join(CHANNEL_IM_PARTICIPANTS_FILE), data).await?;
        Ok(())
    }

    fn key_matches(
        map: &ImParticipantMap,
        channel_type: &str,
        account_ref: &str,
        im_user_id: &str,
    ) -> bool {
        map.channel_type == channel_type
            && map.account_ref == account_ref
            && map.im_user_id == im_user_id
    }
}

impl Default for MemoryImParticipantRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ImParticipantRepoPort for MemoryImParticipantRepo {
    async fn get(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
        im_user_id: &str,
    ) -> ServiceResult<Option<ImParticipantMap>> {
        let maps = self.maps.read().await;
        Ok(maps
            .iter()
            .find(|map| Self::key_matches(map, &channel_type, account_ref, im_user_id))
            .cloned())
    }

    async fn upsert(&self, map: ImParticipantMap) -> ServiceResult<()> {
        {
            let mut maps = self.maps.write().await;
            maps.retain(|existing| {
                !Self::key_matches(
                    existing,
                    &map.channel_type,
                    &map.account_ref,
                    &map.im_user_id,
                )
            });
            maps.push(map);
        }
        self.save_to_disk().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_domain::{BindingTarget, GroupChatScope, Visibility};

    fn binding(id: &str, account_ref: &str, status: BindingStatus) -> ChannelBinding {
        ChannelBinding {
            id: id.to_string(),
            channel_type: "dingtalk".to_string(),
            account_ref: account_ref.to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            status,
            created_by: Some("creator".to_string()),
            config: serde_json::json!({
                "robot_code": account_ref,
                "client_id": "client_id",
                "client_secret": "sec",
                "send_mode": {
                    "mode": "normal",
                    "message_type": "markdown"
                }
            }),
        }
    }

    fn conversation_map(
        session_scope: SessionScope,
        im_user_id: Option<&str>,
        bcs_session_id: &str,
    ) -> ConversationSessionMap {
        ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conversation_1".to_string(),
            im_conversation_type: "group".to_string(),
            session_scope,
            im_user_id: im_user_id.map(str::to_string),
            bcs_session_id: bcs_session_id.to_string(),
            last_active_at: 1,
        }
    }

    fn participant(actor_id: &str, display_name: &str) -> ImParticipantMap {
        ImParticipantMap {
            channel_type: "dingtalk".to_string(),
            account_ref: "robot_1".to_string(),
            im_user_id: "staff_1".to_string(),
            actor_id: actor_id.to_string(),
            display_name: Some(display_name.to_string()),
        }
    }

    #[tokio::test]
    async fn find_active_by_account_matches_active_only() -> ServiceResult<()> {
        let repo = MemoryChannelBindingRepo::new();

        repo.create(binding(
            "binding_active",
            "robot_active",
            BindingStatus::Active,
        ))
        .await?;
        repo.create(binding(
            "binding_disabled",
            "robot_disabled",
            BindingStatus::Disabled,
        ))
        .await?;

        let active = repo
            .find_active_by_account("dingtalk".to_string(), "robot_active")
            .await?;
        assert_eq!(active.as_ref().map(|binding| binding.id.as_str()), Some("binding_active"));

        let disabled = repo
            .find_active_by_account("dingtalk".to_string(), "robot_disabled")
            .await?;
        assert_eq!(disabled, None);

        Ok(())
    }

    #[tokio::test]
    async fn delete_removes_binding() -> ServiceResult<()> {
        let repo = MemoryChannelBindingRepo::new();

        repo.create(binding("binding_delete", "robot_delete", BindingStatus::Active))
            .await?;

        let created = repo.get("binding_delete").await?;
        assert_eq!(
            created.as_ref().map(|binding| binding.id.as_str()),
            Some("binding_delete")
        );

        repo.delete("binding_delete").await?;

        let deleted = repo.get("binding_delete").await?;
        assert_eq!(deleted, None);

        Ok(())
    }

    #[tokio::test]
    async fn conversation_upsert_replaces_same_scope_only() -> ServiceResult<()> {
        let repo = MemoryConversationSessionRepo::new();

        repo.upsert(conversation_map(
            SessionScope::Conversation,
            None,
            "session_old",
        ))
        .await?;
        repo.upsert(conversation_map(
            SessionScope::PerSender,
            Some("staff_1"),
            "session_sender",
        ))
        .await?;
        repo.upsert(conversation_map(
            SessionScope::Conversation,
            None,
            "session_new",
        ))
        .await?;

        let shared = repo
            .get("binding_1", "conversation_1", SessionScope::Conversation, None)
            .await?;
        assert_eq!(
            shared.as_ref().map(|map| map.bcs_session_id.as_str()),
            Some("session_new")
        );

        let per_sender = repo
            .get(
                "binding_1",
                "conversation_1",
                SessionScope::PerSender,
                Some("staff_1"),
            )
            .await?;
        assert_eq!(
            per_sender.as_ref().map(|map| map.bcs_session_id.as_str()),
            Some("session_sender")
        );

        Ok(())
    }

    #[tokio::test]
    async fn participant_upsert_replaces_same_external_identity() -> ServiceResult<()> {
        let repo = MemoryImParticipantRepo::new();

        repo.upsert(participant("actor_old", "Old Name")).await?;
        repo.upsert(participant("actor_new", "New Name")).await?;

        let found = repo
            .get("dingtalk".to_string(), "robot_1", "staff_1")
            .await?;
        assert_eq!(
            found.as_ref().map(|map| map.actor_id.as_str()),
            Some("actor_new")
        );
        assert_eq!(
            found
                .as_ref()
                .and_then(|map| map.display_name.as_ref())
                .map(String::as_str),
            Some("New Name")
        );

        Ok(())
    }

    #[tokio::test]
    async fn binding_repo_persists_to_disk() -> Result<(), Box<dyn std::error::Error>> {
        let data_dir = tempfile::tempdir()?;
        let path = data_dir.path().to_path_buf();

        let repo = MemoryChannelBindingRepo::with_data_dir(path.clone());
        repo.create(binding(
            "binding_persisted",
            "robot_persisted",
            BindingStatus::Active,
        ))
        .await?;

        let loaded_repo = MemoryChannelBindingRepo::with_data_dir(path);
        loaded_repo.load_from_disk().await?;

        let loaded = loaded_repo.get("binding_persisted").await?;
        assert_eq!(
            loaded.as_ref().map(|binding| binding.account_ref.as_str()),
            Some("robot_persisted")
        );

        Ok(())
    }
}
