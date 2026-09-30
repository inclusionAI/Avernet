use std::sync::Arc;

use bcs_proposal::{GroupProposalUseCases, ProposalStore};
use bcs_test_support::{
    NoopBotRegistryCoreService, NoopFriendCoreService, NoopGroupCoreService,
    NoopSessionManagementService, NoopSystemMessageService,
};

#[tokio::test]
async fn conformance_group_proposal_use_cases() {
    let svc = GroupProposalUseCases::with_defaults(
        Arc::new(NoopGroupCoreService),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        Arc::new(ProposalStore::new()),
        Arc::new(NoopSessionManagementService),
        Arc::new(NoopSystemMessageService),
    );
    bcs_test_support::contract::application::group_proposal_service_contract_tests(&svc).await;
}
