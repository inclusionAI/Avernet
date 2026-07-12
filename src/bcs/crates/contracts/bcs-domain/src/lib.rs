//! BCS core domain model contract.
//!
//! Pure data types shared across `bcs-service-api` (Service API) and
//! `crates/plugin-api/store/*` (Plugin API). This crate is a leaf — it
//! depends only on basic types crates and contains no traits, no I/O,
//! no service implementations.
//!
//! See `docs/arch/refactor-arch-proposal.md` Second-pass amendment 1
//! and `docs/specs/2026-05-16-bcs-arch-refactor-second-pass-design.md`
//! Phase 0 for the rationale.

pub mod actor;
pub mod channel;
pub mod collaboration;
pub mod friend;
pub mod fusion;
pub mod group;
pub mod invite;
pub mod message;
pub mod organization;
pub mod proposal;
pub mod provider;
pub mod register;
pub mod registry;
pub mod routing;
pub mod session;
pub mod system_message;
pub mod task_ledger;

pub use actor::{ActorKind, ActorStatus, EnsureHumanResult, EnsureOwnerEdgesResult, RelationEdge};
pub use channel::{
    BindingStatus, BindingTarget, GroupChatScope, ChannelBinding, ChannelConfig, ChannelType,
    ConversationSessionMap, ImParticipantMap, SessionScope, Visibility,
};
pub use collaboration::{
    ChatRuntimeProfile, CollaborationDefinition, CollaborationDefinitionRef,
    CollaborationMetadata, CollaborationParticipantBinding, CollaborationRequirements,
    CollaborationRuntimeDefinition, GroupRuntimeBinding, JudgePolicy,
    ManagerWorkerRuntimeProfile, OutputContract, ProjectionPolicy, ProjectionVisibility,
    ResolvedParticipant, ResolvedParticipantBinding, RuntimeParticipantBinding,
    StateMachineAction, StateMachineAssignee, StateMachineDefaults,
    StateMachineDefinition, StateMachineDeliveryCorrelation, StateMachineGraphMode,
    StateMachineNodeDefinition, StateMachineNodeKind, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
    StateMachineTransition,
};
pub use friend::{FriendRequest, FriendRequestDirection, FriendRequestStatus};
pub use fusion::{
    ContextBotSummary, ContextConflict, ContextConflictPosition, ContextFusionRequest,
    ContextFusionResponse, ContextParticipantPerspective,
};
pub use group::{
    DefaultDelivery, Group, GroupKind, GroupStatus, GroupStrategy, Participant, ParticipantKind,
    ParticipantMode, ParticipantRole, RoutingMode, RoutingPolicy, SenderRoutesValidationError,
    Workspace,
};
pub use message::{
    AuditEntry, DeliveryType, GroupMessage, GroupMessageType, MessageOwnerFilter, MessagePage, MessageQuery,
    MessageRole, NewMessage, PersistedMessage, PersistedMessageStatus, SenderType, Task, TaskStatus,
};
pub use organization::{Organization, OrganizationMember};
pub use proposal::GroupChatProposal;
pub use provider::{
    BotDeliveryTarget, CoordinationMode, CoordinationSurface, ProviderAuthMode,
    ProviderBotBinding, ProviderCoordinationConfig, ProviderCredential, ProviderRecord,
    RedactedToken,
};
pub use registry::{
    AgentCredentials, BindingChannel, BindingChannels, BotCapabilities, BotConnectParams,
    BotConnectResult, BotDynamicStatus, ConnectionKind, DynamicStatusResponse, RegisteredBot,
    Skill,
};
pub use routing::{
    BotSendResult, ChatEventRouting, HiddenMentionInfo, ResponseMode, RouteAndSendResult,
    RouteParticipantOverlay, RouteSelectorWire, RoutingDecision, RoutingTarget,
};
pub use session::{
    CallbackChannelConfig, CallbackConfig, Session, SessionKind, SessionStatus, ServiceSpec,
};
pub use system_message::{
    SystemGroupMessage, SystemMessageEvent, SystemMessageEventKind
};
pub use task_ledger::LedgerSummary;
pub use invite::{InviteTokenPayload, InviteTokenError, encode as invite_token_encode, decode_and_verify as invite_token_decode_and_verify, decode_and_verify_no_expiry as invite_token_decode_no_expiry};
pub use register::{RegisterTokenPayload, RegisterTokenError, encode as register_token_encode, decode_and_verify as register_token_decode_and_verify};
