//! Legacy Group application service implementation, split by concern.

use std::{collections::HashSet, sync::Arc};

use async_trait::async_trait;
use bcs_service_api::core::{
    GroupMutationCommand, GroupMutationKind, SessionAuthorizationCoreService,
};
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::{NoopParticipantViewBindingPort, ParticipantViewBindingPort};

use crate::noop::{
    EmptyRelationCoreService, EmptySessionManagementService, NoopSystemMessageService,
};
use bcs_service_api::types::{EventActor, EventActorType, MessageViewScope, OpeningMessageScope};
use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, BotRuntimeConnectionService,
    CanResolveInteraction, CanResolveInteractionCommand,
    ChannelBindingCleanupPort, CollaborationRuntimeService, DeliveryType, DmActorSpec,
    DmCreateCommand, DmCreateResult, FriendCoreService, Group as DomainGroup,
    GroupAddMemberCommand, GroupAddMemberResult, GroupCoreService, GroupCreateCommand,
    GroupDeleteCommand, GroupDeleteResult, GroupDetailCommand, GroupDetailResult, GroupKind,
    GroupListCommand, GroupListEntry, GroupListResult, GroupManagementService,
    GroupMutableFieldsPatch, GroupParticipantModeCommand, GroupParticipantModeResult,
    GroupParticipantView, GroupQueryService, GroupRemoveMemberCommand, GroupRemoveMemberResult,
    GroupRoutingPolicyCommand, GroupRoutingPolicyResult, GroupStatus, GroupStatusCommand,
    GroupStrategy, GroupTerminateCommand, GroupUpdateLabelCommand, GroupUpdateVisibilityCommand,
    GroupUseCaseError,
    InitialGroupRun, InitialGroupRunActivityKind, InitialGroupRunState,
    NoopChannelBindingCleanupPort, Participant, ParticipantMode, ParticipantRole, RegisteredBot,
    RelationCoreService, ServiceError, ServiceResult,
    SessionKind, SessionManagementService, SystemMessageEvent,
    WorkbenchChatAbortAuthorizationCommand,
    WorkbenchChatAuthorizationCommand, WorkbenchConnectCommand, WorkbenchConnectOutcome,
    WorkbenchParticipantView, WorkbenchSessionService, WorkbenchUseCaseError, backfill_bot_names,
    generated_group_id, validate_sender_routes,
};
use tracing::warn;

mod create;
mod guards;
mod operations;
mod projections;
mod queries;
mod workbench;

use projections::*;
use workbench::WorkbenchAuthorizedHuman;

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

#[derive(Clone)]
pub struct GroupManagement {
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    config: GroupConfig,
    system_message: Arc<dyn bcs_service_api::SystemMessageService>,
    session_management: Arc<dyn SessionManagementService>,
    channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    bot_runtime: Option<Arc<dyn BotRuntimeConnectionService>>,
    v1_openapi_create_policy: bool,
    /// The narrow authority hook (bot-authority plan
    /// Task 6.3): the Human→Bot control judgments in
    /// this service consult it live; a missing wiring
    /// fails closed.
    bot_authority: Option<Arc<dyn BotAuthorityPort>>,
    /// The narrow Session authorization-fact Core (F4,
    /// spec §8.3): Workbench connect/send admission reads
    /// an explicit Session target ONLY through this strict
    /// snapshot read — never the display cache, never a
    /// fallback to Group membership. A missing wiring
    /// fails closed.
    session_authorization: Option<Arc<dyn SessionAuthorizationCoreService>>,
}

pub struct GroupManagementWithRuntimeCleanup {
    inner: Arc<dyn GroupManagementService>,
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
}

impl GroupManagementWithRuntimeCleanup {
    pub fn new(
        inner: Arc<dyn GroupManagementService>,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        Self {
            inner,
            collaboration_runtime,
        }
    }

    async fn cleanup_group_runtime(&self, group_id: &str) -> Result<(), GroupUseCaseError> {
        self.collaboration_runtime
            .cancel_group_runs(group_id, "group_deleted")
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "Failed to cancel active state-machine runs for deleted group '{group_id}': {error}"
                ))
            })?;
        self.collaboration_runtime
            .delete_group_runtime_state(group_id)
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "Failed to delete state-machine runtime state for group '{group_id}': {error}"
                ))
            })?;
        Ok(())
    }
}

#[async_trait]
impl GroupManagementService for GroupManagementWithRuntimeCleanup {
    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.create_group(cmd).await
    }

    async fn create_dm(&self, cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        self.inner.create_dm(cmd).await
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.update_status(cmd).await
    }

    async fn add_member(
        &self,
        cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError> {
        self.inner.add_member(cmd).await
    }

    async fn remove_member(
        &self,
        cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError> {
        self.inner.remove_member(cmd).await
    }

    async fn delete_group(
        &self,
        cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError> {
        let result = self.inner.delete_group(cmd).await?;
        self.cleanup_group_runtime(&result.group_id).await?;
        Ok(result)
    }

    async fn terminate_group(
        &self,
        cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.terminate_group(cmd).await
    }

    async fn update_label(
        &self,
        cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.update_label(cmd).await
    }

    async fn update_visibility(
        &self,
        cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        self.inner.update_visibility(cmd).await
    }

    async fn update_routing_policy(
        &self,
        cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        self.inner.update_routing_policy(cmd).await
    }

    async fn update_participant_mode(
        &self,
        cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError> {
        self.inner.update_participant_mode(cmd).await
    }

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
            channel_binding_cleanup: Arc::new(NoopChannelBindingCleanupPort),
            participant_view_bindings: Arc::new(NoopParticipantViewBindingPort),
            bot_runtime: None,
            v1_openapi_create_policy: false,
            bot_authority: None,
            session_authorization: None,
        }
    }

    pub fn with_bot_runtime(mut self, bot_runtime: Arc<dyn BotRuntimeConnectionService>) -> Self {
        self.bot_runtime = Some(bot_runtime);
        self
    }

    /// Wire the authority hook (rows 14/15/16,
    /// bot-authority plan Task 6.3).
    pub fn with_bot_authority(mut self, bot_authority: Arc<dyn BotAuthorityPort>) -> Self {
        self.bot_authority = Some(bot_authority);
        self
    }

    /// The wired authority hook; failing to wire one is
    /// a composition error and must fail closed (never
    /// deny-as-Ok).
    pub(crate) fn authority(
        &self,
    ) -> Result<&dyn BotAuthorityPort, GroupUseCaseError> {
        self.bot_authority.as_deref().ok_or_else(|| {
            GroupUseCaseError::Service(ServiceError::InternalError(
                "bot authority hook is not wired".to_string(),
            ))
        })
    }

    /// Wire the strict Session authorization snapshot Core
    /// (F4, bot-authority plan §3).
    pub fn with_session_authorization(
        mut self,
        session_authorization: Arc<dyn SessionAuthorizationCoreService>,
    ) -> Self {
        self.session_authorization = Some(session_authorization);
        self
    }

    /// The wired Session authorization Core; failing to
    /// wire one is a composition error and must fail
    /// closed (never degrade to a display-cache read).
    pub(crate) fn session_authorization(
        &self,
    ) -> Result<&dyn SessionAuthorizationCoreService, WorkbenchUseCaseError> {
        self.session_authorization.as_deref().ok_or_else(|| {
            WorkbenchUseCaseError::Service(ServiceError::InternalError(
                "session authorization snapshot service is not wired".to_string(),
            ))
        })
    }

    /// Row 14/15/16 Human→Bot control leg: a live
    /// `can_manage` edge for the staff id; the
    /// historical `created_by` fact, the creator
    /// relation edge and the bot-id owner suffix no
    /// longer authorize anything here (spec §3.2).
    pub(crate) async fn human_controls_bot(
        &self,
        staff_no: &str,
        bot_id: &str,
    ) -> Result<bool, GroupUseCaseError> {
        match self.authority()?.can_manage(staff_no, bot_id).await {
            Ok(result) => Ok(result),
            Err(ServiceError::Forbidden(message)) => {
                Err(GroupUseCaseError::Forbidden(message))
            }
            Err(other) => Err(ServiceError::InternalError(format!(
                "bot authority lookup failed: {other}"
            ))
            .into()),
        }
    }

    pub fn with_channel_binding_cleanup(
        mut self,
        channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    ) -> Self {
        self.channel_binding_cleanup = channel_binding_cleanup;
        self
    }

    pub fn with_participant_view_bindings(
        mut self,
        participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    ) -> Self {
        self.participant_view_bindings = participant_view_bindings;
        self
    }

    /// Select the OpenAPI v1 group-creation reachability policy.
    ///
    /// Legacy instances retain their original caller/originator checks. A
    /// dedicated V1 instance validates collaboration from the selected driver,
    /// after the V1 facade has verified Principal-to-driver eligibility.
    pub fn for_v1_openapi(mut self) -> Self {
        self.v1_openapi_create_policy = true;
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
}

pub(crate) fn group_has_non_absent_participant(group: &DomainGroup, actor_id: &str) -> bool {
    group.participants.iter().any(|participant| {
        participant.bot_uuid == actor_id && participant.effective_mode() != ParticipantMode::Absent
    })
}

pub(crate) fn to_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

pub(crate) fn staff_no_from_bound_actor(bound_actor_id: Option<&str>) -> Result<&str, WorkbenchUseCaseError> {
    let actor_id = bound_actor_id.ok_or(WorkbenchUseCaseError::Unauthorized)?;
    let staff_no = actor_id
        .strip_prefix("human_")
        .ok_or(WorkbenchUseCaseError::Unauthorized)?;
    if staff_no.is_empty() {
        return Err(WorkbenchUseCaseError::Unauthorized);
    }
    Ok(staff_no)
}

/// Whether the bound Human participates in `group` directly,
/// or through a Bot participant it CURRENTLY controls (rows
/// 14/15, bot-authority plan Task 6.3): a live `can_manage`
/// edge per Bot participant — never the historical
/// `created_by` and never the bot-id owner suffix. A
/// same-named manager role inside the Group is NOT what this
/// helper checks: Group-level management gating continues to
/// derive from the Group's own rules (driver/originator
/// strategy). Group facts come from the STRICT authorization
/// snapshot (F4), never from a display cache.
pub(crate) async fn human_has_group_access(
    service: &GroupManagement,
    group: &bcs_service_api::types::authorization_snapshot::GroupAuthorizationSnapshot,
    actor_id: &str,
    staff_no: &str,
) -> Result<bool, GroupUseCaseError> {
    if group
        .participants
        .iter()
        .any(|participant| participant.actor_id == actor_id)
    {
        return Ok(true);
    }

    for participant in group
        .participants
        .iter()
        .filter(|participant| participant.actor_kind == ActorKind::Bot)
    {
        if service
            .human_controls_bot(staff_no, &participant.actor_id)
            .await?
        {
            return Ok(true);
        }
    }

    Ok(false)
}

pub(crate) fn group_mutation_command(
    group_id: &str,
    actor_id: &str,
    mutation: GroupMutationKind,
) -> GroupMutationCommand {
    let actor_type = if actor_id == "system" {
        EventActorType::System
    } else if actor_id.starts_with("human_") {
        EventActorType::Human
    } else {
        EventActorType::Bot
    };
    GroupMutationCommand {
        group_id: group_id.to_string(),
        actor: EventActor {
            actor_type,
            id: actor_id.to_string(),
            display_name: None,
        },
        correlation_id: None,
        trace_id: None,
        mutation,
    }
}

pub(crate) fn parse_group_status(status: &str) -> Result<GroupStatus, GroupUseCaseError> {
    match status.to_lowercase().as_str() {
        "active" => Ok(GroupStatus::Active),
        "completed" => Ok(GroupStatus::Completed),
        "closed" => Ok(GroupStatus::Closed),
        "inactive" => Ok(GroupStatus::Inactive),
        "error" => Ok(GroupStatus::Error),
        other => Err(GroupUseCaseError::InvalidGroupStatus(other.to_string())),
    }
}

pub(crate) fn participant_role(
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

pub(crate) fn validate_participants_for_strategy(
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

pub(crate) fn validate_human_constraints(
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
                if !matches!(
                    p.role,
                    ParticipantRole::Consultant | ParticipantRole::Observer
                ) {
                    return Err(GroupUseCaseError::InvalidProposal(
                        "Human actors can only be consultant or observer in chat/state_machine groups".to_string(),
                    ));
                }
            }
            GroupStrategy::ManagerWorker => {
                if !matches!(p.role, ParticipantRole::Worker | ParticipantRole::Observer) {
                    return Err(GroupUseCaseError::InvalidProposal(
                        "Human actors can only be worker or observer in manager_worker groups"
                            .to_string(),
                    ));
                }
            }
        }
    }

    Ok(())
}

pub(crate) fn default_added_member_role(strategy: GroupStrategy) -> ParticipantRole {
    match strategy {
        GroupStrategy::ManagerWorker => ParticipantRole::Worker,
        GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Consultant,
    }
}
