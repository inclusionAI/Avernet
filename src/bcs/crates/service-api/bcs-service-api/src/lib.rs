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
    ActorStatusUpdateCommand, ActorStatusUpdateResult,
};
#[deprecated(
    note = "worker-profile contracts moved to bcs_service_api::core; import them from core"
)]
pub use core::{
    WorkerProfile, WorkerProfileCoreService as WorkerProfileService, WorkerRecommendCommand,
    WorkerRecommendResult, WorkerRecommendation,
};
pub use application::SystemMessageService;
pub use application::v1::{
    BotInternalAttributes, FriendCheckInStrategy, InternalBotAttributesService,
    PatchBotInternalAttributes, UserVisibility,
};
pub use application::system_message::resolve_session_topic;
pub use application::interaction::{
    InteractionRequestedOutcome, InteractionService, InteractionServiceError,
    ProviderInteractionRequestedCommand,
    ProviderInteractionResolvedCommand, ResolveInteractionCommand, ResolveInteractionResult,
};
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
    ClaimSessionCallbackCommand, ClaimSessionCallbackOutcome,
    CompleteSessionCallbackCommand, CreateOrReactivateCommand, CreateOrReactivateOutcome,
    SessionManagementService, SessionUseCaseError,
};
pub use application::session_launch::{
    CreateSessionLaunch, ReactivateSessionLaunch, RequestedSessionRole, SessionCaller,
    SessionLaunchError, SessionLaunchOutcome, SessionLaunchRequest, SessionLaunchService,
};
pub use application::session_files::{
    CapabilitiesView, DeleteFileCommand, DownloadRoute, PrepareUploadCommand, PrepareUploadResult,
    SessionFileService, SessionFileUseCaseError, ShareConsumeResult, ShareMintCommand,
    ShareMintResult,
};
pub use application::collaboration_runtime::{
    AuthenticatedHumanCaller, CancelStateMachineRunCommand,
    CollaborationDefinitionGraphEdge, CollaborationDefinitionGraphNode,
    CollaborationDefinitionGraphPreview, CollaborationDefinitionParticipantSlot,
    CollaborationDefinitionValidationDiagnostic, CollaborationDefinitionValidationOutcome,
    CollaborationDefinitionValidationSummary, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    DefinitionYamlSource, GroupCollaborationDefinitionView, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, HandleSessionHumanInputCommand,
    HandleSessionHumanInputOutcome, HumanResponseSource, HumanRunAccessCommand,
    ListPendingHumanNodesCommand, MAX_COLLABORATION_DEFINITION_YAML_BYTES,
    PatchGroupCollaborationDefinitionCommand, PendingHumanNodeView, RespondHumanNodeCommand,
    RespondHumanNodeOutcome, RerunStateMachineCommand, RerunStateMachineOutcome,
    SessionStateMachinePermissionCommand, SessionStateMachinePermissionView,
    StartSessionStateMachineRunCommand, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineGraphDefinitionView,
    StateMachineGraphEdgeView, StateMachineGraphNodeView, StateMachineRunGraphView,
    StateMachineJudgeOutputView, StateMachineNodeRunView, StateMachineNodeSubStatus,
    StateMachineRunAccessCommand, StateMachineRunView, UpgradeGroupCollaborationDefinitionCommand,
    ValidateCollaborationDefinitionYamlCommand,
};
pub use application::collaboration_template::{
    CollaborationTemplateDetail, CollaborationTemplateError, CollaborationTemplateFormat,
    CollaborationTemplateListResponse, CollaborationTemplateParticipantSummary,
    CollaborationTemplateService, CollaborationTemplateSummary, GetCollaborationTemplateQuery,
    ListCollaborationTemplatesQuery,
};
pub use application::principal::{
    AdminActor, BotActor, CallerContext, HumanActor, IntegrationClient, RequestAuthHeaders,
};
pub use bot_runtime_use_cases::{
    BotRuntimeConnectCommand, BotRuntimeConnectOutcome, BotRuntimeConnectionService,
    BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, BotRuntimeStatusOutcome,
};
pub use types::{BotSearchCandidateQuery, BotSearchFriendshipFilter};

pub use bot_use_cases::{
    BotConnectCommand, BotDetailCommand, BotDetailResult, BotDiscoveryCommand, BotDiscoveryEntry,
    BotDiscoveryProviderInfo, BotDiscoveryResult, BotDiscoveryService, BotLeaveCommand,
    OrganizationMemberSummary,
    BotLeaveResult, BotListCommand, BotListEntry, BotListResult, BotManagementService,
    BotPagedListCommand, BotPagedListResult, BotQueryByIdsCommand, BotQueryByIdsResult,
    BotQueryEntry, BotQueryService, BotSearchResult, BotStatusUpdateCommand, BotStatusUpdateResult,
    BotUseCaseError, BotVisibilityCommand, BotVisibilityQueryCommand, BotVisibilityQueryResult,
    BotVisibilityResult, MyBotsCommand, SearchBotsCommand, SwitchDeliveryToProviderCommand,
    SwitchDeliveryToProviderResult,
};
pub use friends::{
    CreateFriendRequestCommand, FriendListEntry, FriendRequestDecisionCommand, FriendService,
    FriendUseCaseError, ListFriendRequestsCommand, ListFriendsCommand,
};
pub use group_use_cases::{
    BotGroupListCommand, DmCreateCommand, DmCreateResult, GroupAddMemberCommand,
    GroupAddMemberResult, GroupCreateCommand, GroupCreateParticipantCommand, GroupDeleteCommand,
    GroupDeleteResult, GroupDetailCommand, GroupDetailResult, GroupHistoryCommand,
    GroupHistoryResult, GroupListCommand, GroupListEntry, GroupListResult, GroupManagementService,
    GroupMessageHistoryService, GroupParticipantModeCommand, GroupParticipantModeResult,
    InitialGroupRun, InitialGroupRunActivityKind, InitialGroupRunState, MessageHistoryOptions,
    GroupParticipantView, GroupPatchSettingsCommand, GroupPatchSettingsConflict,
    GroupPatchSettingsResult, GroupProposalConfirmCommand, GroupProposalConfirmResult,
    GroupProposalCreateCommand, GroupProposalCreateResult, GroupProposalPreviewCommand,
    GroupProposalPreviewResult, GroupProposalService, GroupQueryService, GroupRemoveMemberCommand,
    GroupRemoveMemberResult, GroupRoutingPolicyCommand, GroupRoutingPolicyResult,
    GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand, GroupUpdateWorkspaceCommand, GroupUseCaseError,
    GroupWorkspaceQueryCommand, GroupWorkspaceResult, ProposalContext,
    ServiceSpecPatchConflictField, SessionHistoryCommand, SessionHistoryResult,
};
pub use human_actors::{
    CurrentHumanActorCommand, EnsureCurrentHumanActorError, EnsureCurrentHumanActorResult,
    HumanActorService, RepairHumanActorInfoResult,
};
pub use message_flow::{
    A2aChatCommand, A2aChatOutcome, A2aChatRunService, A2aChatService, A2aRunStatus,
    AsyncA2aChatAccepted, AsyncA2aChatCommand,
    BotEventCommand, BotEventOutcome, ChatAbortCommand, ChatAbortFailure, ChatAbortOutcome,
    ChatAbortScope, ChatEventState, ChatResponseMode, ChatRunCancelCommand, ChatRunQueryCommand,
    Conflict, ConflictPosition,
    FusionRequest, FusionResponse, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand,
    GroupChatOutcome, GroupFusionCommand, GroupFusionService, MessageDeliveryResult,
    MessageFlowService, ParticipantPerspective, PersistentGroupSendCommand,
    PersistentGroupSendOutcome, ProviderEventIngestCommand, ProviderEventSource,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand,
    TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome, TaskRunAliasRegistration,
    ChannelSenderIdentity, WebSendCommand, WebSendOutcome,
};
pub use onboard::{
    AdminBotOnboardCommand, BotOnboardCommand, BotOnboardResult, BotOnboardingService,
    EnsureBotCommand, EnsureBotResult, OnboardActorIdentity,
};
pub use application::{
    DeleteProviderBotCommand, DeleteProviderBotOutcome, ProviderBotCoordinationCommand,
    ProviderBotCoordinationOutcome, ProviderBotEventCommand, ProviderBotEventCredential,
    ProviderBotEventError, ProviderBotEventOutcome, ProviderBotEventService,
    ProviderBotRosterItem, ProviderBotTaskModesFilter,
    ProviderEventIngestService,
    ProviderCoordinationEventKind, ProviderCoordinationIntent, ProviderManagementService,
    RegisterProviderBotCommand, RegisterProviderBotOutcome, RegisterProviderCommand,
    RegisterProviderOutcome, UpdateProviderBotCommand, UpdateProviderBotOutcome,
    UpdateProviderCommand, DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
    CreateOrganizationCommand,
    OrganizationAuth, OrganizationManagementService, OrganizationMemberAuth,
    PutOrganizationMemberCommand, UpdateOrganizationCommand,
    UpdateOrganizationMemberProfileCommand,
};
pub use port::{
    ActiveBotRunContext, BotAbortDeliveryCommand, BotAbortDeliveryResult, BotConnectionControlPort,
    BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotMetricCount,
    BotMetricsSnapshotPort, BotRepoPort, BotRunContext, BotControlPlaneRepoPort,
    BotRunContextPort, BotRunScope, BotRunTransportOwner, BotTerminalEvent,
    BotTerminalObserverPort, BotTerminalState,
    CompositeBotTerminalObserver, ProviderRunTransport, ProviderTransportPreference,
    NoopBotTerminalObserver, NoopChannelBindingCleanupPort, ChatRunCleanupPort,
    ChatRunEventPort, ChatRunMetricCount, DeliveryBlockContext,
    DeliveryBlockReason, DeliveryBlockSurface, DeliveryMetricKind, DeliveryMetricTarget,
    DeliveryPolicyBlockInstrumentationHook, DirectChatClientKind, DirectChatRunEvent,
    DirectChatRunLifecycleHook, DirectChatRunReason, DirectChatRunSnapshotPort, DirectChatRunState,
    ChannelBindingCleanupPort, ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult,
    ChannelOutboundEvent, ChannelOutboundEventKind, ChannelOutboundPurpose, ChannelRenderHint,
    ChannelBindingRepoPort, ConversationSessionRepoPort, HumanInputEnqueueDisposition,
    HumanInputRequestRepoPort,
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort, NoopFriendConnectNotificationPort,
    FriendRepoPort, FriendRequestRepoPort, FrontendDeliveryCommand, FrontendDeliveryKind,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget, GroupDispatchContextPort,
    GroupHistoryBotRequestPort, GroupMetricCount, GroupMetricsSnapshotPort, GroupRepoPort,
    GroupRuntimeBindingRepoPort, GroupSessionMetricCount, GroupSessionMetricsSnapshotPort,
    HumanInputReadyEvent, ImParticipantRepoPort, InteractionFrontendPort, JudgeArtifact,
    JudgeCheckedCriterion, JudgeDecision, JudgeEvaluatorPort, JudgeRequest, KickReason,
    LeaderElectionPort, LeaderInfo,
    LeaderStatus, MarkHumanNodeRunningCommand, MetricsResult, NewSessionParams,
    PendingGroupMessage, PendingGroupMessageKind, PendingGroupMessagePort,
    ProviderBotBindingRepoPort, ProviderBotDiscoveryRecord, ProviderBotDiscoverySelector,
    ProviderCredentialRepoPort, ProviderRepoPort, ProviderStreamGrayList,
    CreateOrganizationRecord, ListOrganizationMembersPageQuery, ListOrganizationMembersQuery,
    ListOrganizationsQuery, OrganizationCandidateReadPage, OrganizationCandidateReadPort,
    OrganizationCandidateReadQuery, OrganizationMemberPage, OrganizationRepoPort, UpdateOrganizationRecord,
    UpsertOrganizationMemberRecord,
    RelationRepoPort, RunFallbackDelivery,
    SessionCallbackDispatchPort, SessionChannelDeliveryOutcome, SessionChannelOutboundPort,
    SessionRepoPort, StateMachineDefinitionRepoPort, StateMachineResultPublishCommand,
    StateMachineResultPublisherPort, StateMachineRunRepoPort, StateMachineTerminalEvent,
    StateMachineTerminalStatus, UserIdentity, UserIdentityRepoPort,
    CollaborationTemplateEntry, CollaborationTemplateRepoPort,
    CollaborationDefinitionRecord, CollaborationEventRecord, CollaborationEventRepoPort,
    CreateStateMachineRerun, CreateStateMachineRerunOutcome,
    WsCloseReason, WsErrorKind, WsLifecycleInstrumentationHook, WsPeer,
};
pub use workbench_use_cases::{
    WorkbenchChatAbortAuthorizationCommand, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchConnectOutcome, WorkbenchParticipantView,
    WorkbenchSessionService, WorkbenchUseCaseError,
};

pub use types::{
    BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotControlPlaneDescriptor, BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery,
    BotControlPlanePatch, BotControlPlaneRecord,
    BotTaskModesQuery, TaskModeMatch,
    BotDeliveryTarget, CallbackChannelConfig, CallbackConfig, CoordinationMode,
    CoordinationSurface, ProviderAuthMode, ProviderBotBinding, ProviderBotConnectionMode,
    ProviderCoordinationConfig, ProviderCredential, ProviderOrganizationManagementConfig,
    ProviderRecord, RedactedToken,
    ChatRuntimeProfile, CollaborationDefinition,
    CollaborationDefinitionRef, CollaborationMetadata, CollaborationParticipantBinding,
    CollaborationRequirements, CollaborationRuntimeDefinition, GroupRuntimeBinding,
    JudgePolicy, ManagerWorkerRuntimeProfile, OutputContract, ProjectionPolicy,
    ProjectionVisibility, ResolvedParticipant, ResolvedParticipantBinding,
    RuntimeParticipantBinding, StateMachineAction, StateMachineAssignee, StateMachineDefaults,
    StateMachineDefinition, StateMachineDeliveryCorrelation, StateMachineGraphMode,
    StateMachineNodeDefinition, StateMachineNodeKind, StateMachineNodeRun, StateMachineNodeStatus,
    StateMachineRun, StateMachineRunStatus, StateMachineTransition,
};

pub use core::{
    ActorKind, ActorStatus, AgentCredentials, AuditEntry, BindingChannel, BindingChannels,
    BotCandidateSearchCoreResult, BotCandidateSearchCoreService, BotCandidateSearchHit,
    BotCandidateSearchMode, BotCandidateSearchQuery, BotCapabilities, BotConnectParams,
    BotConnectResult, BotControlPlaneCandidate,
    BotControlPlaneCoreService, BotControlPlaneProvider, BotControlPlaneView, BotDynamicStatus,
    BotRegistryCoreService,
    BotSendResult, ChatEventRouting, ConnectError, ConnectStreamError, ConnectionKind,
    ContextBotSummary, HiddenMentionInfo, ContextBotSummary as BotContextSummary, ContextConflict, ContextConflictPosition,
    ContextFusionRequest, ContextFusionResponse, ContextParticipantPerspective, DefaultDelivery,
    DeliveryType, DmActorSpec, DynamicStatusResponse, EnsureHumanResult, EnsureOwnerEdgesResult,
    EdgePermissionFriendSyncService, FriendCoreService, FriendRequest, FriendRequestCoreService, FriendRequestDirection,
    FriendRequestStatus, Friendship, FusionCoreService, Group, GroupChatProposal, GroupCoreService, GroupKind,
    GroupMessage, GroupMessageType, GroupMutableFieldsPatch, GroupStatus, GroupStrategy, MessageRole,
    Participant, ParticipantKind, ParticipantMode, ParticipantRole, ProposalCoreService,
    ProviderBotCoreService, ProviderCoreService, RegisterProviderBotParams, RegisteredBot,
    RegisteredProvider, UpdateProviderBotCoreResult, AuthorizedOrganizationPair,
    OrganizationCandidateBot, OrganizationCandidateBotDetail, OrganizationCandidateBotPage,
    OrganizationCandidatePageQuery, OrganizationCandidateQuery,
    OrganizationCoreService, OrganizationMemberBotDetail, OrganizationMemberDetail,
    OrganizationMemberPageQuery, OrganizationMemberProfile, OrganizationMemberProfilePatch,
    LegacyBotCandidateSearchCoreResult, RelationCoreService, BCS_SYSTEM_MESSAGE, RelationEdge,
    ResponseMode, RouteAndSendResult,
    RouteParticipantOverlay, RouteSelectorWire, RoutingCoreService, RoutingDecision,
    RoutingMode, RoutingPolicy, RoutingTarget, RuntimeBotIdentity, SenderRoutesValidationError,
    ServiceError, ServiceResult, ServiceSpec, Session, SessionKind, SessionStatus, Skill,
    StructuredRoutingError, MOCK_TOKEN_PREFIX, mock_token, is_mock_token,
    SystemGroupMessage, SystemMessageDispatchOutcome, SystemMessageDispatcherService,
    SystemMessageEvent, SystemMessageEventKind, SystemMessageProducerService,
    SystemMessageRecipientResult, Task, TaskStatus, Workspace, backfill_bot_names,
    backfill_participant_names, deserialize_skills, validate_sender_routes,
};

pub use core::interaction::{
    InteractionKey, InteractionKind, InteractionStatus, InteractionTransitionError,
};
pub use port::interaction::{
    CanResolveInteraction, CanResolveInteractionCommand, CanResolveInteractionPort,
    InteractionFrontendEvent, InteractionInsertResult, InteractionProviderAck,
    InteractionProviderCommand, InteractionProviderPort, InteractionRecord,
    InteractionResolveClaim, InteractionResolveCommit, InteractionStorePort,
};

pub use port::repo::chat_run::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, MAX_CONTENT_BYTES as CHAT_RUN_MAX_CONTENT_BYTES,
};

pub use bcs_domain::{
    GENERATED_SESSION_ID_SUFFIX_CHARS, GROUP_ID_PREFIX, GroupIdBuildError,
    InviteTargetType, InviteTokenPayload, InviteTokenError,
    MAX_GENERATED_GROUP_ID_CHARS, MAX_SESSION_ID_CHARS, channel_group_id,
    generated_group_id,
    invite_token_encode, invite_token_decode_and_verify, invite_token_decode_no_expiry,
};

// Note: bcs-bot-connectors has been removed. Bot communication uses the streaming adapter.
