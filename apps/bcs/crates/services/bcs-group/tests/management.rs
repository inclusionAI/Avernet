use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, RwLock},
};

use async_trait::async_trait;
use bcs_service_api::{
    ActorKind, AgentCredentials, BotCapabilities, BotRegistryCoreService,
    BotDeliveryTarget, BotRuntimeConnectCommand, BotRuntimeConnectOutcome,
    BotRuntimeConnectionService, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand,
    BotRuntimeStatusOutcome, BotUseCaseError, CanResolveInteraction, CanResolveInteractionCommand,
    DefaultDelivery, DmCreateCommand,
    EnsureOwnerEdgesResult, FriendCoreService, Group, GroupDetailCommand,
    HumanMentionNotifyMode,
    GroupAddMemberCommand, GroupCreateCommand, GroupCreateParticipantCommand, GroupDeleteCommand, GroupRemoveMemberCommand,
    GroupListCommand, GroupManagementService, GroupParticipantModeCommand,
    GroupQueryService, GroupRoutingPolicyCommand, GroupCoreService, GroupKind, GroupStatus,
    GroupStatusCommand, GroupStrategy, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand,
    GroupUseCaseError, RegisteredBot, RelationEdge, RelationCoreService,
    Participant, ParticipantMode, ParticipantRole, RoutingMode, RoutingPolicy, ServiceError, ServiceResult, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError, SystemMessageDispatchOutcome,
    SystemMessageEvent, SystemMessageRecipientResult, SystemMessageService,
    WorkbenchChatAbortAuthorizationCommand, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchSessionService, WorkbenchUseCaseError,
};
use bcs_service_api::types::{MessageViewScope, OpeningMessage};
use bcs_group::{GroupConfig, GroupManagement, GroupStore};
use bcs_test_support::NoopSystemMessageService;
use tokio::sync::Mutex;

#[path = "management/support/fixture.rs"]
mod fixture;
#[path = "management/support/builders.rs"]
mod builders;
#[path = "management/support/session.rs"]
mod session_support;
#[path = "management/support/registry.rs"]
mod registry_support;
#[path = "management/support/friend.rs"]
mod friend_support;
#[path = "management/support/relation.rs"]
mod relation_support;
#[path = "management/support/runtime.rs"]
mod runtime_support;

use fixture::Fixture;
use builders::{bot, create_cmd, human, participant};
use session_support::{StaticSessionManagement, test_session};
use registry_support::FakeRegistry;
use friend_support::FakeFriendCoreService;
use relation_support::FakeRelationCoreService;
use runtime_support::FakeBotRuntimeConnectionService;

#[path = "management/no_session.rs"]
mod no_session;

#[path = "management/creation.rs"]
mod creation;
#[path = "management/initial_session.rs"]
mod initial_session;
#[path = "management/query_projections.rs"]
mod query_projections;
#[path = "management/query_lists.rs"]
mod query_lists;
#[path = "management/members.rs"]
mod members;
#[path = "management/mutations.rs"]
mod mutations;
#[path = "management/dm.rs"]
mod dm;
#[path = "management/deletion.rs"]
mod deletion;
#[path = "management/visibility.rs"]
mod visibility;
#[path = "management/interaction.rs"]
mod interaction;
#[path = "management/workbench_connect.rs"]
mod workbench_connect;
#[path = "management/workbench_chat.rs"]
mod workbench_chat;
#[path = "management/workbench_abort.rs"]
mod workbench_abort;
#[path = "management/human_authority.rs"]
mod human_authority;
