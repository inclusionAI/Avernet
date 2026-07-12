//! BCS service trait contracts and domain types.
//!
//! This crate provides service trait definitions that decouple the gateway
//! from the underlying implementations. Each trait defines the interface
//! for a specific domain, allowing for different implementations.
//!
//! Previously lived in `bcs-services`. `bcs-services` is now a pub-use shim
//! and will be removed in a later migration step.

pub mod actors;
pub mod application;
pub mod bot_runtime_use_cases;
pub mod bot_use_cases;
pub mod core;
pub mod friends;
pub mod group_use_cases;
pub mod human_actors;
pub mod interceptor;
pub mod lifecycle;
pub mod message_flow;
pub mod onboard;
pub mod port;
pub mod principal;
pub mod types;
pub mod workbench_use_cases;

pub use actors::{
    ActorCapabilitiesView, ActorDirectoryEntry, ActorDirectoryService, ActorListCommand,
    ActorListResult, ActorSearchCommand, ActorSearchContext, ActorSearchResult,
    ActorStatusUpdateCommand, ActorStatusUpdateResult, WorkerProfile, WorkerProfileService,
    WorkerRecommendCommand, WorkerRecommendResult, WorkerRecommendation,
};
pub use application::SystemMessageService;
pub use application::channel::{
    ChannelInboundError, ChannelInboundFailureKind, ChannelService, ChannelUseCaseError,
    CreateBindingCommand, InboundMessage, OutboundMessage,
};
pub use application::message_log::{
    MessageLogContent, MessageLogEventType, MessageLogMode, MessageLogStatus,
    MessageLogTargetSummary, MESSAGE_LOG_CONTENT_MAX_BYTES, MESSAGE_LOG_SCHEMA_VERSION,
    MSG_LOG_TARGET, message_log_json,
};
pub use application::secret::{SecretService, SecretServiceError, SecretView};
pub use application::invite::{
    CreateInviteTokenCommand, InviteService, InviteTokenResult,
    InviteUseCaseError, JoinByInviteCommand, JoinByInviteResult,
};
pub use application::session::{
    CreateOrReactivateCommand, CreateOrReactivateOutcome, SessionManagementService,
    SessionUseCaseError,
};
pub use application::collaboration_runtime::{
    CancelStateMachineRunCommand, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, DefinitionYamlSource,
    GroupCollaborationDefinitionView, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, MAX_COLLABORATION_DEFINITION_YAML_BYTES,
    PatchGroupCollaborationDefinitionCommand,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineGraphDefinitionView,
    StateMachineGraphEdgeView, StateMachineGraphNodeView, StateMachineRunGraphView,
    StateMachineJudgeOutputView, StateMachineNodeRunView, StateMachineRunView,
    UpgradeGroupCollaborationDefinitionCommand,
};
pub use application::collaboration_template::{
    CollaborationTemplateDetail, CollaborationTemplateError, CollaborationTemplateFormat,
    CollaborationTemplateListResponse, CollaborationTemplateParticipantSummary,
    CollaborationTemplateService, CollaborationTemplateSummary,
    GetCollaborationTemplateQuery, ListCollaborationTemplatesQuery,
};
pub use application::principal::{
    AdminActor, BotActor, CallerContext, HumanActor, IntegrationClient,
};
pub use bot_runtime_use_cases::{
    BotRuntimeConnectCommand, BotRuntimeConnectOutcome, BotRuntimeConnectionService,
    BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, BotRuntimeStatusOutcome,
};
pub use bot_use_cases::{
    BotConnectCommand, BotDetailCommand, BotDetailResult, BotDiscoveryCommand, BotDiscoveryEntry,
    BotDiscoveryProviderInfo, BotDiscoveryResult, BotDiscoveryService, BotLeaveCommand,
    BotLeaveResult, BotListCommand, BotListEntry, BotListResult, BotManagementService,
    BotPagedListCommand, BotPagedListResult, BotQueryByIdsCommand, BotQueryByIdsResult,
    BotQueryEntry, BotQueryService,
    BotStatusUpdateCommand, BotStatusUpdateResult, BotUseCaseError, BotVisibilityCommand,
    BotVisibilityQueryCommand, BotVisibilityQueryResult, BotVisibilityResult, MyBotsCommand,
    SwitchDeliveryToProviderCommand, SwitchDeliveryToProviderResult,
};
pub use friends::{
    CreateFriendRequestCommand, FriendListEntry, FriendRequestDecisionCommand, FriendUseCaseError,
    FriendService, ListFriendRequestsCommand, ListFriendsCommand,
};
pub use group_use_cases::{
    BotGroupListCommand, DmCreateCommand, DmCreateResult, GroupAddMemberCommand, GroupAddMemberResult, GroupCreateCommand,
    GroupCreateParticipantCommand, GroupDeleteCommand, GroupDeleteResult, GroupDetailCommand,
    GroupDetailResult, GroupHistoryCommand, GroupHistoryResult, GroupListCommand, GroupListEntry,
    GroupListResult, GroupManagementService, GroupMessageHistoryService,
    GroupPatchSettingsCommand, GroupPatchSettingsConflict, GroupPatchSettingsResult,
    GroupParticipantModeCommand, GroupParticipantModeResult, GroupParticipantView,
    GroupRemoveMemberCommand, GroupRemoveMemberResult,
    ServiceSpecPatchConflictField,
    SessionHistoryCommand, SessionHistoryResult,
    GroupProposalConfirmCommand, GroupProposalConfirmResult, GroupProposalCreateCommand,
    GroupProposalCreateResult, GroupProposalPreviewCommand, GroupProposalPreviewResult,
    GroupProposalService, GroupQueryService, GroupRoutingPolicyCommand, GroupRoutingPolicyResult,
    GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand, GroupUpdateWorkspaceCommand, GroupUseCaseError,
    GroupWorkspaceQueryCommand,
    GroupWorkspaceResult, ProposalContext,
};
pub use human_actors::{
    CurrentHumanActorCommand, EnsureCurrentHumanActorError, EnsureCurrentHumanActorResult,
    HumanActorService, RepairHumanActorInfoResult,
};
pub use message_flow::{
    A2aChatCommand, A2aChatOutcome, A2aChatRunService, A2aChatService, A2aRunStatus,
    AsyncA2aChatAccepted, AsyncA2aChatCommand, BlockingA2aChatCommand, BlockingA2aChatOutcome,
    BotEventCommand, BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, ChatEventState,
    ChatResponseMode,
    ChatRunCancelCommand, ChatRunQueryCommand, Conflict, ConflictPosition, FusionRequest,
    FusionResponse, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand, GroupChatOutcome,
    GroupFusionCommand, GroupFusionService, MessageDeliveryResult, MessageFlowService,
    ParticipantPerspective, PersistentGroupSendCommand, PersistentGroupSendOutcome,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskMessageCommand, TaskMessageOutcome, TaskRunAliasRegistration, WebSendCommand,
    WebSendOutcome,
};
pub use onboard::{
    AdminBotOnboardCommand, BotOnboardCommand, BotOnboardResult, BotOnboardingService,
    OnboardActorIdentity,
};
pub use application::{
    DeleteProviderBotCommand, DeleteProviderBotOutcome, ProviderBotCoordinationCommand,
    ProviderBotCoordinationOutcome, ProviderBotEventCommand, ProviderBotEventCredential,
    ProviderBotEventError, ProviderBotEventOutcome, ProviderBotEventService,
    ProviderCoordinationEventKind, ProviderCoordinationIntent, ProviderManagementService,
    RegisterProviderBotCommand, RegisterProviderBotOutcome, RegisterProviderCommand,
    RegisterProviderOutcome, UpdateProviderCommand, CreateOrganizationCommand,
    OrganizationAuth, OrganizationManagementService, PutOrganizationMemberCommand,
    UpdateOrganizationCommand,
};
pub use port::{
    BotConnectionControlPort, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryResult, BotMetricCount, BotMetricsSnapshotPort, BotRepoPort, BotRunContext,
    BotRunContextPort, ChatRunCleanupPort, ChatRunEventPort, ChatRunMetricCount, DeliveryBlockContext,
    DeliveryBlockReason, DeliveryBlockSurface, DeliveryMetricKind, DeliveryMetricTarget,
    DeliveryPolicyBlockInstrumentationHook, DirectChatClientKind, DirectChatRunEvent,
    DirectChatRunLifecycleHook, DirectChatRunReason, DirectChatRunSnapshotPort, DirectChatRunState,
    ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
    ChannelOutboundEventKind, ChannelRenderHint,
    ChannelBindingRepoPort, ConversationSessionRepoPort,
    FriendRepoPort, FriendRequestRepoPort, FrontendDeliveryCommand, FrontendDeliveryKind,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget,
    GroupHistoryBotRequestPort, GroupDispatchContextPort, GroupMetricCount, GroupMetricsSnapshotPort,
    GroupRepoPort, GroupRuntimeBindingRepoPort, GroupSessionMetricCount,
    GroupSessionMetricsSnapshotPort, JudgeArtifact,
    JudgeCheckedCriterion, JudgeDecision, JudgeEvaluatorPort, JudgeRequest, KickReason,
    ImParticipantRepoPort,
    LeaderElectionPort, LeaderInfo, LeaderStatus, MetricsResult, NewSessionParams,
    ProviderBotBindingRepoPort, ProviderBotDiscoveryRecord, ProviderBotDiscoverySelector,
    ProviderCredentialRepoPort, ProviderRepoPort, ProviderStreamGrayList,
    CreateOrganizationRecord, ListOrganizationMembersQuery, ListOrganizationsQuery,
    OrganizationRepoPort, UpdateOrganizationRecord, UpsertOrganizationMemberRecord,
    ProviderTransportPreference, RelationRepoPort,
    RunFallbackDelivery, SessionCallbackDispatchPort, SessionRepoPort,
    StateMachineDefinitionRepoPort, StateMachineRunRepoPort,
    UserIdentity, UserIdentityRepoPort,
    CollaborationTemplateEntry, CollaborationTemplateRepoPort,
    CollaborationDefinitionRecord, CollaborationEventRecord, CollaborationEventRepoPort,
    WsCloseReason, WsErrorKind,
    WsLifecycleInstrumentationHook, WsPeer,
};
pub use workbench_use_cases::{
    WorkbenchChatAuthorizationCommand, WorkbenchConnectCommand, WorkbenchConnectOutcome,
    WorkbenchParticipantView, WorkbenchSessionService, WorkbenchUseCaseError,
};

pub use types::{
    BotDeliveryTarget, CallbackChannelConfig, CallbackConfig, CoordinationMode,
    CoordinationSurface, ProviderAuthMode, ProviderBotBinding, ProviderCoordinationConfig,
    ProviderCredential, ProviderOrganizationManagementConfig, ProviderRecord, RedactedToken,
    ChatRuntimeProfile, CollaborationDefinition,
    CollaborationDefinitionRef, CollaborationMetadata, CollaborationParticipantBinding,
    CollaborationRequirements, CollaborationRuntimeDefinition, GroupRuntimeBinding,
    JudgePolicy, ManagerWorkerRuntimeProfile, OutputContract, ProjectionPolicy,
    ProjectionVisibility, ResolvedParticipant, ResolvedParticipantBinding,
    RuntimeParticipantBinding, StateMachineAction, StateMachineAssignee, StateMachineDefaults,
    StateMachineDefinition, StateMachineDeliveryCorrelation, StateMachineGraphMode,
    StateMachineNodeDefinition, StateMachineNodeKind, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus, StateMachineTransition,
};

pub use core::{
    ActorKind, ActorStatus, AgentCredentials, AuditEntry, BindingChannel, BindingChannels,
    BotCapabilities, BotConnectParams, BotConnectResult, BotDynamicStatus, BotRegistryCoreService,
    BotSendResult, ChatEventRouting, ConnectError, ConnectionKind, ContextBotSummary, HiddenMentionInfo,
    ContextBotSummary as BotContextSummary, ContextConflict, ContextConflictPosition,
    ContextFusionRequest, ContextFusionResponse, ContextParticipantPerspective, DefaultDelivery,
    DeliveryType, DmActorSpec, DynamicStatusResponse, EnsureHumanResult, EnsureOwnerEdgesResult,
    FriendCoreService, FriendRequest, FriendRequestCoreService, FriendRequestDirection,
    FriendRequestStatus, FusionCoreService, Group, GroupChatProposal, GroupCoreService, GroupKind,
    GroupMessage, GroupMessageType, GroupStatus, GroupStrategy, MessageRole, Participant,
    ParticipantKind, ParticipantMode, ParticipantRole, ProposalCoreService,
    ProviderBotCoreService, ProviderCoreService, RegisterProviderBotParams, RegisteredBot,
    RegisteredProvider, AuthorizedOrganizationPair, OrganizationCandidateBot, OrganizationCandidateQuery,
    OrganizationCoreService,
    RelationCoreService, BCS_SYSTEM_MESSAGE, RelationEdge, ResponseMode, RouteAndSendResult,
    RouteParticipantOverlay, RouteSelectorWire, RoutingCoreService, RoutingDecision,
    RoutingMode, RoutingPolicy, RoutingTarget, RuntimeBotIdentity, SenderRoutesValidationError,
    ServiceError, ServiceResult, ServiceSpec, Session, SessionKind, SessionStatus, Skill,
    StructuredRoutingError,
    SystemGroupMessage, SystemMessageDispatchOutcome, SystemMessageDispatcherService,
    SystemMessageEvent, SystemMessageEventKind, SystemMessageProducerService,
    SystemMessageRecipientResult, Task, TaskStatus, Workspace,
    backfill_bot_names, backfill_participant_names, deserialize_skills, validate_sender_routes,
};

pub use bcs_domain::{
    InviteTokenPayload, InviteTokenError,
    invite_token_encode, invite_token_decode_and_verify, invite_token_decode_no_expiry,
};

// Note: bcs-bot-connectors has been removed. Bot communication uses the streaming adapter.
