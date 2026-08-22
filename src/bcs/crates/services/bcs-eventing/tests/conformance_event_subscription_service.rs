#![allow(clippy::expect_used, clippy::unwrap_used)]

mod support;

use bcs_service_api::types::EventPayloadMode;
use bcs_test_support::contract::application::event_subscription_service_contract_tests;

use support::{create_command, harness};

#[tokio::test]
async fn memory_backed_application_service_satisfies_shared_contract() {
    let harness = harness(true);
    event_subscription_service_contract_tests(
        &harness.service,
        create_command(
            vec!["group.*".to_string()],
            EventPayloadMode::MetadataOnly,
        ),
    )
    .await;
}
