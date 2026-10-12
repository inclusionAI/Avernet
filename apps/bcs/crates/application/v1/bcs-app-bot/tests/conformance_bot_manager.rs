//! Rule 25 driver: `BotManagerServiceImpl` (plan Task 13/18).
//!
//! Runs the SHARED `BotManagerService` conformance suite against the
//! PRODUCTION manager facade. The harness doubles (recording hook +
//! counting core) are the shared test-support ones; the deeper
//! owner/manager/team semantics stay pinned by the facade's own unit
//! suite and the Task 4/7 store conformance drivers.

use std::sync::Arc;

use bcs_app_bot::BotManagerServiceImpl;
use bcs_test_support::contract::application::bot_manager::{
    BotManagerServiceHarness, bot_manager_service_contract_tests,
};
use bcs_test_support::{CountingBotAuthorityCore, RecordingBotAuthorityHook};

#[tokio::test]
async fn conformance_bot_manager_service_over_the_production_facade() {
    let hook = Arc::new(RecordingBotAuthorityHook::denying());
    let core = Arc::new(CountingBotAuthorityCore::fail_closed());
    let service = Arc::new(BotManagerServiceImpl::new(core.clone(), hook.clone()));

    let harness = BotManagerServiceHarness {
        service,
        hook,
        core,
    };
    bot_manager_service_contract_tests(&harness).await;
}