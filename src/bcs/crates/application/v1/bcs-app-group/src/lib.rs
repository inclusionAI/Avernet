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
    GroupVisibility, HumanPrincipal, IdentityPolicy, InlineGroupEventSubscriptionRequest,
    ListGroups, ListPublicGroups, ManagerWorkerConfiguration, Membership, MembershipFilter,
    NormalGroupSummary, Page, Participant as V1Participant, PreparedGroupEventSubscriptions,
    Principal,
    StateMachineConfiguration, StateMachineDefinition, StateMachineDefinitionReference,
    StateMachineParticipantBinding, UpdateGroup, UpdateGroupParticipant,
    require_authenticated_user, require_human, select_principal,
};
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
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

    pub fn with_event_subscription_provisioner(
        mut self,
        provisioner: Arc<dyn GroupEventSubscriptionProvisioner>,
    ) -> Self {
        self.event_subscription_provisioner = Some(provisioner);
        self
    }

    async fn load_bot(
        &self,
        bot_uuid: &str,
    ) -> Result<bcs_service_api::RegisteredBot, ApplicationError> {
        self.registry
            .try_get(bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_uuid}' was not found"),
                )
            })
    }

    async fn resolve_view_actor(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        requested: Option<&str>,
    ) -> Result<String, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        let human_actor_id = format!("human_{}", user.id);
        let Some(requested) = requested else {
            return Ok(human_actor_id);
        };
        if requested == human_actor_id {
            return Ok(human_actor_id);
        }
        if requested.starts_with("human_") {
            return Err(ApplicationError::forbidden(
                "The explicit Human View Actor must identify the authenticated User",
            ));
        }
        let bot = self.load_bot(requested).await.map_err(|error| match error {
            ApplicationError::NotFound { .. } => {
                ApplicationError::forbidden("The explicit View Actor is not authorized")
            }
            other => other,
        })?;
        if bot.actor_kind == ActorKind::Bot
            && bot.created_by.as_deref() == Some(user.id.as_str())
        {
            Ok(requested.to_string())
        } else {
            Err(ApplicationError::forbidden(
                "The explicit View Actor is not authorized",
            ))
        }
    }

    async fn ensure_collaboration_eligible(
        &self,
        principal: &Principal,
        bot_uuid: &str,
        field_name: &str,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                format!("{field_name} must identify a Bot Actor"),
            ));
        }
        if bot.status == ActorStatus::Hidden {
            return Err(ApplicationError::forbidden(format!(
                "Bot '{bot_uuid}' is hidden and cannot collaborate"
            )));
        }
        let principal_actor_id = principal.actor_id();
        if principal_actor_id == bot_uuid || bot.capabilities.visibility == "public" {
            return Ok(());
        }

        if let Principal::Human(human) = principal {
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(());
            }
            let creator_edge = self
                .relation
                .get_edge(&principal_actor_id, bot_uuid, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(());
            }
        }

        if self
            .friends
            .try_are_friends(&principal_actor_id, bot_uuid)
            .await
            .map_err(map_service_error)?
        {
            return Ok(());
        }

        Err(ApplicationError::forbidden(format!(
            "Bot '{bot_uuid}' is not collaboration-eligible for this Principal"
        )))
    }

    async fn can_read_group_detail(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == principal_actor_id)
        {
            return Ok(true);
        }
        match principal {
            Principal::Bot(_) => Ok(Self::group_management_actor_ids(group)
                .iter()
                .any(|actor_id| actor_id == &principal_actor_id)),
            Principal::Human(human) => {
                let owned_bot_ids = self
                    .registry
                    .try_list_bots_by_creator(&human.subject.id)
                    .await
                    .map_err(map_service_error)?
                    .into_iter()
                    .filter(|bot| bot.actor_kind == ActorKind::Bot)
                    .map(|bot| bot.bot_uuid)
                    .collect::<HashSet<_>>();
                Ok(group.participants.iter().any(|participant| {
                    participant.actor_kind == ActorKind::Bot
                        && owned_bot_ids.contains(&participant.bot_uuid)
                }))
            }
        }
    }

    /// The effective Human principal may act as itself or as an owned Bot
    /// originator. The effective Bot principal may act only as itself. Anything
    /// else is forbidden. This is stricter than legacy `authorize_originator`
    /// (which lets any human designate any originator); it is a V1 gate and
    /// legacy `POST /groups` is unaffected.
    async fn authorize_originator(
        &self,
        principal: &Principal,
        originator: &str,
    ) -> Result<(), ApplicationError> {
        if principal.actor_id() == originator {
            return Ok(());
        }
        let bot = self.load_bot(originator).await.map_err(|error| match error {
            ApplicationError::NotFound { .. } => ApplicationError::forbidden(format!(
                "Authenticated Principal cannot act as originator '{originator}'"
            )),
            other => other,
        })?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_originator",
                "originator must be a Bot Actor",
            ));
        }
        if let Principal::Human(human) = principal {
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(());
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated Principal cannot act as originator '{originator}'"
        )))
    }

    /// Structural validity only: the driver must resolve to a registered Bot
    /// Actor. Does NOT check any caller↔driver relationship (that was dropped
    /// by design) — it only rejects a non-existent or non-Bot driver id.
    async fn ensure_driver_is_registered_bot(
        &self,
        driver_bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(driver_bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "driver_bot_uuid must identify a Bot Actor",
            ));
        }
        Ok(())
    }

    /// When the originator is a caller-owned Bot distinct from the caller,
    /// the driver must be reachable from that originator bot (public or a
    /// friend of it). Skipped when the originator is the human caller itself
    /// — the driver is ungated against the caller by design.
    async fn ensure_originator_can_reach_driver(
        &self,
        originator_bot_id: &str,
        driver_bot_id: &str,
    ) -> Result<(), ApplicationError> {
        let driver = self.load_bot(driver_bot_id).await?;
        if driver.status == ActorStatus::Hidden {
            return Err(ApplicationError::forbidden(format!(
                "Bot '{driver_bot_id}' is hidden and cannot be invited into a group"
            )));
        }
        if driver.capabilities.visibility == "public" {
            return Ok(());
        }
        if self
            .friends
            .try_are_friends(originator_bot_id, driver_bot_id)
            .await
            .map_err(map_service_error)?
        {
            return Ok(());
        }
        Err(ApplicationError::forbidden(format!(
            "Driver '{driver_bot_id}' is not reachable from originator '{originator_bot_id}'"
        )))
    }

    async fn can_read_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == principal_actor_id)
        {
            return Ok(true);
        }
        let management_actor_ids = Self::group_management_actor_ids(group);
        if management_actor_ids
            .iter()
            .any(|actor_id| actor_id == &principal_actor_id)
        {
            return Ok(true);
        }
        if let Principal::Human(human) = principal {
            let mut actor_ids = group
                .participants
                .iter()
                .map(|participant| participant.bot_uuid.clone())
                .collect::<Vec<_>>();
            actor_ids.extend(management_actor_ids);
            if self.human_can_act_as_any(human, actor_ids).await? {
                return Ok(true);
            }
        }
        let session_group_ids = self
            .sessions
            .list_group_ids_by_session_participant(&principal_actor_id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        Ok(session_group_ids.iter().any(|id| id == &group.id))
    }

    fn group_management_actor_ids(group: &DomainGroup) -> Vec<String> {
        let mut actor_ids = vec![group.driver_bot.clone(), group.originator().to_string()];
        if group.group_strategy == GroupStrategy::ManagerWorker {
            actor_ids.extend(
                group
                    .participants
                    .iter()
                    .filter(|participant| participant.role == bcs_service_api::ParticipantRole::Manager)
                    .map(|participant| participant.bot_uuid.clone()),
            );
        }
        actor_ids
    }

    async fn human_actable_actor_id(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<Option<String>, ApplicationError> {
        let human_actor_id = format!("human_{}", human.subject.id);
        let mut seen = HashSet::new();
        for actor_id in actor_ids {
            if !seen.insert(actor_id.clone()) {
                continue;
            }
            if actor_id == human_actor_id {
                return Ok(Some(actor_id));
            }
            if actor_id.starts_with("human_") {
                continue;
            }
            let Some(bot) = self
                .registry
                .try_get(&actor_id)
                .await
                .map_err(map_service_error)?
            else {
                continue;
            };
            if bot.actor_kind != ActorKind::Bot {
                continue;
            }
            if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                return Ok(Some(actor_id));
            }
            let creator_edge = self
                .relation
                .get_edge(&human_actor_id, &actor_id, &self.config.relation_env)
                .await
                .map_err(map_service_error)?;
            if creator_edge.is_some_and(|edge| edge.is_creator) {
                return Ok(Some(actor_id));
            }
        }
        Ok(None)
    }

    async fn human_can_act_as_any(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<bool, ApplicationError> {
        Ok(self.human_actable_actor_id(human, actor_ids).await?.is_some())
    }

    async fn principal_can_act_as(
        &self,
        principal: &Principal,
        actor_id: &str,
    ) -> Result<bool, ApplicationError> {
        if principal.actor_id() == actor_id {
            return Ok(true);
        }
        match principal {
            Principal::Human(human) => {
                self.human_can_act_as_any(human, vec![actor_id.to_string()]).await
            }
            Principal::Bot(_) => Ok(false),
        }
    }

    async fn resolve_group_manage_actor(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<Option<String>, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        let candidates = Self::group_management_actor_ids(group);
        if candidates.iter().any(|actor_id| actor_id == &principal_actor_id) {
            return Ok(Some(principal_actor_id));
        }
        if let Principal::Human(human) = principal {
            return self.human_actable_actor_id(human, candidates).await;
        }
        Ok(None)
    }

    async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        Ok(self
            .resolve_group_manage_actor(principal, group)
            .await?
            .is_some())
    }

    async fn load_readable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if !self.can_read_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    async fn load_group_detail_for_caller(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let principal = select_principal(caller, IdentityPolicy::HumanOrOwnedBot)?;
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if group.visibility != visibility_name(GroupVisibility::Public)
            && !self.can_read_group_detail(&principal, &group).await?
        {
            return Err(ApplicationError::forbidden(
                "The selected Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if !self.can_manage_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Only the Group originator or driver may manage this Group",
            ));
        }
        Ok(group)
    }

    async fn project_detail_with_state_machine(
        &self,
        mut group: DomainGroup,
        state_machine_override: Option<StateMachineConfiguration>,
    ) -> Result<GroupDetail, ApplicationError> {
        bcs_service_api::backfill_bot_names(self.registry.as_ref(), &mut group).await;
        let participants = group
            .participants
            .iter()
            .map(project_participant)
            .collect::<Vec<_>>();
        let common = DetailCommon {
            group_id: group.id.clone(),
            version: group.version,
            name: group.label.clone(),
            status: project_status(group.status),
            visibility: project_visibility(&group.visibility)?,
            context: group.context.clone(),
            originator_actor_id: group.originator().to_string(),
            participants,
            created_at: group.created_at,
            updated_at: group.updated_at,
        };

        if group.group_kind == GroupKind::Dm {
            if common.participants.len() != 2 {
                return Err(ApplicationError::internal(format!(
                    "DM Group '{}' does not contain exactly two participants",
                    group.id
                )));
            }
            return Ok(GroupDetail::DirectMessage(DirectMessageGroupDetail {
                group_id: common.group_id,
                version: common.version,
                name: common.name,
                status: common.status,
                visibility: common.visibility,
                context: common.context,
                originator_actor_id: common.originator_actor_id,
                participants: common.participants,
                created_at: common.created_at,
                updated_at: common.updated_at,
            }));
        }

        let collaboration = match group.group_strategy {
            GroupStrategy::Chat => CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: bcs_service_api::application::v1::GroupDeliveryPolicy {
                    bot_final_delivery: project_delivery(
                        group
                            .routing_policy
                            .as_ref()
                            .map(|policy| policy.default_bot_final_delivery)
                            .unwrap_or_default(),
                    ),
                },
            }),
            GroupStrategy::ManagerWorker => {
                CollaborationConfiguration::ManagerWorker(ManagerWorkerConfiguration::default())
            }
            GroupStrategy::StateMachine => {
                let configuration = match state_machine_override {
                    Some(configuration) => configuration,
                    None => self.load_state_machine_configuration(&group.id).await?,
                };
                CollaborationConfiguration::StateMachine(configuration)
            }
        };

        Ok(GroupDetail::Collaboration(CollaborationGroupDetail {
            group_id: common.group_id,
            version: common.version,
            name: common.name,
            status: common.status,
            visibility: common.visibility,
            context: common.context,
            opening_message: group.opening_message,
            originator_actor_id: common.originator_actor_id,
            participants: common.participants,
            driver_bot_uuid: group.driver_bot,
            collaboration,
            created_at: common.created_at,
            updated_at: common.updated_at,
        }))
    }

    async fn project_detail(&self, group: DomainGroup) -> Result<GroupDetail, ApplicationError> {
        self.project_detail_with_state_machine(group, None).await
    }

    async fn load_state_machine_configuration(
        &self,
        group_id: &str,
    ) -> Result<StateMachineConfiguration, ApplicationError> {
        let runtime = self.collaboration_runtime.as_ref().ok_or_else(|| {
            ApplicationError::internal(
                "StateMachine Group projection requires CollaborationRuntimeService",
            )
        })?;
        let view = runtime
            .get_group_collaboration_definition(group_id)
            .await
            .map_err(map_runtime_error)?;
        let definition = view.default_definition.ok_or_else(|| {
            ApplicationError::conflict(
                "state_machine_definition_missing",
                "StateMachine Group has no default definition",
            )
        })?;
        let participant_bindings = view
            .participant_bindings
            .into_iter()
            .map(|(binding, value)| StateMachineParticipantBinding {
                binding,
                actor_ids: value.bot_ids,
            })
            .collect();
        Ok(StateMachineConfiguration {
            definition: StateMachineDefinition::Reference(StateMachineDefinitionReference {
                definition_id: definition.id,
                version: definition.version,
            }),
            participant_bindings,
        })
    }

    async fn project_summary(
        &self,
        mut group: DomainGroup,
        target_bot_uuid: &str,
        membership: Membership,
    ) -> Result<GroupSummary, ApplicationError> {
        bcs_service_api::backfill_bot_names(self.registry.as_ref(), &mut group).await;
        let status = project_status(group.status);
        let visibility = project_visibility(&group.visibility)?;
        let originator_actor_id = group.originator().to_string();
        if group.group_kind == GroupKind::Dm {
            let peer_actor = group
                .participants
                .iter()
                .any(|participant| participant.bot_uuid == target_bot_uuid)
                .then(|| {
                    group
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid != target_bot_uuid)
                        .map(|participant| Actor {
                            actor_id: participant.bot_uuid.clone(),
                            actor_kind: participant.actor_kind,
                            name: participant.bot_name.clone(),
                        })
                })
                .flatten();
            return Ok(GroupSummary::DirectMessage(DirectMessageGroupSummary {
                group_id: group.id,
                version: group.version,
                name: group.label,
                context: group.context,
                status,
                visibility,
                membership,
                originator_actor_id,
                participant_count: group.participants.len(),
                peer_actor,
                created_at: group.created_at,
                updated_at: group.updated_at,
            }));
        }

        Ok(GroupSummary::Normal(NormalGroupSummary {
            group_id: group.id,
            version: group.version,
            name: group.label,
            context: group.context,
            status,
            visibility,
            membership,
            originator_actor_id,
            participant_count: group.participants.len(),
            driver_bot_uuid: group.driver_bot,
            strategy: project_strategy(group.group_strategy),
            created_at: group.created_at,
            updated_at: group.updated_at,
        }))
    }

    async fn create_collaboration(
        &self,
        principal: Principal,
        mut request: CreateCollaborationGroup,
        group_id: Option<String>,
        provisioning: bool,
    ) -> Result<(GroupDetail, Option<String>, Option<bcs_service_api::InitialGroupRun>), ApplicationError> {
        let originator = request
            .originator
            .take()
            .unwrap_or_else(|| principal.actor_id());
        self.authorize_originator(&principal, &originator)
            .await?;
        // Structural validity only: the driver must be a registered Bot Actor.
        // This is NOT a caller↔driver relationship check (that was deliberately
        // dropped); callers may drive bots they do not own, as long as the bot
        // exists and is a Bot.
        self.ensure_driver_is_registered_bot(&request.driver_bot_uuid)
            .await?;
        // The driver is ungated against the caller (caller↔driver is not
        // checked). Only when the originator is a caller-owned Bot distinct
        // from both the caller and the driver must the driver be reachable
        // from that originator (an originator that is itself the driver is
        // self-reachable; a protected bot cannot friend itself).
        if originator != principal.actor_id()
            && originator != request.driver_bot_uuid
        {
            self.ensure_originator_can_reach_driver(&originator, &request.driver_bot_uuid)
                .await?;
        }
        if request
            .participants
            .iter()
            .any(|participant| participant.actor_id.is_empty())
        {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "participant actor_id cannot be empty",
            ));
        }
        let mut participant_actor_ids = HashSet::new();
        if request
            .participants
            .iter()
            .any(|participant| !participant_actor_ids.insert(participant.actor_id.as_str()))
        {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "participant actor_id values must be unique",
            ));
        }

        let principal_actor_id = principal.actor_id();
        if let Principal::Human(human) = &principal {
            self.registry
                .ensure_human_actor(&human.subject.id, &human_display_name(human))
                .await
                .map_err(map_service_error)?;
        }
        let authenticated_human = match &principal {
            Principal::Human(human) => Some(AuthenticatedHumanCaller {
                actor_id: principal_actor_id.clone(),
                display_name: Some(human_display_name(human)),
            }),
            Principal::Bot(_) => None,
        };
        let (strategy, routing_policy, state_machine) =
            map_create_collaboration(request.collaboration.clone());
        if let Some(opening_message) = &request.opening_message {
            opening_message.validate_for(opening_message_scope(strategy)).map_err(|error| {
                ApplicationError::invalid("invalid_opening_message", error.to_string())
            })?;
        }
        let lead_role = strategy.lead_role();
        if request
            .participants
            .iter()
            .any(|participant| !strategy.allows_role(participant.role))
        {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "Participant role is not allowed by the selected collaboration strategy",
            ));
        }
        match request
            .participants
            .iter()
            .find(|participant| participant.actor_id == request.driver_bot_uuid)
        {
            Some(driver) if driver.role != lead_role => {
                return Err(ApplicationError::invalid(
                    "invalid_participant",
                    "driver_bot_uuid must have the strategy lead role",
                ));
            }
            None => request.participants.push(CreateParticipant {
                actor_id: request.driver_bot_uuid.clone(),
                role: lead_role,
                tags: Vec::new(),
            }),
            _ => {}
        }
        if request.participants.iter().any(|participant| {
            participant.actor_id != request.driver_bot_uuid && participant.role == lead_role
        }) {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "Only driver_bot_uuid may have the strategy lead role",
            ));
        }
        if state_machine.is_some() && self.collaboration_runtime.is_none() {
            return Err(ApplicationError::internal(
                "StateMachine creation requires CollaborationRuntimeService",
            ));
        }
        if let Some(state_machine) = &state_machine {
            let mut binding_names = HashSet::new();
            if state_machine
                .participant_bindings
                .iter()
                .any(|binding| !binding_names.insert(binding.binding.as_str()))
            {
                return Err(ApplicationError::invalid(
                    "invalid_participant_binding",
                    "StateMachine participant binding names must be unique",
                ));
            }
            let canonical_actor_ids = request
                .participants
                .iter()
                .map(|participant| participant.actor_id.as_str())
                .chain(std::iter::once(request.driver_bot_uuid.as_str()))
                .collect::<HashSet<_>>();
            if state_machine
                .participant_bindings
                .iter()
                .flat_map(|binding| binding.actor_ids.iter())
                .any(|actor_id| !canonical_actor_ids.contains(actor_id.as_str()))
            {
                return Err(ApplicationError::invalid(
                    "invalid_participant_binding",
                    "StateMachine participant bindings must reference Group participants",
                ));
            }
            let bound_actor_ids = state_machine
                .participant_bindings
                .iter()
                .flat_map(|binding| binding.actor_ids.iter())
                .collect::<HashSet<_>>();
            for actor_id in bound_actor_ids {
                let actor = self.load_bot(actor_id).await?;
                if actor.actor_kind != ActorKind::Bot {
                    return Err(ApplicationError::invalid(
                        "invalid_participant_binding",
                        "StateMachine participant bindings may reference only Bot actors",
                    ));
                }
            }
        }

        let participants = request
            .participants
            .into_iter()
            .map(|participant| GroupCreateParticipantCommand {
                bot_id: participant.actor_id,
                role: Some(role_name(participant.role).to_string()),
                tags: normalize_participant_tags(participant.tags),
            })
            .collect::<Vec<_>>();
        let created = self
            .management
            .create_group(GroupCreateCommand {
                group_id,
                caller_actor_id: Some(principal_actor_id.clone()),
                driver_bot_id: request.driver_bot_uuid,
                label: request.name,
                topic: None,
                context: request.context,
                opening_message: request.opening_message,
                routing_policy,
                participants,
                member_bot_ids: Vec::new(),
                group_kind: Some(GroupKind::Normal),
                service_spec: None,
                group_strategy: Some(strategy),
                originator: Some(originator),
                visibility: Some(visibility_name(request.visibility).to_string()),
                provisioning,
            })
            .await
            .map_err(map_group_error)?;
        let initial_session_id = created.latest_running_session_id.clone();
        let initial_run = created.initial_run.clone();

        if let Some(state_machine) = state_machine {
            let mut response_state_machine = state_machine.clone();
            let runtime = self.collaboration_runtime.as_ref().ok_or_else(|| {
                ApplicationError::internal(
                    "StateMachine creation requires CollaborationRuntimeService",
                )
            })?;
            let participant_bindings = state_machine
                .participant_bindings
                .into_iter()
                .map(|binding| {
                    (
                        binding.binding,
                        RuntimeParticipantBinding {
                            source: "manual".to_string(),
                            bot_ids: binding.actor_ids,
                            extensions: Default::default(),
                        },
                    )
                })
                .collect::<BTreeMap<_, _>>();
            let (definition_yaml, definition_ref) = match state_machine.definition {
                StateMachineDefinition::Reference(reference) => (
                    None,
                    Some(CollaborationDefinitionRef {
                        id: reference.definition_id,
                        version: reference.version,
                    }),
                ),
                StateMachineDefinition::Content(content) => (Some(content.content_yaml), None),
            };
            let configured = match runtime
                .configure_group_runtime(ConfigureGroupRuntimeCommand {
                    group_id: created.group_id.clone(),
                    definition_yaml,
                    definition: None,
                    definition_ref,
                    participant_bindings,
                    auto_start_on_service_invocation: true,
                })
                .await
            {
                Ok(configured) => configured,
                Err(error) => {
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(
                            runtime.as_ref(),
                            &created.group_id,
                            created.latest_running_session_id.as_deref(),
                        )
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
            };
            if let Some(definition) = configured.default_definition.clone() {
                response_state_machine.definition = StateMachineDefinition::Reference(
                    StateMachineDefinitionReference {
                        definition_id: definition.id,
                        version: definition.version,
                    },
                );
            }
            if configured.requires_human_input_channel {
                let group = self
                    .groups
                    .try_get(&created.group_id)
                    .await
                    .map_err(map_service_error)?
                    .ok_or_else(|| {
                        ApplicationError::internal(
                            "created Group disappeared before deferred-run projection",
                        )
                    })?;
                let detail = self
                    .project_detail_with_state_machine(group, Some(response_state_machine))
                    .await?;
                return Ok((detail, initial_session_id, initial_run));
            }

            let session_id = match created.latest_running_session_id.clone() {
                Some(session_id) => session_id,
                None => {
                    let error = CollaborationRuntimeError::Internal(
                        ServiceError::InternalError(
                            "StateMachine Group creation did not produce an initial ServiceInvocation session"
                                .to_string(),
                        ),
                    );
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(runtime.as_ref(), &created.group_id, None)
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
            };
            let session = match self.sessions.get(&session_id).await {
                Ok(Some(session)) => session,
                Ok(None) => {
                    let error = CollaborationRuntimeError::Internal(ServiceError::InternalError(
                        "StateMachine initial ServiceInvocation session disappeared before start"
                            .to_string(),
                    ));
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(
                            runtime.as_ref(),
                            &created.group_id,
                            Some(&session_id),
                        )
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
                Err(error) => {
                    let error = CollaborationRuntimeError::Internal(ServiceError::InternalError(
                        error.to_string(),
                    ));
                    let (session_cleanup_error, group_cleanup_error) = self
                        .rollback_state_machine_creation(
                            runtime.as_ref(),
                            &created.group_id,
                            Some(&session_id),
                        )
                        .await;
                    return Err(map_runtime_and_rollback_error(
                        error,
                        session_cleanup_error,
                        group_cleanup_error,
                    ));
                }
            };
            if let Err(error) = runtime
                .start_state_machine_run(StartStateMachineRunCommand {
                    group_id: created.group_id.clone(),
                    session_id: Some(session.id),
                    definition_yaml: None,
                    definition: None,
                    definition_ref: None,
                    participant_bindings: None,
                    input: session.input.unwrap_or(Value::Null),
                    caller_id: Some(principal_actor_id),
                    authenticated_human,
                })
                .await
            {
                let (session_cleanup_error, group_cleanup_error) = self
                    .rollback_state_machine_creation(
                        runtime.as_ref(),
                        &created.group_id,
                        Some(&session_id),
                    )
                    .await;
                return Err(map_runtime_and_rollback_error(
                    error,
                    session_cleanup_error,
                    group_cleanup_error,
                ));
            }

            let group = self
                .groups
                .try_get(&created.group_id)
                .await
                .map_err(map_service_error)?
                .ok_or_else(|| {
                    ApplicationError::internal("created Group disappeared before projection")
                })?;
            let detail = self
                .project_detail_with_state_machine(group, Some(response_state_machine))
                .await?;
            return Ok((detail, initial_session_id, initial_run));
        }

        let group = self
            .groups
            .try_get(&created.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::internal("created Group disappeared before projection")
            })?;
        Ok((self.project_detail(group).await?, initial_session_id, initial_run))
    }

    async fn rollback_state_machine_creation(
        &self,
        runtime: &dyn CollaborationRuntimeService,
        group_id: &str,
        session_id: Option<&str>,
    ) -> (Option<String>, Option<String>) {
        let mut runtime_cleanup_errors = Vec::new();
        if let Err(error) = runtime
            .cancel_group_runs(group_id, "state_machine_creation_failed")
            .await
        {
            runtime_cleanup_errors.push(format!("run cancellation: {error}"));
        }
        if let Err(error) = runtime.delete_group_runtime_state(group_id).await {
            runtime_cleanup_errors.push(format!("runtime state: {error}"));
        }
        if let Some(session_id) = session_id
            && let Err(error) = self.sessions.delete(session_id).await
        {
            runtime_cleanup_errors.push(format!("initial session: {error}"));
        }
        let runtime_cleanup_error =
            (!runtime_cleanup_errors.is_empty()).then(|| runtime_cleanup_errors.join("; "));
        let group_cleanup_error = self
            .groups
            .delete(group_id)
            .await
            .err()
            .map(|cleanup| cleanup.to_string());
        (runtime_cleanup_error, group_cleanup_error)
    }

    async fn create_dm(
        &self,
        principal: Principal,
        request: CreateDirectMessageGroup,
        group_id: Option<String>,
        provisioning: bool,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        self.ensure_collaboration_eligible(
            &principal,
            &request.target_actor_id,
            "target_actor_id",
        )
        .await?;
        if let Principal::Human(human) = &principal {
            self.registry
                .ensure_human_actor(&human.subject.id, &human_display_name(human))
                .await
                .map_err(map_service_error)?;
        }
        let result = self
            .management
            .create_dm(DmCreateCommand {
                group_id,
                caller_actor_id: Some(principal.actor_id()),
                driver_bot: None,
                target_actor_id: request.target_actor_id,
                label: request.name,
                topic: None,
                context: request.context,
                provisioning,
            })
            .await
            .map_err(map_group_error)?;
        let group = self
            .groups
            .try_get(&result.group.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::internal("created DM Group disappeared before projection")
            })?;
        Ok(CreateGroupOutcome {
            group: self.project_detail(group).await?,
            created: result.created,
            initial_session_id: None,
            initial_run: None,
            event_subscriptions: Vec::new(),
        })
    }

    async fn rollback_provisioning_creation(
        &self,
        group: &DomainGroup,
        initial_session_id: Option<&str>,
    ) -> Option<String> {
        let mut errors = Vec::new();
        if group.group_strategy == GroupStrategy::StateMachine
            && let Some(runtime) = self.collaboration_runtime.as_ref()
        {
            if let Err(error) = runtime
                .cancel_group_runs(&group.id, "group_provisioning_failed")
                .await
            {
                errors.push(format!("run cancellation: {error}"));
            }
            if let Err(error) = runtime.delete_group_runtime_state(&group.id).await {
                errors.push(format!("runtime state: {error}"));
            }
        }
        if let Some(session_id) = initial_session_id
            && let Err(error) = self.sessions.delete(session_id).await
        {
            errors.push(format!("initial session: {error}"));
        }
        if let Err(error) = self.groups.delete(&group.id).await {
            errors.push(format!("group: {error}"));
        }
        (!errors.is_empty()).then(|| errors.join("; "))
    }

    async fn rollback_provisioning_creation_by_id(
        &self,
        group_id: &str,
        state_machine: bool,
    ) -> Option<String> {
        let mut errors = Vec::new();
        if state_machine && let Some(runtime) = self.collaboration_runtime.as_ref() {
            if let Err(error) = runtime
                .cancel_group_runs(group_id, "group_provisioning_failed")
                .await
            {
                errors.push(format!("run cancellation: {error}"));
            }
            if let Err(error) = runtime.delete_group_runtime_state(group_id).await {
                errors.push(format!("runtime state: {error}"));
            }
        }
        match self
            .sessions
            .list_by_group(group_id, None, 0, 100, None, None)
            .await
        {
            Ok(sessions) => {
                for session in sessions {
                    if let Err(error) = self.sessions.delete(&session.id).await {
                        errors.push(format!("initial session '{}': {error}", session.id));
                    }
                }
            }
            Err(error) => errors.push(format!("initial session lookup: {error}")),
        }
        if let Err(error) = self.groups.delete(group_id).await {
            errors.push(format!("group: {error}"));
        }
        (!errors.is_empty()).then(|| errors.join("; "))
    }

    async fn provisioning_error_with_compensation(
        &self,
        provisioner: &dyn GroupEventSubscriptionProvisioner,
        prepared: &PreparedGroupEventSubscriptions,
        original: ApplicationError,
        rollback_error: Option<String>,
    ) -> ApplicationError {
        let cancel_error = provisioner
            .cancel(prepared, "group_provisioning_failed")
            .await
            .err()
            .map(|error| error.to_string());
        if rollback_error.is_none() && cancel_error.is_none() {
            return original;
        }
        ApplicationError::internal(format!(
            "{original}; provisioning compensation failed: {}{}",
            rollback_error.unwrap_or_else(|| "none".to_string()),
            cancel_error.map_or_else(String::new, |error| format!("; subscription: {error}"))
        ))
    }

    async fn create_without_eventing(
        &self,
        command: CreateGroup,
        principal: Principal,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        match command.group {
            CreateGroupSpec::Collaboration(request) => {
                let (group, initial_session_id, initial_run) = self
                    .create_collaboration(principal, request, None, false)
                    .await?;
                Ok(CreateGroupOutcome {
                    group,
                    created: true,
                    initial_session_id,
                    initial_run,
                    event_subscriptions: Vec::new(),
                })
            }
            CreateGroupSpec::DirectMessage(request) => {
                self.create_dm(principal, request, None, false).await
            }
        }
    }
}

#[async_trait]
impl GroupService for GroupServiceImpl {
    async fn list_groups(
        &self,
        command: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        let view_actor_id = self
            .resolve_view_actor(&command.caller, command.view_bot_id.as_deref())
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        if command.kind == GroupKindFilter::Dm && command.strategy.is_some() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "kind=dm cannot be combined with strategy",
            ));
        }

        let direct = self
            .groups
            .try_find_by_participant(&view_actor_id)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .filter(|group| group.record_status == "active")
            .map(|group| (group.id.clone(), (group, Membership::Direct)))
            .collect::<HashMap<_, _>>();
        let direct_group_ids = direct.keys().cloned().collect::<HashSet<_>>();
        let mut related = match command.membership {
            MembershipFilter::All | MembershipFilter::Direct => direct,
            MembershipFilter::SessionOnly => HashMap::new(),
        };
        if command.membership != MembershipFilter::Direct {
            let session_group_ids = self
                .sessions
                .list_group_ids_by_session_participant(&view_actor_id)
                .await
                .map_err(|error| ApplicationError::internal(error.to_string()))?;
            for group_id in session_group_ids {
                if direct_group_ids.contains(&group_id) {
                    continue;
                }
                if let Some(group) = self
                    .groups
                    .try_get(&group_id)
                    .await
                    .map_err(map_service_error)?
                    .filter(|group| group.record_status == "active")
                {
                    related.insert(group_id, (group, Membership::SessionOnly));
                }
            }
        }

        let q = command
            .q
            .as_deref()
            .map(str::trim)
            .filter(|query| !query.is_empty())
            .map(str::to_lowercase);
        let mut groups = related
            .into_values()
            .filter(|(group, _)| {
                command.visibility.is_none_or(|visibility| {
                    group.visibility == visibility_name(visibility)
                })
            })
            .filter(|(group, _)| match command.kind {
                GroupKindFilter::Normal => group.group_kind == GroupKind::Normal,
                GroupKindFilter::Dm => group.group_kind == GroupKind::Dm,
                GroupKindFilter::All => true,
            })
            .filter(|(group, _)| {
                command.strategy.is_none_or(|strategy| {
                    group.group_kind == GroupKind::Normal
                        && project_strategy(group.group_strategy) == strategy
                })
            })
            .filter(|(group, _)| {
                q.as_ref().is_none_or(|query| {
                    group
                        .label
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(query)
                })
            })
            .collect::<Vec<_>>();
        // V1 contract (`api-contracts/v1/openapi/groups.yaml`) declares
        // `created_at DESC, group_id ASC`. Legacy HTTP endpoints keep
        // `updated_at` sort, so we use a dedicated comparator here.
        groups.sort_by(|(left, _), (right, _)| {
            DomainGroup::cmp_by_created_at_desc_group_id_asc(left, right)
        });
        let total = groups.len() as u64;
        let page = groups
            .into_iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for (group, membership) in page {
            items.push(
                self.project_summary(group, &view_actor_id, membership)
                    .await?,
            );
        }
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn list_public_groups(
        &self,
        command: ListPublicGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let q = command
            .q
            .as_deref()
            .map(str::trim)
            .filter(|query| !query.is_empty());
        let n = self
            .groups
            .count_filtered(None, Some("public"), q)
            .await;
        const SAFETY_CAP: u64 = 1000;
        if n > SAFETY_CAP {
            tracing::warn!(
                count = n,
                cap = SAFETY_CAP,
                "public group plaza candidate set truncated to SAFETY_CAP"
            );
        }
        let candidates = self
            .groups
            .list_paginated_filtered(0, n.min(SAFETY_CAP), None, Some("public"), q)
            .await;
        let filtered = candidates
            .into_iter()
            .filter(|group| group.record_status == "active")
            .filter(|group| {
                command.strategy.is_none_or(|strategy| {
                    group.group_kind == GroupKind::Normal
                        && project_strategy(group.group_strategy) == strategy
                })
            })
            .collect::<Vec<_>>();
        let total = filtered.len() as u64;
        let page = filtered
            .into_iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .collect::<Vec<_>>();
        let mut items = Vec::with_capacity(page.len());
        for group in page {
            items.push(self.project_summary(group, "", Membership::None).await?);
        }
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Ok(self
            .create_with_event_subscriptions(command, Vec::new())
            .await?
            .group)
    }

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        self.create_with_event_subscriptions(command, Vec::new())
            .await
    }

    async fn create_with_event_subscriptions(
        &self,
        command: CreateGroup,
        event_subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        let principal = select_principal(&command.caller, IdentityPolicy::HumanOrOwnedBot)?;
        let Some(provisioner) = self.event_subscription_provisioner.as_ref().cloned() else {
            if event_subscriptions.is_empty() {
                return self.create_without_eventing(command, principal).await;
            }
            return Err(ApplicationError::internal(
                "Group Event Subscription provisioning is not configured",
            ));
        };
        let CreateGroup { caller, group } = command;
        let state_machine = matches!(
            &group,
            CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                collaboration: CollaborationConfiguration::StateMachine(_),
                ..
            })
        );
        let group_kind = match &group {
            CreateGroupSpec::Collaboration(_) => GroupKind::Normal,
            CreateGroupSpec::DirectMessage(_) => GroupKind::Dm,
        };
        let group_id = generated_group_id(group_kind);
        let prepared = provisioner
            .prepare(&caller, &group_id, event_subscriptions)
            .await?;
        let creation = match group {
            CreateGroupSpec::Collaboration(request) => self
                .create_collaboration(principal, request, Some(group_id.clone()), true)
                .await
                .map(|(group, initial_session_id, initial_run)| {
                    (
                        CreateGroupOutcome {
                            group,
                            created: true,
                            initial_session_id: initial_session_id.clone(),
                            initial_run,
                            event_subscriptions: Vec::new(),
                        },
                        initial_session_id,
                    )
                }),
            CreateGroupSpec::DirectMessage(request) => self
                .create_dm(principal, request, Some(group_id.clone()), true)
                .await
                .map(|outcome| (outcome, None)),
        };
        let (mut outcome, created_initial_session_id) = match creation {
            Ok(creation) => creation,
            Err(error) => {
                let rollback = self
                    .rollback_provisioning_creation_by_id(&group_id, state_machine)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        error,
                        rollback,
                    )
                    .await);
            }
        };

        if !outcome.created {
            provisioner.cancel(&prepared, "existing_dm_reused").await?;
            let existing_id = match &outcome.group {
                GroupDetail::Collaboration(group) => &group.group_id,
                GroupDetail::DirectMessage(group) => &group.group_id,
            };
            let existing = self
                .groups
                .try_get(existing_id)
                .await
                .map_err(map_service_error)?
                .ok_or_else(|| {
                    ApplicationError::internal("Reused DM Group disappeared before response")
                })?;
            if existing.record_status != "active" {
                return Err(ApplicationError::conflict(
                    "group_provisioning_in_progress",
                    "The canonical DM Group is still being provisioned",
                ));
            }
            return Ok(outcome);
        }

        let domain_group = match self.groups.try_get(&group_id).await {
            Ok(Some(group)) => group,
            Ok(None) => {
                let error = ApplicationError::internal(
                    "Provisioning Group disappeared before Event finalization",
                );
                let rollback = self
                    .rollback_provisioning_creation_by_id(&group_id, state_machine)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        error,
                        rollback,
                    )
                    .await);
            }
            Err(error) => {
                let rollback = self
                    .rollback_provisioning_creation_by_id(&group_id, state_machine)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        map_service_error(error),
                        rollback,
                    )
                    .await);
            }
        };
        let initial_session = if domain_group.group_kind == GroupKind::Normal {
            let Some(initial_session_id) = created_initial_session_id.as_deref() else {
                let error = ApplicationError::internal(format!(
                    "Provisioning Group '{group_id}' did not create an initial Session"
                ));
                let rollback = self
                    .rollback_provisioning_creation(&domain_group, None)
                    .await;
                return Err(self
                    .provisioning_error_with_compensation(
                        provisioner.as_ref(),
                        &prepared,
                        error,
                        rollback,
                    )
                    .await);
            };
            match self.sessions.get(initial_session_id).await {
                Ok(Some(session)) => Some(session),
                Ok(None) => {
                    let error = ApplicationError::internal(format!(
                        "Provisioning initial Session '{initial_session_id}' disappeared"
                    ));
                    let rollback = self
                        .rollback_provisioning_creation(&domain_group, Some(initial_session_id))
                        .await;
                    return Err(self
                        .provisioning_error_with_compensation(
                            provisioner.as_ref(),
                            &prepared,
                            error,
                            rollback,
                        )
                        .await);
                }
                Err(error) => {
                    let rollback = self
                        .rollback_provisioning_creation(&domain_group, Some(initial_session_id))
                        .await;
                    return Err(self
                        .provisioning_error_with_compensation(
                            provisioner.as_ref(),
                            &prepared,
                            ApplicationError::internal(error.to_string()),
                            rollback,
                        )
                        .await);
                }
            }
        } else {
            None
        };

        if let Err(error) = provisioner
            .finalize(&prepared, &domain_group, initial_session.as_ref())
            .await
        {
            let rollback = self
                .rollback_provisioning_creation(
                    &domain_group,
                    initial_session.as_ref().map(|session| session.id.as_str()),
                )
                .await;
            return Err(self
                .provisioning_error_with_compensation(
                    provisioner.as_ref(),
                    &prepared,
                    error,
                    rollback,
                )
                .await);
        }
        outcome.event_subscriptions = provisioner.load_activated(&prepared).await?;
        Ok(outcome)
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        let group = self
            .load_group_detail_for_caller(&query.caller, &query.group_id)
            .await?;
        self.project_detail(group).await
    }

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        let principal = select_principal(&command.caller, IdentityPolicy::HumanOrOwnedBot)?;
        let mutation_actor = event_actor_for_principal(&principal);
        if command.patch.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "at least one mutable field is required",
            ));
        }
        let mut group = self
            .load_manageable_group(&principal, &command.group_id)
            .await?;
        if group.group_kind == GroupKind::Dm && command.patch.opening_message.is_some() {
            return Err(ApplicationError::invalid(
                "invalid_opening_message",
                "opening_message is not supported for DM Groups",
            ));
        }
        if group.group_kind == GroupKind::Dm
            && (command.patch.delivery_policy.is_some()
                || command.patch.visibility == Some(GroupVisibility::Public))
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "DM Groups do not expose delivery policy or public visibility",
            ));
        }
        if command.patch.delivery_policy.is_some()
            && group.group_strategy != GroupStrategy::Chat
        {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "delivery_policy may be updated only for Chat Groups",
            ));
        }
        if let Some(opening_message) = &command.patch.opening_message {
            if let Some(opening_message) = opening_message {
                opening_message
                    .validate_for(opening_message_scope(group.group_strategy))
                    .map_err(|error| {
                    ApplicationError::invalid("invalid_opening_message", error.to_string())
                })?;
            }
        }
        let patch = command.patch;
        let mut persistence_patch = GroupMutableFieldsPatch::default();
        if let Some(name) = patch.name {
            group.label = Some(name.clone());
            persistence_patch.label = Some(name);
        }
        if let Some(context) = patch.context {
            group.context = Some(context.clone());
            persistence_patch.context = Some(context);
        }
        if let Some(opening_message) = patch.opening_message {
            group.opening_message = opening_message.clone();
            persistence_patch.opening_message = Some(opening_message);
        }
        if let Some(visibility) = patch.visibility {
            if visibility == GroupVisibility::Public {
                for participant in &group.participants {
                    if participant.actor_kind != ActorKind::Bot {
                        continue;
                    }
                    let bot = self.load_bot(&participant.bot_uuid).await?;
                    if bot.capabilities.visibility != "public" {
                        return Err(ApplicationError::conflict(
                            "non_public_participant",
                            "All Bot participants must be public before Group visibility is public",
                        ));
                    }
                }
            }
            group.visibility = visibility_name(visibility).to_string();
            persistence_patch.visibility = Some(group.visibility.clone());
        }
        if let Some(delivery_policy) = patch.delivery_policy {
            let policy = group
                .routing_policy
                .get_or_insert_with(RoutingPolicy::default);
            policy.default_bot_final_delivery =
                persist_delivery(delivery_policy.bot_final_delivery);
            persistence_patch.default_bot_final_delivery = Some(policy.default_bot_final_delivery);
        }
        let state_machine_projection = if group.group_strategy == GroupStrategy::StateMachine {
            Some(
                self.load_state_machine_configuration(&command.group_id)
                    .await?,
            )
        } else {
            None
        };
        let persisted = self
            .groups
            .mutate(GroupMutationCommand {
                group_id: command.group_id.clone(),
                actor: mutation_actor,
                correlation_id: None,
                trace_id: None,
                mutation: GroupMutationKind::PatchMutableFields(persistence_patch),
            })
            .await
            .map_err(map_service_error)?;
        self.project_detail_with_state_machine(persisted, state_machine_projection)
            .await
    }

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        let principal = select_principal(&command.caller, IdentityPolicy::HumanOrOwnedBot)?;
        let Some(group) = self
            .groups
            .try_get(&command.group_id)
            .await
            .map_err(map_service_error)?
        else {
            let deletion_error = self
                .management
                .delete_group(GroupDeleteCommand {
                    caller_actor_id: principal.actor_id(),
                    group_id: command.group_id.clone(),
                })
                .await
                .err()
                .map(map_delete_group_error);
            let runtime_cleanup_error = if let Some(runtime) = self.collaboration_runtime.as_ref() {
                cleanup_deleted_group_runtime(runtime.as_ref(), &command.group_id).await
            } else {
                None
            };
            match (deletion_error, runtime_cleanup_error) {
                (Some(deletion_error), Some(runtime_cleanup_error)) => {
                    return Err(ApplicationError::internal(format!(
                        "Group cleanup failed: {deletion_error}; runtime cleanup failed: {runtime_cleanup_error}"
                    )));
                }
                (Some(deletion_error), None) => return Err(deletion_error),
                (None, Some(runtime_cleanup_error)) => {
                    return Err(ApplicationError::internal(format!(
                        "Group runtime cleanup failed: {runtime_cleanup_error}"
                    )));
                }
                (None, None) => {}
            }
            return Ok(DeleteResult {
                deleted: false,
            });
        };
        let manage_actor_id = if let Some(requested) = command.acting_bot_id.as_deref() {
            let acting_actor_id = match &principal {
                Principal::Human(_) => {
                    self.resolve_view_actor(&command.caller, Some(requested)).await?
                }
                Principal::Bot(bot) if requested == bot.bot_uuid => requested.to_string(),
                Principal::Bot(_) => {
                    return Err(ApplicationError::forbidden(
                        "The explicit acting Bot must identify the authenticated Bot",
                    ));
                }
            };
            let management_actor_ids = Self::group_management_actor_ids(&group);
            if !management_actor_ids
                .iter()
                .any(|actor_id| actor_id == &acting_actor_id)
            {
                return Err(ApplicationError::forbidden(
                    "Principal cannot delete the group",
                ));
            }
            acting_actor_id
        } else {
            let Some(manage_actor_id) = self.resolve_group_manage_actor(&principal, &group).await? else {
                return Err(ApplicationError::forbidden(
                    "Principal cannot delete the group",
                ));
            };
            manage_actor_id
        };
        let state_machine_runtime = if group.group_strategy == GroupStrategy::StateMachine {
            Some(self.collaboration_runtime.as_ref().ok_or_else(|| {
                ApplicationError::internal(
                    "StateMachine Group deletion requires CollaborationRuntimeService",
                )
            })?)
        } else {
            None
        };
        let group_id = command.group_id.clone();
        let result = match self
            .management
            .delete_group(GroupDeleteCommand {
                caller_actor_id: manage_actor_id,
                group_id: command.group_id,
            })
            .await
        {
            Ok(result) => result,
            Err(error) => {
                let deletion_error = map_delete_group_error(error);
                match self.groups.try_get(&group_id).await {
                    Ok(None) => {
                        if let Some(runtime) = state_machine_runtime
                            && let Some(runtime_cleanup_error) =
                                cleanup_deleted_group_runtime(runtime.as_ref(), &group_id).await
                        {
                            return Err(ApplicationError::internal(format!(
                                "Group deletion failed after commit: {deletion_error}; runtime cleanup failed: {runtime_cleanup_error}"
                            )));
                        }
                    }
                    Ok(Some(_)) => {}
                    Err(verification_error) => {
                        return Err(ApplicationError::internal(format!(
                            "Group deletion failed: {deletion_error}; failed to verify commit state: {verification_error}"
                        )));
                    }
                }
                return Err(deletion_error);
            }
        };
        if result.deleted
            && let Some(runtime) = state_machine_runtime
            && let Some(runtime_cleanup_error) =
                cleanup_deleted_group_runtime(runtime.as_ref(), &result.group_id).await
        {
            return Err(ApplicationError::internal(format!(
                "Group runtime cleanup failed: {runtime_cleanup_error}"
            )));
        }
        Ok(DeleteResult {
            deleted: result.deleted,
        })
    }

    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<V1Participant, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let group = self
            .load_readable_group(&principal, &command.group_id)
            .await?;
        let Some(manage_actor_id) = self.resolve_group_manage_actor(&principal, &group).await? else {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        };
        // `AddGroupParticipant` carries no `actor_kind`; resolve it from the
        // registry so legacy `add_member` gets the target Actor ID for both Bot
        // and Human participants. If the V1 caller references a Human actor that
        // has not been materialized yet, create the legacy Human actor at this
        // boundary before delegating.
        let target_actor = self
            .registry
            .try_get(&command.actor_id)
            .await
            .map_err(map_service_error)?;
        if target_actor.is_none() && let Some(staff_no) = command.actor_id.strip_prefix("human_") {
            self.registry
                .ensure_human_actor(staff_no, staff_no)
                .await
                .map_err(map_service_error)?;
        }
        let bot_id = command.actor_id.clone();
        let legacy_human_actor_id = match &principal {
            Principal::Human(human) => {
                let human_actor_id = format!("human_{}", human.subject.id);
                if manage_actor_id == human_actor_id {
                    None
                } else {
                    let manage_actor = self
                        .registry
                        .try_get(&manage_actor_id)
                        .await
                        .map_err(map_service_error)?;
                    manage_actor
                        .filter(|actor| {
                            actor.actor_kind == ActorKind::Bot
                                && actor.created_by.as_deref() == Some(human.subject.id.as_str())
                        })
                        .map(|_| human_actor_id)
                }
            }
            Principal::Bot(_) => None,
        };
        let result = self
            .management
            .add_member(GroupAddMemberCommand {
                caller_actor_id: Some(manage_actor_id),
                human_actor_id: legacy_human_actor_id,
                group_id: command.group_id.clone(),
                bot_id,
            })
            .await
            .map_err(map_group_error)?;
        Ok(participant_view_to_v1(result.member))
    }

    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<V1Participant, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let mutation_actor = event_actor_for_principal(&principal);
        let group = self
            .load_readable_group(&principal, &command.group_id)
            .await?;
        // Design §8.7: the target Actor may update its own participant mode
        // (self-service) in addition to the driver/originator/manager path.
        let is_self = self.principal_can_act_as(&principal, &command.actor_id).await?;
        if !is_self && self.resolve_group_manage_actor(&principal, &group).await?.is_none() {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        }
        let target = self.load_bot(&command.actor_id).await?;
        if !command.mode.is_valid_for(target.actor_kind) {
            return Err(ApplicationError::invalid(
                "invalid_participant_mode",
                format!(
                    "Participant mode '{:?}' is invalid for actor kind '{:?}'",
                    command.mode, target.actor_kind
                ),
            ));
        }
        let group = self
            .groups
            .mutate(GroupMutationCommand {
                group_id: command.group_id.clone(),
                actor: mutation_actor,
                correlation_id: None,
                trace_id: None,
                mutation: GroupMutationKind::UpdateParticipantMode {
                    actor_id: command.actor_id.clone(),
                    mode: command.mode,
                },
            })
            .await
            .map_err(map_service_error)?;
        group
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.actor_id)
            .map(project_participant)
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "participant_not_found",
                    format!("Participant '{}' not found", command.actor_id),
                )
            })
    }

    async fn delete_participant(
        &self,
        command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let group = self
            .load_readable_group(&principal, &command.group_id)
            .await?;
        // Design §8.7: the target Actor may leave the group (self-service
        // delete) in addition to the driver/originator/manager path. The legacy
        // `remove_member` still rejects driver/originator removal, preserving
        // the role invariant; non-driver self-leave proceeds.
        let is_self = self.principal_can_act_as(&principal, &command.actor_id).await?;
        let effective_caller_actor_id = if is_self {
            command.actor_id.clone()
        } else if let Some(manage_actor_id) = self.resolve_group_manage_actor(&principal, &group).await? {
            manage_actor_id
        } else {
            return Err(ApplicationError::forbidden(
                "Principal cannot manage the group",
            ));
        };
        // Phase one: target is a Bot actor (legacy `remove_member` uses bot_id).
        // The V1 contract treats an already-removed/missing participant as
        // idempotent success, so swallow `ParticipantNotFound` into
        // `DeleteResult { deleted: false }` (mirroring the group-level `delete`
        // facade's not-found handling) instead of surfacing a 404.
        match self
            .management
            .remove_member(GroupRemoveMemberCommand {
                caller_actor_id: Some(effective_caller_actor_id),
                group_id: command.group_id.clone(),
                bot_id: command.actor_id.clone(),
            })
            .await
        {
            Ok(_) => Ok(DeleteResult { deleted: true }),
            Err(GroupUseCaseError::Service(ServiceError::ParticipantNotFound(_))) => {
                Ok(DeleteResult { deleted: false })
            }
            Err(error) => Err(map_group_error(error)),
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_code(error: ApplicationError, expected: &str) {
        assert_eq!(error.code(), expected);
    }

    #[test]
    fn group_use_case_errors_map_to_stable_v1_codes() {
        assert_code(
            map_group_error(GroupUseCaseError::Unauthorized("missing principal".into())),
            "unauthenticated",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidGroupId("bad id".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidGroupStatus("closed".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidProposal("bad proposal".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::ProposalNotFound("proposal-1".into())),
            "not_found",
        );
        assert_code(
            map_group_error(GroupUseCaseError::ProposalExpired("proposal-2".into())),
            "not_found",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidHistoryLimit(0)),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidParticipantMode {
                mode: ParticipantMode::Auto,
                actor_kind: ActorKind::Human,
            }),
            "invalid_participant",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Conflict("version mismatch".into())),
            "conflict",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Service(ServiceError::BotNotFound(
                "bot-1".into(),
            ))),
            "bot_not_found",
        );
    }

    #[test]
    fn service_errors_map_to_stable_v1_codes() {
        assert_code(
            map_service_error(ServiceError::GroupNotFound("group-1".into())),
            "group_not_found",
        );
        assert_code(
            map_service_error(ServiceError::BotNotFound("bot-1".into())),
            "bot_not_found",
        );
        assert_code(
            map_service_error(ServiceError::BotNotRegistered("bot-2".into())),
            "bot_not_found",
        );
        assert_code(
            map_service_error(ServiceError::ParticipantNotFound("bot-3".into())),
            "participant_not_found",
        );
        assert_code(
            map_service_error(ServiceError::Unauthorized("missing principal".into())),
            "unauthenticated",
        );
        assert_code(
            map_service_error(ServiceError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::Conflict("version mismatch".into())),
            "conflict",
        );
        assert_code(
            map_service_error(ServiceError::InvalidOperation {
                message: "bad patch".into(),
                request_id: Some("request-1".into()),
            }),
            "invalid_request",
        );
        assert_code(
            map_service_error(ServiceError::SessionInvalidParams("bad session".into())),
            "invalid_request",
        );
        assert_code(
            map_service_error(ServiceError::ExistNonPublicBots { bots: Vec::new() }),
            "non_public_participant",
        );
        assert_code(
            map_service_error(ServiceError::BotHidden("bot-4".into())),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::PrivateBotCannotCollaborate),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::NotFriends(vec!["bot-5".into()])),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::InternalError("database unavailable".into())),
            "internal_error",
        );
    }

    #[test]
    fn collaboration_runtime_errors_map_to_stable_v1_codes() {
        assert_code(
            map_runtime_error(CollaborationRuntimeError::DefinitionNotFound(
                "definition-1".into(),
                1,
            )),
            "collaboration_definition_not_found",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidDefinition(
                "bad definition".into(),
            )),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidParticipantBinding(
                "bad binding".into(),
            )),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidRequest("bad request".into())),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Unauthenticated),
            "unauthenticated",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Conflict("run active".into())),
            "conflict",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::RunNotFound("run-1".into())),
            "internal_error",
        );
    }

    #[test]
    fn runtime_rollback_failure_preserves_primary_and_cleanup_errors() {
        let error = map_runtime_and_rollback_error(
            CollaborationRuntimeError::InvalidRequest("bad definition".to_string()),
            Some("session store unavailable".to_string()),
            Some("group store unavailable".to_string()),
        );

        assert!(matches!(
            error,
            ApplicationError::Internal(message)
                if message.contains("bad definition")
                    && message.contains("session store unavailable")
                    && message.contains("group store unavailable")
        ));
    }

    #[test]
    fn successful_runtime_rollback_keeps_client_error_classification() {
        let error = map_runtime_and_rollback_error(
            CollaborationRuntimeError::InvalidParticipantBinding("unknown actor".to_string()),
            None,
            None,
        );

        assert!(matches!(
            error,
            ApplicationError::InvalidInput { code, message }
                if code == "invalid_request" && message == "unknown actor"
        ));
    }
}
