//! In-memory channel repository implementations.

use std::path::PathBuf;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::info;

use bcs_domain::{
    BindingStatus, BindingTarget, ChannelBinding, ChannelType, ConversationSessionMap,
    HumanInputRequest, HumanInputRequestStatus, ImParticipantMap, SessionScope,
};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, HumanInputEnqueueDisposition,
    HumanInputRequestRepoPort, ImParticipantRepoPort,
};

const CHANNEL_BINDINGS_FILE: &str = "channel_bindings.json";
const CHANNEL_CONVERSATIONS_FILE: &str = "channel_conversations.json";
const CHANNEL_IM_PARTICIPANTS_FILE: &str = "channel_im_participants.json";
const HUMAN_INPUT_REQUESTS_FILE: &str = "human_input_requests.json";

/// In-memory implementation of [`ChannelBindingRepoPort`].
#[derive(Debug)]
pub struct MemoryChannelBindingRepo {
    bindings: RwLock<Vec<ChannelBinding>>,
    data_dir: Option<PathBuf>,
    env: String,
}

impl MemoryChannelBindingRepo {
    pub fn new(env: impl Into<String>) -> Self {
        Self {
            bindings: RwLock::new(Vec::new()),
            data_dir: None,
            env: env.into(),
        }
    }

    pub fn with_data_dir(data_dir: PathBuf, env: impl Into<String>) -> Self {
        Self {
            bindings: RwLock::new(Vec::new()),
            data_dir: Some(data_dir),
            env: env.into(),
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

#[async_trait]
impl ChannelBindingRepoPort for MemoryChannelBindingRepo {
    async fn create(&self, binding: ChannelBinding) -> ServiceResult<()> {
        // 以下为安全注释COSEC：拒绝跨环境写入，避免调用方绕过 repository 的环境隔离。
        if binding.env != self.env {
            return Err(ServiceError::InternalError(format!(
                "channel binding env '{}' does not match repository env '{}'",
                binding.env, self.env
            )));
        }
        self.bindings.write().await.push(binding);
        self.save_to_disk().await
    }

    async fn get(&self, id: &str) -> ServiceResult<Option<ChannelBinding>> {
        let bindings = self.bindings.read().await;
        Ok(bindings
            .iter()
            .find(|binding| binding.id == id && binding.env == self.env)
            .cloned())
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
                    && binding.env == self.env
            })
            .cloned())
    }

    async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
        Ok(self
            .bindings
            .read()
            .await
            .iter()
            .filter(|binding| binding.env == self.env)
            .cloned()
            .collect())
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
                binding.env == self.env
                    && binding.target == *target
                    && channel_type
                        .map(|expected| binding.channel_type == expected)
                        .unwrap_or(true)
            })
            .cloned()
            .collect())
    }

    async fn delete_by_target(&self, target: &BindingTarget) -> ServiceResult<u64> {
        // 以下为安全注释COSEC：删除范围固定为 repository env，禁止调用方选择其他环境。
        let removed = {
            let mut bindings = self.bindings.write().await;
            let original_len = bindings.len();
            bindings.retain(|binding| binding.target != *target || binding.env != self.env);
            (original_len - bindings.len()) as u64
        };
        self.save_to_disk().await?;
        Ok(removed)
    }

    async fn set_status(&self, id: &str, active: bool) -> ServiceResult<()> {
        {
            let mut bindings = self.bindings.write().await;
            if let Some(binding) = bindings
                .iter_mut()
                .find(|binding| binding.id == id && binding.env == self.env)
            {
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
            if let Some(binding) = bindings
                .iter_mut()
                .find(|binding| binding.id == id && binding.env == self.env)
            {
                binding.config = config;
            }
        }
        self.save_to_disk().await
    }

    async fn delete(&self, id: &str) -> ServiceResult<()> {
        self.bindings
            .write()
            .await
            .retain(|binding| binding.id != id || binding.env != self.env);
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
                Self::key_matches(
                    map,
                    binding_id,
                    im_conversation_id,
                    session_scope,
                    im_user_id,
                )
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

    async fn delete_if_session(
        &self,
        binding_id: &str,
        im_conversation_id: &str,
        session_scope: SessionScope,
        im_user_id: Option<&str>,
        expected_bcs_session_id: &str,
    ) -> ServiceResult<bool> {
        let deleted = {
            let mut maps = self.maps.write().await;
            let before = maps.len();
            maps.retain(|map| {
                !(Self::key_matches(
                    map,
                    binding_id,
                    im_conversation_id,
                    session_scope,
                    im_user_id,
                ) && map.bcs_session_id == expected_bcs_session_id)
            });
            maps.len() != before
        };
        if deleted {
            self.save_to_disk().await?;
        }
        Ok(deleted)
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

    async fn find_by_actor(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
        actor_id: &str,
    ) -> ServiceResult<Vec<ImParticipantMap>> {
        let maps = self.maps.read().await;
        Ok(maps
            .iter()
            .filter(|map| {
                map.channel_type == channel_type
                    && map.account_ref == account_ref
                    && map.actor_id == actor_id
            })
            .cloned()
            .collect())
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

/// In-memory implementation of [`HumanInputRequestRepoPort`].
#[derive(Debug)]
pub struct MemoryHumanInputRequestRepo {
    requests: RwLock<Vec<HumanInputRequest>>,
    data_dir: Option<PathBuf>,
}

impl MemoryHumanInputRequestRepo {
    pub fn new() -> Self {
        Self {
            requests: RwLock::new(Vec::new()),
            data_dir: None,
        }
    }

    pub fn with_data_dir(data_dir: PathBuf) -> Self {
        Self {
            requests: RwLock::new(Vec::new()),
            data_dir: Some(data_dir),
        }
    }

    pub async fn load_from_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        let path = dir.join(HUMAN_INPUT_REQUESTS_FILE);
        if !path.exists() {
            return Ok(());
        }
        let data = tokio::fs::read_to_string(&path).await?;
        let loaded: Vec<HumanInputRequest> = serde_json::from_str(&data)?;
        *self.requests.write().await = loaded;
        Ok(())
    }

    async fn save_to_disk(&self) -> ServiceResult<()> {
        let Some(ref dir) = self.data_dir else {
            return Ok(());
        };
        tokio::fs::create_dir_all(dir).await?;
        let requests = self.requests.read().await;
        let data = serde_json::to_string_pretty(&*requests)?;
        tokio::fs::write(dir.join(HUMAN_INPUT_REQUESTS_FILE), data).await?;
        Ok(())
    }

    fn occupies_slot(request: &HumanInputRequest) -> bool {
        matches!(
            request.status,
            HumanInputRequestStatus::Notifying | HumanInputRequestStatus::Active
        )
    }
}

impl Default for MemoryHumanInputRequestRepo {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl HumanInputRequestRepoPort for MemoryHumanInputRequestRepo {
    async fn enqueue(
        &self,
        mut request: HumanInputRequest,
    ) -> ServiceResult<HumanInputEnqueueDisposition> {
        let disposition = {
            let mut requests = self.requests.write().await;
            if let Some(existing) = requests
                .iter()
                .find(|existing| existing.request_id == request.request_id)
            {
                return Ok(if Self::occupies_slot(existing) {
                    HumanInputEnqueueDisposition::Notifying
                } else {
                    HumanInputEnqueueDisposition::Queued
                });
            }
            let occupied = requests.iter().any(|existing| {
                existing.reply_scope_key == request.reply_scope_key
                    && Self::occupies_slot(existing)
            });
            if occupied {
                request.status = HumanInputRequestStatus::Queued;
                request.active_slot_key = None;
                requests.push(request);
                HumanInputEnqueueDisposition::Queued
            } else {
                request.status = HumanInputRequestStatus::Notifying;
                request.active_slot_key = Some(request.reply_scope_key.clone());
                requests.push(request);
                HumanInputEnqueueDisposition::Notifying
            }
        };
        self.save_to_disk().await?;
        Ok(disposition)
    }

    async fn get(&self, request_id: &str) -> ServiceResult<Option<HumanInputRequest>> {
        Ok(self
            .requests
            .read()
            .await
            .iter()
            .find(|request| request.request_id == request_id)
            .cloned())
    }

    async fn list_by_run(&self, run_id: &str) -> ServiceResult<Vec<HumanInputRequest>> {
        Ok(self
            .requests
            .read()
            .await
            .iter()
            .filter(|request| request.run_id == run_id)
            .cloned()
            .collect())
    }

    async fn find_active_by_scope(
        &self,
        reply_scope_key: &str,
    ) -> ServiceResult<Option<HumanInputRequest>> {
        Ok(self
            .requests
            .read()
            .await
            .iter()
            .find(|request| {
                request.reply_scope_key == reply_scope_key
                    && request.status == HumanInputRequestStatus::Active
            })
            .cloned())
    }

    async fn mark_active(
        &self,
        request_id: &str,
        provider_message_ref: Option<&str>,
        activated_at: u64,
    ) -> ServiceResult<bool> {
        let updated = {
            let mut requests = self.requests.write().await;
            let Some(request) = requests.iter_mut().find(|request| {
                request.request_id == request_id
                    && request.status == HumanInputRequestStatus::Notifying
            }) else {
                return Ok(false);
            };
            request.status = HumanInputRequestStatus::Active;
            request.provider_message_ref = provider_message_ref.map(str::to_string);
            request.delivery_attempts = request.delivery_attempts.saturating_add(1);
            request.activated_at = Some(activated_at);
            true
        };
        self.save_to_disk().await?;
        Ok(updated)
    }

    async fn mark_delivery_failed(
        &self,
        request_id: &str,
        error: &str,
    ) -> ServiceResult<bool> {
        let updated = {
            let mut requests = self.requests.write().await;
            let Some(request) = requests.iter_mut().find(|request| {
                request.request_id == request_id
                    && request.status == HumanInputRequestStatus::Notifying
            }) else {
                return Ok(false);
            };
            request.status = HumanInputRequestStatus::DeliveryFailed;
            request.active_slot_key = None;
            request.delivery_attempts = request.delivery_attempts.saturating_add(1);
            request.last_delivery_error = Some(error.to_string());
            true
        };
        self.save_to_disk().await?;
        Ok(updated)
    }

    async fn mark_responded(
        &self,
        request_id: &str,
        responded_at: u64,
    ) -> ServiceResult<bool> {
        let updated = {
            let mut requests = self.requests.write().await;
            let Some(request) = requests.iter_mut().find(|request| {
                request.request_id == request_id
                    && request.status == HumanInputRequestStatus::Active
            }) else {
                return Ok(false);
            };
            request.status = HumanInputRequestStatus::Responded;
            request.active_slot_key = None;
            request.responded_at = Some(responded_at);
            true
        };
        self.save_to_disk().await?;
        Ok(updated)
    }

    async fn promote_next(
        &self,
        reply_scope_key: &str,
        now_ms: u64,
    ) -> ServiceResult<Option<HumanInputRequest>> {
        let promoted = {
            let mut requests = self.requests.write().await;
            for request in requests.iter_mut().filter(|request| {
                request.reply_scope_key == reply_scope_key
                    && request.status == HumanInputRequestStatus::Queued
                    && request.deadline_ms <= now_ms
            }) {
                request.status = HumanInputRequestStatus::Expired;
            }
            if requests.iter().any(|request| {
                request.reply_scope_key == reply_scope_key && Self::occupies_slot(request)
            }) {
                None
            } else {
                let next_index = requests
                    .iter()
                    .enumerate()
                    .filter(|(_, request)| {
                        request.reply_scope_key == reply_scope_key
                            && request.status == HumanInputRequestStatus::Queued
                    })
                    .min_by_key(|(_, request)| {
                        (request.deadline_ms, request.created_at, request.request_id.as_str())
                    })
                    .map(|(index, _)| index);
                next_index.map(|index| {
                    let request = &mut requests[index];
                    request.status = HumanInputRequestStatus::Notifying;
                    request.active_slot_key = Some(reply_scope_key.to_string());
                    request.clone()
                })
            }
        };
        self.save_to_disk().await?;
        Ok(promoted)
    }

    async fn count_queued(&self, reply_scope_key: &str) -> ServiceResult<usize> {
        Ok(self
            .requests
            .read()
            .await
            .iter()
            .filter(|request| {
                request.reply_scope_key == reply_scope_key
                    && request.status == HumanInputRequestStatus::Queued
            })
            .count())
    }

    async fn close_for_run_node(
        &self,
        run_id: &str,
        node_id: &str,
        status: HumanInputRequestStatus,
    ) -> ServiceResult<u64> {
        if !matches!(
            status,
            HumanInputRequestStatus::Expired | HumanInputRequestStatus::Cancelled
        ) {
            return Err(ServiceError::InvalidOperation {
                message: "HumanInput request can only be closed as expired or cancelled"
                    .to_string(),
                request_id: None,
            });
        }
        let updated = {
            let mut requests = self.requests.write().await;
            let mut updated = 0_u64;
            for request in requests.iter_mut().filter(|request| {
                request.run_id == run_id
                    && request.node_id == node_id
                    && matches!(
                        request.status,
                        HumanInputRequestStatus::Queued
                            | HumanInputRequestStatus::Notifying
                            | HumanInputRequestStatus::Active
                    )
            }) {
                request.status = status;
                request.active_slot_key = None;
                updated += 1;
            }
            updated
        };
        if updated > 0 {
            self.save_to_disk().await?;
        }
        Ok(updated)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_domain::{
        BindingTarget, GroupChatScope, HumanInputNotificationMode, Visibility,
    };

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

    fn human_input_request(id: &str, scope: &str, created_at: u64) -> HumanInputRequest {
        HumanInputRequest {
            request_id: id.to_string(),
            session_id: format!("session-{id}"),
            run_id: format!("run-{id}"),
            node_id: "review".to_string(),
            binding_id: "binding_1".to_string(),
            channel_type: "dingtalk".to_string(),
            account_ref: "robot_1".to_string(),
            notification_mode: HumanInputNotificationMode::FixedGroup,
            reply_scope_key: scope.to_string(),
            active_slot_key: None,
            assignee_actor_id: "human_1".to_string(),
            im_conversation_id: "cid_1".to_string(),
            im_conversation_type: "2".to_string(),
            im_user_id: None,
            node_display_name: "Review".to_string(),
            notification_text: "Please review".to_string(),
            deadline_ms: 10_000,
            status: HumanInputRequestStatus::Queued,
            provider_message_ref: None,
            delivery_attempts: 0,
            last_delivery_error: None,
            created_at,
            activated_at: None,
            responded_at: None,
        }
    }

    #[tokio::test]
    async fn find_active_by_account_matches_active_only() -> ServiceResult<()> {
        let repo = MemoryChannelBindingRepo::new("dev");

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
        assert_eq!(
            active.as_ref().map(|binding| binding.id.as_str()),
            Some("binding_active")
        );

        let disabled = repo
            .find_active_by_account("dingtalk".to_string(), "robot_disabled")
            .await?;
        assert_eq!(disabled, None);

        Ok(())
    }

    #[tokio::test]
    async fn delete_removes_binding() -> ServiceResult<()> {
        let repo = MemoryChannelBindingRepo::new("dev");

        repo.create(binding(
            "binding_delete",
            "robot_delete",
            BindingStatus::Active,
        ))
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
            .get(
                "binding_1",
                "conversation_1",
                SessionScope::Conversation,
                None,
            )
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
        let reverse = repo
            .find_by_actor("dingtalk".to_string(), "robot_1", "actor_new")
            .await?;
        assert_eq!(reverse.len(), 1);
        assert_eq!(reverse[0].im_user_id, "staff_1");

        Ok(())
    }

    #[tokio::test]
    async fn human_input_requests_serialize_and_promote_fifo_per_scope() -> ServiceResult<()> {
        let repo = MemoryHumanInputRequestRepo::new();
        assert_eq!(
            repo.enqueue(human_input_request("first", "scope-a", 10))
                .await?,
            HumanInputEnqueueDisposition::Notifying
        );
        assert_eq!(
            repo.enqueue(human_input_request("second", "scope-a", 20))
                .await?,
            HumanInputEnqueueDisposition::Queued
        );
        assert_eq!(
            repo.enqueue(human_input_request("parallel", "scope-b", 30))
                .await?,
            HumanInputEnqueueDisposition::Notifying
        );
        assert!(repo.mark_active("first", Some("card-1"), 40).await?);
        assert!(repo.mark_active("parallel", None, 41).await?);
        assert_eq!(repo.count_queued("scope-a").await?, 1);
        assert!(repo.mark_responded("first", 50).await?);

        let promoted = repo
            .promote_next("scope-a", 50)
            .await?
            .expect("queued request should be promoted");
        assert_eq!(promoted.request_id, "second");
        assert_eq!(promoted.status, HumanInputRequestStatus::Notifying);
        assert_eq!(promoted.active_slot_key.as_deref(), Some("scope-a"));
        assert!(
            repo.find_active_by_scope("scope-a").await?.is_none(),
            "notifying requests must not consume user replies before delivery is confirmed"
        );
        assert!(repo.mark_active("second", Some("card-2"), 60).await?);
        assert_eq!(
            repo.find_active_by_scope("scope-a")
                .await?
                .map(|request| request.request_id),
            Some("second".to_string())
        );
        Ok(())
    }

    #[tokio::test]
    async fn binding_repo_persists_to_disk() -> Result<(), Box<dyn std::error::Error>> {
        let data_dir = tempfile::tempdir()?;
        let path = data_dir.path().to_path_buf();

        let repo = MemoryChannelBindingRepo::with_data_dir(path.clone(), "dev");
        repo.create(binding(
            "binding_persisted",
            "robot_persisted",
            BindingStatus::Active,
        ))
        .await?;

        let loaded_repo = MemoryChannelBindingRepo::with_data_dir(path, "dev");
        loaded_repo.load_from_disk().await?;

        let loaded = loaded_repo.get("binding_persisted").await?;
        assert_eq!(
            loaded.as_ref().map(|binding| binding.account_ref.as_str()),
            Some("robot_persisted")
        );

        Ok(())
    }

    #[tokio::test]
    async fn binding_repo_isolates_environment_reads_and_writes(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let data_dir = tempfile::tempdir()?;
        let path = data_dir.path().to_path_buf();

        let pre_repo = MemoryChannelBindingRepo::with_data_dir(path.clone(), "pre");
        let mut pre_binding = binding("binding_pre", "robot_shared", BindingStatus::Active);
        pre_binding.env = "pre".to_string();
        pre_repo.create(pre_binding.clone()).await?;

        let prod_repo = MemoryChannelBindingRepo::with_data_dir(path.clone(), "prod");
        prod_repo.load_from_disk().await?;
        let mut prod_binding = binding("binding_prod", "robot_shared", BindingStatus::Active);
        prod_binding.env = "prod".to_string();
        prod_repo.create(prod_binding.clone()).await?;

        assert_eq!(prod_repo.list().await?, vec![prod_binding.clone()]);
        assert_eq!(prod_repo.get("binding_pre").await?, None);
        prod_repo.set_status("binding_pre", false).await?;
        prod_repo
            .set_config("binding_pre", serde_json::json!({"changed": true}))
            .await?;
        prod_repo.delete("binding_pre").await?;
        assert_eq!(prod_repo.delete_by_target(&prod_binding.target).await?, 1);

        let reloaded_pre = MemoryChannelBindingRepo::with_data_dir(path, "pre");
        reloaded_pre.load_from_disk().await?;
        assert_eq!(reloaded_pre.list().await?, vec![pre_binding]);
        assert_eq!(reloaded_pre.get("binding_prod").await?, None);

        let mut mismatched = binding(
            "binding_mismatched",
            "robot_mismatched",
            BindingStatus::Active,
        );
        mismatched.env = "prod".to_string();
        let error = reloaded_pre
            .create(mismatched)
            .await
            .expect_err("repository must reject a cross-environment write");
        assert!(error.to_string().contains("does not match repository env"));

        Ok(())
    }
}
