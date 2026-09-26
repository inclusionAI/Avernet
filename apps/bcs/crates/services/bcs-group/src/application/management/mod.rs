//! Legacy Group application service implementation, split by concern.

use std::{collections::HashSet, sync::Arc};

use async_trait::async_trait;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::{NoopParticipantViewBindingPort, ParticipantViewBindingPort};

use crate::core::validate_service_spec_patch;
use crate::noop::{
    EmptyRelationCoreService, EmptySessionManagementService, NoopSystemMessageService,
};
use bcs_service_api::types::{EventActor, EventActorType, MessageViewScope, OpeningMessageScope};
use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, BotRuntimeConnectionService,
    CallbackChannelConfig, CanResolveInteraction, CanResolveInteractionCommand,
    ChannelBindingCleanupPort, CollaborationRuntimeService, DeliveryType, DmActorSpec,
    DmCreateCommand, DmCreateResult, FriendCoreService, Group as DomainGroup,
    GroupAddMemberCommand, GroupAddMemberResult, GroupCoreService, GroupCreateCommand,
    GroupDeleteCommand, GroupDeleteResult, GroupDetailCommand, GroupDetailResult, GroupKind,
    GroupListCommand, GroupListEntry, GroupListResult, GroupManagementService,
    GroupMutableFieldsPatch, GroupParticipantModeCommand, GroupParticipantModeResult,
    GroupParticipantView, GroupPatchSettingsCommand, GroupPatchSettingsConflict,
    GroupPatchSettingsResult, GroupQueryService, GroupRemoveMemberCommand, GroupRemoveMemberResult,
    GroupRoutingPolicyCommand, GroupRoutingPolicyResult, GroupStatus, GroupStatusCommand,
    GroupStrategy, GroupTerminateCommand, GroupUpdateLabelCommand, GroupUpdateVisibilityCommand,
    GroupUpdateWorkspaceCommand, GroupUseCaseError, GroupWorkspaceQueryCommand,
    GroupWorkspaceResult, InitialGroupRun, InitialGroupRunActivityKind, InitialGroupRunState,
    NoopChannelBindingCleanupPort, Participant, ParticipantMode, ParticipantRole, RegisteredBot,
    RelationCoreService, ServiceError, ServiceResult, ServiceSpec, ServiceSpecPatchConflictField,
    Session, SessionKind, SessionManagementService, SystemMessageEvent,
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
    outbound_url_guard: OutboundUrlGuard,
    v1_openapi_create_policy: bool,
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

    async fn update_workspace(
        &self,
        cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        self.inner.update_workspace(cmd).await
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

    async fn patch_group_settings(
        &self,
        cmd: GroupPatchSettingsCommand,
    ) -> Result<GroupPatchSettingsResult, GroupUseCaseError> {
        self.inner.patch_group_settings(cmd).await
    }
}

pub(crate) fn validate_service_spec_callback_urls(
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
            channel_binding_cleanup: Arc::new(NoopChannelBindingCleanupPort),
            participant_view_bindings: Arc::new(NoopParticipantViewBindingPort),
            bot_runtime: None,
            outbound_url_guard: OutboundUrlGuard::strict(),
            v1_openapi_create_policy: false,
        }
    }

    pub fn with_bot_runtime(mut self, bot_runtime: Arc<dyn BotRuntimeConnectionService>) -> Self {
        self.bot_runtime = Some(bot_runtime);
        self
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

    pub fn with_outbound_url_guard(mut self, outbound_url_guard: OutboundUrlGuard) -> Self {
        self.outbound_url_guard = outbound_url_guard;
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

pub(crate) async fn human_has_group_access(
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

pub(crate) fn bot_belongs_to_staff(bot_uuid: &str, created_by: Option<&str>, staff_no: &str) -> bool {
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

pub(crate) fn find_session_participant(
    session: &Session,
    actor_id: &str,
) -> Option<bcs_service_api::Participant> {
    session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == actor_id)
        .cloned()
}

pub(crate) fn workbench_participants(group: &DomainGroup) -> Vec<WorkbenchParticipantView> {
    workbench_participants_from_slice(&group.participants)
}

pub(crate) fn workbench_participants_from_slice(
    participants: &[bcs_service_api::Participant],
) -> Vec<WorkbenchParticipantView> {
    participants
        .iter()
        .map(|participant| WorkbenchParticipantView {
            bot_uuid: participant.bot_uuid.clone(),
            role: participant_role_to_wire(participant.role).to_string(),
            kind: participant.effective_kind(),
            mode: participant.mode,
            message_view_scope: participant.message_view_scope,
        })
        .collect()
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
