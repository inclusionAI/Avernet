use std::{collections::HashSet, sync::Arc};

use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;

use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, BotRuntimeConnectionService, DmActorSpec, DmCreateCommand,
    DmCreateResult, FriendCoreService, Group as DomainGroup, GroupAddMemberCommand,
    GroupAddMemberResult, GroupCoreService, GroupCreateCommand,
    GroupDeleteCommand, GroupDeleteResult, GroupDetailCommand, GroupDetailResult, GroupKind,
    GroupListCommand, GroupListEntry, GroupListResult, GroupManagementService,
    GroupPatchSettingsCommand, GroupPatchSettingsConflict, GroupPatchSettingsResult,
    GroupParticipantModeCommand, GroupParticipantModeResult, GroupParticipantView, GroupQueryService,
    GroupRemoveMemberCommand, GroupRemoveMemberResult, GroupRoutingPolicyCommand,
    GroupRoutingPolicyResult, GroupStatus, GroupStatusCommand, GroupTerminateCommand,
    GroupStrategy, GroupUpdateLabelCommand, GroupUpdateVisibilityCommand,
    GroupUpdateWorkspaceCommand, GroupUseCaseError,
    GroupWorkspaceQueryCommand, GroupWorkspaceResult, Participant, ParticipantMode,
    ParticipantRole, RelationCoreService, RegisteredBot, ServiceError, ServiceSpec,
    ServiceSpecPatchConflictField, Session, SessionKind,
    SessionManagementService, SystemMessageEvent,
    WorkbenchChatAuthorizationCommand, WorkbenchConnectCommand, WorkbenchConnectOutcome,
    WorkbenchParticipantView, WorkbenchSessionService, WorkbenchUseCaseError,
    backfill_bot_names, validate_sender_routes, CallbackChannelConfig,
    generated_group_id,
};
use tracing::warn;
use crate::core::validate_service_spec_patch;
use crate::noop::{EmptyRelationCoreService, EmptySessionManagementService, NoopSystemMessageService};

#[derive(Debug, Clone)]
pub struct GroupConfig {
    pub max_group_members: usize,
    pub max_groups_as_driver: usize,
    pub max_groups_as_member: usize,
    pub relation_env: String,
}

impl Default for GroupConfig {
    fn default() -> Self {
        Self {
            max_group_members: 20,
            max_groups_as_driver: 10,
            max_groups_as_member: 50,
            relation_env: "dev".to_string(),
        }
    }
}

pub struct GroupManagement {
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    config: GroupConfig,
    system_message: Arc<dyn bcs_service_api::SystemMessageService>,
    session_management: Arc<dyn SessionManagementService>,
    bot_runtime: Option<Arc<dyn BotRuntimeConnectionService>>,
    outbound_url_guard: OutboundUrlGuard,
}

fn validate_service_spec_callback_urls(
    guard: &OutboundUrlGuard,
    service_spec: Option<&ServiceSpec>,
) -> Result<(), GroupUseCaseError> {
    let Some(callback_config) = service_spec.and_then(|spec| spec.callback_config.as_ref()) else {
        return Ok(());
    };
    for (index, channel) in callback_config.channels.iter().enumerate() {
        if let CallbackChannelConfig::Baas { base_url, .. } = channel {
            guard
                .validate_configured_http_url(base_url)
                .map_err(|error| {
                    GroupUseCaseError::InvalidProposal(format!(
                        "service_spec.callback_config.channels[{index}].base_url is not allowed: {error}"
                    ))
                })?;
        }
    }
    Ok(())
}

impl GroupManagement {
    pub fn new(
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
        relation: Arc<dyn RelationCoreService>,
        config: GroupConfig,
        session_management: Arc<dyn SessionManagementService>,
        system_message: Arc<dyn bcs_service_api::SystemMessageService>,
    ) -> Self {
        Self {
            group,
            registry,
            friend,
            relation,
            config,
            system_message,
            session_management,
            bot_runtime: None,
            outbound_url_guard: OutboundUrlGuard::strict(),
        }
    }

    pub fn with_bot_runtime(
        mut self,
        bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    ) -> Self {
        self.bot_runtime = Some(bot_runtime);
        self
    }

    pub fn with_outbound_url_guard(mut self, outbound_url_guard: OutboundUrlGuard) -> Self {
        self.outbound_url_guard = outbound_url_guard;
        self
    }

    pub fn with_defaults(
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friend: Arc<dyn FriendCoreService>,
    ) -> Self {
        Self::new(
            group,
            registry,
            friend,
            Arc::new(EmptyRelationCoreService),
            Default::default(),
            Arc::new(EmptySessionManagementService),
            Arc::new(NoopSystemMessageService),
        )
    }

    async fn authorize_originator(
        &self,
        caller_actor_id: Option<&str>,
        originator: &str,
    ) -> Result<(), GroupUseCaseError> {
        let caller = caller_actor_id
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        if caller == originator {
            return Ok(());
        }

        // Human callers can designate any bot or human as originator.
        if caller.starts_with("human_") {
            return Ok(());
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "Not authorized to create group as '{}'",
            originator
        )))
    }

    async fn ensure_limits(
        &self,
        driver_bot_id: &str,
        participant_ids: &[String],
    ) -> Result<(), GroupUseCaseError> {
        if participant_ids.len() > self.config.max_group_members {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Group would have {} members, exceeding the limit of {}",
                participant_ids.len(),
                self.config.max_group_members
            )));
        }

        let driver_active_count = self
            .group
            .find_by_participant(driver_bot_id)
            .await
            .into_iter()
            .filter(|group| {
                group.driver_bot == driver_bot_id && group.status == GroupStatus::Active
            })
            .count();
        if driver_active_count >= self.config.max_groups_as_driver {
            return Err(GroupUseCaseError::InvalidProposal(format!(
                "Bot '{}' already drives {} active group(s) (max {})",
                driver_bot_id, driver_active_count, self.config.max_groups_as_driver
            )));
        }

        for bot_id in participant_ids {
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

    async fn ensure_reachable(
        &self,
        driver_bot_id: &str,
        target_bot_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        if target.status == ActorStatus::Hidden {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is hidden (offline) and cannot be invited into a group",
                target_bot_id
            )));
        }

        let is_friend = self.friend.are_friends(driver_bot_id, target_bot_id).await;
        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            _ if is_friend => Ok(()),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is not friends with '{}'",
                driver_bot_id, target_bot_id
            ))),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string()).into()),
        }
    }

    async fn try_write_subscription_edge(&self, requester_bot_id: &str, target: &RegisteredBot) {
        if target.capabilities.visibility != "public" {
            return;
        }

        let env = &self.config.relation_env;
        match self.relation.get_edge(requester_bot_id, &target.bot_uuid, env).await {
            Ok(Some(_)) => {}
            Ok(None) => {
                if let Err(error) = self
                    .relation
                    .add_relation_edge(requester_bot_id, &target.bot_uuid, env)
                    .await
                {
                    warn!(
                        requester = %requester_bot_id,
                        target = %target.bot_uuid,
                        env = %env,
                        error = %error,
                        "subscription edge write failed; group membership remains the source of truth"
                    );
                }
            }
            Err(error) => {
                warn!(
                    requester = %requester_bot_id,
                    target = %target.bot_uuid,
                    env = %env,
                    error = %error,
                    "relation.get_edge failed before subscription edge write; skipping"
                );
            }
        }
    }

    async fn ensure_add_member_reachable(
        &self,
        driver_bot_id: &str,
        target_bot_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        let target = self
            .registry
            .get(target_bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(target_bot_id.to_string()))?;

        if target.status == ActorStatus::Hidden {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is hidden (offline) and cannot be invited into a group",
                target_bot_id
            )));
        }

        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            "protected" if self.friend.are_friends(driver_bot_id, target_bot_id).await => Ok(()),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Bot '{}' is not friends with '{}'",
                driver_bot_id, target_bot_id
            ))),
            _ => Err(ServiceError::BotNotFound(target_bot_id.to_string()).into()),
        }
    }

    async fn ensure_manager_worker_accepts_participants(
        &self,
        _strategy: GroupStrategy,
        _participants: &[Participant],
    ) -> Result<(), GroupUseCaseError> {
        Ok(())
    }

    /// Check that all bot participants in the list have public visibility.
    /// Returns error listing non-public bots if any exist.
    async fn ensure_all_bots_public(
        &self,
        participants: &[Participant],
    ) -> Result<(), GroupUseCaseError> {
        let mut non_public = Vec::new();
        for p in participants {
            if p.actor_kind != ActorKind::Bot {
                continue;
            }
            if let Some(bot) = self.registry.get(&p.bot_uuid).await {
                if bot.capabilities.visibility != "public" {
                    non_public.push((p.bot_uuid.clone(), bot.capabilities.name.clone()));
                }
            }
        }
        if non_public.is_empty() {
            Ok(())
        } else {
            Err(GroupUseCaseError::Service(
                ServiceError::ExistNonPublicBots { bots: non_public },
            ))
        }
    }

    async fn relation_has_creator_edge(
        &self,
        human_actor_id: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        match self
            .relation
            .get_edge(human_actor_id, bot_id, &self.config.relation_env)
            .await
        {
            Ok(Some(edge)) => Ok(edge.is_creator),
            Ok(None) => Ok(false),
            Err(error) => Err(ServiceError::InternalError(format!(
                "Failed to verify owner relation: {}",
                error
            ))
            .into()),
        }
    }

    async fn has_bidirectional_relation_or_friendship(
        &self,
        actor_id: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        let relation_friends = self
            .relation
            .list_friends_via_relation(actor_id, &self.config.relation_env)
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "Failed to verify relation friendship: {}",
                    error
                ))
            })?;
        if relation_friends.iter().any(|friend| friend == bot_id) {
            return Ok(true);
        }

        Ok(self.friend.are_friends(actor_id, bot_id).await)
    }

    async fn ensure_human_can_dm_bot(
        &self,
        human_actor_id: &str,
        target: &RegisteredBot,
    ) -> Result<(), GroupUseCaseError> {
        let staff_no = human_actor_id.strip_prefix("human_").ok_or_else(|| {
            GroupUseCaseError::InvalidProposal("caller actor_id must use human_ prefix".to_string())
        })?;

        if target.created_by.as_deref() == Some(staff_no)
            || self
                .relation_has_creator_edge(human_actor_id, &target.bot_uuid)
                .await?
        {
            return Ok(());
        }

        let has_relation = self
            .has_bidirectional_relation_or_friendship(human_actor_id, &target.bot_uuid)
            .await?;
        match target.capabilities.visibility.as_str() {
            "public" => Ok(()),
            "protected" if has_relation => Ok(()),
            "protected" => Err(GroupUseCaseError::Forbidden(format!(
                "Human '{}' is not related to protected bot '{}'",
                human_actor_id, target.bot_uuid
            ))),
            "private" if has_relation => Ok(()),
            _ => Err(ServiceError::BotNotFound(target.bot_uuid.clone()).into()),
        }
    }

    fn is_human_bot_dm(group: &DomainGroup) -> bool {
        group.group_kind == GroupKind::Dm
            && group
                .participants
                .iter()
                .any(|participant| participant.is_human())
            && group
                .participants
                .iter()
                .any(|participant| participant.is_bot())
    }

    async fn authorize_human_owner(
        &self,
        human_actor_id: Option<&str>,
        bot_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        let Some(human_actor_id) = human_actor_id else {
            return Ok(()); // bot 自主操作，由后续 ensure_group_coordinator 接管
        };
        let staff_no = human_actor_id
            .strip_prefix("human_")
            .unwrap_or(human_actor_id);
        let bot = self.registry.get(bot_id).await.ok_or_else(|| {
            GroupUseCaseError::Forbidden(format!("Not authorized as bot '{}'", bot_id))
        })?;
        if bot
            .created_by
            .as_deref()
            .map(|owner| owner == staff_no)
            .unwrap_or(true)
        {
            return Ok(());
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "Not authorized as bot '{}'",
            bot_id
        )))
    }

    fn ensure_group_coordinator(
        &self,
        group: &DomainGroup,
        caller_actor_id: &str,
        action: &str,
    ) -> Result<(), GroupUseCaseError> {
        if group.originator() == caller_actor_id || group.driver_bot == caller_actor_id {
            return Ok(());
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "Only the group coordinator (originator: {} or driver: {}) can {}, not '{}'",
            group.originator(),
            group.driver_bot,
            action,
            caller_actor_id
        )))
    }

    async fn authorize_add_member(
        &self,
        cmd: &GroupAddMemberCommand,
    ) -> Result<(String, DomainGroup), GroupUseCaseError> {
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|c| !c.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        if group.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot add members".to_string(),
            ));
        }
        self.authorize_human_owner(cmd.human_actor_id.as_deref(), caller)
            .await?;
        self.ensure_group_coordinator(&group, caller, "add members")?;
        Ok((caller.to_string(), group))
    }

    async fn ensure_actor_self_or_creator(
        &self,
        caller_actor_id: &str,
        actor_id: &str,
    ) -> Result<(), GroupUseCaseError> {
        if caller_actor_id == actor_id {
            return Ok(());
        }

        match self
            .relation
            .get_edge(caller_actor_id, actor_id, &self.config.relation_env)
            .await
        {
            Ok(Some(edge)) if edge.is_creator => Ok(()),
            Ok(_) => Err(GroupUseCaseError::Forbidden(format!(
                "Caller '{}' is not the actor itself nor a creator of '{}'",
                caller_actor_id, actor_id
            ))),
            Err(error) => Err(ServiceError::InternalError(format!(
                "Failed to verify creator relation: {}",
                error
            ))
            .into()),
        }
    }

    async fn authorize_workbench_group_access(
        &self,
        group: &DomainGroup,
        bound_actor_id: Option<&str>,
    ) -> Result<WorkbenchAuthorizedHuman, WorkbenchUseCaseError> {
        let actor_id = bound_actor_id.ok_or(WorkbenchUseCaseError::Unauthorized)?;
        let staff_no = staff_no_from_bound_actor(Some(actor_id))?;

        if human_has_group_access(self.registry.as_ref(), group, actor_id, staff_no).await {
            return Ok(WorkbenchAuthorizedHuman {
                actor_id: actor_id.to_string(),
                staff_no: staff_no.to_string(),
            });
        }

        Err(WorkbenchUseCaseError::ForbiddenGroupAccess)
    }

    async fn authorize_workbench_sender(
        &self,
        group: &DomainGroup,
        from_actor_id: &str,
        auth: &WorkbenchAuthorizedHuman,
    ) -> Result<(), WorkbenchUseCaseError> {
        if from_actor_id == auth.actor_id {
            return Ok(());
        }

        if Self::is_human_bot_dm(group) {
            return Err(WorkbenchUseCaseError::ForbiddenSender);
        }

        let Some(bot) = self.registry.get(from_actor_id).await else {
            return Err(WorkbenchUseCaseError::ForbiddenSender);
        };
        if bot.actor_kind != ActorKind::Bot {
            return Err(WorkbenchUseCaseError::ForbiddenSender);
        }
        if bot_belongs_to_staff(from_actor_id, bot.created_by.as_deref(), &auth.staff_no) {
            return Ok(());
        }

        Err(WorkbenchUseCaseError::ForbiddenSender)
    }

    async fn session_participant(
        &self,
        session_id: Option<&str>,
        group_id: &str,
        actor_id: &str,
    ) -> Result<Option<bcs_service_api::Participant>, WorkbenchUseCaseError> {
        let Some(session_id) = session_id else {
            return Ok(None);
        };
        let session = self
            .session_management
            .get(session_id)
            .await
            .map_err(|error| {
                WorkbenchUseCaseError::Service(ServiceError::InternalError(error.to_string()))
            })?;
        Ok(session
            .filter(|session| session.group_id == group_id)
            .and_then(|session| find_session_participant(&session, actor_id)))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct WorkbenchAuthorizedHuman {
    actor_id: String,
    staff_no: String,
}

#[async_trait]
impl GroupQueryService for GroupManagement {
    async fn list_groups(
        &self,
        cmd: GroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        let total = self.group.count_filtered(
            cmd.group_kind,
            cmd.visibility.as_deref(),
            cmd.label.as_deref(),
        ).await;
        let mut groups = self.group.list_paginated_filtered(
            cmd.offset,
            cmd.limit,
            cmd.group_kind,
            cmd.visibility.as_deref(),
            cmd.label.as_deref(),
        ).await;
        for group in &mut groups {
            backfill_bot_names(self.registry.as_ref(), group).await;
        }
        Ok(GroupListResult {
            items: groups.into_iter().map(group_to_list_entry).collect(),
            total,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_group(
        &self,
        cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let mut group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        backfill_bot_names(self.registry.as_ref(), &mut group).await;
        Ok(group_to_detail(group))
    }

    async fn list_bot_groups(
        &self,
        cmd: bcs_service_api::BotGroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        // Contract-level absent filtering remains in the application layer:
        // the core query returns participant membership, not a mode-aware
        // predicate. Keep filtering and ordering before pagination so `total`
        // and `items` describe only visible groups.
        let mut filtered = self
            .group
            .find_by_participant_filtered(&cmd.bot_id, cmd.group_kind, cmd.q.as_deref())
            .await
            .into_iter()
            .filter(|group| group_has_non_absent_participant(group, &cmd.bot_id))
            .collect::<Vec<_>>();
        DomainGroup::sort_by_updated_at_desc(&mut filtered);
        let total = filtered.len() as u64;
        let mut page = filtered
            .into_iter()
            .skip(to_usize(cmd.offset))
            .take(to_usize(cmd.limit))
            .collect::<Vec<_>>();
        for group in &mut page {
            backfill_bot_names(self.registry.as_ref(), group).await;
        }
        let items = page
            .into_iter()
            .map(group_to_list_entry)
            .collect();
        Ok(GroupListResult {
            items,
            total,
            offset: cmd.offset,
            limit: cmd.limit,
        })
    }

    async fn get_workspace(
        &self,
        cmd: GroupWorkspaceQueryCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        Ok(GroupWorkspaceResult {
            group_id: cmd.group_id,
            workspace: group.workspace,
        })
    }
}

#[async_trait]
impl GroupManagementService for GroupManagement {
    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        if cmd.group_kind == Some(GroupKind::Dm) {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups must be created through create_dm".to_string(),
            ));
        }
        validate_service_spec_callback_urls(&self.outbound_url_guard, cmd.service_spec.as_ref())?;

        let originator = cmd
            .originator
            .clone()
            .unwrap_or_else(|| cmd.driver_bot_id.clone());

        self.authorize_originator(cmd.caller_actor_id.as_deref(), &originator)
            .await?;

        let is_human_originator = originator.starts_with("human_");

        let group_id = cmd
            .group_id
            .unwrap_or_else(|| generated_group_id(GroupKind::Normal));
        let mut requested = Vec::new();
        for participant in cmd.participants {
            requested.push((participant.bot_id, participant.role));
        }
        for bot_id in cmd.member_bot_ids {
            requested.push((bot_id, None));
        }
        if !requested
            .iter()
            .any(|(bot_id, _)| bot_id == &cmd.driver_bot_id)
        {
            requested.push((cmd.driver_bot_id.clone(), Some("driver".to_string())));
        }

        let mut seen = HashSet::new();
        let mut participants = Vec::with_capacity(requested.len());
        let mut participant_ids = Vec::with_capacity(requested.len());
        let mut subscription_targets = Vec::new();
        for (bot_id, role) in requested {
            if !seen.insert(bot_id.clone()) {
                continue;
            }
            let bot = self
                .registry
                .get(&bot_id)
                .await
                .ok_or_else(|| ServiceError::BotNotFound(bot_id.clone()))?;
            if bot.actor_kind == ActorKind::Bot {
                if bot_id != originator {
                    if is_human_originator {
                        let staff_no = originator.trim_start_matches("human_");
                        if bot.capabilities.visibility != "public"
                            && bot.created_by.as_deref() != Some(staff_no)
                        {
                            return Err(GroupUseCaseError::Forbidden(format!(
                                "Bot '{}' is neither public nor owned by human '{}'",
                                bot_id, staff_no
                            )));
                        }
                    } else {
                        self.ensure_reachable(&originator, &bot_id).await?;
                    }
                    if bot.capabilities.visibility == "public" {
                        subscription_targets.push(bot.clone());
                    }
                }
            }
            let role = participant_role(role.as_deref(), bot_id == cmd.driver_bot_id)?;
            let mode = match bot.actor_kind {
                ActorKind::Human => ParticipantMode::Present,
                ActorKind::Bot => ParticipantMode::default_for(ActorKind::Bot),
            };
            participants.push(Participant {
                bot_uuid: bot_id.clone(),
                bot_name: bot.capabilities.name,
                kind: None,
                role,
                actor_kind: bot.actor_kind,
                mode: Some(mode),
            });
            participant_ids.push(bot_id);
        }
        let requested_strategy = cmd.group_strategy.unwrap_or_default();
        validate_participants_for_strategy(requested_strategy, &participants)?;
        validate_human_constraints(requested_strategy, &participants, &cmd.driver_bot_id)?;
        self.ensure_manager_worker_accepts_participants(requested_strategy, &participants)
            .await?;

        self.ensure_limits(&cmd.driver_bot_id, &participant_ids)
            .await?;

        if let Some(policy) = &cmd.routing_policy {
            if !policy.sender_routes.is_empty() {
                let participant_refs: Vec<&str> =
                    participant_ids.iter().map(String::as_str).collect();
                validate_sender_routes(&policy.sender_routes, &participant_refs)
                    .map_err(|error| GroupUseCaseError::InvalidProposal(error.to_string()))?;
            }
        }

        let mut group = DomainGroup::new(&group_id, cmd.driver_bot_id.clone(), participants);
        group.originator = cmd
            .originator
            .clone()
            .or_else(|| cmd.caller_actor_id.clone())
            .or_else(|| Some(cmd.driver_bot_id.clone()));
        group.label = match (cmd.topic.as_ref(), cmd.label.as_ref()) {
            (Some(topic), _) => Some(format!("Group: {}", topic)),
            (None, Some(label)) => Some(label.clone()),
            (None, None) => {
                let now = chrono::Utc::now()
                    .with_timezone(&chrono::FixedOffset::east_opt(8 * 3600).expect("UTC+8"))
                    .format("%Y%m%d%H%M")
                    .to_string();
                Some(format!("{}-{}", cmd.driver_bot_id, now))
            }
        };
        group.context = cmd.context.clone();
        group.routing_policy = cmd.routing_policy;
        group.group_kind = cmd.group_kind.unwrap_or(GroupKind::Normal);
        group.service_spec = cmd.service_spec.clone();
        group.group_strategy = requested_strategy;

        let visibility = cmd.visibility
            .as_deref()
            .unwrap_or("private")
            .to_string();
        if visibility != "public" && visibility != "private" {
            return Err(GroupUseCaseError::InvalidProposal(
                "Invalid visibility value: must be 'public' or 'private'".to_string(),
            ));
        }
        if visibility == "public" {
            if group.group_kind == GroupKind::Dm {
                return Err(GroupUseCaseError::InvalidProposal(
                    "DM groups cannot be set to public".to_string(),
                ));
            }
            self.ensure_all_bots_public(&group.participants).await?;
        }
        group.visibility = visibility;

        self.group.upsert(group.clone()).await?;

        for target in &subscription_targets {
            self.try_write_subscription_edge(&cmd.driver_bot_id, target).await;
        }

        let topic = cmd.topic.as_deref().unwrap_or_else(|| {
            group.label.as_deref().unwrap_or("")
        });
        let initial_session_kind = match requested_strategy {
            GroupStrategy::StateMachine => SessionKind::ServiceInvocation,
            GroupStrategy::Chat | GroupStrategy::ManagerWorker => SessionKind::Chat,
        };
        let initial_session_title = Some("新会话".to_string());
        let initial_session_input = match requested_strategy {
            GroupStrategy::StateMachine => state_machine_initial_session_input(
                cmd.context.as_deref(),
                cmd.topic.as_deref(),
            ),
            GroupStrategy::Chat | GroupStrategy::ManagerWorker => None,
        };
        let initial_session_id;
        let context_injected = match self
            .session_management
            .create_or_reactivate(bcs_service_api::CreateOrReactivateCommand {
                group_id: group.id.clone(),
                session_id: None,
                params: bcs_service_api::NewSessionParams {
                    session_kind: initial_session_kind,
                    participants: group.participants.clone(),
                    group_version: Some(group.version),
                    input: initial_session_input,
                    session_title: initial_session_title,
                    created_by: Some(originator.clone()),
                    caller_principal: cmd.caller_actor_id.clone(),
                    ..Default::default()
                },
            })
            .await
        {
            Ok(outcome) => {
                initial_session_id = Some(outcome.session.id.clone());
                tracing::info!(
                    group_id = %group.id,
                    session_id = %outcome.session.id,
                    session_kind = ?outcome.session.session_kind,
                    "auto-created initial session for new group"
                );
                if requested_strategy == GroupStrategy::StateMachine {
                    0
                } else {
                    let sid = outcome.session.id.clone();
                    let gid = group.id.clone();
                    let session_participants = outcome.session.participants.clone();
                    let reason = topic.to_string();
                    self.system_message
                        .notify(
                            &gid,
                            SystemMessageEvent::SessionContext {
                                group_id: gid.clone(),
                                session_id: sid.clone(),
                                reason,
                                session_input: None,
                                task_ledger: None,
                            },
                            &sid,
                            &session_participants,
                        )
                        .await
                        .unwrap_or(0) as u64
                }
            }
            Err(error) => {
                warn!(
                    group_id = %group.id,
                    error = %error,
                    "failed to auto-create initial session for new group"
                );
                if let Err(rollback_error) = self.group.delete(&group.id).await {
                    warn!(
                        group_id = %group.id,
                        error = %rollback_error,
                        "failed to roll back group after initial session creation failure"
                    );
                }
                return Err(GroupUseCaseError::Service(ServiceError::InternalError(format!(
                    "failed to auto-create initial session for new group: {error}"
                ))));
            }
        };

        let mut detail = group_to_detail_with_context(group, context_injected);
        detail.latest_running_session_id = initial_session_id;
        Ok(detail)
    }

    async fn create_dm(&self, cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;

        let caller_actor = self
            .registry
            .get(caller)
            .await
            .ok_or_else(|| GroupUseCaseError::ActorNotFound(caller.to_string()))?;
        let target = self
            .registry
            .get(&cmd.target_actor_id)
            .await
            .ok_or_else(|| GroupUseCaseError::ActorNotFound(cmd.target_actor_id.clone()))?;
        if target.actor_kind != ActorKind::Bot {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM target must be a Bot actor".to_string(),
            ));
        }

        let group_id = cmd
            .group_id
            .unwrap_or_else(|| generated_group_id(GroupKind::Dm));
        let label = dm_label(cmd.label, cmd.topic.as_deref(), caller, &target.bot_uuid);

        let (actor_a, actor_b, legacy_driver_bot, originator_actor_id) = match caller_actor.actor_kind {
            ActorKind::Human => {
                if let Some(driver_bot) = cmd.driver_bot.as_deref() {
                    if driver_bot != target.bot_uuid {
                        return Err(GroupUseCaseError::InvalidProposal(
                            "driver_bot must match target_actor_id for Human-Bot DM".to_string(),
                        ));
                    }
                }
                self.ensure_human_can_dm_bot(caller, &target).await?;
                (
                    DmActorSpec {
                        actor_id: caller.to_string(),
                        actor_kind: ActorKind::Human,
                        display_name: caller_actor.capabilities.name.clone(),
                    },
                    DmActorSpec {
                        actor_id: target.bot_uuid.clone(),
                        actor_kind: ActorKind::Bot,
                        display_name: target.capabilities.name.clone(),
                    },
                    target.bot_uuid.clone(),
                    caller.to_string(),
                )
            }
            ActorKind::Bot => {
                self.ensure_reachable(caller, &target.bot_uuid).await?;
                (
                    DmActorSpec {
                        actor_id: caller_actor.bot_uuid.clone(),
                        actor_kind: ActorKind::Bot,
                        display_name: caller_actor.capabilities.name.clone(),
                    },
                    DmActorSpec {
                        actor_id: target.bot_uuid.clone(),
                        actor_kind: ActorKind::Bot,
                        display_name: target.capabilities.name.clone(),
                    },
                    caller_actor.bot_uuid.clone(),
                    caller_actor.bot_uuid,
                )
            }
        };

        let (group, created) = self
            .group
            .create_or_reuse_actor_dm_group(
                &group_id,
                actor_a,
                actor_b,
                &legacy_driver_bot,
                &originator_actor_id,
                label,
                cmd.context,
            )
            .await?;

        Ok(DmCreateResult {
            group: group_to_detail_with_context(group, 0),
            created,
        })
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let status = parse_group_status(&cmd.status)?;
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|caller| !caller.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;
        if caller != group.driver_bot && caller != group.originator() {
            return Err(GroupUseCaseError::Forbidden(format!(
                "Only the group coordinator (originator: {} or driver: {}) can update status, not '{}'",
                group.originator(),
                group.driver_bot,
                caller
            )));
        }

        self.group.update_status(&cmd.group_id, status).await?;
        let updated = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        Ok(group_to_detail(updated))
    }

    async fn add_member(
        &self,
        cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError> {
        let (_caller, group) = self.authorize_add_member(&cmd).await?;

        let bot = self
            .registry
            .get(&cmd.bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(cmd.bot_id.clone()))?;
        let role = member_role(cmd.role.as_deref());

        if bot.actor_kind == ActorKind::Human {
            let allowed = match group.group_strategy {
                GroupStrategy::Chat | GroupStrategy::StateMachine => matches!(
                    role,
                    ParticipantRole::Consultant | ParticipantRole::Observer
                ),
                GroupStrategy::ManagerWorker => matches!(
                    role,
                    ParticipantRole::Worker | ParticipantRole::Observer
                ),
            };
            if !allowed {
                let strategy_name = match group.group_strategy {
                    GroupStrategy::Chat => "chat",
                    GroupStrategy::ManagerWorker => "manager_worker",
                    GroupStrategy::StateMachine => "state_machine",
                };
                return Err(GroupUseCaseError::InvalidProposal(format!(
                    "Human actors cannot have role '{}' in {} groups",
                    participant_role_to_wire(role),
                    strategy_name
                )));
            }
        }

        if bot.actor_kind == ActorKind::Bot {
            self.ensure_add_member_reachable(&group.driver_bot, &cmd.bot_id)
                .await?;
            if group.visibility == "public" && bot.capabilities.visibility != "public" {
                return Err(GroupUseCaseError::InvalidProposal(format!(
                    "Cannot add non-public bot '{}' to a public group",
                    cmd.bot_id
                )));
            }
        }

        let bot_name = bot.capabilities.name.clone();
        let mode = if bot.actor_kind == ActorKind::Human {
            Some(ParticipantMode::Present)
        } else {
            None
        };
        let participant = Participant {
            bot_uuid: cmd.bot_id.clone(),
            bot_name,
            kind: None,
            role,
            actor_kind: bot.actor_kind,
            mode,
        };

        self.group
            .add_participant(&cmd.group_id, participant.clone())
            .await?;

        if bot.actor_kind == ActorKind::Bot {
            self.try_write_subscription_edge(&group.driver_bot, &bot).await;
        }

        Ok(GroupAddMemberResult {
            group_id: cmd.group_id,
            member: GroupParticipantView {
                bot_uuid: participant.bot_uuid,
                bot_name: participant.bot_name,
                kind: participant.kind,
                role: participant_role_to_wire(participant.role).to_string(),
                actor_kind: participant.actor_kind,
                mode: participant.mode,
            },
        })
    }

    async fn remove_member(
        &self,
        cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError> {
        let caller = cmd
            .caller_actor_id
            .as_deref()
            .filter(|c| !c.is_empty())
            .ok_or_else(|| GroupUseCaseError::Unauthorized("caller is required".to_string()))?;

        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        if group.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot remove members".to_string(),
            ));
        }

        let is_coordinator = group.originator() == caller || group.driver_bot == caller || {
            if caller.starts_with("human_") {
                let staff_no = caller.trim_start_matches("human_");
                let owned = self.registry.list_bots_by_creator(staff_no).await;
                owned.iter().any(|b| {
                    b.bot_uuid == group.driver_bot || b.bot_uuid == group.originator()
                })
            } else {
                false
            }
        };
        let is_self = caller == cmd.bot_id;
        let is_owner = if caller.starts_with("human_") {
            let staff_no = caller.trim_start_matches("human_");
            self.registry
                .list_bots_by_creator(staff_no)
                .await
                .iter()
                .any(|b| b.bot_uuid == cmd.bot_id)
        } else {
            false
        };
        if !is_coordinator && !is_self && !is_owner {
            return Err(GroupUseCaseError::Forbidden(
                "Caller is not authorized to remove this member".to_string(),
            ));
        }

        if cmd.bot_id == group.driver_bot || cmd.bot_id == group.originator() {
            return Err(GroupUseCaseError::InvalidProposal(
                "Cannot remove the group driver/coordinator".to_string(),
            ));
        }

        if group.group_strategy == GroupStrategy::ManagerWorker {
            if let Some(manager) = group.participants.iter().find(|p| p.role == ParticipantRole::Manager) {
                if cmd.bot_id == manager.bot_uuid {
                    return Err(GroupUseCaseError::InvalidProposal(
                        "Cannot remove the Manager bot from a ManagerWorker group".to_string(),
                    ));
                }
            }
        }

        if !group.participants.iter().any(|p| p.bot_uuid == cmd.bot_id) {
            return Err(ServiceError::ParticipantNotFound(cmd.bot_id.clone()).into());
        }

        self.group
            .remove_participant(&cmd.group_id, &cmd.bot_id)
            .await?;

        Ok(GroupRemoveMemberResult {
            group_id: cmd.group_id,
            removed_bot_uuid: cmd.bot_id,
        })
    }

    async fn delete_group(
        &self,
        cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        if group.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot be deleted or left".to_string(),
            ));
        }
        self.ensure_group_coordinator(&group, &cmd.caller_actor_id, "delete this group")?;

        self.group.delete(&cmd.group_id).await?;
        Ok(GroupDeleteResult {
            group_id: cmd.group_id,
            deleted: true,
        })
    }

    async fn terminate_group(
        &self,
        cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let existing = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        if existing.group_kind == GroupKind::Dm {
            return Err(GroupUseCaseError::InvalidProposal(
                "DM groups cannot be terminated".to_string(),
            ));
        }
        let group = self
            .group
            .terminate(&cmd.group_id, &cmd.caller_actor_id)
            .await?;
        Ok(group_to_detail(group))
    }

    async fn update_label(
        &self,
        cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        self.ensure_group_coordinator(&group, &cmd.caller_actor_id, "update label")?;

        self.group
            .update_label(&cmd.group_id, cmd.label.clone())
            .await?;
        let updated = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        Ok(group_to_detail(updated))
    }

    async fn update_visibility(
        &self,
        cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let visibility = cmd.visibility.as_str();
        if visibility != "public" && visibility != "private" {
            return Err(GroupUseCaseError::InvalidProposal(
                "Invalid visibility value: must be 'public' or 'private'".to_string(),
            ));
        }

        let group = self.group.get(&cmd.group_id).await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        // Only coordinator (driver, originator, or driver's owner) can change visibility
        let is_coordinator = cmd.caller_actor_id == group.driver_bot
            || group.originator.as_deref() == Some(&cmd.caller_actor_id)
            || self.registry.list_bots_by_creator(&cmd.caller_actor_id).await
                .iter().any(|b| b.bot_uuid == group.driver_bot);
        if !is_coordinator {
            return Err(GroupUseCaseError::Forbidden(
                "Only the group coordinator can change visibility".to_string(),
            ));
        }

        if visibility == "public" {
            if group.group_kind == GroupKind::Dm {
                return Err(GroupUseCaseError::InvalidProposal(
                    "DM groups cannot be set to public".to_string(),
                ));
            }
            self.ensure_all_bots_public(&group.participants).await?;
        }

        self.group.update_visibility(&cmd.group_id, visibility).await?;

        let updated = self.group.get(&cmd.group_id).await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        Ok(group_to_detail(updated))
    }

    async fn update_workspace(
        &self,
        cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        self.group
            .update_workspace(&cmd.group_id, cmd.workspace.clone())
            .await?;
        Ok(GroupWorkspaceResult {
            group_id: cmd.group_id,
            workspace: cmd.workspace,
        })
    }

    async fn update_routing_policy(
        &self,
        cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        let mut group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
        let existing_policy = group.routing_policy.clone().unwrap_or_default();
        let new_policy = bcs_service_api::RoutingPolicy {
            mode: cmd.mode.unwrap_or(existing_policy.mode),
            default_bot_final_delivery: cmd
                .default_bot_final_delivery
                .unwrap_or(existing_policy.default_bot_final_delivery),
            sender_routes: cmd.sender_routes.unwrap_or(existing_policy.sender_routes),
        };

        if !new_policy.sender_routes.is_empty() {
            let participant_ids = group.participant_ids();
            validate_sender_routes(&new_policy.sender_routes, &participant_ids)
                .map_err(|error| GroupUseCaseError::InvalidProposal(error.to_string()))?;
        }

        group.routing_policy = Some(new_policy.clone());
        self.group.upsert(group).await?;
        Ok(GroupRoutingPolicyResult {
            group_id: cmd.group_id,
            routing_policy: new_policy,
        })
    }

    async fn update_participant_mode(
        &self,
        cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError> {
        let target = self
            .registry
            .get(&cmd.actor_id)
            .await
            .ok_or_else(|| GroupUseCaseError::ActorNotFound(cmd.actor_id.clone()))?;
        if !cmd.mode.is_valid_for(target.actor_kind) {
            return Err(GroupUseCaseError::InvalidParticipantMode {
                mode: cmd.mode,
                actor_kind: target.actor_kind,
            });
        }

        self.ensure_actor_self_or_creator(&cmd.caller_actor_id, &cmd.actor_id)
            .await?;

        match self
            .group
            .update_participant_mode(&cmd.group_id, &cmd.actor_id, cmd.mode)
            .await
        {
            Ok(()) => {}
            Err(ServiceError::BotNotFound(_)) if target.actor_kind == ActorKind::Human => {
                self.group
                    .insert_human_participant(&cmd.group_id, &cmd.actor_id, cmd.mode)
                    .await?;
            }
            Err(error) => return Err(error.into()),
        }

        Ok(GroupParticipantModeResult {
            group_id: cmd.group_id,
            actor_id: cmd.actor_id,
            mode: cmd.mode,
        })
    }

    async fn patch_group_settings(
        &self,
        cmd: GroupPatchSettingsCommand,
    ) -> Result<GroupPatchSettingsResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        let running_service_count = self
            .session_management
            .count_running_service(&cmd.group_id)
            .await
            .unwrap_or(0);

        if let Some(spec_patch) = cmd.service_spec.clone() {
            if let Err(error) =
                validate_service_spec_patch(group.service_spec.as_ref(), spec_patch.as_ref(), running_service_count)
            {
                let conflict = match error {
                    crate::core::ServiceSpecPatchError::CallbackConfigImmutable => {
                        GroupPatchSettingsConflict {
                            field: ServiceSpecPatchConflictField::CallbackConfig,
                            running_service_count,
                        }
                    }
                    crate::core::ServiceSpecPatchError::RouteFieldsLocked(count) => {
                        GroupPatchSettingsConflict {
                            field: ServiceSpecPatchConflictField::RouteFields,
                            running_service_count: count,
                        }
                    }
                };
                return Err(GroupUseCaseError::Conflict(serde_json::to_string(&conflict)
                    .unwrap_or_else(|_| "service_spec patch rejected".to_string())));
            }

            self.group
                .update_service_spec(&cmd.group_id, spec_patch)
                .await?;
        }

        let updated = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        Ok(GroupPatchSettingsResult {
            group_id: cmd.group_id,
            service_spec: updated.service_spec,
        })
    }
}

#[async_trait]
impl WorkbenchSessionService for GroupManagement {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        let group = self
            .group
            .get(&command.group_id)
            .await
            .ok_or_else(|| WorkbenchUseCaseError::GroupNotFound(command.group_id.clone()))?;

        let participants = match self
            .authorize_workbench_group_access(&group, command.bound_actor_id.as_deref())
            .await
        {
            Ok(_) => workbench_participants(&group),
            Err(WorkbenchUseCaseError::ForbiddenGroupAccess) => {
                let actor_id = command
                    .bound_actor_id
                    .as_deref()
                    .ok_or(WorkbenchUseCaseError::Unauthorized)?;
                let Some(session_id) = command.session_id.as_deref() else {
                    return Err(WorkbenchUseCaseError::ForbiddenGroupAccess);
                };
                let session = self
                    .session_management
                    .get(session_id)
                    .await
                    .map_err(|error| {
                        WorkbenchUseCaseError::Service(ServiceError::InternalError(error.to_string()))
                    })?
                    .filter(|session| session.group_id == command.group_id)
                    .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
                let participant = find_session_participant(&session, actor_id)
                    .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
                if participant.mode == Some(ParticipantMode::Absent) {
                    return Err(WorkbenchUseCaseError::ParticipantAbsent);
                }
                workbench_participants_from_slice(&session.participants)
            }
            Err(error) => return Err(error),
        };

        Ok(WorkbenchConnectOutcome {
            group_id: command.group_id,
            participants,
        })
    }

    async fn authorize_chat_send(
        &self,
        command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        let group = self
            .group
            .get(&command.group_id)
            .await
            .ok_or_else(|| WorkbenchUseCaseError::GroupNotFound(command.group_id.clone()))?;
        let auth = match self
            .authorize_workbench_group_access(&group, command.bound_actor_id.as_deref())
            .await
        {
            Ok(auth) => auth,
            Err(WorkbenchUseCaseError::ForbiddenGroupAccess) => {
                let actor_id = command
                    .bound_actor_id
                    .as_deref()
                    .ok_or(WorkbenchUseCaseError::Unauthorized)?;
                let staff_no = staff_no_from_bound_actor(Some(actor_id))?;
                if self
                    .session_participant(
                        command.session_id.as_deref(),
                        &command.group_id,
                        actor_id,
                    )
                    .await?
                    .is_some()
                {
                    WorkbenchAuthorizedHuman {
                        actor_id: actor_id.to_string(),
                        staff_no: staff_no.to_string(),
                    }
                } else {
                    return Err(WorkbenchUseCaseError::ForbiddenGroupAccess);
                }
            }
            Err(error) => return Err(error),
        };

        self.authorize_workbench_sender(&group, &command.from_actor_id, &auth)
            .await?;

        let group_participant = group.get_participant(&command.from_actor_id).cloned();
        let session_participant = self
            .session_participant(
                command.session_id.as_deref(),
                &command.group_id,
                &command.from_actor_id,
            )
            .await?;

        match session_participant.or(group_participant) {
            Some(participant) if participant.mode == Some(ParticipantMode::Absent) => {
                Err(WorkbenchUseCaseError::ParticipantAbsent)
            }
            Some(_) => Ok(()),
            None => Err(WorkbenchUseCaseError::SenderNotInGroup),
        }
    }
}

fn staff_no_from_bound_actor(bound_actor_id: Option<&str>) -> Result<&str, WorkbenchUseCaseError> {
    let actor_id = bound_actor_id.ok_or(WorkbenchUseCaseError::Unauthorized)?;
    let staff_no = actor_id
        .strip_prefix("human_")
        .ok_or(WorkbenchUseCaseError::Unauthorized)?;
    if staff_no.is_empty() {
        return Err(WorkbenchUseCaseError::Unauthorized);
    }
    Ok(staff_no)
}

async fn human_has_group_access(
    registry: &dyn BotRegistryCoreService,
    group: &DomainGroup,
    actor_id: &str,
    staff_no: &str,
) -> bool {
    if group
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == actor_id)
    {
        return true;
    }

    for participant in group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
    {
        let Some(bot) = registry.get(&participant.bot_uuid).await else {
            continue;
        };
        if bot_belongs_to_staff(&participant.bot_uuid, bot.created_by.as_deref(), staff_no) {
            return true;
        }
    }

    false
}

fn bot_belongs_to_staff(bot_uuid: &str, created_by: Option<&str>, staff_no: &str) -> bool {
    if created_by == Some(staff_no) {
        return true;
    }
    if created_by.is_some() {
        return false;
    }
    bot_uuid
        .rsplit_once(':')
        .map(|(_, suffix)| suffix == staff_no)
        .unwrap_or(false)
}

fn find_session_participant(
    session: &Session,
    actor_id: &str,
) -> Option<bcs_service_api::Participant> {
    session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == actor_id)
        .cloned()
}

fn workbench_participants(group: &DomainGroup) -> Vec<WorkbenchParticipantView> {
    workbench_participants_from_slice(&group.participants)
}

fn workbench_participants_from_slice(
    participants: &[bcs_service_api::Participant],
) -> Vec<WorkbenchParticipantView> {
    participants
        .iter()
        .map(|participant| WorkbenchParticipantView {
            bot_uuid: participant.bot_uuid.clone(),
            role: participant_role_to_wire(participant.role).to_string(),
            kind: participant.effective_kind(),
            mode: participant.mode,
        })
        .collect()
}

fn parse_group_status(status: &str) -> Result<GroupStatus, GroupUseCaseError> {
    match status.to_lowercase().as_str() {
        "active" => Ok(GroupStatus::Active),
        "completed" => Ok(GroupStatus::Completed),
        "closed" => Ok(GroupStatus::Closed),
        "inactive" => Ok(GroupStatus::Inactive),
        "error" => Ok(GroupStatus::Error),
        other => Err(GroupUseCaseError::InvalidGroupStatus(other.to_string())),
    }
}

fn participant_role(
    role: Option<&str>,
    is_driver: bool,
) -> Result<ParticipantRole, GroupUseCaseError> {
    match role {
        Some("driver") => Ok(ParticipantRole::Driver),
        Some("consultant") => Ok(ParticipantRole::Consultant),
        Some("observer") => Ok(ParticipantRole::Observer),
        Some("manager") => Ok(ParticipantRole::Manager),
        Some("worker") => Ok(ParticipantRole::Worker),
        Some(other) => Err(GroupUseCaseError::InvalidProposal(format!(
            "invalid participant role: {}",
            other
        ))),
        None if is_driver => Ok(ParticipantRole::Driver),
        None => Ok(ParticipantRole::Consultant),
    }
}

fn validate_participants_for_strategy(
    strategy: GroupStrategy,
    participants: &[Participant],
) -> Result<(), GroupUseCaseError> {
    if strategy != GroupStrategy::ManagerWorker {
        return Ok(());
    }
    let manager_count = participants
        .iter()
        .filter(|participant| participant.role == ParticipantRole::Manager)
        .count();
    if manager_count != 1 {
        return Err(GroupUseCaseError::InvalidProposal(format!(
            "manager_worker mode requires exactly one manager participant, got {}",
            manager_count
        )));
    }
    Ok(())
}

fn validate_human_constraints(
    strategy: GroupStrategy,
    participants: &[Participant],
    driver_bot_id: &str,
) -> Result<(), GroupUseCaseError> {
    if !participants.iter().any(|p| p.is_bot()) {
        return Err(GroupUseCaseError::InvalidProposal(
            "Group must have at least one bot participant".to_string(),
        ));
    }

    if driver_bot_id.starts_with("human_") {
        return Err(GroupUseCaseError::InvalidProposal(
            "Driver/Manager must be a bot, not a human actor".to_string(),
        ));
    }

    for p in participants.iter().filter(|p| p.is_human()) {
        match strategy {
            GroupStrategy::Chat | GroupStrategy::StateMachine => {
                if !matches!(p.role, ParticipantRole::Consultant | ParticipantRole::Observer) {
                    return Err(GroupUseCaseError::InvalidProposal(
                        "Human actors can only be consultant or observer in chat/state_machine groups".to_string(),
                    ));
                }
            }
            GroupStrategy::ManagerWorker => {
                if !matches!(p.role, ParticipantRole::Worker | ParticipantRole::Observer) {
                    return Err(GroupUseCaseError::InvalidProposal(
                        "Human actors can only be worker or observer in manager_worker groups".to_string(),
                    ));
                }
            }
        }
    }

    Ok(())
}

fn member_role(role: Option<&str>) -> ParticipantRole {
    match role {
        Some("driver") => ParticipantRole::Driver,
        Some("manager") => ParticipantRole::Manager,
        Some("worker") => ParticipantRole::Worker,
        Some("observer") => ParticipantRole::Observer,
        _ => ParticipantRole::Consultant,
    }
}

fn group_to_detail(group: DomainGroup) -> GroupDetailResult {
    group_to_detail_with_context(group, 0)
}

fn state_machine_initial_session_input(
    context: Option<&str>,
    topic: Option<&str>,
) -> Option<serde_json::Value> {
    first_non_empty([context, topic])
        .map(|query| serde_json::json!({ "query": query }))
}

fn first_non_empty<const N: usize>(values: [Option<&str>; N]) -> Option<&str> {
    values
        .into_iter()
        .flatten()
        .map(str::trim)
        .find(|value| !value.is_empty())
}

fn group_to_detail_with_context(group: DomainGroup, context_injected: u64) -> GroupDetailResult {
    let message_count = group.messages.len();
    GroupDetailResult {
        group_id: group.id,
        label: group.label,
        status: group.status,
        driver_bot_id: group.driver_bot,
        context: group.context,
        participants: group
            .participants
            .into_iter()
            .map(|participant| GroupParticipantView {
                bot_uuid: participant.bot_uuid,
                bot_name: participant.bot_name,
                kind: participant.kind,
                role: participant_role_to_wire(participant.role).to_string(),
                actor_kind: participant.actor_kind,
                mode: participant.mode,
            })
            .collect(),
        message_count,
        workspace: group.workspace,
        service_group_uuid: group.service_group_uuid,
        service_mode: group.service_mode,
        group_kind: group.group_kind,
        dm_pair_key: group.dm_pair_key,
        group_strategy: group.group_strategy,
        created_at: group.created_at,
        updated_at: group.updated_at,
        chat_url: None,
        context_injected,
        service_spec: group.service_spec.clone(),
        latest_running_session_id: None,
        originator: group.originator,
        visibility: group.visibility.clone(),
    }
}

fn group_to_list_entry(group: DomainGroup) -> GroupListEntry {
    let participant_count = group.participants.len();
    let message_count = group.messages.len();
    GroupListEntry {
        group_id: group.id,
        label: group.label,
        driver_bot_id: group.driver_bot,
        originator: group.originator.clone(),
        context: group.context,
        participants: group
            .participants
            .into_iter()
            .map(|participant| GroupParticipantView {
                bot_uuid: participant.bot_uuid,
                bot_name: participant.bot_name,
                kind: participant.kind,
                role: participant_role_to_wire(participant.role).to_string(),
                actor_kind: participant.actor_kind,
                mode: participant.mode,
            })
            .collect(),
        participant_count,
        message_count,
        created_at: group.created_at,
        updated_at: group.updated_at,
        group_kind: group.group_kind,
        group_strategy: group.group_strategy,
        visibility: group.visibility.clone(),
    }
}

fn group_has_non_absent_participant(group: &DomainGroup, actor_id: &str) -> bool {
    group.participants.iter().any(|participant| {
        participant.bot_uuid == actor_id
            && participant.effective_mode() != ParticipantMode::Absent
    })
}

fn participant_role_to_wire(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

fn dm_label(
    label: Option<String>,
    topic: Option<&str>,
    source_actor_id: &str,
    target_actor_id: &str,
) -> Option<String> {
    match (topic, label) {
        (Some(topic), _) => Some(format!("DM: {}", topic)),
        (None, Some(label)) => Some(label),
        (None, None) => Some(format!("DM: {} - {}", source_actor_id, target_actor_id)),
    }
}

fn to_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}
