//! Contract for a fixture containing a fresh bcs_assign_task reference.
use bcs_service_api::port::{CoordinationClaim, CoordinationContext, CoordinationIntentPort,
    CoordinationResult, CoordinationStatus};

pub async fn coordination_intent_port_contract_tests(
    port: &dyn CoordinationIntentPort, id: &str, tool: &str,
    context: &CoordinationContext, deadline_ms: u64,
) {
    let CoordinationClaim::Acquired(lease) = port.resolve_and_claim(id, tool, context, deadline_ms)
        .await.expect("fresh reference grants one lease") else { panic!("expected lease") };
    assert!(lease.arguments["message"].as_str().expect("message").contains(&"中".repeat(4198)));
    assert!(matches!(port.resolve_and_claim(id, tool, context, deadline_ms).await.expect("duplicate"),
        CoordinationClaim::Duplicate(None)));
    port.finish(id, context, &lease.claim_token, &CoordinationResult {
        status: CoordinationStatus::Applied, task_id: Some("task-real".into()), error_code: None,
    }).await.expect("immutable finish");
}
