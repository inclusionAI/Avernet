use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, Principal};

pub use bcs_domain::{ActorKind, ParticipantMode, ParticipantRole};

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
    pub definition: StateMachineDefinitionReference,
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
pub struct ListBotGroups {
    pub principal: Principal,
    pub bot_uuid: String,
    pub offset: u64,
    pub limit: u64,
    pub q: Option<String>,
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
    pub visibility: GroupVisibility,
    pub driver_bot_uuid: String,
    pub participants: Vec<CreateParticipant>,
    pub collaboration: CollaborationConfiguration,
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
    pub principal: Principal,
    pub group: CreateGroupSpec,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateGroupOutcome {
    pub group: GroupDetail,
    pub created: bool,
}

#[derive(Debug, Clone)]
pub struct GetGroup {
    pub principal: Principal,
    pub group_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct GroupPatch {
    pub name: Option<String>,
    pub context: Option<String>,
    pub visibility: Option<GroupVisibility>,
    pub delivery_policy: Option<GroupDeliveryPolicy>,
}

impl GroupPatch {
    pub fn is_empty(&self) -> bool {
        self.name.is_none()
            && self.context.is_none()
            && self.visibility.is_none()
            && self.delivery_policy.is_none()
    }
}

#[derive(Debug, Clone)]
pub struct UpdateGroup {
    pub principal: Principal,
    pub group_id: String,
    pub patch: GroupPatch,
}

#[derive(Debug, Clone)]
pub struct DeleteGroup {
    pub principal: Principal,
    pub group_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeleteResult {
    pub deleted: bool,
}

#[async_trait]
pub trait GroupService: Send + Sync {
    async fn list_bot_groups(
        &self,
        command: ListBotGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError>;

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError>;

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        Ok(CreateGroupOutcome {
            group: self.create(command).await?,
            created: true,
        })
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError>;

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError>;

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError>;
}
