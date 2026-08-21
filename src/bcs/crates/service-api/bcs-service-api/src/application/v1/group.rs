use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{
    ApplicationError, AuthenticatedCaller, CreateEventSubscriptionRequest, EventSinkInput,
    EventSubscription,
};
use crate::types::{
    EventActor, EventPayload, EventSubscriptionScope, EventSubscriptionScopeType, Group, Session,
};

pub use bcs_domain::{ActorKind, OpeningMessage, ParticipantMode, ParticipantRole};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupStatus {
    Active,
    Completed,
    Error,
    Closed,
    Inactive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupVisibility {
    Private,
    Public,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupStrategy {
    Chat,
    ManagerWorker,
    StateMachine,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Membership {
    Direct,
    SessionOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MembershipFilter {
    All,
    Direct,
    SessionOnly,
}

impl Default for MembershipFilter {
    fn default() -> Self {
        Self::All
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupKindFilter {
    Normal,
    Dm,
    All,
}

impl Default for GroupKindFilter {
    fn default() -> Self {
        Self::Normal
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotFinalDelivery {
    SendToDriver,
    InjectObservers,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GroupDeliveryPolicy {
    pub bot_final_delivery: BotFinalDelivery,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Actor {
    pub actor_id: String,
    pub actor_kind: ActorKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Participant {
    pub actor_id: String,
    pub actor_kind: ActorKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub role: ParticipantRole,
    pub mode: ParticipantMode,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum GroupSummary {
    Normal(NormalGroupSummary),
    #[serde(rename = "dm")]
    DirectMessage(DirectMessageGroupSummary),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NormalGroupSummary {
    pub group_id: String,
    pub version: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub status: GroupStatus,
    pub visibility: GroupVisibility,
    pub membership: Membership,
    pub originator_actor_id: String,
    pub participant_count: usize,
    pub driver_bot_uuid: String,
    pub strategy: GroupStrategy,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DirectMessageGroupSummary {
    pub group_id: String,
    pub version: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub status: GroupStatus,
    pub visibility: GroupVisibility,
    pub membership: Membership,
    pub originator_actor_id: String,
    pub participant_count: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub peer_actor: Option<Actor>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StateMachineDefinitionReference {
    pub definition_id: String,
    pub version: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StateMachineDefinitionContent {
    pub content_yaml: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum StateMachineDefinition {
    Reference(StateMachineDefinitionReference),
    Content(StateMachineDefinitionContent),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StateMachineParticipantBinding {
    pub binding: String,
    pub actor_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "strategy", rename_all = "snake_case")]
pub enum CollaborationConfiguration {
    Chat(ChatConfiguration),
    ManagerWorker(ManagerWorkerConfiguration),
    StateMachine(StateMachineConfiguration),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChatConfiguration {
    pub delivery_policy: GroupDeliveryPolicy,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ManagerWorkerConfiguration {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StateMachineConfiguration {
    pub definition: StateMachineDefinition,
    pub participant_bindings: Vec<StateMachineParticipantBinding>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum GroupDetail {
    #[serde(rename = "normal")]
    Collaboration(CollaborationGroupDetail),
    #[serde(rename = "dm")]
    DirectMessage(DirectMessageGroupDetail),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CollaborationGroupDetail {
    pub group_id: String,
    pub version: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub status: GroupStatus,
    pub visibility: GroupVisibility,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub opening_message: Option<OpeningMessage>,
    pub originator_actor_id: String,
    pub participants: Vec<Participant>,
    pub driver_bot_uuid: String,
    pub collaboration: CollaborationConfiguration,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DirectMessageGroupDetail {
    pub group_id: String,
    pub version: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub status: GroupStatus,
    pub visibility: GroupVisibility,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
    pub originator_actor_id: String,
    pub participants: Vec<Participant>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Page<T> {
    pub items: Vec<T>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

impl<T> Page<T> {
    pub fn empty(offset: u64, limit: u64) -> Self {
        Self {
            items: Vec::new(),
            total: 0,
            offset,
            limit,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ListGroups {
    pub caller: AuthenticatedCaller,
    pub view_bot_id: Option<String>,
    pub offset: u64,
    pub limit: u64,
    pub q: Option<String>,
    pub visibility: Option<GroupVisibility>,
    pub membership: MembershipFilter,
    pub kind: GroupKindFilter,
    pub strategy: Option<GroupStrategy>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateParticipant {
    pub actor_id: String,
    pub role: ParticipantRole,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateCollaborationGroup {
    pub name: Option<String>,
    pub context: Option<String>,
    pub opening_message: Option<OpeningMessage>,
    pub visibility: GroupVisibility,
    pub driver_bot_uuid: String,
    pub participants: Vec<CreateParticipant>,
    pub collaboration: CollaborationConfiguration,
    /// Caller-designated originator. `None` ⇒ resolve to the authenticated
    /// caller principal at the facade (current behavior).
    pub originator: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateDirectMessageGroup {
    pub name: Option<String>,
    pub context: Option<String>,
    pub target_actor_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CreateGroupSpec {
    Collaboration(CreateCollaborationGroup),
    DirectMessage(CreateDirectMessageGroup),
}

#[derive(Debug, Clone)]
pub struct CreateGroup {
    pub caller: AuthenticatedCaller,
    pub group: CreateGroupSpec,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateGroupOutcome {
    pub group: GroupDetail,
    pub created: bool,
    /// Subscriptions provisioned as part of this create operation. Empty for
    /// legacy requests and for a reused DM that did not request subscriptions.
    pub event_subscriptions: Vec<EventSubscription>,
}

/// Event Subscription input nested inside a Group create request.
///
/// Scope is deliberately absent: the application service fixes it to the
/// server-generated Group id, so callers cannot subscribe an arbitrary
/// resource through the Group creation endpoint.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InlineGroupEventSubscriptionRequest {
    pub name: String,
    pub event_filters: Vec<String>,
    #[serde(default)]
    pub payload: EventPayload,
    pub sink: EventSinkInput,
}

impl InlineGroupEventSubscriptionRequest {
    pub fn into_scoped(self, group_id: String) -> CreateEventSubscriptionRequest {
        CreateEventSubscriptionRequest {
            name: self.name,
            scope: EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: group_id,
            },
            event_filters: self.event_filters,
            payload: self.payload,
            sink: self.sink,
        }
    }
}

#[derive(Debug, Clone)]
pub struct PreparedGroupEventSubscriptions {
    pub group_id: String,
    pub subscription_ids: Vec<String>,
    pub actor: EventActor,
}

#[derive(Debug, Clone)]
pub struct PendingGroupEventSubscriptions {
    pub prepared: PreparedGroupEventSubscriptions,
    pub created_at_ms: u64,
}

#[async_trait]
pub trait GroupEventSubscriptionProvisioner: Send + Sync {
    /// Validate, protect, and persist pending subscriptions for a server-fixed
    /// Group scope. This happens before any Group or Session row is created.
    async fn prepare(
        &self,
        caller: &AuthenticatedCaller,
        group_id: &str,
        requests: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<PreparedGroupEventSubscriptions, ApplicationError>;

    /// Best-effort compensation used when Group provisioning fails. The
    /// implementation may only cancel subscriptions that are still pending.
    async fn cancel(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
        reason: &str,
    ) -> Result<(), ApplicationError>;

    /// Atomically make the Group available, activate all prepared
    /// subscriptions, and persist the ordered creation Events. The optional
    /// Session is the initial Session created by Group management.
    async fn finalize(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
        group: &Group,
        initial_session: Option<&Session>,
    ) -> Result<(), ApplicationError>;

    /// Rebuild the pending set after a process restart. The returned actor is
    /// System because recovery, rather than the original HTTP request, owns
    /// the finalization decision.
    async fn recover_pending(
        &self,
        group_id: &str,
    ) -> Result<PreparedGroupEventSubscriptions, ApplicationError>;

    /// List pending Group-scoped sets so recovery can cancel subscriptions
    /// whose process crashed after prepare but before the Group row existed.
    async fn list_pending_groups(
        &self,
    ) -> Result<Vec<PendingGroupEventSubscriptions>, ApplicationError>;

    /// Load redacted summaries after atomic finalization activated them.
    async fn load_activated(
        &self,
        prepared: &PreparedGroupEventSubscriptions,
    ) -> Result<Vec<EventSubscription>, ApplicationError>;
}

#[derive(Debug, Clone)]
pub struct GetGroup {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct GroupPatch {
    pub name: Option<String>,
    pub context: Option<String>,
    /// Outer `None` leaves the field unchanged; `Some(None)` restores the default.
    pub opening_message: Option<Option<OpeningMessage>>,
    pub visibility: Option<GroupVisibility>,
    pub delivery_policy: Option<GroupDeliveryPolicy>,
}

impl GroupPatch {
    pub fn is_empty(&self) -> bool {
        self.name.is_none()
            && self.context.is_none()
            && self.opening_message.is_none()
            && self.visibility.is_none()
            && self.delivery_policy.is_none()
    }
}

#[derive(Debug, Clone)]
pub struct UpdateGroup {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub patch: GroupPatch,
}

#[derive(Debug, Clone)]
pub struct DeleteGroup {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub acting_bot_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct AddGroupParticipant {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub actor_id: String,
}

#[derive(Debug, Clone)]
pub struct UpdateGroupParticipant {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub actor_id: String,
    pub mode: ParticipantMode,
}

#[derive(Debug, Clone)]
pub struct DeleteGroupParticipant {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub actor_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeleteResult {
    pub deleted: bool,
}

#[async_trait]
pub trait GroupService: Send + Sync {
    async fn list_groups(
        &self,
        command: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError>;

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError>;

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        Ok(CreateGroupOutcome {
            group: self.create(command).await?,
            created: true,
            event_subscriptions: Vec::new(),
        })
    }

    async fn create_with_event_subscriptions(
        &self,
        command: CreateGroup,
        event_subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        if !event_subscriptions.is_empty() {
            return Err(ApplicationError::internal(
                "Group Event Subscription provisioning is not configured",
            ));
        }
        self.create_with_outcome(command).await
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError>;

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError>;

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError>;

    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError>;

    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError>;

    async fn delete_participant(
        &self,
        command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError>;
}
