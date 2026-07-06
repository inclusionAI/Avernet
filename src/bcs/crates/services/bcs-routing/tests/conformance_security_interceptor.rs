use std::sync::Arc;

use bcs_routing::security::SecurityInterceptor;
use bcs_security_gateway_local::NoopSecurityGateway;
use bcs_test_support::contract::interceptor::message_interceptor_contract_tests;

#[tokio::test]
async fn security_interceptor_passes_message_interceptor_contract() {
    let interceptor = SecurityInterceptor::new(Arc::new(NoopSecurityGateway), true);
    message_interceptor_contract_tests(&interceptor).await;
}
