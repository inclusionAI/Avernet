//! `Services` bundle and `ServicesBuilder`.
//!
//! The production builder fails fast when a required service is unset. Test
//! Noop wiring lives in `test_support`.

use std::sync::Arc;

use bcs_service_api::{
    A2aChatRunService, A2aChatService, ActorDirectoryService, BotDeliveryPort, BotDiscoveryService,
    BotManagementService, BotOnboardingService, BotQueryService, BotRegistryCoreService,
    BotRunContextPort, BotRuntimeConnectionService, ChannelService, CollaborationRuntimeService,
    CollaborationTemplateService,
    FriendCoreService, FriendService, FrontendDeliveryPort,
    FusionCoreService, Group, GroupCoreService, GroupFusionService, GroupManagementService,
    GroupMessageHistoryService, GroupProposalService, GroupQueryService, HumanActorService,
    MessageFlowService, OrganizationManagementService,
    ProposalCoreService, ProviderBotCoreService, ProviderCoreService,
    ProviderBotEventService, ProviderManagementService, RelationCoreService, RoutingCoreService,
    SecretService, SessionManagementService, SystemMessageService, WorkbenchSessionService,
    backfill_bot_names,
};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum BuilderError {
    #[error("missing service: {0}")]
    MissingService(&'static str),
}

/// Bundle of all services.
#[derive(Clone)]
pub struct Services {
    /// Bot registry service.
    pub registry: Arc<dyn BotRegistryCoreService>,
    /// Group service.
    pub group: Arc<dyn GroupCoreService>,
    /// Routing service.
    pub routing: Arc<dyn RoutingCoreService>,
    /// Fusion service.
    pub fusion: Arc<dyn FusionCoreService>,
    /// Proposal service.
    pub proposal: Arc<dyn ProposalCoreService>,
    /// Friend relationship service.
    pub friend: Arc<dyn FriendCoreService>,
    /// Relation graph service (Human Actor V1, see `bcs_actor_relations`).
    pub relation: Arc<dyn RelationCoreService>,
    /// Message/chat flow orchestration service.
    pub message_flow: Arc<dyn MessageFlowService>,
    /// A2A direct chat and async run service.
    pub a2a_chat: Arc<dyn A2aChatService>,
    /// A2A route-closure chat run service.
    pub a2a_chat_runs: Arc<dyn A2aChatRunService>,
    /// Collaboration state-machine runtime service.
    pub collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    /// File-backed collaboration template application service.
    pub collaboration_templates: Arc<dyn CollaborationTemplateService>,
    /// Bot runtime delivery port.
    pub bot_delivery: Arc<dyn BotDeliveryPort>,
    /// Bot run context store for async callbacks.
    pub bot_run_context: Arc<dyn BotRunContextPort>,
    /// Workbench/Web frontend delivery port.
    pub frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    /// Actor directory application service.
    pub actor_directory: Arc<dyn ActorDirectoryService>,
    /// Friend application service.
    pub friend_use_cases: Arc<dyn FriendService>,
    /// Human actor application service.
    pub human_actors: Arc<dyn HumanActorService>,
    /// Bot onboarding application service.
    pub bot_onboarding: Arc<dyn BotOnboardingService>,
    /// Bot query application service.
    pub bot_query: Arc<dyn BotQueryService>,
    /// Bot discovery application service.
    pub bot_discovery: Arc<dyn BotDiscoveryService>,
    /// Bot management application service.
    pub bot_management: Arc<dyn BotManagementService>,
    /// Bot runtime WebSocket application service.
    pub bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    /// Provider registry core service.
    pub provider_core: Arc<dyn ProviderCoreService>,
    /// Provider-bot binding core service.
    pub provider_bot_core: Arc<dyn ProviderBotCoreService>,
    /// Provider management application service.
    pub provider_management: Arc<dyn ProviderManagementService>,
    /// Provider bot event application service.
    pub provider_bot_events: Arc<dyn ProviderBotEventService>,
    /// Organization management application service.
    pub organization_management: Arc<dyn OrganizationManagementService>,
    /// Group query application service.
    pub group_query: Arc<dyn GroupQueryService>,
    /// Group management application service.
    pub group_management: Arc<dyn GroupManagementService>,
    /// Workbench WebSocket session application service.
    pub workbench_sessions: Arc<dyn WorkbenchSessionService>,
    /// Group proposal application service.
    pub group_proposals: Arc<dyn GroupProposalService>,
    /// Group message-history application service.
    pub group_message_history: Arc<dyn GroupMessageHistoryService>,
    /// System message service.
    pub system_message: Arc<dyn SystemMessageService>,
    /// Group-aware fusion application service.
    pub group_fusion: Arc<dyn GroupFusionService>,
    /// Session management application service.
    pub session_management: Arc<dyn SessionManagementService>,
    /// Channel(IM bridge) application service.
    pub channel: Arc<dyn ChannelService>,
    /// Secret access application service (mist in prod, in-memory/env in dev).
    pub secret: Arc<dyn SecretService>,
}

impl Services {
    /// Create a builder for constructing services.
    pub fn builder() -> ServicesBuilder {
        ServicesBuilder::new()
    }

    /// Fill in missing `bot_name` fields on a Group's participants from the registry cache.
    pub async fn backfill_bot_names(&self, session: &mut Group) {
        backfill_bot_names(self.registry.as_ref(), session).await;
    }
}

/// Builder for constructing services bundle.
#[derive(Default)]
pub struct ServicesBuilder {
    registry: Option<Arc<dyn BotRegistryCoreService>>,
    group: Option<Arc<dyn GroupCoreService>>,
    routing: Option<Arc<dyn RoutingCoreService>>,
    fusion: Option<Arc<dyn FusionCoreService>>,
    proposal: Option<Arc<dyn ProposalCoreService>>,
    friend: Option<Arc<dyn FriendCoreService>>,
    relation: Option<Arc<dyn RelationCoreService>>,
    message_flow: Option<Arc<dyn MessageFlowService>>,
    a2a_chat: Option<Arc<dyn A2aChatService>>,
    a2a_chat_runs: Option<Arc<dyn A2aChatRunService>>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
    collaboration_templates: Option<Arc<dyn CollaborationTemplateService>>,
    bot_delivery: Option<Arc<dyn BotDeliveryPort>>,
    bot_run_context: Option<Arc<dyn BotRunContextPort>>,
    frontend_delivery: Option<Arc<dyn FrontendDeliveryPort>>,
    actor_directory: Option<Arc<dyn ActorDirectoryService>>,
    friend_use_cases: Option<Arc<dyn FriendService>>,
    human_actors: Option<Arc<dyn HumanActorService>>,
    bot_onboarding: Option<Arc<dyn BotOnboardingService>>,
    bot_query: Option<Arc<dyn BotQueryService>>,
    bot_discovery: Option<Arc<dyn BotDiscoveryService>>,
    bot_management: Option<Arc<dyn BotManagementService>>,
    bot_runtime: Option<Arc<dyn BotRuntimeConnectionService>>,
    provider_core: Option<Arc<dyn ProviderCoreService>>,
    provider_bot_core: Option<Arc<dyn ProviderBotCoreService>>,
    provider_management: Option<Arc<dyn ProviderManagementService>>,
    provider_bot_events: Option<Arc<dyn ProviderBotEventService>>,
    organization_management: Option<Arc<dyn OrganizationManagementService>>,
    group_query: Option<Arc<dyn GroupQueryService>>,
    group_management: Option<Arc<dyn GroupManagementService>>,
    workbench_sessions: Option<Arc<dyn WorkbenchSessionService>>,
    group_proposals: Option<Arc<dyn GroupProposalService>>,
    group_message_history: Option<Arc<dyn GroupMessageHistoryService>>,
    system_message: Option<Arc<dyn SystemMessageService>>,
    group_fusion: Option<Arc<dyn GroupFusionService>>,
    session_management: Option<Arc<dyn SessionManagementService>>,
    channel: Option<Arc<dyn ChannelService>>,
    secret: Option<Arc<dyn SecretService>>,
}

impl ServicesBuilder {
    /// Create an empty builder.
    pub fn new() -> Self {
        Self::default()
    }

    /// Set the registry service.
    pub fn registry(mut self, service: Arc<dyn BotRegistryCoreService>) -> Self {
        self.registry = Some(service);
        self
    }

    /// Set the group service.
    pub fn group(mut self, service: Arc<dyn GroupCoreService>) -> Self {
        self.group = Some(service);
        self
    }

    /// Set the routing service.
    pub fn routing(mut self, service: Arc<dyn RoutingCoreService>) -> Self {
        self.routing = Some(service);
        self
    }

    /// Set the fusion service.
    pub fn fusion(mut self, service: Arc<dyn FusionCoreService>) -> Self {
        self.fusion = Some(service);
        self
    }

    /// Set the proposal service.
    pub fn proposal(mut self, service: Arc<dyn ProposalCoreService>) -> Self {
        self.proposal = Some(service);
        self
    }

    /// Set the friend service.
    pub fn friend(mut self, service: Arc<dyn FriendCoreService>) -> Self {
        self.friend = Some(service);
        self
    }

    /// Set the relation service.
    pub fn relation(mut self, service: Arc<dyn RelationCoreService>) -> Self {
        self.relation = Some(service);
        self
    }

    /// Set the message flow service.
    pub fn message_flow(mut self, service: Arc<dyn MessageFlowService>) -> Self {
        self.message_flow = Some(service);
        self
    }

    /// Set the A2A chat service.
    pub fn a2a_chat(mut self, service: Arc<dyn A2aChatService>) -> Self {
        self.a2a_chat = Some(service);
        self
    }

    /// Set the A2A chat run service.
    pub fn a2a_chat_runs(mut self, service: Arc<dyn A2aChatRunService>) -> Self {
        self.a2a_chat_runs = Some(service);
        self
    }

    /// Set the collaboration state-machine runtime service.
    pub fn collaboration_runtime(mut self, service: Arc<dyn CollaborationRuntimeService>) -> Self {
        self.collaboration_runtime = Some(service);
        self
    }

    /// Set the collaboration template application service.
    pub fn collaboration_templates(
        mut self,
        service: Arc<dyn CollaborationTemplateService>,
    ) -> Self {
        self.collaboration_templates = Some(service);
        self
    }

    /// Set the bot delivery port.
    pub fn bot_delivery(mut self, port: Arc<dyn BotDeliveryPort>) -> Self {
        self.bot_delivery = Some(port);
        self
    }

    /// Set the bot run context port.
    pub fn bot_run_context(mut self, port: Arc<dyn BotRunContextPort>) -> Self {
        self.bot_run_context = Some(port);
        self
    }

    /// Set the frontend delivery port.
    pub fn frontend_delivery(mut self, port: Arc<dyn FrontendDeliveryPort>) -> Self {
        self.frontend_delivery = Some(port);
        self
    }

    /// Set the actor directory application service.
    pub fn actor_directory(mut self, service: Arc<dyn ActorDirectoryService>) -> Self {
        self.actor_directory = Some(service);
        self
    }

    /// Set the friend application service.
    pub fn friend_use_cases(mut self, service: Arc<dyn FriendService>) -> Self {
        self.friend_use_cases = Some(service);
        self
    }

    /// Set the human actor application service.
    pub fn human_actors(mut self, service: Arc<dyn HumanActorService>) -> Self {
        self.human_actors = Some(service);
        self
    }

    /// Set the bot onboarding application service.
    pub fn bot_onboarding(mut self, service: Arc<dyn BotOnboardingService>) -> Self {
        self.bot_onboarding = Some(service);
        self
    }

    /// Set the bot query application service.
    pub fn bot_query(mut self, service: Arc<dyn BotQueryService>) -> Self {
        self.bot_query = Some(service);
        self
    }

    /// Set the bot discovery application service.
    pub fn bot_discovery(mut self, service: Arc<dyn BotDiscoveryService>) -> Self {
        self.bot_discovery = Some(service);
        self
    }

    /// Set the bot management application service.
    pub fn bot_management(mut self, service: Arc<dyn BotManagementService>) -> Self {
        self.bot_management = Some(service);
        self
    }

    /// Set the bot runtime WebSocket application service.
    pub fn bot_runtime(mut self, service: Arc<dyn BotRuntimeConnectionService>) -> Self {
        self.bot_runtime = Some(service);
        self
    }

    /// Set the provider registry core service.
    pub fn provider_core(mut self, service: Arc<dyn ProviderCoreService>) -> Self {
        self.provider_core = Some(service);
        self
    }

    /// Set the provider-bot binding core service.
    pub fn provider_bot_core(mut self, service: Arc<dyn ProviderBotCoreService>) -> Self {
        self.provider_bot_core = Some(service);
        self
    }

    /// Set the provider management application service.
    pub fn provider_management(mut self, service: Arc<dyn ProviderManagementService>) -> Self {
        self.provider_management = Some(service);
        self
    }

    /// Set the provider bot event application service.
    pub fn provider_bot_events(mut self, service: Arc<dyn ProviderBotEventService>) -> Self {
        self.provider_bot_events = Some(service);
        self
    }

    /// Set the organization management application service.
    pub fn organization_management(
        mut self,
        service: Arc<dyn OrganizationManagementService>,
    ) -> Self {
        self.organization_management = Some(service);
        self
    }

    /// Set the group query application service.
    pub fn group_query(mut self, service: Arc<dyn GroupQueryService>) -> Self {
        self.group_query = Some(service);
        self
    }

    /// Set the group management application service.
    pub fn group_management(mut self, service: Arc<dyn GroupManagementService>) -> Self {
        self.group_management = Some(service);
        self
    }

    /// Set the workbench WebSocket session application service.
    pub fn workbench_sessions(mut self, service: Arc<dyn WorkbenchSessionService>) -> Self {
        self.workbench_sessions = Some(service);
        self
    }

    /// Set the group proposal application service.
    pub fn group_proposals(mut self, service: Arc<dyn GroupProposalService>) -> Self {
        self.group_proposals = Some(service);
        self
    }

    /// Set the group message-history application service.
    pub fn group_message_history(mut self, service: Arc<dyn GroupMessageHistoryService>) -> Self {
        self.group_message_history = Some(service);
        self
    }

    /// Set the system message service.
    pub fn system_message(mut self, service: Arc<dyn SystemMessageService>) -> Self {
        self.system_message = Some(service);
        self
    }

    /// Set the group-aware fusion service.
    pub fn group_fusion(mut self, service: Arc<dyn GroupFusionService>) -> Self {
        self.group_fusion = Some(service);
        self
    }

    /// Set the session management application service.
    pub fn session_management(mut self, service: Arc<dyn SessionManagementService>) -> Self {
        self.session_management = Some(service);
        self
    }

    /// Set the channel application service.
    pub fn channel(mut self, service: Arc<dyn ChannelService>) -> Self {
        self.channel = Some(service);
        self
    }

    /// Set the secret access application service.
    pub fn secret(mut self, service: Arc<dyn SecretService>) -> Self {
        self.secret = Some(service);
        self
    }

    /// Build the services bundle, failing if any required service is unset.
    pub fn build(self) -> Result<Services, BuilderError> {
        Ok(Services {
            registry: required(self.registry, "registry")?,
            group: required(self.group, "group")?,
            routing: required(self.routing, "routing")?,
            fusion: required(self.fusion, "fusion")?,
            proposal: required(self.proposal, "proposal")?,
            friend: required(self.friend, "friend")?,
            relation: required(self.relation, "relation")?,
            message_flow: required(self.message_flow, "message_flow")?,
            a2a_chat: required(self.a2a_chat, "a2a_chat")?,
            a2a_chat_runs: required(self.a2a_chat_runs, "a2a_chat_runs")?,
            collaboration_runtime: required(self.collaboration_runtime, "collaboration_runtime")?,
            collaboration_templates: required(
                self.collaboration_templates,
                "collaboration_templates",
            )?,
            bot_delivery: required(self.bot_delivery, "bot_delivery")?,
            bot_run_context: required(self.bot_run_context, "bot_run_context")?,
            frontend_delivery: required(self.frontend_delivery, "frontend_delivery")?,
            actor_directory: required(self.actor_directory, "actor_directory")?,
            friend_use_cases: required(self.friend_use_cases, "friend_use_cases")?,
            human_actors: required(self.human_actors, "human_actors")?,
            bot_onboarding: required(self.bot_onboarding, "bot_onboarding")?,
            bot_query: required(self.bot_query, "bot_query")?,
            bot_discovery: required(self.bot_discovery, "bot_discovery")?,
            bot_management: required(self.bot_management, "bot_management")?,
            bot_runtime: required(self.bot_runtime, "bot_runtime")?,
            provider_core: required(self.provider_core, "provider_core")?,
            provider_bot_core: required(self.provider_bot_core, "provider_bot_core")?,
            provider_management: required(self.provider_management, "provider_management")?,
            provider_bot_events: required(self.provider_bot_events, "provider_bot_events")?,
            organization_management: required(
                self.organization_management,
                "organization_management",
            )?,
            group_query: required(self.group_query, "group_query")?,
            group_management: required(self.group_management, "group_management")?,
            workbench_sessions: required(self.workbench_sessions, "workbench_sessions")?,
            group_proposals: required(self.group_proposals, "group_proposals")?,
            group_message_history: required(self.group_message_history, "group_message_history")?,
            system_message: required(self.system_message, "system_message")?,
            group_fusion: required(self.group_fusion, "group_fusion")?,
            session_management: required(self.session_management, "session_management")?,
            channel: required(self.channel, "channel")?,
            secret: required(self.secret, "secret")?,
        })
    }

    /// Test-only build that fills any unset service with a Noop.
    #[cfg(any(test, feature = "test-support"))]
    pub fn build_for_test(self) -> Services {
        use bcs_test_support::{
            NoopA2aChatRunService, NoopA2aChatService, NoopActorDirectoryService,
            NoopBotDeliveryPort, NoopBotDiscoveryService, NoopBotManagementService,
            NoopBotOnboardingService, NoopBotQueryService, NoopBotRegistryCoreService,
            NoopBotRunContextPort, NoopBotRuntimeConnectionService, NoopChannelService,
            NoopFriendCoreService, NoopFriendService,
            NoopFrontendDeliveryPort, NoopFusionCoreService, NoopGroupCoreService,
            NoopGroupFusionService, NoopGroupManagementService, NoopGroupMessageHistoryService,
            NoopGroupProposalService, NoopGroupQueryService, NoopHumanActorService,
            NoopCollaborationRuntimeService, NoopCollaborationTemplateService,
            NoopMessageFlowService, NoopOrganizationManagementService,
            NoopProposalCoreService, NoopProviderBotCoreService, NoopProviderBotEventService,
            NoopProviderCoreService,
            NoopProviderManagementService, NoopRelationCoreService, NoopRoutingCoreService,
            NoopSecretService, NoopSessionManagementService,
            NoopSystemMessageService, NoopWorkbenchSessionService,
        };

        Services {
            registry: self
                .registry
                .unwrap_or_else(|| Arc::new(NoopBotRegistryCoreService)),
            group: self.group.unwrap_or_else(|| Arc::new(NoopGroupCoreService)),
            routing: self
                .routing
                .unwrap_or_else(|| Arc::new(NoopRoutingCoreService)),
            fusion: self
                .fusion
                .unwrap_or_else(|| Arc::new(NoopFusionCoreService)),
            proposal: self
                .proposal
                .unwrap_or_else(|| Arc::new(NoopProposalCoreService)),
            friend: self
                .friend
                .unwrap_or_else(|| Arc::new(NoopFriendCoreService)),
            relation: self
                .relation
                .unwrap_or_else(|| Arc::new(NoopRelationCoreService)),
            message_flow: self
                .message_flow
                .unwrap_or_else(|| Arc::new(NoopMessageFlowService)),
            a2a_chat: self
                .a2a_chat
                .unwrap_or_else(|| Arc::new(NoopA2aChatService)),
            a2a_chat_runs: self
                .a2a_chat_runs
                .unwrap_or_else(|| Arc::new(NoopA2aChatRunService)),
            collaboration_runtime: self
                .collaboration_runtime
                .unwrap_or_else(|| Arc::new(NoopCollaborationRuntimeService)),
            collaboration_templates: self
                .collaboration_templates
                .unwrap_or_else(|| Arc::new(NoopCollaborationTemplateService)),
            bot_delivery: self
                .bot_delivery
                .unwrap_or_else(|| Arc::new(NoopBotDeliveryPort)),
            bot_run_context: self
                .bot_run_context
                .unwrap_or_else(|| Arc::new(NoopBotRunContextPort)),
            frontend_delivery: self
                .frontend_delivery
                .unwrap_or_else(|| Arc::new(NoopFrontendDeliveryPort)),
            actor_directory: self
                .actor_directory
                .unwrap_or_else(|| Arc::new(NoopActorDirectoryService)),
            friend_use_cases: self
                .friend_use_cases
                .unwrap_or_else(|| Arc::new(NoopFriendService)),
            human_actors: self
                .human_actors
                .unwrap_or_else(|| Arc::new(NoopHumanActorService)),
            bot_onboarding: self
                .bot_onboarding
                .unwrap_or_else(|| Arc::new(NoopBotOnboardingService)),
            bot_query: self
                .bot_query
                .unwrap_or_else(|| Arc::new(NoopBotQueryService)),
            bot_discovery: self
                .bot_discovery
                .unwrap_or_else(|| Arc::new(NoopBotDiscoveryService)),
            bot_management: self
                .bot_management
                .unwrap_or_else(|| Arc::new(NoopBotManagementService)),
            bot_runtime: self
                .bot_runtime
                .unwrap_or_else(|| Arc::new(NoopBotRuntimeConnectionService)),
            provider_core: self
                .provider_core
                .unwrap_or_else(|| Arc::new(NoopProviderCoreService)),
            provider_bot_core: self
                .provider_bot_core
                .unwrap_or_else(|| Arc::new(NoopProviderBotCoreService)),
            provider_management: self
                .provider_management
                .unwrap_or_else(|| Arc::new(NoopProviderManagementService)),
            provider_bot_events: self
                .provider_bot_events
                .unwrap_or_else(|| Arc::new(NoopProviderBotEventService)),
            organization_management: self
                .organization_management
                .unwrap_or_else(|| Arc::new(NoopOrganizationManagementService)),
            group_query: self
                .group_query
                .unwrap_or_else(|| Arc::new(NoopGroupQueryService)),
            group_management: self
                .group_management
                .unwrap_or_else(|| Arc::new(NoopGroupManagementService)),
            workbench_sessions: self
                .workbench_sessions
                .unwrap_or_else(|| Arc::new(NoopWorkbenchSessionService)),
            group_proposals: self
                .group_proposals
                .unwrap_or_else(|| Arc::new(NoopGroupProposalService)),
            group_message_history: self
                .group_message_history
                .unwrap_or_else(|| Arc::new(NoopGroupMessageHistoryService)),
            system_message: self
                .system_message
                .unwrap_or_else(|| Arc::new(NoopSystemMessageService)),
            group_fusion: self
                .group_fusion
                .unwrap_or_else(|| Arc::new(NoopGroupFusionService)),
            session_management: self
                .session_management
                .unwrap_or_else(|| Arc::new(NoopSessionManagementService)),
            channel: self.channel.unwrap_or_else(|| Arc::new(NoopChannelService)),
            secret: self.secret.unwrap_or_else(|| Arc::new(NoopSecretService)),
        }
    }
}

fn required<T: ?Sized>(value: Option<Arc<T>>, name: &'static str) -> Result<Arc<T>, BuilderError> {
    value.ok_or(BuilderError::MissingService(name))
}
