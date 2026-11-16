use bcn_runtime_api::port::{AgentConnector, AgentEventStream};
use bcn_runtime_model::*;

// Defaults are the real optional-operation implementation: no transport exists here.
struct UnsupportedTarget;
impl AgentConnector for UnsupportedTarget {
    fn capabilities(&self) -> ConnectorCapabilities {
        ConnectorCapabilities { streaming: false, query: false, subscribe: false,
            input_continuation: false, cancellation: CancellationSupport::Unsupported,
            idempotency: IdempotencySupport::Unsupported }
    }
}
fn assert_send<T: Send>(_: &T) {}
#[tokio::test]
async fn optional_operations_reject_on_trait_object_without_io() {
    let connector: Box<dyn AgentConnector> = Box::new(UnsupportedTarget);
    let scope = Scope::new("local", "tenant", "runtime");
    let handle = ExecutionHandle { target: AgentId::new(scope.clone(), "agent"),
        binding_version: "v1".into(), remote_task_id: Some("task".into()), remote_context_id: None };
    let request = AgentSendRequest {
        dispatch_id: DispatchId::new(scope.clone(), "dispatch"),
        operation_id: OperationId::new(scope.clone(), "send"),
        binding: FrozenTargetBinding { target: handle.target.clone(), binding_version: "v1".into(),
            binding_id: "target-binding".into(), capabilities: connector.capabilities(), credential_ref: None },
        message: Message { message_id: MessageId::new(scope.clone(), "message"), context_id: None, task_id: None,
            parts: vec![MessagePart::Text { text: "hello".into() }] },
        handle: None, expires_at_ms: None,
    };
    assert!(matches!(connector.send_message(request.clone()).await, Err(ConnectorError::Unsupported(_))));
    assert!(matches!(connector.send_streaming_message(request).await, Err(ConnectorError::Unsupported(_))));
    assert!(matches!(connector.get_task(handle.clone()).await, Err(ConnectorError::Unsupported(_))));
    let stream: ConnectorResult<AgentEventStream> = connector.subscribe_to_task(handle.clone()).await;
    assert_send(&stream);
    assert!(matches!(stream, Err(ConnectorError::Unsupported(_))));
    assert!(matches!(connector.cancel_task(AgentCancelRequest {
        operation_id: OperationId::new(scope, "cancel"), handle, exclusive_context: false,
    }).await, Err(ConnectorError::Unsupported(_))));
}

#[test]
fn credential_debug_cannot_expose_resolved_bytes() {
    let credential = bcn_runtime_api::port::ResolvedCredential::new(b"test-token".to_vec());
    assert_eq!(format!("{credential:?}"), "ResolvedCredential([REDACTED])");
}
