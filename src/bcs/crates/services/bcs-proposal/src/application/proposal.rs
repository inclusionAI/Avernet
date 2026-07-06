use std::{collections::HashSet, sync::Arc};

use async_trait::async_trait;
use bcs_service_api::{
    BotRegistryCoreService, CreateOrReactivateCommand, FriendCoreService, Group,
    GroupChatProposal, GroupCoreService, GroupProposalConfirmCommand, GroupProposalConfirmResult,
    GroupProposalCreateCommand, GroupProposalCreateResult, GroupProposalPreviewCommand,
    GroupProposalPreviewResult, GroupProposalService, GroupStatus, GroupUseCaseError,
    NewSessionParams, Participant, ParticipantMode, ParticipantRole, ProposalCoreService,
    RegisteredBot, ServiceError, SessionKind, SessionManagementService, SystemMessageEvent,
    SystemMessageService,
};
use tokio::sync::Mutex;

#[derive(Debug, Clone)]
pub struct GroupProposalUseCasesConfig {
    pub max_group_members: usize,
    pub max_groups_as_driver: usize,
    pub max_groups_as_member: usize,
    pub proposal_base_url: String,
    pub botchat_base_url: Option<String>,
}

impl Default for GroupProposalUseCasesConfig {
    fn default() -> Self {
        Self {
            max_group_members: 20,
            max_groups_as_driver: 10,
            max_groups_as_member: 50,
            proposal_base_url: "http://localhost:21000".to_string(),
            botchat_base_url: None,
        }
    }
}

pub struct GroupProposalUseCases {
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    proposal: Arc<dyn ProposalCoreService>,
    session_management: Arc<dyn SessionManagementService>,
    system_message: Arc<dyn SystemMessageService>,
    config: GroupProposalUseCasesConfig,
    confirm_lock: Mutex<()>,
}

impl GroupProposalUseCases {
    pub fn new(
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        proposal: Arc<dyn ProposalCoreService>,
        session_management: Arc<dyn SessionManagementService>,
        system_message: Arc<dyn SystemMessageService>,
        config: GroupProposalUseCasesConfig,
    ) -> Self {
        Self {
            group,
            registry,
            friend,
            proposal,
            session_management,
            system_message,
            config,
            confirm_lock: Mutex::new(()),
        }
    }

    pub fn with_defaults(
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        proposal: Arc<dyn ProposalCoreService>,
        session_management: Arc<dyn SessionManagementService>,
        system_message: Arc<dyn SystemMessageService>,
    ) -> Self {
        Self::new(
            group,
            registry,
            friend,
            proposal,
            session_management,
            system_message,
            Default::default(),
        )
    }

    async fn authorize_driver(
        &self,
        caller_actor_id: Option<&str>,
        driver: &RegisteredBot,
    ) -> Result<(), GroupUseCaseError> {
        let caller = caller_actor_id
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        if caller == driver.bot_uuid {
            return Ok(());
        }
        if let Some(staff_no) = caller.strip_prefix("human_") {
            if driver.created_by.as_deref() == Some(staff_no) {
                return Ok(());
            }
        }
        Err(GroupUseCaseError::Forbidden(format!(
            "Caller '{}' is not authorized as bot '{}'",
            caller, driver.bot_uuid
        )))
    }

    async fn ensure_limits(&self, proposal: &GroupChatProposal) -> Result<(), GroupUseCaseError> {
        let driver_active_count = self
            .group
            .find_by_participant(&proposal.driver_bot)
            .await
            .into_iter()
            .filter(|group| {
                group.driver_bot == proposal.driver_bot && group.status == GroupStatus::Active
            })
            .count();
        if driver_active_count >= self.config.max_groups_as_driver {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Bot '{}' already drives {} active group(s) (max {})",
                proposal.driver_bot, driver_active_count, self.config.max_groups_as_driver
            )));
        }

        if proposal.participants.len() > self.config.max_group_members {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Group would have {} members, exceeding the limit of {}",
                proposal.participants.len(),
                self.config.max_group_members
            )));
        }

        for bot_id in &proposal.participants {
            let active_count = self
                .group
                .find_by_participant(bot_id)
                .await
                .into_iter()
                .filter(|group| group.status == GroupStatus::Active)
                .count();
            if active_count >= self.config.max_groups_as_member {
                return Err(GroupUseCaseError::InvalidProposal(format!(
                    "Bot '{}' is already in {} active group(s) (max {})",
                    bot_id, active_count, self.config.max_groups_as_member
                )));
            }
        }
        Ok(())
    }

    async fn validate_target(
        &self,
        driver_bot_id: &str,
        target_bot_id: &str,
    ) -> Result<RegisteredBot, GroupUseCaseError> {
        let bot = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;
        if target_bot_id == driver_bot_id {
            return Ok(bot);
        }
        let is_friend = self.friend.are_friends(driver_bot_id, target_bot_id).await;
        match bot.capabilities.visibility.as_str() {
            "public" => Ok(bot),
            _ if is_friend => Ok(bot),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is not friends with '{}'",
                driver_bot_id, target_bot_id
            ))),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string()).into()),
        }
    }
}

#[async_trait]
impl GroupProposalService for GroupProposalUseCases {
    async fn create_proposal(
        &self,
        cmd: GroupProposalCreateCommand,
    ) -> Result<GroupProposalCreateResult, GroupUseCaseError> {
        let driver = self
            .registry
            .get(&cmd.driver_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(cmd.driver_bot_id.clone()))?;
        self.authorize_driver(cmd.caller_actor_id.as_deref(), &driver)
            .await?;

        if let Some(suggested_driver) = cmd.suggested_driver_bot_id.as_ref() {
            if suggested_driver != &cmd.driver_bot_id {
                self.registry
                    .get(suggested_driver)
                    .await
                    .ok_or_else(|| ServiceError::BotNotFound(suggested_driver.clone()))?;
            }
        }

        let mut participant_ids = if cmd.suggested_participants.is_empty() {
            self.registry
                .discover(&cmd.topic)
                .await
                .into_iter()
                .filter(|bot| bot.bot_uuid != cmd.driver_bot_id)
                .take(3)
                .map(|bot| bot.bot_uuid)
                .collect()
        } else {
            cmd.suggested_participants
        };
        if !participant_ids.contains(&cmd.driver_bot_id) {
            participant_ids.push(cmd.driver_bot_id.clone());
        }
        participant_ids = dedupe_preserving_order(participant_ids);

        let mut validated_bots = Vec::with_capacity(participant_ids.len());
        for participant_id in &participant_ids {
            let bot = if participant_id == &driver.bot_uuid {
                driver.clone()
            } else {
                self.validate_target(&cmd.driver_bot_id, participant_id)
                    .await?
            };
            validated_bots.push(bot);
        }

        let member_intros = generate_member_intros(&validated_bots, &cmd.driver_bot_id);
        let token = uuid::Uuid::new_v4().to_string();
        let confirm_url = format!(
            "{}/groups/{}/confirm",
            self.config.proposal_base_url.trim_end_matches('/'),
            token
        );
        let proposal = GroupChatProposal {
            token: token.clone(),
            driver_bot: cmd.driver_bot_id.clone(),
            participants: participant_ids.clone(),
            reason: cmd.topic.clone(),
            proposed_by: cmd.driver_bot_id.clone(),
            member_intros: member_intros.clone(),
            confirm_url: confirm_url.clone(),
            created_at: now_ms(),
        };
        self.proposal.store(proposal).await;

        Ok(GroupProposalCreateResult {
            proposal_created: true,
            driver_bot_id: cmd.driver_bot_id,
            participant_bot_ids: participant_ids,
            member_intros: member_intros.clone(),
            confirm_url,
            expires_in_seconds: GroupChatProposal::EXPIRY_MS / 1000,
            message: generate_user_message(&member_intros, &cmd.topic),
        })
    }

    async fn confirm_proposal(
        &self,
        cmd: GroupProposalConfirmCommand,
    ) -> Result<GroupProposalConfirmResult, GroupUseCaseError> {
        let _guard = self.confirm_lock.lock().await;
        let proposal = self.proposal.get(&cmd.token).await.ok_or_else(|| {
            GroupUseCaseError::InvalidProposal(format!(
                "Proposal '{}' not found or expired",
                cmd.token
            ))
        })?;

        if proposal.is_expired() {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Proposal '{}' expired",
                cmd.token
            )));
        }

        self.ensure_limits(&proposal).await?;

        let mut participants = Vec::with_capacity(proposal.participants.len());
        for bot_id in &proposal.participants {
            let bot = self.validate_target(&proposal.driver_bot, bot_id).await?;
            let role = if bot_id == &proposal.driver_bot {
                ParticipantRole::Driver
            } else {
                ParticipantRole::Consultant
            };
            participants.push(Participant {
                bot_uuid: bot_id.clone(),
                bot_name: bot.capabilities.name,
                kind: None,
                role,
                actor_kind: bot.actor_kind,
                mode: Some(ParticipantMode::default_for(bot.actor_kind)),
            });
        }

        let group_id = uuid::Uuid::new_v4().to_string();
        let mut group = Group::new(&group_id, proposal.driver_bot.clone(), participants);
        group.label = Some(format!("Group: {}", proposal.reason));
        self.group.upsert(group.clone()).await?;
        let session = match self
            .session_management
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group.id.clone(),
                session_id: None,
                params: NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: group.participants.clone(),
                    group_version: Some(group.version),
                    session_title: Some("新会话".to_string()),
                    ..Default::default()
                },
            })
            .await
        {
            Ok(outcome) => outcome.session,
            Err(error) => {
                let _ = self.group.delete(&group.id).await;
                return Err(GroupUseCaseError::Service(ServiceError::InternalError(format!(
                    "failed to auto-create initial session for proposal group: {error}"
                ))));
            }
        };
        let context_injected = self
            .system_message
            .notify(
                &group.id,
                SystemMessageEvent::SessionContext {
                    group_id: group.id.clone(),
                    session_id: session.id.clone(),
                    reason: proposal.reason.clone(),
                    session_input: None,
                    task_ledger: None,
                },
                &session.id,
                &session.participants,
            )
            .await
            .unwrap_or(0) as u64;
        let _ = self.proposal.take(&cmd.token).await;

        let chat_url = self.config.botchat_base_url.as_ref().map(|base| {
            format!(
                "{}/bcn/chat/detail?id={}",
                base.trim_end_matches('/'),
                group_id
            )
        });

        Ok(GroupProposalConfirmResult {
            created: true,
            group_id,
            driver_bot_id: proposal.driver_bot,
            participant_bot_ids: proposal.participants,
            chat_url,
            context_injected,
        })
    }

    async fn preview_proposal(
        &self,
        cmd: GroupProposalPreviewCommand,
    ) -> Result<GroupProposalPreviewResult, GroupUseCaseError> {
        let proposal = self
            .proposal
            .get(&cmd.token)
            .await
            .ok_or_else(|| GroupUseCaseError::ProposalNotFound(cmd.token.clone()))?;
        if proposal.is_expired() {
            return Err(GroupUseCaseError::ProposalExpired(cmd.token));
        }
        Ok(GroupProposalPreviewResult {
            token: cmd.token,
            proposal,
        })
    }
}

fn dedupe_preserving_order(participants: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    participants
        .into_iter()
        .filter(|participant| seen.insert(participant.clone()))
        .collect()
}

fn generate_member_intros(bots: &[RegisteredBot], driver_bot: &str) -> String {
    bots.iter()
        .map(|bot| {
            let name = bot.capabilities.name.as_deref().unwrap_or(&bot.bot_uuid);
            let role = if bot.bot_uuid == driver_bot {
                "Driver"
            } else {
                "成员"
            };
            format!("**{}** ({})", name, role)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn generate_user_message(member_intros: &str, topic: &str) -> String {
    format!(
        "📋 **群聊建议**\n\n主题: {}，建议创建群聊。\n\n**参与者：**\n{}\n\n👉 请在10分钟内点击链接确认：\n",
        topic, member_intros
    )
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
