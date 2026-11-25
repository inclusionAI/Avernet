//! R25 conformance driver for the central `AuthService` harness (plan
//! Task 14): the REAL `AuthApplicationService` (the same orchestration
//! both HTTP entries call) assembled over the crate's recording fakes.
//! The boundary assertions live in the central
//! `bcs-test-support` harness; this driver only wires the production
//! service with at least one enabled provider, per the harness driver
//! contract.

mod support;

use std::sync::Arc;

use bcs_test_support::contract::application::auth_service_contract_tests;

use support::fake_ports::{FakePendingLoginPort, FakeProviderPort, FakeSessionPort};

#[tokio::test]
async fn auth_application_service_passes_the_service_boundary_contract() {
    let providers = Arc::new(FakeProviderPort::new(&["github", "google"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = support::auth_service(providers, sessions, pending);
    auth_service_contract_tests(Arc::new(service)).await;
}
