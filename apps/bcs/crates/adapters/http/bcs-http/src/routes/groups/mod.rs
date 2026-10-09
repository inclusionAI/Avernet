use axum::{
    Json,
    extract::{Path, Query, State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, CollaborationRuntimeDefinition,
    OpeningMessageScope, RuntimeParticipantBinding, StateMachineAssignee,
};
use bcs_protocol::{
    CreateGroupRequest, InlineEventPayloadMode, InlineEventSinkInfo,
    InlineGroupEventSubscriptionInfo,
};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::application::v1::{
    ApplicationError, BotFinalDelivery, ChatConfiguration, CollaborationConfiguration,
    CreateCollaborationGroup, CreateDirectMessageGroup, CreateGroup as V1CreateGroup,
    CreateGroupSpec, CreateParticipant, EventPayload, EventPayloadMode, EventSinkInput,
    GroupDeliveryPolicy, GroupDetail as V1GroupDetail, GroupPatch, GroupVisibility,
    InlineGroupEventSubscriptionRequest, ManagerWorkerConfiguration,
    ParticipantRole as V1ParticipantRole, StateMachineConfiguration, StateMachineDefinition,
    StateMachineDefinitionContent, StateMachineParticipantBinding, UpdateGroup,
};
use bcs_service_api::{
    BotDetailCommand, BotGroupListCommand, CallbackChannelConfig, CollaborationRuntimeError,
    ConfigureGroupRuntimeCommand, DefaultDelivery, DmCreateCommand, GroupAddMemberCommand,
    GroupCreateCommand, GroupCreateParticipantCommand, GroupDeleteCommand, GroupDetailCommand,
    GroupDetailResult, GroupKind, GroupListCommand, GroupListEntry, GroupParticipantModeCommand,
    GroupPatchSettingsCommand, GroupRemoveMemberCommand, GroupRoutingPolicyCommand, GroupStatus,
    GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand, GroupUpdateWorkspaceCommand, GroupUseCaseError,
    GroupWorkspaceQueryCommand, MAX_COLLABORATION_DEFINITION_YAML_BYTES, ParticipantMode,
    PatchGroupCollaborationDefinitionCommand, RoutingMode, RoutingPolicy, ServiceError,
    ServiceSpec, SessionKind, SessionStatus, StartStateMachineRunCommand,
    UpgradeGroupCollaborationDefinitionCommand, Workspace,
};
use bcs_service_api::types::MessageViewScope;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap, HashSet};

use crate::error::HttpAdapterError;
use crate::routes::group_messages::{
    GroupChatCaller, application_caller, resolve_group_chat_caller,
};
use crate::state::HttpAppState;

use super::{
    authenticated_bot_from_headers, bots::bot_use_case_error_to_http,
    reject_judge_definition_when_unavailable, reject_judge_yaml_when_unavailable,
    validate_container_header,
};
use super::collaboration_runs::optional_authenticated_human;
mod create;
mod dto;
mod handlers;
mod projections;
mod translation;

pub use create::*;
pub use dto::*;
pub use handlers::*;
pub use projections::*;
pub use translation::*;
