//! Versioned Group application facade for the BCN V1 API.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    Actor, AddGroupParticipant, ApplicationError, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CollaborationGroupDetail, CreateCollaborationGroup,
    CreateDirectMessageGroup, CreateGroup, CreateGroupOutcome, CreateGroupSpec, CreateParticipant,
    DeleteGroup, DeleteGroupParticipant, DeleteResult, DirectMessageGroupDetail,
    DirectMessageGroupSummary, GetGroup, GroupDetail, GroupEventSubscriptionProvisioner,
    GroupKindFilter, GroupService, GroupStatus, GroupStrategy as V1GroupStrategy, GroupSummary,
    GroupVisibility, HumanMentionNotifyMode, HumanPrincipal, IdentityPolicy,
    InlineGroupEventSubscriptionRequest, ListGroups, ListPublicGroups,
    ManagerWorkerConfiguration, Membership, MembershipFilter,
    NormalGroupSummary, Page, Participant as V1Participant, PreparedGroupEventSubscriptions,
    Principal,
    StateMachineConfiguration, StateMachineDefinition, StateMachineDefinitionReference,
    StateMachineParticipantBinding, UpdateGroup, UpdateGroupParticipant,
    require_authenticated_user, require_human, select_principal,
};
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::port::{NoopParticipantViewBindingPort, ParticipantViewBindingPort};
use bcs_service_api::types::{EventActor, EventActorType, OpeningMessageScope};
use bcs_service_api::{
    ActorKind, ActorStatus, AuthenticatedHumanCaller, BotRegistryCoreService,
    CollaborationDefinitionRef, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, DefaultDelivery, DmCreateCommand, FriendCoreService,
    Group as DomainGroup, GroupAddMemberCommand, GroupCoreService, GroupCreateCommand,
    GroupCreateParticipantCommand, GroupDeleteCommand, GroupKind, GroupManagementService,
    GroupMutableFieldsPatch, GroupParticipantView, GroupRemoveMemberCommand, GroupStrategy,
    GroupUseCaseError, ParticipantMode, RelationCoreService, RoutingMode, RoutingPolicy,
    RuntimeParticipantBinding, ServiceError, SessionManagementService, StartStateMachineRunCommand,
    generated_group_id,
};
use serde_json::Value;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};

#[derive(Debug, Clone)]
pub struct GroupServiceConfig {
    pub relation_env: String,
}

/// OpenAPI v1 Group facade.
///
/// It owns authenticated-Caller resource authorization and V1 projections while
/// delegating existing group creation/deletion side effects to the legacy-
/// compatible application service. No HTTP type crosses this boundary.
pub struct GroupServiceImpl {
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    relation: Arc<dyn RelationCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    management: Arc<dyn GroupManagementService>,
    participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
    event_subscription_provisioner: Option<Arc<dyn GroupEventSubscriptionProvisioner>>,
    config: GroupServiceConfig,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct GroupProvisioningReconcileOutcome {
    pub finalized: u64,
    pub compensated: u64,
    pub deferred: u64,
}

/// Recovers Groups left between resource creation and Event finalization.
///
/// Startup may pass `minimum_age_ms = 0`; periodic runs should use a grace
/// period so they never race an in-flight create request.
pub struct GroupProvisioningReconciler {
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
    provisioner: Arc<dyn GroupEventSubscriptionProvisioner>,
}

struct RunningProvisioningReconciler {
    cancellation: CancellationToken,
    handle: JoinHandle<()>,
}

pub struct GroupProvisioningLifecycle {
    reconciler: Arc<GroupProvisioningReconciler>,
    poll_interval: Duration,
    minimum_age_ms: u64,
    shutdown_timeout: Duration,
    running: Mutex<Option<RunningProvisioningReconciler>>,
}

impl GroupProvisioningLifecycle {
    pub fn new(
        reconciler: Arc<GroupProvisioningReconciler>,
        poll_interval: Duration,
        minimum_age_ms: u64,
        shutdown_timeout: Duration,
    ) -> Result<Self, LifecycleError> {
        if poll_interval.is_zero() || shutdown_timeout.is_zero() {
            return Err(LifecycleError::Precondition(
                "Group provisioning lifecycle intervals must be non-zero".to_string(),
            ));
        }
        Ok(Self {
            reconciler,
            poll_interval,
            minimum_age_ms,
            shutdown_timeout,
            running: Mutex::new(None),
        })
    }
}

#[async_trait]
impl ServiceLifecycle for GroupProvisioningLifecycle {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        if self
            .running
            .lock()
            .map_err(|_| LifecycleError::Transient("provisioning lifecycle lock poisoned".into()))?
            .is_some()
        {
            return Ok(());
        }
        // No API traffic is admitted while lifecycle initialization runs, so
        // every pre-existing provisioning row is safe to reconcile without an
        // age grace period.
        self.reconciler.reconcile_once(system_now_ms(), 0).await;
        let cancellation = CancellationToken::new();
        let worker_cancellation = cancellation.clone();
        let reconciler = self.reconciler.clone();
        let poll_interval = self.poll_interval;
        let minimum_age_ms = self.minimum_age_ms;
        let handle = tokio::spawn(async move {
            loop {
                tokio::select! {
                    () = worker_cancellation.cancelled() => return,
                    () = tokio::time::sleep(poll_interval) => {
                        reconciler
                            .reconcile_once(system_now_ms(), minimum_age_ms)
                            .await;
                    }
                }
            }
        });
        let mut running = self.running.lock().map_err(|_| {
            LifecycleError::Transient("provisioning lifecycle lock poisoned".into())
        })?;
        if running.is_some() {
            cancellation.cancel();
            handle.abort();
            return Ok(());
        }
        *running = Some(RunningProvisioningReconciler {
            cancellation,
            handle,
        });
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        let running = self
            .running
            .lock()
            .map_err(|_| {
                LifecycleError::ShutdownFailed("provisioning lifecycle lock poisoned".into())
            })?
            .take();
        let Some(mut running) = running else {
            return Ok(());
        };
        running.cancellation.cancel();
        match tokio::time::timeout(self.shutdown_timeout, &mut running.handle).await {
            Ok(result) => result.map_err(|error| {
                LifecycleError::ShutdownFailed(format!(
                    "Group provisioning reconciler join failed: {error}"
                ))
            }),
            Err(_) => {
                running.handle.abort();
                let _ = running.handle.await;
                Err(LifecycleError::ShutdownTimeout(
                    "Group provisioning reconciler did not stop in time".to_string(),
                ))
            }
        }
    }
}

fn system_now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

impl GroupProvisioningReconciler {
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
        provisioner: Arc<dyn GroupEventSubscriptionProvisioner>,
    ) -> Self {
        Self {
            groups,
            sessions,
            collaboration_runtime,
            provisioner,
        }
    }

    pub async fn reconcile_once(
        &self,
        now_ms: u64,
        minimum_age_ms: u64,
    ) -> GroupProvisioningReconcileOutcome {
        let mut outcome = GroupProvisioningReconcileOutcome::default();
        let groups = self.groups.list().await;
        let known_group_ids = groups
            .iter()
            .map(|group| group.id.clone())
            .collect::<HashSet<_>>();
        for group in groups.into_iter().filter(|group| {
            group.record_status == "provisioning"
                && group.created_at.saturating_add(minimum_age_ms) <= now_ms
        }) {
            let prepared = match self.provisioner.recover_pending(&group.id).await {
                Ok(prepared) => prepared,
                Err(_) => {
                    outcome.deferred = outcome.deferred.saturating_add(1);
                    continue;
                }
            };
            let sessions = match self
                .sessions
                .list_by_group(&group.id, None, 0, 100, None, None)
                .await
            {
                Ok(sessions) => sessions,
                Err(_) => {
                    outcome.deferred = outcome.deferred.saturating_add(1);
                    continue;
                }
            };
            let shape_is_valid = match group.group_kind {
                GroupKind::Normal => sessions.len() == 1,
                GroupKind::Dm => sessions.is_empty(),
            };
            if prepared.subscription_ids.is_empty() || !shape_is_valid {
                if self.compensate(&group, &sessions, &prepared).await {
                    outcome.compensated = outcome.compensated.saturating_add(1);
                } else {
                    outcome.deferred = outcome.deferred.saturating_add(1);
                }
                continue;
            }
            if group.group_strategy == GroupStrategy::StateMachine {
                let Some(runtime) = self.collaboration_runtime.as_ref() else {
                    if self.compensate(&group, &sessions, &prepared).await {
                        outcome.compensated = outcome.compensated.saturating_add(1);
                    } else {
                        outcome.deferred = outcome.deferred.saturating_add(1);
                    }
                    continue;
                };
                match runtime.get_group_collaboration_definition(&group.id).await {
                    Ok(definition) if definition.default_definition.is_some() => {}
                    Ok(_)
                    | Err(
                        CollaborationRuntimeError::DefinitionNotFound(_, _)
                        | CollaborationRuntimeError::InvalidDefinition(_)
                        | CollaborationRuntimeError::InvalidParticipantBinding(_)
                        | CollaborationRuntimeError::InvalidRequest(_),
                    ) => {
                        if self.compensate(&group, &sessions, &prepared).await {
                            outcome.compensated = outcome.compensated.saturating_add(1);
                        } else {
                            outcome.deferred = outcome.deferred.saturating_add(1);
                        }
                        continue;
                    }
                    Err(_) => {
                        outcome.deferred = outcome.deferred.saturating_add(1);
                        continue;
                    }
                }
            }
            match self
                .provisioner
                .finalize(&prepared, &group, sessions.first())
                .await
            {
                Ok(()) => outcome.finalized = outcome.finalized.saturating_add(1),
                Err(_) => outcome.deferred = outcome.deferred.saturating_add(1),
            }
        }
        match self.provisioner.list_pending_groups().await {
            Ok(pending_groups) => {
                for pending in pending_groups.into_iter().filter(|pending| {
                    !known_group_ids.contains(pending.prepared.group_id.as_str())
                        && pending.created_at_ms.saturating_add(minimum_age_ms) <= now_ms
                }) {
                    if self
                        .provisioner
                        .cancel(
                            &pending.prepared,
                            "group_provisioning_orphan_recovery_rollback",
                        )
                        .await
                        .is_ok()
                    {
                        outcome.compensated = outcome.compensated.saturating_add(1);
                    } else {
                        outcome.deferred = outcome.deferred.saturating_add(1);
                    }
                }
            }
            Err(_) => outcome.deferred = outcome.deferred.saturating_add(1),
        }
        outcome
    }

    async fn compensate(
        &self,
        group: &DomainGroup,
        sessions: &[bcs_service_api::Session],
        prepared: &PreparedGroupEventSubscriptions,
    ) -> bool {
        let mut succeeded = true;
        if group.group_strategy == GroupStrategy::StateMachine
            && let Some(runtime) = self.collaboration_runtime.as_ref()
        {
            succeeded &= runtime
                .cancel_group_runs(&group.id, "group_provisioning_recovery_rollback")
                .await
                .is_ok();
            succeeded &= runtime.delete_group_runtime_state(&group.id).await.is_ok();
        }
        for session in sessions {
            succeeded &= self.sessions.delete(&session.id).await.is_ok();
        }
        succeeded &= self.groups.delete(&group.id).await.is_ok();
        succeeded &= self
            .provisioner
            .cancel(prepared, "group_provisioning_recovery_rollback")
            .await
            .is_ok();
        succeeded
    }
}

mod authorization;
mod create;
mod projections;
mod service;

#[cfg(test)]
mod tests;


impl GroupServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        friends: Arc<dyn FriendCoreService>,
        relation: Arc<dyn RelationCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        management: Arc<dyn GroupManagementService>,
        config: GroupServiceConfig,
    ) -> Self {
        Self {
            groups,
            registry,
            friends,
            relation,
            sessions,
            management,
            participant_view_bindings: Arc::new(NoopParticipantViewBindingPort),
            collaboration_runtime: None,
            event_subscription_provisioner: None,
            config,
        }
    }

    pub fn with_collaboration_runtime(
        mut self,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        self.collaboration_runtime = Some(collaboration_runtime);
        self
    }

    pub fn with_participant_view_bindings(
        mut self,
        participant_view_bindings: Arc<dyn ParticipantViewBindingPort>,
    ) -> Self {
        self.participant_view_bindings = participant_view_bindings;
        self
    }

    pub fn with_event_subscription_provisioner(
        mut self,
        provisioner: Arc<dyn GroupEventSubscriptionProvisioner>,
    ) -> Self {
        self.event_subscription_provisioner = Some(provisioner);
        self
    }

}



fn opening_message_scope(strategy: GroupStrategy) -> OpeningMessageScope {
    match strategy {
        GroupStrategy::StateMachine => OpeningMessageScope::StateMachineRun,
        GroupStrategy::Chat | GroupStrategy::ManagerWorker => OpeningMessageScope::Session,
    }
}

#[derive(Debug)]
struct DetailCommon {
    group_id: String,
    version: i32,
    name: Option<String>,
    status: GroupStatus,
    visibility: GroupVisibility,
    context: Option<String>,
    originator_actor_id: String,
    participants: Vec<V1Participant>,
    human_mention_notify_mode: HumanMentionNotifyMode,
    created_at: u64,
    updated_at: u64,
}

fn project_participant(participant: &bcs_service_api::Participant) -> V1Participant {
    V1Participant {
        actor_id: participant.bot_uuid.clone(),
        actor_kind: participant.actor_kind,
        name: participant.bot_name.clone(),
        role: participant.role,
        mode: participant.effective_mode(),
        tags: participant.tags.clone(),
        message_view_scope: participant.message_view_scope,
    }
}

/// Project a legacy `GroupParticipantView` (returned by `add_member`) into the
/// V1 `Participant` shape. The view carries `role` as a wire string, so it is
/// parsed back into the typed enum; a missing `mode` falls back to the
/// kind-aware default, mirroring `Participant::effective_mode`.
fn participant_view_to_v1(view: GroupParticipantView) -> V1Participant {
    V1Participant {
        actor_id: view.bot_uuid,
        actor_kind: view.actor_kind,
        name: view.bot_name,
        role: parse_participant_role(&view.role),
        mode: view
            .mode
            .unwrap_or_else(|| ParticipantMode::default_for(view.actor_kind)),
        tags: view.tags,
        message_view_scope: view.message_view_scope,
    }
}

fn normalize_participant_tags(tags: Vec<String>) -> Vec<String> {
    tags.into_iter()
        .map(|tag| tag.trim().to_string())
        .filter(|tag| !tag.is_empty())
        .collect()
}

fn parse_participant_role(role: &str) -> bcs_service_api::ParticipantRole {
    match role {
        "driver" => bcs_service_api::ParticipantRole::Driver,
        "manager" => bcs_service_api::ParticipantRole::Manager,
        "worker" => bcs_service_api::ParticipantRole::Worker,
        "observer" => bcs_service_api::ParticipantRole::Observer,
        _ => bcs_service_api::ParticipantRole::Consultant,
    }
}

fn project_status(status: bcs_service_api::GroupStatus) -> GroupStatus {
    match status {
        bcs_service_api::GroupStatus::Active => GroupStatus::Active,
        bcs_service_api::GroupStatus::Completed => GroupStatus::Completed,
        bcs_service_api::GroupStatus::Error => GroupStatus::Error,
        bcs_service_api::GroupStatus::Closed => GroupStatus::Closed,
        bcs_service_api::GroupStatus::Inactive => GroupStatus::Inactive,
    }
}

fn project_visibility(visibility: &str) -> Result<GroupVisibility, ApplicationError> {
    match visibility {
        "private" => Ok(GroupVisibility::Private),
        "public" => Ok(GroupVisibility::Public),
        other => Err(ApplicationError::internal(format!(
            "stored Group has unsupported visibility '{other}'"
        ))),
    }
}

fn project_strategy(strategy: GroupStrategy) -> V1GroupStrategy {
    match strategy {
        GroupStrategy::Chat => V1GroupStrategy::Chat,
        GroupStrategy::ManagerWorker => V1GroupStrategy::ManagerWorker,
        GroupStrategy::StateMachine => V1GroupStrategy::StateMachine,
    }
}

fn project_delivery(delivery: DefaultDelivery) -> BotFinalDelivery {
    match delivery {
        DefaultDelivery::SendToDriver => BotFinalDelivery::SendToDriver,
        DefaultDelivery::InjectObservers => BotFinalDelivery::InjectObservers,
    }
}

fn persist_delivery(delivery: BotFinalDelivery) -> DefaultDelivery {
    match delivery {
        BotFinalDelivery::SendToDriver => DefaultDelivery::SendToDriver,
        BotFinalDelivery::InjectObservers => DefaultDelivery::InjectObservers,
    }
}

fn map_create_collaboration(
    collaboration: CollaborationConfiguration,
) -> (
    GroupStrategy,
    Option<RoutingPolicy>,
    Option<StateMachineConfiguration>,
) {
    match collaboration {
        CollaborationConfiguration::Chat(configuration) => (
            GroupStrategy::Chat,
            Some(RoutingPolicy {
                mode: RoutingMode::Hybrid,
                default_bot_final_delivery: persist_delivery(
                    configuration.delivery_policy.bot_final_delivery,
                ),
                sender_routes: HashMap::new(),
            }),
            None,
        ),
        CollaborationConfiguration::ManagerWorker(_) => (GroupStrategy::ManagerWorker, None, None),
        CollaborationConfiguration::StateMachine(configuration) => {
            (GroupStrategy::StateMachine, None, Some(configuration))
        }
    }
}

fn role_name(role: bcs_service_api::ParticipantRole) -> &'static str {
    match role {
        bcs_service_api::ParticipantRole::Driver => "driver",
        bcs_service_api::ParticipantRole::Consultant => "consultant",
        bcs_service_api::ParticipantRole::Manager => "manager",
        bcs_service_api::ParticipantRole::Worker => "worker",
        bcs_service_api::ParticipantRole::Observer => "observer",
    }
}

fn human_display_name(human: &HumanPrincipal) -> String {
    human
        .subject
        .display_name
        .clone()
        .or_else(|| human.subject.full_name.clone())
        .unwrap_or_else(|| human.subject.username.clone())
}

fn event_actor_for_principal(principal: &Principal) -> EventActor {
    match principal {
        Principal::Human(human) => EventActor {
            actor_type: EventActorType::Human,
            id: format!("human_{}", human.subject.id),
            display_name: Some(human_display_name(human)),
        },
        Principal::Bot(bot) => EventActor {
            actor_type: EventActorType::Bot,
            id: bot.bot_uuid.clone(),
            display_name: None,
        },
    }
}

async fn cleanup_deleted_group_runtime(
    runtime: &dyn CollaborationRuntimeService,
    group_id: &str,
) -> Option<String> {
    let mut errors = Vec::new();
    if let Err(error) = runtime.cancel_group_runs(group_id, "group_deleted").await {
        errors.push(format!("run cancellation: {error}"));
    }
    if let Err(error) = runtime.delete_group_runtime_state(group_id).await {
        errors.push(format!("runtime state: {error}"));
    }
    (!errors.is_empty()).then(|| errors.join("; "))
}

fn visibility_name(visibility: GroupVisibility) -> &'static str {
    match visibility {
        GroupVisibility::Private => "private",
        GroupVisibility::Public => "public",
    }
}

fn saturating_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

fn map_group_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::Unauthorized(_) => ApplicationError::Unauthenticated,
        GroupUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        GroupUseCaseError::ProposalNotFound(message)
        | GroupUseCaseError::ProposalExpired(message) => {
            ApplicationError::not_found("not_found", message)
        }
        GroupUseCaseError::InvalidHistoryLimit(limit) => {
            ApplicationError::invalid("invalid_request", format!("invalid history limit {limit}"))
        }
        GroupUseCaseError::ActorNotFound(actor_id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{actor_id}' was not found"))
        }
        GroupUseCaseError::InvalidParticipantMode { .. } => {
            ApplicationError::invalid("invalid_participant", error.to_string())
        }
        GroupUseCaseError::Conflict(message) => ApplicationError::conflict("conflict", message),
        GroupUseCaseError::Service(error) => map_service_error(error),
    }
}

fn map_delete_group_error(error: GroupUseCaseError) -> ApplicationError {
    match error {
        GroupUseCaseError::InvalidProposal(message)
            if message == "DM groups cannot be deleted or left" =>
        {
            ApplicationError::conflict("conflict", message)
        }
        other => map_group_error(other),
    }
}

fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::GroupNotFound(id) => {
            ApplicationError::not_found("group_not_found", format!("Group '{id}' was not found"))
        }
        ServiceError::BotNotFound(id) | ServiceError::BotNotRegistered(id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{id}' was not found"))
        }
        ServiceError::ParticipantNotFound(id) => ApplicationError::not_found(
            "participant_not_found",
            format!("Participant '{id}' was not found"),
        ),
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        ServiceError::Conflict(message) => ApplicationError::conflict("conflict", message),
        ServiceError::InvalidOperation { message, .. }
        | ServiceError::SessionInvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        ServiceError::ExistNonPublicBots { .. } => ApplicationError::conflict(
            "non_public_participant",
            "All Bot participants must be public for this operation",
        ),
        ServiceError::BotHidden(id) => {
            ApplicationError::forbidden(format!("Bot '{id}' is hidden and cannot collaborate"))
        }
        ServiceError::PrivateBotCannotCollaborate => {
            ApplicationError::forbidden("Private Bot cannot collaborate")
        }
        ServiceError::NotFriends(_) => {
            ApplicationError::forbidden("Actors are not collaboration-eligible")
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::DefinitionNotFound(id, version) => ApplicationError::not_found(
            "collaboration_definition_not_found",
            format!("Collaboration definition '{id}@{version}' was not found"),
        ),
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => ApplicationError::forbidden(message),
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

fn map_runtime_and_rollback_error(
    runtime_error: CollaborationRuntimeError,
    session_cleanup_error: Option<String>,
    group_cleanup_error: Option<String>,
) -> ApplicationError {
    if session_cleanup_error.is_none() && group_cleanup_error.is_none() {
        return map_runtime_error(runtime_error);
    }
    ApplicationError::internal(format!(
        "StateMachine runtime configuration failed: {runtime_error}; rollback failed: session cleanup: {}; group cleanup: {}",
        session_cleanup_error.as_deref().unwrap_or("ok"),
        group_cleanup_error.as_deref().unwrap_or("ok"),
    ))
}
