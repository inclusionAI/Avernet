//! §15.83 S3 (review-V P1-1): the runtime's node dispatch seam selects its
//! wire by the INJECTED policy resolver — an Ordinary assignee keeps the
//! historical direct delivery untouched; a Protected assignee routes
//! through the orchestration caller port, and a non-Provider target
//! refuses closed (caller containers live behind a Provider heartbeat,
//! spec §7.5.1). The pipeline-level happy-path (protected send under the
//! instance association) is locked by the message-flow e2e family.

use bcs_service_api::port::{
    CallerDispatchAck, OrchestrationAssigneePolicy, OrchestrationAssigneeResolution,
    OrchestrationCallerDispatchPort, OrchestrationCallerPolicyResolver,
    OrchestrationDispatchError, OrchestrationNodeDispatch,
};
use super::*;
use std::sync::Mutex as StdMutex;

struct RecordingOrchestrationPort {
    calls: StdMutex<Vec<OrchestrationNodeDispatch>>,
}
#[async_trait::async_trait]
impl OrchestrationCallerDispatchPort for RecordingOrchestrationPort {
    async fn dispatch_orchestration_node(
        &self,
        _target: &BotDeliveryTarget,
        _service_credential: &bcs_domain::RedactedToken,
        _policy: OrchestrationAssigneePolicy,
        dispatch: OrchestrationNodeDispatch,
    ) -> Result<CallerDispatchAck, OrchestrationDispatchError> {
        self.calls.lock().unwrap().push(dispatch);
        Ok(CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() })
    }
}

// The composition's per-assignee read — fixed per test. The enum is not
// Clone (it stays a closed ship), so a constructor captures the choice.
enum FixedResolution {
    Protected(OrchestrationAssigneePolicy),
    Ordinary,
}
#[async_trait::async_trait]
impl OrchestrationCallerPolicyResolver for FixedResolution {
    async fn resolve(
        &self,
        _assignee_bot_id: &str,
        _environment: &str,
    ) -> OrchestrationAssigneeResolution {
        match self {
            FixedResolution::Protected(policy) => {
                OrchestrationAssigneeResolution::Protected(match policy {
                    OrchestrationAssigneePolicy::Execution => OrchestrationAssigneePolicy::Execution,
                    OrchestrationAssigneePolicy::DelegationOnly => {
                        OrchestrationAssigneePolicy::DelegationOnly
                    }
                })
            }
            FixedResolution::Ordinary => OrchestrationAssigneeResolution::Ordinary,
        }
    }
}

async fn wired_harness(
    resolution: Arc<dyn OrchestrationCallerPolicyResolver>,
) -> (Harness, Arc<RecordingOrchestrationPort>) {
    let (mut h, runs) = harness(&[]).await;
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.unwrap();
    let port = Arc::new(RecordingOrchestrationPort { calls: StdMutex::new(Vec::new()) });
    let judge = noop_judge();
    h.runtime = test_runtime!(h.definitions.clone(), h.store.clone(), runs, h.store.clone(), group,
        h.sessions.clone(), h.delivery.clone(), judge)
        .with_session_channel_outbound(h.channel.clone()).with_loop_execution()
        .with_caller_node_dispatch(port.clone(), resolution);
    (h, port)
}

/// The Provider-targeted variant: the loop's `driver` binding resolves to
/// an HTTP Provider delivery target, so a Protected resolution reaches the
/// orchestration caller port instead of refusing on a non-Provider target.
async fn wired_harness_with_provider_target(
    resolution: Arc<dyn OrchestrationCallerPolicyResolver>,
) -> (Harness, Arc<RecordingOrchestrationPort>) {
    let registry = Arc::new(registry_routing_fakes::FakeRegistryService::default());
    registry.set_delivery_target(
        "driver-bot",
        registry_routing_fakes::FakeRegistryService::provider_target("driver-bot"),
    )
    .await;
    let (mut h, runs) = harness(&[]).await;
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.unwrap();
    let port = Arc::new(RecordingOrchestrationPort { calls: StdMutex::new(Vec::new()) });
    let judge = noop_judge();
    h.runtime = test_runtime!(h.definitions.clone(), h.store.clone(), runs, h.store.clone(), group,
        h.sessions.clone(), h.delivery.clone(), judge)
        .with_session_channel_outbound(h.channel.clone()).with_loop_execution()
        .with_bot_registry(registry)
        .with_caller_node_dispatch(port.clone(), resolution);
    (h, port)
}

#[tokio::test]
async fn an_ordinary_assignee_keeps_the_historical_direct_delivery() {
    // The seam must be a no-op for owner (caller-disabled) assignees: the
    // very same ordinary wire, the very same frames, untouched counts.
    let (h, port) = wired_harness(Arc::new(FixedResolution::Ordinary)).await;
    let _ = h.runtime.start_state_machine_run(command(loop_yaml(3, false, false, 1), false)).await;
    recover(&h).await;
    let deliveries = h.delivery.commands.lock().await;
    assert_eq!(deliveries.len(), 1, "the ordinary direct delivery happened");
    assert!(port.calls.lock().unwrap().is_empty(), "the caller port was never touched");
    let run = h.store.list_running_runs(None, 1).await.unwrap().remove(0);
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|node| node.status == StateMachineNodeStatus::Running).unwrap();
    assert_eq!(
        h.store.get_node_dispatch(&run.run_id, &node.node_id, node.attempt).await.unwrap().unwrap().status,
        bcs_service_api::StateMachineDispatchStatus::Delivered
    );
}

#[tokio::test]
async fn a_protected_assignee_on_a_provider_target_dispatches_with_a_one_based_node_attempt() {
    // The §8.5 port contract is 1-based (`node_attempt <= 0` is refused as
    // structurally invalid before any side effect) while the runtime's node
    // attempts are 0-based: the FIRST dispatch of a Protected assignee on a
    // Provider target must reach the port labeled attempt 1 — a 0-labeled
    // dispatch would be refused closed (the gap the 2026-10-22 singlebox
    // smoke caught live).
    let (h, port) = wired_harness_with_provider_target(Arc::new(FixedResolution::Protected(
        OrchestrationAssigneePolicy::Execution,
    )))
    .await;
    let _ = h.runtime.start_state_machine_run(command(loop_yaml(3, false, false, 1), false)).await;
    recover(&h).await;
    assert!(h.delivery.commands.lock().await.is_empty(), "the protected node never rides the ordinary wire");
    let calls = port.calls.lock().unwrap();
    assert!(!calls.is_empty(), "the protected port dispatched the node");
    assert_eq!(
        calls[0].node_attempt, 1,
        "the port contract is 1-based: the first attempt must reach the wire as 1"
    );
    assert!(
        !calls[0].caller_task.trim().is_empty(),
        "the wire text is extracted from the persisted chat.send content blocks: {:?}",
        calls[0].caller_task
    );
}

#[tokio::test]
async fn a_protected_assignee_on_a_non_provider_target_refuses_closed() {
    // Spec §7.5.1: caller containers live behind a Provider heartbeat —
    // a Protected resolution with a WebSocket-only target cannot carry the
    // runtime-only extensions zone: refused closed, never silently
    // downgraded to the ordinary wire (that would let a caller-enabled
    // assignee out of the protected discipline).
    let (h, port) = wired_harness(Arc::new(FixedResolution::Protected(
        OrchestrationAssigneePolicy::Execution,
    )))
    .await;
    let _ = h.runtime.start_state_machine_run(command(loop_yaml(3, false, false, 1), false)).await;
    // A closed node refusal surfaces in the recovery page (never delivered);
    // its code path is the runtime's own reject discipline for failed
    // dispatches.
    let _ = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert!(h.delivery.commands.lock().await.is_empty(), "no ordinary wire was touched");
    assert!(port.calls.lock().unwrap().is_empty(), "no protected wire was embraced");
    // The closed refusal converges the node through the runtime's own
    // failed-dispatch discipline — whatever state the run converged to,
    // the node's dispatch was NEVER delivered.
    let runs_list = h.store.list_running_runs(None, 1).await.unwrap();
    if let Some(run) = runs_list.into_iter().next() {
        if let Some(node) = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|node| node.status == StateMachineNodeStatus::Running) {
            let dispatch = h.store.get_node_dispatch(&run.run_id, &node.node_id, node.attempt).await.unwrap();
            if let Some(saved) = dispatch {
                assert_ne!(
                    saved.status,
                    bcs_service_api::StateMachineDispatchStatus::Delivered,
                    "the closed refusal never marks the node delivered"
                );
            }
        }
    }
}
