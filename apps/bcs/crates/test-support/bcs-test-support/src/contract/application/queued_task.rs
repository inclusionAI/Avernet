//! Task acceptance contracts for implementations wired with Task enforce.
//! The caller supplies a valid ManagerWorker Session and verifies durable rows
//! and transport invocation through its concrete fixture.
use bcs_service_api::{MessageFlowService, TaskDispatchCommand, TaskDispatchOutcome};

pub async fn queued_task_dispatch_contract_tests<T: MessageFlowService + ?Sized>(
    service: &T,
    command: TaskDispatchCommand,
) -> TaskDispatchOutcome {
    let outcome = service.handle_task_dispatch(command).await.expect("durable task admission");
    assert_eq!(outcome.status, "queued", "acceptance must not claim dispatch");
    assert!(!outcome.task_id.is_empty(), "queued acceptance retains task identity");
    assert!(outcome.bot_deliveries.is_empty(), "admission cannot perform direct Bot I/O");
    outcome
}
