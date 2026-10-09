//! Rule 25 driver: `OwnershipTransferServiceImpl` (plan Task 14/18).
//!
//! Runs the SHARED `OwnershipTransferService` conformance suite against the
//! PRODUCTION transfer facade. The receipt/idempotency/committed-outcome
//! semantics stay pinned by the facade's own unit suite and the Task 8
//! store conformance drivers (both drivers of the shared harness record
//! through the shared recording hook + counting core).

use std::sync::Arc;

use bcs_app_bot::OwnershipTransferServiceImpl;
use bcs_test_support::contract::application::ownership_transfer::{
    OwnershipTransferServiceHarness, ownership_transfer_service_contract_tests,
};
use bcs_test_support::{CountingBotAuthorityCore, RecordingBotAuthorityHook};

#[tokio::test]
async fn conformance_ownership_transfer_over_the_production_facade() {
    let hook = Arc::new(RecordingBotAuthorityHook::denying());
    let core = Arc::new(CountingBotAuthorityCore::fail_closed());
    let service = Arc::new(OwnershipTransferServiceImpl::new(core.clone(), hook.clone()));

    let harness = OwnershipTransferServiceHarness {
        service,
        hook,
        core,
    };
    ownership_transfer_service_contract_tests(&harness).await;
}