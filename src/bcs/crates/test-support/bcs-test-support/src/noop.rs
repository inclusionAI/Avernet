//! No-op service implementations for tests and explicit test containers.

use async_trait::async_trait;
use bcs_domain::{
    ChannelBinding, Organization, OrganizationMember, SystemGroupMessage, SystemMessageEvent,
    SystemMessageEventKind,
};
use bcs_service_api::core::{
    SystemMessageDispatchOutcome, SystemMessageDispatcherService, SystemMessageProducerService,
};
use bcs_service_api::*;

#[derive(Debug, Default)]
pub struct NoopWsLifecycleInstrumentationHook;

#[async_trait]
impl WsLifecycleInstrumentationHook for NoopWsLifecycleInstrumentationHook {
    async fn accepted(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn registered(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn error(&self, _peer: WsPeer, _endpoint: &'static str, _kind: WsErrorKind) {}

    async fn closed(
        &self,
        _peer: WsPeer,
        _endpoint: &'static str,
        _close_reason: WsCloseReason,
        _duration: std::time::Duration,
    ) {
    }
}

#[derive(Debug, Default)]
pub struct NoopGroupMetricsSnapshotPort;

#[async_trait]
impl GroupMetricsSnapshotPort for NoopGroupMetricsSnapshotPort {
    async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>> {
        Ok(Vec::new())
    }
}

#[derive(Debug, Default)]
pub struct NoopGroupSessionMetricsSnapshotPort;

#[async_trait]
impl GroupSessionMetricsSnapshotPort for NoopGroupSessionMetricsSnapshotPort {
    async fn group_session_counts(&self) -> ServiceResult<Vec<GroupSessionMetricCount>> {
        Ok(Vec::new())
    }
}

#[derive(Debug, Default)]
pub struct NoopBotMetricsSnapshotPort;

#[async_trait]
impl BotMetricsSnapshotPort for NoopBotMetricsSnapshotPort {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
        Ok(Vec::new())
    }
}

#[derive(Debug, Default)]
pub struct NoopDirectChatRunSnapshotPort;

#[async_trait]
impl DirectChatRunSnapshotPort for NoopDirectChatRunSnapshotPort {
    async fn direct_chat_run_counts(&self) -> ServiceResult<Vec<ChatRunMetricCount>> {
        Ok(Vec::new())
    }
}

#[derive(Debug, Default)]
pub struct NoopDirectChatRunLifecycleHook;

#[async_trait]
impl DirectChatRunLifecycleHook for NoopDirectChatRunLifecycleHook {
    async fn event(
        &self,
        _event: DirectChatRunEvent,
        _result: MetricsResult,
        _client_kind: DirectChatClientKind,
        _reason: DirectChatRunReason,
    ) {
    }
}

#[derive(Debug, Default)]
pub struct NoopDeliveryPolicyBlockInstrumentationHook;

#[async_trait]
impl DeliveryPolicyBlockInstrumentationHook for NoopDeliveryPolicyBlockInstrumentationHook {
    async fn blocked(&self, _context: DeliveryBlockContext) {}
}

#[derive(Debug, Default)]
pub struct NoopFriendCoreService;

#[async_trait]
impl FriendCoreService for NoopFriendCoreService {
    async fn list_friends(&self, _bot_id: &str) -> Vec<String> {
        Vec::new()
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> bool {
        false
    }

    async fn are_all_friends(&self, _bot_id: &str, others: &[String]) -> ServiceResult<()> {
        if others.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(others.to_vec()))
        }
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

#[derive(Debug, Default)]
pub struct NoopFriendRequestCoreService;

#[async_trait]
impl FriendRequestCoreService for NoopFriendRequestCoreService {
    async fn create_request(&self, _from_bot: &str, _to_bot: &str) -> ServiceResult<FriendRequest> {
        Err(ServiceError::InternalError(
            "Noop implementation".to_string(),
        ))
    }

    async fn accept_request(&self, _request_id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn reject_request(&self, _request_id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<FriendRequest> {
        Err(ServiceError::FriendRequestNotFound(request_id.to_string()))
    }

    async fn list_requests(
        &self,
        _bot_id: &str,
        _direction: FriendRequestDirection,
        _status_filter: Option<FriendRequestStatus>,
    ) -> Vec<FriendRequest> {
        Vec::new()
    }

    async fn cancel_pending_requests(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

#[derive(Debug, Default)]
pub struct NoopProposalCoreService;

#[async_trait]
impl ProposalCoreService for NoopProposalCoreService {
    async fn store(&self, proposal: GroupChatProposal) -> String {
        proposal.token
    }

    async fn get(&self, _token: &str) -> Option<GroupChatProposal> {
        None
    }

    async fn take(&self, _token: &str) -> Option<GroupChatProposal> {
        None
    }

    async fn cleanup_expired(&self) -> usize {
        0
    }
}

#[derive(Debug, Default)]
pub struct NoopBotRegistryCoreService;

#[async_trait]
impl BotRegistryCoreService for NoopBotRegistryCoreService {
    async fn register(&self, _bot_id: String, _capabilities: BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, _bot_id: &str) -> Option<RegisteredBot> {
        None
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_actor_status(&self, _bot_id: &str, _status: ActorStatus) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        _staff_no: &str,
        _nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        Ok(EnsureHumanResult { created: false })
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }

    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}

#[derive(Debug, Default)]
pub struct NoopProviderCoreService;

#[async_trait]
impl ProviderCoreService for NoopProviderCoreService {
    async fn register_provider(
        &self,
        _name: String,
        _webhook_url: String,
        _auth_mode: ProviderAuthMode,
        _created_by: String,
        _protocol_version: Option<String>,
        _coordination: Option<ProviderCoordinationConfig>,
    ) -> ServiceResult<RegisteredProvider> {
        Err(service_not_configured("provider core service"))
    }

    async fn authenticate_provider_admin(&self, _token: &str) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider core service"))
    }

    async fn get_downlink_credential(&self, _provider_id: &str) -> ServiceResult<ProviderCredential> {
        Err(service_not_configured("provider core service"))
    }

    async fn get_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider core service"))
    }

    async fn update_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _name: Option<String>,
        _webhook_url: Option<String>,
        _protocol_version: Option<String>,
        _coordination: Option<ProviderCoordinationConfig>,
        _organization_management: Option<ProviderOrganizationManagementConfig>,
    ) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider core service"))
    }

    async fn set_provider_disabled(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _disabled: bool,
    ) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider core service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopProviderBotCoreService;

#[async_trait]
impl ProviderBotCoreService for NoopProviderBotCoreService {
    async fn register_provider_bot_with_bot_uuid(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _params: RegisterProviderBotParams,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)> {
        Err(service_not_configured("provider bot core service"))
    }

    async fn list_provider_bots(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        Ok(Vec::new())
    }

    async fn authenticate_static_bearer_event(
        &self,
        _provider_id: &str,
        _bot_runtime_token: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        Err(service_not_configured("provider bot core service"))
    }

    async fn authenticate_agentpass_event(
        &self,
        _provider_id: &str,
        _agent_code: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        Err(service_not_configured("provider bot core service"))
    }

    async fn authenticate_provider_admin_event(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _provider_bot_ref: &str,
    ) -> ServiceResult<RuntimeBotIdentity> {
        Err(service_not_configured("provider bot core service"))
    }

    async fn get_provider_coordination_config(
        &self,
        _provider_id: &str,
    ) -> ServiceResult<ProviderCoordinationConfig> {
        Err(service_not_configured("provider bot core service"))
    }

    async fn set_provider_bot_disabled(
        &self,
        _provider_id: &str,
        _bot_uuid: &str,
        _provider_admin_token: &str,
        _disabled: bool,
    ) -> ServiceResult<ProviderBotBinding> {
        Err(service_not_configured("provider bot core service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopProviderManagementService;

#[async_trait]
impl ProviderManagementService for NoopProviderManagementService {
    async fn register_provider(
        &self,
        _command: RegisterProviderCommand,
    ) -> ServiceResult<RegisterProviderOutcome> {
        Err(service_not_configured("provider management service"))
    }

    async fn get_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider management service"))
    }

    async fn update_provider(
        &self,
        _command: UpdateProviderCommand,
    ) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider management service"))
    }

    async fn register_provider_bot(
        &self,
        _command: RegisterProviderBotCommand,
    ) -> ServiceResult<RegisterProviderBotOutcome> {
        Err(service_not_configured("provider management service"))
    }

    async fn list_provider_bots(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>> {
        Err(service_not_configured("provider management service"))
    }

    async fn delete_provider_bot(
        &self,
        _command: DeleteProviderBotCommand,
    ) -> ServiceResult<DeleteProviderBotOutcome> {
        Err(service_not_configured("provider management service"))
    }

    async fn set_provider_disabled(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _disabled: bool,
    ) -> ServiceResult<ProviderRecord> {
        Err(service_not_configured("provider management service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopProviderBotEventService;

#[async_trait]
impl ProviderBotEventService for NoopProviderBotEventService {
    async fn submit_event(
        &self,
        _command: ProviderBotEventCommand,
    ) -> Result<ProviderBotEventOutcome, ProviderBotEventError> {
        Err(ProviderBotEventError::Internal(
            service_not_configured("provider bot event service").to_string(),
        ))
    }

    async fn submit_coordination(
        &self,
        _command: ProviderBotCoordinationCommand,
    ) -> Result<ProviderBotCoordinationOutcome, ProviderBotEventError> {
        Err(ProviderBotEventError::Internal(
            service_not_configured("provider bot event service").to_string(),
        ))
    }
}

#[derive(Debug, Default)]
pub struct NoopOrganizationCoreService;

#[async_trait]
impl OrganizationCoreService for NoopOrganizationCoreService {
    async fn create(
        &self,
        _managing_provider_id: &str,
        _code: &str,
        _name: &str,
        _description: Option<&str>,
    ) -> ServiceResult<Organization> {
        Err(service_not_configured("organization service"))
    }

    async fn get_for_manager(
        &self,
        _managing_provider_id: &str,
        _code: &str,
    ) -> ServiceResult<Organization> {
        Err(service_not_configured("organization service"))
    }

    async fn list_for_manager(
        &self,
        _managing_provider_id: &str,
        _include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>> {
        Err(service_not_configured("organization service"))
    }

    async fn update_for_manager(
        &self,
        _managing_provider_id: &str,
        _code: &str,
        _name: Option<&str>,
        _description: Option<Option<&str>>,
        _disabled: Option<bool>,
    ) -> ServiceResult<Organization> {
        Err(service_not_configured("organization service"))
    }

    async fn put_member(
        &self,
        _managing_provider_id: &str,
        _organization_code: &str,
        _bot_uuid: &str,
        _role: Option<&str>,
    ) -> ServiceResult<OrganizationMember> {
        Err(service_not_configured("organization service"))
    }

    async fn delete_member(
        &self,
        _managing_provider_id: &str,
        _organization_code: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<()> {
        Err(service_not_configured("organization service"))
    }

    async fn get_member_for_manager(
        &self,
        _managing_provider_id: &str,
        _organization_code: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        Err(service_not_configured("organization service"))
    }

    async fn list_members_for_manager(
        &self,
        _managing_provider_id: &str,
        _organization_code: &str,
        _include_disabled: bool,
        _role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        Err(service_not_configured("organization service"))
    }

    async fn candidate_bots(
        &self,
        _managing_provider_id: &str,
        _query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        Err(service_not_configured("organization service"))
    }

    async fn require_effective_member(
        &self,
        _organization_code: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember> {
        Err(service_not_configured("organization service"))
    }

    async fn list_effective_members(
        &self,
        _organization_code: &str,
        _role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        Err(service_not_configured("organization service"))
    }

    async fn authorize_pair(
        &self,
        _organization_code: &str,
        _sender_bot_uuid: &str,
        _target_bot_uuid: &str,
    ) -> ServiceResult<AuthorizedOrganizationPair> {
        Err(service_not_configured("organization service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopOrganizationManagementService;

#[async_trait]
impl OrganizationManagementService for NoopOrganizationManagementService {
    async fn create(&self, _command: CreateOrganizationCommand) -> ServiceResult<Organization> {
        Err(service_not_configured("organization service"))
    }

    async fn get(&self, _auth: OrganizationAuth, _code: &str) -> ServiceResult<Organization> {
        Err(service_not_configured("organization service"))
    }

    async fn list(
        &self,
        _auth: OrganizationAuth,
        _include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>> {
        Err(service_not_configured("organization service"))
    }

    async fn update(&self, _command: UpdateOrganizationCommand) -> ServiceResult<Organization> {
        Err(service_not_configured("organization service"))
    }

    async fn put_member(
        &self,
        _command: PutOrganizationMemberCommand,
    ) -> ServiceResult<OrganizationMember> {
        Err(service_not_configured("organization service"))
    }

    async fn delete_member(
        &self,
        _auth: OrganizationAuth,
        _organization_code: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<()> {
        Err(service_not_configured("organization service"))
    }

    async fn get_member(
        &self,
        _auth: OrganizationAuth,
        _organization_code: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        Err(service_not_configured("organization service"))
    }

    async fn list_members(
        &self,
        _auth: OrganizationAuth,
        _organization_code: &str,
        _include_disabled: bool,
        _role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        Err(service_not_configured("organization service"))
    }

    async fn candidate_bots(
        &self,
        _auth: OrganizationAuth,
        _query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        Err(service_not_configured("organization service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopRelationCoreService;

#[async_trait]
impl RelationCoreService for NoopRelationCoreService {
    async fn upsert_edge(&self, _edge: RelationEdge) -> ServiceResult<()> {
        Ok(())
    }

    async fn delete_edge(&self, _from_id: &str, _to_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_edge(
        &self,
        _from_id: &str,
        _to_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(None)
    }

    async fn ensure_owner_edges(
        &self,
        _human_id: &str,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        _human_id: &str,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        Err(ServiceError::InternalError(
            "NoopRelationCoreService::ensure_owner_edges_counted called; \
             a real RelationCoreService implementation must be configured"
                .to_string(),
        ))
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(
        &self,
        _caller: &str,
        _target: &str,
        _env: &str,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

#[derive(Debug, Default)]
pub struct NoopFusionCoreService;

#[async_trait]
impl FusionCoreService for NoopFusionCoreService {
    async fn fuse(&self, _request: &ContextFusionRequest) -> ServiceResult<ContextFusionResponse> {
        Ok(ContextFusionResponse::default())
    }

    fn load_bot_context(&self, bot_id: &str) -> ServiceResult<ContextBotSummary> {
        Ok(ContextBotSummary {
            bot_uuid: bot_id.to_string(),
            name: None,
            emoji: None,
            identity: None,
            soul: None,
            rules: None,
            memory: None,
        })
    }

    fn load_bot_contexts(&self, _bot_ids: &[String]) -> Vec<ContextBotSummary> {
        Vec::new()
    }
}

#[derive(Debug, Default)]
pub struct NoopRoutingCoreService;

#[async_trait]
impl RoutingCoreService for NoopRoutingCoreService {
    async fn route(
        &self,
        _group: &Group,
        message: &str,
        _sender_bot_id: Option<&str>,
    ) -> RoutingDecision {
        RoutingDecision {
            targets: vec![],
            mentions: vec![],
            cleaned_message: message.to_string(),
            hidden_mentions: vec![],
        }
    }

    async fn send_to_bot(
        &self,
        _target: &RoutingTarget,
        _message: &str,
        _from: Option<&str>,
        _group_id: Option<&str>,
    ) -> BotSendResult {
        BotSendResult {
            bot_uuid: String::new(),
            content: String::new(),
            success: false,
            error: Some("Noop implementation".to_string()),
        }
    }

    async fn route_and_send(
        &self,
        _group: &Group,
        _message: &str,
        _from: Option<&str>,
    ) -> RouteAndSendResult {
        RouteAndSendResult {
            results: vec![],
            mentions: vec![],
        }
    }

    async fn route_structured(
        &self,
        _group: &Group,
        _routing: &ChatEventRouting,
        _sender_bot_id: &str,
        _registry: &dyn BotRegistryCoreService,
    ) -> Result<RoutingDecision, StructuredRoutingError> {
        Err(StructuredRoutingError::NoTargetMatched)
    }
}

#[derive(Debug, Default)]
pub struct NoopGroupCoreService;

#[async_trait]
impl GroupCoreService for NoopGroupCoreService {
    async fn upsert(&self, _group: Group) -> ServiceResult<()> {
        Ok(())
    }

    async fn get(&self, _id: &str) -> Option<Group> {
        None
    }

    async fn add_message(&self, id: &str, _message: GroupMessage) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn add_participant(&self, id: &str, _participant: Participant) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn remove_participant(&self, group_id: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(group_id.to_string()))
    }

    async fn update_participant_mode(
        &self,
        id: &str,
        _actor_id: &str,
        _mode: ParticipantMode,
    ) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_workspace(&self, id: &str, _workspace: Workspace) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_label(&self, id: &str, _label: Option<String>) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_status(&self, id: &str, _status: GroupStatus) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_service_spec(
        &self,
        id: &str,
        _service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn terminate(&self, id: &str, _caller_bot_id: &str) -> ServiceResult<Group> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn delete(&self, _id: &str) -> ServiceResult<Option<Group>> {
        Ok(None)
    }

    async fn list(&self) -> Vec<Group> {
        Vec::new()
    }

    async fn list_paginated(&self, _offset: u64, _limit: u64) -> Vec<Group> {
        Vec::new()
    }

    async fn find_by_participant(&self, _bot_uuid: &str) -> Vec<Group> {
        Vec::new()
    }

    async fn count(&self) -> u64 {
        0
    }

    async fn count_by_participant(&self, _bot_uuid: &str) -> u64 {
        0
    }

    async fn find_by_participant_paginated(
        &self,
        _bot_uuid: &str,
        _offset: u64,
        _limit: u64,
    ) -> Vec<Group> {
        Vec::new()
    }

    async fn message_count(&self, _id: &str) -> ServiceResult<usize> {
        Ok(0)
    }

    async fn increment_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn reset_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        _id: &str,
        _actor_a: DmActorSpec,
        _actor_b: DmActorSpec,
        _legacy_driver_bot: &str,
        _originator_actor_id: &str,
        _label: Option<String>,
        _context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        Err(service_not_configured("group core service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopGroupDispatchContextPort;

#[async_trait]
impl GroupDispatchContextPort for NoopGroupDispatchContextPort {
    async fn participants(&self, _group_id: &str) -> Option<Vec<Participant>> {
        None
    }
}

#[derive(Debug, Default)]
pub struct NoopSessionCallbackDispatchPort;

#[async_trait]
impl SessionCallbackDispatchPort for NoopSessionCallbackDispatchPort {
    async fn maybe_dispatch(
        &self,
        _session: Session,
        _session_management: std::sync::Arc<dyn SessionManagementService>,
    ) {
    }
}

pub struct NoopWorkbenchSessionService;

#[async_trait]
impl WorkbenchSessionService for NoopWorkbenchSessionService {
    async fn connect(
        &self,
        _command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        Err(WorkbenchUseCaseError::from_service_error(
            service_not_configured("workbench session service"),
        ))
    }

    async fn authorize_chat_send(
        &self,
        _command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        Err(WorkbenchUseCaseError::from_service_error(
            service_not_configured("workbench session service"),
        ))
    }
}

pub struct NoopBotRuntimeConnectionService;

#[async_trait]
impl BotRuntimeConnectionService for NoopBotRuntimeConnectionService {
    async fn connect_streaming(
        &self,
        _command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        Err(service_not_configured("bot runtime connection service").into())
    }

    async fn update_runtime_status(
        &self,
        _command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        Err(service_not_configured("bot runtime connection service").into())
    }

    async fn disconnect_streaming(
        &self,
        _command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        Err(service_not_configured("bot runtime connection service").into())
    }

    async fn resolve_delivery_target(
        &self,
        bot_id: &str,
    ) -> ServiceResult<BotDeliveryTarget> {
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}

pub struct NoopGroupMessageHistoryService;

#[async_trait]
impl GroupMessageHistoryService for NoopGroupMessageHistoryService {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        Err(service_not_configured("group message history service").into())
    }

    async fn get_session_history(
        &self,
        _cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        Err(service_not_configured("group message history service").into())
    }
}

#[derive(Debug, Default)]
pub struct NoopCollaborationRuntimeService;

#[async_trait]
impl CollaborationRuntimeService for NoopCollaborationRuntimeService {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "Noop implementation".to_string(),
        ))
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: false,
            view: None,
        })
    }

    async fn upsert_definition(
        &self,
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "Noop implementation".to_string(),
        ))
    }
}

#[derive(Debug, Default)]
pub struct NoopCollaborationTemplateService;

#[async_trait]
impl CollaborationTemplateService for NoopCollaborationTemplateService {
    async fn list_templates(
        &self,
        _query: ListCollaborationTemplatesQuery,
    ) -> Result<CollaborationTemplateListResponse, CollaborationTemplateError> {
        Ok(CollaborationTemplateListResponse {
            templates: Vec::new(),
            tag_labels: std::collections::BTreeMap::new(),
            default_language: "zh-CN".to_string(),
            supported_languages: Vec::new(),
        })
    }

    async fn get_template(
        &self,
        query: GetCollaborationTemplateQuery,
    ) -> Result<CollaborationTemplateDetail, CollaborationTemplateError> {
        Err(CollaborationTemplateError::NotFound(query.template_id))
    }
}

#[derive(Debug, Default)]
pub struct NoopMessageFlowService;

#[async_trait]
impl MessageFlowService for NoopMessageFlowService {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_group_chat(&self, _cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_persistent_group_send(
        &self,
        _cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        Err(service_not_configured("message flow service"))
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        Err(service_not_configured("message flow service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopGroupFusionService;

#[async_trait]
impl GroupFusionService for NoopGroupFusionService {
    async fn fuse_for_group(&self, _cmd: GroupFusionCommand) -> ServiceResult<FusionResponse> {
        Err(service_not_configured("group fusion service"))
    }
}

pub struct NoopBotManagementService;

#[async_trait]
impl BotManagementService for NoopBotManagementService {
    async fn connect_bot(
        &self,
        _command: BotConnectCommand,
    ) -> Result<BotConnectResult, BotUseCaseError> {
        Err(service_not_configured("bot management service").into())
    }

    async fn update_status(
        &self,
        _command: BotStatusUpdateCommand,
    ) -> Result<BotStatusUpdateResult, BotUseCaseError> {
        Err(service_not_configured("bot management service").into())
    }

    async fn set_visibility(
        &self,
        _command: BotVisibilityCommand,
    ) -> Result<BotVisibilityResult, BotUseCaseError> {
        Err(service_not_configured("bot management service").into())
    }

    async fn leave_bot(
        &self,
        _command: BotLeaveCommand,
    ) -> Result<BotLeaveResult, BotUseCaseError> {
        Err(service_not_configured("bot management service").into())
    }

    async fn switch_delivery_to_provider(
        &self,
        _command: SwitchDeliveryToProviderCommand,
    ) -> Result<SwitchDeliveryToProviderResult, BotUseCaseError> {
        Err(service_not_configured("bot management service").into())
    }
}

pub struct NoopBotQueryService;

#[async_trait]
impl BotQueryService for NoopBotQueryService {
    async fn list_bots(&self, _command: BotListCommand) -> Result<BotListResult, BotUseCaseError> {
        Err(service_not_configured("bot query service").into())
    }

    async fn get_bot(
        &self,
        _command: BotDetailCommand,
    ) -> Result<BotDetailResult, BotUseCaseError> {
        Err(service_not_configured("bot query service").into())
    }

    async fn get_visibility(
        &self,
        _command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, BotUseCaseError> {
        Err(service_not_configured("bot query service").into())
    }

    async fn list_bots_paged(
        &self,
        _command: BotPagedListCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        Err(service_not_configured("bot query service").into())
    }

    async fn list_my_bots(
        &self,
        _command: MyBotsCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        Err(service_not_configured("bot query service").into())
    }

    async fn query_bots_by_ids(
        &self,
        _command: BotQueryByIdsCommand,
    ) -> Result<BotQueryByIdsResult, BotUseCaseError> {
        Err(service_not_configured("bot query service").into())
    }
}

pub struct NoopBotDiscoveryService;

#[async_trait]
impl BotDiscoveryService for NoopBotDiscoveryService {
    async fn discover_bots(
        &self,
        _command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, BotUseCaseError> {
        Err(service_not_configured("bot discovery service").into())
    }
}

pub struct NoopFriendService;

#[async_trait]
impl FriendService for NoopFriendService {
    async fn create_friend_request(
        &self,
        command: CreateFriendRequestCommand,
    ) -> Result<FriendRequest, FriendUseCaseError> {
        Ok(FriendRequest {
            id: String::new(),
            from_bot: command.caller_actor_id,
            to_bot: command.to_bot,
            status: FriendRequestStatus::Accepted,
            created_at: 0,
            updated_at: 0,
        })
    }

    async fn list_friend_requests(
        &self,
        _command: ListFriendRequestsCommand,
    ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
        Ok(Vec::new())
    }

    async fn accept_friend_request(
        &self,
        _command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        Err(FriendUseCaseError::service(ServiceError::InternalError(
            "Noop implementation".to_string(),
        )))
    }

    async fn reject_friend_request(
        &self,
        _command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        Err(FriendUseCaseError::service(ServiceError::InternalError(
            "Noop implementation".to_string(),
        )))
    }

    async fn friend_request_receiver(
        &self,
        request_id: &str,
    ) -> Result<String, FriendUseCaseError> {
        Err(FriendUseCaseError::service(
            ServiceError::FriendRequestNotFound(request_id.to_string()),
        ))
    }

    async fn list_friends(
        &self,
        _command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
        Ok(Vec::new())
    }
}

pub struct NoopHumanActorService;

#[async_trait]
impl HumanActorService for NoopHumanActorService {
    async fn repair_human_actor_info(
        &self,
        command: CurrentHumanActorCommand,
    ) -> RepairHumanActorInfoResult {
        RepairHumanActorInfoResult {
            ok: true,
            user_id: command.staff_no.clone(),
            staff_no: command.staff_no,
            nick_name: command.nick_name,
            human_id: None,
            previous_name: None,
            current_name: None,
            name_repaired: false,
            skipped_reason: Some("human_actor_service_not_configured"),
            error: None,
        }
    }

    async fn ensure_current_human_actor(
        &self,
        _command: CurrentHumanActorCommand,
    ) -> Result<EnsureCurrentHumanActorResult, EnsureCurrentHumanActorError> {
        Err(EnsureCurrentHumanActorError::LoginRequired)
    }
}

pub struct NoopWorkerProfileService;

#[async_trait]
impl WorkerProfileService for NoopWorkerProfileService {
    async fn recommend_workers(
        &self,
        _command: WorkerRecommendCommand,
    ) -> ServiceResult<WorkerRecommendResult> {
        Ok(WorkerRecommendResult {
            recommendations: Vec::new(),
            raw_response: serde_json::Value::Null,
        })
    }

    async fn batch_query_worker_profiles(
        &self,
        _worker_ids: &[String],
    ) -> ServiceResult<Vec<WorkerProfile>> {
        Ok(Vec::new())
    }
}

pub struct NoopActorDirectoryService;

#[async_trait]
impl ActorDirectoryService for NoopActorDirectoryService {
    async fn list_actors(&self, _command: ActorListCommand) -> ActorListResult {
        ActorListResult {
            bots: Vec::new(),
            total: 0,
        }
    }

    async fn search_actors(&self, _command: ActorSearchCommand) -> ActorSearchResult {
        ActorSearchResult {
            bots: Vec::new(),
            context: ActorSearchContext {
                recommend_response: None,
            },
        }
    }

    async fn update_actor_status_for_caller(
        &self,
        command: ActorStatusUpdateCommand,
    ) -> ServiceResult<ActorStatusUpdateResult> {
        Ok(ActorStatusUpdateResult {
            actor_id: command.actor_id,
            status: command.status,
        })
    }
}

pub struct NoopBotOnboardingService;

#[async_trait]
impl BotOnboardingService for NoopBotOnboardingService {
    async fn onboard_bot(&self, _command: BotOnboardCommand) -> ServiceResult<BotOnboardResult> {
        Err(ServiceError::InternalError(
            "Noop implementation".to_string(),
        ))
    }

    async fn admin_onboard_bot(
        &self,
        _command: AdminBotOnboardCommand,
    ) -> ServiceResult<BotOnboardResult> {
        Err(ServiceError::InternalError(
            "Noop implementation".to_string(),
        ))
    }
}

#[derive(Debug, Default)]
pub struct NoopA2aChatService;

#[async_trait]
impl A2aChatService for NoopA2aChatService {
    async fn chat(&self, _cmd: A2aChatCommand) -> ServiceResult<A2aChatOutcome> {
        Err(service_not_configured("a2a chat service"))
    }

    async fn get_run(&self, _caller: CallerContext, _run_id: &str) -> ServiceResult<A2aRunStatus> {
        Err(service_not_configured("a2a chat service"))
    }

    async fn wait_run(
        &self,
        _caller: CallerContext,
        _run_id: &str,
        _since_version: u64,
        _wait_ms: u64,
    ) -> ServiceResult<A2aRunStatus> {
        Err(service_not_configured("a2a chat service"))
    }

    async fn record_run_event(&self, _run_id: &str, _event_json: &str) -> ServiceResult<bool> {
        Err(service_not_configured("a2a chat service"))
    }

    async fn fail_run_if_open(&self, _run_id: &str, _error: &str) -> ServiceResult<bool> {
        Err(service_not_configured("a2a chat service"))
    }

    async fn cancel_run(
        &self,
        _caller: CallerContext,
        _run_id: &str,
    ) -> ServiceResult<A2aRunStatus> {
        Err(service_not_configured("a2a chat service"))
    }

    async fn cleanup_expired(
        &self,
        _now_ms: u64,
        _retention_ms: u64,
    ) -> ServiceResult<(Vec<String>, Vec<String>)> {
        Err(service_not_configured("a2a chat service"))
    }
}

#[derive(Debug, Default)]
pub struct NoopA2aChatRunService;

#[async_trait]
impl A2aChatRunService for NoopA2aChatRunService {
    async fn run_blocking_chat(
        &self,
        _cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        Err(service_not_configured("a2a chat run service"))
    }

    async fn start_async_chat(
        &self,
        _cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        Err(service_not_configured("a2a chat run service"))
    }

    async fn get_run(&self, _cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        Err(service_not_configured("a2a chat run service"))
    }

    async fn cancel_run(&self, _cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        Err(service_not_configured("a2a chat run service"))
    }
}

pub struct NoopGroupProposalService;

#[async_trait]
impl GroupProposalService for NoopGroupProposalService {
    async fn create_proposal(
        &self,
        _cmd: GroupProposalCreateCommand,
    ) -> Result<GroupProposalCreateResult, GroupUseCaseError> {
        Err(service_not_configured("group proposal service").into())
    }

    async fn confirm_proposal(
        &self,
        _cmd: GroupProposalConfirmCommand,
    ) -> Result<GroupProposalConfirmResult, GroupUseCaseError> {
        Err(service_not_configured("group proposal service").into())
    }

    async fn preview_proposal(
        &self,
        _cmd: GroupProposalPreviewCommand,
    ) -> Result<GroupProposalPreviewResult, GroupUseCaseError> {
        Err(service_not_configured("group proposal service").into())
    }
}

pub struct NoopGroupQueryService;

#[async_trait]
impl GroupQueryService for NoopGroupQueryService {
    async fn list_groups(
        &self,
        _cmd: GroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        Err(service_not_configured("group query service").into())
    }

    async fn get_group(
        &self,
        _cmd: GroupDetailCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Err(service_not_configured("group query service").into())
    }

    async fn list_bot_groups(
        &self,
        _cmd: BotGroupListCommand,
    ) -> Result<GroupListResult, GroupUseCaseError> {
        Err(service_not_configured("group query service").into())
    }

    async fn get_workspace(
        &self,
        _cmd: GroupWorkspaceQueryCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        Err(service_not_configured("group query service").into())
    }
}

pub struct NoopGroupManagementService;

#[async_trait]
impl GroupManagementService for NoopGroupManagementService {
    async fn create_group(
        &self,
        _cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn create_dm(&self, _cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn update_status(
        &self,
        _cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn add_member(
        &self,
        _cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn remove_member(
        &self,
        _cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn delete_group(
        &self,
        _cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn terminate_group(
        &self,
        _cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn update_label(
        &self,
        _cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn update_visibility(
        &self,
        _cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn update_workspace(
        &self,
        _cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn update_routing_policy(
        &self,
        _cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn update_participant_mode(
        &self,
        _cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }

    async fn patch_group_settings(
        &self,
        _cmd: GroupPatchSettingsCommand,
    ) -> Result<GroupPatchSettingsResult, GroupUseCaseError> {
        Err(service_not_configured("group management service").into())
    }
}

#[derive(Debug, Default)]
pub struct NoopBotDeliveryPort;

#[async_trait]
impl BotDeliveryPort for NoopBotDeliveryPort {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        false
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: false,
            error: None,
        })
    }
}

#[derive(Debug, Default)]
pub struct NoopBotConnectionControlPort;

#[async_trait]
impl BotConnectionControlPort for NoopBotConnectionControlPort {
    async fn kick(&self, _bot_id: &str, _reason: KickReason) -> bool {
        false
    }
}

#[derive(Debug, Default)]
pub struct NoopBotRunContextPort;

#[async_trait]
impl BotRunContextPort for NoopBotRunContextPort {
    async fn put_context(&self, _context: BotRunContext) {}

    async fn get_context(&self, _run_id: &str) -> Option<BotRunContext> {
        None
    }

    async fn try_begin_terminal(&self, _run_id: &str) -> bool {
        false
    }

    async fn mark_terminal(&self, _run_id: &str) -> bool {
        false
    }

    async fn release_terminal(&self, _run_id: &str) {}
}

#[derive(Debug, Default)]
pub struct NoopFrontendDeliveryPort;

#[async_trait]
impl FrontendDeliveryPort for NoopFrontendDeliveryPort {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered: 0,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

#[derive(Debug, Default)]
pub struct NoopChannelDeliveryPort;

#[async_trait]
impl ChannelDeliveryPort for NoopChannelDeliveryPort {
    async fn is_available(&self, _binding: &ChannelBindingRef) -> bool {
        false
    }

    async fn deliver_event(
        &self,
        _event: ChannelOutboundEvent,
    ) -> ServiceResult<ChannelDeliveryResult> {
        Ok(ChannelDeliveryResult {
            delivered: false,
            error: None,
        })
    }
}

#[derive(Debug, Default)]
pub struct NoopChannelService;

#[async_trait]
impl ChannelService for NoopChannelService {
    async fn handle_inbound(
        &self,
        _msg: InboundMessage,
    ) -> Result<(), ChannelInboundError> {
        Ok(())
    }

    async fn try_outbound(
        &self,
        _msg: OutboundMessage,
    ) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn create_binding(
        &self,
        _cmd: CreateBindingCommand,
    ) -> Result<ChannelBinding, ChannelUseCaseError> {
        Err(ChannelUseCaseError::InvalidParams("noop".to_string()))
    }

    async fn list_bindings(&self) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        Ok(Vec::new())
    }

    async fn set_binding_status(
        &self,
        _id: &str,
        _active: bool,
    ) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn update_binding_config(
        &self,
        _id: &str,
        _config: serde_json::Value,
    ) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }

    async fn delete_binding(&self, _id: &str) -> Result<(), ChannelUseCaseError> {
        Ok(())
    }
}

#[derive(Debug, Default)]
pub struct NoopGroupHistoryBotRequestPort;

#[async_trait]
impl GroupHistoryBotRequestPort for NoopGroupHistoryBotRequestPort {
    async fn send_history_request(
        &self,
        _target: BotDeliveryTarget,
        _method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        Err("group_history_bot_request_not_configured".to_string())
    }
}

#[derive(Debug, Default)]
pub struct NoopSystemMessageDispatcher;

#[async_trait]
impl SystemMessageDispatcherService for NoopSystemMessageDispatcher {
    async fn dispatch(
        &self,
        _event: SystemMessageEvent,
        _group: &Group,
        _session_id: &str,
        _participants: &[Participant],
    ) -> ServiceResult<SystemMessageDispatchOutcome> {
        Ok(SystemMessageDispatchOutcome {
            total_recipients: 0,
            successful_deliveries: 0,
            failed_deliveries: 0,
            recipient_results: vec![],
        })
    }
}

#[derive(Debug, Default)]
pub struct NoopSystemMessageProducer;

#[async_trait]
impl SystemMessageProducerService for NoopSystemMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::GenericNotification
    }

    async fn produce(
        &self,
        _event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        _participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        vec![]
    }
}

#[derive(Debug, Default)]
pub struct NoopSystemMessageService;

#[async_trait]
impl SystemMessageService for NoopSystemMessageService {
    async fn notify(&self, _group_id: &str, _event: SystemMessageEvent, _session_id: &str, _session_participants: &[Participant]) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn service_not_configured(name: &str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!("{name} is not configured"),
        request_id: None,
    }
}

pub use bcs_session::NoopSessionManagementService;

#[derive(Debug, Default, Clone, Copy)]
pub struct NoopSecretService;

#[async_trait]
impl SecretService for NoopSecretService {
    async fn get_secret(
        &self,
        name: &str,
    ) -> Result<SecretView, SecretServiceError> {
        Err(SecretServiceError::Unavailable(format!(
            "noop secret service ({name})"
        )))
    }
}
