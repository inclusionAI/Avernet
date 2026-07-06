use bcs_test_support::{
    NoopDeliveryPolicyBlockInstrumentationHook, NoopDirectChatRunLifecycleHook,
    NoopWsLifecycleInstrumentationHook, contract::port,
};

#[tokio::test]
async fn noop_ws_lifecycle_hook_satisfies_contract() {
    port::ws_lifecycle_instrumentation_hook_contract_tests(&NoopWsLifecycleInstrumentationHook)
        .await;
}

#[tokio::test]
async fn noop_direct_chat_run_lifecycle_hook_satisfies_contract() {
    port::direct_chat_run_lifecycle_hook_contract_tests(&NoopDirectChatRunLifecycleHook).await;
}

#[tokio::test]
async fn noop_delivery_policy_block_hook_satisfies_contract() {
    port::delivery_policy_block_instrumentation_hook_contract_tests(
        &NoopDeliveryPolicyBlockInstrumentationHook,
    )
    .await;
}
