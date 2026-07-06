//! Application service contract harnesses.

use bcs_service_api::{
    A2aChatRunService, A2aChatService, ActorDirectoryService, BotDiscoveryService,
    BotManagementService, BotOnboardingService, BotQueryService, BotRuntimeConnectionService,
    FriendService, GroupFusionService, GroupManagementService, GroupMessageHistoryService,
    GroupProposalService, GroupQueryService, HumanActorService, MessageFlowService,
    SystemMessageService, WorkbenchSessionService, WorkerProfileService,
};

pub async fn a2a_chat_service_contract_tests<T: A2aChatService + ?Sized>(_svc: &T) {}

pub async fn a2a_chat_run_service_contract_tests<T: A2aChatRunService + ?Sized>(_svc: &T) {}

pub async fn actor_directory_service_contract_tests<T: ActorDirectoryService + ?Sized>(_svc: &T) {}

pub async fn bot_discovery_service_contract_tests<T: BotDiscoveryService + ?Sized>(_svc: &T) {}

pub async fn bot_management_service_contract_tests<T: BotManagementService + ?Sized>(_svc: &T) {}

pub async fn bot_onboarding_service_contract_tests<T: BotOnboardingService + ?Sized>(_svc: &T) {}

pub async fn bot_query_service_contract_tests<T: BotQueryService + ?Sized>(_svc: &T) {}

pub async fn bot_runtime_connection_service_contract_tests<
    T: BotRuntimeConnectionService + ?Sized,
>(
    _svc: &T,
) {
}

pub async fn friend_service_contract_tests<T: FriendService + ?Sized>(_svc: &T) {}

pub async fn group_fusion_service_contract_tests<T: GroupFusionService + ?Sized>(_svc: &T) {}

pub async fn group_management_service_contract_tests<T: GroupManagementService + ?Sized>(_svc: &T) {
}

pub async fn group_message_history_service_contract_tests<
    T: GroupMessageHistoryService + ?Sized,
>(
    _svc: &T,
) {
}

pub async fn group_proposal_service_contract_tests<T: GroupProposalService + ?Sized>(_svc: &T) {}

pub async fn group_query_service_contract_tests<T: GroupQueryService + ?Sized>(_svc: &T) {}

pub async fn human_actor_service_contract_tests<T: HumanActorService + ?Sized>(_svc: &T) {}

pub async fn message_flow_service_contract_tests<T: MessageFlowService + ?Sized>(_svc: &T) {}

pub async fn worker_profile_service_contract_tests<T: WorkerProfileService + ?Sized>(_svc: &T) {}

pub async fn system_message_service_contract_tests<T: SystemMessageService + ?Sized>(_svc: &T) {}

pub async fn workbench_session_service_contract_tests<T: WorkbenchSessionService + ?Sized>(
    _svc: &T,
) {
}
