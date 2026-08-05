//! Group query and management use-case contracts.

use std::collections::HashMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{
    ActorKind, DefaultDelivery, GroupKind, GroupStatus, GroupStrategy, ParticipantKind,
    ParticipantMode,
    RoutingMode, RoutingPolicy, ServiceError, ServiceSpec, Workspace,
};

/// Request for creating a group collaboration session.
#[derive(Debug, Clone)]
pub struct GroupCreateCommand {
    pub group_id: Option<String>,
    pub caller_actor_id: Option<String>,
    pub driver_bot_id: String,
    pub label: Option<String>,
    pub topic: Option<String>,
    pub context: Option<String>,
    pub routing_policy: Option<RoutingPolicy>,
    pub participants: Vec<GroupCreateParticipantCommand>,
    /// Backward-compatible flat member list for callers that do not provide roles.
    pub member_bot_ids: Vec<String>,
    /// Group kind: Normal (default) or Dm (1:1 direct message).
    pub group_kind: Option<GroupKind>,
    /// Service-as-a-Group configuration. None for regular groups.
    pub service_spec: Option<ServiceSpec>,
    /// Group strategy: Chat (default) or ManagerWorker.
    pub group_strategy: Option<GroupStrategy>,
    /// Actor (bot_uuid or human_xxx) that initiated group creation.
    /// Defaults to driver_bot_id when not specified.
    pub originator: Option<String>,
    /// Group visibility: "public" or "private". Defaults to "private".
    pub visibility: Option<String>,
}

/// Participant input for group creation.
#[derive(Debug, Clone)]
pub struct GroupCreateParticipantCommand {
    pub bot_id: String,
    pub role: Option<String>,
}

/// Request for creating or reusing a 1:1 direct message group.
#[derive(Debug, Clone)]
pub struct DmCreateCommand {
    pub group_id: Option<String>,
    pub caller_actor_id: Option<String>,
    pub driver_bot: Option<String>,
    pub target_actor_id: String,
    pub label: Option<String>,
    pub topic: Option<String>,
    pub context: Option<String>,
}

/// Request for updating a group's lifecycle status.
#[derive(Debug, Clone)]
pub struct GroupStatusCommand {
    pub caller_actor_id: Option<String>,
    pub group_id: String,
    pub status: String,
}

/// Request for adding a bot participant to an existing group.
#[derive(Debug, Clone)]
pub struct GroupAddMemberCommand {
    pub caller_actor_id: Option<String>,
    pub human_actor_id: Option<String>,
    pub group_id: String,
    pub bot_id: String,
    pub role: Option<String>,
}

/// Request for deleting a group.
#[derive(Debug, Clone)]
pub struct GroupDeleteCommand {
    pub caller_actor_id: String,
    pub group_id: String,
}

/// Request for terminating a group.
#[derive(Debug, Clone)]
pub struct GroupTerminateCommand {
    pub caller_actor_id: String,
    pub group_id: String,
}

/// Request for updating a group label.
#[derive(Debug, Clone)]
pub struct GroupUpdateLabelCommand {
    pub caller_actor_id: String,
    pub group_id: String,
    pub label: Option<String>,
}

/// Request for updating a group visibility.
#[derive(Debug, Clone)]
pub struct GroupUpdateVisibilityCommand {
    pub caller_actor_id: String,
    pub group_id: String,
    pub visibility: String,
}

/// Request for replacing a group workspace.
#[derive(Debug, Clone)]
pub struct GroupUpdateWorkspaceCommand {
    pub caller_actor_id: Option<String>,
    pub group_id: String,
    pub workspace: Workspace,
}

/// Request for updating a group's routing policy.
#[derive(Debug, Clone)]
pub struct GroupRoutingPolicyCommand {
    pub caller_actor_id: Option<String>,
    pub group_id: String,
    pub mode: Option<RoutingMode>,
    pub default_bot_final_delivery: Option<DefaultDelivery>,
    pub sender_routes: Option<HashMap<String, Vec<String>>>,
}

/// Request for updating a participant's collaboration mode.
#[derive(Debug, Clone)]
pub struct GroupParticipantModeCommand {
    pub caller_actor_id: String,
    pub group_id: String,
    pub actor_id: String,
    pub mode: ParticipantMode,
}

#[derive(Debug, Clone)]
pub struct GroupListCommand {
    pub group_kind: Option<GroupKind>,
    pub offset: u64,
    pub limit: u64,
    pub visibility: Option<String>,
    pub label: Option<String>,
}

#[derive(Debug, Clone)]
pub struct GroupDetailCommand {
    pub group_id: String,
}

#[derive(Debug, Clone)]
pub struct BotGroupListCommand {
    pub bot_id: String,
    pub group_kind: Option<GroupKind>,
    pub q: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct GroupWorkspaceQueryCommand {
    pub group_id: String,
}

/// Participant view returned by group application-service DTOs.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupParticipantView {
    pub bot_uuid: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_name: Option<String>,
    #[serde(rename = "type", default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<ParticipantKind>,
    pub role: String,
    pub actor_kind: ActorKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ParticipantMode>,
}

/// Group detail shape returned by group management use cases.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupDetailResult {
    pub group_id: String,
    pub label: Option<String>,
    pub status: GroupStatus,
    pub driver_bot_id: String,
    pub context: Option<String>,
    pub participants: Vec<GroupParticipantView>,
    pub message_count: usize,
    pub workspace: Workspace,
    pub service_group_uuid: Option<String>,
    pub service_mode: Option<String>,
    pub group_kind: GroupKind,
    pub dm_pair_key: Option<String>,
    pub group_strategy: GroupStrategy,
    pub created_at: u64,
    pub updated_at: u64,
    pub chat_url: Option<String>,
    pub context_injected: u64,
    pub service_spec: Option<ServiceSpec>,
    pub latest_running_session_id: Option<String>,
    pub originator: Option<String>,
    pub visibility: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupListEntry {
    pub group_id: String,
    pub label: Option<String>,
    pub driver_bot_id: String,
    pub originator: Option<String>,
    pub context: Option<String>,
    pub participants: Vec<GroupParticipantView>,
    pub participant_count: usize,
    pub message_count: usize,
    pub created_at: u64,
    pub updated_at: u64,
    pub group_kind: GroupKind,
    pub group_strategy: GroupStrategy,
    pub visibility: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupListResult {
    pub items: Vec<GroupListEntry>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

/// Response payload for a successful direct-message create-or-reuse.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DmCreateResult {
    pub group: GroupDetailResult,
    pub created: bool,
}

/// Response payload for a successful group member add.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupAddMemberResult {
    pub group_id: String,
    pub member: GroupParticipantView,
}

/// Request for removing a bot participant from an existing group.
#[derive(Debug, Clone)]
pub struct GroupRemoveMemberCommand {
    pub caller_actor_id: Option<String>,
    pub group_id: String,
    pub bot_id: String,
}

/// Response payload for a successful group member removal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupRemoveMemberResult {
    pub group_id: String,
    pub removed_bot_uuid: String,
}

/// Response payload for a successful group delete.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupDeleteResult {
    pub group_id: String,
    pub deleted: bool,
}

/// Response payload for a successful workspace update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupWorkspaceResult {
    pub group_id: String,
    pub workspace: Workspace,
}

/// Response payload for a successful routing-policy update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupRoutingPolicyResult {
    pub group_id: String,
    pub routing_policy: RoutingPolicy,
}

/// Response payload for a successful participant-mode update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupParticipantModeResult {
    pub group_id: String,
    pub actor_id: String,
    pub mode: ParticipantMode,
}

/// Patch the mutable `ServiceSpec` fields of a group's settings.
///
/// The application use case owns the validation rules (immutable
/// `callback_config`; `timeout_seconds` / `max_concurrency` locked while
/// service-invocation sessions are running) so delivery adapters never call
/// into the core service spec helper directly.
#[derive(Debug, Clone)]
pub struct GroupPatchSettingsCommand {
    pub group_id: String,
    /// `None` — no service_spec change. `Some(None)` — remove service_spec.
    /// `Some(Some(spec))` — install / replace the service_spec.
    pub service_spec: Option<Option<ServiceSpec>>,
}

/// Outcome of a successful group settings patch.
#[derive(Debug, Clone)]
pub struct GroupPatchSettingsResult {
    pub group_id: String,
    pub service_spec: Option<ServiceSpec>,
}

/// When validation rejects the patch, the use case returns this via
/// [`GroupUseCaseError::Conflict`]; the running-session count lets the
/// delivery adapter build a precise conflict body.
#[derive(Debug, Clone, serde::Serialize)]
pub struct GroupPatchSettingsConflict {
    pub field: ServiceSpecPatchConflictField,
    pub running_service_count: u64,
}

/// Which `ServiceSpec` field was rejected by the patch validation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceSpecPatchConflictField {
    /// `callback_config` is immutable.
    CallbackConfig,
    /// `timeout_seconds` / `max_concurrency` are locked while sessions run.
    RouteFields,
}

/// Use-case level error with enough detail for delivery adapters to map status.
#[derive(Debug, thiserror::Error)]
pub enum GroupUseCaseError {
    #[error("Unauthorized: {0}")]
    Unauthorized(String),
    #[error("Forbidden: {0}")]
    Forbidden(String),
    #[error("Invalid group ID: {0}")]
    InvalidGroupId(String),
    #[error("Invalid group status: {0}")]
    InvalidGroupStatus(String),
    #[error("Invalid proposal: {0}")]
    InvalidProposal(String),
    #[error("Proposal '{0}' not found or expired")]
    ProposalNotFound(String),
    #[error("Proposal '{0}' expired")]
    ProposalExpired(String),
    #[error("Invalid history limit: {0}")]
    InvalidHistoryLimit(u64),
    #[error("Actor '{0}' not found")]
    ActorNotFound(String),
    #[error("mode '{mode:?}' is not valid for actor_kind '{actor_kind:?}'")]
    InvalidParticipantMode {
        mode: ParticipantMode,
        actor_kind: ActorKind,
    },
    #[error("Conflict: {0}")]
    Conflict(String),
    #[error(transparent)]
    Service(#[from] ServiceError),
}

#[async_trait]
pub trait GroupQueryService: Send + Sync {
    /// List groups, ordered by `updated_at` descending.
    ///
    /// Pagination (`offset`, `limit`) is applied after `group_kind` filtering
    /// and after ordering.
    async fn list_groups(
        &self,
        cmd: GroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError>;

    async fn get_group(
        &self,
        cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError>;

    /// List groups the given actor participates in.
    ///
    /// Groups where the actor's effective mode is [`ParticipantMode::Absent`]
    /// are excluded from both `items` and `total`. This means Human actors
    /// only see groups where they are explicitly present, while Bot actors
    /// using their default `auto` mode are unaffected.
    ///
    /// Results are ordered by `updated_at` descending. Pagination (`offset`,
    /// `limit`) is applied after absent filtering, `group_kind` filtering, and
    /// ordering.
    async fn list_bot_groups(
        &self,
        cmd: BotGroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError>;

    async fn get_workspace(
        &self,
        cmd: GroupWorkspaceQueryCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError>;
}

/// Group management application service.
#[async_trait]
pub trait GroupManagementService: Send + Sync {
    /// Create a group and, after the membership write succeeds, best-effort
    /// create driver-to-public-participant subscription edges.
    async fn create_group(
        &self,
        cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError>;

    async fn create_dm(&self, cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError>;

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError>;

    /// Add a member and, after the membership write succeeds, best-effort
    /// create a driver-to-public-member subscription edge. Edge write failures
    /// do not roll back group membership.
    async fn add_member(
        &self,
        cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError>;

    /// Remove a member from an existing group.
    /// Only the driver (coordinator) can remove members.
    /// The driver itself cannot be removed.
    async fn remove_member(
        &self,
        cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError>;

    /// Delete a group, abort its active StateMachine runs, and remove every
    /// channel binding and StateMachine runtime binding that targets it.
    /// Cleanup failures fail the use case instead of reporting a partial success.
    async fn delete_group(
        &self,
        cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError>;

    async fn terminate_group(
        &self,
        cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError>;

    async fn update_label(
        &self,
        cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError>;

    async fn update_visibility(
        &self,
        cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError>;

    async fn update_workspace(
        &self,
        cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError>;

    async fn update_routing_policy(
        &self,
        cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError>;

    async fn update_participant_mode(
        &self,
        cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError>;

    /// Patch a group's mutable `ServiceSpec` settings. Validates the patch
    /// against the current value and the running service-invocation session
    /// count; returns [`GroupUseCaseError::Conflict`] carrying a
    /// [`GroupPatchSettingsConflict`] in its message (JSON-encoded) when the
    /// patch is rejected.
    async fn patch_group_settings(
        &self,
        cmd: GroupPatchSettingsCommand,
    ) -> Result<GroupPatchSettingsResult, GroupUseCaseError>;
}
