//! Shared proposal-fixture fakes (used by
//! `use_cases.rs` and `authority_cutover.rs`).
#![allow(clippy::expect_used, clippy::unwrap_used, dead_code)]

use std::{
    collections::{HashMap, HashSet},
    sync::atomic::{AtomicUsize, Ordering},
    sync::RwLock,
};

use tokio::sync::Mutex;

use async_trait::async_trait;

use bcs_service_api::{
    ActorKind, AgentCredentials, BotCapabilities, BotRegistryCoreService,
    CreateOrReactivateCommand, CreateOrReactivateOutcome, DmActorSpec,
    FriendCoreService, Group, GroupCoreService, GroupKind, GroupMessage,
    GroupStatus, Participant, ParticipantMode, RegisteredBot, ServiceError,
    ServiceResult, Session, SessionKind, SessionManagementService,
    SessionStatus, SessionUseCaseError, SystemMessageEvent,
    SystemMessageService,
};

pub fn bot(bot_uuid: &str, name: &str, visibility: &str, created_by: Option<&str>) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: bot_uuid.to_string(),
        capabilities: BotCapabilities {
            name: Some(name.to_string()),
            visibility: visibility.to_string(),
            ..Default::default()
        },
        env: None,
        created_by: created_by.map(str::to_string),
        actor_kind: ActorKind::Bot,
        status: Default::default(),
    }
}

#[derive(Default)]
pub struct FakeRegistry {
    bots: RwLock<HashMap<String, RegisteredBot>>,
    /// dev #2654 scope fixture: when set, `collaboration_owner`
    /// only admits the alice-owned actor set.
    pub same_owner: std::sync::atomic::AtomicBool,
}

impl FakeRegistry {

    pub fn insert(&self, bot: RegisteredBot) {
        self.bots.write().unwrap().insert(bot.bot_uuid.clone(), bot);
    }
}

#[async_trait]
impl BotRegistryCoreService for FakeRegistry {
    async fn collaboration_owner(&self, actor_ids: &[String]) -> ServiceResult<Option<String>> {
        if !self.same_owner.load(Ordering::SeqCst) {
            return Ok(None);
        }
        let bots = self.bots.read().unwrap();
        if actor_ids.iter().any(|id| {
            id.strip_prefix("human_")
                .or_else(|| bots.get(id).and_then(|b| b.created_by.as_deref()))
                != Some("alice")
        }) {
            return Err(ServiceError::Forbidden("same_owner".into()));
        }
        Ok(Some("alice".into()))
    }

    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.bots.write().unwrap().insert(
            bot_id.clone(),
            RegisteredBot {
                bot_uuid: bot_id,
                capabilities,
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: Default::default(),
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
        true
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.read().unwrap().get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.read().unwrap().values().cloned().collect()
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        created_by: &str,
        limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        Ok(self
            .list_bots_by_creator(created_by)
            .await
            .into_iter()
            .take(limit)
            .collect())
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .unwrap()
            .values()
            .filter(|bot| bot.created_by.as_deref() == Some(created_by))
            .cloned()
            .collect()
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .unwrap()
            .values()
            .filter(|bot| {
                bot.bot_uuid != query
                    || bot
                        .capabilities
                        .summary
                        .as_deref()
                        .unwrap_or_default()
                        .contains(query)
            })
            .cloned()
            .collect()
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

    async fn unregister(&self, bot_id: &str) -> bool {
        self.bots.write().unwrap().remove(bot_id).is_some()
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().unwrap().get_mut(bot_id) {
            bot.capabilities.visibility = visibility.to_string();
        }
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.bots.read().unwrap().contains_key(bot_id)
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().unwrap().get_mut(bot_id) {
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

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
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
}

#[derive(Default)]
pub struct FakeFriendCoreService {
    pairs: RwLock<HashSet<(String, String)>>,
}

impl FakeFriendCoreService {
    pub fn insert(&self, a: &str, b: &str) {
        self.pairs.write().unwrap().insert(canonical_pair(a, b));
    }
}

#[async_trait]
impl FriendCoreService for FakeFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.pairs
            .read()
            .unwrap()
            .iter()
            .filter_map(|(a, b)| {
                if a == bot_id {
                    Some(b.clone())
                } else if b == bot_id {
                    Some(a.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.pairs
            .read()
            .unwrap()
            .contains(&canonical_pair(bot_a, bot_b))
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let pairs = self.pairs.read().unwrap();
        let not_friends: Vec<String> = others
            .iter()
            .filter(|other| !pairs.contains(&canonical_pair(bot_id, other)))
            .cloned()
            .collect();
        if not_friends.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(not_friends))
        }
    }

    async fn add_friendship(&self, bot_a: &str, bot_b: &str) -> ServiceResult<()> {
        self.pairs
            .write()
            .unwrap()
            .insert(canonical_pair(bot_a, bot_b));
        Ok(())
    }

    async fn remove_all_friendships(&self, bot_id: &str) -> ServiceResult<usize> {
        let mut pairs = self.pairs.write().unwrap();
        let before = pairs.len();
        pairs.retain(|(a, b)| a != bot_id && b != bot_id);
        Ok(before - pairs.len())
    }
}

#[derive(Default)]
pub struct FakeGroupStore {
    groups: RwLock<HashMap<String, Group>>,
pub(crate) fail_upsert: RwLock<bool>,
}

impl FakeGroupStore {
    pub fn fail_upsert(&self) {
        *self.fail_upsert.write().unwrap() = true;
    }
}

#[async_trait]
impl GroupCoreService for FakeGroupStore {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        if *self.fail_upsert.read().unwrap() {
            return Err(ServiceError::InvalidOperation {
                message: "group persistence failed".to_string(),
                request_id: None,
            });
        }
        self.groups.write().unwrap().insert(group.id.clone(), group);
        Ok(())
    }

    async fn get(&self, id: &str) -> Option<Group> {
        self.groups.read().unwrap().get(id).cloned()
    }

    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.messages.push(message);
        Ok(())
    }

    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.participants.push(participant);
        Ok(())
    }

    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(group_id)
            .ok_or_else(|| ServiceError::GroupNotFound(group_id.to_string()))?;
        group.participants.retain(|p| p.bot_uuid != bot_uuid);
        Ok(())
    }

    async fn update_participant_mode(
        &self,
        id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        let participant = group
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| ServiceError::BotNotFound(actor_id.to_string()))?;
        participant.mode = Some(mode);
        Ok(())
    }


    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.label = label;
        Ok(())
    }

    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.status = status;
        Ok(())
    }

    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<bcs_service_api::ServiceSpec>,
    ) -> ServiceResult<()> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.service_spec = service_spec;
        Ok(())
    }

    async fn terminate(&self, id: &str, _caller_bot_id: &str) -> ServiceResult<Group> {
        let mut groups = self.groups.write().unwrap();
        let group = groups
            .get_mut(id)
            .ok_or_else(|| ServiceError::GroupNotFound(id.to_string()))?;
        group.status = GroupStatus::Completed;
        Ok(group.clone())
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        Ok(self.groups.write().unwrap().remove(id))
    }

    async fn list(&self) -> Vec<Group> {
        self.groups.read().unwrap().values().cloned().collect()
    }

    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group> {
        let mut groups = self
            .groups
            .read()
            .unwrap()
            .values()
            .cloned()
            .collect::<Vec<_>>();
        Group::sort_by_updated_at_desc(&mut groups);
        groups
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.groups
            .read()
            .unwrap()
            .values()
            .filter(|group| {
                group
                    .participants
                    .iter()
                    .any(|participant| participant.bot_uuid == bot_uuid)
            })
            .cloned()
            .collect()
    }

    async fn count(&self) -> u64 {
        self.groups.read().unwrap().len() as u64
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.find_by_participant(bot_uuid).await.len() as u64
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        let mut groups = self.find_by_participant(bot_uuid).await;
        Group::sort_by_updated_at_desc(&mut groups);
        groups
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }

    async fn message_count(&self, id: &str) -> ServiceResult<usize> {
        Ok(self
            .groups
            .read()
            .unwrap()
            .get(id)
            .map(|group| group.messages.len())
            .unwrap_or(0))
    }

    async fn increment_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn reset_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        _id: &str,
        _actor_a: DmActorSpec,
        _actor_b: DmActorSpec,
        _legacy_driver_bot: &str,
        _originator_actor_id: &str,
        _label: Option<String>,
        _context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        Err(ServiceError::InternalError(
            "dm group creation is not supported by FakeGroupStore".to_string(),
        ))
    }

    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64 {
        self.groups
            .read()
            .unwrap()
            .values()
            .filter(|group| kind.is_none_or(|kind| group.group_kind == kind))
            .count() as u64
    }
}

#[derive(Default)]
pub struct RecordingSessionManagement {
pub(crate) commands: Mutex<Vec<CreateOrReactivateCommand>>,
}

#[async_trait]
impl SessionManagementService for RecordingSessionManagement {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        self.commands.lock().await.push(cmd.clone());
        Ok(CreateOrReactivateOutcome {
            session: test_session(
                &format!("{}:initial", cmd.group_id),
                &cmd.group_id,
                cmd.params.participants.clone(),
                cmd.params.session_kind,
                cmd.params.group_version,
            ),
            created: true,
        })
    }

    async fn get(&self, _session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not implemented".to_string()))
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }
}

#[derive(Default)]
pub struct RecordingSystemMessageService {
    successful_deliveries: AtomicUsize,
pub(crate) notifications: Mutex<Vec<RecordingSystemMessageNotification>>,
}

impl RecordingSystemMessageService {
    pub fn set_successful_deliveries(&self, value: usize) {
        self.successful_deliveries.store(value, Ordering::SeqCst);
    }
}

pub struct RecordingSystemMessageNotification {
    pub(crate) group_id: String,
    pub(crate) event: SystemMessageEvent,
    pub(crate) session_id: String,
    pub(crate) participants: Vec<Participant>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessageService {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.notifications.lock().await.push(RecordingSystemMessageNotification {
            group_id: group_id.to_string(),
            event,
            session_id: session_id.to_string(),
            participants: session_participants.to_vec(),
        });
        Ok(self.successful_deliveries.load(Ordering::SeqCst))
    }
}

pub fn test_session(
    session_id: &str,
    group_id: &str,
    participants: Vec<Participant>,
    session_kind: SessionKind,
    group_version: Option<i32>,
) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind,
        participants,
        group_version,
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        message_visibility_version: 1,
        caller_principal: None,
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        collected_at: None,
    }
}

pub fn canonical_pair(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
